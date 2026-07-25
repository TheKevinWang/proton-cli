"""Workflow orchestration for proton-cli."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from proton_cli.browser import Browser as BrowserFacade
from proton_cli.errors import CliError
from proton_cli.output import (
    format_inbox_text,
    format_read_text,
    format_status_text,
    help_text,
    json_output,
    serialize_read_message,
)
from proton_cli.parser import parse_argv
from proton_cli.paths import default_app_root, ensure_private_dir, output_dir, profile_dir
from proton_cli.session import SessionState, SessionStore, serialize_session_state
from proton_cli.snapshot import (
    find_composer_body_editor_ref,
    find_first_ref,
    find_recipient_field_ref,
    find_send_confirmation_ref,
    find_welcome_button_ref,
    has_login_password_field,
    is_composer_visible,
    is_inbox_rows_visible,
    is_login_form_visible,
    is_mailbox_visible,
    is_manual_challenge_visible,
    is_search_dialog_visible,
    is_send_in_progress,
    is_welcome_dialog_visible,
    parse_inbox_snapshot,
    parse_read_message_snapshot,
)
from proton_cli.types import (
    BrowserMode,
    CloseOutcome,
    GlobalOptions,
    InboxCommand,
    InboxRow,
    LoginCommand,
    LoginOutcome,
    ParsedCommand,
    ParsedInvocation,
    ReadMessage,
    RecoveryEmailAddCommand,
    RecoveryEmailOutcome,
    RecoveryVerificationMode,
    RecoveryVerificationSelection,
    SendCommand,
)
from proton_cli.validation import (
    LoginPasswordInput,
    ResolvedSendInputs,
    resolve_login_password,
    resolve_send_inputs,
)
from proton_cli.workflows.recovery_email import (
    InteractiveRecoveryEmailVerifier,
    ProtonMailboxVerifier,
    RecoveryEmailVerifier,
    run_recovery_email_workflow,
)

LOGIN_URL = "https://account.proton.me/mail"
INBOX_URL = "https://mail.proton.me/u/0/inbox"
SENT_URL = "https://mail.proton.me/u/0/all-sent"
SEND_CONFIRMATION_TIMEOUT_MS = 120_000
SEND_CLICK_SETTLE_MS = 10_000
_PROTON_RECOVERY_DOMAINS = {"proton.me", "protonmail.com", "protonmail.ch", "pm.me"}


async def _default_interactive_callback(prompt: str) -> None:
    print(prompt, file=sys.stderr)
    await asyncio.to_thread(sys.stdin.readline)


@dataclass
class Runtime:
    cwd: Path
    env: Mapping[str, str | None]
    app_root: Path
    store: SessionStore
    browser: BrowserFacade
    verbose: bool = False
    logs: list[str] = field(default_factory=list)
    sleep: Callable[[float], Awaitable[Any]] = field(default_factory=lambda: asyncio.sleep)
    tcp_probe: Callable[[str, int], Awaitable[bool]] = field(
        default_factory=lambda: _tcp_port_open
    )
    borrowed_sessions: dict[str, SessionState] = field(default_factory=dict)
    interactive_callback: Callable[[str], Awaitable[None]] = field(
        default_factory=lambda: _default_interactive_callback
    )


@dataclass
class RunCliOptions:
    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str | None] | None = None
    app_root: Path | None = None
    browser: BrowserFacade | None = None
    sleep: Callable[[float], Any] | None = None
    tcp_probe: Callable[[str, int], Awaitable[bool]] | None = None
    interactive_callback: Callable[[str], Awaitable[None]] | None = None


@dataclass
class RunCliResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _ManagedProfile:
    path: str
    owned: bool


async def run_cli(options: RunCliOptions) -> RunCliResult:
    env = options.env if options.env is not None else dict(os.environ)
    app_root = options.app_root if options.app_root is not None else default_app_root(env)
    runtime = Runtime(
        cwd=options.cwd if options.cwd is not None else Path.cwd(),
        env=env,
        app_root=app_root,
        store=SessionStore(app_root),
        browser=options.browser if options.browser is not None else BrowserFacade(),
        sleep=options.sleep if options.sleep is not None else asyncio.sleep,
        tcp_probe=options.tcp_probe if options.tcp_probe is not None else _tcp_port_open,
        interactive_callback=(
            options.interactive_callback
            if options.interactive_callback is not None
            else _default_interactive_callback
        ),
    )

    parsed = parse_argv(options.argv)
    runtime.verbose = parsed.global_options.verbose
    stdout = await execute(parsed, runtime)
    return RunCliResult(exit_code=0, stdout=stdout, stderr="\n".join(runtime.logs))


async def _browser_status_core(
    global_options: GlobalOptions, runtime: Runtime
) -> SessionState | None:
    borrowed = runtime.borrowed_sessions.get(global_options.session)
    if borrowed is not None:
        return borrowed
    return await runtime.store.load(global_options.session)


async def execute(parsed: ParsedInvocation, runtime: Runtime) -> str:
    command = parsed.command
    if command.kind == "help":
        return help_text()
    if command.kind == "browser-status":
        state = await _browser_status_core(parsed.global_options, runtime)
        return (
            json_output(
                {"ok": True, "session": serialize_session_state(state) if state else None}
            )
            if parsed.global_options.json
            else format_status_text(state)
        )
    if command.kind == "browser-close":
        return await close_browser(parsed.global_options, command.force, runtime)

    async def action() -> str:
        if command.kind == "login":
            return await login(parsed.global_options, command, runtime)
        if command.kind == "inbox":
            return await inbox_command(parsed.global_options, command, runtime)
        if command.kind == "refresh":
            return await refresh(parsed.global_options, command.limit, runtime)
        if command.kind == "read":
            return await read_message(parsed.global_options, command.handle, runtime)
        if command.kind == "send":
            return await send_message(parsed.global_options, command, runtime)
        if command.kind == "recovery-email-add":
            return await recovery_email_command(
                parsed.global_options, command, runtime
            )
        raise CliError("Unsupported command.", code="UNSUPPORTED_COMMAND")

    if parsed.global_options.trace:
        await _validate_before_browser(command, runtime)
        await _ensure_session(
            parsed.global_options,
            runtime,
            command.mode if command.kind == "login" else None,
        )
        async with runtime.browser.with_trace(
            parsed.global_options.session, parsed.global_options.trace
        ):
            result = await action()
        runtime.logs.append(f"Trace: {Path(runtime.cwd / parsed.global_options.trace).resolve()}")
        return result
    return await action()


async def _validate_before_browser(command: ParsedCommand, runtime: Runtime) -> None:
    if command.kind == "login":
        resolve_login_password(
            LoginPasswordInput(password=command.password, password_env=command.password_env),
            runtime.env,
        )
    if command.kind == "send":
        await resolve_send_inputs(command, runtime.cwd)
    if command.kind == "recovery-email-add":
        _resolve_optional_secret(
            literal=None,
            env_name=command.password_env,
            runtime=runtime,
            label="source account password",
        )


async def _login_core(
    global_options: GlobalOptions, command: LoginCommand, runtime: Runtime
) -> LoginOutcome:
    password = resolve_login_password(
        LoginPasswordInput(password=command.password, password_env=command.password_env),
        runtime.env,
    )
    state = await _ensure_session(
        global_options,
        runtime,
        command.mode,
        replace_external_with_managed=True,
    )
    await _goto_with_retry(runtime, global_options.session, LOGIN_URL)

    snapshot = await _wait_for_snapshot(
        runtime,
        global_options.session,
        lambda candidate: is_mailbox_visible(candidate)
        or is_login_form_visible(candidate)
        or is_manual_challenge_visible(candidate),
        "Proton login form or mailbox shell",
        120_000,
    )
    if not is_mailbox_visible(snapshot):
        if is_login_form_visible(snapshot):
            await _submit_login_form(global_options, command, runtime, password.value)
        snapshot = await _wait_for_mailbox_or_challenge(
            global_options, command.mode, runtime, state.profile_dir
        )

    await _dismiss_welcome_dialog(global_options.session, runtime)

    snapshot = await runtime.browser.snapshot(global_options.session)
    if not is_mailbox_visible(snapshot):
        raise CliError(
            "Timed out waiting for Proton Mail inbox to become visible.",
            code="MAILBOX_NOT_VISIBLE",
            details={"cdp_endpoint": state.debug_endpoint or state.attached_endpoint},
        )

    state = await _refresh_managed_browser_state(
        state, global_options.session, command.mode, runtime
    )

    await _save_success(
        runtime,
        SessionState(
            name=state.name,
            managed=state.managed,
            mode=command.mode,
            profile_dir=state.profile_dir,
            profile_owned=state.profile_owned,
            browser_process_id=state.browser_process_id,
            debug_endpoint=state.debug_endpoint,
            attached_endpoint=state.attached_endpoint,
            account_email=command.email,
        ),
    )

    return LoginOutcome(account=command.email, session=global_options.session)


async def login(global_options: GlobalOptions, command: LoginCommand, runtime: Runtime) -> str:
    outcome = await _login_core(global_options, command, runtime)
    if global_options.json:
        return json_output(
            {"ok": True, "command": "login", "account": outcome.account, "session": outcome.session}
        )
    return f"Logged in as {outcome.account}\nBrowser session: {outcome.session}"


async def _refresh_core(global_options: GlobalOptions, limit: int, runtime: Runtime) -> list[InboxRow]:
    state = await _ensure_session(global_options, runtime)
    await _goto_with_retry(runtime, global_options.session, INBOX_URL)
    await _dismiss_welcome_dialog(global_options.session, runtime)
    before = await runtime.browser.snapshot(global_options.session)
    refresh_ref = find_first_ref(
        before, [re.compile(r'button "Refresh"', re.I), re.compile(r"refresh", re.I)]
    )
    if refresh_ref:
        try:
            await runtime.browser.click(global_options.session, refresh_ref)
        except Exception as exc:  # noqa: BLE001
            _log(runtime, f"Inbox refresh click failed (stale ref?): {exc}; continuing.")
    else:
        _log(runtime, "No visible Inbox refresh control found; re-reading the current inbox snapshot.")
    try:
        snapshot = await _wait_for_snapshot(
            runtime, global_options.session, is_inbox_rows_visible, "inbox rows after refresh", 6_000
        )
    except CliError:
        snapshot = await runtime.browser.snapshot(global_options.session)
    rows = parse_inbox_snapshot(snapshot, limit)
    await _save_success(runtime, SessionState(**{**state.__dict__, "last_inbox": rows}))
    return rows


async def refresh(global_options: GlobalOptions, limit: int, runtime: Runtime) -> str:
    rows = await _refresh_core(global_options, limit, runtime)
    return (
        json_output({"ok": True, "command": "refresh", "rows": rows})
        if global_options.json
        else format_inbox_text(rows)
    )


async def _inbox_core(
    global_options: GlobalOptions, command: InboxCommand, runtime: Runtime
) -> list[InboxRow]:
    state = await _ensure_session(global_options, runtime)
    await _goto_with_retry(runtime, global_options.session, _mailbox_url(command.folder))
    await _dismiss_welcome_dialog(global_options.session, runtime)
    # Wait for email row regions to render.  For a genuinely empty folder the
    # predicate never fires, so we cap the wait at 6 s and fall back to a fresh
    # snapshot (which will also have no rows, giving the correct empty result).
    try:
        snapshot = await _wait_for_snapshot(
            runtime, global_options.session, is_inbox_rows_visible, "inbox rows", 6_000
        )
    except CliError:
        snapshot = await runtime.browser.snapshot(global_options.session)
    rows = parse_inbox_snapshot(snapshot, command.limit)
    await _save_success(runtime, SessionState(**{**state.__dict__, "last_inbox": rows}))
    return rows


async def inbox_command(global_options: GlobalOptions, command: InboxCommand, runtime: Runtime) -> str:
    rows = await _inbox_core(global_options, command, runtime)
    return (
        json_output({"ok": True, "command": "inbox", "folder": command.folder, "rows": rows})
        if global_options.json
        else format_inbox_text(rows)
    )


async def _read_core(
    global_options: GlobalOptions, handle: str, runtime: Runtime
) -> ReadMessage:
    state = await _ensure_session(global_options, runtime)
    await _dismiss_welcome_dialog(global_options.session, runtime)
    rows = state.last_inbox or []
    row = next(
        (candidate for candidate in rows if candidate.handle == handle or str(candidate.row) == handle),
        None,
    )
    if row is None:
        raise CliError(
            "Unknown message handle. Run proton-cli inbox again and use one of the returned row numbers.",
            code="UNKNOWN_MESSAGE_HANDLE",
            exit_code=2,
        )
    await _open_inbox_row(global_options, runtime, row)
    await runtime.sleep(1.2)
    snapshot = await _expand_conversation_messages(global_options.session, runtime)
    # The message body renders in an iframe; the multi-frame snapshot descends
    # into it, so the standard parser sees the body text directly.
    message = parse_read_message_snapshot(snapshot)
    latest = await runtime.store.load(global_options.session) or state
    await _save_success(runtime, latest)
    return message


_ARTICLE_LINE_RE = re.compile(r"^(?P<indent>\s*)- article(?P<active> \[active\])?(?: \[ref=e\d+\])?:?\s*$")
_COLLAPSED_HEADER_RE = re.compile(r"^\s*- generic \[ref=(?P<ref>e\d+)\] \[cursor=pointer\]")


def _collapsed_message_header_refs(snapshot: str) -> list[str]:
    """Return refs of collapsed conversation message headers.

    Proton threads related messages into one conversation view. Collapsed
    messages render as an ``article`` whose only interactive child is a
    cursor-pointer generic header; their bodies (and any links inside them)
    are absent from the snapshot. Expanded messages are marked
    ``article [active]`` and must not be clicked (that would collapse them).
    """
    refs: list[str] = []
    article_indent: int | None = None
    article_active = False
    for line in snapshot.splitlines():
        article_match = _ARTICLE_LINE_RE.match(line)
        if article_match:
            article_indent = len(article_match.group("indent"))
            article_active = bool(article_match.group("active"))
            continue
        if article_indent is None:
            continue
        stripped = line.strip()
        if stripped and len(line) - len(line.lstrip()) <= article_indent:
            article_indent = None
            continue
        if article_active:
            continue
        header_match = _COLLAPSED_HEADER_RE.match(line)
        if header_match:
            refs.append(header_match.group("ref"))
            article_indent = None
    return refs


async def _expand_conversation_messages(session: str, runtime: Runtime) -> str:
    """Expand collapsed conversation messages and return a fresh snapshot.

    A threaded conversation hides collapsed message bodies, so a read must
    expand each collapsed message before parsing, otherwise content such as
    verification links in the latest message is silently missing.
    """
    snapshot = await runtime.browser.snapshot(session)
    for _pass in range(3):
        refs = _collapsed_message_header_refs(snapshot)
        if not refs:
            return snapshot
        for ref in refs:
            try:
                await runtime.browser.click(session, ref)
            except Exception as exc:  # noqa: BLE001
                _log(runtime, f"Conversation message expand failed (stale ref?): {exc}; continuing.")
            await runtime.sleep(0.4)
        snapshot = await runtime.browser.snapshot(session)
    return snapshot


async def _open_inbox_row(
    global_options: GlobalOptions,
    runtime: Runtime,
    row: InboxRow,
) -> None:
    """Click an inbox row, re-resolving its target when the saved ref is stale.

    The inbox re-renders whenever new mail arrives, which detaches refs saved
    from an earlier inbox snapshot. On a stale-ref click failure the row is
    matched again (by subject and sender) against a fresh snapshot.
    """
    last_error: BaseException | None = None
    for _attempt in range(2):
        if not row.target:
            raise CliError(
                "The saved message handle does not include a browser target. Run proton-cli inbox again.",
                code="STALE_MESSAGE_HANDLE",
                exit_code=2,
            )
        try:
            await runtime.browser.click(global_options.session, row.target)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            _log(
                runtime,
                f"Message row click failed ({exc}); re-resolving from a fresh inbox snapshot.",
            )
            refreshed = await _refresh_inbox_rows(global_options, runtime)
            row = _match_inbox_row(refreshed, row)
    raise CliError(
        f"Could not open message {row.subject!r}: {last_error}",
        code="MESSAGE_OPEN_FAILED",
    ) from last_error


async def _refresh_inbox_rows(global_options: GlobalOptions, runtime: Runtime) -> list[InboxRow]:
    """Re-parse the current inbox snapshot and persist it as the latest inbox."""
    state = await _ensure_session(global_options, runtime)
    snapshot = await runtime.browser.snapshot(global_options.session)
    rows = parse_inbox_snapshot(snapshot, 50)
    await _save_success(runtime, SessionState(**{**state.__dict__, "last_inbox": rows}))
    return rows


def _match_inbox_row(rows: list[InboxRow], wanted: InboxRow) -> InboxRow:
    def key(candidate: InboxRow) -> tuple[str, str]:
        return (
            candidate.subject.strip().lower(),
            (candidate.from_email or candidate.from_display).strip().lower(),
        )

    wanted_key = key(wanted)
    for candidate in rows:
        if key(candidate) == wanted_key:
            return candidate
    raise CliError(
        f"Message {wanted.subject!r} is no longer visible in the inbox.",
        code="STALE_MESSAGE_HANDLE",
        exit_code=2,
    )


async def read_message(global_options: GlobalOptions, handle: str, runtime: Runtime) -> str:
    message = await _read_core(global_options, handle, runtime)
    return (
        json_output({"ok": True, "command": "read", "message": serialize_read_message(message)})
        if global_options.json
        else format_read_text(message)
    )


async def _send_core(
    global_options: GlobalOptions, command: SendCommand, runtime: Runtime
) -> None:
    inputs: ResolvedSendInputs = await resolve_send_inputs(command, runtime.cwd)
    state = await _ensure_session(global_options, runtime)
    await _goto_with_retry(runtime, global_options.session, INBOX_URL)
    await _dismiss_welcome_dialog(global_options.session, runtime)
    await _wait_for_snapshot(
        runtime, global_options.session, is_mailbox_visible, "mailbox shell before compose"
    )
    await _click_visible(
        global_options.session,
        runtime,
        [
            re.compile(r'button "Compose"', re.I),
            re.compile(r'button "New message"', re.I),
            re.compile(r"\bCompose\b", re.I),
        ],
        "Compose button",
    )
    await runtime.sleep(1.0)
    await _fill_recipients(global_options.session, runtime, "to", inputs.to)
    if inputs.cc:
        await _reveal_recipient_field(global_options.session, runtime, "Cc")
        await _fill_recipients(global_options.session, runtime, "cc", inputs.cc)
    if inputs.bcc:
        await _reveal_recipient_field(global_options.session, runtime, "Bcc")
        await _fill_recipients(global_options.session, runtime, "bcc", inputs.bcc)
    await _fill_subject(global_options.session, runtime, inputs.subject)
    await _fill_body(global_options.session, runtime, inputs.body)
    await _attach_files(global_options.session, runtime, inputs.attachments)
    await _send_current_composer(global_options.session, runtime, inputs.subject)
    await _save_success(runtime, state)


async def send_message(global_options: GlobalOptions, command: SendCommand, runtime: Runtime) -> str:
    await _send_core(global_options, command, runtime)
    return (
        json_output({"ok": True, "command": "send", "session": global_options.session})
        if global_options.json
        else "Message sent."
    )


async def _recovery_email_core(
    global_options: GlobalOptions,
    *,
    email: str,
    account_password: str | None,
    account_password_env: str | None,
    verifier: RecoveryEmailVerifier | None,
    verification: RecoveryVerificationSelection,
    recovery_password: str | None,
    recovery_password_env: str | None,
    recovery_proxy: str,
    timeout_seconds: int,
    runtime: Runtime,
) -> RecoveryEmailOutcome:
    if not runtime.browser.supports_recovery_email():
        raise CliError(
            "Recovery-email automation is unavailable in this browser backend.",
            code="RECOVERY_EMAIL_UNAVAILABLE",
        )
    if "@" not in email or not email.rpartition("@")[2]:
        raise CliError(
            "A valid recovery email address is required.",
            code="INVALID_EMAIL",
            exit_code=2,
        )
    if timeout_seconds <= 0:
        raise CliError(
            "timeout_seconds must be positive.",
            code="INVALID_NUMBER",
            exit_code=2,
        )
    if not recovery_proxy.startswith("socks5://"):
        raise CliError(
            "recovery_proxy must be a socks5:// URL.",
            code="INVALID_PROXY",
            exit_code=2,
        )

    source_secret = _resolve_optional_secret(
        literal=account_password,
        env_name=account_password_env,
        runtime=runtime,
        label="source account password",
    )
    selected = _select_recovery_verification(verification, email)
    selected_verifier = verifier
    if selected_verifier is None and selected == "proton":
        recovery_secret = _resolve_optional_secret(
            literal=recovery_password,
            env_name=recovery_password_env,
            runtime=runtime,
            label="recovery mailbox password",
        )
        if recovery_secret is None:
            raise CliError(
                "Automatic Proton verification requires recovery mailbox credentials.",
                code="RECOVERY_MAILBOX_CREDENTIALS_REQUIRED",
            )
        selected_verifier = ProtonMailboxVerifier(
            email=email,
            password=recovery_secret,
            proxy=recovery_proxy,
            timeout_seconds=timeout_seconds,
        )
    elif selected_verifier is None:
        selected_verifier = InteractiveRecoveryEmailVerifier(
            runtime.interactive_callback
        )

    await _ensure_session(global_options, runtime)
    return await run_recovery_email_workflow(
        runtime.browser,
        source_session=global_options.session,
        email=email,
        account_password=source_secret,
        verifier=selected_verifier,
        verification=selected,
        timeout_seconds=timeout_seconds,
        sleep=runtime.sleep,
    )


async def recovery_email_command(
    global_options: GlobalOptions,
    command: RecoveryEmailAddCommand,
    runtime: Runtime,
) -> str:
    outcome = await _recovery_email_core(
        global_options,
        email=command.email,
        account_password=None,
        account_password_env=command.password_env,
        verifier=None,
        verification=command.verification,
        recovery_password=None,
        recovery_password_env=command.recovery_password_env,
        recovery_proxy=command.recovery_proxy,
        timeout_seconds=command.timeout_seconds,
        runtime=runtime,
    )
    if global_options.json:
        return json_output(
            {
                "ok": True,
                "command": "recovery-email add",
                "outcome": outcome,
            }
        )
    if outcome.status == "already_verified":
        return f"Recovery email already verified: {outcome.email}"
    return f"Recovery email verified: {outcome.email}"


def _select_recovery_verification(
    requested: RecoveryVerificationSelection, email: str
) -> RecoveryVerificationMode:
    if requested == "proton":
        return "proton"
    if requested == "interactive":
        return "interactive"
    domain = email.rpartition("@")[2].lower()
    return "proton" if domain in _PROTON_RECOVERY_DOMAINS else "interactive"


def _resolve_optional_secret(
    *,
    literal: str | None,
    env_name: str | None,
    runtime: Runtime,
    label: str,
) -> str | None:
    if literal is not None and env_name is not None:
        raise CliError(
            f"Provide the {label} directly or through an environment variable, not both.",
            code="CONFLICTING_PASSWORD_SOURCE",
            exit_code=2,
        )
    if literal is not None:
        return literal
    if env_name is None:
        return None
    value = runtime.env.get(env_name)
    if value is None or value == "":
        raise CliError(
            f"Environment variable {env_name} is not set.",
            code="MISSING_PASSWORD_ENV",
            exit_code=2,
        )
    return value


async def _close_core(
    global_options: GlobalOptions, force: bool, runtime: Runtime
) -> CloseOutcome:
    if global_options.session in runtime.borrowed_sessions:
        await runtime.browser.release(global_options.session)
        del runtime.borrowed_sessions[global_options.session]
        return CloseOutcome(session=global_options.session, status="closed")
    state = await runtime.store.load(global_options.session)
    if state is None:
        return CloseOutcome(session=global_options.session, status="not_configured")

    out = output_dir(runtime.app_root, global_options.session)
    ensure_private_dir(out)

    # The Browser facade is process-local; each CLI process starts with an empty
    # session map. Re-attach to the still-running browser before asking the
    # facade to close it.
    attached = False
    try:
        attached = await _reattach_existing(global_options, state, runtime, out)
    except Exception as exc:  # noqa: BLE001
        if not force:
            raise
        _log(runtime, f"Force close failed to re-attach: {exc}")

    if attached:
        try:
            await runtime.browser.close(global_options.session)
        except Exception as exc:
            if not force:
                raise
            _log(runtime, f"Force close failed to close cleanly: {exc}")
    elif not force:
        raise CliError(
            f"Browser session {global_options.session} is configured but not reachable.",
            code="BROWSER_NOT_REACHABLE",
            exit_code=2,
        )
    else:
        _log(runtime, f"Force close: clearing unreachable session {global_options.session}")

    if state.profile_owned:
        try:
            await _remove_owned_profile(runtime.app_root, state.profile_dir)
        except CliError as exc:
            if not force:
                raise
            _log(runtime, f"Force close left the managed profile in place: {exc}")

    await runtime.store.clear(global_options.session)
    return CloseOutcome(session=global_options.session, status="closed")


async def _remove_owned_profile(app_root: Path, raw_profile_path: str) -> None:
    """Remove a tool-owned profile only when it is below the managed profile root."""
    managed_root = (app_root / "profiles").resolve()
    requested = Path(raw_profile_path).resolve()
    try:
        requested.relative_to(managed_root)
    except ValueError as exc:
        raise CliError(
            f"Refusing to remove managed profile outside {managed_root}.",
            code="UNSAFE_PROFILE_DELETE",
            exit_code=2,
        ) from exc
    if requested == managed_root:
        raise CliError(
            "Refusing to remove the managed profile root.",
            code="UNSAFE_PROFILE_DELETE",
            exit_code=2,
        )
    if requested.exists():
        await asyncio.to_thread(shutil.rmtree, requested)


async def close_browser(global_options: GlobalOptions, force: bool, runtime: Runtime) -> str:
    outcome = await _close_core(global_options, force, runtime)
    if outcome.status == "not_configured":
        return (
            json_output(
                {
                    "ok": True,
                    "command": "browser close",
                    "session": outcome.session,
                    "status": "not_configured",
                }
            )
            if global_options.json
            else f"Browser session {outcome.session} is not configured."
        )
    return (
        json_output({"ok": True, "command": "browser close", "session": outcome.session})
        if global_options.json
        else f"Browser session {outcome.session} closed."
    )


async def _ensure_session(
    global_options: GlobalOptions,
    runtime: Runtime,
    requested_mode: BrowserMode | None = None,
    replace_external_with_managed: bool = False,
) -> SessionState:
    borrowed = runtime.borrowed_sessions.get(global_options.session)
    if borrowed is not None:
        return borrowed
    existing = await runtime.store.load(global_options.session)
    out = output_dir(runtime.app_root, global_options.session)
    ensure_private_dir(out)

    if global_options.cdp:
        _log(runtime, f"Attaching to external CDP endpoint {global_options.cdp}")
        await runtime.browser.attach_external(
            session=global_options.session,
            endpoint=global_options.cdp,
            output_dir=out,
        )
        state = SessionState(
            name=global_options.session,
            managed=False,
            mode="headed",
            profile_dir=existing.profile_dir if existing else str(profile_dir(runtime.app_root, global_options.session)),
            profile_owned=False,
            attached_endpoint=global_options.cdp,
            debug_endpoint=global_options.cdp,
        )
        await _save_success(runtime, state)
        return state

    # Reuse a saved session by re-attaching to its still-running browser in this
    # process. A managed session being replaced by a fresh managed login
    # (replace_external_with_managed and the saved session is external) skips reuse.
    if existing and not (replace_external_with_managed and not existing.managed):
        if await _reattach_existing(global_options, existing, runtime, out):
            _log(runtime, f"Reusing browser session {global_options.session}")
            return existing
        _log(
            runtime,
            f"Saved browser session {global_options.session} was unreachable; "
            "a new login is required.",
        )
        with contextlib.suppress(Exception):
            await runtime.browser.close(global_options.session)
        if not requested_mode:
            raise CliError(
                "The saved browser is no longer reachable. Run proton-cli login again, "
                "or proton-cli browser close --force to remove its local profile.",
                code="BROWSER_NOT_REACHABLE",
                exit_code=2,
            )
        await runtime.store.clear(global_options.session)
        existing = None

    if existing and not existing.managed:
        with contextlib.suppress(Exception):
            await runtime.browser.close(global_options.session)

    if not requested_mode:
        raise CliError(
            "No browser session exists. Run proton-cli login first.",
            code="NO_BROWSER_SESSION",
            exit_code=1,
        )

    profile = await _prepare_managed_chrome_data_dir(global_options, runtime)
    _log(runtime, f"Opening managed {requested_mode} browser session {global_options.session}")
    try:
        await runtime.browser.open_managed(
            session=global_options.session,
            mode=requested_mode,
            profile_dir=profile.path,
            output_dir=out,
            debug_port=global_options.debug_port,
            proxy=global_options.proxy,
        )
    except BaseException:
        if profile.owned:
            await _remove_owned_profile(runtime.app_root, profile.path)
        raise
    # Persist the host/port/pid the browser actually bound to so a later command
    # process can re-attach to it (the core of the attach-per-command model).
    info = await runtime.browser.status(global_options.session)
    host = str(info.get("host") or "127.0.0.1")
    port = info.get("port")
    process_id = info.get("process_id")

    state = SessionState(
        name=global_options.session,
        managed=True,
        mode=requested_mode,
        profile_dir=profile.path,
        profile_owned=profile.owned,
        debug_endpoint=f"http://{host}:{port}" if port else None,
        browser_process_id=int(process_id) if isinstance(process_id, int) else None,
    )
    await _save_success(runtime, state)
    return state


async def _refresh_managed_browser_state(
    state: SessionState,
    session: str,
    mode: BrowserMode,
    runtime: Runtime,
) -> SessionState:
    """Refresh the saved endpoint after a managed browser may have relaunched."""
    if not state.managed:
        return state
    info = await runtime.browser.status(session)
    host = str(info.get("host") or "127.0.0.1")
    port = info.get("port")
    process_id = info.get("process_id")
    return SessionState(
        **{
            **state.__dict__,
            "mode": mode,
            "debug_endpoint": f"http://{host}:{port}" if port else None,
            "browser_process_id": int(process_id)
            if isinstance(process_id, int)
            else None,
        }
    )


async def _reattach_existing(
    global_options: GlobalOptions, state: SessionState, runtime: Runtime, out: Path
) -> bool:
    """Connect to the saved session's running browser; False if unreachable."""
    endpoint = state.debug_endpoint or state.attached_endpoint
    if not endpoint:
        return False
    host, port = _parse_endpoint(endpoint)
    if port is None:
        return False
    # Quick TCP probe: skip the 40-retry connect loop (~2 min) when the port is
    # already refusing connections (i.e. Chrome has exited since the last run).
    if not await runtime.tcp_probe(host, port):
        return False
    try:
        await runtime.browser.attach(
            session=global_options.session,
            host=host,
            port=port,
            output_dir=out,
            process_id=state.browser_process_id if state.managed else None,
        )
    except CliError as exc:
        if exc.code == "BROWSER_CONNECTION_FAILED":
            return False
        raise
    return True


