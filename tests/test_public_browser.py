"""Tests for behavior intentionally specific to the public browser backend."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

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
        _ax_node("main-root", role="RootWebArea", name="Mail", backend_node_id=None, child_ids=["compose"]),
        _ax_node(
            "compose",
            role="button",
            name="Compose",
            backend_node_id=101,
            parent_id="main-root",
        ),
    ]
    child_tree = [
        _ax_node("child-root", role="RootWebArea", name="Body", backend_node_id=None, child_ids=["editor"]),
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
        frame=SimpleNamespace(id_=zd.cdp.page.FrameId("main")), child_frames=[child_frame]
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
