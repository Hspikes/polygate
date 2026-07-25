"""Task 2 Step 1: policy 数据模型的对齐测试（正向解析 + 反向拒绝）。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from automation.app.policy_models import (
    PolicyDraft,
    PolicyStoreDocument,
    PolicyVersion,
)

EXAMPLES_PATH = Path(__file__).resolve().parents[2] / "contracts" / "policy-examples.json"
EXAMPLES = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))


def test_draft_example_parses():
    draft = PolicyDraft.model_validate(EXAMPLES["draft"])
    assert draft.gateway.budget_mode == "soft"
    assert draft.automation.urgency_scores.critical == 100


def test_store_example_parses():
    store = PolicyStoreDocument.model_validate(EXAMPLES["store"])
    assert store.active.version == store.active_version


def test_reject_unknown_field():
    with pytest.raises(ValidationError):
        PolicyDraft.model_validate({**EXAMPLES["draft"], "unknown": True})


def test_reject_finance_privacy_standard():
    bad = copy.deepcopy(EXAMPLES["draft"])
    bad["automation"]["scenarios"]["finance_summary"]["defaults"]["privacy"] = "standard"
    with pytest.raises(ValidationError):
        PolicyDraft.model_validate(bad)


def test_reject_scores_critical_below_high():
    bad = copy.deepcopy(EXAMPLES["draft"])
    bad["automation"]["urgency_scores"]["critical"] = 10
    with pytest.raises(ValidationError):
        PolicyDraft.model_validate(bad)


def test_reject_missing_scenario():
    # 漏洞1修复：删掉一个必需场景必须被拒绝
    bad = copy.deepcopy(EXAMPLES["draft"])
    del bad["automation"]["scenarios"]["marketing_batch"]
    with pytest.raises(ValidationError):
        PolicyDraft.model_validate(bad)


def test_reject_missing_rollback_from():
    # 漏洞2修复：version record 缺 rollback_from 字段必须被拒绝
    record = copy.deepcopy(EXAMPLES["store"]["versions"][0])
    record.pop("rollback_from", None)
    with pytest.raises(ValidationError):
        PolicyVersion.model_validate(record)


def test_reject_created_at_without_timezone():
    record = copy.deepcopy(EXAMPLES["store"]["versions"][0])
    record["created_at"] = "2026-07-24T10:30:00"  # 无时区
    with pytest.raises(ValidationError):
        PolicyVersion.model_validate(record)


def test_reject_duplicate_versions():
    bad = copy.deepcopy(EXAMPLES["store"])
    dup = copy.deepcopy(bad["versions"][0])
    dup["status"] = "archived"
    bad["versions"].append(dup)  # 重复 version 号
    with pytest.raises(ValidationError):
        PolicyStoreDocument.model_validate(bad)