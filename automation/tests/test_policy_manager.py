"""Task 2 Step 5: PolicyManager 生命周期测试。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from automation.app.models import AutomationIntent
from automation.app.policy_models import PolicyDraft, PolicyStoreDocument
from automation.app.policy_repository import InMemoryPolicyRepository
from automation.app.policy_manager import PolicyManager, PolicyConflictError

EXAMPLES_PATH = Path(__file__).resolve().parents[2] / "contracts" / "policy-examples.json"
EXAMPLES = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))


def _single_version_store(version: int = 1) -> PolicyStoreDocument:
    """构造一个只有单个 active 版本的 store（version 号可指定）。"""
    return PolicyStoreDocument.model_validate({
        "active_version": version,
        "versions": [{
            "version": version,
            "status": "active",
            "created_at": "2026-07-24T10:30:00Z",
            "created_by": "policy-admin",
            "change_note": "seed",
            "rollback_from": None,
            "policy": EXAMPLES["draft"],
        }],
    })


def _manager(store: PolicyStoreDocument | None = None):
    store = store or _single_version_store(1)
    repo = InMemoryPolicyRepository(store)
    return PolicyManager(repository=repo), repo


def test_active_reflects_seed():
    mgr, _ = _manager()
    assert mgr.active.version == 1


def test_publish_increments_version_and_persists():
    mgr, repo = _manager()
    changed = copy.deepcopy(EXAMPLES["draft"])
    changed["automation"]["queue"]["waiting_bonus_cap"] = 50
    result = mgr.publish(
        base_version=1,
        draft=PolicyDraft.model_validate(changed),
        change_note="Change queue weights",
        actor="policy-admin",
    )
    assert result.version == 2
    assert repo.load().document.active_version == 2


def test_stale_base_version_raises_conflict():
    mgr, _ = _manager()
    draft = PolicyDraft.model_validate(EXAMPLES["draft"])
    mgr.publish(base_version=1, draft=draft, change_note="v2", actor="a")
    # 再用过期的 base_version=1 发布，应冲突
    with pytest.raises(PolicyConflictError):
        mgr.publish(base_version=1, draft=draft, change_note="v3", actor="a")


def test_version_list_truncates_to_20():
    mgr, repo = _manager()
    draft = PolicyDraft.model_validate(EXAMPLES["draft"])
    for i in range(2, 22):  # 发布 v2..v21
        mgr.publish(base_version=i - 1, draft=draft, change_note=f"v{i}", actor="a")
    doc = repo.load().document
    assert len(doc.versions) == 20
    assert doc.active_version == 21


def test_rollback_creates_new_version_number():
    mgr, repo = _manager()
    draft = PolicyDraft.model_validate(EXAMPLES["draft"])
    for i in range(2, 22):  # 发布到 v21
        mgr.publish(base_version=i - 1, draft=draft, change_note=f"v{i}", actor="a")
    # 从 v2 回滚，应生成 v22（版本号永不重用）
    result = mgr.rollback(target_version=2, base_version=21, change_note="rollback", actor="a")
    assert result.version == 22
    assert result.rollback_from == 2
    assert repo.load().document.active_version == 22


def test_repository_failure_keeps_active():
    mgr, repo = _manager()
    draft = PolicyDraft.model_validate(EXAMPLES["draft"])
    mgr.publish(base_version=1, draft=draft, change_note="v2", actor="a")
    # 手动制造过期：直接对底层再 CAS 一次，让 manager 的下次操作过期
    # 用错误的 base_version 触发冲突，active 不应改变
    before = mgr.active.version
    with pytest.raises(PolicyConflictError):
        mgr.publish(base_version=99, draft=draft, change_note="bad", actor="a")
    assert mgr.active.version == before


def test_active_is_immutable_to_callers():
    mgr, _ = _manager()
    a = mgr.active
    a.change_note = "hacked"
    assert mgr.active.change_note != "hacked"


def test_preview_priority_computes_before_after():
    mgr, _ = _manager()
    changed = copy.deepcopy(EXAMPLES["draft"])
    changed["automation"]["urgency_scores"]["critical"] = 200  # 抬高 critical
    intent = AutomationIntent.model_validate({
        "employee": "demo", "department": "engineering",
        "scenario": "production_incident", "urgency": "critical",
        "prompt": "p", "preferences": {"quality": "high", "privacy": "high", "max_cost_usd": 0.01, "latency_target_ms": 1000},
    })
    sims = mgr.preview_priority(PolicyDraft.model_validate(changed), [intent])
    # before: critical(100)+weight(40)=140; after: critical(200)+40=240
    assert sims[0].before_score == 140
    assert sims[0].after_score == 240