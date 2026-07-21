"""Pre-browser validation and input resolution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from proton_cli.errors import CliError
from proton_cli.types import SendCommand


@dataclass
class ResolvedSendInputs:
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    attachments: list[str]


class LoginPasswordInput:
    def __init__(self, password: str | None = None, password_env: str | None = None) -> None:
        self.password = password
        self.password_env = password_env


class LoginPassword:
    def __init__(self, value: str, source: str) -> None:
        self.value = value
        self.source = source


def resolve_login_password(input: LoginPasswordInput, env: Mapping[str, str | None]) -> LoginPassword:
    if input.password is not None and input.password_env is not None:
        raise CliError(
            "Use either --password or --password-env, not both.",
            code="CONFLICTING_PASSWORD_SOURCES",
            exit_code=2,
        )
    if input.password is not None:
        return LoginPassword(input.password, "argument")
    if input.password_env is not None:
        value = env.get(input.password_env)
        if value is None:
            raise CliError(
                f"Password environment variable {input.password_env} is not set.",
                code="UNSET_PASSWORD_ENV",
                exit_code=2,
            )
        return LoginPassword(value, "env")
    raise CliError(
        "login requires --password-env <name> or --password <value>.",
        code="MISSING_PASSWORD_SOURCE",
        exit_code=2,
    )


def split_recipients(values: list[str], flag_name: str) -> list[str]:
    recipients: list[str] = []
    invalid: list[str] = []

    for value in values:
        for token in value.split(","):
            trimmed = token.strip()
            if not trimmed or token != trimmed or any(c.isspace() for c in trimmed) or trimmed.count("@") != 1:
                invalid.append(token)
            else:
                recipients.append(trimmed)

    if invalid:
        display = [value if value else "<blank>" for value in invalid]
        raise CliError(
            f"Invalid --{flag_name} recipient: {', '.join(display)}",
            code="INVALID_RECIPIENT",
            exit_code=2,
        )
    return recipients


async def resolve_body_input(body: str | None, body_file: str | None, cwd: Path) -> str:
    if body is not None and body_file is not None:
        raise CliError(
            "Use either --body or --body-file, not both.",
            code="CONFLICTING_BODY_SOURCES",
            exit_code=2,
        )
    if body is not None:
        return body
    if body_file is not None:
        resolved = resolve_input_path(cwd, body_file)
        try:
            return resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(
                f"Cannot read --body-file path: {body_file}",
                code="BODY_FILE_UNREADABLE",
                exit_code=2,
            ) from exc
    raise CliError(
        "send requires exactly one of --body <text> or --body-file <path>.",
        code="MISSING_BODY_SOURCE",
        exit_code=2,
    )


async def resolve_send_inputs(command: SendCommand, cwd: Path) -> ResolvedSendInputs:
    to = split_recipients(command.to, "to")
    cc = split_recipients(command.cc, "cc")
    bcc = split_recipients(command.bcc, "bcc")
    if not to:
        raise CliError(
            "send requires at least one --to recipient.",
            code="MISSING_TO",
            exit_code=2,
        )
    if command.subject is None:
        raise CliError(
            "send requires --subject, though the value may be empty.",
            code="MISSING_SUBJECT",
            exit_code=2,
        )
    body = await resolve_body_input(command.body, command.body_file, cwd)
    attachments = [await _resolve_existing_path(cwd, attachment, "--attach") for attachment in command.attachments]
    return ResolvedSendInputs(
        to=to,
        cc=cc,
        bcc=bcc,
        subject=command.subject,
        body=body,
        attachments=attachments,
    )


def resolve_input_path(cwd: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (cwd / value).resolve()


async def _resolve_existing_path(cwd: Path, value: str, flag: str) -> str:
    resolved = resolve_input_path(cwd, value)
    try:
        # Trigger an access check without blocking the event loop excessively.
        if not resolved.exists() or not os.access(resolved, os.R_OK):
            raise PermissionError(f"Not readable: {resolved}")
        return str(resolved)
    except OSError as exc:
        raise CliError(
            f"Cannot read {flag} path: {value}",
            code="INPUT_PATH_UNREADABLE",
            exit_code=2,
        ) from exc
