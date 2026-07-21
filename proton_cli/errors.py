"""Error types and redaction helpers."""

from __future__ import annotations

import re
from typing import Any


class CliError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "CLI_ERROR",
        exit_code: int = 1,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details or {}


def redact_text(value: str, secrets: list[str] | None = None) -> str:
    secrets = secrets or []
    redacted = value
    for secret in secrets:
        if secret:
            redacted = "[REDACTED]".join(redacted.split(secret))
    redacted = re.sub(
        r"(--password(?:=|\s+))(?:\"[^\"]*\"|'[^']*'|\S+)",
        r"\1[REDACTED]",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def redact_object(value: dict[str, Any], secrets: list[str] | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, item in value.items():
        if re.search(r"password", key, re.IGNORECASE):
            output[key] = "[REDACTED]"
        elif isinstance(item, str):
            output[key] = redact_text(item, secrets)
        elif isinstance(item, dict):
            output[key] = redact_object(item, secrets)
        else:
            output[key] = item
    return output


def to_cli_error(error: BaseException) -> CliError:
    if isinstance(error, CliError):
        return error
    return CliError(redact_text(str(error)), code="UNEXPECTED_ERROR")
