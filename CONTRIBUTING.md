# Contributing

Issues and pull requests are welcome. Changes should include focused tests and
must pass:

    uv run mypy proton_cli
    uv run ruff check
    uv run pytest

Do not include real account identifiers, mailbox contents, browser profiles,
session state, traces, screenshots, or credentials in reports or fixtures.
Use synthetic `example.com` addresses and minimal synthetic snapshots.

The maintainers also develop a private canonical repository. A merged public
change is imported there before the next public synchronization so it will not
be overwritten by a later release export.
