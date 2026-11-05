"""Read acknowledgements of native page claims; never submit reward requests."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ClaimResult:
    status: str
    code: str = ""
    balance: int | float | None = None


def parse_claim_response(body: Any) -> ClaimResult | None:
    entries = body if isinstance(body, list) else [body]
    accepted = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        errors = entry.get("errors") or []
        data = entry.get("data") or {}
        if not isinstance(data, dict) or ("claimCommunityPoints" not in data and data):
            continue
        claim = data.get("claimCommunityPoints")
        if isinstance(claim, dict) and claim.get("error"):
            errors = [claim["error"]]
        if errors:
            first = errors[0] if isinstance(errors, list) else errors
            extensions = first.get("extensions") if isinstance(first, dict) else None
            raw = extensions.get("code") if isinstance(extensions, dict) else None
            if raw is None and isinstance(first, dict):
                raw = first.get("code")
            code = raw if isinstance(raw, str) and raw.isidentifier() and len(raw) <= 80 else "platform_rejected"
            return ClaimResult("rejected", code)
        points = claim.get("currentPoints") if isinstance(claim, dict) else None
        balance = points.get("balance") if isinstance(points, dict) else None
        if (isinstance(balance, (int, float)) and not isinstance(balance, bool)
                and math.isfinite(balance) and balance >= 0):
            accepted = ClaimResult("accepted", balance=balance)
    return accepted


class ClaimAcknowledgements:
    """Keep only bounded request IDs; discard headers, cookies and variables."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, bool] = {}

    def begin(self, client: Any) -> None:
        self.reset()
        client.call("Network.enable", {"maxTotalBufferSize": 1048576, "maxResourceBufferSize": 65536})

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()

    def observe(self, method: str, params: dict[str, Any]) -> bool:
        """Called on the CDP reader thread. Never issue a synchronous CDP call."""
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return False
        if method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            if not isinstance(request, dict):
                return False
            endpoint = urlsplit(str(request.get("url", "")))
            if endpoint.scheme != "https" or endpoint.hostname != "gql.twitch.tv":
                return False
            raw = request.get("postData", "")
            if not isinstance(raw, str) or len(raw) > 65536:
                return False
            try:
                payload = json.loads(raw)
            except ValueError:
                return False
            entries = payload if isinstance(payload, list) else [payload]
            if not any(isinstance(e, dict) and e.get("operationName") == "ClaimCommunityPoints" for e in entries):
                return False
            with self._lock:
                if len(self._pending) >= 16:
                    self._pending.pop(next(iter(self._pending)))
                self._pending[request_id] = False
        elif method == "Network.loadingFinished":
            with self._lock:
                if request_id in self._pending:
                    self._pending[request_id] = True
                    return True
        elif method == "Network.loadingFailed":
            with self._lock:
                self._pending.pop(request_id, None)
        return False

    def poll(self, client: Any) -> ClaimResult | None:
        with self._lock:
            ready = [key for key, done in self._pending.items() if done]
            for key in ready:
                self._pending.pop(key)
        accepted = None
        for request_id in ready:
            try:
                reply = client.call("Network.getResponseBody", {"requestId": request_id})
                if reply.get("base64Encoded"):
                    continue
                raw = reply.get("body", "")
                if not isinstance(raw, str) or len(raw) > 65536:
                    continue
                result = parse_claim_response(json.loads(raw))
            except (OSError, RuntimeError, TimeoutError, ValueError):
                continue
            if result is not None and result.status == "rejected":
                return result
            accepted = result or accepted
        return accepted
