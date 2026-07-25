from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from automation.app.main import create_app
from automation.app.policy_auth import PolicyAdminAuthenticator
from automation.app.policy_manager import HttpGatewaySimulator, PolicyManager
from automation.app.policy_metrics import PUBLICATIONS
from automation.app.policy_models import PolicyStoreDocument
from automation.app.policy_repository import InMemoryPolicyRepository, RepositoryUnavailable


EXAMPLES = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "policy-examples.json").read_text(
        encoding="utf-8"
    )
)
ADMIN_HEADERS = {"Authorization": "Bearer test-policy-admin"}


class FakeGatewaySimulator:
    def __init__(self) -> None:
        self.calls: list[tuple[object, list[object]]] = []

    def simulate(self, draft, cases):
        self.calls.append((draft, cases))
        return [
            {"provider": f"tolerance-{draft.gateway.balanced_price_tolerance}", "reason": "simulated"}
            for _ in cases
        ]


def _store() -> PolicyStoreDocument:
    draft = copy.deepcopy(EXAMPLES["draft"])
    return PolicyStoreDocument.model_validate(
        {
            "active_version": 1,
            "versions": [
                {
                    "version": 1,
                    "status": "active",
                    "created_at": "2026-07-24T10:30:00Z",
                    "created_by": "policy-admin",
                    "change_note": "seed",
                    "rollback_from": None,
                    "policy": draft,
                }
            ],
        }
    )


@pytest.fixture
def api():
    repository = InMemoryPolicyRepository(_store())
    simulator = FakeGatewaySimulator()
    app = create_app(
        policy_manager=PolicyManager(repository),
        policy_authenticator=PolicyAdminAuthenticator("test-policy-admin"),
        gateway_simulator=simulator,
    )
    return TestClient(app), repository, simulator


def test_active_policy_is_public_secret_free_and_etag_aware(api):
    client, _, _ = api

    response = client.get("/v1/policies/active")

    assert response.status_code == 200
    assert response.headers["etag"] == '"policy-v1"'
    assert set(response.json()) == {"version", "schema_version", "published_at", "policy"}
    assert client.get("/v1/policies/active", headers={"If-None-Match": '"policy-v1"'}).status_code == 304


def test_admin_policy_history_requires_bearer_key(api):
    client, _, _ = api

    assert client.get("/v1/admin/policies").status_code == 401
    assert client.get("/v1/admin/policies", headers=ADMIN_HEADERS).status_code == 200


def test_validate_accepts_valid_draft_and_rejects_guardrail_violation(api):
    client, _, _ = api
    valid = client.post("/v1/admin/policies/validate", headers=ADMIN_HEADERS, json=EXAMPLES["draft"])
    invalid_draft = copy.deepcopy(EXAMPLES["draft"])
    invalid_draft["automation"]["scenarios"]["finance_summary"]["defaults"]["privacy"] = "standard"
    invalid = client.post("/v1/admin/policies/validate", headers=ADMIN_HEADERS, json=invalid_draft)

    assert valid.status_code == 200
    assert valid.json() == {"valid": True, "warnings": []}
    assert invalid.status_code == 422


def test_validation_errors_do_not_echo_invalid_policy_values_or_change_notes(api):
    client, _, _ = api
    invalid_draft = copy.deepcopy(EXAMPLES["draft"])
    invalid_draft["automation"]["scenarios"]["finance_summary"]["defaults"]["privacy"] = "sensitive-invalid-policy-value"
    policy_error = client.post("/v1/admin/policies/validate", headers=ADMIN_HEADERS, json=invalid_draft)
    change_note = "sensitive-change-note-" * 30
    note_error = client.post(
        "/v1/admin/policies/publish",
        headers=ADMIN_HEADERS,
        json={"base_version": 1, "change_note": change_note, "policy": EXAMPLES["draft"]},
    )

    assert policy_error.status_code == 422
    assert note_error.status_code == 422
    for response, secret in ((policy_error, "sensitive-invalid-policy-value"), (note_error, change_note)):
        body = response.json()
        assert "input" not in json.dumps(body)
        assert "ctx" not in json.dumps(body)
        assert secret not in json.dumps(body)