async def _tcp_port_open(host: str, port: int) -> bool:
    """Return True if the TCP port is accepting connections."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=2.0,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
        return False


def _parse_endpoint(endpoint: str) -> tuple[str, int | None]:
    url = endpoint.split("://", 1)[1] if "://" in endpoint else endpoint
    host, _, port_str = url.rpartition(":")
    if not host:
        host = "127.0.0.1"
    try:
        return host, int(port_str)
    except ValueError:
        return host, None


async def _prepare_managed_chrome_data_dir(
    global_options: GlobalOptions, runtime: Runtime
) -> _ManagedProfile:
    if global_options.chrome_data_dir:
        return _ManagedProfile(
            path=await _prepare_provided_chrome_data_dir(
                global_options.chrome_data_dir, runtime.cwd
            ),
            owned=False,
        )

    path = profile_dir(runtime.app_root, global_options.session)
    ensure_private_dir(path)
    return _ManagedProfile(path=str(path), owned=True)


async def _prepare_provided_chrome_data_dir(raw_path: str, cwd: Path) -> str:
    requested = (cwd / raw_path).resolve()
    if requested.exists() and not requested.is_dir():
        raise CliError(
            "--chrome-data-dir must point to a directory.",
            code="INVALID_CHROME_DATA_DIR",
            exit_code=2,
            details={"path": str(requested)},
        )
    if not requested.exists():
        requested.mkdir(parents=True, exist_ok=True)

    entries = list(requested.iterdir())
    if entries and not all(entry.name.startswith("proton-cli-chrome-") for entry in entries):
        return str(requested)

    return tempfile.mkdtemp(prefix="proton-cli-chrome-", dir=requested)


async def _wait_for_mailbox_or_challenge(
    global_options: GlobalOptions,
    mode: BrowserMode,
    runtime: Runtime,
    profile: str,
) -> str:
    deadline = asyncio.get_event_loop().time() + 120
    alerted = False
    while asyncio.get_event_loop().time() < deadline:
        snapshot = await runtime.browser.snapshot(global_options.session)
        if is_mailbox_visible(snapshot):
            return snapshot
        if is_manual_challenge_visible(snapshot):
            if mode == "headless":
                runtime.logs.append(
                    "Manual challenge detected. Temporarily relaunching headed with the same profile."
                )
                await runtime.browser.close(global_options.session)
                await runtime.browser.open_managed(
                    session=global_options.session,
                    mode="headed",
                    profile_dir=profile,
                    output_dir=output_dir(runtime.app_root, global_options.session),
                    debug_port=global_options.debug_port,
                    proxy=global_options.proxy,
                )
                await _goto_with_retry(runtime, global_options.session, LOGIN_URL)
                print("\a", file=sys.stderr)
                runtime.logs.append(
                    "Complete the visible Proton challenge in the browser. proton-cli will resume automatically."
                )
                await _wait_for_snapshot(
                    runtime, global_options.session, is_mailbox_visible, "manual challenge completion", 300_000
                )
                await runtime.browser.close(global_options.session)
                await runtime.browser.open_managed(
                    session=global_options.session,
                    mode="headless",
                    profile_dir=profile,
                    output_dir=output_dir(runtime.app_root, global_options.session),
                    debug_port=global_options.debug_port,
                    proxy=global_options.proxy,
                )
                await _goto_with_retry(runtime, global_options.session, INBOX_URL)
                return await _wait_for_snapshot(
                    runtime, global_options.session, is_mailbox_visible, "mailbox after headless relaunch"
                )
            if not alerted:
                print("\a", file=sys.stderr)
                runtime.logs.append(
                    "Complete the visible Proton challenge in the browser. proton-cli will resume automatically."
                )
                alerted = True
        await runtime.sleep(2.0)
    return await runtime.browser.snapshot(global_options.session)


async def _wait_for_snapshot(
    runtime: Runtime,
    session: str,
    predicate: Callable[[str], bool],
    description: str,
    timeout_ms: float = 60_000,
) -> str:
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    attempts = 0
    last_snapshot: str | None = None
    while asyncio.get_event_loop().time() < deadline:
        snapshot = await runtime.browser.snapshot(session)
        attempts += 1
        last_snapshot = snapshot
        if predicate(snapshot):
            return snapshot
        await runtime.sleep(1.0)
    raise CliError(
        f"Timed out waiting for {description}.",
        code="WAIT_TIMEOUT",
        details={
            "attempts": attempts,
            "last_snapshot": _summarize_snapshot(last_snapshot) if last_snapshot else None,
        },
    )


_USERNAME_FIELD_PATTERNS = [
    re.compile(r'textbox "Email or username"', re.I),
    re.compile(r"textbox .*username", re.I),
    re.compile(r"textbox .*email", re.I),
]
_PASSWORD_FIELD_PATTERNS = [
    re.compile(r'textbox "Password"', re.I),
    re.compile(r"textbox .*password", re.I),
]
_SIGNIN_BUTTON_PATTERNS = [
    re.compile(r'button "Sign in"', re.I),
    re.compile(r"button .*(sign in|log in)", re.I),
]
_NEXT_BUTTON_PATTERNS = [
    re.compile(r'button "Sign in"', re.I),
    re.compile(r'button "Continue"', re.I),
    re.compile(r'button "Next"', re.I),
]


def _snapshot_textbox_value(snapshot: str, patterns: list[re.Pattern[str]]) -> str | None:
    """Return the aria value of the first textbox line matching *patterns*.

    Aria snapshots render filled text inputs as ``textbox "Label" [ref=eN]: value``.
    Returns ``None`` when no matching textbox exists and ``""`` when it exists
    but carries no value.
    """
    for line in snapshot.splitlines():
        if not any(pattern.search(line) for pattern in patterns):
            continue
        _, sep, value = line.partition(":")
        return value.strip() if sep else ""
    return None


async def _fill_login_field(
    runtime: Runtime,
    session: str,
    patterns: list[re.Pattern[str]],
    text: str,
    description: str,
    verify: str,
) -> None:
    """Fill a login field using a freshly resolved ref, retrying on stale refs.

    Proton mounts anti-bot challenge iframes shortly after the login form first
    renders; the mount re-renders the form and detaches refs resolved from an
    older snapshot, which silently drops typed text and submits an empty form.
    Resolving the ref from a fresh snapshot right before filling, and checking
    the rendered value afterwards, makes the fill robust against that race.

    ``verify`` is ``"value"`` (compare the aria-rendered value), ``"length"``
    (compare the live input value length — for password fields, whose values
    aria never exposes; catches fills that were silently duplicated), or
    ``"none"``.
    """
    last_error: BaseException | None = None
    for _attempt in range(3):
        snapshot = await runtime.browser.snapshot(session)
        ref = find_first_ref(snapshot, patterns)
        if not ref:
            raise CliError(
                f"Could not find Proton login {description} field.",
                code="LOGIN_FIELDS_NOT_FOUND",
            )
        try:
            await runtime.browser.fill(session, ref, text)
        except Exception as exc:
            last_error = exc
            await runtime.sleep(0.75)
            continue
        if verify == "none":
            return
        confirm = await runtime.browser.snapshot(session)
        if verify == "length":
            confirm_ref = find_first_ref(confirm, patterns)
            actual: str | None = None
            if confirm_ref:
                try:
                    actual = await runtime.browser.read_input_value(session, confirm_ref)
                except Exception:  # noqa: BLE001
                    actual = None
            if actual is not None and len(actual) == len(text):
                return
        elif _snapshot_textbox_value(confirm, patterns) == text:
            return
        _log(runtime, f"Login {description} fill did not stick; retrying with a fresh ref.")
        await runtime.sleep(0.75)
    if last_error is not None:
        raise CliError(
            f"Could not fill Proton login {description} field: {last_error}",
            code="LOGIN_FIELDS_NOT_FOUND",
        ) from last_error
    raise CliError(
        f"Proton login {description} field did not retain the typed value.",
        code="LOGIN_FIELDS_NOT_FOUND",
    )


async def _click_login_button(runtime: Runtime, session: str, patterns: list[re.Pattern[str]]) -> None:
    """Click the first matching login button using a freshly resolved ref."""
    snapshot = await runtime.browser.snapshot(session)
    ref = find_first_ref(snapshot, patterns)
    if ref:
        try:
            await runtime.browser.click(session, ref)
            return
        except Exception as exc:
            _log(runtime, f"Login button click failed (stale ref?): {exc}; pressing Enter.")
    await runtime.browser.press(session, "Enter")


async def _await_login_submit(runtime: Runtime, session: str, timeout_s: float = 30.0) -> str:
    """Classify the result of a login submit.

    Returns ``submitted`` once the login form disappears, ``credentials`` when
    the server rejected the credentials (retrying will not help), ``validation``
    when the form bounced back with empty-field errors, and ``form_stuck`` when
    the form is still visible after the timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        snapshot = await runtime.browser.snapshot(session)
        if not is_login_form_visible(snapshot):
            return "submitted"
        lowered = snapshot.lower()
        if "incorrect login credentials" in lowered or "wrong password" in lowered:
            return "credentials"
        if "this field is required" in lowered:
            return "validation"
        await runtime.sleep(1.0)
    return "form_stuck"


