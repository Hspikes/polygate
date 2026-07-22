from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from automation.app.main import create_app


def intent(**overrides):
    payload = {
        "employee": "Alice",
        "department": "engineering",
        "scenario": "production_incident",
        "urgency": "critical",
        "prompt": "Analyse the production incident log.",
        "preferences": {
            "quality": "high",
            "privacy": "high",
            "max_cost_usd": 0.01,
            "latency_target_ms": 1000,
        },
    }
    payload.update(overrides)
    return payload


class AutomationApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app())

    def test_health_and_four_templates_are_available(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok", "service": "automation"})

        response = self.client.get("/v1/templates")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()],
            ["production_incident", "customer_escalation", "finance_summary", "marketing_batch"],
        )

    def test_preview_compiles_gateway_request_and_copyable_snippets(self):
        response = self.client.post("/v1/requests/preview", json=intent())
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["expires_in_seconds"], 600)
        self.assertEqual(body["priority"]["initial_score"], 140)
        self.assertEqual(body["gateway_request"]["model"], "auto")
        self.assertEqual(body["gateway_request"]["messages"][0]["content"], "Analyse the production incident log.")
        self.assertIn("/v1/chat/completions", body["snippets"]["curl"])
        self.assertIn("requests.post", body["snippets"]["python"])
        self.assertIn(
            'POLYGATE_URL = os.getenv("POLYGATE_URL", "http://localhost:8000")',
            body["snippets"]["python"],
        )
        compile(body["snippets"]["python"], "<polygate-preview>", "exec")

    def test_finance_template_locks_privacy_to_high(self):
        finance = intent(
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
        response = self.client.post("/v1/requests/preview", json=finance)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["normalized_intent"]["preferences"]["privacy"], "high")
        self.assertEqual(body["gateway_request"]["polygate"]["privacy"], "high")
        self.assertEqual(body["policy_adjustments"], ["finance_summary requires privacy=high"])

    def test_invalid_gateway_preferences_are_rejected(self):
        invalid = intent(
            preferences={
                "quality": "ultra",
                "privacy": "public",
                "max_cost_usd": 0.005,
                "latency_target_ms": 3000,
            }
        )
        response = self.client.post("/v1/requests/preview", json=invalid)
        self.assertEqual(response.status_code, 422)

    def test_job_submission_is_idempotent_and_queryable(self):
        preview = self.client.post("/v1/requests/preview", json=intent()).json()
        payload = {"preview_id": preview["preview_id"], "confirmed": True}
        headers = {"Idempotency-Key": "demo-idempotency-key"}

        first = self.client.post("/v1/jobs", json=payload, headers=headers)
        second = self.client.post("/v1/jobs", json=payload, headers=headers)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(first.json()["status"], "queued")
        self.assertEqual(first.json()["queue_position"], 1)

        lookup = self.client.get(f"/v1/jobs/{first.json()['job_id']}")
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.json(), first.json())

    def test_jobs_can_be_listed_and_filtered_by_status(self):
        preview = self.client.post("/v1/requests/preview", json=intent()).json()
        submitted = self.client.post(
            "/v1/jobs",
            json={"preview_id": preview["preview_id"], "confirmed": True},
            headers={"Idempotency-Key": "list-job"},
        ).json()

        queued = self.client.get("/v1/jobs", params={"status": "queued"})
        completed = self.client.get("/v1/jobs", params={"status": "completed"})

        self.assertEqual(queued.status_code, 200)
        self.assertEqual([job["job_id"] for job in queued.json()], [submitted["job_id"]])
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json(), [])

    def test_unconfirmed_or_unknown_preview_is_rejected(self):
        unconfirmed = self.client.post(
            "/v1/jobs",
            json={"preview_id": "preview_missing", "confirmed": False},
            headers={"Idempotency-Key": "unconfirmed"},
        )
        self.assertEqual(unconfirmed.status_code, 422)

        missing = self.client.post(
            "/v1/jobs",
            json={"preview_id": "preview_missing", "confirmed": True},
            headers={"Idempotency-Key": "missing-preview"},
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
