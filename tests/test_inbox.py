"""Inbox/refresh workflow tests (Milestone 6)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from proton_cli.app import RunCliResult
from proton_cli.session import SessionState, SessionStore

INBOX_SNAPSHOT = (
    'button "Compose" [ref=e1]\n'
    'region "Hello world" [ref=e5] [cursor=pointer]:\n'
    '  checkbox [ref=e10]\n'
    '  generic [ref=e11]:\n'
    '    button "Star conversation" [ref=e12]\n'
    '    generic "alice@example.com" [ref=e15]:\n'
    '      generic [ref=e16]: Alice Example\n'
    '    heading "Hello world" [level=2] [ref=e18]\n'
    '    generic [ref=e20]:\n'
    '      time [ref=e21]: 10:30 AM\n'
    'region "Lunch plans" [ref=e6] [cursor=pointer]:\n'
    '  checkbox [ref=e30]\n'
    '  generic [ref=e31]:\n'
    '    button "Star conversation" [ref=e32]\n'
    '    generic "bob@example.com" [ref=e35]\n'
    '    heading "Lunch plans" [level=2] [ref=e38]\n'
    '    generic [ref=e40]:\n'
    '      time [ref=e41]: Yesterday\n'
)

RunCommand = Callable[..., Awaitable[RunCliResult]]
MakeBrowser = Callable[..., object]


async def _seed_managed_session(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path)
    await store.save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(tmp_path / "profile"),
            debug_endpoint="http://127.0.0.1:9333",
            browser_process_id=4242,
            account_email="user@example.com",
        )
    )
    return store


@pytest.mark.asyncio
async def test_inbox_parses_rows_and_saves_last_inbox(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    store = await _seed_managed_session(tmp_path)
    browser = make_browser([INBOX_SNAPSHOT])

    result = await run_command(["inbox"], browser)
    assert result.exit_code == 0
    assert "Alice" in result.stdout
    assert "Hello world" in result.stdout
    # Reusing the saved managed session must not open a new browser.
    assert browser.calls_of("open_managed") == []
    assert ("goto", "https://mail.proton.me/u/0/inbox") in browser.calls

    saved = await store.load("default")
    assert saved is not None
    assert saved.last_inbox is not None
    assert saved.last_inbox[0].handle == "1"
    assert saved.last_inbox[0].target == "e5"


@pytest.mark.asyncio
async def test_inbox_requires_existing_session(
    run_command: RunCommand, make_browser: MakeBrowser
) -> None:
    browser = make_browser([INBOX_SNAPSHOT])
    with pytest.raises(Exception) as exc_info:
        await run_command(["inbox"], browser)
    assert getattr(exc_info.value, "code", None) == "NO_BROWSER_SESSION"


@pytest.mark.asyncio
async def test_sent_folder_navigates_to_sent(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    await _seed_managed_session(tmp_path)
    browser = make_browser([INBOX_SNAPSHOT])
    await run_command(["inbox", "--folder", "sent"], browser)
    assert ("goto", "https://mail.proton.me/u/0/all-sent") in browser.calls


@pytest.mark.asyncio
async def test_refresh_updates_last_inbox(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    store = await _seed_managed_session(tmp_path)
    browser = make_browser([INBOX_SNAPSHOT])
    result = await run_command(["refresh"], browser)
    assert result.exit_code == 0
    saved = await store.load("default")
    assert saved is not None and saved.last_inbox is not None
    assert len(saved.last_inbox) >= 1


WELCOME_SCREEN_1 = """
- main [ref=e1]:
  - button "New message" [ref=e2]
  - dialog [active] [ref=e10]:
    - heading "Welcome to Proton Mail" [level=1] [ref=e11]
    - button "Let's get started" [ref=e12]
"""

WELCOME_SCREEN_2 = """
- main [ref=e1]:
  - button "New message" [ref=e2]
  - dialog [active] [ref=e10]:
    - heading "Distraction-free emailing" [level=1] [ref=e11]
    - button "Maybe later" [ref=e12]
"""

WELCOME_SCREEN_DONE = """
- main [ref=e1]:
  - button "New message" [ref=e2]
  - region "Hello world" [ref=e5] [cursor=pointer]:
    - checkbox [ref=e10]
    - generic [ref=e11]:
      - button "Star conversation" [ref=e12]
      - generic "alice@example.com" [ref=e15]:
        - generic [ref=e16]: Alice Example
      - heading "Hello world" [level=2] [ref=e18]
      - generic [ref=e20]:
        - time [ref=e21]: 10:30 AM
"""


@pytest.mark.asyncio
async def test_inbox_dismisses_welcome_dialog(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    await _seed_managed_session(tmp_path)
    browser = make_browser([WELCOME_SCREEN_1, WELCOME_SCREEN_2, WELCOME_SCREEN_DONE])
    result = await run_command(["inbox"], browser)
    assert result.exit_code == 0
    assert "Alice" in result.stdout
    assert ("click", "e12") in browser.calls
    assert browser.calls.count(("click", "e12")) == 2