async def _submit_login_form(
    global_options: GlobalOptions,
    command: LoginCommand,
    runtime: Runtime,
    password_value: str,
) -> None:
    """Fill and submit the Proton login form, retrying the whole form on failure."""
    session = global_options.session
    last_outcome = "form_stuck"
    for attempt in range(1, 4):
        await _fill_login_field(
            runtime, session, _USERNAME_FIELD_PATTERNS, command.email, "username", verify="value"
        )
        # Proton uses a two-step login: step 1 shows only the email field.
        # Single-step forms already have the password field, so skip the
        # advance step in that case.
        intermediate = await runtime.browser.snapshot(session)
        if not has_login_password_field(intermediate):
            await _click_login_button(runtime, session, _NEXT_BUTTON_PATTERNS)
            await runtime.sleep(0.5)
            await _wait_for_snapshot(
                runtime,
                session,
                lambda candidate: has_login_password_field(candidate)
                or is_manual_challenge_visible(candidate),
                "Proton login password step",
                30_000,
            )
        await _fill_login_field(
            runtime, session, _PASSWORD_FIELD_PATTERNS, password_value, "password", verify="length"
        )
        await _click_login_button(runtime, session, _SIGNIN_BUTTON_PATTERNS)
        last_outcome = await _await_login_submit(runtime, session)
        if last_outcome == "submitted":
            return
        if last_outcome == "credentials":
            raise CliError(
                "Proton rejected the login credentials.",
                code="LOGIN_CREDENTIALS_REJECTED",
            )
        _log(runtime, f"Login submit attempt {attempt} did not go through ({last_outcome}); retrying.")
    raise CliError(
        f"Proton login form could not be submitted after 3 attempts (last outcome: {last_outcome}).",
        code="LOGIN_SUBMIT_FAILED",
    )


