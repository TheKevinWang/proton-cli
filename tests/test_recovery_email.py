from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from proton_cli.app import RunCliOptions, run_cli
from proton_cli.errors import CliError
from proton_cli.session import SessionState, SessionStore
from proton_cli.types import RecoveryEmailOutcome
from proton_cli.workflows.recovery_email import (
    INTERACTIVE_PROMPT,
    InteractiveRecoveryEmailVerifier,
    ProtonMailboxVerifier,
    RecoveryEmailChallenge,
    run_recovery_email_workflow,
)
from tests.conftest import FakeBrowser

INBOX = '- button "Toggle settings" [ref=e1]'
DRAWER = '- link "All settings" [ref=e2]'
DASHBOARD = '- link "Recovery" [ref=e3]'
RECOVERY = '- link "Email verification Add an email address" [ref=e4]'
EMPTY = """
- textbox "Your recovery email" [ref=e5]:
  - /placeholder: example@domain.com
"""
TYPED = """
- textbox "Your recovery email" [ref=e5]:
  - text: recovery@proton.me
- button "Add and verify" [ref=e6]
"""
PASSWORD = """
- dialog [ref=e20]:
  - heading "Enter your password" [ref=e21]
  - textbox "Password" [ref=e7]
  - button "Authenticate" [ref=e8]
"""
UNVERIFIED_DIALOG = """
- textbox "Your recovery email" [ref=e5]:
  - text: recovery@proton.me
- generic [ref=e10]: Unverified
- button "Verify" [ref=e11]
- dialog [ref=e12]:
  - heading "Verify recovery email?" [ref=e13]
  - button "Verify with email" [ref=e9]
"""
UNVERIFIED = """
- textbox "Your recovery email" [ref=e5]:
  - text: recovery@proton.me
- generic [ref=e10]: Unverified
- button "Verify" [ref=e11]
"""
SENT = UNVERIFIED + '\n- alert [ref=e14]: Verification email sent to recovery@proton.me'
VERIFIED = """
- textbox "Your recovery email" [ref=e5]:
  - text: recovery@proton.me
- generic [ref=e15]: Verified
"""
DIFFERENT = """
- textbox "Your recovery email" [ref=e5]:
  - text: other@example.test
- generic [ref=e10]: Unverified
"""


class _InputOnlyBrowser:
    def __init__(self, snapshots: list[str]) -> None:
        self.snapshots = snapshots
        self.index = 0
        self.calls: list[tuple[str, Any]] = []

    def supports_recovery_email(self) -> bool:
        return True

    @asynccontextmanager
    async def work_tab(
        self, *, source_session: str, work_session: str
    ) -> AsyncIterator[str]:
        self.calls.append(("work_tab", (source_session, work_session)))
        yield work_session

    async def snapshot(self, session: str) -> str:
        self.calls.append(("snapshot", session))
        snapshot = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return snapshot

    async def click(self, session: str, target: str) -> str:
        self.calls.append(("click", target))
        return "clicked"

    async def type_text(self, session: str, text: str) -> str:
        self.calls.append(("type_text", text))
        return "typed"

    async def press(self, session: str, key: str) -> str:
        self.calls.append(("press", key))
        return "pressed"

    async def key_down(self, session: str, key: str) -> str:
        self.calls.append(("key_down", key))
        return "down"

    async def key_up(self, session: str, key: str) -> str:
        self.calls.append(("key_up", key))
        return "up"


class _Verifier:
    def __init__(self) -> None:
        self.entered = 0
        self.completed: list[RecoveryEmailChallenge] = []
        self.exited = 0

    async def __aenter__(self) -> _Verifier:
        self.entered += 1
        return self

    async def complete(self, challenge: RecoveryEmailChallenge) -> None:
        self.completed.append(challenge)

    async def __aexit__(self, *_exc: object) -> None:
        self.exited += 1


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_source_workflow_uses_only_input_methods_and_returns_verified() -> None:
    browser = _InputOnlyBrowser(
        [
            INBOX,
            DRAWER,
            DASHBOARD,
            RECOVERY,
            EMPTY,
            TYPED,
            TYPED,
            PASSWORD,
            UNVERIFIED_DIALOG,
            SENT,
            VERIFIED,
        ]
    )
    verifier = _Verifier()

    outcome = await run_recovery_email_workflow(
        browser,
        source_session="borrowed",
        email="recovery@proton.me",
        account_password="source-secret",
        verifier=verifier,
        verification="proton",
        timeout_seconds=10,
        sleep=_no_sleep,
    )

    assert outcome == RecoveryEmailOutcome(
        email="recovery@proton.me",
        status="verified",
        verification="proton",
        session="borrowed",
    )
    assert verifier.entered == 1
    assert verifier.exited == 1
    assert len(verifier.completed) == 1
    assert verifier.completed[0].email == "recovery@proton.me"
    assert set(vars(verifier.completed[0])) == {"email", "requested_at", "timeout_seconds"}
    names = {name for name, _payload in browser.calls}
    assert names <= {
        "work_tab",
        "snapshot",
        "click",
        "type_text",
        "press",
        "key_down",
        "key_up",
    }
    assert ("type_text", "source-secret") in browser.calls
    assert ("type_text", "recovery@proton.me") in browser.calls
    assert ("press", "Control+R") in browser.calls


