from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

from automation.app.main import _compile_preview
from automation.app.models import (
    AutomationIntent,
    JobRecord,
    PreviewResponse,
)
from automation.app.policy_models import PolicyStoreDocument, PolicyVersion
from automation.app.store import InMemoryAutomationStore


# 仓库根目录：automation/tests/test_contract_alignment.py -> parents[2] == repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_FILE = REPO_ROOT / "contracts" / "automation-examples.json"
POLICY_EXAMPLES_FILE = REPO_ROOT / "contracts" / "policy-examples.json"


def load_examples() -> dict:
    with EXAMPLES_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


EXAMPLES = load_examples()
POLICY_EXAMPLES = json.loads(POLICY_EXAMPLES_FILE.read_text(encoding="utf-8"))
ACTIVE_POLICY = PolicyStoreDocument.model_validate(POLICY_EXAMPLES["store"]).active


class ExampleParsesIntoModelTests(unittest.TestCase):
    """契约示例必须能被对应 Pydantic model 解析（抓 model 与冻结契约松紧不一致）。"""

    def test_intent_example_parses(self):
        intent = AutomationIntent.model_validate(EXAMPLES["intent"])
        # 回读关键字段，确认解析结果与示例一致
        self.assertEqual(intent.employee, "Alice")
        self.assertEqual(intent.scenario.value, "production_incident")
        self.assertEqual(intent.preferences.privacy, "high")

    def test_preview_example_parses_with_aliases(self):
        # 示例里用的是契约字段名 class / json，靠 populate_by_name + alias 解析
        preview = PreviewResponse.model_validate(EXAMPLES["preview"])
        self.assertEqual(preview.priority.class_.value, "critical")
        self.assertEqual(preview.priority.initial_score, 140)
        # snippets.json_ 对应示例里的 "json" 键
        self.assertIsInstance(preview.snippets.json_, str)

    def test_job_example_parses(self):
        job = JobRecord.model_validate(EXAMPLES["job"])
        self.assertEqual(job.status.value, "queued")
        self.assertEqual(job.queue_position, 1)
        self.assertEqual(job.priority.class_.value, "critical")


class SerializationAliasTests(unittest.TestCase):
    """核心：by_alias 序列化后输出字段必须是 class / json，而不是内部名 class_ / json_。"""

    def _intent_payload(self) -> dict:
        return copy.deepcopy(EXAMPLES["intent"])

    def test_compiled_preview_emits_class_not_class_underscore(self):
        intent = AutomationIntent.model_validate(self._intent_payload())
        preview = _compile_preview(intent, ACTIVE_POLICY)
        payload = preview.model_dump(mode="json", by_alias=True)

        # priority 用契约字段名 class，不能是内部 class_
        self.assertIn("class", payload["priority"])
        self.assertNotIn("class_", payload["priority"])
        self.assertEqual(payload["priority"]["class"], "critical")

        # snippets 用契约字段名 json，不能是内部 json_
        self.assertIn("json", payload["snippets"])
        self.assertNotIn("json_", payload["snippets"])
        self.assertIsInstance(payload["snippets"]["json"], str)

    def test_compiled_preview_top_level_shape_matches_contract(self):
        intent = AutomationIntent.model_validate(self._intent_payload())
        preview = _compile_preview(intent, ACTIVE_POLICY)
        payload = preview.model_dump(mode="json", by_alias=True)

        # 顶层字段集合与冻结契约 required 一致
        self.assertEqual(
            set(payload),
            {
                "preview_id",
                "expires_in_seconds",
                "normalized_intent",
                "priority",
                "gateway_request",
                "snippets",
                "policy_adjustments",
		"policy_version",
            },
        )
        self.assertEqual(payload["expires_in_seconds"], 600)
        # gateway_request.polygate 四字段与 Gateway 契约一致
        self.assertEqual(
            set(payload["gateway_request"]["polygate"]),
            {"quality", "privacy", "max_cost_usd", "latency_target_ms"},
        )

    def test_serialization_round_trips_back_into_model(self):
        # 序列化往返：dump(by_alias) 出来的 JSON 必须能重新被 model 解析回去
        intent = AutomationIntent.model_validate(self._intent_payload())
        preview = _compile_preview(intent, ACTIVE_POLICY)
        payload = preview.model_dump(mode="json", by_alias=True)

        reparsed = PreviewResponse.model_validate(payload)
        self.assertEqual(reparsed.priority.class_.value, "critical")
        self.assertEqual(reparsed.priority.initial_score, 140)


