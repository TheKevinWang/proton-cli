"""Browser facade implemented with the released Zendriver package.

The public backend combines standard CDP accessibility trees for the page's
same-target frame tree. The private distribution additionally handles
out-of-process targets and trace capture.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import sys
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zendriver as zd

from proton_cli.errors import CliError
from proton_cli.paths import ensure_private_dir
from proton_cli.release_policy import RELEASE_POLICY
from proton_cli.snapshot import find_first_ref

_NAV_TIMEOUT_SECONDS = 60.0
_CLICKABLE_ROLES = {"button", "link", "listitem", "menuitem", "region", "tab"}


@dataclass(frozen=True)
class _RefTarget:
    backend_node_id: Any


@dataclass
class _SessionHandle:
    browser: zd.Browser
    tab: zd.Tab
    host: str
    port: int
    process_id: int | None = None
    output_dir: Path = field(default_factory=Path)
    refs: dict[str, _RefTarget] = field(default_factory=dict)


class Browser:
    """Workflow-facing browser facade backed by public Zendriver v0.15.x."""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionHandle] = {}

    async def open_managed(
        self,
        *,
        session: str,
        mode: str,
        profile_dir: str,
        output_dir: Path,
        debug_port: int | None = None,
        url: str | None = None,
        proxy: str | None = RELEASE_POLICY.default_proxy,
    ) -> str:
        if session in self._sessions:
            raise CliError(
                f"Browser session {session} is already open.",
                code="BROWSER_ALREADY_OPEN",
                exit_code=2,
            )
        _validate_proxy(proxy)
        profile_path = Path(profile_dir)
        ensure_private_dir(profile_path)
        host = "127.0.0.1"
        port = debug_port or _free_port()
        config = zd.Config(
            user_data_dir=str(profile_path),
            headless=(mode == "headless"),
            host=host,
            port=port,
            browser_args=_proxy_browser_args(proxy),
        )
        process_id = _launch_detached(
            [str(config.browser_executable_path), *config(), "about:blank"]
        )
        try:
            browser, tab = await _connect_browser(host, port)
            if url:
                await tab.get(url)
        except BaseException:
            _kill_process(process_id)
            raise
        self._sessions[session] = _SessionHandle(
            browser=browser,
            tab=tab,
            host=host,
            port=port,
            process_id=process_id,
            output_dir=output_dir,
        )
        return f"Browser session {session} started on {host}:{port}"

    async def attach(
        self,
        *,
        session: str,
        host: str,
        port: int,
        output_dir: Path,
        process_id: int | None = None,
    ) -> str:
        if session in self._sessions:
            return f"Browser session {session} already attached"
        browser, tab = await _connect_browser(host, port)
        self._sessions[session] = _SessionHandle(
            browser=browser,
            tab=tab,
            host=host,
            port=port,
            process_id=process_id,
            output_dir=output_dir,
        )
        return f"Browser session {session} attached to {host}:{port}"

    async def attach_external(
        self, *, session: str, endpoint: str, output_dir: Path
    ) -> str:
        host, port = _parse_cdp_endpoint(endpoint)
        return await self.attach(session=session, host=host, port=port, output_dir=output_dir)

    async def close(self, session: str) -> str:
        handle = self._require_session(session)
        await handle.browser.stop()
        if handle.process_id is not None:
            _kill_process(handle.process_id)
        self._sessions.pop(session, None)
        return f"Browser session {session} closed"

    async def status(self, session: str) -> dict[str, Any]:
        handle = self._require_session(session)
        return {
            "session": session,
            "url": handle.tab.url,
            "host": handle.host,
            "port": handle.port,
            "process_id": handle.process_id,
        }

    async def goto(self, session: str, url: str) -> str:
        handle = self._require_session(session)
        try:
            await asyncio.wait_for(handle.tab.get(url), timeout=_NAV_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise CliError(
                f"Timeout {int(_NAV_TIMEOUT_SECONDS * 1000)}ms exceeded navigating to {url}",
                code="NAVIGATION_TIMEOUT",
            ) from exc
        return f"Navigated to {url}"

    async def snapshot(self, session: str) -> str:
        handle = self._require_session(session)
        await handle.tab.send(zd.cdp.accessibility.enable())
        frame_tree = await handle.tab.send(zd.cdp.page.get_frame_tree())
        snapshots: list[str] = []
        refs: dict[str, _RefTarget] = {}
        next_ref = 1
        for index, frame_id in enumerate(_frame_ids(frame_tree)):
            try:
                nodes = await handle.tab.send(
                    zd.cdp.accessibility.get_full_ax_tree(frame_id=frame_id)
                )
            except Exception:  # noqa: BLE001
                # Out-of-process frames can belong to a different CDP target.
                # Keep the usable main-frame snapshot and document the public
                # backend limitation rather than failing every mailbox action.
                if index == 0:
                    raise
                continue
            rendered, frame_refs = _render_ax_tree(nodes, ref_start=next_ref)
            if rendered:
                snapshots.append(rendered if index == 0 else _nest_frame_snapshot(rendered))
            refs.update(frame_refs)
            next_ref += len(frame_refs)
        handle.refs = refs
        return "\n".join(snapshots)

    async def click(self, session: str, target: str) -> str:
        handle, ref = await self._resolve_target(session, target)
        remote = await handle.tab.send(
            zd.cdp.dom.resolve_node(backend_node_id=ref.backend_node_id)
        )
        if remote.object_id is None:
            raise CliError(f"Could not resolve target {target}.", code="STALE_ELEMENT_REF")
        _, error = await handle.tab.send(
            zd.cdp.runtime.call_function_on(
                "function() { this.click(); }",
                object_id=remote.object_id,
                await_promise=True,
                user_gesture=True,
                return_by_value=True,
            )
        )
        if error is not None:
            raise CliError(f"Could not click target {target}.", code="ELEMENT_CLICK_FAILED")
        return f"Clicked {target}"

    async def fill(
        self, session: str, target: str, text: str, submit: bool = False
    ) -> str:
        handle, ref = await self._resolve_target(session, target)
        remote = await handle.tab.send(
            zd.cdp.dom.resolve_node(backend_node_id=ref.backend_node_id)
        )
        if remote.object_id is None:
            raise CliError(f"Could not resolve target {target}.", code="STALE_ELEMENT_REF")
        _, error = await handle.tab.send(
            zd.cdp.runtime.call_function_on(
                """function(value) {
                    this.focus();
                    if (this.isContentEditable) this.textContent = value;
                    else this.value = value;
                    this.dispatchEvent(new InputEvent('input', {bubbles: true, data: value}));
                    this.dispatchEvent(new Event('change', {bubbles: true}));
                }""",
                object_id=remote.object_id,
                arguments=[zd.cdp.runtime.CallArgument(value=text)],
                await_promise=True,
                user_gesture=True,
                return_by_value=True,
            )
        )
        if error is not None:
            raise CliError(f"Could not fill target {target}.", code="ELEMENT_FILL_FAILED")
        if submit:
            await self.press(session, "Enter")
        return f"Filled {target}"

    async def read_input_value(self, session: str, target: str) -> str:
        handle, ref = await self._resolve_target(session, target)
        remote = await handle.tab.send(
            zd.cdp.dom.resolve_node(backend_node_id=ref.backend_node_id)
        )
        if remote.object_id is None:
            raise CliError(f"Could not resolve target {target}.", code="STALE_ELEMENT_REF")
        result, error = await handle.tab.send(
            zd.cdp.runtime.call_function_on(
                "function() { return this.value ?? this.textContent ?? ''; }",
                object_id=remote.object_id,
                return_by_value=True,
            )
        )
        if error is not None:
            raise CliError(f"Could not read target {target}.", code="ELEMENT_READ_FAILED")
        return "" if result.value is None else str(result.value)

    async def type_text(self, session: str, text: str) -> str:
        handle = self._require_session(session)
        for payload in zd.KeyEvents.from_text(text, zd.KeyPressEvent.CHAR):
            await handle.tab.send(zd.cdp.input_.dispatch_key_event(**payload))
        return f"Typed {text!r}"

    async def press(self, session: str, key: str) -> str:
        handle = self._require_session(session)
        special = _special_key(key)
        events = zd.KeyEvents(special if special is not None else key).to_cdp_events(
            zd.KeyPressEvent.DOWN_AND_UP
        )
        for payload in events:
            await handle.tab.send(zd.cdp.input_.dispatch_key_event(**payload))
        return f"Pressed {key!r}"

    async def key_down(self, session: str, key: str) -> str:
        handle = self._require_session(session)
        await handle.tab.send(
            zd.cdp.input_.dispatch_key_event(type_="keyDown", key=key, code=key)
        )
        return f"Key down {key!r}"

    async def key_up(self, session: str, key: str) -> str:
        handle = self._require_session(session)
        await handle.tab.send(
            zd.cdp.input_.dispatch_key_event(type_="keyUp", key=key, code=key)
        )
        return f"Key up {key!r}"

    async def upload(self, session: str, paths: list[str]) -> str:
        handle = self._require_session(session)
        elements = await handle.tab.select_all("input[type=file]", timeout=1)
        if not elements:
            raise CliError("Could not find a file input.", code="FILE_INPUT_NOT_FOUND")
        await elements[0].send_file(*paths)
        return f"Uploaded {', '.join(paths)}"

    def with_trace(
        self, session: str, requested_path: str
    ) -> AbstractAsyncContextManager[None]:
        self._require_session(session)
        return _UnsupportedTrace(requested_path)

    def _require_session(self, session: str) -> _SessionHandle:
        if session not in self._sessions:
            raise CliError(
                f"Browser session {session} is not open.",
                code="NO_BROWSER_SESSION",
                exit_code=2,
            )
        return self._sessions[session]

    async def _resolve_target(self, session: str, target: str) -> tuple[_SessionHandle, _RefTarget]:
        handle = self._require_session(session)
        ref_name = target
        if not re.match(r"^e\d+$", ref_name, re.I):
            snapshot = await self.snapshot(session)
            resolved = find_first_ref(snapshot, [re.compile(re.escape(target), re.I)])
            if resolved is None:
                raise CliError(
                    f"Could not find ref for target: {target}",
                    code="VISIBLE_CONTROL_NOT_FOUND",
                    exit_code=2,
                )
            ref_name = resolved
        ref = handle.refs.get(ref_name)
        if ref is None:
            await self.snapshot(session)
            ref = handle.refs.get(ref_name)
        if ref is None:
            raise CliError(f"Target {target} is stale.", code="STALE_ELEMENT_REF")
        return handle, ref


@dataclass
class _UnsupportedTrace:
    requested_path: str

    async def __aenter__(self) -> None:
        raise CliError(
            "Trace capture requires the enhanced private browser backend and is unavailable in the public build.",
            code="TRACE_UNAVAILABLE",
            exit_code=2,
        )

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


def _validate_proxy(proxy: str | None) -> None:
    if not proxy:
        if RELEASE_POLICY.require_proxy:
            raise CliError("A SOCKS5 proxy is required.", code="PROXY_REQUIRED", exit_code=2)
        return
    if not proxy.startswith("socks5://"):
        raise CliError(
            f"Proxy must be a socks5:// URL, got: {proxy}",
            code="INVALID_PROXY",
            exit_code=2,
        )


def _proxy_browser_args(proxy: str | None) -> list[str]:
    if proxy is None:
        return []
    return [
        f"--proxy-server={proxy}",
        "--host-resolver-rules=MAP * ~NOTFOUND,EXCLUDE 127.0.0.1,EXCLUDE localhost",
        "--disable-quic",
        "--proxy-bypass-list=",
    ]


def _frame_ids(frame_tree: Any) -> list[Any]:
    """Return root and descendant frame IDs from a public CDP frame tree."""
    result: list[Any] = []
    pending = [frame_tree]
    while pending:
        current = pending.pop(0)
        result.append(current.frame.id_)
        pending[0:0] = list(current.child_frames or [])
    return result


def _nest_frame_snapshot(snapshot: str) -> str:
    """Wrap a separately fetched child AX tree in the snapshot's iframe dialect."""
    indented = "\n".join(f"  {line}" for line in snapshot.splitlines())
    return f"- iframe:\n{indented}"


