"""Prometheus metrics emitted by the PolyGate gateway.

Keep labels low-cardinality: provider names and a small, fixed set of outcomes
are safe; request IDs, prompts, reasons, and raw error messages belong in logs.
"""

from prometheus_client import Counter, Histogram, generate_latest


REQUESTS = Counter(
    "polygate_requests_total",
    "Chat completion requests handled by the gateway.",
    ["outcome"],
)
REQUEST_DURATION = Histogram(
    "polygate_request_duration_seconds",
    "End-to-end chat completion request duration.",
    ["outcome"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
CACHE_REQUESTS = Counter(
    "polygate_cache_requests_total",
    "Gateway cache lookups.",
    ["result"],
)
PROVIDER_REQUESTS = Counter(
    "polygate_provider_requests_total",
    "Calls from the gateway to a provider.",
    ["provider", "outcome"],
)
PROVIDER_DURATION = Histogram(
    "polygate_provider_duration_seconds",
    "Provider call duration observed by the gateway.",
    ["provider", "outcome"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
TOKENS = Counter(
    "polygate_tokens_total",
    "Tokens consumed by successful provider calls.",
    ["provider", "direction"],
)
ESTIMATED_COST = Counter(
    "polygate_estimated_cost_usd_total",
    "Estimated USD cost of successful provider calls.",
    ["provider"],
)


def record_request(outcome: str, duration_seconds: float) -> None:
    REQUESTS.labels(outcome=outcome).inc()
    REQUEST_DURATION.labels(outcome=outcome).observe(duration_seconds)


def record_cache(result: str) -> None:
    CACHE_REQUESTS.labels(result=result).inc()


def record_provider(
    provider: str,
    outcome: str,
    duration_seconds: float,
) -> None:
    PROVIDER_REQUESTS.labels(provider=provider, outcome=outcome).inc()
    PROVIDER_DURATION.labels(provider=provider, outcome=outcome).observe(duration_seconds)


def record_usage(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost_usd: float,
) -> None:
    TOKENS.labels(provider=provider, direction="input").inc(input_tokens)
    TOKENS.labels(provider=provider, direction="output").inc(output_tokens)
    ESTIMATED_COST.labels(provider=provider).inc(estimated_cost_usd)


def render_metrics() -> bytes:
    """Return the current registry in Prometheus' text exposition format."""
    return generate_latest()