@pytest.mark.asyncio
async def test_already_verified_is_idempotent_and_does_not_enter_verifier() -> None:
    browser = _InputOnlyBrowser([INBOX, DRAWER, DASHBOARD, RECOVERY, VERIFIED])
    verifier = _Verifier()

    outcome = await run_recovery_email_workflow(
        browser,
        source_session="borrowed",
        email="recovery@proton.me",
        account_password=None,
        verifier=verifier,
        verification="proton",
        timeout_seconds=10,
        sleep=_no_sleep,
    )

    assert outcome.status == "already_verified"
    assert verifier.entered == 0


@pytest.mark.asyncio
async def test_different_recovery_address_is_not_replaced() -> None:
    browser = _InputOnlyBrowser([INBOX, DRAWER, DASHBOARD, RECOVERY, DIFFERENT])

    with pytest.raises(CliError) as raised:
        await run_recovery_email_workflow(
            browser,
            source_session="default",
            email="recovery@proton.me",
            account_password=None,
            verifier=_Verifier(),
            verification="proton",
            timeout_seconds=10,
            sleep=_no_sleep,
        )

    assert raised.value.code == "RECOVERY_EMAIL_ALREADY_SET"
    assert not any(name == "type_text" for name, _payload in browser.calls)


@pytest.mark.asyncio
async def test_password_prompt_requires_account_password() -> None:
    browser = _InputOnlyBrowser(
        [INBOX, DRAWER, DASHBOARD, RECOVERY, EMPTY, TYPED, TYPED, PASSWORD]
    )

    with pytest.raises(CliError) as raised:
        await run_recovery_email_workflow(
            browser,
            source_session="default",
            email="recovery@proton.me",
            account_password=None,
            verifier=_Verifier(),
            verification="proton",
            timeout_seconds=10,
            sleep=_no_sleep,
        )

    assert raised.value.code == "ACCOUNT_PASSWORD_REQUIRED"


@pytest.mark.asyncio
async def test_same_unverified_address_resumes_without_retyping() -> None:
    browser = _InputOnlyBrowser(
        [INBOX, DRAWER, DASHBOARD, RECOVERY, UNVERIFIED, UNVERIFIED_DIALOG, SENT, VERIFIED]
    )
    verifier = _Verifier()

    outcome = await run_recovery_email_workflow(
        browser,
        source_session="default",
        email="recovery@proton.me",
        account_password=None,
        verifier=verifier,
        verification="proton",
        timeout_seconds=10,
        sleep=_no_sleep,
    )

    assert outcome.status == "verified"
    assert not any(name == "type_text" for name, _payload in browser.calls)


@pytest.mark.asyncio
async def test_interactive_verifier_uses_trace_confirmed_prompt() -> None:
    prompts: list[str] = []

    async def callback(prompt: str) -> None:
        prompts.append(prompt)

    verifier = InteractiveRecoveryEmailVerifier(callback)
    async with verifier:
        await verifier.complete(
            RecoveryEmailChallenge(
                email="person@example.test",
                requested_at=1.0,
                timeout_seconds=10,
            )
        )

    assert prompts == [INTERACTIVE_PROMPT]
    assert INTERACTIVE_PROMPT == (
        'Open the new recovery email, click "Verify email", '
        "then press Enter when verification is complete."
    )