async def _fill_recipients(
    session: str, runtime: Runtime, kind: str, recipients: list[str]
) -> None:
    if not recipients:
        return
    snapshot = await runtime.browser.snapshot(session)
    ref = find_recipient_field_ref(snapshot, kind)  # type: ignore[arg-type]
    if not ref:
        raise CliError(
            f"Could not find Proton composer {kind.upper()} recipient field.",
            code="RECIPIENT_FIELD_NOT_FOUND",
        )
    await runtime.browser.click(session, ref)
    for recipient in recipients:
        await runtime.browser.type_text(session, recipient)
        await runtime.browser.press(session, "Enter")


async def _reveal_recipient_field(session: str, runtime: Runtime, label: str) -> None:
    snapshot = await runtime.browser.snapshot(session)
    ref = find_first_ref(
        snapshot,
        [
            re.compile(rf'button "{label}"', re.I),
            re.compile(rf"\b{label}\b", re.I),
        ],
    )
    if ref:
        await runtime.browser.click(session, ref)
        await runtime.sleep(0.5)


async def _fill_body(session: str, runtime: Runtime, body: str) -> None:
    if not body:
        return
    await _dismiss_writing_assistant(session, runtime)
    await _focus_composer_body(session, runtime)
    await runtime.browser.type_text(session, body)
    await runtime.sleep(0.5)
    updated = await runtime.browser.snapshot(session)
    if is_search_dialog_visible(updated):
        await runtime.browser.press(session, "Escape")
        raise CliError(
            "Typed message body into Proton search instead of the composer body editor.",
            code="BODY_EDITOR_FOCUS_FAILED",
        )
    # The editor lives in an iframe; the multi-frame snapshot captures its text,
    # so verify the typed body landed by searching the snapshot directly.
    if body not in updated:
        raise CliError(
            "Proton composer body did not contain the requested body text after typing.",
            code="BODY_EDITOR_FILL_FAILED",
        )


