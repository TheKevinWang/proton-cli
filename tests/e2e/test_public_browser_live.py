"""Opt-in local Chrome smoke test for the released-Zendriver backend."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from pathlib import Path

import pytest

from proton_cli.browser import Browser
from proton_cli.snapshot import find_composer_body_editor_ref, find_first_ref

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PROTON_CLI_BROWSER_TESTS") != "1",
    reason="set RUN_PROTON_CLI_BROWSER_TESTS=1 to launch Chrome",
)


@pytest.mark.asyncio
async def test_public_backend_can_snapshot_fill_and_click(tmp_path: Path) -> None:
    browser = Browser()
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "simple.html"
    try:
        await browser.open_managed(
            session="public-smoke",
            mode="headless",
            profile_dir=str(tmp_path / "profile"),
            output_dir=tmp_path / "output",
            url=fixture.as_uri(),
            proxy=None,
        )
        snapshot = await browser.snapshot("public-smoke")
        textbox = find_first_ref(snapshot, [re.compile(r'textbox "Your name"', re.I)])
        button = find_first_ref(snapshot, [re.compile(r'button "Click me"', re.I)])
        editor = find_first_ref(snapshot, [re.compile(r'generic "Message body"', re.I)])
        assert textbox is not None
        assert button is not None
        assert editor is not None
        assert find_composer_body_editor_ref(snapshot) == editor

        await browser.fill("public-smoke", textbox, "Public")
        await browser.fill("public-smoke", editor, "Frame text")
        await browser.click("public-smoke", button)
        await asyncio.sleep(0.2)

        updated = await browser.snapshot("public-smoke")
        assert "Hello, Public" in updated
        assert "Frame text" in updated
    finally:
        with contextlib.suppress(Exception):
            await browser.close("public-smoke")