@pytest.mark.asyncio
async def test_proton_mailbox_verifier_is_isolated_clicks_visible_link_and_cleans_up() -> None:
    recipient_snapshots = [
        '- heading "Inbox" [ref=e1]',
        """
- region [ref=e20] [cursor=pointer]:
  - generic [ref=e19]: Unread email
  - generic "no-reply@verify.proton.me" [ref=e21]
  - heading "Verify your recovery email" [level=2] [ref=e22]
""",
        """
- article [ref=e30]:
  - iframe [ref=f2e5]:
    - link "Verify email" [ref=f2e6]
""",
        """
- heading "Email verified" [level=1] [ref=e40]
- generic [ref=e41]: Thank you for verifying your email address.
""",
    ]

    class RecipientBrowser:
        def __init__(self) -> None:
            self.snapshots = list(recipient_snapshots)
            self.calls: list[tuple[str, Any]] = []
            self.urls_calls = 0

        async def snapshot(self, session: str) -> str:
            self.calls.append(("snapshot", session))
            return self.snapshots.pop(0)

        async def press(self, session: str, key: str) -> str:
            self.calls.append(("press", key))
            return "pressed"

        async def click(self, session: str, ref: str) -> str:
            self.calls.append(("click", ref))
            return "clicked"

        async def tab_urls(self, session: str) -> list[str]:
            self.urls_calls += 1
            return (
                ["https://mail.proton.me/u/1/inbox"]
                if self.urls_calls == 1
                else [
                    "https://mail.proton.me/u/1/inbox",
                    "https://account.proton.me/verify-email",
                ]
            )

        async def select_tab(self, session: str, index: int) -> str:
            self.calls.append(("select_tab", index))
            return "selected"

    class Runtime:
        def __init__(self, browser: RecipientBrowser) -> None:
            self.browser = browser

        async def sleep(self, _seconds: float) -> None:
            return None

    class Client:
        def __init__(self, browser: RecipientBrowser) -> None:
            self.runtime = Runtime(browser)
            self.login_calls: list[dict[str, Any]] = []
            self.close_calls: list[bool] = []

        async def login(self, **kwargs: Any) -> None:
            self.login_calls.append(kwargs)

        async def _get_runtime(self) -> Runtime:
            return self.runtime

        async def close(self, *, force: bool) -> None:
            self.close_calls.append(force)

    browser = RecipientBrowser()
    client = Client(browser)
    factory_kwargs: dict[str, Any] = {}

    def factory(**kwargs: Any) -> Client:
        factory_kwargs.update(kwargs)
        return client

    verifier = ProtonMailboxVerifier(
        email="recovery@proton.me",
        password="recovery-secret",
        client_factory=factory,
        timeout_seconds=10,
    )
    async with verifier:
        app_root = Path(factory_kwargs["app_root"])
        assert app_root.exists()
        assert factory_kwargs["proxy"] == "socks5://localhost:9150"
        assert Path(factory_kwargs["env"]["PROTON_CLI_CHROME_DATA_ROOT"]).is_relative_to(
            app_root
        )
        await verifier.complete(
            RecoveryEmailChallenge(
                email="recovery@proton.me",
                requested_at=1.0,
                timeout_seconds=10,
            )
        )

    assert not app_root.exists()
    assert client.close_calls == [True]
    assert ("click", "e22") in browser.calls
    assert ("click", "f2e6") in browser.calls
    assert ("select_tab", 1) in browser.calls
    assert {name for name, _payload in browser.calls} <= {
        "snapshot",
        "press",
        "click",
        "select_tab",
    }


