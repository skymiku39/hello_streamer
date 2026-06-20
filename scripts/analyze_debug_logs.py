"""Quick analysis of project debug logs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_ndjson(path: Path) -> list[dict]:
    entries: list[dict] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def analyze_poll_log(path: Path) -> dict:
    entries = load_ndjson(path)
    msgs = Counter(e.get("message") for e in entries)
    tier1 = [
        e["data"]["tier1_elapsed_ms"]
        for e in entries
        if e.get("message") == "tier1_complete"
    ]
    pool_done = [
        e["data"]["elapsed_ms"] for e in entries if e.get("message") == "pool_done"
    ]
    reorder = [e for e in entries if e.get("event") in ("begin", "motion", "repack", "end")]
    bad = [
        e
        for e in entries
        if any(
            k in json.dumps(e, ensure_ascii=False).lower()
            for k in ("error", "exception", "traceback", "fail")
        )
    ]
    last_cycle = next(
        (
            e
            for e in reversed(entries)
            if e.get("message") == "cycle_enabled_entries"
        ),
        None,
    )
    return {
        "path": str(path),
        "size_mb": round(path.stat().st_size / (1024 * 1024), 1) if path.exists() else 0,
        "entries": len(entries),
        "messages": dict(msgs.most_common(8)),
        "tier1_count": len(tier1),
        "tier1_ms": {
            "min": min(tier1) if tier1 else None,
            "max": max(tier1) if tier1 else None,
            "avg": sum(tier1) // len(tier1) if tier1 else None,
        },
        "pool_done_ms": {
            "min": min(pool_done) if pool_done else None,
            "max": max(pool_done) if pool_done else None,
            "avg": sum(pool_done) // len(pool_done) if pool_done else None,
        },
        "reorder_events": len(reorder),
        "error_like": len(bad),
        "last_poll_cycle": (
            last_cycle["data"].get("poll_cycle") if last_cycle else None
        ),
        "last_channel_count": (
            len(last_cycle["data"].get("keys", [])) if last_cycle else None
        ),
    }


def analyze_reorder_log(path: Path) -> dict:
    entries = load_ndjson(path)
    if not entries:
        return {"path": str(path), "exists": path.exists(), "entries": 0}
    mismatches = []
    sessions: list[dict] = []
    current: dict | None = None
    for e in entries:
        event = e.get("event")
        if event == "begin":
            current = {"begin": e, "motions": [], "repacks": [], "end": None}
            sessions.append(current)
        elif current is not None:
            if event == "motion":
                current["motions"].append(e)
            elif event == "repack":
                current["repacks"].append(e)
                order = e.get("order")
                visual = e.get("visual")
                if visual is not None and order is not None and visual != order:
                    mismatches.append(e)
            elif event == "end":
                current["end"] = e
    incomplete = [s for s in sessions if s["end"] is None]
    return {
        "path": str(path),
        "exists": True,
        "entries": len(entries),
        "sessions": len(sessions),
        "visual_order_mismatches": len(mismatches),
        "incomplete_sessions": len(incomplete),
        "mismatch_samples": mismatches[:3],
        "incomplete_samples": incomplete[:2],
    }


def main() -> None:
    poll = analyze_poll_log(ROOT / "debug-f9fde6.log")
    reorder = analyze_reorder_log(ROOT / "debug-reorder.log")
    verify_path = ROOT / "debug-reorder-verify.json"
    verify_ok = None
    if verify_path.exists():
        verify_ok = json.loads(verify_path.read_text(encoding="utf-8")).get("ok")

    print(json.dumps({"poll": poll, "reorder": reorder, "verify_ok": verify_ok}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
