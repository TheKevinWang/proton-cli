"""Atomic session JSON read/write."""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from proton_cli.types import BrowserMode, InboxRow

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass
class SessionState:
    name: str
    managed: bool
    mode: BrowserMode
    profile_dir: str
    profile_owned: bool = False
    browser_process_id: int | None = None
    debug_endpoint: str | None = None
    attached_endpoint: str | None = None
    account_email: str | None = None
    last_successful_command_at: str | None = None
    last_inbox: list[InboxRow] | None = None


class SessionStore:
    def __init__(self, app_root: Path) -> None:
        self.app_root = app_root

    def path_for(self, name: str) -> Path:
        if not _SESSION_NAME_RE.fullmatch(name):
            raise ValueError(
                "Invalid session name; use 1-64 letters, digits, dots, underscores, or hyphens."
            )
        return self.app_root / "sessions" / f"{name}.json"

    async def load(self, name: str) -> SessionState | None:
        path = self.path_for(name)
        try:
            loop = __import__("asyncio").get_event_loop()
            raw = await loop.run_in_executor(None, path.read_text, "utf-8")
        except FileNotFoundError:
            return None
        parsed = json.loads(raw)
        if parsed.get("name") != name:
            return None
        return _deserialize_session_state(parsed)

    async def save(self, state: SessionState) -> None:
        final_path = self.path_for(state.name)
        final_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        final_path.parent.chmod(0o700)
        temp_path = final_path.with_suffix(f".json.{os.getpid()}.{int(time.time() * 1000)}.tmp")
        data = serialize_session_state(state)
        loop = __import__("asyncio").get_event_loop()
        await loop.run_in_executor(None, _write_atomic, temp_path, final_path, data)

    async def clear(self, name: str) -> None:
        path = self.path_for(name)
        loop = __import__("asyncio").get_event_loop()
        await loop.run_in_executor(None, _silent_remove, path)


def _write_atomic(temp_path: Path, final_path: Path, data: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temp_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
        temp_path.chmod(0o600)
        os.replace(temp_path, final_path)
        final_path.chmod(0o600)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()
        raise


def _silent_remove(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


# Dataclass field name -> on-disk/JSON key. The shipped TypeScript CLI persists
# the session record with camelCase keys (profileDir, debugEndpoint, ...), so the
# Python port emits the same shape for parity. InboxRow keys stay snake_case to
# match the TypeScript InboxRow interface.
_FIELD_TO_JSON: dict[str, str] = {
    "name": "name",
    "managed": "managed",
    "mode": "mode",
    "profile_dir": "profileDir",
    "profile_owned": "profileOwned",
    "browser_process_id": "browserProcessId",
    "debug_endpoint": "debugEndpoint",
    "attached_endpoint": "attachedEndpoint",
    "account_email": "accountEmail",
    "last_successful_command_at": "lastSuccessfulCommandAt",
    "last_inbox": "lastInbox",
}

_KEY_MAP: dict[str, str] = {json_key: field for field, json_key in _FIELD_TO_JSON.items()}


def serialize_session_state(state: SessionState) -> dict[str, Any]:
    """Serialize to the camelCase JSON/disk shape, omitting None and secrets."""
    data: dict[str, Any] = {}
    for field_name, json_key in _FIELD_TO_JSON.items():
        if re.search(r"password", json_key, re.IGNORECASE):
            continue
        value = getattr(state, field_name)
        if value is None:
            continue
        if field_name == "last_inbox":
            value = [asdict(row) for row in value]
        data[json_key] = value
    return data


def _deserialize_session_state(data: dict[str, Any]) -> SessionState:
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        normalized[_KEY_MAP.get(key, key)] = value
    inbox_rows = normalized.get("last_inbox")
    if inbox_rows:
        normalized["last_inbox"] = [InboxRow(**row) for row in inbox_rows]
    return SessionState(**normalized)
