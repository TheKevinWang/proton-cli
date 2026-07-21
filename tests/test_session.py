"""Tests for the session store."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from proton_cli.app import Runtime, _close_core
from proton_cli.errors import CliError
from proton_cli.session import SessionState, SessionStore
from proton_cli.types import GlobalOptions


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path)


@pytest.mark.asyncio
async def test_persists_external_cdp_state(store: SessionStore, tmp_path: Path) -> None:
    state = SessionState(
        name="default",
        managed=False,
        mode="headed",
        profile_dir=str(tmp_path / "profiles" / "default"),
        attached_endpoint="http://localhost:9222",
        debug_endpoint="http://localhost:9222",
        account_email="user@example.com",
        last_successful_command_at="2026-06-11T00:00:00.000Z",
    )
    await store.save(state)
    loaded = await store.load("default")
    assert loaded is not None
    assert loaded.managed is False
    assert loaded.attached_endpoint == "http://localhost:9222"
    raw = (store.path_for("default")).read_text(encoding="utf-8")
    assert "password" not in raw

    session_dir = store.path_for("default").parent
    if os.name != "nt":
        assert stat.S_IMODE(session_dir.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(store.path_for("default").stat().st_mode) & 0o077 == 0


@pytest.mark.asyncio
async def test_persists_profile_ownership(store: SessionStore, tmp_path: Path) -> None:
    state = SessionState(
        name="default",
        managed=True,
        mode="headed",
        profile_dir=str(tmp_path / "profiles" / "default"),
        profile_owned=True,
    )

    await store.save(state)

    loaded = await store.load("default")
    assert loaded is not None
    assert loaded.profile_owned is True


def test_rejects_unsafe_session_names(store: SessionStore) -> None:
    with pytest.raises(ValueError, match="Invalid session name"):
        store.path_for("../outside")


@pytest.mark.asyncio
async def test_clear_idempotently(store: SessionStore) -> None:
    await store.clear("default")
    state = SessionState(
        name="default",
        managed=True,
        mode="headless",
        profile_dir="/tmp/profiles/default",
        browser_process_id=123,
        debug_endpoint="http://localhost:9222",
        last_successful_command_at="2026-06-11T00:00:00.000Z",
    )
    await store.save(state)
    await store.clear("default")
    loaded = await store.load("default")
    assert loaded is None


@pytest.mark.asyncio
async def test_close_removes_tool_owned_profile(
    store: SessionStore,
    tmp_path: Path,
    make_runtime: object,
    make_browser: object,
) -> None:
    owned_profile = tmp_path / "profiles" / "default"
    owned_profile.mkdir(parents=True)
    (owned_profile / "Cookies").write_text("synthetic", encoding="utf-8")
    await store.save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(owned_profile),
            profile_owned=True,
            browser_process_id=123,
            debug_endpoint="http://127.0.0.1:9333",
        )
    )
    browser = make_browser([])  # type: ignore[operator]
    runtime: Runtime = make_runtime(browser)  # type: ignore[operator]

    await _close_core(GlobalOptions(), False, runtime)

    assert not owned_profile.exists()
    assert await store.load("default") is None


@pytest.mark.asyncio
async def test_close_refuses_to_delete_owned_profile_outside_app_root(
    store: SessionStore,
    tmp_path: Path,
    make_runtime: object,
    make_browser: object,
) -> None:
    outside_profile = tmp_path.parent / f"{tmp_path.name}-outside-profile"
    outside_profile.mkdir()
    await store.save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(outside_profile),
            profile_owned=True,
            browser_process_id=123,
            debug_endpoint="http://127.0.0.1:9333",
        )
    )
    browser = make_browser([])  # type: ignore[operator]
    runtime: Runtime = make_runtime(browser)  # type: ignore[operator]

    with pytest.raises(CliError) as exc_info:
        await _close_core(GlobalOptions(), False, runtime)

    assert exc_info.value.code == "UNSAFE_PROFILE_DELETE"
    assert outside_profile.exists()
    assert await store.load("default") is not None
    outside_profile.rmdir()


@pytest.mark.asyncio
async def test_close_preserves_user_supplied_profile(
    store: SessionStore,
    tmp_path: Path,
    make_runtime: object,
    make_browser: object,
) -> None:
    supplied_profile = tmp_path / "custom-chrome-data"
    supplied_profile.mkdir()
    (supplied_profile / "Cookies").write_text("synthetic", encoding="utf-8")
    await store.save(
        SessionState(
            name="default",
            managed=True,
            mode="headed",
            profile_dir=str(supplied_profile),
            profile_owned=False,
            browser_process_id=123,
            debug_endpoint="http://127.0.0.1:9333",
        )
    )
    browser = make_browser([])  # type: ignore[operator]
    runtime: Runtime = make_runtime(browser)  # type: ignore[operator]

    await _close_core(GlobalOptions(), False, runtime)

    assert supplied_profile.exists()
    assert (supplied_profile / "Cookies").exists()
