"""Port of src/snapshot-parser.ts — parser for Zendriver accessibility snapshots."""

from __future__ import annotations

import re
from typing import Literal

from proton_cli.types import InboxRow, ReadMessage


def find_first_ref(snapshot: str, patterns: list[re.Pattern[str]]) -> str | None:
    for line in snapshot.splitlines():
        if not any(pattern.search(line) for pattern in patterns):
            continue
        ref = _extract_ref(line)
        if ref:
            return ref
    return None


def find_composer_body_editor_ref(snapshot: str) -> str | None:
    lines = snapshot.splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"\biframe\b", line, re.I):
            continue
        iframe_indent = _indent_of(line)
        for child_index in range(index + 1, len(lines)):
            child = lines[child_index]
            if child.strip() and _indent_of(child) <= iframe_indent:
                break
            if not re.search(r"\b(generic|textbox|article)\b", child, re.I) or re.search(
                r"\blink\b", child, re.I
            ):
                continue
            ref = _extract_ref(child)
            if ref:
                return ref

    return find_first_ref(
        snapshot,
        [
            re.compile(r"textbox .*message", re.I),
            re.compile(r"article", re.I),
            re.compile(r"rooster-editor", re.I),
            re.compile(r"iframe", re.I),
        ],
    )


def is_mailbox_visible(snapshot: str) -> bool:
    return bool(
        re.search(r'\b(button "Compose"|Compose|Inbox|Conversation list)\b', snapshot, re.I)
        and not is_login_form_visible(snapshot)
    )


def is_inbox_rows_visible(snapshot: str) -> bool:
    """True when at least one fully-valid email row has rendered and is parseable."""
    if is_login_form_visible(snapshot):
        return False
    return len(parse_inbox_snapshot(snapshot, 1)) > 0


def is_manual_challenge_visible(snapshot: str) -> bool:
    return bool(
        re.search(
            r"captcha|two[- ]factor|verification code|passkey|security key|recover|confirm it'?s you",
            snapshot,
            re.I,
        )
    )


def find_recipient_field_ref(snapshot: str, kind: Literal["to", "cc", "bcc"]) -> str | None:
    label = kind if kind != "to" else "to"
    specific = find_first_ref(
        snapshot,
        [
            re.compile(rf'(?:textbox|combobox).*"[^"]*\b{label}\b[^"]*"', re.I),
            re.compile(rf"(?:textbox|combobox).*\b{label}\b", re.I),
        ],
    )
    if specific:
        return specific
    if kind == "to":
        return find_first_ref(snapshot, [re.compile(r"(?:textbox|combobox).*email", re.I)])
    return None


def find_send_confirmation_ref(snapshot: str) -> str | None:
    return find_first_ref(
        snapshot,
        [
            re.compile(r'button "Send anyway"', re.I),
            re.compile(r'button "OK"', re.I),
            re.compile(r'button "Confirm"', re.I),
        ],
    )


def parse_inbox_snapshot(snapshot: str, limit: int) -> list[InboxRow]:
    lines = snapshot.splitlines()
    rows: list[InboxRow] = []
    i = 0
    while i < len(lines) and len(rows) < limit:
        line = lines[i]
        # Email rows are clickable region elements: region "Subject" [ref=eXXX] [cursor=pointer]:
        if not (re.search(r"\bregion\b", line, re.I) and "[cursor=pointer]" in line):
            i += 1
            continue
        ref = _extract_ref(line)
        quoted = re.search(r'"([^"]+)"', line)
        if not ref or not quoted:
            i += 1
            continue
        subject = quoted.group(1)

        # Collect indented child lines belonging to this region
        region_indent = _indent_of(line)
        j = i + 1
        subtree_lines: list[str] = []
        while j < len(lines):
            child_line = lines[j]
            if child_line.strip() and _indent_of(child_line) <= region_indent:
                break
            subtree_lines.append(child_line)
            j += 1
        subtree = "\n".join(subtree_lines)

        # Confirm this is an email row: must have a star/unstar button and a level-2 heading
        if not re.search(r"\b(Star|Unstar) conversation\b", subtree):
            i = j
            continue
        if not re.search(r"\bheading\b[^\n]*\[level=2\]", subtree):
            i = j
            continue

        status: Literal["read", "unread", "unknown"] = (
            "unread" if "Unread email" in subtree else "read"
        )
        starred = "Unstar conversation" in subtree
        has_attachment = bool(re.search(r"\bHas \d+ attachment", subtree))
        time_m = re.search(r"\btime\b[^\n]*:\s+(.+)", subtree)
        age = time_m.group(1).strip() if time_m else None
        email_m = re.search(r'\bgeneric\b "([^"]*@[^"]*)"', subtree)
        from_email = email_m.group(1) if email_m else None
        from_display = _sender_display_name(subtree_lines, from_email)
        labels = [
            lbl
            for lbl in ("Official", "External", "Newsletter", "Auto", "Notification")
            if re.search(rf"\bgeneric\b[^\n]*:\s+{lbl}\s*$", subtree, re.M)
        ]

        rows.append(
            InboxRow(
                row=len(rows) + 1,
                handle=str(len(rows) + 1),
                status=status,
                starred=starred,
                has_attachment=has_attachment,
                age=age,
                from_display=from_display,
                from_email=from_email,
                labels=labels,
                subject=subject,
                target=ref,
            )
        )
        i = j

    return rows


