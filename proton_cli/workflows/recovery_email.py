"""Visible-input-only recovery-email workflows and verifier contracts."""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol

from proton_cli.errors import CliError
from proton_cli.snapshot import (
    find_collapsed_message_header_refs,
    find_latest_recovery_verify_link_ref,
    find_mark_as_read_ref,
    find_recovery_control_ref,
    find_recovery_message_checkbox_ref,
    find_recovery_message_open_ref,
    find_recovery_message_row_ref,
    is_recovery_email_sent_visible,
    is_recovery_password_dialog_visible,
    is_recovery_recipient_success_visible,
    recovery_email_state,
)
from proton_cli.types import RecoveryEmailOutcome, RecoveryVerificationMode

INTERACTIVE_PROMPT = (
    'Open the new recovery email, click "Verify email", '
    "then press Enter when verification is complete."
)


@dataclass(frozen=True)
class RecoveryEmailChallenge:
    email: str
    requested_at: float
    timeout_seconds: int


class RecoveryEmailVerifier(Protocol):
    async def __aenter__(self) -> RecoveryEmailVerifier: ...

    async def complete(self, challenge: RecoveryEmailChallenge) -> None: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class RecoveryBrowser(Protocol):
    def supports_recovery_email(self) -> bool: ...

    def work_tab(
        self, *, source_session: str, work_session: str
    ) -> AbstractAsyncContextManager[str]: ...

    async def snapshot(self, session: str) -> str: ...

    async def click(self, session: str, target: str) -> str: ...

    async def type_text(self, session: str, text: str) -> str: ...

    async def press(self, session: str, key: str) -> str: ...

    async def key_down(self, session: str, key: str) -> str: ...

    async def key_up(self, session: str, key: str) -> str: ...


class InteractiveRecoveryEmailVerifier:
    def __init__(self, callback: Callable[[str], Awaitable[None]]) -> None:
        self._callback = callback

    async def __aenter__(self) -> InteractiveRecoveryEmailVerifier:
        return self

    async def complete(self, challenge: RecoveryEmailChallenge) -> None:
        del challenge
        await self._callback(INTERACTIVE_PROMPT)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback


