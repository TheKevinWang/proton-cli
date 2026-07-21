# proton-cli

`proton-cli` is an unofficial typed Python CLI and async library that controls
Proton Mail through the visible web interface using a real Chrome browser. It
supports login, inbox and Sent listings, refresh, message reading, sending,
and browser-session lifecycle operations.

This project is not affiliated with, endorsed by, or sponsored by Proton AG.
Proton is a trademark of its respective owner.

## Public backend

This public distribution uses the released `zendriver` package directly. It
walks Chrome's public CDP frame tree and combines the accessibility tree for
the root document and same-target descendant frames. This supports ordinary
iframe-hosted message bodies and composer editors without private Zendriver
extensions.

Chrome can isolate some cross-origin or sandboxed frames into separate CDP
targets. The released backend does not currently merge those out-of-process
targets, so their content may be absent: message reads can be incomplete and a
send will fail body verification rather than claim success. The private
development backend has broader multi-target support. Playwright-compatible
`--trace` capture is also unavailable here and returns `TRACE_UNAVAILABLE`.

Direct browser traffic is the default. To use a SOCKS5 proxy, pass
`--proxy socks5://HOST:PORT` as a global option.

## Install and run

Python 3.10 or newer and Chrome or Chromium are required.

    pip install proton-cli
    proton-cli --help
    proton-cli login --email USER@example.com --password-env PROTON_PASSWORD
    proton-cli inbox --limit 10
    proton-cli read 1

For local development:

    uv sync
    uv run mypy proton_cli
    uv run ruff check
    uv run pytest

## Python API

    from proton_cli import ProtonClient

    async with ProtonClient(session="default") as proton:
        rows = await proton.inbox(limit=10)
        message = await proton.read(rows[0].handle)

## Privacy and safety

Browser profiles and session records contain authenticated browser state and
recent mailbox metadata. Default managed profiles are stored below the
application-data directory with private POSIX permissions (Windows relies on
the selected directory's inherited ACL). `proton-cli browser close` removes
these profiles. A directory supplied through `--chrome-data-dir` is user-owned
and is never removed automatically.

While a managed browser is running, proton-cli keeps a Chrome DevTools endpoint
on loopback so later CLI processes can reconnect. CDP has no authentication;
other processes, and potentially other users on a shared machine, can control
that logged-in browser. Use proton-cli only on a trusted single-user machine,
and close the browser when finished.

Prefer `--password-env` to a password command-line argument because arguments
may be stored in shell history or exposed to process inspection.

The tool does not solve or bypass captcha, passkey, two-factor, or recovery
challenges. Complete manual challenges in the visible browser. Do not use this
project for bulk account operation, spam, abusive traffic, or attempts to evade
service security controls.

## License

The `proton-cli` source is available under the MIT License. Zendriver is a
separately distributed dependency with its own license; see
`THIRD_PARTY_NOTICES.md`.
