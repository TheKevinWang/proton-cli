"""Send workflow decision-logic tests (Milestone 7)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from proton_cli import app
from proton_cli.app import Runtime
from proton_cli.errors import CliError

MakeBrowser = Callable[..., object]

COMPOSER_OPEN = (
    'region "Composer: Test" [ref=e1]\n'
    'textbox "Subject" [ref=e2]\n'
    'button "Send" [ref=e5]\n'
)
COMPOSER_GONE = 'button "Compose" [ref=e1]\nInbox\n'
CONFIRM_DIALOG = (
    'dialog "Send without subject?" [ref=e7]\n'
    'button "Send anyway" [ref=e8]\n'
)
TO_FIELD = 'combobox "To" [ref=e2]\n'


@pytest.mark.asyncio
async def test_send_returns_when_composer_closes(
    make_runtime: Callable[..., Runtime], make_browser: MakeBrowser
) -> None:
    browser = make_browser([COMPOSER_OPEN, COMPOSER_GONE])
    runtime = make_runtime(browser)
    await app._send_current_composer("default", runtime, "Test")
    assert "e5" in browser.calls_of("click")


@pytest.mark.asyncio
async def test_send_clicks_confirmation_dialog(
    make_runtime: Callable[..., Runtime], make_browser: MakeBrowser
) -> None:
    browser = make_browser([CONFIRM_DIALOG, COMPOSER_GONE])
    runtime = make_runtime(browser)
    await app._send_current_composer("default", runtime, "Test")
    # The "Send anyway" confirmation control must be clicked.
    assert "e8" in browser.calls_of("click")


@pytest.mark.asyncio
async def test_send_does_not_succeed_on_persistent_composer(
    make_runtime: Callable[..., Runtime],
    make_browser: MakeBrowser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Composer never closes (draft autosave keeps it open) -> must not be treated
    # as success; the loop exhausts its deadline and raises.
    monkeypatch.setattr(app, "SEND_CONFIRMATION_TIMEOUT_MS", 50)
    monkeypatch.setattr(app, "SEND_CLICK_SETTLE_MS", 0)
    browser = make_browser([COMPOSER_OPEN])
    runtime = make_runtime(browser)
    with pytest.raises(CliError) as exc_info:
        await app._send_current_composer("default", runtime, "Test")
    assert exc_info.value.code == "SEND_NOT_CONFIRMED"


@pytest.mark.asyncio
async def test_fill_recipients_targets_field_and_submits_each(
    make_runtime: Callable[..., Runtime], make_browser: MakeBrowser
) -> None:
    browser = make_browser([TO_FIELD])
    runtime = make_runtime(browser)
    await app._fill_recipients("default", runtime, "to", ["a@example.com", "b@example.com"])

    assert "e2" in browser.calls_of("click")
    assert browser.calls_of("type_text") == ["a@example.com", "b@example.com"]
    assert browser.calls_of("press") == ["Enter", "Enter"]


@pytest.mark.asyncio
async def test_fill_recipients_missing_field_raises(
    make_runtime: Callable[..., Runtime], make_browser: MakeBrowser
) -> None:
    browser = make_browser(['button "Compose" [ref=e1]\n'])
    runtime = make_runtime(browser)
    with pytest.raises(CliError) as exc_info:
        await app._fill_recipients("default", runtime, "to", ["a@example.com"])
    assert exc_info.value.code == "RECIPIENT_FIELD_NOT_FOUND"
