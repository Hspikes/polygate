"""Task 2 Step 5: PolicyManager 生命周期测试。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from automation.app.models import AutomationIntent
from automation.app.policy_models import PolicyDraft, PolicyStoreDocument
from automation.app.policy_repository import (
    InMemoryPolicyRepository,
    PolicyVersionNotFound,
    RepositoryUnavailable,
)
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


class RecordingCache:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.responses = []

    def set_active(self, response) -> None:
        self.responses.append(response)
        if self.fail:
            raise RuntimeError("cache unavailable")


class PreWriteFailureRepository(InMemoryPolicyRepository):
    def compare_and_swap(self, document, expected_revision):
        raise RepositoryUnavailable("request failed before write")


class CommitThenErrorRepository(InMemoryPolicyRepository):
    def compare_and_swap(self, document, expected_revision):
        super().compare_and_swap(document, expected_revision)
        raise RepositoryUnavailable("response lost after commit")


class ConcurrentCommitThenErrorRepository(InMemoryPolicyRepository):
    def compare_and_swap(self, document, expected_revision):
        concurrent = document.model_copy(deep=True)
        concurrent.active.change_note = "concurrent durable state"
        concurrent.active.policy.automation.urgency_scores.critical = 150
        super().compare_and_swap(concurrent, expected_revision)
        raise RepositoryUnavailable("response lost after concurrent commit")


class CommitThenUnreadableRepository(InMemoryPolicyRepository):
    def __init__(self, document):
        super().__init__(document)
        self.readable = True

    def load(self):
        if not self.readable:
            raise RepositoryUnavailable("reconciliation read failed")
        return super().load()

    def compare_and_swap(self, document, expected_revision):
        super().compare_and_swap(document, expected_revision)
        self.readable = False
        raise RepositoryUnavailable("response lost after commit")


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


def test_rollback_missing_target_raises_policy_version_not_found():
    mgr, _ = _manager()

    with pytest.raises(PolicyVersionNotFound):
        mgr.rollback(
            target_version=99,
            base_version=1,
            change_note="missing target",
            actor="policy-admin",
        )


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


def test_pre_write_failure_keeps_known_active_ready_and_cache_untouched():
    repository = PreWriteFailureRepository(_single_version_store())
    cache = RecordingCache()
    manager = PolicyManager(repository, cache)

    with pytest.raises(RepositoryUnavailable):
        manager.publish(
            base_version=1,
            draft=PolicyDraft.model_validate(EXAMPLES["draft"]),
            change_note="pre-write failure",
            actor="policy-admin",
        )

    assert manager.active.version == 1
    assert manager.ready is True
    assert repository.load().document.active_version == 1
    assert cache.responses == []


def test_commit_then_error_reconciles_desired_durable_state_and_returns_success():
    repository = CommitThenErrorRepository(_single_version_store())
    cache = RecordingCache()
    manager = PolicyManager(repository, cache)

    result = manager.publish(
        base_version=1,
        draft=PolicyDraft.model_validate(EXAMPLES["draft"]),
        change_note="committed despite response loss",
        actor="policy-admin",
    )

    assert result.version == 2
    assert result.warnings == []
    assert manager.active.version == 2
    assert manager.ready is True
    assert repository.load().document.active_version == 2
    assert [response.version for response in cache.responses] == [2]


def test_different_concurrent_durable_state_is_adopted_without_claiming_success():
    repository = ConcurrentCommitThenErrorRepository(_single_version_store())
    cache = RecordingCache()
    manager = PolicyManager(repository, cache)

    with pytest.raises(PolicyConflictError):
        manager.publish(
            base_version=1,
            draft=PolicyDraft.model_validate(EXAMPLES["draft"]),
            change_note="desired state",
            actor="policy-admin",
        )

    assert manager.active.change_note == "concurrent durable state"
    assert manager.active.policy.automation.urgency_scores.critical == 150
    assert manager.ready is True
    assert cache.responses[-1].policy.automation.urgency_scores.critical == 150


def test_unrecoverable_reconciliation_keeps_prior_active_marks_unready_and_skips_cache():
    repository = CommitThenUnreadableRepository(_single_version_store())
    cache = RecordingCache()
    manager = PolicyManager(repository, cache)

    with pytest.raises(RepositoryUnavailable):
        manager.publish(
            base_version=1,
            draft=PolicyDraft.model_validate(EXAMPLES["draft"]),
            change_note="unknown durable outcome",
            actor="policy-admin",
        )

    assert manager.active.version == 1
    assert manager.ready is False
    assert cache.responses == []


def test_failing_cache_degrades_committed_publish_without_reverting_active():
    repository = InMemoryPolicyRepository(_single_version_store())
    cache = RecordingCache(fail=True)
    manager = PolicyManager(repository, cache)

    result = manager.publish(
        base_version=1,
        draft=PolicyDraft.model_validate(EXAMPLES["draft"]),
        change_note="cache failure",
        actor="policy-admin",
    )

    assert result.version == 2
    assert result.warnings == ["policy cache degraded"]
    assert repository.load().document.active_version == 2
    assert manager.active.version == 2
    assert [response.version for response in cache.responses] == [2]


def test_active_is_immutable_to_callers():
    mgr, _ = _manager()
    a = mgr.active
    a.change_note = "hacked"
    assert mgr.active.change_note != "hacked"


def test_publish_does_not_retain_the_callers_mutable_draft():
    mgr, repo = _manager()
    draft = PolicyDraft.model_validate(copy.deepcopy(EXAMPLES["draft"]))

    mgr.publish(
        base_version=1,
        draft=draft,
        change_note="publish caller draft",
        actor="policy-admin",
    )
    draft.automation.urgency_scores.critical = 999

    assert mgr.active.policy.automation.urgency_scores.critical == 100
    assert repo.load().document.active.policy.automation.urgency_scores.critical == 100


def test_in_memory_repository_deep_copies_inputs_and_loaded_snapshots():
    source = _single_version_store()
    repo = InMemoryPolicyRepository(source)
    source.versions[0].change_note = "mutated caller source"
    loaded = repo.load()
    loaded.document.versions[0].change_note = "mutated loaded snapshot"

    assert repo.load().document.versions[0].change_note == "seed"

    replacement = repo.load().document
    replacement.versions[0].change_note = "persisted replacement"
    repo.compare_and_swap(replacement, "rev-1")
    replacement.versions[0].change_note = "mutated CAS caller"

    assert repo.load().document.versions[0].change_note == "persisted replacement"


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


def test_preview_priority_uses_semantic_slugs_and_disambiguates_duplicates():
    mgr, _ = _manager()
    draft = PolicyDraft.model_validate(EXAMPLES["draft"])
    intent = AutomationIntent.model_validate({
        "employee": "demo", "department": "engineering",
        "scenario": "production_incident", "urgency": "critical",
        "prompt": "p", "preferences": {"quality": "high", "privacy": "high", "max_cost_usd": 0.01, "latency_target_ms": 1000},
    })

    simulations = mgr.preview_priority(draft, [intent, intent, intent])

    assert [simulation.case_id for simulation in simulations] == [
        "critical-production-incident",
        "critical-production-incident-2",
        "critical-production-incident-3",
    ]
