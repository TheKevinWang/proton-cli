"""Text and JSON output formatting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from proton_cli.errors import redact_object, redact_text
from proton_cli.release_policy import proxy_help_text
from proton_cli.types import InboxRow, ReadMessage

if TYPE_CHECKING:
    from proton_cli.session import SessionState


def _bool_word(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def format_inbox_text(rows: list[InboxRow]) -> str:
    if not rows:
        return "Inbox is empty."
    header = "#  Status   Star  Attach  Age        From                 Labels      Subject"
    lines = [header]
    for row in rows:
        lines.append(
            " ".join(
                [
                    str(row.row).ljust(2),
                    row.status.ljust(8),
                    _bool_word(row.starred).ljust(5),
                    _bool_word(row.has_attachment).ljust(7),
                    (row.age or "").ljust(10),
                    row.from_display.ljust(20),
                    ",".join(row.labels).ljust(11),
                    row.subject,
                ]
            )
        )
    return "\n".join(lines)


def format_read_text(message: ReadMessage) -> str:
    lines = [
        f"From: {message.sender or ''}",
        f"To: {', '.join(message.to)}",
    ]
    if message.cc:
        lines.append(f"Cc: {', '.join(message.cc)}")
    lines.append(f"Date: {message.date or ''}")
    lines.append(f"Subject: {message.subject or ''}")
    lines.append("")
    lines.append(message.body)
    return "\n".join(lines).rstrip()


def serialize_read_message(message: ReadMessage) -> dict[str, Any]:
    """JSON shape matching the TypeScript ReadMessage interface (``from`` key)."""
    return {
        "subject": message.subject,
        "from": message.sender,
        "to": message.to,
        "cc": message.cc,
        "date": message.date,
        "body": message.body,
    }


def format_status_text(state: SessionState | None) -> str:
    if state is None:
        return "Browser session default: not configured"
    return "\n".join(
        [
            f"Browser session: {state.name}",
            f"Managed: {'yes' if state.managed else 'no'}",
            f"Mode: {state.mode}",
            f"Profile: {state.profile_dir}",
            f"Account: {state.account_email or 'unknown'}",
            f"Debug endpoint: {state.debug_endpoint or state.attached_endpoint or 'none'}",
            f"Last successful command: {state.last_successful_command_at or 'unknown'}",
        ]
    )


def json_output(data: Any, secrets: list[str] | None = None) -> str:
    safe = redact_object(_to_serializable(data), secrets)
    return json.dumps(safe, indent=2)


def error_output(error: Any, secrets: list[str] | None = None) -> str:
    return json_output({"ok": False, "code": error.code, "message": redact_text(str(error), secrets), **error.details}, secrets)


def _to_serializable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, list):
        return [_to_serializable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_serializable(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_serializable(getattr(value, key)) for key in value.__dataclass_fields__}
    return str(value)


def help_text() -> str:
    return (
        "Usage: proton-cli [global options] <command> [options]\n"
        "\n"
        "Commands:\n"
        "  login --email <address> --password-env <name>|--password <value>\n"
        "  inbox [--limit <n>] [--folder inbox|sent]\n"
        "  refresh\n"
        "  inbox refresh\n"
        "  read <handle>\n"
        "  send --to <address> --subject <text> --body <text>\n"
        "  browser status\n"
        "  browser close [--force]\n"
        "\n"
        "Global options:\n"
        "  --chrome-data-dir <path>  Use user-owned Chrome data (never deleted automatically).\n"
        f"  --proxy <url>             {proxy_help_text()}\n"
    )