class ProtonMailboxVerifier:
    """Complete verification in an isolated, operation-scoped Proton browser."""

    def __init__(
        self,
        *,
        email: str,
        password: str,
        proxy: str = "socks5://localhost:9150",
        timeout_seconds: int = 180,
        client_factory: Callable[..., object] | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._proxy = proxy
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        self._client: object | None = None
        self._runtime: object | None = None
        self._app_root: Path | None = None
        self._session = f"recovery-{uuid.uuid4().hex}"
        self._baseline = ""

    async def __aenter__(self) -> ProtonMailboxVerifier:
        self._app_root = Path(tempfile.mkdtemp(prefix="proton-cli-recovery-")).resolve()
        env = dict(os.environ)
        env["PROTON_CLI_APP_ROOT"] = str(self._app_root)
        env["PROTON_CLI_CHROME_DATA_ROOT"] = str(self._app_root / "chrome")
        factory = self._client_factory
        if factory is None:
            from proton_cli.api import ProtonClient

            factory = ProtonClient
        try:
            client = factory(
                session=self._session,
                app_root=self._app_root,
                env=env,
                proxy=self._proxy,
            )
            self._client = client
            await client.login(email=self._email, password=self._password, mode="headed")  # type: ignore[attr-defined]
            runtime = await client._get_runtime()  # type: ignore[attr-defined]
            self._runtime = runtime
            self._baseline = await self._prepare_recipient_inbox()
            return self
        except BaseException:
            with suppress(BaseException):
                await self._cleanup()
            raise

    async def complete(self, challenge: RecoveryEmailChallenge) -> None:
        if self._runtime is None:
            raise CliError(
                "The recovery mailbox verifier was not prepared.",
                code="RECOVERY_EMAIL_VERIFICATION_FAILED",
            )
        runtime = self._runtime
        browser = runtime.browser  # type: ignore[attr-defined]
        attempts = max(1, math.ceil(self._timeout_seconds / 2))
        row_ref: str | None = None
        snapshot = ""
        for _ in range(attempts):
            await browser.press(self._session, "Control+R")
            await runtime.sleep(2)  # type: ignore[attr-defined]
            snapshot = str(await browser.snapshot(self._session))
            row_ref = find_recovery_message_row_ref(snapshot, require_unread=True)
            if row_ref is not None:
                break
        if row_ref is None:
            raise CliError(
                "A newly unread Proton recovery verification message was not found.",
                code="RECOVERY_EMAIL_VERIFICATION_MESSAGE_NOT_FOUND",
                details={"requested_at": challenge.requested_at},
            )

        open_ref = find_recovery_message_open_ref(snapshot, require_unread=True)
        if open_ref is None:
            raise CliError(
                "The newly unread recovery conversation had no visible subject control.",
                code="RECOVERY_EMAIL_VERIFICATION_MESSAGE_NOT_FOUND",
            )
        await browser.click(self._session, open_ref)
        verify_ref: str | None = None
        for _ in range(attempts):
            snapshot = await self._expand_recovery_conversation()
            verify_ref = find_latest_recovery_verify_link_ref(snapshot)
            if verify_ref is not None:
                break
            await runtime.sleep(2)  # type: ignore[attr-defined]
        if verify_ref is None:
            raise CliError(
                "The visible Verify email control was not found in the recovery message.",
                code="RECOVERY_EMAIL_VERIFICATION_MESSAGE_NOT_FOUND",
            )

        before_urls = await browser.tab_urls(self._session)
        await browser.click(self._session, verify_ref)
        selected = False
        for _ in range(attempts):
            urls = await browser.tab_urls(self._session)
            if len(urls) > len(before_urls):
                await browser.select_tab(self._session, len(urls) - 1)
                selected = True
                break
            changed_index = next(
                (
                    index
                    for index, (before, after) in enumerate(
                        zip(before_urls, urls, strict=False)
                    )
                    if before != after
                ),
                None,
            )
            if changed_index is not None:
                await browser.select_tab(self._session, changed_index)
                selected = True
                break
            await runtime.sleep(2)  # type: ignore[attr-defined]
        if not selected:
            raise CliError(
                "The recovery verification page did not open or navigate.",
                code="RECOVERY_EMAIL_VERIFICATION_FAILED",
            )

        for _ in range(attempts):
            snapshot = await browser.snapshot(self._session)
            if is_recovery_recipient_success_visible(snapshot):
                return
            await runtime.sleep(2)  # type: ignore[attr-defined]
        raise CliError(
            "The recovery mailbox did not show the visible Email verified confirmation.",
            code="RECOVERY_EMAIL_VERIFICATION_TIMEOUT",
        )

    async def _prepare_recipient_inbox(self) -> str:
        if self._runtime is None:
            raise CliError(
                "The recovery mailbox verifier was not prepared.",
                code="RECOVERY_EMAIL_VERIFICATION_FAILED",
            )
        runtime = self._runtime
        browser = runtime.browser  # type: ignore[attr-defined]
        attempts = max(1, math.ceil(min(self._timeout_seconds, 30) / 2))
        snapshot = ""
        for _ in range(attempts):
            snapshot = str(await browser.snapshot(self._session))
            row_ref = find_recovery_message_row_ref(snapshot, require_unread=True)
            if row_ref is None:
                return snapshot
            checkbox_ref = find_recovery_message_checkbox_ref(
                snapshot, require_unread=True
            )
            if checkbox_ref is None:
                raise CliError(
                    "Could not select older unread recovery messages.",
                    code="RECOVERY_EMAIL_VERIFICATION_FAILED",
                )
            await browser.click(self._session, checkbox_ref)

            mark_read_ref: str | None = None
            for _ in range(attempts):
                snapshot = str(await browser.snapshot(self._session))
                mark_read_ref = find_mark_as_read_ref(snapshot)
                if mark_read_ref is not None:
                    break
                await runtime.sleep(2)  # type: ignore[attr-defined]
            if mark_read_ref is None:
                raise CliError(
                    "Could not find the visible Mark as read action for old recovery messages.",
                    code="RECOVERY_EMAIL_VERIFICATION_FAILED",
                )
            await browser.click(self._session, mark_read_ref)
            await runtime.sleep(2)  # type: ignore[attr-defined]
        raise CliError(
            "Could not clear older unread recovery messages before verification.",
            code="RECOVERY_EMAIL_VERIFICATION_FAILED",
        )

    async def _expand_recovery_conversation(self) -> str:
        if self._runtime is None:
            raise CliError(
                "The recovery mailbox verifier was not prepared.",
                code="RECOVERY_EMAIL_VERIFICATION_FAILED",
            )
        runtime = self._runtime
        browser = runtime.browser  # type: ignore[attr-defined]
        snapshot = ""
        for _ in range(3):
            snapshot = str(await browser.snapshot(self._session))
            if find_latest_recovery_verify_link_ref(snapshot) is not None:
                return snapshot
            refs = find_collapsed_message_header_refs(snapshot)
            if not refs:
                return snapshot
            try:
                await browser.click(self._session, refs[-1])
            except Exception:
                return snapshot
            await runtime.sleep(0.4)  # type: ignore[attr-defined]
        return str(await browser.snapshot(self._session))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, traceback
        try:
            await self._cleanup()
        except BaseException:
            if exc is None:
                raise

    async def _cleanup(self) -> None:
        client = self._client
        app_root = self._app_root
        self._client = None
        self._runtime = None
        self._app_root = None
        close_error: BaseException | None = None
        try:
            if client is not None:
                await client.close(force=True)  # type: ignore[attr-defined]
        except BaseException as exc:
            close_error = exc
        finally:
            if app_root is not None and app_root.exists():
                await _remove_operation_root(app_root)
        if close_error is not None:
            raise close_error


async def _remove_operation_root(app_root: Path) -> None:
    attempts = 12
    for attempt in range(attempts):
        try:
            shutil.rmtree(app_root, onerror=_clear_readonly_and_retry)
            return
        except OSError:
            if attempt + 1 == attempts:
                raise
            await asyncio.sleep(0.25)


def _clear_readonly_and_retry(
    function: Callable[[str], object],
    path: str,
    _error: object,
) -> None:
    with suppress(OSError):
        os.chmod(path, stat.S_IWRITE)
    function(path)


async def run_recovery_email_workflow(
    browser: RecoveryBrowser,
    *,
    source_session: str,
    email: str,
    account_password: str | None,
    verifier: RecoveryEmailVerifier,
    verification: RecoveryVerificationMode,
    timeout_seconds: int,
    sleep: Callable[[float], Awaitable[object]],
) -> RecoveryEmailOutcome:
    """Add and verify one address through snapshots, clicks, and keyboard input."""
    if not browser.supports_recovery_email():
        raise CliError(
            "Recovery-email automation is unavailable in this browser backend.",
            code="RECOVERY_EMAIL_UNAVAILABLE",
        )

    work_session = f"{source_session}:recovery-email"
    async with browser.work_tab(
        source_session=source_session,
        work_session=work_session,
    ) as session:
        snapshot = await _open_recovery_email_settings(
            browser,
            session,
            timeout_seconds=min(timeout_seconds, 30),
            sleep=sleep,
        )
        state = recovery_email_state(snapshot, email)
        if state == "same_verified":
            return RecoveryEmailOutcome(
                email=email,
                status="already_verified",
                verification=verification,
                session=source_session,
            )
        if state == "different":
            raise CliError(
                "A different recovery email is already configured.",
                code="RECOVERY_EMAIL_ALREADY_SET",
            )

        if state == "empty":
            field_ref = _require_control(snapshot, "recovery-email")
            await browser.click(session, field_ref)
            await browser.key_down(session, "Control")
            try:
                await browser.press(session, "a")
            finally:
                await browser.key_up(session, "Control")
            await browser.press(session, "Backspace")
            await browser.type_text(session, email)
            snapshot, add_ref = await _wait_for_control(
                browser,
                session,
                "add-and-verify",
                timeout_seconds=min(timeout_seconds, 10),
                sleep=sleep,
            )
            await browser.click(session, add_ref)
            snapshot, state = await _wait_after_add(
                browser,
                session,
                email,
                timeout_seconds=min(timeout_seconds, 20),
                sleep=sleep,
            )

            if is_recovery_password_dialog_visible(snapshot):
                if account_password is None:
                    raise CliError(
                        "The source account password is required to save this recovery email.",
                        code="ACCOUNT_PASSWORD_REQUIRED",
                    )
                password_ref = _require_control(snapshot, "password")
                authenticate_ref = _require_control(snapshot, "authenticate")
                await browser.click(session, password_ref)
                await browser.type_text(session, account_password)
                # Do not snapshot or read the field after typing the password.
                await browser.click(session, authenticate_ref)
                snapshot = await browser.snapshot(session)
                state = recovery_email_state(snapshot, email)

        if state not in {"same_unverified", "same_verified"}:
            snapshot, state = await _wait_for_recovery_state(
                browser,
                session,
                email,
                timeout_seconds=min(timeout_seconds, 20),
                sleep=sleep,
            )
        if state == "same_verified":
            return RecoveryEmailOutcome(
                email=email,
                status="already_verified",
                verification=verification,
                session=source_session,
            )
        if state != "same_unverified":
            raise CliError(
                "The recovery email controls did not reach an unverified state.",
                code="RECOVERY_EMAIL_CONTROL_NOT_FOUND",
            )

        async with verifier:
            requested_at = time.time()
            verify_with_email_ref = find_recovery_control_ref(
                snapshot, "verify-with-email"
            )
            if verify_with_email_ref is None:
                await browser.click(session, _require_control(snapshot, "verify"))
                snapshot = await browser.snapshot(session)
                verify_with_email_ref = _require_control(
                    snapshot, "verify-with-email"
                )
            await browser.click(session, verify_with_email_ref)
            snapshot = await _wait_for_sent_notification(
                browser,
                session,
                email,
                timeout_seconds=min(timeout_seconds, 30),
                sleep=sleep,
            )
            del snapshot
            await verifier.complete(
                RecoveryEmailChallenge(
                    email=email,
                    requested_at=requested_at,
                    timeout_seconds=timeout_seconds,
                )
            )

        attempts = max(1, math.ceil(timeout_seconds / 2))
        for _ in range(attempts):
            await browser.press(session, "Control+R")
            await sleep(2)
            snapshot = await browser.snapshot(session)
            if recovery_email_state(snapshot, email) == "same_verified":
                return RecoveryEmailOutcome(
                    email=email,
                    status="verified",
                    verification=verification,
                    session=source_session,
                )

    raise CliError(
        "Timed out waiting for the source settings page to show the recovery email as verified.",
        code="RECOVERY_EMAIL_VERIFICATION_TIMEOUT",
        details={"state": "configured_unverified"},
    )


async def _open_recovery_email_settings(
    browser: RecoveryBrowser,
    session: str,
    *,
    timeout_seconds: int,
    sleep: Callable[[float], Awaitable[object]],
) -> str:
    snapshot = await browser.snapshot(session)
    for control in (
        "toggle-settings",
        "all-settings",
        "recovery",
        "email-verification",
    ):
        ref = find_recovery_control_ref(snapshot, control)
        for _ in range(max(1, math.ceil(timeout_seconds))):
            if ref is not None:
                break
            await sleep(1)
            snapshot = await browser.snapshot(session)
            ref = find_recovery_control_ref(snapshot, control)
        if ref is None:
            raise CliError(
                f"Could not find the visible recovery-email control: {control}.",
                code="RECOVERY_EMAIL_CONTROL_NOT_FOUND",
            )
        await browser.click(session, ref)
        snapshot = await browser.snapshot(session)
    return snapshot


async def _wait_for_recovery_state(
    browser: RecoveryBrowser,
    session: str,
    email: str,
    *,
    timeout_seconds: int,
    sleep: Callable[[float], Awaitable[object]],
) -> tuple[
    str,
    Literal["empty", "same_verified", "same_unverified", "different", "unknown"],
]:
    snapshot = ""
    state: Literal[
        "empty", "same_verified", "same_unverified", "different", "unknown"
    ] = "unknown"
    for _ in range(max(1, math.ceil(timeout_seconds))):
        await sleep(1)
        snapshot = await browser.snapshot(session)
        state = recovery_email_state(snapshot, email)
        if state in {"same_unverified", "same_verified", "different"}:
            return snapshot, state
    return snapshot, state


async def _wait_after_add(
    browser: RecoveryBrowser,
    session: str,
    email: str,
    *,
    timeout_seconds: int,
    sleep: Callable[[float], Awaitable[object]],
) -> tuple[
    str,
    Literal["empty", "same_verified", "same_unverified", "different", "unknown"],
]:
    snapshot = ""
    state: Literal[
        "empty", "same_verified", "same_unverified", "different", "unknown"
    ] = "unknown"
    attempts = max(1, math.ceil(timeout_seconds))
    for attempt in range(attempts):
        snapshot = await browser.snapshot(session)
        state = recovery_email_state(snapshot, email)
        if is_recovery_password_dialog_visible(snapshot):
            return snapshot, state
        if state in {"same_unverified", "same_verified", "different"}:
            return snapshot, state
        if attempt + 1 < attempts:
            await sleep(1)
    return snapshot, state


async def _wait_for_control(
    browser: RecoveryBrowser,
    session: str,
    control: str,
    *,
    timeout_seconds: int,
    sleep: Callable[[float], Awaitable[object]],
) -> tuple[str, str]:
    snapshot = ""
    for attempt in range(max(1, math.ceil(timeout_seconds))):
        snapshot = await browser.snapshot(session)
        ref = find_recovery_control_ref(snapshot, control)
        if ref is not None:
            return snapshot, ref
        if attempt + 1 < max(1, math.ceil(timeout_seconds)):
            await sleep(1)
    raise CliError(
        f"Could not find the visible recovery-email control: {control}.",
        code="RECOVERY_EMAIL_CONTROL_NOT_FOUND",
    )


async def _wait_for_sent_notification(
    browser: RecoveryBrowser,
    session: str,
    email: str,
    *,
    timeout_seconds: int,
    sleep: Callable[[float], Awaitable[object]],
) -> str:
    for _ in range(max(1, math.ceil(timeout_seconds))):
        snapshot = await browser.snapshot(session)
        if is_recovery_email_sent_visible(snapshot, email):
            return snapshot
        await sleep(1)
    raise CliError(
        "Proton did not visibly confirm that the recovery verification email was sent.",
        code="RECOVERY_EMAIL_VERIFICATION_FAILED",
    )


def _require_control(snapshot: str, control: str) -> str:
    ref = find_recovery_control_ref(snapshot, control)
    if ref is None:
        raise CliError(
            f"Could not find the visible recovery-email control: {control}.",
            code="RECOVERY_EMAIL_CONTROL_NOT_FOUND",
        )
    return ref