def _render_ax_tree(
    nodes: list[Any], *, ref_start: int = 1
) -> tuple[str, dict[str, _RefTarget]]:
    """Render Chrome AX nodes in the snapshot dialect consumed by workflows."""
    by_id = {str(node.node_id): node for node in nodes}
    roots = [node for node in nodes if node.parent_id is None]
    refs: dict[str, _RefTarget] = {}
    lines: list[str] = []

    def visit(node: Any, depth: int) -> None:
        ignored = bool(node.ignored)
        role = _ax_value(node.role) or "generic"
        name = _clean_text(_ax_value(node.name))
        value = _clean_text(_ax_value(node.value))
        rendered_depth = depth
        if not ignored and role.lower() != "inlinetextbox":
            normalized_role = _normalize_role(role)
            line = f"{'  ' * depth}- {normalized_role}"
            if name:
                line += f' "{_quote(name)}"'
            properties = _ax_properties(node)
            if normalized_role == "heading" and "level" in properties:
                line += f" [level={properties['level']}]"
            if node.backend_dom_node_id is not None:
                ref_name = f"e{ref_start + len(refs)}"
                refs[ref_name] = _RefTarget(node.backend_dom_node_id)
                line += f" [ref={ref_name}]"
            if normalized_role in _CLICKABLE_ROLES:
                line += " [cursor=pointer]"
            if value and value != name:
                line += f": {value}"
            lines.append(line)
            rendered_depth += 1
        for child_id in node.child_ids or []:
            child = by_id.get(str(child_id))
            if child is not None:
                visit(child, rendered_depth)

    for root in roots:
        visit(root, 0)
    return "\n".join(lines), refs


