"""Execute an action plan through platform ports."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from stream_monitor.action_plan import ActionPlan, LifecycleEffect
from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.platform_ports import ActionPorts

logger = logging.getLogger(__name__)

ActionCallback = Callable[[], None]
CancellationCheck = Callable[[], bool]


class ActionStatus(StrEnum):
    """Stable result categories exposed to coordination and diagnostics."""

    COMPLETED = "completed"
    NOTIFICATION_FAILED = "notification_failed"
    BROWSER_FAILED = "browser_failed"
    EXECUTION_FAILED = "execution_failed"
    LIFECYCLE_FAILED = "lifecycle_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of one plan, including which side-effects actually worked."""

    status: ActionStatus
    action: str
    notification_sent: bool | None = None
    browser_opened: bool | None = None
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        """Whether the plan completed its required effect."""
        return self.status == ActionStatus.COMPLETED

    @classmethod
    def completed(
        cls,
        action: str,
        *,
        notification_sent: bool | None = None,
        browser_opened: bool | None = None,
    ) -> "ActionResult":
        return cls(
            status=ActionStatus.COMPLETED,
            action=action,
            notification_sent=notification_sent,
            browser_opened=browser_opened,
        )

    @classmethod
    def failed(
        cls,
        action: str,
        status: ActionStatus,
        *,
        notification_sent: bool | None = None,
        browser_opened: bool | None = None,
        detail: str = "",
    ) -> "ActionResult":
        return cls(
            status=status,
            action=action,
            notification_sent=notification_sent,
            browser_opened=browser_opened,
            detail=detail,
        )


class ActionExecutor:
    """Translate one immutable plan into notification/browser side-effects."""

    def __init__(
        self,
        *,
        ports: ActionPorts,
    ) -> None:
        self._ports = ports

    def execute(
        self,
        plan: ActionPlan,
        info: StreamInfo,
        *,
        stop_fn: ActionCallback | None = None,
        exit_fn: ActionCallback | None = None,
        browser_settings: BrowserSettings | dict[str, Any] | None = None,
        is_cancelled: CancellationCheck | None = None,
    ) -> ActionResult:
        """Run a plan and return a structured side-effect result."""
        cancelled = is_cancelled or (lambda: False)
        if cancelled():
            return ActionResult.failed(plan.key, ActionStatus.CANCELLED)

        notification_sent: bool | None = None
        if plan.notify:
            try:
                # Legacy notification implementations returned None on
                # success. Only an explicit False means that the port failed.
                notification_sent = (
                    self._ports.notification.send(
                        info, with_open_button=not plan.open_browser
                    )
                    is not False
                )
            except Exception:
                notification_sent = False
                logger.exception(
                    "Notification effect failed: action=%s platform=%s channel=%s",
                    plan.key,
                    info.platform,
                    info.channel,
                )

        if cancelled():
            return ActionResult.failed(
                plan.key,
                ActionStatus.CANCELLED,
                notification_sent=notification_sent,
            )

        if not plan.open_browser:
            if notification_sent is False:
                return ActionResult.failed(
                    plan.key,
                    ActionStatus.NOTIFICATION_FAILED,
                    notification_sent=False,
                )
            return ActionResult.completed(
                plan.key,
                notification_sent=notification_sent,
            )

        try:
            opened = self._ports.browser.open(info, browser_settings)
        except Exception:
            opened = False
            logger.exception(
                "Browser effect raised: action=%s platform=%s channel=%s",
                plan.key,
                info.platform,
                info.channel,
            )
        if cancelled():
            return ActionResult.failed(
                plan.key,
                ActionStatus.CANCELLED,
                notification_sent=notification_sent,
                browser_opened=bool(opened),
            )
        if not opened:
            if plan.notify_on_open_failure:
                # Preserve a recovery path when the configured browser cannot
                # start. The notification layer may provide an open button.
                try:
                    self._ports.notification.send(info, with_open_button=True)
                except Exception:
                    logger.exception("Failed to show browser-failure notification")
            logger.warning(
                "Browser effect failed: action=%s platform=%s channel=%s",
                plan.key,
                info.platform,
                info.channel,
            )
            return ActionResult.failed(
                plan.key,
                ActionStatus.BROWSER_FAILED,
                notification_sent=notification_sent,
                browser_opened=False,
            )

        lifecycle_callback = {
            LifecycleEffect.STOP_MONITOR: stop_fn,
            LifecycleEffect.EXIT_APP: exit_fn,
        }.get(plan.after_open)
        if plan.is_terminal and lifecycle_callback is None:
            logger.error(
                "Lifecycle effect has no callback: action=%s effect=%s",
                plan.key,
                plan.after_open,
            )
            return ActionResult.failed(
                plan.key,
                ActionStatus.LIFECYCLE_FAILED,
                notification_sent=notification_sent,
                browser_opened=True,
                detail="missing lifecycle callback",
            )
        if lifecycle_callback is not None:
            lifecycle_callback()
        if notification_sent is False:
            logger.warning(
                "Browser opened but notification failed: action=%s platform=%s "
                "channel=%s",
                plan.key,
                info.platform,
                info.channel,
            )
            return ActionResult.failed(
                plan.key,
                ActionStatus.NOTIFICATION_FAILED,
                notification_sent=False,
                browser_opened=True,
                detail="browser opened; notification failed",
            )
        return ActionResult.completed(
            plan.key,
            notification_sent=notification_sent,
            browser_opened=True,
        )
