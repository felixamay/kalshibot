"""
Backtest different signal TTL values.

Does NOT auto-select the best TTL without out-of-sample validation.
Reports descriptive metrics only.
"""

from __future__ import annotations

from typing import Any

from app.services.analytics.performance import performance_tracker

TTL_CANDIDATES = [2, 3, 5, 8, 10, 15]


def run_ttl_backtest() -> dict[str, Any]:
    """
    Analyze recorded signal outcomes grouped by original TTL buckets.

    Returns advisory metrics. Explicitly does NOT recommend auto-adopting
    the historically best TTL without out-of-sample validation.
    """
    summary = performance_tracker.summary()
    buckets: dict[int, dict[str, Any]] = {t: {"count": 0, "avg_mfe": 0.0, "avg_mae": 0.0, "samples": []} for t in TTL_CANDIDATES}

    for sig in summary.get("signals", []):
        ttl_s = int(round(sig["original_ttl_ms"] / 1000))
        # map to nearest candidate
        nearest = min(TTL_CANDIDATES, key=lambda x: abs(x - ttl_s))
        res = sig.get("results") or {}
        # use 5s horizon if available else first available
        horizon_data = res.get(5) or res.get("5") or (list(res.values())[0] if res else None)
        if not horizon_data:
            continue
        b = buckets[nearest]
        b["count"] += 1
        mfe = horizon_data.get("max_favorable_movement") or 0
        mae = horizon_data.get("max_adverse_movement") or 0
        b["samples"].append({"mfe": mfe, "mae": mae})

    report = []
    for ttl, b in buckets.items():
        n = b["count"]
        if n == 0:
            report.append({"ttl_seconds": ttl, "count": 0, "avg_mfe": None, "avg_mae": None})
            continue
        avg_mfe = sum(s["mfe"] for s in b["samples"]) / n
        avg_mae = sum(s["mae"] for s in b["samples"]) / n
        report.append(
            {
                "ttl_seconds": ttl,
                "count": n,
                "avg_mfe": round(avg_mfe, 3),
                "avg_mae": round(avg_mae, 3),
            }
        )

    return {
        "ttl_candidates_seconds": TTL_CANDIDATES,
        "results": report,
        "warning": (
            "Do NOT automatically choose the TTL with the best historical result "
            "without out-of-sample validation. These metrics are descriptive only."
        ),
        "auto_select_enabled": False,
    }
