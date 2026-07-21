"""App-data path resolution for proton-cli."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def ensure_private_dir(path: Path) -> Path:
    """Create a directory and restrict it to the current OS user where supported."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def default_app_root(env: Mapping[str, str | None] | None = None) -> Path:
    env = env or dict(os.environ)
    app_root = env.get("PROTON_CLI_APP_ROOT")
    if app_root:
        return Path(app_root)
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "share"
    return Path(base) / "proton_cli"


def profile_dir(app_root: Path, session_name: str) -> Path:
    return app_root / "profiles" / session_name


def output_dir(app_root: Path, session_name: str) -> Path:
    return app_root / "output" / session_name


def workspace_dir(app_root: Path) -> Path:
    return app_root / "zendriver-workspace"


def config_dir(app_root: Path) -> Path:
    return app_root / "configs"