def test_preview_uses_gateway_simulator_without_writing_repository(api):
    client, repository, simulator = api
    before = repository.load().revision
    draft = copy.deepcopy(EXAMPLES["draft"])
    draft["gateway"]["balanced_price_tolerance"] = 0.35
    response = client.post(
        "/v1/admin/policies/preview",
        headers=ADMIN_HEADERS,
        json={
            "policy": draft,
            "gateway_cases": [
                {
                    "model": "auto",
                    "messages": [{"role": "user", "content": "simulate"}],
                    "polygate": {
                        "quality": "balanced",
                        "privacy": "standard",
                        "max_cost_usd": 0.01,
                        "latency_target_ms": 1000,
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["base_version"] == 1
    assert response.json()["simulations"]["routing"] == [
        {
            "case_id": "balanced-standard",
            "before": {"provider": "tolerance-0.2", "reason": "simulated"},
            "after": {"provider": "tolerance-0.35", "reason": "simulated"},
        }
    ]
    assert repository.load().revision == before
    assert len(simulator.calls) == 2


def test_preview_forwards_a_gateway_compatible_agent_request_to_simulation():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        tolerance = body["gateway_policy"]["balanced_price_tolerance"]
        return httpx.Response(
            200,
            json={
                "provider": f"tolerance-{tolerance}",
                "reason": "simulated",
                "estimated_cost_usd": 0.01,
                "typical_latency_ms": 100,
            },
        )

    app = create_app(
        policy_manager=PolicyManager(InMemoryPolicyRepository(_store())),
        policy_authenticator=PolicyAdminAuthenticator("test-policy-admin"),
        gateway_simulator=HttpGatewaySimulator(
            "http://gateway.test", client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
    )
    client = TestClient(app)
    agent_case = {
        "model": "auto",
        "messages": [{"role": "developer", "content": "follow the tool protocol"}],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "stream": True,
        "stream_options": {"include_usage": True},
        "polygate": {
            "quality": "balanced",
            "privacy": "standard",
            "max_cost_usd": 0.01,
            "latency_target_ms": 1000,
            "cache_control": "no-store",
            "session_id": "agent-session",
        },
    }

    accepted = client.post(
        "/v1/admin/policies/preview",
        headers=ADMIN_HEADERS,
        json={"policy": EXAMPLES["draft"], "gateway_cases": [agent_case]},
    )
    rejected_role = client.post(
        "/v1/admin/policies/preview",
        headers=ADMIN_HEADERS,
        json={
            "policy": EXAMPLES["draft"],
            "gateway_cases": [{**agent_case, "messages": [{"role": "invalid", "content": "x"}]}],
        },
    )

    assert accepted.status_code == 200
    assert rejected_role.status_code == 422
    assert len(requests) == 2
    assert requests[0]["request"] == agent_case
    assert requests[0]["gateway_policy"] == EXAMPLES["draft"]["gateway"]


def test_publish_detects_stale_base_version_and_rollback_creates_a_new_version(api):
    client, _, _ = api
    publish = client.post(
        "/v1/admin/policies/publish",
        headers=ADMIN_HEADERS,
        json={"base_version": 1, "change_note": "publish v2", "policy": EXAMPLES["draft"]},
    )
    stale = client.post(
        "/v1/admin/policies/publish",
        headers=ADMIN_HEADERS,
        json={"base_version": 1, "change_note": "stale", "policy": EXAMPLES["draft"]},
    )
    rollback = client.post(
        "/v1/admin/policies/1/rollback",
        headers=ADMIN_HEADERS,
        json={"base_version": 2, "change_note": "rollback to v1"},
    )

    assert publish.status_code == 201
    assert publish.json()["version"] == 2
    assert stale.status_code == 409
    assert rollback.status_code == 201
    assert rollback.json()["version"] == 3
    assert rollback.json()["rollback_from"] == 1


def test_rejected_publication_metrics_include_authenticated_validation_and_missing_target_failures(api):
    client, _, _ = api
    publish_counter = PUBLICATIONS.labels(action="publish", result="rejected")._value.get()
    rollback_counter = PUBLICATIONS.labels(action="rollback", result="rejected")._value.get()
    invalid_draft = copy.deepcopy(EXAMPLES["draft"])
    invalid_draft["automation"]["scenarios"]["finance_summary"]["defaults"]["privacy"] = "standard"
    publish_invalid = client.post(
        "/v1/admin/policies/publish",
        headers=ADMIN_HEADERS,
        json={"base_version": 1, "change_note": "invalid policy", "policy": invalid_draft},
    )
    rollback_invalid = client.post(
        "/v1/admin/policies/1/rollback",
        headers=ADMIN_HEADERS,
        json={"base_version": 1, "change_note": "x" * 501},
    )
    rollback_missing = client.post(
        "/v1/admin/policies/99/rollback",
        headers=ADMIN_HEADERS,
        json={"base_version": 1, "change_note": "missing target"},
    )

    assert publish_invalid.status_code == 422
    assert rollback_invalid.status_code == 422
    assert rollback_missing.status_code == 404
    assert PUBLICATIONS.labels(action="publish", result="rejected")._value.get() == publish_counter + 1
    assert PUBLICATIONS.labels(action="rollback", result="rejected")._value.get() == rollback_counter + 2


def test_metrics_are_exposed_as_prometheus_text(api):
    client, _, _ = api

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "polygate_policy_active_version" in response.text


def test_repository_unavailability_maps_to_service_unavailable():
    class RepositoryUnavailableAfterStartup(InMemoryPolicyRepository):
        def __init__(self, document):
            super().__init__(document)
            self.available = True

        def load(self):
            if not self.available:
                raise RepositoryUnavailable("unavailable")
            return super().load()

    repository = RepositoryUnavailableAfterStartup(_store())
    manager = PolicyManager(repository)
    repository.available = False
    client = TestClient(
        create_app(
            policy_manager=manager,
            policy_authenticator=PolicyAdminAuthenticator("test-policy-admin"),
            gateway_simulator=FakeGatewaySimulator(),
        ),
        raise_server_exceptions=False,
    )

    assert client.get("/v1/admin/policies", headers=ADMIN_HEADERS).status_code == 503


def test_environment_admin_key_is_disabled_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("POLICY_ALLOW_ENV_ADMIN_KEY", raising=False)
    monkeypatch.setenv("POLICY_ADMIN_KEY", "environment-key")
    with pytest.raises(RuntimeError, match="environment policy key is disabled"):
        PolicyAdminAuthenticator.from_environment_for_local_development()

    monkeypatch.setenv("POLICY_ALLOW_ENV_ADMIN_KEY", "true")
    with patch("automation.app.policy_auth.secrets.compare_digest", wraps=__import__("secrets").compare_digest) as compare:
        authenticator = PolicyAdminAuthenticator.from_environment_for_local_development()
        authenticator.require("Bearer environment-key")

    assert compare.called


def test_admin_authentication_rejects_raw_or_malformed_bearer_values():
    authenticator = PolicyAdminAuthenticator("test-policy-admin")

    with patch("automation.app.policy_auth.secrets.compare_digest", wraps=__import__("secrets").compare_digest) as compare:
        for authorization in ("test-policy-admin", "bearer test-policy-admin", "Bearer"):
            with pytest.raises(HTTPException) as error:
                authenticator.require(authorization)
            assert error.value.status_code == 401

    assert compare.call_count == 3
