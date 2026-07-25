"""
Task 5/6: Gateway-side Policy runtime.

Loads the mounted policy-store.json at startup (Last Known Good baseline),
then polls the Policy API's GET /v1/policies/active every
POLICY_REFRESH_SECONDS (default 5) and atomically swaps in a new snapshot
only after it passes validation. Any failure (timeout, transport error,
non-200/304, or an invalid policy body) leaves the current snapshot
untouched — Last Known Good.

Confirmed with C: mounted file path env var is POLICY_FILE (default
/config/policy-store.json, ConfigMap mounted as a directory without
subPath so Kubernetes updates propagate). Policy API base URL env var is
POLICY_API_URL=http://automation:8020, confirmed unchanged.

Task 6: every load attempt (initial mount + each refresh) reports
polygate_policy_loaded_version{component="gateway"} on success, or
polygate_policy_reload_failures_total{component="gateway",reason=...} on
failure. reason is one of network|http|validation|file per contracts/README.md.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.metrics import record_policy_loaded_version, record_policy_reload_failure

log = logging.getLogger("polygate.gateway.policy")

_COMPONENT = "gateway"  # Task 6: fixed component label per contracts/README.md

DEFAULT_REFRESH_SECONDS = 5
DEFAULT_STORE_PATH = "/config/policy-store.json"
ACTIVE_POLICY_PATH = "/v1/policies/active"


class GatewayRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assumed_output_tokens: int = Field(ge=1, le=32768)
    balanced_price_tolerance: float = Field(ge=0, le=2)
    budget_mode: Literal["soft", "hard"]
    latency_mode: Literal["soft", "hard"]
    high_quality_strategy: Literal["prefer_real", "lowest_cost"]


class GatewayPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    gateway: GatewayRoutingPolicy


DEFAULT_GATEWAY_POLICY = GatewayRoutingPolicy(
    assumed_output_tokens=256,
    balanced_price_tolerance=0.20,
    budget_mode="soft",
    latency_mode="soft",
    high_quality_strategy="prefer_real",
)


class PolicyLoadError(Exception):
    """Raised only during the very first (mounted-file) load; there is no
    Last Known Good to fall back to yet, so this is allowed to propagate."""


def _extract_gateway_policy_from_store_version(version_record: dict) -> GatewayRoutingPolicy:
    gateway_dict = version_record["policy"]["gateway"]
    return GatewayRoutingPolicy.model_validate(gateway_dict)


class GatewayPolicyRuntime:
    """Holds one immutable GatewayPolicySnapshot, refreshed from the Policy API."""

    def __init__(
        self,
        store_path: str | Path | None = None,
        api_base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        refresh_interval_seconds: int | None = None,
    ) -> None:
        self._store_path = Path(store_path or os.environ.get("POLICY_FILE", DEFAULT_STORE_PATH))
        self._api_base_url = (api_base_url or os.environ.get("POLICY_API_URL", "http://automation:8020")).rstrip("/")
        self.refresh_interval_seconds = refresh_interval_seconds or int(
            os.environ.get("POLICY_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS)
        )
        self._lock = threading.Lock()
        self._etag: str | None = None
        self._client = httpx.Client(transport=transport, timeout=5.0)

        self._snapshot = self._load_initial_snapshot()

    def _load_initial_snapshot(self) -> GatewayPolicySnapshot:
        try:
            raw = self._store_path.read_text(encoding="utf-8")
            store = json.loads(raw)
            active_version = store["active_version"]
            version_record = next(
                v for v in store["versions"] if v["version"] == active_version
            )
            gateway_policy = _extract_gateway_policy_from_store_version(version_record)
            snapshot = GatewayPolicySnapshot(version=active_version, gateway=gateway_policy)
            record_policy_loaded_version(_COMPONENT, snapshot.version)  # Task 6
            return snapshot
        except FileNotFoundError:
            log.warning(
                '{"event":"policy_store_not_mounted","fallback":"safe_v1_defaults"}'
            )
            record_policy_reload_failure(_COMPONENT, "file")  # Task 6
            fallback = GatewayPolicySnapshot(version=1, gateway=DEFAULT_GATEWAY_POLICY)
            record_policy_loaded_version(_COMPONENT, fallback.version)  # Task 6
            return fallback
        except json.JSONDecodeError as exc:
            record_policy_reload_failure(_COMPONENT, "file")  # Task 6
            raise PolicyLoadError(f"mounted policy-store.json is invalid JSON: {exc}") from exc
        except (KeyError, StopIteration, ValidationError) as exc:
            record_policy_reload_failure(_COMPONENT, "validation")  # Task 6
            raise PolicyLoadError(f"mounted policy-store.json is invalid: {exc}") from exc

    def snapshot(self) -> GatewayPolicySnapshot:
        with self._lock:
            return self._snapshot

    def refresh_once(self) -> None:
        """Poll the Policy API once. Any failure preserves the current
        snapshot (Last Known Good) and is logged, never raised."""
        headers = {"If-None-Match": self._etag} if self._etag else {}
        try:
            response = self._client.get(
                f"{self._api_base_url}{ACTIVE_POLICY_PATH}", headers=headers
            )
        except httpx.TransportError as exc:
            log.warning(f'{{"event":"policy_refresh_transport_error","err":"{exc}"}}')
            record_policy_reload_failure(_COMPONENT, "network")  # Task 6
            return

        if response.status_code == 304:
            return

        if response.status_code != 200:
            log.warning(
                f'{{"event":"policy_refresh_unexpected_status","status":{response.status_code}}}'
            )
            record_policy_reload_failure(_COMPONENT, "http")  # Task 6
            return

        try:
            body = response.json()
            gateway_policy = GatewayRoutingPolicy.model_validate(body["policy"]["gateway"])
            new_snapshot = GatewayPolicySnapshot(version=body["version"], gateway=gateway_policy)
        except (KeyError, ValidationError, ValueError) as exc:
            log.warning(f'{{"event":"policy_refresh_invalid_body","err":"{exc}"}}')
            record_policy_reload_failure(_COMPONENT, "validation")  # Task 6
            return

        with self._lock:
            self._snapshot = new_snapshot
        self._etag = response.headers.get("ETag", self._etag)
        record_policy_loaded_version(_COMPONENT, new_snapshot.version)  # Task 6
        log.info(f'{{"event":"policy_reloaded","version":{new_snapshot.version}}}')

    def close(self) -> None:
        self._client.close()