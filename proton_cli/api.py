"""Importable, typed Python API for proton-cli.

``ProtonClient`` exposes the same mailbox workflows as the command-line tool but
returns typed dataclasses (``InboxRow``, ``ReadMessage``, ``SessionState``) and
raises ``CliError`` on failure, so callers do not need to shell out and parse
CLI output.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from proton_cli.app import (
    Runtime,
    _browser_status_core,
    _close_core,
    _inbox_core,
    _login_core,
    _read_core,
    _refresh_core,
    _send_core,
    _tcp_port_open,
)
from proton_cli.browser import Browser as BrowserFacade
from proton_cli.paths import default_app_root
from proton_cli.session import SessionState, SessionStore
from proton_cli.types import (
    BrowserMode,
    CloseOutcome,
    GlobalOptions,
    InboxCommand,
    InboxRow,
    LoginCommand,
    LoginOutcome,
    MailboxFolder,
    ReadMessage,
    SendCommand,
)


class ProtonClient:
    """Async, in-process client for Proton Mail browser automation.

    A client binds per-session configuration once and reuses the same
    ``Runtime`` (and therefore the same browser facade) across calls. Chrome
    itself is shared with the CLI and other callers; exiting an ``async with``
    block does **not** close the browser. Use ``await client.close()`` for
    deliberate teardown.
    """

    def __init__(
        self,
        *,
        session: str = "default",
        app_root: Path | None = None,
        env: Mapping[str, str | None] | None = None,
        cwd: Path | None = None,
        browser: BrowserFacade | None = None,
        proxy: str | None = None,
        verbose: bool = False,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        tcp_probe: Callable[[str, int], Awaitable[bool]] | None = None,
    ) -> None:
        self._session = session
        self._app_root = app_root
        self._env = env
        self._cwd = cwd
        self._browser_override = browser
        self._proxy = proxy
        self._verbose = verbose
        self._sleep = sleep
        self._tcp_probe = tcp_probe
        self._runtime: Runtime | None = None

    async def _get_runtime(self) -> Runtime:
        if self._runtime is None:
            env = self._env if self._env is not None else dict(os.environ)
            app_root = self._app_root if self._app_root is not None else default_app_root(env)
            cwd = self._cwd if self._cwd is not None else Path.cwd()
            browser = self._browser_override if self._browser_override is not None else BrowserFacade()
            sleep = self._sleep if self._sleep is not None else asyncio.sleep
            tcp_probe = self._tcp_probe if self._tcp_probe is not None else _tcp_port_open
            self._runtime = Runtime(
                cwd=cwd,
                env=env,
                app_root=app_root,
                store=SessionStore(app_root),
                browser=browser,
                verbose=self._verbose,
                sleep=sleep,
                tcp_probe=tcp_probe,
            )
        return self._runtime

    async def __aenter__(self) -> ProtonClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Do not close the shared browser when leaving the context."""
        return None

    def _global_options(self) -> GlobalOptions:
        options = GlobalOptions(session=self._session, json=False, verbose=self._verbose)
        if self._proxy is not None:
            options.proxy = self._proxy
        return options

    async def login(
        self,
        *,
        email: str,
        password: str | None = None,
        password_env: str | None = None,
        mode: BrowserMode = "headed",
    ) -> LoginOutcome:
        runtime = await self._get_runtime()
        command = LoginCommand(
            email=email, password=password, password_env=password_env, mode=mode
        )
        return await _login_core(self._global_options(), command, runtime)

    async def inbox(self, *, limit: int = 20, folder: MailboxFolder = "inbox") -> list[InboxRow]:
        runtime = await self._get_runtime()
        command = InboxCommand(limit=limit, folder=folder)
        return await _inbox_core(self._global_options(), command, runtime)

    async def refresh(self, *, limit: int = 20) -> list[InboxRow]:
        runtime = await self._get_runtime()
        return await _refresh_core(self._global_options(), limit, runtime)

    async def read(self, handle: str) -> ReadMessage:
        runtime = await self._get_runtime()
        return await _read_core(self._global_options(), handle, runtime)

    async def send(
        self,
        *,
        to: Sequence[str],
        subject: str,
        body: str | None = None,
        body_file: str | None = None,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        attachments: Sequence[str] = (),
    ) -> None:
        runtime = await self._get_runtime()
        command = SendCommand(
            to=list(to),
            cc=list(cc),
            bcc=list(bcc),
            subject=subject,
            body=body,
            body_file=body_file,
            attachments=list(attachments),
        )
        await _send_core(self._global_options(), command, runtime)

    async def status(self) -> SessionState | None:
        runtime = await self._get_runtime()
        return await _browser_status_core(self._global_options(), runtime)

    async def close(self, *, force: bool = False) -> CloseOutcome:
        runtime = await self._get_runtime()
        return await _close_core(self._global_options(), force, runtime)
