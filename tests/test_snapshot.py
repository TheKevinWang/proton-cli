"""Tests for the snapshot parser port."""

from __future__ import annotations

import re

from proton_cli.snapshot import (
    find_collapsed_message_header_refs,
    find_composer_body_editor_ref,
    find_first_ref,
    find_mailbox_inbox_ref,
    find_mark_as_read_ref,
    find_recipient_field_ref,
    find_recovery_message_checkbox_ref,
    find_recovery_message_open_ref,
    find_recovery_message_row_ref,
    find_send_confirmation_ref,
    find_welcome_button_ref,
    is_mailbox_visible,
    is_welcome_dialog_visible,
    parse_inbox_snapshot,
    parse_read_message_snapshot,
)
from proton_cli.types import InboxRow, ReadMessage

INBOX_SNAPSHOT = """
- main "Proton Mail" [ref=e1]
  - button "Compose" [ref=e2]
  - button "Refresh" [ref=e3]
  - heading "Conversation list" [ref=e4]
  - region "Discover all the features of your Proton account" [ref=e10] [cursor=pointer]:
    - checkbox [ref=e12]
    - generic [ref=e13]:
      - button "Star conversation" [ref=e14]
      - generic [ref=e15]:
        - generic [ref=e16]:
          - img [ref=e17]
          - generic [ref=e18]: Unread email
        - generic "no-reply@proton.me" [ref=e19]:
          - generic [ref=e20]: Proton
          - generic [ref=e21]: Official
      - heading "Discover all the features of your Proton account" [level=2] [ref=e22]
      - generic [ref=e23]:
        - generic [ref=e24]:
          - img [ref=e25]
          - generic [ref=e26]: Has 3 attachments (150 KB)
        - generic [ref=e27]:
          - time [ref=e28]: Yesterday
  - region "Project notes" [ref=e11] [cursor=pointer]:
    - checkbox [ref=e30]
    - generic [ref=e31]:
      - button "Star conversation" [ref=e32]
      - generic "alice@example.com" [ref=e35]:
        - generic [ref=e36]: Alice
      - heading "Project notes" [level=2] [ref=e38]
      - generic [ref=e40]:
        - time [ref=e41]: 2 days
"""

READ_SNAPSHOT = """
- main "Proton Mail" [ref=e1]
  - heading "Project notes" [ref=e2]
  - text "From: Alice <alice@example.com>" [ref=e3]
  - text "To: David <david@example.com>" [ref=e4]
  - text "Date: Jun 11, 2026, 10:00 AM" [ref=e5]
  - article [ref=e6]
    - text "The meeting is at noon." [ref=e7]
"""


def test_mailbox_visible() -> None:
    assert is_mailbox_visible(INBOX_SNAPSHOT) is True


def test_mailbox_visible_with_password_mention() -> None:
    snapshot = """
- main "Proton Mail" [ref=e1]
  - button "Compose" [ref=e2]
  - listitem "Proton Reset your password Yesterday" [ref=e10]
"""
    assert is_mailbox_visible(snapshot) is True


def test_login_form_not_mailbox() -> None:
    snapshot = """
- main "Proton Mail" [ref=e1]
  - textbox "Email or username" [ref=e2]
  - textbox "Password" [ref=e3]
"""
    assert is_mailbox_visible(snapshot) is False


def test_account_page_not_mailbox() -> None:
    snapshot = """
- generic [ref=e1]:
  - paragraph [ref=e2]: Loading Proton Account
  - link "Proton Mail" [ref=e3]
  - generic "Proton Calendar" [ref=e4]
  - generic "Proton Drive" [ref=e5]
"""
    assert is_mailbox_visible(snapshot) is False


def test_find_first_ref() -> None:
    assert find_first_ref(INBOX_SNAPSHOT, [re.compile(r'button "Compose"', re.I)]) == "e2"
    assert find_first_ref(INBOX_SNAPSHOT, [re.compile(r'button "Refresh"', re.I)]) == "e3"


