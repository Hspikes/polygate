"""Prometheus metrics emitted by the PolyGate gateway.

Keep labels low-cardinality: provider names and a small, fixed set of outcomes
are safe; request IDs, prompts, reasons, and raw error messages belong in logs.
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest


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
CIRCUIT_STATE = Gauge(
    "polygate_circuit_state",
    "Provider circuit breaker state as a one-hot gauge.",
    ["provider", "state"],
)
CIRCUIT_STATES = ("closed", "open", "half_open")
PROVIDER_RETRIES = Counter(
    "polygate_provider_retries_total",
    "Provider retries performed before a terminal result.",
    ["provider", "reason"],
)
FAILOVERS = Counter(
    "polygate_failovers_total",
    "Automatic provider failovers performed before downstream output.",
    ["from_provider", "to_provider"],
)
STREAMS = Counter(
    "polygate_streams_total",
    "Streaming chat requests by terminal outcome.",
    ["outcome"],
)
REQUEST_BUDGET_EXHAUSTED = Counter(
    "polygate_request_budget_exhausted_total",
    "Gateway request budgets exhausted before a provider result.",
    ["phase"],
)
POLICY_LOADED_VERSION = Gauge(
    "polygate_policy_loaded_version",
    "Policy version loaded by this component.",
    ["component"],
)
POLICY_RELOAD_FAILURES = Counter(
    "polygate_policy_reload_failures_total",
    "Policy reload failures.",
    ["component", "reason"],
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


def record_provider_retry(provider: str, reason: str) -> None:
    PROVIDER_RETRIES.labels(provider=provider, reason=reason).inc()


def record_failover(from_provider: str, to_provider: str) -> None:
    FAILOVERS.labels(
        from_provider=from_provider,
        to_provider=to_provider,
    ).inc()


def record_stream(outcome: str) -> None:
    STREAMS.labels(outcome=outcome).inc()


def record_budget_exhausted(phase: str) -> None:
    REQUEST_BUDGET_EXHAUSTED.labels(phase=phase).inc()

def record_policy_loaded_version(component: str, version: int) -> None:
    POLICY_LOADED_VERSION.labels(component=component).set(version)


def record_policy_reload_failure(component: str, reason: str) -> None:
    POLICY_RELOAD_FAILURES.labels(component=component, reason=reason).inc()


def record_usage(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost_usd: float,
) -> None:
    TOKENS.labels(provider=provider, direction="input").inc(input_tokens)
    TOKENS.labels(provider=provider, direction="output").inc(output_tokens)
    ESTIMATED_COST.labels(provider=provider).inc(estimated_cost_usd)


def render_metrics(
    provider_circuit_states: dict[str, str] | None = None,
) -> bytes:
    """Return the current registry in Prometheus' text exposition format."""
    if provider_circuit_states is not None:
        for provider, current_state in provider_circuit_states.items():
            for state in CIRCUIT_STATES:
                CIRCUIT_STATE.labels(provider=provider, state=state).set(
                    1 if state == current_state else 0
                )
    return generate_latest()
