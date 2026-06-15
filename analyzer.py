"""
analyzer.py
-----------
Stateless log analysis engine.

Provides:
  - summary()  : aggregate statistics over a list of LogEntry objects
  - analyse()  : returns a list of alert strings for anomalous patterns
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from log_agent import LogEntry


class LogAnalyzer:
    """
    Analyses a snapshot of LogEntry objects and produces
    statistics and anomaly alerts.
    """

    # Thresholds
    ERROR_RATE_THRESHOLD = 0.20      # >20% error+critical → alert
    SPIKE_MULTIPLIER = 3.0           # current window 3× previous → spike alert
    REPEAT_THRESHOLD = 5             # same message ≥5 times → alert
    SLOW_QUERY_MS_PATTERN = re.compile(r"(\d+)\s*ms", re.I)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summary(self, entries: list["LogEntry"]) -> dict:
        """
        Return aggregate statistics for a list of entries.

        Keys
        ----
        total, by_level, by_node, by_service,
        error_rate, top_errors, avg_response_ms
        """
        if not entries:
            return {"total": 0}

        by_level: Counter = Counter()
        by_node: Counter = Counter()
        by_service: Counter = Counter()
        error_msgs: list[str] = []
        response_times: list[float] = []

        for e in entries:
            by_level[e.level] += 1
            by_node[e.node_id] += 1
            by_service[e.service] += 1
            if e.level in ("ERROR", "CRITICAL"):
                error_msgs.append(e.message)
            m = self.SLOW_QUERY_MS_PATTERN.search(e.message)
            if m:
                response_times.append(float(m.group(1)))

        total = len(entries)
        error_count = by_level.get("ERROR", 0) + by_level.get("CRITICAL", 0)
        error_rate = error_count / total if total else 0.0
        top_errors = Counter(error_msgs).most_common(5)
        avg_ms = sum(response_times) / len(response_times) if response_times else None

        return {
            "total": total,
            "by_level": dict(by_level),
            "by_node": dict(by_node.most_common()),
            "by_service": dict(by_service.most_common()),
            "error_rate": round(error_rate, 4),
            "top_errors": top_errors,
            "avg_response_ms": round(avg_ms, 1) if avg_ms is not None else None,
        }

    def analyse(self, entries: list["LogEntry"]) -> list[str]:
        """
        Run anomaly detection and return a list of human-readable alert strings.
        Returns an empty list if everything looks normal.
        """
        alerts: list[str] = []
        if not entries:
            return alerts

        alerts += self._check_error_rate(entries)
        alerts += self._check_repeated_messages(entries)
        alerts += self._check_critical_entries(entries)
        alerts += self._check_node_silence(entries)
        return alerts

    # ------------------------------------------------------------------
    # Detection rules
    # ------------------------------------------------------------------

    def _check_error_rate(self, entries: list["LogEntry"]) -> list[str]:
        alerts = []
        total = len(entries)
        errors = sum(1 for e in entries if e.level in ("ERROR", "CRITICAL"))
        rate = errors / total if total else 0.0
        if rate >= self.ERROR_RATE_THRESHOLD:
            alerts.append(
                f"High error rate detected: {rate*100:.1f}% "
                f"({errors}/{total} entries are ERROR/CRITICAL)"
            )
        return alerts

    def _check_repeated_messages(self, entries: list["LogEntry"]) -> list[str]:
        alerts = []
        msg_counts: Counter = Counter(e.message for e in entries if e.level in ("ERROR", "CRITICAL", "WARNING"))
        for msg, count in msg_counts.items():
            if count >= self.REPEAT_THRESHOLD:
                alerts.append(
                    f"Repeated message ({count}×): \"{msg[:80]}{'...' if len(msg)>80 else ''}\""
                )
        return alerts

    def _check_critical_entries(self, entries: list["LogEntry"]) -> list[str]:
        alerts = []
        criticals = [e for e in entries if e.level == "CRITICAL"]
        for e in criticals:
            alerts.append(
                f"CRITICAL on [{e.node_id}/{e.service}]: {e.message}"
            )
        return alerts

    def _check_node_silence(self, entries: list["LogEntry"]) -> list[str]:
        """
        Alert if a node that was recently active has gone silent
        (no entries in the last 30 seconds of the snapshot window).
        """
        alerts = []
        if not entries:
            return alerts

        # Determine time window
        def _parse_ts(ts: str) -> datetime | None:
            try:
                return datetime.fromisoformat(ts)
            except ValueError:
                return None

        times_by_node: dict[str, list[datetime]] = defaultdict(list)
        for e in entries:
            t = _parse_ts(e.timestamp)
            if t:
                times_by_node[e.node_id].append(t)

        if not times_by_node:
            return alerts

        latest_global = max(t for ts in times_by_node.values() for t in ts)
        silence_cutoff = latest_global - timedelta(seconds=30)

        for node, ts_list in times_by_node.items():
            if max(ts_list) < silence_cutoff:
                alerts.append(
                    f"Node '{node}' has been silent for >30s (last seen: {max(ts_list).strftime('%H:%M:%S')})"
                )
        return alerts
