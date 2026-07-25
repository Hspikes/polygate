from prometheus_client import Counter, Gauge

ACTIVE_VERSION = Gauge(
    "polygate_policy_active_version",
    "Active policy version served by the Policy Control Plane.",
)
PUBLICATIONS = Counter(
    "polygate_policy_publications_total",
    "Policy publication attempts.",
    ["action", "result"],
)
LAST_PUBLISH = Gauge(
    "polygate_policy_last_publish_timestamp_seconds",
    "Unix timestamp of the last successful policy publication.",
)
