"""Cost estimation from token usage + provider prices. Owned by A."""


def estimate_cost(provider: dict, input_tokens: int, output_tokens: int) -> float:
    pin = float(provider.get("price_per_1k_input", 0.0))
    pout = float(provider.get("price_per_1k_output", 0.0))
    return round(pin * input_tokens / 1000.0 + pout * output_tokens / 1000.0, 8)


def rough_input_tokens(messages: list[dict]) -> int:
    """Crude pre-call estimate (~4 chars/token) used for budget filtering before we know real usage."""
    chars = sum(len(m.get("content", "")) for m in messages)
    return max(1, chars // 4)
