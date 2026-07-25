#!/usr/bin/env python3
"""Render the frozen default Policy Store document from contract examples."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "usage: render-default-policy-store.py POLICY_EXAMPLES_JSON",
            file=sys.stderr,
        )
        return 2

    examples_path = Path(sys.argv[1])
    with examples_path.open(encoding="utf-8") as source:
        examples = json.load(source)

    store = examples.get("store")
    if not isinstance(store, dict):
        raise SystemExit(
            f"{examples_path} must contain an object under the 'store' key"
        )

    if set(store) != {"active_version", "versions"}:
        raise SystemExit(
            "the default store must contain only active_version and versions"
        )

    json.dump(store, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
