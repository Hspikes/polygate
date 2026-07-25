from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from automation.app.kubernetes_policy_repository import KubernetesConfigMapPolicyRepository
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


def test_load_rejects_malformed_policy_json():
    repository = _repository(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "metadata": {"resourceVersion": "17"},
                    "data": {"policy-store.json": "not json"},
                },
            )
        )
    )

    with pytest.raises(ValueError):
        repository.load()
