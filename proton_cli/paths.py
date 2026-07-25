"""App-data path resolution for proton-cli."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path


def ensure_private_dir(path: Path) -> Path:
    """Create a directory restricted to the current user where supported."""
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


def default_chrome_data_root(env: Mapping[str, str | None] | None = None) -> Path:
    env = env or dict(os.environ)
    chrome_root = env.get("PROTON_CLI_CHROME_DATA_ROOT")
    if chrome_root:
        return Path(chrome_root)
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "Temp"
    return Path(env.get("TEMP") or env.get("TMP") or tempfile.gettempdir())


def output_dir(app_root: Path, session_name: str) -> Path:
    return app_root / "output" / session_name


def workspace_dir(app_root: Path) -> Path:
    return app_root / "zendriver-workspace"


def config_dir(app_root: Path) -> Path:
    return app_root / "configs"