async def _focus_composer_body(session: str, runtime: Runtime) -> None:
    snapshot = await runtime.browser.snapshot(session)
    subject_ref = _find_subject_field_ref(snapshot)
    body_ref = find_composer_body_editor_ref(snapshot)
    if not subject_ref and not body_ref:
        raise CliError(
            "Could not find Proton composer body editor.",
            code="BODY_EDITOR_NOT_FOUND",
        )
    if subject_ref:
        await runtime.browser.click(session, subject_ref)
        await runtime.browser.press(session, "Tab")
    elif body_ref:
        await runtime.browser.click(session, body_ref)
    await runtime.sleep(0.75)


async def _fill_subject(session: str, runtime: Runtime, subject: str) -> None:
    snapshot = await runtime.browser.snapshot(session)
    ref = _find_subject_field_ref(snapshot)
    if not ref:
        raise CliError(
            "Could not find Proton composer subject field.",
            code="SUBJECT_FIELD_NOT_FOUND",
        )
    await runtime.browser.click(session, ref)
    if subject:
        await runtime.browser.type_text(session, subject)


async def _attach_files(session: str, runtime: Runtime, attachments: list[str]) -> None:
    if not attachments:
        return
    await _click_visible(
        session,
        runtime,
        [
            re.compile(r'button "Attach files"', re.I),
            re.compile(r"button .*attach", re.I),
            re.compile(r"button .*attachment", re.I),
            re.compile(r"composer-attachments-button", re.I),
        ],
        "attachment button",
    )
    await runtime.browser.upload(session, attachments)


