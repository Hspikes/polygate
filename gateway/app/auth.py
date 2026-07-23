"""Minimal inbound Bearer authentication for PolyGate clients.

Authentication is opt-in for backwards-compatible local development: when
``POLYGATE_API_KEYS`` is empty the gateway accepts requests. Deployments that
set one or more comma-separated keys require a matching Bearer token.
"""
import hmac
import os

from fastapi import HTTPException, Request


def _configured_keys() -> tuple[str, ...]:
    return tuple(
        key.strip()
        for key in os.environ.get("POLYGATE_API_KEYS", "").split(",")
        if key.strip()
    )


def require_client_api_key(request: Request) -> None:
    keys = _configured_keys()
    if not keys:
        return

    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    valid = (
        bool(separator)
        and scheme.lower() == "bearer"
        and bool(supplied)
        and any(hmac.compare_digest(supplied, expected) for expected in keys)
    )
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="invalid or missing PolyGate API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
