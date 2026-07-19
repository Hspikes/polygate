"""Turn fixed Prometheus query results into the frontend-facing overview."""

from datetime import datetime, timezone

from app.models import (
    CacheMetrics,
    GatewayMetrics,
    MonitoringOverview,
    ProviderMetrics,
    ResourceMetrics,
    UsageMetrics,
    Window,
)
from app.prometheus import PrometheusClient, Sample, finite_or_none


def overview_queries(window: Window) -> dict[str, str]:
    return {
        "requests_total": "sum(polygate_requests_total)",
        "request_rate": f"sum(rate(polygate_requests_total[{window}]))",
        "error_rate": (
            "sum(rate(polygate_requests_total"
            f'{{outcome=~"routing_error|provider_error"}}[{window}])) '
            "/ clamp_min("
            f"sum(rate(polygate_requests_total[{window}])), 1e-12)"
        ),
        "request_p95": (
            "histogram_quantile(0.95, "
            "sum by (le) "
            f"(rate(polygate_request_duration_seconds_bucket[{window}]))) * 1000"
        ),
        "cache_total": "sum(polygate_cache_requests_total)",
        "cache_hit_rate": (
            "sum(rate(polygate_cache_requests_total"
            f'{{result="hit"}}[{window}])) '
            "/ clamp_min("
            f"sum(rate(polygate_cache_requests_total[{window}])), 1e-12)"
        ),
        "input_tokens": (
            'sum(polygate_tokens_total{direction="input"})'
        ),
        "output_tokens": (
            'sum(polygate_tokens_total{direction="output"})'
        ),
        "estimated_cost": "sum(polygate_estimated_cost_usd_total)",
        "provider_requests": (
            "sum by (provider) (polygate_provider_requests_total)"
        ),
        "provider_success_rate": (
            "sum by (provider) (rate(polygate_provider_requests_total"
            f'{{outcome="success"}}[{window}])) '
            "/ clamp_min("
            "sum by (provider) (rate(polygate_provider_requests_total"
            f"[{window}])), 1e-12)"
        ),
        "provider_p95": (
            "histogram_quantile(0.95, "
            "sum by (provider, le) "
            f"(rate(polygate_provider_duration_seconds_bucket[{window}]))) "
            "* 1000"
        ),
    }


def build_overview(
    prometheus: PrometheusClient,
    window: Window,
) -> MonitoringOverview:
    results = prometheus.query_many(overview_queries(window))

    provider_requests = _values_by_label(
        results["provider_requests"],
        "provider",
    )
    provider_success = _values_by_label(
        results["provider_success_rate"],
        "provider",
    )
    provider_p95 = _values_by_label(
        results["provider_p95"],
        "provider",
    )
    provider_names = sorted(
        set(provider_requests) | set(provider_success) | set(provider_p95)
    )

    providers = [
        ProviderMetrics(
            name=name,
            requests=_counter(provider_requests.get(name)),
            success_rate=_ratio(provider_success.get(name)),
            p95_latency_ms=finite_or_none(provider_p95.get(name)),
        )
        for name in provider_names
    ]

    return MonitoringOverview(
        generated_at=datetime.now(timezone.utc),
        window=window,
        gateway=GatewayMetrics(
            requests_total=_counter(_first(results["requests_total"])),
            requests_per_second=_non_negative(_first(results["request_rate"])),
            error_rate=_ratio(_first(results["error_rate"])),
            p95_latency_ms=finite_or_none(_first(results["request_p95"])),
        ),
        cache=CacheMetrics(
            lookups_total=_counter(_first(results["cache_total"])),
            hit_rate=_ratio(_first(results["cache_hit_rate"])),
        ),
        usage=UsageMetrics(
            input_tokens=_counter(_first(results["input_tokens"])),
            output_tokens=_counter(_first(results["output_tokens"])),
            estimated_cost_usd=_non_negative(
                _first(results["estimated_cost"])
            ),
        ),
        providers=providers,
        resources=ResourceMetrics(),
    )


def _first(samples: list[Sample]) -> float | None:
    return samples[0].value if samples else None


def _values_by_label(
    samples: list[Sample],
    label: str,
) -> dict[str, float]:
    return {
        sample.labels[label]: sample.value
        for sample in samples
        if label in sample.labels
    }


def _counter(value: float | None) -> int:
    finite = finite_or_none(value)
    return max(0, int(finite)) if finite is not None else 0


def _non_negative(value: float | None) -> float:
    finite = finite_or_none(value)
    return max(0.0, finite) if finite is not None else 0.0


def _ratio(value: float | None) -> float:
    return min(1.0, _non_negative(value))