class CompilePreviewBehaviorTests(unittest.TestCase):
    """_compile_preview 的关键业务行为，与冻结 example 对齐。"""

    def _intent(self, **overrides) -> AutomationIntent:
        payload = copy.deepcopy(EXAMPLES["intent"])
        payload.update(overrides)
        return AutomationIntent.model_validate(payload)

    def test_initial_score_is_140_for_critical_production_incident(self):
        # critical(100) + production_incident(40) = 140，与 example 对齐
        preview = _compile_preview(self._intent(), ACTIVE_POLICY)
        self.assertEqual(preview.priority.initial_score, 140)
        self.assertEqual(preview.policy_version, 4)

    def test_score_and_version_come_from_the_injected_policy(self):
        record = copy.deepcopy(POLICY_EXAMPLES["store"]["versions"][0])
        record["version"] = 9
        record["policy"]["automation"]["urgency_scores"]["critical"] = 200
        record["policy"]["automation"]["scenarios"]["production_incident"][
            "weight"
        ] = 50
        policy = PolicyVersion.model_validate(record)

        preview = _compile_preview(self._intent(), policy)

        self.assertEqual(preview.priority.initial_score, 250)
        self.assertEqual(preview.policy_version, 9)
        self.assertEqual(
            preview.priority.reason,
            "critical urgency (200) + production_incident scenario (50)",
        )

    def test_finance_summary_locks_privacy_to_high(self):
        # 用户传 standard，finance_summary 场景应被强制拉回 high 并记 adjustment
        finance_intent = self._intent(
            department="finance",
            scenario="finance_summary",
            urgency="normal",
            preferences={
                "quality": "balanced",
                "privacy": "standard",
                "max_cost_usd": 0.005,
                "latency_target_ms": 3000,
            },
        )
        preview = _compile_preview(finance_intent, ACTIVE_POLICY)
        self.assertEqual(preview.normalized_intent.preferences.privacy, "high")
        self.assertEqual(preview.gateway_request.polygate.privacy, "high")
        self.assertEqual(
            preview.policy_adjustments,
            ["finance_summary requires privacy=high"],
        )


class EnqueuedJobSerializationTests(unittest.TestCase):
    """enqueue() 产出的 JobRecord 序列化后符合冻结 Job 契约。"""

    def test_enqueued_job_serializes_with_contract_field_names(self):
        intent = AutomationIntent.model_validate(copy.deepcopy(EXAMPLES["intent"]))
        preview = _compile_preview(intent, ACTIVE_POLICY)

        store = InMemoryAutomationStore()
        store.save_preview(preview)
        job = store.enqueue(preview, idempotency_key="alignment-test-key")

        payload = job.model_dump(mode="json", by_alias=True, exclude_none=True)

        # 必填字段齐全
        self.assertEqual(
            {"job_id", "status", "priority", "queue_position", "created_at"}
            - set(payload),
            set(),
        )
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["queue_position"], 1)

        # 嵌套 priority 用契约字段名 class
        self.assertIn("class", payload["priority"])
        self.assertNotIn("class_", payload["priority"])

        # created_at 是合法 ISO date-time（fromisoformat 能解析即通过；Z 归一为 +00:00）
        datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))

    def test_enqueue_is_idempotent_for_same_key(self):
        intent = AutomationIntent.model_validate(copy.deepcopy(EXAMPLES["intent"]))
        preview = _compile_preview(intent, ACTIVE_POLICY)

        store = InMemoryAutomationStore()
        store.save_preview(preview)
        first = store.enqueue(preview, idempotency_key="same-key")
        second = store.enqueue(preview, idempotency_key="same-key")
        self.assertEqual(first.job_id, second.job_id)

class PolicyVersionFieldTests(unittest.TestCase):
    """policy_version 是新增的可选字段（Policy v1 契约），验证其可选行为。"""

    def test_preview_accepts_policy_version(self):
        # 带 policy_version 能解析
        payload = copy.deepcopy(EXAMPLES["preview"])
        payload["policy_version"] = 5
        preview = PreviewResponse.model_validate(payload)
        self.assertEqual(preview.policy_version, 5)

    def test_preview_omitting_policy_version_defaults_none(self):
        # 不带 policy_version 也能解析，默认 None（向后兼容）
        payload = copy.deepcopy(EXAMPLES["preview"])
        payload.pop("policy_version", None)
        preview = PreviewResponse.model_validate(payload)
        self.assertIsNone(preview.policy_version)

    def test_job_accepts_policy_version(self):
        payload = copy.deepcopy(EXAMPLES["job"])
        payload["policy_version"] = 3
        job = JobRecord.model_validate(payload)
        self.assertEqual(job.policy_version, 3)

    def test_job_omitting_policy_version_defaults_none(self):
        payload = copy.deepcopy(EXAMPLES["job"])
        payload.pop("policy_version", None)
        job = JobRecord.model_validate(payload)
        self.assertIsNone(job.policy_version)

if __name__ == "__main__":
    unittest.main(verbosity=2)
