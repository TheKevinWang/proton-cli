"""Entry point and argument parsing for proton-cli."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from proton_cli.app import RunCliOptions, run_cli
from proton_cli.errors import CliError, redact_text, to_cli_error
from proton_cli.output import error_output


def _emit_error(error: CliError, wants_json: bool) -> int:
    if wants_json:
        print(error_output(error), file=sys.stdout)
    else:
        print(redact_text(str(error)), file=sys.stderr)
    return error.exit_code


async def dispatch(argv: list[str]) -> int:
    wants_json = "--json" in argv
    try:
        result = await run_cli(
            RunCliOptions(
                argv=argv,
                cwd=Path.cwd(),
                env=dict(os.environ),
            )
        )
    except CliError as exc:
        return _emit_error(exc, wants_json)
    except Exception as exc:  # noqa: BLE001
        return _emit_error(to_cli_error(exc), wants_json)
    else:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.exit_code


def main() -> None:
    exit_code = asyncio.run(dispatch(sys.argv[1:]))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
