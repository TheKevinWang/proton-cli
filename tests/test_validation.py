"""Tests for pre-browser validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from proton_cli.errors import CliError
from proton_cli.types import SendCommand
from proton_cli.validation import (
    LoginPasswordInput,
    resolve_body_input,
    resolve_login_password,
    resolve_send_inputs,
    split_recipients,
)


def test_split_recipients() -> None:
    assert split_recipients(
        ["alice@example.com,bob@example.com", "carol@example.com"], "to"
    ) == ["alice@example.com", "bob@example.com", "carol@example.com"]


def test_reject_bad_recipients() -> None:
    with pytest.raises(CliError):
        split_recipients(["alice@example.com,"], "to")
    with pytest.raises(CliError):
        split_recipients(["alice@example.com, bob@example.com"], "to")
    with pytest.raises(CliError):
        split_recipients(["alice @example.com"], "to")
    with pytest.raises(CliError):
        split_recipients(["alice.example.com"], "to")
    with pytest.raises(CliError):
        split_recipients(["a@b@example.com"], "to")


@pytest.mark.asyncio
async def test_empty_body_sources(tmp_path: Path) -> None:
    body_file = tmp_path / "body.txt"
    body_file.write_text("", encoding="utf-8")

    assert await resolve_body_input("", None, tmp_path) == ""
    assert await resolve_body_input(None, str(body_file), tmp_path) == ""


@pytest.mark.asyncio
async def test_reject_bad_body_sources(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        await resolve_body_input(None, None, tmp_path)
    with pytest.raises(CliError):
        await resolve_body_input("x", "message.txt", tmp_path)
    with pytest.raises(CliError):
        await resolve_body_input(None, "missing.txt", tmp_path)


@pytest.mark.asyncio
async def test_reject_missing_attachment(tmp_path: Path) -> None:
    command = SendCommand(
        to=["a@example.com"],
        subject="hello",
        body="body",
        attachments=["missing.pdf"],
    )
    with pytest.raises(CliError):
        await resolve_send_inputs(command, tmp_path)


def test_login_password_sources() -> None:
    resolved = resolve_login_password(LoginPasswordInput(password="secret"), {})
    assert resolved.value == "secret"
    assert resolved.source == "argument"
    resolved_env = resolve_login_password(
        LoginPasswordInput(password_env="PROTON_PASSWORD"), {"PROTON_PASSWORD": "secret"}
    )
    assert resolved_env.value == "secret"
    assert resolved_env.source == "env"


def test_reject_bad_login_password_sources() -> None:
    with pytest.raises(CliError):
        resolve_login_password(LoginPasswordInput(), {})
    with pytest.raises(CliError):
        resolve_login_password(
            LoginPasswordInput(password="secret", password_env="PROTON_PASSWORD"),
            {"PROTON_PASSWORD": "secret"},
        )
    with pytest.raises(CliError):
        resolve_login_password(LoginPasswordInput(password_env="PROTON_PASSWORD"), {})
