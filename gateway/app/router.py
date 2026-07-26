"""
Rule-based, cost/health/budget/privacy-aware routing. THE HEART OF MEMBER A's WORK.
P0 keeps it deliberately simple and explainable; every decision produces a human-readable reason.
Extend the policy here (do NOT scatter routing logic elsewhere).

Task 5: select_provider now accepts an optional GatewayRoutingPolicy. When
omitted, safe v1 defaults (DEFAULT_GATEWAY_POLICY) reproduce the exact P0
behavior — existing callers and existing tests are unaffected.
"""
from app.cost import estimate_cost, rough_input_tokens
from app.policy import DEFAULT_GATEWAY_POLICY, GatewayRoutingPolicy


def _healthy(provider: dict, health: dict) -> bool:
    # P0: health map is optional; unknown == healthy. B's probe (P1) fills this in.
    return health.get(provider["name"], "healthy") != "down"


def missing_capabilities(provider: dict, required_capabilities: set[str]) -> set[str]:
    capabilities = provider.get("capabilities", {})
    return {
        capability
        for capability in required_capabilities
        if capabilities.get(capability) is not True
    }


def _supports(provider: dict, required_capabilities: set[str]) -> bool:
    return not missing_capabilities(provider, required_capabilities)


def _quality_rank(provider: dict) -> int:
    """Return static model quality metadata without changing Policy v1."""
    rank = provider.get("quality_rank", 0)
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
        raise RuntimeError(f"provider {provider['name']} has invalid quality_rank")
    return rank


def select_provider(
    providers: list[dict],
    messages: list[dict],
    c,
    health: dict | None = None,
    required_capabilities: set[str] | None = None,
    policy: GatewayRoutingPolicy | None = None,
):
    """
    Returns (chosen_provider_dict, reason_str, candidates_debug).
    Raises RuntimeError with an explanatory message if nothing qualifies.

    `policy` parameterizes cost estimation (assumed_output_tokens), the
    balanced-mode real-provider price tolerance, whether unmet budget/latency
    constraints raise (hard) or relax (soft), and whether quality=high
    prefers a real provider or the lowest-cost one. When policy is None,
    DEFAULT_GATEWAY_POLICY reproduces the exact P0 behavior.
    """
    policy = policy or DEFAULT_GATEWAY_POLICY
    health = health or {}
    required_capabilities = required_capabilities or set()
    in_tok = rough_input_tokens(messages)
    reasons = []

    # 1. privacy filter: high privacy forbids external providers (guardrail,
    #    never affected by policy)
    pool = providers
    if c.privacy == "high":
        pool = [p for p in pool if p.get("privacy") == "internal"]
        reasons.append("privacy=high → 仅保留 internal Provider")

    # 2. Agent capabilities are hard gates (guardrail, never affected by policy).
    if required_capabilities:
        pool = [p for p in pool if _supports(p, required_capabilities)]
        reasons.append(
            "能力要求=" + ",".join(sorted(required_capabilities))
        )

    # 3. health filter
    pool = [p for p in pool if _healthy(p, health)]

    # 4. budget filter (pre-call estimate, output size from policy)
    def est(p):
        return estimate_cost(p, in_tok, policy.assumed_output_tokens)

    affordable = [p for p in pool if est(p) <= c.max_cost_usd]
    if affordable:
        pool = affordable
    elif policy.budget_mode == "hard":
        raise RuntimeError(f"no provider satisfies budget ${c.max_cost_usd}")
    else:
        reasons.append(f"没有 Provider 在预算 ${c.max_cost_usd} 内，放选整体最便宜者")

    if not pool:
        requirements = ",".join(sorted(required_capabilities)) or "none"
        raise RuntimeError(
            "no eligible provider after privacy/capability/health filtering "
            f"(required_capabilities={requirements})"
        )

    # 5. latency awareness
    within_latency = [p for p in pool if p.get("typical_latency_ms", 0) <= c.latency_target_ms]
    if within_latency:
        latency_pool = within_latency
    elif policy.latency_mode == "hard":
        raise RuntimeError(f"no provider satisfies latency {c.latency_target_ms}ms")
    else:
        latency_pool = pool
        reasons.append(f"无 Provider 满足 {c.latency_target_ms}ms 延迟目标，放宽该约束")

    # 6. quality policy → final pick
    reals = [p for p in latency_pool if p.get("kind") == "real"]

    if c.quality == "high":
        if policy.high_quality_strategy == "lowest_cost":
            chosen = min(latency_pool, key=est)
            policy_str = "quality=high → high_quality_strategy=lowest_cost，直接选最低成本"
        elif reals:
            highest_rank = max(_quality_rank(provider) for provider in reals)
            highest_quality_reals = [
                provider for provider in reals
                if _quality_rank(provider) == highest_rank
            ]
            chosen = min(highest_quality_reals, key=est)
            policy_str = (
                "quality=high → 优先最高质量真实 Provider"
                f"（quality_rank={highest_rank}）"
            )
        else:
            chosen = min(latency_pool, key=est)
            policy_str = "quality=high → 无可用真实 Provider（可能已被隐私约束排除），退化为最低成本"

    elif c.quality == "cheap":
        chosen = min(latency_pool, key=est)
        policy_str = "quality=cheap → 直接取最低成本"

    else:  # balanced
        cheapest = min(latency_pool, key=est)
        if cheapest.get("kind") == "real" or not reals:
            chosen = cheapest
            policy_str = "quality=balanced → 最便宜本身即为真实 Provider（或无真实 Provider 可选）"
        else:
            cheapest_real = min(reals, key=est)
            if est(cheapest_real) <= est(cheapest) * (1 + policy.balanced_price_tolerance):
                chosen = cheapest_real
                policy_str = (
                    f"quality=balanced → 最便宜的是 mock，但真实 Provider 价差在"
                    f"{int(policy.balanced_price_tolerance * 100)}% 以内，为提升质量选择真实 Provider"
                )
            else:
                chosen = cheapest
                policy_str = "quality=balanced → 真实 Provider 价差过大，选择最低成本"

    reason = (
        f"{policy_str}；选中 {chosen['name']}（预估 ${est(chosen):.6f}，"
        f"典型延迟 {chosen.get('typical_latency_ms','?')}ms）"
    )
    if reasons:
        reason += "。" + "；".join(reasons)
    return chosen, reason, [{"name": p["name"], "est_cost": est(p)} for p in providers]
