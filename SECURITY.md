# Security policy

Do not open a public issue containing credentials, authenticated session data,
mailbox content, browser profiles, or trace archives. Use the repository host's
private vulnerability-reporting feature. For GitHub releases, maintainers must
enable **Security → Private vulnerability reporting** before publishing. If the
private form is unavailable, open a content-free issue asking the maintainers
for a private contact channel; do not include vulnerability details.

The supported security line is the latest published version. Reports should
include the affected version, operating system, a minimal synthetic
reproduction, and the security impact. Remove all personal and account data.

## Local security boundary

The managed browser exposes an unauthenticated Chrome DevTools endpoint on the
loopback interface while it is running. Treat every local process and user that
can reach loopback as trusted. Shared or hostile multi-user machines are not a
supported security boundary. Run `proton-cli browser close` when finished; use
`--force` to remove a tool-owned profile after the browser has already exited.