def _ax_value(value: Any) -> str:
    if value is None or value.value is None:
        return ""
    return str(value.value)


def _ax_properties(node: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for prop in node.properties or []:
        name = getattr(prop.name, "value", str(prop.name))
        result[str(name)] = _ax_value(prop.value)
    return result


def _normalize_role(role: str) -> str:
    lowered = role.lower()
    return {
        "rootwebarea": "document",
        "statictext": "text",
        "genericcontainer": "generic",
        "paragraph": "generic",
    }.get(lowered, lowered)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _special_key(key: str) -> Any:
    normalized = key.replace("_", "").replace("-", "").lower()
    mapping = {
        "enter": zd.SpecialKeys.ENTER,
        "tab": zd.SpecialKeys.TAB,
        "escape": zd.SpecialKeys.ESCAPE,
        "backspace": zd.SpecialKeys.BACKSPACE,
        "delete": zd.SpecialKeys.DELETE,
        "arrowleft": zd.SpecialKeys.ARROW_LEFT,
        "arrowright": zd.SpecialKeys.ARROW_RIGHT,
        "arrowup": zd.SpecialKeys.ARROW_UP,
        "arrowdown": zd.SpecialKeys.ARROW_DOWN,
    }
    return mapping.get(normalized)


async def _connect_browser(host: str, port: int) -> tuple[zd.Browser, zd.Tab]:
    last_error: BaseException | None = None
    for _ in range(10):
        try:
            config = zd.Config(
                host=host,
                port=port,
                browser_connection_timeout=0.25,
                browser_connection_max_tries=20,
            )
            browser = await zd.Browser.create(config)
            tab = browser.main_tab
            if tab is None:
                await browser.stop()
                raise RuntimeError("No page tab is available")
            return browser, tab
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(0.25)
    raise CliError(
        f"Could not connect to browser at {host}:{port}: {last_error}",
        code="BROWSER_CONNECTION_FAILED",
    ) from last_error


def _launch_detached(args: list[str]) -> int:
    if sys.platform == "win32":
        kw: dict[str, Any] = {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        }
    else:
        kw = {"start_new_session": True}
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kw,
    )
    return process.pid


def _kill_process(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _parse_cdp_endpoint(endpoint: str) -> tuple[str, int]:
    url = endpoint.removeprefix("http://")
    host, separator, port_text = url.rpartition(":")
    if not separator:
        raise CliError(f"Invalid CDP endpoint: {endpoint}", code="INVALID_CDP_ENDPOINT")
    return host or "127.0.0.1", int(port_text)
