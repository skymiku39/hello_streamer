"""Profile ownership and cold-start regression tests for CDP discovery."""

import json
from io import BytesIO

import pytest

from stream_monitor import cdp_client
from stream_monitor.cdp_client import (
    CdpPortRegistry,
    acquire_cdp_debugging_port,
    normalize_profile_key,
    resolve_cdp_attach,
)


def test_cold_start_leaves_port_selection_to_browser(tmp_path):
    result = acquire_cdp_debugging_port(
        str(tmp_path / "fresh"), registry=CdpPortRegistry(),
        endpoint_probe=lambda *_a, **_k: False,
        reader=lambda _p: None, busy_check=lambda _p: False,
        process_lookup=lambda _p: None, profile_running=lambda _p: False,
    )
    try:
        assert result.ok
        assert result.port == 0
        assert result.pass_debugging_flag
        assert result.lease is None
    finally:
        if result.lease:
            result.lease.release()


def test_cached_port_cannot_override_current_profile_endpoint(tmp_path):
    registry = CdpPortRegistry()
    registry.put(normalize_profile_key(str(tmp_path)), 31111)
    result = acquire_cdp_debugging_port(
        str(tmp_path), registry=registry,
        endpoint_probe=lambda *_a, **_k: True,
        reader=lambda _p: 32222, busy_check=lambda _p: True,
        process_lookup=lambda _p: 32222, profile_running=lambda _p: True,
    )
    assert result.ok
    assert result.port == 32222
    assert not result.pass_debugging_flag


def test_failed_launch_does_not_reserve_a_fixed_port(tmp_path):
    registry = CdpPortRegistry()
    registry.put(normalize_profile_key(str(tmp_path)), 31111)
    result = acquire_cdp_debugging_port(
        str(tmp_path), registry=registry,
        endpoint_probe=lambda *_a, **_k: False,
        reader=lambda _p: None, busy_check=lambda _p: False,
        process_lookup=lambda _p: None, profile_running=lambda _p: False,
    )
    assert result.ok and result.port == 0
    assert registry.get(normalize_profile_key(str(tmp_path))) is None


@pytest.mark.parametrize("browser_path, expected", [
    ("/devtools/browser/current", True),
    ("/devtools/browser/unrelated", False),
    ("", False),
])
def test_recycled_port_requires_matching_browser_instance(
    tmp_path, monkeypatch, browser_path, expected,
):
    (tmp_path / "DevToolsActivePort").write_text(
        "32222\n/devtools/browser/current\n", encoding="utf-8",
    )
    monkeypatch.setattr(
        cdp_client.urllib.request, "urlopen",
        lambda *_a, **_k: BytesIO(json.dumps({
            "webSocketDebuggerUrl": f"ws://127.0.0.1:32222{browser_path}",
        }).encode()),
    )
    assert cdp_client.endpoint_belongs_to_profile(
        32222, str(tmp_path), endpoint_probe=lambda *_a, **_k: True,
    ) is expected


def test_resolve_waits_for_live_endpoint_after_partial_or_stale_file(tmp_path):
    now = [0.0]
    reads = iter([None, 31111, 32222])
    result = resolve_cdp_attach(
        str(tmp_path), timeout=1,
        reader=lambda _p: next(reads),
        profile_verifier=lambda port, *_a, **_k: port == 32222,
        clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert result.ok and result.port == 32222
    assert now[0] == pytest.approx(.4)


def test_resolve_is_bounded_when_no_verified_endpoint_exists(tmp_path):
    attempts = []
    result = resolve_cdp_attach(
        str(tmp_path), timeout=.5, reader=lambda _p: 31111,
        profile_verifier=lambda *_a, **_k: attempts.append(1) or False,
        clock=lambda: 0, sleep=lambda _seconds: None,
    )
    assert not result.ok and result.reason == "timeout"
    assert len(attempts) <= 6


def test_running_profile_without_debugging_fails_without_selecting_port(tmp_path):
    now = [0.0]
    result = acquire_cdp_debugging_port(
        str(tmp_path), timeout=.5, registry=CdpPortRegistry(),
        endpoint_probe=lambda *_a, **_k: False, reader=lambda _p: None,
        busy_check=lambda _p: False, process_lookup=lambda _p: None,
        profile_running=lambda _p: True,
        clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert not result.ok and result.reason == "profile_nondebug"
    assert now[0] <= .6


@pytest.mark.parametrize("failure_stage", ["running", "listener"])
def test_unavailable_process_lookup_never_claims_profile_is_free(tmp_path, failure_stage):
    def unavailable(_path):
        raise TimeoutError("native lookup unavailable")

    result = acquire_cdp_debugging_port(
        str(tmp_path), registry=CdpPortRegistry(), reader=lambda _p: None,
        busy_check=lambda _p: True,
        profile_running=unavailable if failure_stage == "running" else lambda _p: True,
        process_lookup=unavailable,
    )
    assert not result.ok and result.reason == "discovery_unavailable"
    assert result.port is None and not result.pass_debugging_flag
