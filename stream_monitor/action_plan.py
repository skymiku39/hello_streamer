"""Pure action plans shared by policy and execution layers.

An action key used to carry several independent meanings through the codebase:
whether to notify, whether to open a browser, and what to do after a successful
launch.  ``ActionPlan`` makes that contract explicit while retaining the
existing string keys at the configuration boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class LifecycleEffect(StrEnum):
    """What the application should do after a browser launch succeeds."""

    NONE = "none"
    STOP_MONITOR = "stop_monitor"
    EXIT_APP = "exit_app"


@dataclass(frozen=True, slots=True)
class TriggerSettings:
    """Independent user-facing trigger controls.

    The old configuration stored four pre-combined action names.  Runtime code
    now consumes these orthogonal settings so notification, browser launch, and
    the follow-up lifecycle effect cannot accidentally imply one another.
    ``notify_on_open_failure`` is deliberately separate: it is an operational
    recovery alert, not a duplicate of the normal live-event notification.
    """

    notify_on_live: bool = True
    open_on_live: bool = True
    after_open: LifecycleEffect = LifecycleEffect.STOP_MONITOR
    notify_on_upcoming: bool = True
    open_on_upcoming: bool = False
    notify_on_open_failure: bool = True

    @classmethod
    def from_mapping(cls, value: Any) -> "TriggerSettings":
        """Coerce a persisted mapping without allowing invalid enum values."""
        if not isinstance(value, dict):
            return cls()

        defaults = cls()
        effect = value.get("after_open", defaults.after_open.value)
        try:
            effect = LifecycleEffect(effect)
        except (TypeError, ValueError):
            effect = defaults.after_open

        def boolean(key: str, default: bool) -> bool:
            raw = value.get(key, default)
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, int) and raw in (0, 1):
                return bool(raw)
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off", ""}:
                    return False
            return default

        return cls(
            notify_on_live=boolean("notify_on_live", defaults.notify_on_live),
            open_on_live=boolean("open_on_live", defaults.open_on_live),
            after_open=effect,
            notify_on_upcoming=boolean(
                "notify_on_upcoming", defaults.notify_on_upcoming
            ),
            open_on_upcoming=boolean(
                "open_on_upcoming", defaults.open_on_upcoming
            ),
            notify_on_open_failure=boolean(
                "notify_on_open_failure", defaults.notify_on_open_failure
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "notify_on_live": self.notify_on_live,
            "open_on_live": self.open_on_live,
            "after_open": self.after_open.value,
            "notify_on_upcoming": self.notify_on_upcoming,
            "open_on_upcoming": self.open_on_upcoming,
            "notify_on_open_failure": self.notify_on_open_failure,
        }


@dataclass(frozen=True)
class ActionPlan:
    """A validated, immutable description of one live-event action."""

    key: str
    notify: bool
    open_browser: bool
    after_open: LifecycleEffect = LifecycleEffect.NONE
    notify_on_open_failure: bool = True

    @property
    def is_terminal(self) -> bool:
        """Whether this action changes the monitor/application lifecycle."""
        return self.after_open is not LifecycleEffect.NONE


ACTION_ORDER = (
    "open_and_stop",
    "open_and_keep",
    "notify_only",
    "open_and_exit",
)

_ACTION_PLANS: dict[str, ActionPlan] = {
    "open_and_stop": ActionPlan(
        key="open_and_stop",
        notify=True,
        open_browser=True,
        after_open=LifecycleEffect.STOP_MONITOR,
    ),
    "open_and_keep": ActionPlan(
        key="open_and_keep",
        notify=True,
        open_browser=True,
    ),
    "notify_only": ActionPlan(
        key="notify_only",
        notify=True,
        open_browser=False,
    ),
    "open_and_exit": ActionPlan(
        key="open_and_exit",
        notify=True,
        open_browser=True,
        after_open=LifecycleEffect.EXIT_APP,
    ),
}

ACTION_KEYS = frozenset(ACTION_ORDER)


def action_plan_for(action: str) -> ActionPlan | None:
    """Return a validated plan for a configured action key."""
    return _ACTION_PLANS.get((action or "").strip())


def effective_action(configured_action: str, stream_status: str) -> str | None:
    """Narrow the configured action by the concrete stream status."""
    status = stream_status or "live"
    if status == "upcoming":
        return "notify_only"
    if status == "video":
        return None
    return configured_action


def action_plan_for_status(
    configured_action: str,
    stream_status: str,
) -> ActionPlan | None:
    """Build a validated plan after applying status-specific policy."""
    action = effective_action(configured_action, stream_status)
    return action_plan_for(action) if action is not None else None


def action_plan_for_trigger(
    settings: TriggerSettings | dict[str, Any] | None,
    stream_status: str,
) -> ActionPlan | None:
    """Build a plan from independent trigger settings.

    Upcoming streams never inherit the live stream's stop/exit effect.  They
    may optionally open a waiting-room URL, but doing so is always non-terminal.
    Plain videos remain informationally ignored by the trigger path.
    """
    if not isinstance(settings, TriggerSettings):
        settings = TriggerSettings.from_mapping(settings)
    status = stream_status or "live"
    if status == "video":
        return None
    if status == "upcoming":
        notify = settings.notify_on_upcoming
        open_browser = settings.open_on_upcoming
        return _build_plan(
            notify=notify,
            open_browser=open_browser,
            after_open=LifecycleEffect.NONE,
            notify_on_open_failure=settings.notify_on_open_failure,
        )
    return _build_plan(
        notify=settings.notify_on_live,
        open_browser=settings.open_on_live,
        after_open=(
            settings.after_open if settings.open_on_live else LifecycleEffect.NONE
        ),
        notify_on_open_failure=settings.notify_on_open_failure,
    )


def _build_plan(
    *,
    notify: bool,
    open_browser: bool,
    after_open: LifecycleEffect,
    notify_on_open_failure: bool,
) -> ActionPlan | None:
    """Create a stable diagnostic key for an independent plan."""
    if not notify and not open_browser:
        return None
    if open_browser:
        suffix = {
            LifecycleEffect.NONE: "keep",
            LifecycleEffect.STOP_MONITOR: "stop",
            LifecycleEffect.EXIT_APP: "exit",
        }[after_open]
        key = f"open_{suffix}" if not notify else f"notify_open_{suffix}"
    else:
        key = "notify_only"
    return ActionPlan(
        key=key,
        notify=notify,
        open_browser=open_browser,
        after_open=after_open,
        notify_on_open_failure=notify_on_open_failure,
    )
