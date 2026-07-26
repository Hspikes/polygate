"""Keep local and Kubernetes Provider registries aligned for real backends."""
from pathlib import Path
import unittest

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _providers(document: dict) -> dict[str, dict]:
    return {provider["name"]: provider for provider in document["providers"]}


class ProviderRegistryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        local_document = yaml.safe_load(
            (PROJECT_ROOT / "contracts/providers.yaml").read_text(encoding="utf-8")
        )
        manifests = list(
            yaml.safe_load_all(
                (PROJECT_ROOT / "deploy/gateway.yaml").read_text(encoding="utf-8")
            )
        )
        provider_config = next(
            manifest
            for manifest in manifests
            if manifest.get("kind") == "ConfigMap"
            and manifest.get("metadata", {}).get("name") == "providers-config"
        )
        cluster_document = yaml.safe_load(provider_config["data"]["providers.yaml"])
        cls.local = _providers(local_document)
        cls.cluster = _providers(cluster_document)

    def test_flash_and_pro_are_registered_in_both_environments(self):
        for registry in (self.local, self.cluster):
            self.assertEqual(registry["real-a"]["model"], "deepseek-v4-flash")
            self.assertEqual(registry["real-b"]["model"], "deepseek-v4-pro")

    def test_pro_has_higher_quality_rank_and_official_cache_miss_prices(self):
        for registry in (self.local, self.cluster):
            flash = registry["real-a"]
            pro = registry["real-b"]
            self.assertGreater(pro["quality_rank"], flash["quality_rank"])
            self.assertEqual(pro["price_per_1k_input"], 0.000435)
            self.assertEqual(pro["price_per_1k_output"], 0.00087)

    def test_deepseek_models_share_credentials_and_disable_thinking(self):
        for registry in (self.local, self.cluster):
            for name in ("real-a", "real-b"):
                provider = registry[name]
                self.assertEqual(provider["api_key_env"], "REAL_A_API_KEY")
                self.assertEqual(
                    provider["request_defaults"],
                    {"thinking": {"type": "disabled"}},
                )

    def test_local_and_cluster_real_provider_metadata_match(self):
        compared_fields = {
            "api_key_env",
            "model",
            "quality_rank",
            "request_defaults",
            "price_per_1k_input",
            "price_per_1k_output",
            "privacy",
            "typical_latency_ms",
            "context_window",
            "max_output_tokens",
            "capabilities",
        }
        for name in ("real-a", "real-b"):
            self.assertEqual(
                {field: self.local[name][field] for field in compared_fields},
                {field: self.cluster[name][field] for field in compared_fields},
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