def _sender_display_name(subtree_lines: list[str], from_email: str | None) -> str:
    """Return the display name from the sender's generic block, or the email local-part."""
    if not from_email:
        return ""
    # Find the line that declares the sender's email address
    email_line_idx = -1
    email_indent = -1
    for k, ln in enumerate(subtree_lines):
        if f'"{from_email}"' in ln and re.search(r"\bgeneric\b", ln, re.I):
            email_line_idx = k
            email_indent = _indent_of(ln)
            break
    if email_line_idx == -1:
        return from_email.split("@")[0]
    # Walk child lines for the first visible name that isn't a label keyword
    for k in range(email_line_idx + 1, len(subtree_lines)):
        child = subtree_lines[k]
        if not child.strip():
            continue
        if _indent_of(child) <= email_indent:
            break
        text = _extract_visible_text(child)
        if text and not re.match(
            r"^(Official|External|Newsletter|Auto|Notification|Unread email)$",
            text,
            re.I,
        ):
            return text
    return from_email.split("@")[0]


def parse_read_message_snapshot(snapshot: str) -> ReadMessage:
    lines = snapshot.splitlines()
    texts = [text for text in (_extract_visible_text(line) for line in lines) if text]
    subject = _first_heading(lines)
    sender = _first_prefixed(texts, "From")
    to = _all_prefixed(texts, "To")
    cc = _all_prefixed(texts, "Cc")
    date = _first_prefixed(texts, "Date")
    body = _article_text(lines)
    return ReadMessage(subject=subject, sender=sender, to=to, cc=cc, date=date, body=body)



def _extract_ref(line: str) -> str | None:
    match = re.search(r"\[ref=([a-z]?\d*e\d+)\]", line, re.I)
    return match.group(1) if match else None


def is_composer_visible(snapshot: str, subject: str) -> bool:
    if not re.search(r"Composer:|textbox \"Subject\"|button \"Send\"", snapshot, re.I):
        return False
    return subject == "" or subject in snapshot or re.search(r'textbox "Subject"|button "Send"', snapshot, re.I) is not None


def is_send_in_progress(snapshot: str) -> bool:
    return bool(re.search(r"Sending message|Sending\.{0,3}|Encrypting message", snapshot, re.I))


def is_search_dialog_visible(snapshot: str) -> bool:
    return bool(re.search(r"dialog[\s\S]*Search date, name, email address, or subject line", snapshot))


_WELCOME_DISMISS_LABELS = ("Close", "Skip", "Maybe later", "Done")
_WELCOME_ADVANCE_LABELS = ("Use this", "Let's get started", "Get started", "Start", "Next", "Continue")


def is_welcome_dialog_visible(snapshot: str) -> bool:
    """True when the Proton Mail onboarding modal is open."""
    return find_welcome_button_ref(snapshot) is not None


def find_welcome_button_ref(snapshot: str) -> str | None:
    """Return the ref of a clickable onboarding button inside the active dialog."""
    dialog = _extract_active_dialog_subtree(snapshot)
    if not dialog:
        return None
    for label in _WELCOME_DISMISS_LABELS:
        ref = find_first_ref(dialog, [re.compile(rf'button "{re.escape(label)}"', re.I)])
        if ref:
            return ref
    for label in _WELCOME_ADVANCE_LABELS:
        ref = find_first_ref(dialog, [re.compile(rf'button "{re.escape(label)}"', re.I)])
        if ref:
            return ref
    return None


