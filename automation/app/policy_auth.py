from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException


class PolicyAdminAuthenticator:
    def __init__(self, expected_key: str):
        if not expected_key:
            raise RuntimeError("policy administrator key must not be empty")
        self._expected_key = expected_key

    @classmethod
    def from_file(cls, key_file: Path) -> "PolicyAdminAuthenticator":
        return cls(key_file.read_text(encoding="utf-8").strip())

    @classmethod
    def from_environment_for_local_development(cls) -> "PolicyAdminAuthenticator":
        if os.getenv("POLICY_ALLOW_ENV_ADMIN_KEY") != "true":
            raise RuntimeError("environment policy key is disabled")
        return cls(os.environ["POLICY_ADMIN_KEY"])

    def require(self, authorization: str | None) -> None:
        scheme, separator, credentials = authorization.partition(" ") if authorization else ("", "", "")
        supplied = credentials.strip() if scheme == "Bearer" and separator else ""
        if not secrets.compare_digest(supplied, self._expected_key):
            raise HTTPException(status_code=401, detail="invalid policy administrator credentials")
