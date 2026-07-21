"""Smoke tests for the Python package scaffold."""

from __future__ import annotations

import sys

import pytest

from proton_cli.cli import main


def test_main_help_prints_usage(monkeypatch: pytest.MonkeyPatch, capsys: object) -> None:
    monkeypatch.setattr(sys, "argv", ["proton-cli", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Usage: proton-cli" in captured.out


def test_main_bare_invocation_prints_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    monkeypatch.setattr(sys, "argv", ["proton-cli"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Usage: proton-cli" in captured.out
