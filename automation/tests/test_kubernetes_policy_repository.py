from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from automation.app import kubernetes_policy_repository as repository_module
from automation.app.kubernetes_policy_repository import KubernetesConfigMapPolicyRepository
from automation.app.policy_manager import PolicyManager
from automation.app.policy_models import PolicyDraft, PolicyStoreDocument
from automation.app.policy_repository import RepositoryConflict, RepositoryUnavailable


def _store() -> dict:
    examples = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts" / "policy-examples.json").read_text(
            encoding="utf-8"
        )
    )
    return examples["store"]


def _repository(handler: httpx.MockTransport) -> KubernetesConfigMapPolicyRepository:
    return KubernetesConfigMapPolicyRepository(
        client=httpx.Client(transport=handler),
        namespace="default",
        configmap_name="polygate-routing-policy",
        configmap_key="policy-store.json",
        api_server="https://kubernetes.default.svc",
    )


def test_load_reads_policy_document_from_the_configured_configmap():
    seen: list[httpx.Request] = []
    store = _store()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "metadata": {"resourceVersion": "17"},
                "data": {"policy-store.json": json.dumps(store)},
            },
        )

    snapshot = _repository(httpx.MockTransport(handler)).load()

    assert snapshot.revision == "17"
    assert snapshot.document.active_version == 4
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/v1/namespaces/default/configmaps/polygate-routing-policy"


def test_from_environment_reloads_rotated_service_account_token_for_each_request(
    monkeypatch, tmp_path
):
    token_path = tmp_path / "token"
    token_path.write_text("token-one", encoding="utf-8")
    (tmp_path / "ca.crt").write_text("test-ca", encoding="utf-8")
    monkeypatch.setattr(repository_module, "SERVICE_ACCOUNT_DIRECTORY", tmp_path)
    monkeypatch.setenv("POD_NAMESPACE", "default")
    monkeypatch.setenv("POLICY_CONFIGMAP_NAME", "polygate-routing-policy")
    monkeypatch.setenv("POLICY_CONFIGMAP_KEY", "policy-store.json")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    authorizations: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers.get("Authorization"))
        return httpx.Response(
            200,
            json={
                "metadata": {"resourceVersion": "17"},
                "data": {"policy-store.json": json.dumps(_store())},
            },
        )

    client_type = httpx.Client

    def client_factory(**kwargs) -> httpx.Client:
        return client_type(
            transport=httpx.MockTransport(handler),
            headers=kwargs.get("headers"),
        )

    monkeypatch.setattr(repository_module.httpx, "Client", client_factory)
    repository = KubernetesConfigMapPolicyRepository.from_environment()

    repository.load()
    token_path.write_text("token-two", encoding="utf-8")
    repository.load()

    assert authorizations == ["Bearer token-one", "Bearer token-two"]


def test_compare_and_swap_puts_resource_version_and_serialized_document():
    seen: list[httpx.Request] = []
    store = _store()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"metadata": {"resourceVersion": "18"}})

    repository = _repository(httpx.MockTransport(handler))
    from automation.app.policy_models import PolicyStoreDocument

    result = repository.compare_and_swap(PolicyStoreDocument.model_validate(store), "17")

    assert result.revision == "18"
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/api/v1/namespaces/default/configmaps/polygate-routing-policy"
    body = json.loads(seen[0].content)
    assert body["metadata"] == {
        "name": "polygate-routing-policy",
        "namespace": "default",
        "resourceVersion": "17",
    }
    assert PolicyStoreDocument.model_validate_json(body["data"]["policy-store.json"]).model_dump(
        mode="json"
    ) == PolicyStoreDocument.model_validate(store).model_dump(mode="json")


def test_malformed_put_response_reconciles_the_durable_configmap_commit():
    durable_store = _store()
    revision = "17"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal durable_store, revision
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "metadata": {"resourceVersion": revision},
                    "data": {"policy-store.json": json.dumps(durable_store)},
                },
            )

        payload = json.loads(request.content)
        durable_store = json.loads(payload["data"]["policy-store.json"])
        revision = "18"
        return httpx.Response(200, content=b"malformed update response")

    manager = PolicyManager(_repository(httpx.MockTransport(handler)))
    result = manager.publish(
        base_version=4,
        draft=PolicyDraft.model_validate(_store()["versions"][0]["policy"]),
        change_note="commit with lost response",
        actor="policy-admin",
    )

    assert result.version == 5
    assert manager.active.version == 5
    assert PolicyStoreDocument.model_validate(durable_store).active_version == 5


def test_compare_and_swap_maps_a_kubernetes_conflict_to_repository_conflict():
    repository = _repository(
        httpx.MockTransport(lambda request: httpx.Response(409, json={"message": "conflict"}))
    )
    from automation.app.policy_models import PolicyStoreDocument

    with pytest.raises(RepositoryConflict):
        repository.compare_and_swap(PolicyStoreDocument.model_validate(_store()), "17")


def test_forbidden_configmap_access_is_repository_unavailable():
    repository = _repository(
        httpx.MockTransport(lambda request: httpx.Response(403, json={"message": "forbidden"}))
    )

    with pytest.raises(RepositoryUnavailable):
        repository.load()


def test_load_raises_sanitized_repository_corrupt_without_pydantic_chaining():
    store = _store()
    secret = "sensitive-repository-change-note"
    store["versions"][0]["change_note"] = secret * 30
    repository = _repository(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "metadata": {"resourceVersion": "17"},
                    "data": {"policy-store.json": json.dumps(store)},
                },
            )
        )
    )

    with pytest.raises(RepositoryUnavailable) as error:
        repository.load()

    assert type(error.value).__name__ == "RepositoryCorrupt"
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    assert secret not in str(error.value)