@pytest.mark.asyncio
async def test_proton_verifier_marks_old_conversation_read_and_clicks_only_new_link() -> None:
    old_unread = """
- region "4 messages in conversation Verify your recovery email" [ref=e10] [cursor=pointer]:
  - checkbox [ref=e9]
  - generic [ref=e11]: Unread email
  - generic "no-reply@verify.proton.me" [ref=e12]: Proton
  - heading "4 messages in conversation Verify your recovery email" [ref=e13]
"""
    old_selected = old_unread + '\n- button "Mark as read" [ref=e3]'
    old_conversation = """
- link "Inbox 3 unread conversations" [ref=e2] [cursor=pointer]
- article [ref=e30]:
  - iframe [ref=f1e5]:
    - link "Verify email" [ref=f1e6]
"""
    old_read = old_unread.replace("- generic [ref=e11]: Unread email\n", "")
    new_unread = old_unread.replace("4 messages", "5 messages").replace(
        "[ref=e10]", "[ref=e20]"
    ).replace("[ref=e13]", "[ref=e23]")
    new_conversation = """
- link "Inbox 3 unread conversations" [ref=e2] [cursor=pointer]
- article [ref=e30]:
  - generic [ref=e31] [cursor=pointer]
- article [active] [ref=e40]:
  - iframe [ref=f2e5]:
    - link "Verify email" [ref=f2e6]
"""
    success = """
- heading "Email verified" [level=1] [ref=e50]
- generic [ref=e51]: Thank you for verifying your email address.
"""

    class RecipientBrowser:
        def __init__(self) -> None:
            self.page = "old_unread"
            self.prepared = False
            self.link_clicked = False
            self.selected = False
            self.calls: list[tuple[str, Any]] = []

        async def snapshot(self, session: str) -> str:
            self.calls.append(("snapshot", session))
            if self.selected:
                return success
            return {
                "old_unread": old_unread,
                "old_unread_changed": old_unread + "\n- status: refreshed",
                "old_selected": old_selected,
                "old_conversation": old_conversation,
                "old_read": old_read,
                "new_unread": new_unread,
                "new_conversation": new_conversation,
            }[self.page]

        async def press(self, session: str, key: str) -> str:
            self.calls.append(("press", key))
            self.page = "new_unread" if self.prepared else "old_unread_changed"
            return "pressed"

        async def click(self, session: str, ref: str) -> str:
            self.calls.append(("click", ref))
            if ref == "e10":
                self.page = "old_conversation"
            elif ref == "e9":
                self.page = "old_selected"
            elif ref == "e3" or ref == "e2":
                self.prepared = True
                self.page = "old_read"
            elif ref == "e23":
                self.page = "new_conversation"
            elif ref in {"f1e6", "f2e6"}:
                self.link_clicked = True
            return "clicked"

        async def tab_urls(self, session: str) -> list[str]:
            return (
                ["https://mail.proton.me/u/1/inbox"]
                if not self.link_clicked
                else ["https://account.proton.me/verify-email"]
            )

        async def select_tab(self, session: str, index: int) -> str:
            self.calls.append(("select_tab", index))
            self.selected = True
            return "selected"

    class Runtime:
        def __init__(self, browser: RecipientBrowser) -> None:
            self.browser = browser

        async def sleep(self, _seconds: float) -> None:
            return None

    class Client:
        def __init__(self, browser: RecipientBrowser) -> None:
            self.runtime = Runtime(browser)

        async def login(self, **_kwargs: Any) -> None:
            return None

        async def _get_runtime(self) -> Runtime:
            return self.runtime

        async def close(self, *, force: bool) -> None:
            assert force is True

    browser = RecipientBrowser()
    client = Client(browser)
    verifier = ProtonMailboxVerifier(
        email="recovery@proton.me",
        password="recovery-secret",
        client_factory=lambda **_kwargs: client,
        timeout_seconds=10,
    )

    async with verifier:
        await verifier.complete(
            RecoveryEmailChallenge(
                email="recovery@proton.me",
                requested_at=1.0,
                timeout_seconds=10,
            )
        )

    clicked = [payload for name, payload in browser.calls if name == "click"]
    assert clicked[:2] == ["e9", "e3"]
    assert "e10" not in clicked
    assert "e23" in clicked
    assert "e20" not in clicked
    assert "e31" not in clicked
    assert "f2e6" in clicked
    assert "f1e6" not in clicked
    assert ("select_tab", 0) in browser.calls


@pytest.mark.asyncio
async def test_proton_mailbox_cleanup_retries_a_transient_windows_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "operation"
    app_root.mkdir()
    (app_root / "metrics.pma").write_text("metrics", encoding="utf-8")
    real_rmtree = shutil.rmtree
    calls = 0

    def flaky_rmtree(path: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("Chrome has not released BrowserMetrics yet")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        "proton_cli.workflows.recovery_email.shutil.rmtree", flaky_rmtree
    )
    verifier = ProtonMailboxVerifier(email="recovery@proton.me", password="secret")
    verifier._app_root = app_root

    await verifier._cleanup()

    assert calls == 2
    assert not app_root.exists()


@pytest.mark.asyncio
async def test_cli_interactive_recovery_command_has_stable_json_shape(
    tmp_path: Path,
) -> None:
    await SessionStore(tmp_path).save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(tmp_path / "profile"),
            debug_endpoint="http://127.0.0.1:9333",
            browser_process_id=4242,
        )
    )
    browser = FakeBrowser(
        [
            snapshot.replace("recovery@proton.me", "recovery@example.test")
            for snapshot in [
                INBOX,
                DRAWER,
                DASHBOARD,
                RECOVERY,
                UNVERIFIED,
                UNVERIFIED_DIALOG,
                SENT,
                VERIFIED,
            ]
        ]
    )
    prompts: list[str] = []

    async def callback(prompt: str) -> None:
        prompts.append(prompt)

    async def no_sleep(_seconds: float) -> None:
        return None

    result = await run_cli(
        RunCliOptions(
            argv=[
                "--json",
                "recovery-email",
                "add",
                "--email",
                "recovery@example.test",
                "--verification",
                "interactive",
                "--timeout-seconds",
                "10",
            ],
            app_root=tmp_path,
            cwd=tmp_path,
            env={},
            browser=browser,  # type: ignore[arg-type]
            sleep=no_sleep,
            tcp_probe=lambda _host, _port: _true(),
            interactive_callback=callback,
        )
    )

    assert '"ok": true' in result.stdout
    assert '"command": "recovery-email add"' in result.stdout
    assert '"status": "verified"' in result.stdout
    assert prompts == [INTERACTIVE_PROMPT]


async def _true() -> bool:
    return True
