"""proton-cli — typed Python CLI and library API for Proton Mail browser automation."""

from proton_cli.api import ProtonClient
from proton_cli.errors import CliError
from proton_cli.session import SessionState
from proton_cli.types import (
    CloseOutcome,
    InboxRow,
    LoginOutcome,
    ReadMessage,
    RecoveryEmailOutcome,
    RecoveryVerificationMode,
    RecoveryVerificationSelection,
)
from proton_cli.workflows.recovery_email import (
    InteractiveRecoveryEmailVerifier,
    ProtonMailboxVerifier,
    RecoveryEmailChallenge,
    RecoveryEmailVerifier,
)

__all__ = [
    "CliError",
    "CloseOutcome",
    "InboxRow",
    "LoginOutcome",
    "ProtonClient",
    "ReadMessage",
    "RecoveryEmailChallenge",
    "RecoveryEmailOutcome",
    "RecoveryEmailVerifier",
    "RecoveryVerificationMode",
    "RecoveryVerificationSelection",
    "InteractiveRecoveryEmailVerifier",
    "ProtonMailboxVerifier",
    "SessionState",
]
