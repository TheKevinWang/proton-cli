# Repository guidance

`proton-cli` is a typed Python CLI and async API that drives the visible Proton
Mail web interface through the released Zendriver package.

- Use red/green TDD.
- New source must pass `uv run mypy proton_cli` under strict mode.
- Run `uv run ruff check` and `uv run pytest` before submitting changes.
- Use only the public `zendriver` API. Do not add local path dependencies or
  imports from private Zendriver extensions.
- Never commit browser profiles, sessions, traces, credentials, or real mailbox
  contents.
- Do not implement challenge bypasses or direct Proton internal API mutations.