async def _click_visible(
    session: str, runtime: Runtime, patterns: list[re.Pattern[str]], label: str
) -> None:
    ref = await _find_visible_control_ref(session, runtime, patterns, label)
    await runtime.browser.click(session, ref)


async def _find_visible_control_ref(
    session: str, runtime: Runtime, patterns: list[re.Pattern[str]], label: str
) -> str:
    snapshot = await runtime.browser.snapshot(session)
    ref = find_first_ref(snapshot, patterns)
    if not ref:
        raise CliError(f"Could not find {label}.", code="VISIBLE_CONTROL_NOT_FOUND")
    return ref


async def _dismiss_writing_assistant(session: str, runtime: Runtime) -> None:
    snapshot = await runtime.browser.snapshot(session)
    if re.search(r"Craft better emails", snapshot):
        close_ref = find_first_ref(snapshot, [re.compile(r'button "Close"', re.I)])
        if close_ref:
            await runtime.browser.click(session, close_ref)
            await runtime.sleep(0.5)
            snapshot = await runtime.browser.snapshot(session)

    assistant_ref = find_first_ref(
        snapshot, [re.compile(r'button "Your email writing assistant"', re.I)]
    )
    if assistant_ref:
        await runtime.browser.click(session, assistant_ref)
        await runtime.sleep(0.5)


