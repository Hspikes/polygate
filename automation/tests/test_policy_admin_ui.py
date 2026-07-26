from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from automation.app.main import ADMIN_CONTENT_SECURITY_POLICY, create_app
from automation.app.store import InMemoryAutomationStore


ADMIN_DIR = Path(__file__).resolve().parents[1] / "admin"


class ResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.resources.append(values["src"] or "")
        if tag == "link" and values.get("href"):
            self.resources.append(values["href"] or "")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(store=InMemoryAutomationStore()))


def test_policy_admin_page_is_served_with_strict_security_headers(client: TestClient) -> None:
    response = client.get("/admin/policies")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == ADMIN_CONTENT_SECURITY_POLICY
    assert "unsafe-eval" not in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'none'",
    ):
        assert directive in response.headers["content-security-policy"]
    assert "PolyGate Policy Management" in response.text


@pytest.mark.parametrize(
    ("path", "content_type"),
    (
        ("/admin/assets/policy-admin.js", "text/javascript"),
        ("/admin/assets/policy-admin.css", "text/css"),
        ("/admin/assets/vendor/alpine-csp.min.js", "text/javascript"),
    ),
)
def test_policy_admin_assets_are_local_uncached_and_typed(
    client: TestClient, path: str, content_type: str
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == ADMIN_CONTENT_SECURITY_POLICY


def test_html_loads_only_same_origin_assets_in_required_script_order(client: TestClient) -> None:
    html = client.get("/admin/policies").text
    parser = ResourceParser()
    parser.feed(html)

    assert parser.resources == [
        "/admin/assets/policy-admin.css",
        "/admin/assets/policy-admin.js",
        "/admin/assets/vendor/alpine-csp.min.js",
    ]
    assert all(resource.startswith("/admin/assets/") for resource in parser.resources)
    assert "http://" not in html
    assert "https://" not in html
    assert "//cdn." not in html


def test_first_party_assets_do_not_embed_or_persist_the_admin_key(client: TestClient) -> None:
    content = "\n".join(
        (
            client.get("/admin/policies").text,
            client.get("/admin/assets/policy-admin.js").text,
            client.get("/admin/assets/policy-admin.css").text,
        )
    )

    for forbidden in (
        "POLICY_ADMIN_KEY",
        "local-policy-admin-development",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "serviceWorker",
        "document.cookie",
        "caches.open",
        "http://",
        "https://",
        "//cdn.",
    ):
        assert forbidden not in content
    assert "x-html" not in content


def test_editor_contains_every_fixed_policy_v1_control_and_guardrail(client: TestClient) -> None:
    html = client.get("/admin/policies").text
    javascript = client.get("/admin/assets/policy-admin.js").text

    expected_paths = (
        "gateway.assumed_output_tokens",
        "gateway.balanced_price_tolerance",
        "gateway.budget_mode",
        "gateway.latency_mode",
        "gateway.high_quality_strategy",
        "automation.urgency_scores.critical",
        "automation.urgency_scores.high",
        "automation.urgency_scores.normal",
        "automation.urgency_scores.low",
        "automation.queue.waiting_bonus_interval_seconds",
        "automation.queue.waiting_bonus_points",
        "automation.queue.waiting_bonus_cap",
        "automation.queue.starvation_streak_threshold",
        "automation.queue.starvation_wait_seconds",
    )
    for path in expected_paths:
        assert path in javascript

    for scenario in (
        "production_incident",
        "customer_escalation",
        "finance_summary",
        "marketing_batch",
    ):
        assert scenario in javascript
        assert f"automation.scenarios.${{{scenario!r}}}" not in javascript
    assert "financeLocked ?" in javascript
    assert ':disabled="field.disabled"' in html
    assert "Finance summary privacy" in html
    assert "Locked guardrail" in html
    assert "Prefer highest-quality real model" in javascript
    assert "highest quality-ranked eligible real model" in javascript


def test_release_preview_history_compare_and_rollback_controls_exist(client: TestClient) -> None:
    html = client.get("/admin/policies").text
    javascript = client.get("/admin/assets/policy-admin.js").text

    for label in (
        "Validate",
        "Preview impact",
        "Publish policy",
        "Version history",
        "Compare",
        "Rollback",
        "Change note",
        "Routing simulation",
        "Priority simulation",
        "Queue order",
    ):
        assert label in html
    for endpoint in (
        "/v1/policies/active",
        "/v1/admin/policies/validate",
        "/v1/admin/policies/preview",
        "/v1/admin/policies/publish",
        "/rollback",
    ):
        assert endpoint in javascript
    assert "The active policy changed while you were editing." in javascript
    assert 'messages: [{ role: "user"' in javascript


def test_core_revision_and_validation_logic_is_exposed_without_secret_state(
    client: TestClient,
) -> None:
    javascript = client.get("/admin/assets/policy-admin.js").text

    for export_name in (
        "getAtPath",
        "setAtPath",
        "invalidateRevisions",
        "canPreviewRevision",
        "canPublishRevision",
        "localUrgencyOrderError",
        "mapValidationDetails",
        "diffObjects",
    ):
        assert export_name in javascript
    assert "adminKey," not in javascript
    assert "validatedRevision = null" in javascript
    assert "previewedRevision = null" in javascript


def test_vendored_alpine_version_provenance_license_and_checksum_are_pinned() -> None:
    vendor_asset = ADMIN_DIR / "vendor" / "alpine-csp.min.js"
    vendor_readme = (ADMIN_DIR / "vendor" / "README.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(vendor_asset.read_bytes()).hexdigest()

    assert "@alpinejs/csp" in vendor_readme
    assert "3.15.12" in vendor_readme
    assert "https://registry.npmjs.org/@alpinejs/csp/-/csp-3.15.12.tgz" in vendor_readme
    assert "License: MIT" in vendor_readme
    assert digest == "566167134bb2347110904e2ced6e816d2e8d837200c158f98b72372b3bb0b9a6"
    assert digest in vendor_readme
