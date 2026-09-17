"""Tests for background action coordination and lifecycle gating."""

from __future__ import annotations

import threading
import time
from typing import Any

from stream_monitor.action_coordinator import ActionCoordinator
from stream_monitor.action_executor import ActionResult, ActionStatus
from stream_monitor.action_plan import action_plan_for
from stream_monitor.fetcher.base import StreamInfo


def _info() -> StreamInfo:
    return StreamInfo(
        channel="hello",
        platform="twitch",
        is_live=True,
        title="Live",
        url="https://www.twitch.tv/hello",
    )


def _coordinator(
    runner,
    *,
    scheduled: list[Any] | None = None,
    lifecycle: list[str] | None = None,
    current_generation: list[int] | None = None,
    max_pending: int = 32,
) -> ActionCoordinator:
    scheduled = scheduled if scheduled is not None else []
    lifecycle = lifecycle if lifecycle is not None else []
    current_generation = current_generation or [1]
    return ActionCoordinator(
        runner=runner,
        schedule_ui=lambda callback: scheduled.append(callback),
        generation_is_current=lambda generation: generation == current_generation[0],
        on_stop=lambda: lifecycle.append("stop"),
        on_exit=lambda: lifecycle.append("exit"),
        max_pending=max_pending,
    )


def test_stale_action_is_discarded_before_worker_starts() -> None:
    calls: list[str] = []
    coordinator = _coordinator(
        lambda action, **_kwargs: calls.append(action)
        or ActionResult.completed(action),
        current_generation=[2],
    )

    assert coordinator.submit(
        action_plan_for("notify_only"), _info(), None, generation=1
    ) is False
    assert calls == []


def test_only_one_terminal_action_can_win_per_generation() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def runner(_action, _info, **_kwargs):
        started.set()
        release.wait(timeout=2)
        finished.set()
        return ActionResult.completed("open_and_stop")

    coordinator = _coordinator(runner)
    plan = action_plan_for("open_and_stop")

    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert started.wait(timeout=1)
    assert coordinator.submit(plan, _info(), None, generation=1) is False

    release.set()
    assert finished.wait(timeout=1)
    assert coordinator.submit(plan, _info(), None, generation=1) is False


def test_failed_terminal_action_releases_generation_claim() -> None:
    finished = threading.Event()
    results = iter(
        [
            ActionResult.failed("open_and_exit", ActionStatus.EXECUTION_FAILED),
            ActionResult.completed("open_and_exit"),
        ]
    )

    def runner(_action, _info, **_kwargs):
        result = next(results)
        finished.set()
        return result

    coordinator = _coordinator(runner)
    plan = action_plan_for("open_and_exit")

    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert finished.wait(timeout=1)
    deadline = time.monotonic() + 1
    accepted = False
    while time.monotonic() < deadline:
        if coordinator.submit(plan, _info(), None, generation=1):
            accepted = True
            break
        time.sleep(0.01)
    assert accepted is True


def test_new_generation_can_claim_terminal_action_after_previous_success() -> None:
    current_generation = [1]
    finished = threading.Event()

    def runner(_action, _info, **_kwargs):
        finished.set()
        return ActionResult.completed("open_and_stop")

    coordinator = _coordinator(runner, current_generation=current_generation)
    plan = action_plan_for("open_and_stop")
    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert finished.wait(timeout=1)

    current_generation[0] = 2
    assert coordinator.submit(plan, _info(), None, generation=2) is True


def test_lifecycle_callback_is_scheduled_only_when_runner_requests_it() -> None:
    finished = threading.Event()
    scheduled: list[Any] = []
    lifecycle: list[str] = []
    captured: list[Any] = []

    def runner(_action, _info, *, stop_fn, **_kwargs):
        captured.append(stop_fn)
        stop_fn()
        finished.set()
        return ActionResult.completed("open_and_stop")

    coordinator = _coordinator(
        runner,
        scheduled=scheduled,
        lifecycle=lifecycle,
    )
    plan = action_plan_for("open_and_stop")
    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert finished.wait(timeout=1)
    assert captured
    assert len(scheduled) == 1

    scheduled[0]()
    assert lifecycle == ["stop"]


def test_shutdown_blocks_new_actions_and_scheduled_lifecycle() -> None:
    scheduled: list[Any] = []
    lifecycle: list[str] = []
    finished = threading.Event()

    def runner(_action, _info, *, stop_fn, **_kwargs):
        stop_fn()
        finished.set()
        return ActionResult.completed("open_and_stop")

    coordinator = _coordinator(
        runner,
        scheduled=scheduled,
        lifecycle=lifecycle,
    )
    plan = action_plan_for("open_and_stop")
    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert finished.wait(timeout=1)
    coordinator.shutdown()

    assert coordinator.submit(plan, _info(), None, generation=1) is False
    assert len(scheduled) == 1
    scheduled[0]()
    assert lifecycle == []


def test_failed_structured_result_releases_terminal_claim() -> None:
    coordinator = _coordinator(
        lambda action, **_kwargs: ActionResult.failed(
            action.key, ActionStatus.EXECUTION_FAILED
        )
    )
    plan = action_plan_for("open_and_stop")

    assert coordinator.submit(plan, _info(), None, generation=1) is True

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        # A failed terminal action releases its claim so a later event can
        # recover within the same monitor generation.
        if coordinator.submit(plan, _info(), None, generation=1):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("failed action did not release its claim")


def test_cancel_generation_reaches_action_runner() -> None:
    seen: list[bool] = []
    started = threading.Event()
    allow = threading.Event()
    finished = threading.Event()

    def runner(_action, _info, *, is_cancelled, **_kwargs):
        started.set()
        allow.wait(timeout=1)
        seen.append(is_cancelled())
        finished.set()
        return ActionResult.failed("open_and_stop", ActionStatus.CANCELLED)

    current_generation = [1]
    coordinator = _coordinator(runner, current_generation=current_generation)
    plan = action_plan_for("open_and_stop")
    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert started.wait(timeout=1)
    coordinator.cancel_generation(1)
    allow.set()

    assert finished.wait(timeout=1)
    assert seen == [True]


def test_duplicate_pending_action_is_coalesced() -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(_action, _info, **_kwargs):
        started.set()
        release.wait(timeout=1)
        return ActionResult.completed("notify_only")

    coordinator = _coordinator(runner)
    plan = action_plan_for("notify_only")
    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert started.wait(timeout=1)
    assert coordinator.submit(plan, _info(), None, generation=1) is False

    release.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if coordinator.submit(plan, _info(), None, generation=1):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("completed action was not removed from pending set")


def test_pending_queue_has_a_bounded_limit() -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(_action, _info, **_kwargs):
        started.set()
        release.wait(timeout=1)
        return ActionResult.completed("notify_only")

    coordinator = _coordinator(runner, max_pending=1)
    plan = action_plan_for("notify_only")
    assert coordinator.submit(plan, _info(), None, generation=1) is True
    assert started.wait(timeout=1)

    second_info = StreamInfo(
        channel="other",
        platform="twitch",
        is_live=True,
        title="Live",
        url="https://www.twitch.tv/other",
    )
    assert coordinator.submit(plan, second_info, None, generation=1) is False
    release.set()
