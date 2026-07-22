#!/usr/bin/env python3
"""Standard-library checks for the cross-team Automation JSON contracts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"

SCHEMA_FILES = {
    "intent": CONTRACTS / "automation-intent.schema.json",
    "preview": CONTRACTS / "automation-preview.schema.json",
    "job": CONTRACTS / "automation-job.schema.json",
}
EXAMPLES_FILE = CONTRACTS / "automation-examples.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the small draft-07 subset used by these repository contracts."""

    expected_type = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    if expected_type in type_map and not isinstance(instance, type_map[expected_type]):
        raise AssertionError(f"{path}: expected {expected_type}, got {type(instance).__name__}")

    if "enum" in schema and instance not in schema["enum"]:
        raise AssertionError(f"{path}: {instance!r} is not in {schema['enum']!r}")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                raise AssertionError(f"{path}: missing required property {required!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(instance) - set(properties)
            if unknown:
                raise AssertionError(f"{path}: unknown properties {sorted(unknown)!r}")
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            validate(item, schema["items"], f"{path}[{index}]")


class AutomationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schemas = {name: load_json(path) for name, path in SCHEMA_FILES.items()}
        self.examples = load_json(EXAMPLES_FILE)

    def test_schemas_use_draft_07_and_forbid_unknown_top_level_fields(self) -> None:
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                self.assertEqual(schema["$schema"], "http://json-schema.org/draft-07/schema#")
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])

    def test_intent_enums_and_required_fields_are_frozen(self) -> None:
        schema = self.schemas["intent"]
        self.assertEqual(
            schema["required"],
            ["employee", "department", "scenario", "urgency", "prompt", "preferences"],
        )
        properties = schema["properties"]
        self.assertEqual(properties["department"]["enum"], ["engineering", "support", "finance", "marketing"])
        self.assertEqual(
            properties["scenario"]["enum"],
            ["production_incident", "customer_escalation", "finance_summary", "marketing_batch"],
        )
        self.assertEqual(properties["urgency"]["enum"], ["critical", "high", "normal", "low"])
        preferences = properties["preferences"]
        self.assertEqual(
            preferences["required"],
            ["quality", "privacy", "max_cost_usd", "latency_target_ms"],
        )

    def test_preview_and_job_shapes_are_frozen(self) -> None:
        self.assertEqual(
            self.schemas["preview"]["required"],
            [
                "preview_id",
                "expires_in_seconds",
                "normalized_intent",
                "priority",
                "gateway_request",
                "snippets",
                "policy_adjustments",
            ],
        )
        self.assertEqual(
            self.schemas["job"]["properties"]["status"]["enum"],
            ["queued", "running", "completed", "failed"],
        )

    def test_versioned_examples_conform_to_their_schemas(self) -> None:
        self.assertEqual(set(self.examples), set(self.schemas))
        for name, schema in self.schemas.items():
            with self.subTest(example=name):
                validate(self.examples[name], schema)


if __name__ == "__main__":
    unittest.main(verbosity=2)
