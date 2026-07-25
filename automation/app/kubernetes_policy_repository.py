from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from automation.app.policy_models import PolicyStoreDocument
from automation.app.policy_repository import (
    RepositoryCorrupt,
    RepositoryConflict,
    RepositorySnapshot,
    RepositoryUnavailable,
)

SERVICE_ACCOUNT_DIRECTORY = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class KubernetesConfigMapPolicyRepository:
    """A compare-and-swap policy repository backed by one Kubernetes ConfigMap."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        namespace: str,
        configmap_name: str,
        configmap_key: str,
        api_server: str,
        token_path: Path | None = None,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._configmap_name = configmap_name
        self._configmap_key = configmap_key
        self._token_path = token_path
        self._url = (
            f"{api_server.rstrip('/')}/api/v1/namespaces/{namespace}/configmaps/"
            f"{configmap_name}"
        )

    @classmethod
    def from_environment(cls) -> "KubernetesConfigMapPolicyRepository":
        namespace = os.environ["POD_NAMESPACE"]
        configmap_name = os.environ["POLICY_CONFIGMAP_NAME"]
        configmap_key = os.environ["POLICY_CONFIGMAP_KEY"]
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
        token_path = SERVICE_ACCOUNT_DIRECTORY / "token"
        ca_path = SERVICE_ACCOUNT_DIRECTORY / "ca.crt"
        client = httpx.Client(
            verify=ca_path,
            timeout=5.0,
        )
        return cls(
            client=client,
            namespace=namespace,
            configmap_name=configmap_name,
            configmap_key=configmap_key,
            api_server=f"https://{host}:{port}",
            token_path=token_path,
        )

    def load(self) -> RepositorySnapshot:
        response = self._request("GET")
        body = self._json_object(response)
        try:
            revision = body["metadata"]["resourceVersion"]
            serialized_document = body["data"][self._configmap_key]
        except (KeyError, TypeError):
            raise RepositoryCorrupt(
                "ConfigMap does not contain a complete policy document"
            ) from None
        if not isinstance(revision, str) or not isinstance(serialized_document, str):
            raise RepositoryCorrupt("ConfigMap policy document has invalid metadata")
        try:
            document = PolicyStoreDocument.model_validate_json(serialized_document)
        except (json.JSONDecodeError, ValueError):
            raise RepositoryCorrupt("ConfigMap policy document is malformed") from None
        return RepositorySnapshot(document=document, revision=revision)

    def compare_and_swap(
        self,
        document: PolicyStoreDocument,
        expected_revision: str,
    ) -> RepositorySnapshot:
        payload = {
            "metadata": {
                "name": self._configmap_name,
                "namespace": self._namespace,
                "resourceVersion": expected_revision,
            },
            "data": {self._configmap_key: document.model_dump_json()},
        }
        response = self._request("PUT", json=payload)
        body = self._json_object(response)
        try:
            revision = body["metadata"]["resourceVersion"]
        except (KeyError, TypeError):
            raise RepositoryCorrupt(
                "ConfigMap update response has no resourceVersion"
            ) from None
        if not isinstance(revision, str):
            raise RepositoryCorrupt(
                "ConfigMap update response has invalid resourceVersion"
            )
        return RepositorySnapshot(document=document, revision=revision)

    def _request(self, method: str, **kwargs: object) -> httpx.Response:
        if self._token_path is not None:
            try:
                token = self._token_path.read_text(encoding="utf-8").strip()
            except OSError:
                raise RepositoryUnavailable(
                    "Kubernetes service account token is unavailable"
                ) from None
            kwargs["headers"] = {"Authorization": f"Bearer {token}"}
        try:
            response = self._client.request(method, self._url, **kwargs)
        except httpx.HTTPError:
            raise RepositoryUnavailable(
                "Kubernetes policy ConfigMap request failed"
            ) from None
        if response.status_code == 409:
            raise RepositoryConflict("Kubernetes policy ConfigMap revision conflict")
        if response.status_code >= 400:
            raise RepositoryUnavailable("Kubernetes policy ConfigMap is unavailable")
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            raise RepositoryCorrupt(
                "Kubernetes API returned malformed JSON"
            ) from None
        if not isinstance(body, dict):
            raise RepositoryCorrupt(
                "Kubernetes API returned a non-object JSON payload"
            )
        return body
