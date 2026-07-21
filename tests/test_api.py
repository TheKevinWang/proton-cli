"""Tests for the importable ``ProtonClient`` Python API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from proton_cli import CliError, ProtonClient
from proton_cli.release_policy import RELEASE_POLICY
from proton_cli.session import SessionState, SessionStore
from proton_cli.types import InboxRow, ReadMessage

# Import test helpers that are not fixtures from the shared conftest.
from tests.conftest import _tcp_always_open

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

READ_SNAPSHOT = (
    '- heading "Hello world" [ref=e1]\n'
    '- text "From: alice@example.com" [ref=e2]\n'
    '- text "To: me@example.com" [ref=e3]\n'
    '- text "Date: Today" [ref=e4]\n'
    '- article [ref=e5]\n'
    '    - text "This is the body." [ref=e6]\n'
)

LOGIN_FORM = (
    'textbox "Email or username" [ref=e1]\n'
    'textbox "Password" [ref=e2]\n'
    'button "Sign in" [ref=e3]\n'
)
LOGIN_FORM_FILLED = (
    'textbox "Email or username" [ref=e1]: user@example.com\n'
    'textbox "Password" [ref=e2]\n'
    'button "Sign in" [ref=e3]\n'
)
MAILBOX = 'button "Compose" [ref=e9]\nInbox\n'

LOGIN_HAPPY_PATH = [
    LOGIN_FORM,
    LOGIN_FORM,
    LOGIN_FORM_FILLED,
    LOGIN_FORM_FILLED,
    LOGIN_FORM_FILLED,
    LOGIN_FORM_FILLED,
    MAILBOX,
    MAILBOX,
]

MakeBrowser = Callable[..., object]
Sleep = Callable[[float], Awaitable[None]]


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


def _client(
    tmp_path: Path,
    browser: object,
    sleep: Sleep,
    session: str = "default",
) -> ProtonClient:
    return ProtonClient(
        session=session,
        app_root=tmp_path,
        env={},
        browser=browser,  # type: ignore[arg-type]
        sleep=sleep,
        tcp_probe=_tcp_always_open,
    )


@pytest.mark.asyncio
async def test_api_inbox_returns_typed_rows(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    await _seed_managed_session(tmp_path)
    browser = make_browser([INBOX_SNAPSHOT])
    client = _client(tmp_path, browser, no_sleep)

    rows = await client.inbox(limit=5)

    assert isinstance(rows, list)
    assert len(rows) == 2
    assert all(isinstance(row, InboxRow) for row in rows)
    assert rows[0].row == 1
    assert rows[0].handle == "1"
    assert rows[0].from_display == "generic : Alice Example"
    assert rows[0].subject == "Hello world"
    assert rows[0].target == "e5"


@pytest.mark.asyncio
async def test_api_read_returns_read_message(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    store = SessionStore(tmp_path)
    await store.save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(tmp_path / "profile"),
            debug_endpoint="http://127.0.0.1:9333",
            browser_process_id=4242,
            last_inbox=[
                InboxRow(
                    row=1,
                    handle="1",
                    status="unread",
                    starred=None,
                    has_attachment=None,
                    age="Today",
                    from_display="Alice Example",
                    from_email="alice@example.com",
                    labels=[],
                    subject="Hello world",
                    target="e5",
                )
            ],
        )
    )
    browser = make_browser([READ_SNAPSHOT])
    client = _client(tmp_path, browser, no_sleep)

    message = await client.read("1")

    assert isinstance(message, ReadMessage)
    assert message.sender == "alice@example.com"
    assert message.body == "This is the body."


@pytest.mark.asyncio
async def test_api_read_unknown_handle_raises_cli_error(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    store = SessionStore(tmp_path)
    await store.save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(tmp_path / "profile"),
            debug_endpoint="http://127.0.0.1:9333",
            browser_process_id=4242,
            last_inbox=[],
        )
    )
    browser = make_browser([READ_SNAPSHOT])
    client = _client(tmp_path, browser, no_sleep)

    with pytest.raises(CliError) as exc_info:
        await client.read("999")

    assert exc_info.value.code == "UNKNOWN_MESSAGE_HANDLE"


@pytest.mark.asyncio
async def test_api_status_returns_session_state(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    await _seed_managed_session(tmp_path)
    browser = make_browser()
    client = _client(tmp_path, browser, no_sleep)

    state = await client.status()

    assert isinstance(state, SessionState)
    assert state.name == "default"
    assert state.account_email == "user@example.com"


@pytest.mark.asyncio
async def test_api_status_none_when_unconfigured(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    browser = make_browser()
    client = _client(tmp_path, browser, no_sleep)

    state = await client.status()

    assert state is None


@pytest.mark.asyncio
async def test_api_login_returns_login_outcome(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    browser = make_browser(list(LOGIN_HAPPY_PATH))
    client = _client(tmp_path, browser, no_sleep)

    outcome = await client.login(email="user@example.com", password="secret")

    assert outcome.account == "user@example.com"
    assert outcome.session == "default"


@pytest.mark.asyncio
async def test_api_context_manager_does_not_close_browser(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    browser = make_browser()
    async with _client(tmp_path, browser, no_sleep) as client:
        # Trigger runtime construction so the facade is initialized.
        await client.status()

    assert ("close", "default") not in browser.calls


@pytest.mark.asyncio
async def test_api_login_passes_custom_proxy_to_browser(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    browser = make_browser(list(LOGIN_HAPPY_PATH))
    client = ProtonClient(
        session="default",
        app_root=tmp_path,
        env={},
        browser=browser,  # type: ignore[arg-type]
        proxy="socks5://10.0.0.1:1080",
        sleep=no_sleep,
        tcp_probe=_tcp_always_open,
    )

    await client.login(email="user@example.com", password="secret")

    open_calls = browser.calls_of("open_managed")
    assert len(open_calls) == 1
    assert open_calls[0]["proxy"] == "socks5://10.0.0.1:1080"


@pytest.mark.asyncio
async def test_api_login_uses_default_proxy_when_not_specified(
    make_browser: MakeBrowser, tmp_path: Path, no_sleep: Sleep
) -> None:
    browser = make_browser(list(LOGIN_HAPPY_PATH))
    client = _client(tmp_path, browser, no_sleep)

    await client.login(email="user@example.com", password="secret")

    open_calls = browser.calls_of("open_managed")
    assert len(open_calls) == 1
    assert open_calls[0]["proxy"] == RELEASE_POLICY.default_proxy
