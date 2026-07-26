"""Offline structural checks for the Web image, proxy and Kubernetes exposure."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


def resources(path):
    with (ROOT / path).open(encoding="utf-8") as source:
        return [item for item in yaml.safe_load_all(source) if item]


class WebDeploymentTests(unittest.TestCase):
    def test_web_is_the_only_public_application_service(self):
        services = []
        for manifest in ("deploy/gateway.yaml", "deploy/mock-providers.yaml", "deploy/web.yaml"):
            services.extend(item for item in resources(manifest) if item["kind"] == "Service")
        public = [item for item in services if item["spec"].get("type", "ClusterIP") == "NodePort"]
        self.assertEqual([item["metadata"]["name"] for item in public], ["web"])
        self.assertEqual(public[0]["spec"]["ports"][0]["nodePort"], 30080)

    def test_web_workload_has_probes_resources_and_hardening(self):
        deployment = next(item for item in resources("deploy/web.yaml") if item["kind"] == "Deployment")
        self.assertGreaterEqual(deployment["spec"]["replicas"], 2)
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["securityContext"]["fsGroup"], 101)
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/healthz")
        self.assertEqual(container["livenessProbe"]["httpGet"]["path"], "/healthz")
        mounts = {
            mount["mountPath"]: mount["name"]
            for mount in container["volumeMounts"]
        }
        self.assertEqual(mounts["/etc/nginx/conf.d"], "nginx-conf")
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        self.assertEqual(volumes["nginx-conf"]["emptyDir"], {})
        self.assertIn("requests", container["resources"])
        self.assertIn("limits", container["resources"])

    def test_nginx_proxies_api_and_sets_browser_security_headers(self):
        config = (ROOT / "web/nginx/default.conf").read_text(encoding="utf-8")
        self.assertIn("location /api/v1/", config)
        self.assertIn("proxy_pass http://gateway:8000/v1/", config)
        self.assertIn("location /api/", config)
        self.assertIn("return 404;", config)
        self.assertIn("location = /healthz", config)
        self.assertIn("Content-Security-Policy", config)
        self.assertIn("frame-ancestors 'none'", config)
        self.assertIn("X-Content-Type-Options", config)

    def test_compose_web_uses_the_same_container_and_relative_proxy(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        web = compose["services"]["web"]
        self.assertEqual(web["build"], "./web")
        self.assertIn("gateway", web["depends_on"])
        self.assertIn("8080:8080", web["ports"])
        self.assertEqual(
            web["healthcheck"]["test"],
            ["CMD", "wget", "-qO-", "http://localhost:8080/healthz"],
        )

    def test_compose_accepts_worker_gateway_identity(self):
        compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("POLYGATE_API_KEY: ${POLYGATE_API_KEY:-}", compose_text)

    def test_web_image_is_static_and_non_privileged(self):
        dockerfile = (ROOT / "web/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("npm ci", dockerfile)
        self.assertIn("npm run build", dockerfile)
        self.assertIn("nginxinc/nginx-unprivileged", dockerfile)
        self.assertIn(
            "wget -qO- http://127.0.0.1:8080/healthz",
            dockerfile,
        )
        self.assertNotIn(
            "wget -qO- http://127.0.0.1:8080/api/v1/models",
            dockerfile,
        )
        self.assertNotIn("npm run dev", dockerfile)


if __name__ == "__main__":
    unittest.main()
