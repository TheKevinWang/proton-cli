"""Example: use proton-cli as an importable Python library.

Run this after ``uv run proton-cli login --email ...`` so a browser session is
persisted. With no session the typed failure contract surfaces as ``CliError``
with a stable ``.code``.
"""

from __future__ import annotations

import asyncio

from proton_cli import CliError, ProtonClient


async def main() -> None:
    async with ProtonClient(session="default") as proton:
        try:
            rows = await proton.inbox(limit=10)
            for row in rows:
                print(row.row, row.from_display, row.subject)
            if rows:
                message = await proton.read(rows[0].handle)
                print(message.body)
        except CliError as exc:
            print(f"proton-cli error [{exc.code}]: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
