"""Tests for behavior intentionally specific to the public browser backend."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import zendriver as zd

from proton_cli.browser import Browser, _proxy_browser_args, _render_ax_tree
from proton_cli.release_policy import RELEASE_POLICY
from proton_cli.snapshot import find_composer_body_editor_ref


def _value(value: Any) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def test_public_release_uses_direct_connection_by_default() -> None:
    assert RELEASE_POLICY.default_proxy is None
    assert RELEASE_POLICY.require_proxy is False
    assert _proxy_browser_args(None) == []


def test_public_release_accepts_explicit_socks5_proxy() -> None:
    args = _proxy_browser_args("socks5://127.0.0.1:1080")
    assert "--proxy-server=socks5://127.0.0.1:1080" in args


def test_public_accessibility_tree_snapshot_has_workflow_refs() -> None:
    root = SimpleNamespace(
        node_id="root",
        parent_id=None,
        child_ids=["button"],
        ignored=True,
        role=_value("RootWebArea"),
        name=_value("Proton Mail"),
        value=None,
        properties=[],
        backend_dom_node_id=None,
    )
    button = SimpleNamespace(
        node_id="button",
        parent_id="root",
        child_ids=[],
        ignored=False,
        role=_value("button"),
        name=_value("Compose"),
        value=None,
        properties=[],
        backend_dom_node_id=123,
    )

    snapshot, refs = _render_ax_tree([root, button])

    assert 'button "Compose" [ref=e1] [cursor=pointer]' in snapshot
    assert refs["e1"].backend_node_id == 123


def _ax_node(
    node_id: str,
    *,
    role: str,
    name: str,
    backend_node_id: int | None,
    child_ids: list[str] | None = None,
    parent_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id,
        parent_id=parent_id,
        child_ids=child_ids or [],
        ignored=False,
        role=_value(role),
        name=_value(name),
        value=None,
        properties=[],
        backend_dom_node_id=backend_node_id,
    )


@pytest.mark.asyncio
async def test_snapshot_includes_child_frame_ax_trees_with_unique_refs() -> None:
    main_tree = [
        _ax_node(
            "main-root",
            role="RootWebArea",
            name="Mail",
            backend_node_id=None,
            child_ids=["compose"],
        ),
        _ax_node(
            "compose",
            role="button",
            name="Compose",
            backend_node_id=101,
            parent_id="main-root",
        ),
    ]
    child_tree = [
        _ax_node(
            "child-root",
            role="RootWebArea",
            name="Body",
            backend_node_id=None,
            child_ids=["editor"],
        ),
        _ax_node(
            "editor",
            role="generic",
            name="Message body",
            backend_node_id=202,
            parent_id="child-root",
        ),
    ]
    child_frame = SimpleNamespace(
        frame=SimpleNamespace(id_=zd.cdp.page.FrameId("child")), child_frames=None
    )
    frame_tree = SimpleNamespace(
        frame=SimpleNamespace(id_=zd.cdp.page.FrameId("main")),
        child_frames=[child_frame],
    )

    class FakeTab:
        def __init__(self) -> None:
            self.frame_requests: list[str | None] = []

        async def send(self, command: Any) -> Any:
            request = next(command)
            if request["method"] == "Accessibility.enable":
                return None
            if request["method"] == "Page.getFrameTree":
                return frame_tree
            if request["method"] == "Accessibility.getFullAXTree":
                frame_id = request.get("params", {}).get("frameId")
                normalized = str(frame_id) if frame_id is not None else None
                self.frame_requests.append(normalized)
                return child_tree if normalized == "child" else main_tree
            raise AssertionError(f"Unexpected CDP command: {request}")

    tab = FakeTab()
    browser = Browser()
    browser._sessions["default"] = SimpleNamespace(tab=tab, refs={})  # type: ignore[assignment]

    snapshot = await browser.snapshot("default")

    assert 'button "Compose" [ref=e1]' in snapshot
    assert 'generic "Message body" [ref=e2]' in snapshot
    assert find_composer_body_editor_ref(snapshot) == "e2"
    assert tab.frame_requests == ["main", "child"]
    assert browser._sessions["default"].refs["e1"].backend_node_id == 101
    assert browser._sessions["default"].refs["e2"].backend_node_id == 202


class _FakeTab:
    def __init__(self, browser: _FakeCoreBrowser, url: str) -> None:
        self.browser = browser
        self.url = url
        self.close = AsyncMock()
        self.commands: list[dict[str, Any]] = []

    async def send(self, command: Any) -> Any:
        request = next(command)
        self.commands.append(request)
        if request["method"] == "DOM.getBoxModel":
            return SimpleNamespace(
                content=[10.0, 20.0, 30.0, 20.0, 30.0, 40.0, 10.0, 40.0]
            )
        return None


class _FakeCoreBrowser:
    def __init__(self) -> None:
        self.stop = AsyncMock()
        self.original = _FakeTab(self, "https://mail.proton.me/u/0/inbox")
        self.tabs = [self.original]

    async def get(
        self, url: str = "about:blank", new_tab: bool = False, new_window: bool = False
    ) -> _FakeTab:
        assert new_tab is True
        assert new_window is False
        tab = _FakeTab(self, url)
        self.tabs.append(tab)
        return tab


@pytest.mark.asyncio
async def test_public_borrowed_tab_and_work_tab_preserve_caller_ownership(
    tmp_path,
) -> None:
    core = _FakeCoreBrowser()
    browser = Browser()

    assert browser.supports_recovery_email() is True
    await browser.borrow_tab(
        session="borrowed",
        tab=core.original,  # type: ignore[arg-type]
        output_dir=tmp_path,
    )
    async with browser.work_tab(
        source_session="borrowed", work_session="recovery-work"
    ) as work_session:
        assert work_session == "recovery-work"
        assert await browser.tab_urls(work_session) == [
            "https://mail.proton.me/u/0/inbox",
            "https://mail.proton.me/u/0/inbox",
        ]
        verification = _FakeTab(core, "https://account.proton.me/verify")
        core.tabs.append(verification)
        await browser.select_tab(work_session, 2)
        assert browser._sessions[work_session].tab is verification

    core.tabs[1].close.assert_awaited_once_with()
    core.original.close.assert_not_awaited()
    core.stop.assert_not_awaited()
    assert "recovery-work" not in browser._sessions
    assert browser._sessions["borrowed"].tab is core.original

    await browser.release("borrowed")

    core.original.close.assert_not_awaited()
    core.stop.assert_not_awaited()
    assert browser._sessions == {}


@pytest.mark.asyncio
async def test_public_click_and_fill_use_native_input_events(tmp_path) -> None:
    core = _FakeCoreBrowser()
    browser = Browser()
    await browser.borrow_tab(
        session="borrowed",
        tab=core.original,  # type: ignore[arg-type]
        output_dir=tmp_path,
    )
    browser._sessions["borrowed"].refs["e1"] = SimpleNamespace(
        backend_node_id=zd.cdp.dom.BackendNodeId(123)
    )

    await browser.click("borrowed", "e1")
    await browser.fill("borrowed", "e1", "recovery@example.test")

    methods = [request["method"] for request in core.original.commands]
    assert "Runtime.callFunctionOn" not in methods
    assert methods.count("DOM.getBoxModel") == 2
    mouse_events = [
        request["params"]["type"]
        for request in core.original.commands
        if request["method"] == "Input.dispatchMouseEvent"
    ]
    assert mouse_events == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]
    key_events = [
        request["params"]
        for request in core.original.commands
        if request["method"] == "Input.dispatchKeyEvent"
    ]
    assert any(event.get("key") == "a" and event.get("modifiers") == 2 for event in key_events)
    assert any(event.get("key") == "Backspace" for event in key_events)
    assert any(event.get("text") == "r" for event in key_events)


@pytest.mark.asyncio
async def test_public_refresh_chord_carries_control_modifier(tmp_path) -> None:
    core = _FakeCoreBrowser()
    browser = Browser()
    await browser.borrow_tab(
        session="borrowed",
        tab=core.original,  # type: ignore[arg-type]
        output_dir=tmp_path,
    )

    await browser.press("borrowed", "Control+R")

    key_events = [
        request["params"]
        for request in core.original.commands
        if request["method"] == "Input.dispatchKeyEvent"
    ]
    assert [event["type"] for event in key_events] == ["keyDown", "keyUp"]
    assert all(event["modifiers"] == 2 for event in key_events)
    assert all(event["key"].lower() == "r" for event in key_events)
