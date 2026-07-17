"""Loads contracts/providers.yaml with ${ENV:-default} expansion. Owned by A."""
import os
import re
import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def _expand(value):
    if not isinstance(value, str):
        return value
    def repl(m):
        name, default = m.group(1), m.group(2) or ""
        return os.environ.get(name, default)
    return _ENV_PATTERN.sub(repl, value)


def _expand_deep(obj):
    if isinstance(obj, dict):
        return {k: _expand_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_deep(v) for v in obj]
    return _expand(obj)


def load_providers(path: str | None = None) -> list[dict]:
    path = path or os.environ.get("PROVIDERS_FILE", "providers.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    providers = _expand_deep(data.get("providers", []))
    return providers
