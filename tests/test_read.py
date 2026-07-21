"""Read workflow tests (Milestone 6)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from proton_cli.app import RunCliResult
from proton_cli.session import SessionState, SessionStore
from proton_cli.types import InboxRow

READ_SNAPSHOT = (
    '- heading "Hello world" [ref=e1]\n'
    '- text "From: alice@example.com" [ref=e2]\n'
    '- text "To: me@example.com" [ref=e3]\n'
    '- text "Date: Today" [ref=e4]\n'
    '- article [ref=e5]\n'
    '    - text "This is the body." [ref=e6]\n'
)

RunCommand = Callable[..., Awaitable[RunCliResult]]
MakeBrowser = Callable[..., object]


def _row(handle: str, target: str | None) -> InboxRow:
    return InboxRow(
        row=int(handle),
        handle=handle,
        status="unread",
        starred=None,
        has_attachment=None,
        age="Today",
        from_display="Alice Example",
        from_email="alice@example.com",
        labels=[],
        subject="Hello world",
        target=target,
    )


async def _seed_session_with_inbox(tmp_path: Path, rows: list[InboxRow]) -> SessionStore:
    store = SessionStore(tmp_path)
    await store.save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(tmp_path / "profile"),
            debug_endpoint="http://127.0.0.1:9333",
            browser_process_id=4242,
            last_inbox=rows,
        )
    )
    return store


@pytest.mark.asyncio
async def test_read_resolves_handle_and_parses_message(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    await _seed_session_with_inbox(tmp_path, [_row("1", "e5")])
    browser = make_browser([READ_SNAPSHOT])

    result = await run_command(["read", "1"], browser)
    assert result.exit_code == 0
    # The saved click target for handle "1" is what gets clicked.
    assert "e5" in browser.calls_of("click")
    assert "From: alice@example.com" in result.stdout
    assert "Subject: Hello world" in result.stdout
    assert "This is the body." in result.stdout


@pytest.mark.asyncio
async def test_read_unknown_handle_guides_back_to_inbox(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    await _seed_session_with_inbox(tmp_path, [_row("1", "e5")])
    browser = make_browser([READ_SNAPSHOT])
    with pytest.raises(Exception) as exc_info:
        await run_command(["read", "9"], browser)
    assert getattr(exc_info.value, "code", None) == "UNKNOWN_MESSAGE_HANDLE"


INBOX_SNAPSHOT = (
    '- region "Hello world" [ref=e77] [cursor=pointer]:\n'
    '    - button "Star conversation" [ref=e78]\n'
    '    - heading "Hello world" [level=2] [ref=e79]\n'
    '    - generic "alice@example.com" [ref=e80]\n'
    '    - time "Today" [ref=e81]: Today\n'
)


@pytest.mark.asyncio
async def test_read_reresolves_stale_row_target(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    # The saved row target (e5) went stale after the inbox re-rendered; the
    # read must re-resolve the same message from a fresh inbox snapshot (e77).
    await _seed_session_with_inbox(tmp_path, [_row("1", "e5")])
    browser = make_browser(["", "", "", INBOX_SNAPSHOT, READ_SNAPSHOT])

    original_click = browser.click
    failed_targets: list[str] = []

    async def flaky_click(session: str, target: str) -> str:
        if target == "e5" and not failed_targets:
            failed_targets.append(target)
            raise RuntimeError("Ref e5 not found in the current page snapshot.")
        return await original_click(session, target)

    browser.click = flaky_click  # type: ignore[method-assign]

    result = await run_command(["read", "1"], browser)
    assert result.exit_code == 0
    assert failed_targets == ["e5"]
    assert "e77" in browser.calls_of("click")
    assert "This is the body." in result.stdout


@pytest.mark.asyncio
async def test_read_stale_handle_without_target(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    await _seed_session_with_inbox(tmp_path, [_row("1", None)])
    browser = make_browser([READ_SNAPSHOT])
    with pytest.raises(Exception) as exc_info:
        await run_command(["read", "1"], browser)
    assert getattr(exc_info.value, "code", None) == "STALE_MESSAGE_HANDLE"


CONVO_COLLAPSED_SNAPSHOT = (
    '- article [ref=e10]:\n'
    '    - generic [ref=e11] [cursor=pointer]:\n'
    '        - link "More details about Proton" [ref=e12]\n'
    '        - time [ref=e13]: Jun 18\n'
    '- article [ref=e20]:\n'
    '    - generic [ref=e21] [cursor=pointer]:\n'
    '        - link "More details about Proton" [ref=e22]\n'
    '        - time [ref=e23]: Jul 14\n'
    '- article [active] [ref=e30]:\n'
    '    - generic [ref=e31]:\n'
    '        - text "Old expanded body." [ref=e32]\n'
)

CONVO_EXPANDED_SNAPSHOT = (
    '- article [active] [ref=e10]:\n'
    '    - generic [ref=e31]:\n'
    '        - text "Old expanded body." [ref=e32]\n'
    '- article [active] [ref=e20]:\n'
    '    - generic [ref=e41]:\n'
    '        - text "This is the body." [ref=e42]\n'
    '        - /url: https://account.proton.me/verify-email?email=laura@example.com#token\n'
    '- article [active] [ref=e30]:\n'
    '    - generic [ref=e51]:\n'
    '        - text "Newest body." [ref=e52]\n'
)


@pytest.mark.asyncio
async def test_read_expands_collapsed_conversation_messages(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    # Three welcome-dialog stability snapshots plus one expansion-pass snapshot
    # show the collapsed conversation; the post-click snapshot has all bodies.
    await _seed_session_with_inbox(tmp_path, [_row("1", "e5")])
    browser = make_browser(
        [CONVO_COLLAPSED_SNAPSHOT] * 4 + [CONVO_EXPANDED_SNAPSHOT]
    )

    result = await run_command(["read", "1"], browser)
    assert result.exit_code == 0
    clicks = browser.calls_of("click")
    assert "e11" in clicks
    assert "e21" in clicks
    assert "e31" not in clicks
    assert "This is the body." in result.stdout
    assert "Newest body." in result.stdout


@pytest.mark.asyncio
async def test_read_json_uses_from_key(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    import json

    await _seed_session_with_inbox(tmp_path, [_row("1", "e5")])
    browser = make_browser([READ_SNAPSHOT])
    result = await run_command(["read", "1", "--json"], browser)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["message"]["from"] == "alice@example.com"
    assert "sender" not in payload["message"]
