"""
Policy 控制平面的数据结构（Pydantic 模型）。

对应两份契约 schema：
- contracts/policy.schema.json        -> PolicyDraft 及其嵌套结构（管理员可编辑的策略）
- contracts/policy-store.schema.json  -> PolicyStoreDocument（存进 ConfigMap 的完整存档）

设计原则（和契约保持一致）：
- 管理员只能编辑"路由 + 调度参数"，碰不到安全护栏（隐私锁定、认证要求等
  是服务器固定的，故意不放进 PolicyDraft）。
- 有两条跨字段约束，draft-07 的 JSON schema 表达不了，靠这里的 Pydantic
  validator 来强制：
    1. urgency_scores 必须满足 critical > high > normal > low
    2. 财务场景（finance_summary）的 privacy 必须锁死为 "high"

字段边界备注（B 负责的 Worker 只消费其中一部分）：
- Worker 调度真正读取：urgency_scores、scenarios.*.weight、queue.*
- scenarios.*.defaults 归 A 的 preview 编译器消费，不是 Worker 调度用的
- gateway.* 整块归 A，Worker 不读；但这里仍完整定义，以便整份 PolicyDraft
  能被正确解析/校验。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """与 automation/app/models.py 里的 StrictModel 保持一致：禁止多余字段。
    （这里单独定义一份，避免 policy_models 反向依赖 models；如果团队希望
    统一，也可以改成 from automation.app.models import StrictModel。）"""
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------
# PolicyDraft 部分（对应 policy.schema.json）
# ----------------------------------------------------------------------------

class Defaults(StrictModel):
    """场景的默认路由偏好。注意：这块是 A 的 preview 编译器消费的，
    不是 Worker 调度用的。"""
    quality: Literal["balanced", "high", "cheap"]
    privacy: Literal["standard", "high"]
    max_cost_usd: float = Field(ge=0, le=10)
    latency_target_ms: int = Field(ge=1, le=120000)


class Scenario(StrictModel):
    """一个业务场景的配置：权重 + 默认偏好。
    Worker 调度只用 weight（场景权重，加到优先级分数里）。"""
    weight: int = Field(ge=0, le=500)
    defaults: Defaults


class UrgencyScores(StrictModel):
    """四档紧急程度对应的基础分。Worker 调度直接消费这块。
    约束 critical > high > normal > low 由下面的 validator 强制。"""
    critical: int = Field(ge=0, le=1000)
    high: int = Field(ge=0, le=1000)
    normal: int = Field(ge=0, le=1000)
    low: int = Field(ge=0, le=1000)

    @model_validator(mode="after")
    def _check_strict_ordering(self) -> "UrgencyScores":
        # draft-07 表达不了的跨字段约束，在这里强制：严格递减
        if not (self.critical > self.high > self.normal > self.low):
            raise ValueError(
                "urgency_scores 必须满足 critical > high > normal > low，"
                f"当前值为 critical={self.critical}, high={self.high}, "
                f"normal={self.normal}, low={self.low}"
            )
        return self


class QueueParams(StrictModel):
    """队列调度参数。Worker 调度直接消费这块——热更新时读的就是这些值。"""
    waiting_bonus_interval_seconds: int = Field(ge=1, le=3600)
    waiting_bonus_points: int = Field(ge=0, le=100)
    waiting_bonus_cap: int = Field(ge=0, le=1000)
    starvation_streak_threshold: int = Field(ge=1, le=100)
    starvation_wait_seconds: int = Field(ge=1, le=86400)


class AutomationPolicy(StrictModel):
    """automation 策略：紧急分数 + 各场景配置 + 队列参数。这是 B 的核心领域。"""
    urgency_scores: UrgencyScores
    scenarios: dict[
        Literal["production_incident", "customer_escalation", "finance_summary", "marketing_batch"],
        Scenario,
    ]
    queue: QueueParams

    @model_validator(mode="after")
    def _check_finance_privacy_locked(self) -> "AutomationPolicy":
        # 财务场景的 privacy 必须锁死为 "high"（数据合规要求，schema 里用
        # scenario_finance 单独约束，这里用代码强制）
        finance = self.scenarios.get("finance_summary")
        if finance is not None and finance.defaults.privacy != "high":
            raise ValueError(
                "finance_summary 场景的 defaults.privacy 必须为 'high'（财务数据合规要求）"
            )
        return self


class GatewayPolicy(StrictModel):
    """gateway 策略——整块归 A 消费，Worker 不读。这里完整定义只是为了能
    解析/校验整份 PolicyDraft。"""
    assumed_output_tokens: int = Field(ge=1, le=32768)
    balanced_price_tolerance: float = Field(ge=0, le=2)
    budget_mode: Literal["soft", "hard"]
    latency_mode: Literal["soft", "hard"]
    high_quality_strategy: Literal["prefer_real", "lowest_cost"]


class PolicyDraft(StrictModel):
    """管理员可编辑的完整策略草稿（validate / preview / publish 的输入内容）。"""
    schema_version: Literal[1]
    gateway: GatewayPolicy
    automation: AutomationPolicy


# ----------------------------------------------------------------------------
# PolicyStore 部分（对应 policy-store.schema.json）
# ----------------------------------------------------------------------------

class VersionRecord(StrictModel):
    """一个版本记录：版本号 + 状态 + 审计信息 + 完整策略内容。"""
    version: int = Field(ge=1)
    status: Literal["active", "archived"]
    created_at: datetime
    created_by: str = Field(min_length=1)
    change_note: str = Field(min_length=1)
    # 如果是回滚产生的，记录从哪个版本复制内容；否则 None
    rollback_from: int | None = Field(default=None, ge=1)
    policy: PolicyDraft


class PolicyStoreDocument(StrictModel):
    """存进 polygate-routing-policy ConfigMap 的完整文档：
    当前生效版本指针 + 最近最多 20 个版本记录。"""
    active_version: int = Field(ge=1)
    versions: list[VersionRecord] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _check_active_pointer(self) -> "PolicyStoreDocument":
        # active_version 必须恰好对应一个 status=active 的版本记录
        active_records = [v for v in self.versions if v.status == "active"]
        if len(active_records) != 1:
            raise ValueError(
                f"必须恰好有一个 status='active' 的版本，当前有 {len(active_records)} 个"
            )
        if active_records[0].version != self.active_version:
            raise ValueError(
                f"active_version={self.active_version} 与唯一 active 记录的 "
                f"version={active_records[0].version} 不一致"
            )
        return self


# ----------------------------------------------------------------------------
# ActivePolicyResponse（GET /v1/policies/active 的响应；对应 policy-examples.json）
# ----------------------------------------------------------------------------

class ActivePolicyResponse(StrictModel):
    """GET /v1/policies/active 返回给消费方（Gateway / Worker）的内容。
    只暴露"当前生效的策略是什么"，不暴露历史版本。"""
    version: int = Field(ge=1)
    schema_version: Literal[1]
    published_at: datetime
    policy: PolicyDraft

    @classmethod
    def from_store(cls, store: PolicyStoreDocument) -> "ActivePolicyResponse":
        """从完整存档里，抽出当前 active 的那一版，组装成对外响应。"""
        active = next(v for v in store.versions if v.version == store.active_version)
        return cls(
            version=active.version,
            schema_version=active.policy.schema_version,
            published_at=active.created_at,
            policy=active.policy,
        )