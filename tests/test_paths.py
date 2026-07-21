"""Tests for app-data path resolution."""

from __future__ import annotations

from pathlib import Path

from proton_cli.paths import default_app_root


def test_default_app_root_honors_env() -> None:
    root = Path("C:/tmp/proton-cli-e2e")
    assert default_app_root({"PROTON_CLI_APP_ROOT": str(root)}) == root
