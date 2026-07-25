#!/usr/bin/env python3
"""Offline structural regression checks for Policy Kubernetes manifests."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment setup guard
    raise SystemExit(
        "PyYAML is required; install scripts/requirements-preflight.txt"
    ) from exc


ROOT_DIR = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT_DIR / "deploy"
RBAC_PATH = DEPLOY_DIR / "policy-rbac.yaml"
AUTOMATION_PATH = DEPLOY_DIR / "automation.yaml"
GATEWAY_PATH = DEPLOY_DIR / "gateway.yaml"


def load_documents(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [
            document
            for document in yaml.safe_load_all(source)
            if isinstance(document, dict)
        ]


def resource(
    documents: list[dict[str, Any]],
    kind: str,
    name: str,
) -> dict[str, Any]:
    matches = [
        document
        for document in documents
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {kind}/{name}, found {len(matches)}"
        )
    return matches[0]


def container_env(container: dict[str, Any]) -> dict[str, Any]:
    return {
        item["name"]: item.get("value")
        for item in container.get("env", [])
    }


def count_string_references(value: Any, target: str) -> int:
    if isinstance(value, dict):
        return sum(
            count_string_references(item, target)
            for item in value.values()
        )
    if isinstance(value, list):
        return sum(
            count_string_references(item, target)
            for item in value
        )
    return int(value == target)


def validate_rbac(documents: list[dict[str, Any]]) -> None:
    resource(documents, "ServiceAccount", "polygate-policy-controller")

    role = resource(documents, "Role", "polygate-policy-controller")
    expected_rules = [
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "resourceNames": ["polygate-routing-policy"],
            "verbs": ["get", "update"],
        }
    ]
    if role.get("rules") != expected_rules:
        raise AssertionError(
            "Policy Role must contain exactly the single ConfigMap rule"
        )

    binding = resource(
        documents,
        "RoleBinding",
        "polygate-policy-controller",
    )
    expected_subjects = [
        {
            "kind": "ServiceAccount",
            "name": "polygate-policy-controller",
        }
    ]
    if binding.get("subjects") != expected_subjects:
        raise AssertionError(
            "Policy RoleBinding must target only the controller ServiceAccount"
        )
    expected_role_ref = {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": "polygate-policy-controller",
    }
    if binding.get("roleRef") != expected_role_ref:
        raise AssertionError(
            "Policy RoleBinding must target the controller Role"
        )


def validate_policy_mount(
    deployment: dict[str, Any],
    *,
    expected_container: str,
) -> None:
    pod_spec = deployment["spec"]["template"]["spec"]
    containers = [
        container
        for container in pod_spec.get("containers", [])
        if container.get("name") == expected_container
    ]
    if len(containers) != 1:
        raise AssertionError(
            f"expected one container named {expected_container}"
        )
    container = containers[0]

    policy_volumes: list[dict[str, Any]] = []
    for volume in pod_spec.get("volumes", []):
        sources = volume.get("projected", {}).get("sources", [])
        matching_sources = [
            source
            for source in sources
            if source.get("configMap", {}).get("name")
            == "polygate-routing-policy"
        ]
        if matching_sources:
            if len(matching_sources) != 1:
                raise AssertionError(
                    "Policy volume must contain one ConfigMap source"
                )
            if matching_sources[0]["configMap"].get("items") != [
                {
                    "key": "policy-store.json",
                    "path": "policy-store.json",
                }
            ]:
                raise AssertionError(
                    "Policy ConfigMap source must project policy-store.json"
                )
            policy_volumes.append(volume)
    if len(policy_volumes) != 1:
        raise AssertionError(
            f"{expected_container} must have exactly one projected Policy volume"
        )

    policy_volume_name = policy_volumes[0]["name"]
    policy_mounts = [
        mount
        for mount in container.get("volumeMounts", [])
        if mount.get("name") == policy_volume_name
    ]
    if len(policy_mounts) != 1:
        raise AssertionError(
            f"{expected_container} must mount its projected Policy volume"
        )
    policy_mount = policy_mounts[0]
    if policy_mount.get("mountPath") != "/config":
        raise AssertionError("Policy volume must mount at /config")
    if policy_mount.get("readOnly") is not True:
        raise AssertionError("Policy volume must be read-only")
    if "subPath" in policy_mount:
        raise AssertionError("Policy volume must not use subPath")

    env = container_env(container)
    if env.get("POLICY_FILE") != "/config/policy-store.json":
        raise AssertionError("POLICY_FILE must use the mounted store")
    if env.get("POLICY_REFRESH_SECONDS") != "5":
        raise AssertionError("POLICY_REFRESH_SECONDS must be 5")


def validate_workloads(
    automation_documents: list[dict[str, Any]],
    gateway_documents: list[dict[str, Any]],
) -> None:
    automation = resource(
        automation_documents,
        "Deployment",
        "automation",
    )
    worker = resource(
        automation_documents,
        "Deployment",
        "automation-worker",
    )
    gateway = resource(gateway_documents, "Deployment", "gateway")

    validate_policy_mount(automation, expected_container="automation")
    validate_policy_mount(worker, expected_container="automation-worker")
    validate_policy_mount(gateway, expected_container="gateway")

    automation_spec = automation["spec"]["template"]["spec"]
    if (
        automation_spec.get("serviceAccountName")
        != "polygate-policy-controller"
    ):
        raise AssertionError("Automation must use the Policy controller SA")
    if "serviceAccount" in automation_spec:
        raise AssertionError("Automation must use serviceAccountName only")
    if automation_spec.get("automountServiceAccountToken", True) is not True:
        raise AssertionError("Automation needs its ServiceAccount token")

    worker_spec = worker["spec"]["template"]["spec"]
    if worker_spec.get("automountServiceAccountToken") is not False:
        raise AssertionError("Worker must keep ServiceAccount token disabled")
    if (
        worker_spec.get("serviceAccountName")
        == "polygate-policy-controller"
        or worker_spec.get("serviceAccount")
        == "polygate-policy-controller"
    ):
        raise AssertionError("Worker must not use the Policy controller SA")

    gateway_spec = gateway["spec"]["template"]["spec"]
    if (
        gateway_spec.get("serviceAccountName")
        == "polygate-policy-controller"
        or gateway_spec.get("serviceAccount")
        == "polygate-policy-controller"
    ):
        raise AssertionError("Gateway must not use the Policy controller SA")

    for pod_spec, container_name in (
        (worker_spec, "automation-worker"),
        (gateway_spec, "gateway"),
    ):
        if count_string_references(
            pod_spec,
            "polygate-policy-admin",
        ):
            raise AssertionError(
                f"{container_name} must not reference the Policy admin Secret"
            )
        container = next(
            container
            for container in pod_spec["containers"]
            if container["name"] == container_name
        )
        env = container_env(container)
        if env.get("POLICY_API_URL") != "http://automation:8020":
            raise AssertionError(
                f"{container_name} must use the private Policy API"
            )
        if "POLICY_ADMIN_KEY" in env or "POLICY_ADMIN_KEY_FILE" in env:
            raise AssertionError(
                f"{container_name} must not receive Policy admin credentials"
            )

    automation_container = next(
        container
        for container in automation_spec["containers"]
        if container["name"] == "automation"
    )
    automation_env = container_env(automation_container)
    expected_automation_env = {
        "POLICY_ADMIN_KEY_FILE": (
            "/var/run/secrets/polygate-policy/admin-key"
        ),
        "POLICY_CONFIGMAP_NAME": "polygate-routing-policy",
        "POLICY_CONFIGMAP_KEY": "policy-store.json",
    }
    for name, value in expected_automation_env.items():
        if automation_env.get(name) != value:
            raise AssertionError(f"Automation {name} is not correctly wired")
    if (
        "POLICY_ADMIN_KEY" in automation_env
        or "POLICY_ALLOW_ENV_ADMIN_KEY" in automation_env
    ):
        raise AssertionError(
            "Kubernetes Automation must not receive a plaintext admin key"
        )
    pod_namespace_entries = [
        item
        for item in automation_container.get("env", [])
        if item.get("name") == "POD_NAMESPACE"
    ]
    if pod_namespace_entries != [
        {
            "name": "POD_NAMESPACE",
            "valueFrom": {
                "fieldRef": {"fieldPath": "metadata.namespace"}
            },
        }
    ]:
        raise AssertionError(
            "Automation POD_NAMESPACE must come from metadata.namespace"
        )

    admin_volumes = [
        volume
        for volume in automation_spec.get("volumes", [])
        if volume.get("secret", {}).get("secretName")
        == "polygate-policy-admin"
    ]
    if len(admin_volumes) != 1:
        raise AssertionError("Automation must have one Policy admin volume")
    if (
        count_string_references(
            automation_spec,
            "polygate-policy-admin",
        )
        != 1
    ):
        raise AssertionError(
            "Automation must reference the Policy admin Secret exactly once"
        )
    if admin_volumes[0]["secret"].get("items") != [
        {"key": "admin-key", "path": "admin-key"}
    ]:
        raise AssertionError(
            "Policy admin volume must project only the admin-key"
        )
    admin_volume_name = admin_volumes[0]["name"]
    admin_mounts = [
        mount
        for mount in automation_container.get("volumeMounts", [])
        if mount.get("name") == admin_volume_name
    ]
    expected_admin_mount = {
        "name": admin_volume_name,
        "mountPath": "/var/run/secrets/polygate-policy",
        "readOnly": True,
    }
    if admin_mounts != [expected_admin_mount]:
        raise AssertionError(
            "Automation must mount the admin Secret read-only"
        )


def validate_service_exposure(
    documents: list[dict[str, Any]],
) -> None:
    for document in documents:
        if document.get("kind") != "Service":
            continue
        name = document.get("metadata", {}).get("name", "")
        service_type = document.get("spec", {}).get("type", "ClusterIP")
        if "policy" in name.lower():
            raise AssertionError("Policy must not have a separate Service")
        if service_type in {"NodePort", "LoadBalancer"} and not (
            name == "web" and service_type == "NodePort"
        ):
            raise AssertionError(
                f"only Service/web may be public, found {name}/{service_type}"
            )


def all_deploy_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    paths = set(DEPLOY_DIR.rglob("*.yaml"))
    paths.update(DEPLOY_DIR.rglob("*.yml"))
    for path in sorted(paths):
        documents.extend(load_documents(path))
    return documents


class PolicyManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rbac = load_documents(RBAC_PATH)
        self.automation = load_documents(AUTOMATION_PATH)
        self.gateway = load_documents(GATEWAY_PATH)

    def test_current_policy_manifests_are_structurally_safe(self) -> None:
        validate_rbac(self.rbac)
        validate_workloads(self.automation, self.gateway)
        validate_service_exposure(all_deploy_documents())

    def test_extra_flow_style_role_rule_is_rejected(self) -> None:
        documents = copy.deepcopy(self.rbac)
        role = resource(
            documents,
            "Role",
            "polygate-policy-controller",
        )
        role["rules"].append(
            {
                "apiGroups": [""],
                "resources": ["configmaps"],
                "resourceNames": ["another-configmap"],
                "verbs": ["get", "update"],
            }
        )
        with self.assertRaisesRegex(AssertionError, "exactly the single"):
            validate_rbac(documents)

    def test_wrong_rolebinding_subject_is_rejected(self) -> None:
        documents = copy.deepcopy(self.rbac)
        binding = resource(
            documents,
            "RoleBinding",
            "polygate-policy-controller",
        )
        binding["subjects"][0]["name"] = "default"
        with self.assertRaisesRegex(AssertionError, "only the controller"):
            validate_rbac(documents)

    def test_unmounted_policy_volume_is_rejected(self) -> None:
        automation = copy.deepcopy(self.automation)
        worker = resource(automation, "Deployment", "automation-worker")
        container = worker["spec"]["template"]["spec"]["containers"][0]
        for mount in container["volumeMounts"]:
            if mount["name"] == "policy-config":
                mount["name"] = "tmp"
        with self.assertRaisesRegex(AssertionError, "must mount"):
            validate_workloads(automation, self.gateway)

    def test_gateway_policy_controller_service_account_is_rejected(
        self,
    ) -> None:
        gateway = copy.deepcopy(self.gateway)
        deployment = resource(gateway, "Deployment", "gateway")
        deployment["spec"]["template"]["spec"][
            "serviceAccountName"
        ] = "polygate-policy-controller"
        with self.assertRaisesRegex(AssertionError, "Gateway must not"):
            validate_workloads(self.automation, gateway)

    def test_gateway_legacy_policy_controller_service_account_is_rejected(
        self,
    ) -> None:
        gateway = copy.deepcopy(self.gateway)
        deployment = resource(gateway, "Deployment", "gateway")
        deployment["spec"]["template"]["spec"][
            "serviceAccount"
        ] = "polygate-policy-controller"
        with self.assertRaisesRegex(AssertionError, "Gateway must not"):
            validate_workloads(self.automation, gateway)

    def test_worker_admin_secret_volume_is_rejected(self) -> None:
        automation = copy.deepcopy(self.automation)
        worker = resource(automation, "Deployment", "automation-worker")
        pod_spec = worker["spec"]["template"]["spec"]
        pod_spec["volumes"].append(
            {
                "name": "stolen-admin-key",
                "secret": {"secretName": "polygate-policy-admin"},
            }
        )
        pod_spec["containers"][0]["volumeMounts"].append(
            {
                "name": "stolen-admin-key",
                "mountPath": "/stolen",
                "readOnly": True,
            }
        )
        with self.assertRaisesRegex(AssertionError, "admin Secret"):
            validate_workloads(automation, self.gateway)

    def test_gateway_projected_admin_secret_is_rejected(self) -> None:
        gateway = copy.deepcopy(self.gateway)
        deployment = resource(gateway, "Deployment", "gateway")
        pod_spec = deployment["spec"]["template"]["spec"]
        pod_spec["volumes"].append(
            {
                "name": "projected-admin-key",
                "projected": {
                    "sources": [
                        {
                            "secret": {
                                "name": "polygate-policy-admin",
                            }
                        }
                    ]
                },
            }
        )
        with self.assertRaisesRegex(AssertionError, "admin Secret"):
            validate_workloads(self.automation, gateway)

    def test_worker_admin_secret_env_is_rejected(self) -> None:
        automation = copy.deepcopy(self.automation)
        worker = resource(automation, "Deployment", "automation-worker")
        container = worker["spec"]["template"]["spec"]["containers"][0]
        container["env"].append(
            {
                "name": "STOLEN_ADMIN_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "polygate-policy-admin",
                        "key": "admin-key",
                    }
                },
            }
        )
        with self.assertRaisesRegex(AssertionError, "admin Secret"):
            validate_workloads(automation, self.gateway)

    def test_gateway_admin_secret_env_from_is_rejected(self) -> None:
        gateway = copy.deepcopy(self.gateway)
        deployment = resource(gateway, "Deployment", "gateway")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        container["envFrom"] = [
            {
                "secretRef": {
                    "name": "polygate-policy-admin",
                }
            }
        ]
        with self.assertRaisesRegex(AssertionError, "admin Secret"):
            validate_workloads(self.automation, gateway)

    def test_policy_mount_subpath_is_rejected(self) -> None:
        automation = copy.deepcopy(self.automation)
        worker = resource(automation, "Deployment", "automation-worker")
        container = worker["spec"]["template"]["spec"]["containers"][0]
        next(
            mount
            for mount in container["volumeMounts"]
            if mount["name"] == "policy-config"
        )["subPath"] = "policy-store.json"
        with self.assertRaisesRegex(AssertionError, "must not use subPath"):
            validate_workloads(automation, self.gateway)

    def test_unrelated_subpath_is_allowed(self) -> None:
        automation = copy.deepcopy(self.automation)
        worker = resource(automation, "Deployment", "automation-worker")
        container = worker["spec"]["template"]["spec"]["containers"][0]
        container["volumeMounts"].append(
            {
                "name": "unrelated",
                "mountPath": "/unrelated",
                "subPath": "allowed-for-unrelated-data",
            }
        )
        worker["spec"]["template"]["spec"]["volumes"].append(
            {"name": "unrelated", "emptyDir": {}}
        )
        validate_workloads(automation, self.gateway)

    def test_policy_nodeport_in_any_deploy_manifest_is_rejected(self) -> None:
        documents = all_deploy_documents()
        documents.append(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "policy-editor"},
                "spec": {"type": "NodePort"},
            }
        )
        with self.assertRaisesRegex(AssertionError, "separate Service"):
            validate_service_exposure(documents)


if __name__ == "__main__":
    unittest.main(verbosity=2)