async def _dismiss_welcome_dialog(session: str, runtime: Runtime) -> None:
    """Click through the Proton Mail welcome/onboarding dialog if it appears.

    The modal renders asynchronously after the inbox shell loads, so we poll for
    a short time and require a few consecutive "no dialog" snapshots before
    treating the mailbox as ready.
    """
    deadline = asyncio.get_event_loop().time() + 30
    dismissed: set[str] = set()
    stable_count = 0
    while asyncio.get_event_loop().time() < deadline and stable_count < 3:
        snapshot = await runtime.browser.snapshot(session)
        if not is_welcome_dialog_visible(snapshot):
            stable_count += 1
            await runtime.sleep(0.3)
            continue
        stable_count = 0
        ref = find_welcome_button_ref(snapshot)
        if not ref:
            _log(runtime, "Welcome dialog visible but no known button found; waiting.")
            await runtime.sleep(0.5)
            continue
        label = _extract_button_label(snapshot, ref)
        if label and label in dismissed:
            _log(runtime, f"Welcome dialog button {label!r} already dismissed; waiting.")
            await runtime.sleep(0.5)
            continue
        _log(runtime, f"Dismissing welcome dialog by clicking button {label!r}.")
        try:
            await runtime.browser.click(session, ref)
        except Exception as exc:
            _log(runtime, f"Welcome dialog click failed (stale ref?): {exc}")
            await runtime.sleep(0.5)
            continue
        if label:
            dismissed.add(label)
        await runtime.sleep(0.5)
    if stable_count < 3:
        _log(runtime, "Welcome dialog dismissal timed out; proceeding.")


