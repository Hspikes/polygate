#!/usr/bin/env python3
"""Standard checks for the Policy v1 contracts (contracts/policy*.json).

Uses jsonschema (not the hand-rolled draft-07 subset in
test-automation-contracts.py) because policy-store.schema.json uses a
cross-file $ref into policy.schema.json, which the hand-rolled validator
does not support. This file is dev/CI-only and is never bundled into any
Docker image, so it does not conflict with the decision to keep jsonschema
out of automation/requirements.txt (runtime deps).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, RefResolver

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"

POLICY_SCHEMA_PATH = CONTRACTS / "policy.schema.json"
STORE_SCHEMA_PATH = CONTRACTS / "policy-store.schema.json"
EXAMPLES_PATH = CONTRACTS / "policy-examples.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class PolicyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy_schema = load_json(POLICY_SCHEMA_PATH)
        self.store_schema = load_json(STORE_SCHEMA_PATH)
        self.examples = load_json(EXAMPLES_PATH)

        self.policy_validator = Draft7Validator(self.policy_schema)

        # policy-store.schema.json's "policy" field is a cross-file $ref into
        # policy.schema.json; register it so the resolver can find it by $id.
        store_resolver = RefResolver.from_schema(
            self.store_schema,
            store={self.policy_schema["$id"]: self.policy_schema},
        )
        self.store_validator = Draft7Validator(self.store_schema, resolver=store_resolver)

    # ---- schema sanity -------------------------------------------------

    def test_schemas_use_draft_07_and_forbid_unknown_fields(self) -> None:
        for name, schema in [("policy", self.policy_schema), ("store", self.store_schema)]:
            with self.subTest(schema=name):
                self.assertEqual(schema["$schema"], "http://json-schema.org/draft-07/schema#")
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])

    def test_policy_schema_version_is_fixed_to_1(self) -> None:
        self.assertEqual(self.policy_schema["properties"]["schema_version"], {"const": 1})

    def test_finance_summary_privacy_is_locked_to_high(self) -> None:
        finance_defaults = self.policy_schema["definitions"]["defaults_finance"]["properties"]["privacy"]
        self.assertEqual(finance_defaults, {"const": "high"})

    def test_store_schema_top_level_shape_is_active_version_plus_versions(self) -> None:
        self.assertEqual(set(self.store_schema["required"]), {"active_version", "versions"})
        versions_prop = self.store_schema["properties"]["versions"]
        self.assertEqual(versions_prop["minItems"], 1)
        self.assertEqual(versions_prop["maxItems"], 20)

    def test_store_version_record_status_enum_is_active_or_archived(self) -> None:
        status_enum = self.store_schema["definitions"]["version_record"]["properties"]["status"]["enum"]
        self.assertEqual(set(status_enum), {"active", "archived"})

    # ---- examples conform to schema ------------------------------------

    def test_example_draft_conforms_to_policy_schema(self) -> None:
        errors = list(self.policy_validator.iter_errors(self.examples["draft"]))
        self.assertEqual(errors, [], msg=[e.message for e in errors])

    def test_example_store_conforms_to_store_schema_including_nested_policy(self) -> None:
        # This is the key regression test: it exercises the cross-file $ref,
        # so a malformed nested "policy" (e.g. bad finance privacy) inside
        # any version record would be caught here, not just at the top level.
        errors = list(self.store_validator.iter_errors(self.examples["store"]))
        self.assertEqual(errors, [], msg=[e.message for e in errors])

    def test_example_active_response_policy_conforms_to_policy_schema(self) -> None:
        active_response = self.examples["active_response"]
        for key in ("version", "schema_version", "published_at", "policy"):
            self.assertIn(key, active_response)
        errors = list(self.policy_validator.iter_errors(active_response["policy"]))
        self.assertEqual(errors, [], msg=[e.message for e in errors])

    def test_all_expected_example_keys_are_present(self) -> None:
        expected = {
            "draft",
            "store",
            "active_response",
            "validate_response",
            "preview_response",
            "publish_request",
            "publish_response",
            "rollback_request",
        }
        self.assertEqual(expected - set(self.examples), set(), "missing example keys")

    # ---- guardrail / regression checks ---------------------------------

    def test_store_rejects_finance_privacy_downgrade_inside_nested_policy(self) -> None:
        bad_store = json.loads(json.dumps(self.examples["store"]))
        bad_store["versions"][0]["policy"]["automation"]["scenarios"]["finance_summary"]["defaults"]["privacy"] = "standard"
        errors = list(self.store_validator.iter_errors(bad_store))
        self.assertTrue(errors, "store validator did not catch a downgraded finance privacy nested inside a version record")

    def test_store_rejects_more_than_twenty_versions(self) -> None:
        bad_store = json.loads(json.dumps(self.examples["store"]))
        template = bad_store["versions"][0]
        bad_store["versions"] = [json.loads(json.dumps(template)) for _ in range(21)]
        errors = list(self.store_validator.iter_errors(bad_store))
        self.assertTrue(errors, "store validator did not enforce maxItems=20 on versions")

    def test_store_rejects_empty_versions(self) -> None:
        bad_store = json.loads(json.dumps(self.examples["store"]))
        bad_store["versions"] = []
        errors = list(self.store_validator.iter_errors(bad_store))
        self.assertTrue(errors, "store validator did not enforce minItems=1 on versions")

    def test_policy_rejects_unknown_gateway_field(self) -> None:
        bad_draft = json.loads(json.dumps(self.examples["draft"]))
        bad_draft["gateway"]["unexpected_field"] = True
        errors = list(self.policy_validator.iter_errors(bad_draft))
        self.assertTrue(errors, "policy validator did not reject an unknown gateway field")

    def test_publish_request_has_base_version_change_note_and_policy(self) -> None:
        publish_request = self.examples["publish_request"]
        for key in ("base_version", "change_note", "policy"):
            self.assertIn(key, publish_request)
        errors = list(self.policy_validator.iter_errors(publish_request["policy"]))
        self.assertEqual(errors, [], msg=[e.message for e in errors])


if __name__ == "__main__":
    unittest.main(verbosity=2)