"""Verify discovery against isolated Chromium processes, never user profiles."""

import os
import shutil
import subprocess
import time

import pytest

from stream_monitor.cdp_client import (
    CdpClient,
    CdpPortRegistry,
    acquire_cdp_debugging_port,
    allocate_debugging_port,
    endpoint_belongs_to_profile,
    probe_cdp_endpoint,
    resolve_cdp_attach,
)
from stream_monitor.notifier import _resolve_browser_executable


@pytest.fixture
def chrome_processes(caplog):
    caplog.set_level("DEBUG", logger="stream_monitor.cdp_client")
    executable = _resolve_browser_executable("chrome")
    if not shutil.which(executable):
        pytest.skip("Chrome not available for isolated CDP integration")
    processes = []

    def launch(profile, *, debugging=True, port=0):
        args = [
            executable, "--headless=new", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
        ]
        if debugging:
            args += [f"--remote-debugging-port={port}", "--remote-allow-origins=*"]
        args.append("about:blank")
        process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        processes.append(process)
        return process

    yield launch
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_concurrent_cold_openers_resolve_one_shared_profile_endpoint(
    tmp_path, chrome_processes,
):
    profile = tmp_path / "shared"
    registry = CdpPortRegistry()
    preparations = [acquire_cdp_debugging_port(str(profile), registry=registry) for _ in range(2)]
    assert all(result.ok and result.port == 0 for result in preparations)
    chrome_processes(profile)
    chrome_processes(profile)
    results = [resolve_cdp_attach(str(profile)) for _ in range(2)]
    assert all(result.ok for result in results), results
    assert results[0].port == results[1].port
    warm = acquire_cdp_debugging_port(str(profile), registry=registry)
    assert warm.ok and warm.port == results[0].port
    assert not warm.pass_debugging_flag
    client = CdpClient()
    try:
        client.connect(warm.port, "about:blank", timeout=5)
        assert client.evaluate("1 + 1") == 2
    finally:
        client.close()


def test_stale_file_cannot_attach_to_other_browser_instance(tmp_path, chrome_processes):
    profile = tmp_path / "actual"
    chrome_processes(profile)
    result = resolve_cdp_attach(str(profile))
    assert result.ok
    stale = tmp_path / "stale"
    stale.mkdir()
    (stale / "DevToolsActivePort").write_text(
        f"{result.port}\n/devtools/browser/old-instance\n", encoding="utf-8",
    )
    assert not endpoint_belongs_to_profile(result.port, str(stale))
    assert endpoint_belongs_to_profile(result.port, str(profile))


def test_existing_nondebug_browser_has_bounded_discovery(tmp_path, chrome_processes):
    profile = tmp_path / "nondebug"
    process = chrome_processes(profile, debugging=False)
    result = resolve_cdp_attach(str(profile), timeout=1, profile_was_busy=True)
    assert not result.ok and result.reason == "profile_busy"
    assert process.poll() is None
    assert not (profile / "DevToolsActivePort").exists()
    if os.name == "nt":
        preflight = acquire_cdp_debugging_port(str(profile), registry=CdpPortRegistry())
        assert not preflight.ok and preflight.reason == "profile_nondebug"


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy listener ownership lookup")
def test_legacy_fixed_port_is_reused_only_by_its_owning_profile(tmp_path, chrome_processes):
    # Port selection here deliberately models the legacy fixed-port launcher.
    # Production cold launches use zero and have no handoff interval.
    port = allocate_debugging_port()
    owner_profile = tmp_path / "owner"
    chrome_processes(owner_profile, port=port)
    deadline = time.monotonic() + 10
    while not probe_cdp_endpoint(port, timeout=.2) and time.monotonic() < deadline:
        time.sleep(.1)
    assert probe_cdp_endpoint(port)
    owner = acquire_cdp_debugging_port(str(owner_profile), registry=CdpPortRegistry())
    assert owner.ok and owner.port == port and owner.reason == "process"
    assert not owner.pass_debugging_flag

    other_profile = tmp_path / "other profile"
    chrome_processes(other_profile, port=port)
    deadline = time.monotonic() + 5
    while not other_profile.exists() and time.monotonic() < deadline:
        time.sleep(.1)
    other = acquire_cdp_debugging_port(str(other_profile), registry=CdpPortRegistry())
    assert not other.ok and other.reason == "profile_nondebug"
