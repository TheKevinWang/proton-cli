"""Login workflow decision-logic tests (Milestone 5)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from proton_cli.app import RunCliResult
from proton_cli.errors import CliError, redact_text
from proton_cli.session import SessionStore

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
LOGIN_FORM_REQUIRED = (
    'textbox "Email or username" [ref=e1]\n'
    'textbox "Password" [ref=e2]\n'
    'generic [ref=e4]: This field is required\n'
    'button "Sign in" [ref=e3]\n'
)
MAILBOX = 'button "Compose" [ref=e9]\nInbox\n'
CHALLENGE = 'heading "Verification code" [ref=e3]\nEnter the captcha to continue\n'

HAPPY_PATH_SNAPSHOTS = [
    LOGIN_FORM,  # initial wait for the login form
    LOGIN_FORM,  # resolve username ref
    LOGIN_FORM_FILLED,  # confirm username value stuck
    LOGIN_FORM_FILLED,  # single-step check (password already visible)
    LOGIN_FORM_FILLED,  # resolve password ref
    LOGIN_FORM_FILLED,  # confirm password value length
    LOGIN_FORM_FILLED,  # resolve Sign in ref
    MAILBOX,  # submit outcome
    MAILBOX,  # mailbox wait
]


RunCommand = Callable[..., Awaitable[RunCliResult]]
MakeBrowser = Callable[..., object]


@pytest.mark.asyncio
async def test_login_fills_credentials_and_saves_session(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    browser = make_browser(list(HAPPY_PATH_SNAPSHOTS))
    result = await run_command(
        ["login", "--email", "user@example.com", "--password", "secret"],
        browser,
    )
    assert result.exit_code == 0
    assert "Logged in as user@example.com" in result.stdout

    # Username then password are filled into the located refs, then the visible
    # "Sign in" button is clicked to submit.
    fills = browser.calls_of("fill")
    assert ("e1", "user@example.com") in fills
    assert ("e2", "secret") in fills
    assert "e3" in browser.calls_of("click")

    store = SessionStore(tmp_path)
    saved = await store.load("default")
    assert saved is not None
    assert saved.account_email == "user@example.com"


@pytest.mark.asyncio
async def test_login_persists_browser_status_refreshed_after_relaunch(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    browser = make_browser(list(HAPPY_PATH_SNAPSHOTS))
    statuses = iter(
        [
            {
                "session": "default",
                "url": "about:blank",
                "host": "127.0.0.1",
                "port": 9333,
                "process_id": 4242,
            },
            {
                "session": "default",
                "url": "https://mail.proton.me/u/0/inbox",
                "host": "127.0.0.1",
                "port": 9444,
                "process_id": 5252,
            },
        ]
    )

    async def changing_status(session: str) -> dict[str, object]:
        del session
        return next(statuses)

    browser.status = changing_status  # type: ignore[method-assign]

    await run_command(
        ["login", "--email", "user@example.com", "--password", "secret"],
        browser,
    )

    saved = await SessionStore(tmp_path).load("default")
    assert saved is not None
    assert saved.browser_process_id == 5252
    assert saved.debug_endpoint == "http://127.0.0.1:9444"


@pytest.mark.asyncio
async def test_login_password_from_env(
    run_command: RunCommand, make_browser: MakeBrowser
) -> None:
    browser = make_browser(list(HAPPY_PATH_SNAPSHOTS))
    result = await run_command(
        ["login", "--email", "user@example.com", "--password-env", "PROTON_PW"],
        browser,
        {"PROTON_PW": "from-env"},
    )
    assert result.exit_code == 0
    assert ("e2", "from-env") in browser.calls_of("fill")


@pytest.mark.asyncio
async def test_login_retries_fill_when_value_does_not_stick(
    run_command: RunCommand, make_browser: MakeBrowser
) -> None:
    # The first username fill is lost (e.g. the challenge iframe mounted and
    # re-rendered the form); the retry with a fresh ref must succeed.
    browser = make_browser(
        [
            LOGIN_FORM,  # initial wait for the login form
            LOGIN_FORM,  # resolve username ref (attempt 1)
            LOGIN_FORM,  # confirm: value missing -> retry
            LOGIN_FORM,  # resolve username ref (attempt 2)
            LOGIN_FORM_FILLED,  # confirm: value stuck
            LOGIN_FORM_FILLED,  # single-step check
            LOGIN_FORM_FILLED,  # resolve password ref
            LOGIN_FORM_FILLED,  # confirm password value length
            LOGIN_FORM_FILLED,  # resolve Sign in ref
            MAILBOX,  # submit outcome
            MAILBOX,  # mailbox wait
        ]
    )
    result = await run_command(
        ["login", "--email", "user@example.com", "--password", "secret"],
        browser,
    )
    assert result.exit_code == 0
    username_fills = [f for f in browser.calls_of("fill") if f == ("e1", "user@example.com")]
    assert len(username_fills) == 2


@pytest.mark.asyncio
async def test_login_retries_whole_form_on_empty_submit_validation(
    run_command: RunCommand, make_browser: MakeBrowser
) -> None:
    # A submit that bounces back with "This field is required" restarts the
    # fill sequence instead of timing out on the mailbox wait.
    browser = make_browser(
        [
            LOGIN_FORM,  # initial wait for the login form
            LOGIN_FORM,  # resolve username ref
            LOGIN_FORM_FILLED,  # confirm username value stuck
            LOGIN_FORM_FILLED,  # single-step check
            LOGIN_FORM_FILLED,  # resolve password ref
            LOGIN_FORM_FILLED,  # confirm password value length
            LOGIN_FORM_FILLED,  # resolve Sign in ref
            LOGIN_FORM_REQUIRED,  # submit outcome: validation -> retry form
            LOGIN_FORM,  # resolve username ref (attempt 2)
            LOGIN_FORM_FILLED,  # confirm username value stuck
            LOGIN_FORM_FILLED,  # single-step check
            LOGIN_FORM_FILLED,  # resolve password ref
            LOGIN_FORM_FILLED,  # confirm password value length
            LOGIN_FORM_FILLED,  # resolve Sign in ref
            MAILBOX,  # submit outcome
            MAILBOX,  # mailbox wait
        ]
    )
    result = await run_command(
        ["login", "--email", "user@example.com", "--password", "secret"],
        browser,
    )
    assert result.exit_code == 0
    username_fills = [f for f in browser.calls_of("fill") if f == ("e1", "user@example.com")]
    assert len(username_fills) == 2
    assert "Logged in as user@example.com" in result.stdout


LOGIN_FORM_REJECTED = (
    'textbox "Email or username" [ref=e1]: user@example.com\n'
    'textbox "Password" [ref=e2]\n'
    'generic [ref=e4]: Incorrect login credentials. Please try again.\n'
    'button "Sign in" [ref=e3]\n'
)


@pytest.mark.asyncio
async def test_login_fails_fast_when_credentials_rejected(
    run_command: RunCommand, make_browser: MakeBrowser
) -> None:
    browser = make_browser(
        [
            LOGIN_FORM,  # initial wait for the login form
            LOGIN_FORM,  # resolve username ref
            LOGIN_FORM_FILLED,  # confirm username value stuck
            LOGIN_FORM_FILLED,  # single-step check
            LOGIN_FORM_FILLED,  # resolve password ref
            LOGIN_FORM_FILLED,  # confirm password value length
            LOGIN_FORM_FILLED,  # resolve Sign in ref
            LOGIN_FORM_REJECTED,  # submit outcome: credentials rejected
        ]
    )
    with pytest.raises(CliError) as exc_info:
        await run_command(
            ["login", "--email", "user@example.com", "--password", "wrong"],
            browser,
        )
    assert exc_info.value.code == "LOGIN_CREDENTIALS_REJECTED"
    password_fills = [f for f in browser.calls_of("fill") if f == ("e2", "wrong")]
    assert len(password_fills) == 1


@pytest.mark.asyncio
async def test_login_retries_password_fill_when_value_duplicated(
    run_command: RunCommand, make_browser: MakeBrowser
) -> None:
    # A fill that silently lands twice (React restored its model over the
    # JS-cleared field) must be detected by the length check and re-filled.
    browser = make_browser(
        [
            LOGIN_FORM,  # initial wait for the login form
            LOGIN_FORM,  # resolve username ref
            LOGIN_FORM_FILLED,  # confirm username value stuck
            LOGIN_FORM_FILLED,  # single-step check
            LOGIN_FORM_FILLED,  # resolve password ref (attempt 1)
            LOGIN_FORM_FILLED,  # confirm snapshot (attempt 1)
            LOGIN_FORM_FILLED,  # resolve password ref (attempt 2)
            LOGIN_FORM_FILLED,  # confirm snapshot (attempt 2)
            LOGIN_FORM_FILLED,  # resolve Sign in ref
            MAILBOX,  # submit outcome
            MAILBOX,  # mailbox wait
        ]
    )
    read_values = iter(["secretsecret", "secret"])

    async def fake_read_input_value(session: str, target: str) -> str:
        return next(read_values, "secret")

    browser.read_input_value = fake_read_input_value  # type: ignore[method-assign]

    result = await run_command(
        ["login", "--email", "user@example.com", "--password", "secret"],
        browser,
    )
    assert result.exit_code == 0
    password_fills = [f for f in browser.calls_of("fill") if f == ("e2", "secret")]
    assert len(password_fills) == 2


@pytest.mark.asyncio
async def test_login_unset_password_env_fails_before_browser(
    run_command: RunCommand, make_browser: MakeBrowser
) -> None:
    browser = make_browser([LOGIN_FORM])
    with pytest.raises(CliError) as exc_info:
        await run_command(
            ["login", "--email", "user@example.com", "--password-env", "MISSING_PW"],
            browser,
            {},
        )
    assert exc_info.value.code == "UNSET_PASSWORD_ENV"
    # No browser was opened because validation failed first.
    assert browser.calls == []


@pytest.mark.asyncio
async def test_failed_browser_launch_removes_new_tool_owned_profile(
    run_command: RunCommand, make_browser: MakeBrowser, tmp_path: Path
) -> None:
    browser = make_browser([LOGIN_FORM])

    async def fail_open(**_kwargs: object) -> str:
        raise CliError("Chrome failed to start", code="BROWSER_CONNECTION_FAILED")

    browser.open_managed = fail_open  # type: ignore[method-assign]

    with pytest.raises(CliError):
        await run_command(
            ["login", "--email", "user@example.com", "--password", "secret"],
            browser,
        )

    assert not (tmp_path / "profiles" / "default").exists()


def test_password_value_is_redacted_in_diagnostics() -> None:
    redacted = redact_text("login failed with --password hunter2 in args")
    assert "hunter2" not in redacted
    assert "[REDACTED]" in redacted
