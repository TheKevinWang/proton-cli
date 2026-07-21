"""Shared fixtures for proton-cli workflow tests.

The workflow layer (``proton_cli.app``) is exercised with a fake in-process
browser facade so the login/inbox/read/send decision logic can be tested
without launching Chrome, per Milestones 5-7 of the migration plan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from proton_cli.app import RunCliOptions, RunCliResult, Runtime, run_cli
from proton_cli.session import SessionStore


async def _tcp_always_open(host: str, port: int) -> bool:
    return True


class FakeBrowser:
    """Records facade calls and replays scripted snapshots.

    ``snapshot()`` returns the next entry from ``snapshots``; once the list is
    exhausted the final entry is returned for every subsequent call, which lets
    a workflow's polling loops settle on a terminal state.
    """

    def __init__(self, snapshots: list[str] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._snapshots = list(snapshots) if snapshots else [""]
        self._index = 0

    def _next_snapshot(self) -> str:
        if self._index < len(self._snapshots):
            snap = self._snapshots[self._index]
            self._index += 1
            return snap
        return self._snapshots[-1]

    async def open_managed(self, **kwargs: Any) -> str:
        self.calls.append(("open_managed", dict(kwargs)))
        return "started"

    async def attach(self, **kwargs: Any) -> str:
        self.calls.append(("attach", kwargs.get("port")))
        return "attached"

    async def attach_external(self, **kwargs: Any) -> str:
        self.calls.append(("attach_external", kwargs.get("endpoint")))
        return "attached"

    async def close(self, session: str) -> str:
        self.calls.append(("close", session))
        return "closed"

    async def status(self, session: str) -> dict[str, Any]:
        return {
            "session": session,
            "url": "about:blank",
            "host": "127.0.0.1",
            "port": 9333,
            "process_id": 4242,
        }

    async def goto(self, session: str, url: str) -> str:
        self.calls.append(("goto", url))
        return url

    async def snapshot(self, session: str) -> str:
        return self._next_snapshot()

    async def click(self, session: str, target: str) -> str:
        self.calls.append(("click", target))
        return "clicked"

    async def fill(self, session: str, target: str, text: str, submit: bool = False) -> str:
        self.calls.append(("fill", (target, text)))
        return "filled"

    async def read_input_value(self, session: str, target: str) -> str:
        self.calls.append(("read_input_value", target))
        for name, payload in reversed(self.calls):
            if name == "fill" and payload[0] == target:
                return payload[1]
        return ""

    async def type_text(self, session: str, text: str) -> str:
        self.calls.append(("type_text", text))
        return "typed"

    async def press(self, session: str, key: str) -> str:
        self.calls.append(("press", key))
        return "pressed"

    async def key_down(self, session: str, key: str) -> str:
        self.calls.append(("key_down", key))
        return "down"

    async def key_up(self, session: str, key: str) -> str:
        self.calls.append(("key_up", key))
        return "up"

    async def upload(self, session: str, paths: list[str]) -> str:
        self.calls.append(("upload", tuple(paths)))
        return "uploaded"

    def with_trace(self, session: str, requested_path: str) -> Any:
        @asynccontextmanager
        async def _cm() -> AsyncIterator[None]:
            yield

        return _cm()

    # Convenience accessors for assertions.
    def calls_of(self, name: str) -> list[Any]:
        return [payload for call_name, payload in self.calls if call_name == name]


@pytest.fixture
def make_browser() -> Callable[..., FakeBrowser]:
    def _make(snapshots: list[str] | None = None) -> FakeBrowser:
        return FakeBrowser(snapshots)

    return _make


@pytest.fixture
def no_sleep() -> Callable[[float], Awaitable[None]]:
    async def _sleep(_seconds: float = 0) -> None:
        return None

    return _sleep


@pytest.fixture
def make_runtime(
    tmp_path: Path, no_sleep: Callable[[float], Awaitable[None]]
) -> Callable[..., Runtime]:
    def _make(browser: FakeBrowser, env: dict[str, str | None] | None = None) -> Runtime:
        return Runtime(
            cwd=tmp_path,
            env=env or {},
            app_root=tmp_path,
            store=SessionStore(tmp_path),
            browser=browser,  # type: ignore[arg-type]
            sleep=no_sleep,
            tcp_probe=_tcp_always_open,
        )

    return _make


@pytest.fixture
def run_command(
    tmp_path: Path, no_sleep: Callable[[float], Awaitable[None]]
) -> Callable[..., Awaitable[RunCliResult]]:
    async def _run(
        argv: list[str],
        browser: FakeBrowser,
        env: dict[str, str | None] | None = None,
    ) -> RunCliResult:
        return await run_cli(
            RunCliOptions(
                argv=argv,
                cwd=tmp_path,
                env=env or {},
                app_root=tmp_path,
                browser=browser,  # type: ignore[arg-type]
                sleep=no_sleep,
                tcp_probe=_tcp_always_open,
            )
        )

    return _run