def test_recovery_message_row_can_require_visible_unread_state() -> None:
    unread = """
- region "4 messages in conversation Verify your recovery email" [ref=e10] [cursor=pointer]:
  - checkbox [ref=e9]
  - generic [ref=e11]:
    - generic [ref=e12]: Unread email
    - generic "no-reply@verify.proton.me" [ref=e13]: Proton
    - heading "4 messages in conversation Verify your recovery email" [ref=e14]
"""
    read = unread.replace("- generic [ref=e12]: Unread email\n", "")

    assert find_recovery_message_row_ref(unread, require_unread=True) == "e10"
    assert find_recovery_message_row_ref(read, require_unread=True) is None
    assert find_recovery_message_row_ref(read) == "e10"
    assert find_recovery_message_checkbox_ref(unread, require_unread=True) == "e9"
    assert find_recovery_message_checkbox_ref(read, require_unread=True) is None
    assert find_recovery_message_open_ref(unread, require_unread=True) == "e14"
    assert find_mark_as_read_ref('- button "Mark as read" [ref=e20]') == "e20"


def test_find_mailbox_inbox_ref() -> None:
    assert (
        find_mailbox_inbox_ref(
            '- link "Inbox 3 unread conversations" [ref=e20] [cursor=pointer]'
        )
        == "e20"
    )


def test_find_collapsed_message_header_refs() -> None:
    snapshot = """
- article [active] [ref=e30]:
  - generic [ref=e31] [cursor=pointer]
  - iframe [ref=f1e5]:
    - link "Verify email" [ref=f1e6]
- article [ref=e40]:
  - generic [ref=e41] [cursor=pointer]
"""

    assert find_collapsed_message_header_refs(snapshot) == ["e41"]


def test_find_new_message_button() -> None:
    assert (
        find_first_ref(
            '- button "New message" [ref=e33]',
            [re.compile(r'button "Compose"', re.I), re.compile(r'button "New message"', re.I)],
        )
        == "e33"
    )


def test_keeps_iframe_refs() -> None:
    assert find_first_ref('- generic [ref=f17e8]: Sent with Proton Mail', [re.compile(r"generic", re.I)]) == "f17e8"


def test_composer_body_editor_ref() -> None:
    snapshot = """
- dialog "New message" [ref=e1]
  - textbox "Search messages" [ref=e2]
  - iframe [ref=e3]:
    - generic [ref=f17e8]:
      - text: Sent with
      - link "Proton Mail" [ref=f17e9]
  - button "Send" [ref=e4]
"""
    assert find_composer_body_editor_ref(snapshot) == "f17e8"


def test_recipient_field_ref() -> None:
    snapshot = """
- dialog "New message" [ref=e1]
  - combobox "Email address" [ref=e2]
"""
    assert find_recipient_field_ref(snapshot, "to") == "e2"
    assert find_recipient_field_ref(snapshot, "cc") is None
    assert find_recipient_field_ref(snapshot, "bcc") is None


def test_send_confirmation_ref() -> None:
    assert find_send_confirmation_ref('- button "Send" [ref=e1]') is None
    assert find_send_confirmation_ref('- button "Send anyway" [ref=e2]') == "e2"


def test_parse_inbox_snapshot() -> None:
    assert parse_inbox_snapshot(INBOX_SNAPSHOT, 5) == [
        InboxRow(
            row=1,
            handle="1",
            status="unread",
            starred=False,
            has_attachment=True,
            age="Yesterday",
            from_display="Proton",
            from_email="no-reply@proton.me",
            labels=["Official"],
            subject="Discover all the features of your Proton account",
            target="e10",
        ),
        InboxRow(
            row=2,
            handle="2",
            status="read",
            starred=False,
            has_attachment=False,
            age="2 days",
            from_display="Alice",
            from_email="alice@example.com",
            labels=[],
            subject="Project notes",
            target="e11",
        ),
    ]


def test_parse_region_inbox_rows() -> None:
    snapshot = """
- region "proton-cli e2e 123 abc" [ref=e307] [cursor=pointer]:
  - checkbox [ref=e309]
  - generic [ref=e313]:
    - button "Star conversation" [ref=e314]
    - generic "sender@example.com" [ref=e321]
    - heading "proton-cli e2e 123 abc" [level=2] [ref=e324]
    - generic [ref=e326]:
      - time [ref=e327]: 5:13 PM
"""
    rows = parse_inbox_snapshot(snapshot, 5)
    assert rows[0].handle == "1"
    assert rows[0].subject == "proton-cli e2e 123 abc"
    assert rows[0].target == "e307"
    assert rows[0].from_email == "sender@example.com"
    assert rows[0].age == "5:13 PM"