def _extract_button_label(snapshot: str, ref: str) -> str | None:
    pattern = re.compile(rf"button \"([^\"]+)\"\s*\[ref={re.escape(ref)}\]", re.I)
    match = pattern.search(snapshot)
    return match.group(1) if match else None


async def _send_current_composer(session: str, runtime: Runtime, subject: str) -> None:
    deadline = asyncio.get_event_loop().time() + SEND_CONFIRMATION_TIMEOUT_MS / 1000
    attempts = 0
    while asyncio.get_event_loop().time() < deadline:
        snapshot = await runtime.browser.snapshot(session)
        confirmation_ref = find_send_confirmation_ref(snapshot)
        if confirmation_ref:
            await runtime.browser.click(session, confirmation_ref)
            await runtime.sleep(1.0)
            continue
        if not is_composer_visible(snapshot, subject):
            return
        if is_send_in_progress(snapshot):
            await runtime.sleep(2.0)
            continue

        send_ref = find_first_ref(snapshot, [re.compile(r'button "Send"', re.I)])
        if not send_ref:
            await runtime.sleep(2.0)
            continue
        attempts += 1
        await runtime.browser.click(session, send_ref)
        await runtime.sleep(SEND_CLICK_SETTLE_MS / 1000)

    raise CliError(
        f"Clicked Send {attempts} time(s), but Proton composer remained open.",
        code="SEND_NOT_CONFIRMED",
    )


async def _goto_with_retry(runtime: Runtime, session: str, url: str) -> None:
    last_error: BaseException | None = None
    for attempt in range(1, 4):
        try:
            await runtime.browser.goto(session, url)
            return
        except Exception as exc:
            last_error = exc
            if not _is_transient_navigation_error(exc) or attempt == 3:
                raise
            await runtime.sleep(3.0)
    if last_error:
        raise last_error


def _is_transient_navigation_error(error: BaseException) -> bool:
    message = str(error)
    return bool(re.search(r"net::ERR_TIMED_OUT|Timeout \d+ms exceeded", message))


def _mailbox_url(folder: str) -> str:
    return SENT_URL if folder == "sent" else INBOX_URL


def _find_subject_field_ref(snapshot: str) -> str | None:
    return find_first_ref(
        snapshot,
        [re.compile(r'textbox "Subject"', re.I), re.compile(r"textbox .*subject", re.I)],
    )


def _summarize_snapshot(snapshot: str) -> dict[str, Any]:
    return {
        "bytes": len(snapshot),
        "login_form": is_login_form_visible(snapshot),
        "mailbox": is_mailbox_visible(snapshot),
        "manual_challenge": is_manual_challenge_visible(snapshot),
    }


async def _save_success(runtime: Runtime, state: SessionState) -> None:
    updated = SessionState(
        **{
            **state.__dict__,
            "last_successful_command_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    if state.name in runtime.borrowed_sessions:
        runtime.borrowed_sessions[state.name] = updated
        return
    await runtime.store.save(updated)


def _log(runtime: Runtime, message: str) -> None:
    if runtime.verbose:
        runtime.logs.append(message)
