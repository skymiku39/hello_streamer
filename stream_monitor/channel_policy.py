"""Pure decision policy for per-channel side-effects.

Historically the "should we act on this channel?" decision was scattered across
``MonitorEventBridge.tick`` as a chain of ``if trigger and not monitor_only and
action == …`` predicates, mixing three orthogonal concepts:

* **mode** — the global monitor mode (``idle`` / ``trigger`` / ``watch``); only
  ``trigger`` performs side-effects.
* **monitor_only** — a per-channel flag that says "observe but never act".
* **action** — the configured trigger action, further narrowed by the stream
  status (upcoming → notify only, plain video → nothing).

This module centralises those rules into small, dependency-free pure functions
so the bridge only *applies* decisions and the rules can be unit-tested in
isolation. Keeping them pure (no I/O, no Tk, no Win32) is what lets the tricky
mode × monitor_only × action matrix be verified exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass

TRIGGER_MODE = "trigger"

# Actions that open a browser window (vs. ``notify_only`` which just toasts).
_OPENING_ACTIONS = frozenset(
    {"open_and_stop", "open_and_keep", "open_and_exit"}
)


def effective_action(configured_action: str, stream_status: str) -> str | None:
    """Narrow the configured action by the concrete stream status.

    * ``upcoming`` (waiting room / scheduled) → only notify, never open.
    * plain ``video`` (a normal upload, not a stream) → do nothing.
    * anything else (``live`` / empty) → the user's configured action.
    """
    status = stream_status or "live"
    if status == "upcoming":
        return "notify_only"
    if status == "video":
        return None
    return configured_action


@dataclass(frozen=True)
class LiveActionDecision:
    """What to do for a single went-live / upcoming event.

    ``action`` is ``None`` when nothing should happen; ``suppressed_reason`` then
    explains why (for logging / tests). ``opens_window`` distinguishes an
    open-browser action from a bare ``notify_only`` so the caller knows whether
    to run it off the UI thread. ``triggers_stop`` / ``triggers_exit`` fold in
    the auto-stop / auto-exit side-effects of the corresponding actions.
    """

    action: str | None
    opens_window: bool = False
    triggers_stop: bool = False
    triggers_exit: bool = False
    suppressed_reason: str = ""


def resolve_live_action(
    *,
    mode: str,
    monitor_only: bool,
    configured_action: str,
    stream_status: str,
) -> LiveActionDecision:
    """Decide the side-effect for one live/upcoming channel event."""
    if mode != TRIGGER_MODE:
        return LiveActionDecision(action=None, suppressed_reason="mode")
    if monitor_only:
        return LiveActionDecision(action=None, suppressed_reason="monitor_only")
    action = effective_action(configured_action, stream_status)
    if action is None:
        return LiveActionDecision(action=None, suppressed_reason="video")
    return LiveActionDecision(
        action=action,
        opens_window=action in _OPENING_ACTIONS,
        triggers_stop=action == "open_and_stop",
        triggers_exit=action == "open_and_exit",
    )


def should_close_on_offline(
    *,
    mode: str,
    monitor_only: bool,
    wake_verify_active: bool,
    close_on_offline: bool,
    tracking_available: bool,
) -> bool:
    """Whether a went-offline event should auto-close the tracked window.

    Suppressed during wake verification (post-sleep) because a stale cache can
    momentarily look offline before the confirming poll, and for monitor-only
    channels (observe but never act).
    """
    return (
        mode == TRIGGER_MODE
        and close_on_offline
        and tracking_available
        and not monitor_only
        and not wake_verify_active
    )


def should_prune_blank_tabs(
    *,
    mode: str,
    close_off_topic: bool,
    tracking_available: bool,
) -> bool:
    """Whether the post-poll blank-tab prune sweep should run."""
    return mode == TRIGGER_MODE and close_off_topic and tracking_available
