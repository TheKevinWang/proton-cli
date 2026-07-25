"""Command-line parsing types for proton-cli."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from proton_cli.release_policy import RELEASE_POLICY

BrowserMode = Literal["headed", "headless"]
MailboxFolder = Literal["inbox", "sent"]
RecoveryVerificationMode = Literal["proton", "interactive"]
RecoveryVerificationSelection = Literal["auto", "proton", "interactive"]


@dataclass
class GlobalOptions:
    session: str = "default"
    keep_open: bool = False
    json: bool = False
    verbose: bool = False
    cdp: str | None = None
    debug_port: int | None = None
    chrome_data_dir: str | None = None
    trace: str | None = None
    proxy: str | None = RELEASE_POLICY.default_proxy


@dataclass
class LoginCommand:
    kind: Literal["login"] = "login"
    email: str = ""
    password_env: str | None = None
    password: str | None = None
    mode: BrowserMode = "headed"


@dataclass
class InboxCommand:
    kind: Literal["inbox"] = "inbox"
    limit: int = 20
    folder: MailboxFolder = "inbox"


@dataclass
class RefreshCommand:
    kind: Literal["refresh"] = "refresh"
    limit: int = 20


@dataclass
class ReadCommand:
    kind: Literal["read"] = "read"
    handle: str = ""


@dataclass
class SendCommand:
    kind: Literal["send"] = "send"
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str | None = None
    body: str | None = None
    body_file: str | None = None
    attachments: list[str] = field(default_factory=list)


@dataclass
class RecoveryEmailAddCommand:
    kind: Literal["recovery-email-add"] = "recovery-email-add"
    email: str = ""
    password_env: str | None = None
    verification: RecoveryVerificationSelection = "auto"
    recovery_password_env: str | None = None
    recovery_proxy: str = "socks5://localhost:9150"
    timeout_seconds: int = 180


@dataclass
class BrowserStatusCommand:
    kind: Literal["browser-status"] = "browser-status"


@dataclass
class BrowserCloseCommand:
    kind: Literal["browser-close"] = "browser-close"
    force: bool = False


@dataclass
class HelpCommand:
    kind: Literal["help"] = "help"


@dataclass
class InboxRow:
    row: int
    handle: str
    status: Literal["read", "unread", "unknown"]
    starred: bool | None
    has_attachment: bool | None
    age: str | None
    from_display: str
    from_email: str | None
    labels: list[str]
    subject: str
    target: str | None = None


@dataclass
class ReadMessage:
    subject: str | None
    sender: str | None
    to: list[str]
    cc: list[str]
    date: str | None
    body: str


@dataclass
class LoginOutcome:
    account: str
    session: str


@dataclass
class CloseOutcome:
    session: str
    status: Literal["closed", "not_configured"]


@dataclass
class RecoveryEmailOutcome:
    email: str
    status: Literal["verified", "already_verified"]
    verification: RecoveryVerificationMode
    session: str


ParsedCommand = (
    LoginCommand
    | InboxCommand
    | RefreshCommand
    | ReadCommand
    | SendCommand
    | RecoveryEmailAddCommand
    | BrowserStatusCommand
    | BrowserCloseCommand
    | HelpCommand
)


@dataclass
class ParsedInvocation:
    global_options: GlobalOptions
    command: ParsedCommand