def _extract_active_dialog_subtree(snapshot: str) -> str | None:
    lines = snapshot.splitlines()
    for i, line in enumerate(lines):
        if not re.search(r"\bdialog\b.*\[active\]", line, re.I):
            continue
        dialog_indent = _indent_of(line)
        subtree = [line]
        for j in range(i + 1, len(lines)):
            candidate = lines[j]
            if candidate.strip() and _indent_of(candidate) <= dialog_indent:
                break
            subtree.append(candidate)
        return "\n".join(subtree)
    return None


def has_login_username_field(snapshot: str) -> bool:
    """True when the Proton login username/email step is visible."""
    return bool(
        re.search(r'textbox "Email or username"', snapshot, re.I)
        or re.search(r"(?:textbox|input).*\busername\b", snapshot, re.I)
    )


def has_login_password_field(snapshot: str) -> bool:
    """True when the Proton login password step is visible."""
    return bool(re.search(r"(?:textbox|input|field).*password", snapshot, re.I))


def is_login_form_visible(snapshot: str) -> bool:
    # Proton uses a two-step login: step 1 shows only the email/username field,
    # step 2 shows only the password field. Require either step so that the
    # login page is never misidentified as the mailbox by is_mailbox_visible.
    return has_login_username_field(snapshot) or has_login_password_field(snapshot)


def _extract_visible_text(line: str) -> str:
    quoted = re.search(r'"([^"]+)"', line)
    if quoted:
        return quoted.group(1).strip()
    cleaned = re.sub(r"^[\s|`+\-]*-\s*", "", line)
    cleaned = re.sub(r"\[ref=[a-z]?\d*e\d+\]", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^\w+\s*", "", cleaned)
    cleaned = re.sub(r"^:\s*", "", cleaned)
    return cleaned.strip()


def _indent_of(line: str) -> int:
    match = re.search(r"\S", line)
    return match.start() if match else len(line)


def _extract_age(text: str) -> str | None:
    match = re.search(r"\b(?:Today|Yesterday)\b", text, re.I)
    if match:
        return match.group(0)
    match = re.search(
        r"\b\d+\s+(?:minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\b",
        text,
        re.I,
    )
    if match:
        return match.group(0)
    match = re.search(r"\b[A-Z][a-z]{2}\s+\d{1,2}(?:,\s+\d{4})?\b", text)
    return match.group(0) if match else None


def _extract_email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    return match.group(0) if match else None


def _looks_like_label(value: str) -> bool:
    return bool(
        re.match(
            r"^(Official|External|Newsletter|Auto|Notification|Important|Work|Personal)$",
            value,
            re.I,
        )
    )


def _first_heading(lines: list[str]) -> str | None:
    for line in lines:
        if re.search(r"\bheading\b", line, re.I):
            text = _extract_visible_text(line)
            if text and not _is_shell_heading(text):
                return text
    return None


def _is_shell_heading(text: str) -> bool:
    return bool(
        re.match(
            r"^(Proton Mail|Navigation|More|Views|Folders|Labels|Get storage bonus|Conversation list\b.*|Message list\b.*|Inbox|Sent)$",
            text,
            re.I,
        )
    )


def _first_prefixed(texts: list[str], label: str) -> str | None:
    result = _all_prefixed(texts, label)
    return result[0] if result else None


def _all_prefixed(texts: list[str], label: str) -> list[str]:
    prefix = re.compile(rf"^{label}:\s*(.+)$", re.I)
    values: list[str] = []
    for text in texts:
        match = prefix.search(text)
        if match and match.group(1):
            values.append(match.group(1).strip())
    return values


def _article_text(lines: list[str]) -> str:
    body: list[str] = []
    in_article = False
    article_indent = 0

    for line in lines:
        indent = _indent_of(line)
        if re.search(r"\barticle\b", line, re.I):
            in_article = True
            article_indent = indent
            continue
        if in_article and indent <= article_indent and line.strip():
            in_article = False
        if in_article:
            text = _extract_visible_text(line)
            if text:
                body.append(text)

    if body:
        return "\n".join(body).strip()

    fallback = [
        text
        for text in (_extract_visible_text(line) for line in lines)
        if text and not re.match(r"^(From|To|Cc|Date):", text, re.I) and "Proton Mail" not in text
    ]
    return "\n".join(fallback[1:]).strip()
