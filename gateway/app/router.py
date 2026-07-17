"""
Rule-based, cost/health/budget/privacy-aware routing. THE HEART OF MEMBER A's WORK.
P0 keeps it deliberately simple and explainable; every decision produces a human-readable reason.
Extend the policy here (do NOT scatter routing logic elsewhere).
"""
from app.cost import estimate_cost, rough_input_tokens


# Assumed output size for pre-call budget filtering (we don't know real output yet).
ASSUMED_OUTPUT_TOKENS = 256


def _healthy(provider: dict, health: dict) -> bool:
    # P0: health map is optional; unknown == healthy. B's probe (P1) fills this in.
    return health.get(provider["name"], "healthy") != "down"


def select_provider(providers: list[dict], messages: list[dict], c, health: dict | None = None):
    """
    Returns (chosen_provider_dict, reason_str, candidates_debug).
    Raises RuntimeError with an explanatory message if nothing qualifies.
    """
    health = health or {}
    in_tok = rough_input_tokens(messages)
    reasons = []

    # 1. privacy filter: high privacy forbids external providers
    pool = providers
    if c.privacy == "high":
        pool = [p for p in pool if p.get("privacy") == "internal"]
        reasons.append("privacy=high → 仅保留 internal Provider")

    # 2. health filter
    pool = [p for p in pool if _healthy(p, health)]

    # 3. budget filter (rough pre-call estimate)
    def est(p):
        return estimate_cost(p, in_tok, ASSUMED_OUTPUT_TOKENS)
    affordable = [p for p in pool if est(p) <= c.max_cost_usd]
    if affordable:
        pool = affordable
    else:
        reasons.append(f"没有 Provider 在预算 ${c.max_cost_usd} 内，改选整体最便宜者")

    if not pool:
        raise RuntimeError("no eligible provider after privacy/health filtering")

    # 4. latency awareness: prefer those meeting the latency target
    within_latency = [p for p in pool if p.get("typical_latency_ms", 0) <= c.latency_target_ms]
    latency_pool = within_latency or pool
    if not within_latency:
        reasons.append(f"无 Provider 满足 {c.latency_target_ms}ms 延迟目标，放宽此约束")

    # 5. quality policy → final pick
    if c.quality == "high":
        # prefer a real provider if present, else cheapest
        reals = [p for p in latency_pool if p.get("kind") == "real"]
        chosen = min(reals or latency_pool, key=est)
        policy = "quality=high → 优先真实 Provider"
    elif c.quality == "cheap":
        chosen = min(latency_pool, key=est)
        policy = "quality=cheap → 直接取最低成本"
    else:  # balanced
        chosen = min(latency_pool, key=est)
        policy = "quality=balanced → 在满足延迟/预算的候选中取最低成本"

    reason = (
        f"{policy}；选中 {chosen['name']}（预估 ${est(chosen):.6f}，"
        f"典型延迟 {chosen.get('typical_latency_ms','?')}ms）"
    )
    if reasons:
        reason += "。" + "；".join(reasons)
    return chosen, reason, [{"name": p["name"], "est_cost": est(p)} for p in providers]
