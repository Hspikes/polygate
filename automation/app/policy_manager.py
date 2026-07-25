"""
PolicyManager：策略生命周期的核心。负责 validate（验证）、preview_priority
（预览优先级变化）、publish（发布新版本）、rollback（回滚），并维护"当前
生效策略"(_active)。

关键设计（按 Task 2 规格）：
- 先把持久状态写进 repository，成功后再切换内存里的 _active（顺序不能反，
  否则 repository 写失败但 _active 已变，会不一致）。
- cache 更新发生在 _active 切换之后；cache 失败不回滚 _active，只在 warnings
  里加一条 "policy cache degraded"（降级，不是致命错误）。
- 版本号单调递增、永不重用（即使回滚也是生成新版本号）。
- versions 最多保留 20 个，超出时丢弃最老的非 active 记录。
- 并发：publish/rollback 用 repository 的 compare_and_swap，base_version 过期
  会抛 PolicyConflict。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from automation.app.models import AutomationIntent, GatewaySimulationRequest
from automation.app.policy_models import (
    ActivePolicyResponse,
    PolicyDraft,
    PolicyStoreDocument,
    PolicyVersion,
)
from automation.app.policy_repository import (
    PolicyConflict,
    PolicyRepository,
    PolicyVersionNotFound,
    RepositoryUnavailable,
)

MAX_VERSIONS = 20


def disambiguate_case_id(slug: str, slug_counts: dict[str, int]) -> str:
    """给重复出现的 case slug 追加出现序号，保证 case_id 在一次 preview 内唯一。

    routing 和 priority 两组模拟共用这个规则：case_id 是 Policy Editor 用来索引
    before/after 的键，重复会让行互相覆盖。slug_counts 由调用方持有并复用。
    """
    slug_counts[slug] = slug_counts.get(slug, 0) + 1
    occurrence = slug_counts[slug]
    return slug if occurrence == 1 else f"{slug}-{occurrence}"


class PolicyConflictError(PolicyConflict):
    """publish/rollback 时 base_version 已过期（不是当前 active 版本）。"""


class GatewaySimulationUnavailable(RuntimeError):
    """Gateway routing simulation failed without exposing upstream details."""


@dataclass
class PrioritySimulation:
    """一个任务在'新策略'下的优先级模拟结果。"""
    case_id: str
    before_score: int
    after_score: int


@dataclass
class PublishResult:
    version: int
    previous_version: int
    rollback_from: int | None
    published_at: datetime
    warnings: list[str]


class PolicyCache(Protocol):
    """缓存协议——把 active policy 推给需要快速读取的地方。可能失败（降级）。"""
    def set_active(self, response) -> None: ...


class RedisPolicyCache:
    def __init__(self, redis_client, key: str = "polygate:policy:active") -> None:
        self._redis = redis_client
        self._key = key

    def set_active(self, response) -> None:
        self._redis.set(self._key, response.model_dump_json())


class GatewaySimulator(Protocol):
    def simulate(self, draft: PolicyDraft, cases: list[GatewaySimulationRequest]) -> list[dict]: ...


class HttpGatewaySimulator:
    def __init__(self, gateway_url: str, client: httpx.Client | None = None) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._client = client or httpx.Client(timeout=5.0)

    def simulate(self, draft: PolicyDraft, cases: list[GatewaySimulationRequest]) -> list[dict]:
        results: list[dict] = []
        for case in cases:
            try:
                response = self._client.post(
                    f"{self._gateway_url}/internal/routing/simulate",
                    json={
                        "request": case.model_dump(mode="json", exclude_none=True),
                        "gateway_policy": draft.gateway.model_dump(mode="json"),
                    },
                )
                response.raise_for_status()
                result = response.json()
            except (httpx.HTTPError, ValueError):
                raise GatewaySimulationUnavailable(
                    "gateway policy simulation is unavailable"
                ) from None
            if not isinstance(result, dict):
                raise GatewaySimulationUnavailable(
                    "gateway policy simulation returned an invalid response"
                )
            results.append(result)
        return results


class _NullCache:
    """默认空缓存：什么都不做，永不失败。"""
    def set_active(self, response) -> None:
        return None


class PolicyManager:
    def __init__(
        self,
        repository: PolicyRepository,
        cache: PolicyCache | None = None,
    ) -> None:
        self._repository = repository
        self._cache = cache or _NullCache()
        snapshot = repository.load()
        self._active: PolicyVersion = snapshot.document.active.model_copy(deep=True)
        self._ready = True

    @property
    def active(self) -> PolicyVersion:
        # 返回一个深拷贝，防止调用方改到内部状态（active policy 对外不可变）
        return self._active.model_copy(deep=True)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def history(self) -> list[PolicyVersion]:
        document = self._repository.load().document
        return [record.model_copy(deep=True) for record in document.versions]

    def get_version(self, version: int) -> PolicyVersion | None:
        return next((record for record in self.history if record.version == version), None)

    # ---------- validate ----------
    def validate(self, draft: PolicyDraft) -> list[str]:
        """draft 能被构造出来就说明已通过 schema/校验（Pydantic 在解析时已校验）。
        这里返回非致命的 warnings 列表（目前无额外软性警告，返回空）。"""
        warnings: list[str] = []
        return warnings

    # ---------- preview_priority ----------
    def preview_priority(
        self,
        draft: PolicyDraft,
        intents: list[AutomationIntent],
        *,
        active_policy: PolicyDraft | None = None,
    ) -> list[PrioritySimulation]:
        """对每个 intent，比较'当前 active 策略'与'新 draft'下的优先级分数。"""
        results: list[PrioritySimulation] = []
        current = active_policy or self._active.policy
        slug_counts: dict[str, int] = {}
        for intent in intents:
            before = self._score(current, intent)
            after = self._score(draft, intent)
            slug = (
                f"{intent.urgency.value}-"
                f"{intent.scenario.value.replace('_', '-')}"
            )
            results.append(
                PrioritySimulation(
                    case_id=disambiguate_case_id(slug, slug_counts),
                    before_score=before,
                    after_score=after,
                )
            )
        return results

    @staticmethod
    def _score(policy: PolicyDraft, intent: AutomationIntent) -> int:
        urgency_score = getattr(
            policy.automation.urgency_scores, intent.urgency.value
        )
        scenario_policy = getattr(
            policy.automation.scenarios, intent.scenario.value
        )
        return urgency_score + scenario_policy.weight

    # ---------- publish ----------
    def publish(
        self,
        *,
        base_version: int,
        draft: PolicyDraft,
        change_note: str,
        actor: str,
    ) -> PublishResult:
        return self._commit(
            base_version=base_version,
            draft=draft,
            change_note=change_note,
            actor=actor,
            rollback_from=None,
        )

    # ---------- rollback ----------
    def rollback(
        self,
        *,
        target_version: int,
        base_version: int,
        change_note: str,
        actor: str,
    ) -> PublishResult:
        snapshot = self._repository.load()
        target = next(
            (v for v in snapshot.document.versions if v.version == target_version),
            None,
        )
        if target is None:
            raise PolicyVersionNotFound("policy version not found")
        # 回滚 = 用目标版本的 policy 内容，生成一个全新版本号
        return self._commit(
            base_version=base_version,
            draft=target.policy,
            change_note=change_note,
            actor=actor,
            rollback_from=target_version,
        )

    # ---------- 内部：真正提交一个新版本 ----------
    def _commit(
        self,
        *,
        base_version: int,
        draft: PolicyDraft,
        change_note: str,
        actor: str,
        rollback_from: int | None,
    ) -> PublishResult:
        snapshot = self._repository.load()
        document = snapshot.document

        # base_version 必须是当前 active 版本，否则说明调用方基于过期状态操作
        if base_version != document.active_version:
            raise PolicyConflictError(
                f"base_version {base_version} is stale; current active is "
                f"{document.active_version}"
            )

        new_version_num = max(v.version for v in document.versions) + 1
        now = datetime.now(UTC)
        new_record = PolicyVersion(
            version=new_version_num,
            status="active",
            created_at=now,
            created_by=actor,
            change_note=change_note,
            rollback_from=rollback_from,
            policy=draft.model_copy(deep=True),
        )

        # 旧的 active 记录降级为 archived
        new_versions: list[PolicyVersion] = []
        for v in document.versions:
            if v.status == "active":
                new_versions.append(v.model_copy(update={"status": "archived"}))
            else:
                new_versions.append(v)
        new_versions.append(new_record)

        # 截断到最多 20 个：丢弃最老的非 active 记录
        if len(new_versions) > MAX_VERSIONS:
            archived = sorted(
                [v for v in new_versions if v.status == "archived"],
                key=lambda v: v.version,
            )
            to_drop = len(new_versions) - MAX_VERSIONS
            drop_versions = {v.version for v in archived[:to_drop]}
            new_versions = [v for v in new_versions if v.version not in drop_versions]

        new_document = PolicyStoreDocument(
            active_version=new_version_num,
            versions=new_versions,
        )

        # 1) 先写持久状态（compare_and_swap 保证并发安全）
        try:
            persisted = self._repository.compare_and_swap(
                new_document,
                snapshot.revision,
            )
        except PolicyConflict as exc:
            # repository 写失败 -> _active 完全不变
            raise PolicyConflictError(str(exc)) from exc
        except RepositoryUnavailable:
            try:
                persisted = self._repository.load()
            except RepositoryUnavailable:
                self._ready = False
                raise RepositoryUnavailable(
                    "policy repository write outcome could not be reconciled"
                ) from None

            self._ready = True
            if persisted.document == new_document:
                pass
            elif (
                persisted.revision == snapshot.revision
                and persisted.document == document
            ):
                raise RepositoryUnavailable(
                    "policy repository write was not committed"
                ) from None
            else:
                self._activate_and_cache(persisted.document.active)
                raise PolicyConflictError(
                    "a different policy state was committed concurrently"
                ) from None

        self._ready = True
        durable_record = persisted.document.active

        # 2) 写成功后才切换内存 active
        warnings = self._activate_and_cache(durable_record)

        return PublishResult(
            version=durable_record.version,
            previous_version=base_version,
            rollback_from=durable_record.rollback_from,
            published_at=durable_record.created_at,
            warnings=warnings,
        )

    def _activate_and_cache(self, record: PolicyVersion) -> list[str]:
        self._active = record.model_copy(deep=True)
        try:
            self._cache.set_active(
                ActivePolicyResponse(
                    version=record.version,
                    schema_version=record.policy.schema_version,
                    published_at=record.created_at,
                    policy=record.policy.model_copy(deep=True),
                )
            )
        except Exception:
            return ["policy cache degraded"]
        return []
