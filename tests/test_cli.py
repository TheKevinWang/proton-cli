"""Tests for command-line parsing."""

from __future__ import annotations

import pytest

from proton_cli.errors import CliError
from proton_cli.parser import parse_argv
from proton_cli.types import InboxCommand, RefreshCommand, SendCommand


def test_global_flags_before_and_after_command() -> None:
    before = parse_argv(["--json", "--trace", "temp/inbox.zip", "inbox", "--limit", "5"])
    after = parse_argv(["inbox", "--limit", "5", "--trace", "temp/inbox.zip", "--json"])

    assert before.global_options == after.global_options
    assert before.command == after.command
    assert before.global_options.json is True
    assert before.global_options.trace == "temp/inbox.zip"
    assert before.command == InboxCommand(limit=5, folder="inbox")


def test_only_default_session_supported() -> None:
    parsed = parse_argv(["--session", "default", "browser", "status"])
    assert parsed.global_options.session == "default"
    with pytest.raises(CliError):
        parse_argv(["--session", "work", "inbox"])


def test_chrome_data_dir_global_flag() -> None:
    parsed = parse_argv(
        [
            "login",
            "--email",
            "user@example.com",
            "--password",
            "secret",
            "--chrome-data-dir",
            "temp/chrome",
        ]
    )
    assert parsed.global_options.chrome_data_dir == "temp/chrome"
    assert parsed.command.kind == "login"
    assert parsed.command.email == "user@example.com"  # type: ignore[attr-defined]


def test_rejects_empty_chrome_data_dir() -> None:
    with pytest.raises(CliError):
        parse_argv(
            ["--chrome-data-dir=", "login", "--email", "user@example.com", "--password", "secret"]
        )


def test_inbox_refresh_alias() -> None:
    parsed = parse_argv(["inbox", "refresh"])
    assert parsed.command == RefreshCommand(limit=20)


def test_sent_folder() -> None:
    parsed = parse_argv(["inbox", "--folder", "sent", "--limit", "3"])
    assert parsed.command == InboxCommand(limit=3, folder="sent")


def test_rejects_browser_mode_flags_on_non_login() -> None:
    with pytest.raises(CliError):
        parse_argv(["inbox", "--headless"])
    with pytest.raises(CliError):
        parse_argv(
            ["send", "--to", "a@example.com", "--subject", "x", "--body", "y", "--headed"]
        )


def test_rejects_keep_open_browser_close() -> None:
    with pytest.raises(CliError):
        parse_argv(["--keep-open", "browser", "close"])


def test_accepts_empty_subject_and_body() -> None:
    parsed = parse_argv(["send", "--to", "a@example.com", "--subject", "", "--body", ""])
    assert parsed.command == SendCommand(
        to=["a@example.com"],
        subject="",
        body="",
    )


def test_proxy_global_flag() -> None:
    parsed = parse_argv(
        [
            "--proxy",
            "socks5://10.0.0.1:1080",
            "login",
            "--email",
            "user@example.com",
            "--password",
            "secret",
        ]
    )
    assert parsed.global_options.proxy == "socks5://10.0.0.1:1080"


def test_proxy_global_flag_with_equals() -> None:
    parsed = parse_argv(["login", "--email", "user@example.com", "--password", "secret", "--proxy=socks5://10.0.0.1:1080"])
    assert parsed.global_options.proxy == "socks5://10.0.0.1:1080"


def test_rejects_empty_proxy() -> None:
    with pytest.raises(CliError) as exc_info:
        parse_argv(["--proxy=", "login", "--email", "user@example.com", "--password", "secret"])
    assert exc_info.value.code == "MISSING_PROXY"


def test_rejects_non_socks5_proxy() -> None:
    with pytest.raises(CliError) as exc_info:
        parse_argv(["--proxy", "http://127.0.0.1:8080", "login", "--email", "user@example.com", "--password", "secret"])
    assert exc_info.value.code == "INVALID_PROXY"
