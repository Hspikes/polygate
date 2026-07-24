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
        "gateway_up": 'max(up{job="polygate-gateway"})',
        "requests_total": "sum(polygate_requests_total)",
        "request_rate": f"sum(rate(polygate_requests_total[{window}]))",
        "error_rate": (
            "(sum(rate(polygate_requests_total"
            "{outcome=~\"routing_error|provider_error|provider_timeout|"
            f'server_error|partial_error\"}}[{window}])) or vector(0)) '
            "/ sum(rate(polygate_requests_total"
            f'{{outcome!~"client_error|cancelled"}}[{window}]))'
        ),
        "client_rejection_rate": (
            "(sum(rate(polygate_requests_total"
            f'{{outcome="client_error"}}[{window}])) or vector(0)) '
            f"/ sum(rate(polygate_requests_total[{window}]))"
        ),
        "cancellation_rate": (
            "(sum(rate(polygate_requests_total"
            f'{{outcome="cancelled"}}[{window}])) or vector(0)) '
            f"/ sum(rate(polygate_requests_total[{window}]))"
        ),
        "request_p95": (
            "histogram_quantile(0.95, "
            "sum by (le) "
            f"(rate(polygate_request_duration_seconds_bucket[{window}]))) * 1000"
        ),
        "cache_total": "sum(polygate_cache_requests_total)",
        "cache_hit_rate": (
            "(sum(rate(polygate_cache_requests_total"
            f'{{result="hit"}}[{window}])) or vector(0)) '
            f"/ sum(rate(polygate_cache_requests_total[{window}]))"
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
            "(sum by (provider) (rate(polygate_provider_requests_total"
            f'{{outcome="success"}}[{window}])) '
            "or on (provider) "
            "(0 * sum by (provider) (rate(polygate_provider_requests_total"
            f'{{outcome!="cancelled"}}[{window}])))) '
            "/ sum by (provider) (rate(polygate_provider_requests_total"
            f'{{outcome!="cancelled"}}[{window}]))'
        ),
        "provider_p95": (
            "histogram_quantile(0.95, "
            "sum by (provider, le) "
            "(rate(polygate_provider_duration_seconds_bucket"
            f'{{outcome!="cancelled"}}[{window}]))) '
            "* 1000"
        ),
    }


def build_overview(
    prometheus: PrometheusClient,
    window: Window,
) -> MonitoringOverview:
    results = prometheus.query_many(overview_queries(window))

    gateway_up = finite_or_none(_first(results["gateway_up"]))
    request_rate = _non_negative(_first(results["request_rate"]))
    error_rate = _optional_ratio(_first(results["error_rate"]))
    client_rejection_rate = _optional_ratio(
        _first(results["client_rejection_rate"])
    )
    cancellation_rate = _optional_ratio(
        _first(results["cancellation_rate"])
    )
    request_p95 = finite_or_none(_first(results["request_p95"]))
    cache_hit_rate = _optional_ratio(_first(results["cache_hit_rate"]))

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
            success_rate=_optional_ratio(provider_success.get(name)),
            p95_latency_ms=finite_or_none(provider_p95.get(name)),
        )
        for name in provider_names
    ]

    partial = gateway_up != 1.0
    warnings: list[str] = []
    if gateway_up is None:
        warnings.append(
            "Gateway scrape target is missing from Prometheus; "
            "metrics may be incomplete or stale."
        )
    elif gateway_up != 1.0:
        warnings.append(
            "Gateway scrape target is DOWN; "
            "metrics may be incomplete or stale."
        )
    else:
        if error_rate is None:
            if client_rejection_rate is None and cancellation_rate is None:
                warnings.append(
                    "No Gateway request traffic in the selected window; "
                    "service error rate and P95 latency are unavailable."
                )
            else:
                warnings.append(
                    "No service-eligible Gateway traffic in the selected "
                    "window; service error rate is unavailable."
                )
        if cache_hit_rate is None:
            warnings.append(
                "No cache lookups in the selected window; "
                "cache hit rate is unavailable."
            )
        if providers and all(
            provider.success_rate is None for provider in providers
        ):
            warnings.append(
                "No provider calls in the selected window; "
                "provider success rates and P95 latencies are unavailable."
            )

    return MonitoringOverview(
        generated_at=datetime.now(timezone.utc),
        window=window,
        gateway=GatewayMetrics(
            requests_total=_counter(_first(results["requests_total"])),
            requests_per_second=request_rate,
            error_rate=error_rate,
            client_rejection_rate=client_rejection_rate,
            cancellation_rate=cancellation_rate,
            p95_latency_ms=request_p95,
        ),
        cache=CacheMetrics(
            lookups_total=_counter(_first(results["cache_total"])),
            hit_rate=cache_hit_rate,
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
        partial=partial,
        warnings=warnings,
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


def _optional_ratio(value: float | None) -> float | None:
    finite = finite_or_none(value)
    if finite is None:
        return None
    return min(1.0, max(0.0, finite))