def test_non_email_regions_are_skipped() -> None:
    snapshot = """
- region "Message list" [ref=e1]:
  - heading "Conversation list 0 unread messages" [level=1] [ref=e2]
- region "Clickable but no email markers" [ref=e3] [cursor=pointer]:
  - generic [ref=e4]: some content
"""
    rows = parse_inbox_snapshot(snapshot, 5)
    assert rows == []


def test_parse_read_message() -> None:
    assert parse_read_message_snapshot(READ_SNAPSHOT) == ReadMessage(
        subject="Project notes",
        sender="Alice <alice@example.com>",
        to=["David <david@example.com>"],
        cc=[],
        date="Jun 11, 2026, 10:00 AM",
        body="The meeting is at noon.",
    )


def test_skips_shell_headings_for_subject() -> None:
    snapshot = """
- heading "Proton Mail" [level=1] [ref=e10]
- heading "Navigation" [level=2] [ref=e27]
- heading "More" [level=3] [ref=e32]
- heading "Views" [level=3] [ref=e40]
- heading "Folders" [level=3] [ref=e56]
- heading "Labels" [level=3] [ref=e73]
- heading "Get storage bonus" [level=3] [ref=e254]
- heading "proton-cli e2e 1781243105177 76115fe0-321" [level=1] [ref=e428]
- article [ref=e431]
  - generic [ref=f73e5]: proton-cli self-send/readback body 76115fe0-321
"""
    message = parse_read_message_snapshot(snapshot)
    assert message.subject == "proton-cli e2e 1781243105177 76115fe0-321"
    assert message.body == "proton-cli self-send/readback body 76115fe0-321"


WELCOME_SNAPSHOT = """
- main [ref=e1]:
  - button "New message" [ref=e2]
  - button "Maybe later" [ref=e3]
  - dialog [active] [ref=e4]:
    - heading "Welcome to Proton Mail" [level=1] [ref=e5]
    - button "Let's get started" [ref=e6]
"""


def test_welcome_dialog_visible() -> None:
    assert is_welcome_dialog_visible(WELCOME_SNAPSHOT) is True


def test_welcome_dialog_not_visible_without_active_dialog() -> None:
    snapshot = """
- main [ref=e1]:
  - heading "Welcome to Proton Mail" [level=1] [ref=e5]
"""
    assert is_welcome_dialog_visible(snapshot) is False


def test_welcome_dialog_not_visible_without_known_button() -> None:
    snapshot = """
- main [ref=e1]:
  - dialog [active] [ref=e4]:
    - heading "Welcome to Proton Mail" [level=1] [ref=e5]
    - button "Unknown" [ref=e6]
"""
    assert is_welcome_dialog_visible(snapshot) is False


def test_find_welcome_button_ref_prefers_dialog_over_background() -> None:
    # Background "Maybe later" (e3) must be ignored in favor of the dialog button.
    snapshot = """
- main [ref=e1]:
  - button "Maybe later" [ref=e3]
  - dialog [active] [ref=e4]:
    - heading "Welcome to Proton Mail" [level=1] [ref=e5]
    - button "Maybe later" [ref=e7]
"""
    assert find_welcome_button_ref(snapshot) == "e7"


def test_find_welcome_button_ref_dismiss_before_advance() -> None:
    snapshot = """
- dialog [active] [ref=e4]:
  - heading "Distraction-free emailing" [level=1] [ref=e5]
  - button "Download the desktop app" [ref=e6]
  - button "Maybe later" [ref=e7]
  - button "Next" [ref=e8]
"""
    assert find_welcome_button_ref(snapshot) == "e7"


def test_find_welcome_button_ref_none_when_no_known_button() -> None:
    snapshot = """
- dialog [active] [ref=e4]:
  - heading "Welcome to Proton Mail" [level=1] [ref=e5]
  - button "Unknown" [ref=e6]
"""
    assert find_welcome_button_ref(snapshot) is None
