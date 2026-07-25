"""
Policy 控制平面的数据结构（Pydantic 模型）。

对应两份契约 schema：
- contracts/policy.schema.json        -> PolicyDraft 及其嵌套结构
- contracts/policy-store.schema.json  -> PolicyStoreDocument

跨字段约束（JSON schema draft-07 表达不了，靠 Pydantic validator 强制）：
1. urgency_scores 必须 critical > high > normal > low
2. finance_summary 场景的 defaults.privacy 必须锁死 "high"
3. scenarios 必须恰好包含四个必需场景（用固定字段模型保证，不用 dict）
4. PolicyStoreDocument：version 唯一、active_version 指向唯一 active 版本
   且为最大版本号、created_at 必须带时区

字段边界：Worker 调度只消费 urgency_scores + scenarios.*.weight + queue；
scenarios.*.defaults 归 preview 编译器；gateway.* 归 Gateway。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from automation.app.models import Preferences, StrictModel


# ----------------------------------------------------------------------------
# PolicyDraft 部分（policy.schema.json）
# ----------------------------------------------------------------------------

class GatewayPolicy(StrictModel):
    """gateway 策略——归 A 消费，Worker 不读。完整定义以便解析整份 draft。"""
    assumed_output_tokens: int = Field(ge=1, le=32768)
    balanced_price_tolerance: float = Field(ge=0, le=2)
    budget_mode: Literal["soft", "hard"]
    latency_mode: Literal["soft", "hard"]
    high_quality_strategy: Literal["prefer_real", "lowest_cost"]


class UrgencyScores(StrictModel):
    """四档紧急程度基础分。约束 critical > high > normal > low。"""
    critical: int = Field(ge=0, le=1000)
    high: int = Field(ge=0, le=1000)
    normal: int = Field(ge=0, le=1000)
    low: int = Field(ge=0, le=1000)

    @model_validator(mode="after")
    def _ordered(self) -> "UrgencyScores":
        if not (self.critical > self.high > self.normal > self.low):
            raise ValueError(
                "urgency scores must satisfy critical > high > normal > low"
            )
        return self


class QueuePolicy(StrictModel):
    """队列调度参数。Worker 热更新时读的就是这些。"""
    waiting_bonus_interval_seconds: int = Field(ge=1, le=3600)
    waiting_bonus_points: int = Field(ge=0, le=100)
    waiting_bonus_cap: int = Field(ge=0, le=1000)
    starvation_streak_threshold: int = Field(ge=1, le=100)
    starvation_wait_seconds: int = Field(ge=1, le=86400)


class ScenarioPolicy(StrictModel):
    """单个场景配置：权重 + 默认偏好。Worker 只用 weight。
    defaults 复用现有 Preferences 模型。"""
    weight: int = Field(ge=0, le=500)
    defaults: Preferences


class Scenarios(StrictModel):
    """四个必需场景——用固定字段模型（不是 dict），这样少任何一个都会报错。
    修复：dict[Literal, ...] 只能拒绝未知键，无法保证四个都存在。"""
    production_incident: ScenarioPolicy
    customer_escalation: ScenarioPolicy
    finance_summary: ScenarioPolicy
    marketing_batch: ScenarioPolicy


class AutomationPolicy(StrictModel):
    """automation 策略：紧急分数 + 场景配置 + 队列参数。B 的核心领域。"""
    urgency_scores: UrgencyScores
    scenarios: Scenarios
    queue: QueuePolicy

    @model_validator(mode="after")
    def _finance_privacy_locked(self) -> "AutomationPolicy":
        if self.scenarios.finance_summary.defaults.privacy != "high":
            raise ValueError(
                "finance_summary scenario defaults.privacy must be 'high'"
            )
        return self


class PolicyDraft(StrictModel):
    """管理员可编辑的完整策略草稿。"""
    schema_version: Literal[1]
    gateway: GatewayPolicy
    automation: AutomationPolicy


# ----------------------------------------------------------------------------
# PolicyStore 部分（policy-store.schema.json）
# ----------------------------------------------------------------------------

class PolicyVersion(StrictModel):
    """一个版本记录：版本号 + 状态 + 审计信息 + 完整策略。"""
    version: int = Field(ge=1)
    status: Literal["active", "archived"]
    created_at: datetime
    created_by: str = Field(min_length=1)
    change_note: str = Field(min_length=1, max_length=500)
    # 修复：schema 里 rollback_from 是 required（值可为 null），不能给 default，
    # 否则"缺字段"也会被接受。去掉 default，强制显式提供（int 或 None）。
    rollback_from: int | None
    policy: PolicyDraft

    @model_validator(mode="after")
    def _check_rollback_from(self) -> "PolicyVersion":
        if self.rollback_from is not None and self.rollback_from < 1:
            raise ValueError("rollback_from must be >= 1 or null")
        return self

    @model_validator(mode="after")
    def _created_at_has_tz(self) -> "PolicyVersion":
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return self


class ActivePolicyResponse(StrictModel):
    """GET /v1/policies/active 的响应——只暴露当前生效策略。"""
    version: int = Field(ge=1)
    schema_version: Literal[1]
    published_at: datetime
    policy: PolicyDraft


class PolicyStoreDocument(StrictModel):
    """存进 ConfigMap 的完整文档：active 指针 + 最近最多 20 个版本。"""
    active_version: int = Field(ge=1)
    versions: list[PolicyVersion] = Field(min_length=1, max_length=20)

    @property
    def active(self) -> PolicyVersion:
        matches = [v for v in self.versions if v.version == self.active_version]
        if len(matches) != 1:
            raise ValueError("active_version must reference exactly one version")
        return matches[0]

    @model_validator(mode="after")
    def _integrity(self) -> "PolicyStoreDocument":
        nums = [v.version for v in self.versions]
        # version 唯一
        if len(nums) != len(set(nums)):
            raise ValueError("version numbers must be unique")
        # active_version 必须指向恰好一个 status=active 的记录
        actives = [v for v in self.versions if v.status == "active"]
        if len(actives) != 1:
            raise ValueError("exactly one version must have status='active'")
        if actives[0].version != self.active_version:
            raise ValueError("active_version must match the active record's version")
        # active_version 必须是最大版本号
        if self.active_version != max(nums):
            raise ValueError("active_version must be the highest version number")
        return self