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
    PolicyDraft,
    PolicyStoreDocument,
    PolicyVersion,
)
from automation.app.policy_repository import PolicyConflict, PolicyRepository

MAX_VERSIONS = 20


class PolicyConflictError(PolicyConflict):
    """publish/rollback 时 base_version 已过期（不是当前 active 版本）。"""


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
            except (httpx.HTTPError, ValueError) as exc:
                raise RuntimeError("gateway policy simulation is unavailable") from exc
            if not isinstance(result, dict):
                raise RuntimeError("gateway policy simulation returned an invalid response")
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
        self._active: PolicyVersion = snapshot.document.active

    @property
    def active(self) -> PolicyVersion:
        # 返回一个深拷贝，防止调用方改到内部状态（active policy 对外不可变）
        return self._active.model_copy(deep=True)

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
    ) -> list[PrioritySimulation]:
        """对每个 intent，比较'当前 active 策略'与'新 draft'下的优先级分数。"""
        results: list[PrioritySimulation] = []
        current = self._active.policy
        for i, intent in enumerate(intents):
            before = self._score(current, intent)
            after = self._score(draft, intent)
            results.append(
                PrioritySimulation(
                    case_id=f"{intent.urgency.value}-{intent.scenario.value}-{i}",
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
            raise ValueError(f"target_version {target_version} not found")
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
            policy=draft,
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
            self._repository.compare_and_swap(new_document, snapshot.revision)
        except PolicyConflict as exc:
            # repository 写失败 -> _active 完全不变
            raise PolicyConflictError(str(exc)) from exc

        # 2) 写成功后才切换内存 active
        self._active = new_record

        # 3) 更新 cache（失败只降级，不回滚 active）
        warnings: list[str] = []
        try:
            from automation.app.policy_models import ActivePolicyResponse
            self._cache.set_active(
                ActivePolicyResponse(
                    version=new_record.version,
                    schema_version=new_record.policy.schema_version,
                    published_at=new_record.created_at,
                    policy=new_record.policy,
                )
            )
        except Exception:
            warnings.append("policy cache degraded")

        return PublishResult(
            version=new_version_num,
            previous_version=base_version,
            rollback_from=rollback_from,
            published_at=now,
            warnings=warnings,
        )
