"""Release defaults for the public proton-cli distribution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReleasePolicy:
    default_proxy: str | None
    require_proxy: bool


RELEASE_POLICY = ReleasePolicy(default_proxy=None, require_proxy=False)


def proxy_help_text() -> str:
    return "Optional SOCKS5 proxy URL for managed browsers."
