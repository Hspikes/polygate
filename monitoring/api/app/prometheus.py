"""Small client for the fixed Prometheus queries used by Monitoring API."""

from dataclasses import dataclass
import math

import httpx


class PrometheusError(RuntimeError):
    """Raised when Prometheus cannot execute or return a query."""


@dataclass(frozen=True)
class Sample:
    labels: dict[str, str]
    value: float


class PrometheusClient:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def ready(self) -> bool:
        try:
            response = httpx.get(
                f"{self.base_url}/-/ready",
                timeout=self.timeout_seconds,
            )
            return response.is_success
        except httpx.HTTPError:
            return False

    def query_many(self, expressions: dict[str, str]) -> dict[str, list[Sample]]:
        results: dict[str, list[Sample]] = {}
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                for name, expression in expressions.items():
                    response = client.get(
                        "/api/v1/query",
                        params={"query": expression},
                    )
                    response.raise_for_status()
                    results[name] = self._parse_vector(response.json())
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise PrometheusError(f"Prometheus query failed: {exc}") from exc
        return results

    @staticmethod
    def _parse_vector(payload: dict) -> list[Sample]:
        if payload.get("status") != "success":
            raise PrometheusError(
                f"Prometheus returned status {payload.get('status', 'unknown')}"
            )

        data = payload["data"]
        if data["resultType"] != "vector":
            raise PrometheusError(
                f"Expected vector result, got {data['resultType']}"
            )

        samples: list[Sample] = []
        for item in data["result"]:
            value = float(item["value"][1])
            samples.append(
                Sample(
                    labels={
                        str(key): str(label_value)
                        for key, label_value in item.get("metric", {}).items()
                    },
                    value=value,
                )
            )
        return samples


def finite_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return value
