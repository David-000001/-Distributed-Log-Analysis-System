"""
netlify/functions/analyze.py
-----------------------------
Netlify Python serverless function that exposes the log analysis engine
as an HTTP API endpoint.

Endpoint : POST /api/analyze   (mapped via netlify.toml redirect)

Request body (JSON):
    {
            "entries": [
                        {
                                        "timestamp": "2024-01-01T00:00:00+00:00",
                                                        "node_id":   "node-1",
                                                                        "service":   "auth-service",
                                                                                        "level":     "ERROR",
                                                                                                        "message":   "Connection refused",
                                                                                                                        "trace_id":  "abc123"
            },
                        ...
                                ]
                                    }

                                    Response body (JSON):
                                        {
                                                "stats":  { ... },   // aggregate statistics
                                                        "alerts": [ ... ]    // anomaly alert strings
                                                            }

                                                            Netlify Functions receive a standard AWS Lambda-style `event` dict.
                                                            """

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Embedded LogEntry (no imports from sibling modules in Lambda context)
# ---------------------------------------------------------------------------

class LogEntry:
      """Lightweight log entry model for the serverless function."""

    __slots__ = ("timestamp", "node_id", "service", "level", "message", "trace_id")

    def __init__(self, data: dict):
              self.timestamp = data.get("timestamp", "")
              self.node_id   = data.get("node_id", "unknown")
              self.service   = data.get("service", "unknown")
              self.level     = data.get("level", "INFO").upper()
              self.message   = data.get("message", "")
              self.trace_id  = data.get("trace_id", "")


# ---------------------------------------------------------------------------
# Embedded LogAnalyzer (self-contained copy of analyzer.py logic)
# ---------------------------------------------------------------------------

class LogAnalyzer:
      ERROR_RATE_THRESHOLD = 0.20
      REPEAT_THRESHOLD     = 5
      SILENCE_SECONDS      = 30
      SLOW_QUERY_MS        = re.compile(r"(\d+)\s*ms", re.I)

    # ---- public API -------------------------------------------------------

    def summary(self, entries: list[LogEntry]) -> dict:
              if not entries:
                            return {"total": 0}

              by_level: Counter   = Counter()
              by_node: Counter    = Counter()
              by_service: Counter = Counter()
              error_msgs: list[str]      = []
              response_times: list[float] = []

        for e in entries:
                      by_level[e.level]     += 1
                      by_node[e.node_id]    += 1
                      by_service[e.service] += 1
                      if e.level in ("ERROR", "CRITICAL"):
                                        error_msgs.append(e.message)
                                    m = self.SLOW_QUERY_MS.search(e.message)
            if m:
                              response_times.append(float(m.group(1)))

        total       = len(entries)
        error_count = by_level.get("ERROR", 0) + by_level.get("CRITICAL", 0)
        error_rate  = error_count / total if total else 0.0
        top_errors  = Counter(error_msgs).most_common(5)
        avg_ms      = sum(response_times) / len(response_times) if response_times else None

        return {
                      "total":          total,
                      "by_level":       dict(by_level),
                      "by_node":        dict(by_node.most_common()),
                      "by_service":     dict(by_service.most_common()),
                      "error_rate":     round(error_rate, 4),
                      "top_errors":     top_errors,
                      "avg_response_ms": round(avg_ms, 1) if avg_ms is not None else None,
        }

    def analyse(self, entries: list[LogEntry]) -> list[str]:
              if not entries:
                            return []
                        alerts: list[str] = []
        alerts += self._check_error_rate(entries)
        alerts += self._check_repeated_messages(entries)
        alerts += self._check_critical_entries(entries)
        alerts += self._check_node_silence(entries)
        return alerts

    # ---- detection rules --------------------------------------------------

    def _check_error_rate(self, entries: list[LogEntry]) -> list[str]:
              total  = len(entries)
        errors = sum(1 for e in entries if e.level in ("ERROR", "CRITICAL"))
        rate   = errors / total if total else 0.0
        if rate >= self.ERROR_RATE_THRESHOLD:
                      return [
                          f"High error rate detected: {rate*100:.1f}% "
                          f"({errors}/{total} entries are ERROR/CRITICAL)"
        ]
        return []

    def _check_repeated_messages(self, entries: list[LogEntry]) -> list[str]:
              alerts: list[str] = []
        counts = Counter(
                      e.message for e in entries if e.level in ("ERROR", "CRITICAL", "WARNING")
        )
        for msg, count in counts.items():
                      if count >= self.REPEAT_THRESHOLD:
                                        short = msg[:80] + ("..." if len(msg) > 80 else "")
                                        alerts.append(f'Repeated message ({count}x): "{short}"')
                                return alerts

    def _check_critical_entries(self, entries: list[LogEntry]) -> list[str]:
              alerts: list[str] = []
        for e in entries:
                      if e.level == "CRITICAL":
                                        alerts.append(
                                                              f"CRITICAL on [{e.node_id}/{e.service}]: {e.message[:100]}"
                                        )
                                return alerts

    def _check_node_silence(self, entries: list[LogEntry]) -> list[str]:
              alerts: list[str] = []
        last_seen: dict[str, datetime] = {}
        for e in entries:
                      try:
                                        ts = datetime.fromisoformat(e.timestamp)
                                        if ts.tzinfo is None:
                                                              ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
                continue
            if e.node_id not in last_seen or ts > last_seen[e.node_id]:
                              last_seen[e.node_id] = ts
                      if last_seen:
                                    latest = max(last_seen.values())
                                    threshold = timedelta(seconds=self.SILENCE_SECONDS)
                                    for node, ts in last_seen.items():
                                                      if latest - ts >= threshold:
                                                                            alerts.append(
                                                                                                      f"Node silence: [{node}] has not sent logs for "
                                                                                                      f"{int((latest - ts).total_seconds())}s"
                                                                            )
                                                                return alerts


# ---------------------------------------------------------------------------
# Netlify Function handler
# ---------------------------------------------------------------------------

_analyzer = LogAnalyzer()


def handler(event: dict, context: Any) -> dict:
      """
          AWS Lambda / Netlify Functions entry point.

              Supports:
                    - CORS preflight  (OPTIONS)
                          - POST with JSON body containing {"entries": [...]}
                              """
    cors_headers = {
              "Access-Control-Allow-Origin":  "*",
              "Access-Control-Allow-Headers": "Content-Type",
              "Access-Control-Allow-Methods": "POST, OPTIONS",
              "Content-Type":                 "application/json",
    }

    method = event.get("httpMethod", "").upper()

    # Handle CORS preflight
    if method == "OPTIONS":
              return {"statusCode": 204, "headers": cors_headers, "body": ""}

    if method != "POST":
              return {
                            "statusCode": 405,
                            "headers":    cors_headers,
                            "body":       json.dumps({"error": "Method not allowed. Use POST."}),
              }

    # Parse request body
    try:
              raw_body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
                      import base64
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        payload = json.loads(raw_body)
except (json.JSONDecodeError, Exception) as exc:
        return {
                      "statusCode": 400,
                      "headers":    cors_headers,
                      "body":       json.dumps({"error": f"Invalid JSON: {exc}"}),
        }

    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
              return {
                            "statusCode": 400,
                            "headers":    cors_headers,
                            "body":       json.dumps({"error": "'entries' must be a JSON array."}),
              }

    # Convert dicts to LogEntry objects
    entries = [LogEntry(e) for e in raw_entries if isinstance(e, dict)]

    # Run analysis
    stats  = _analyzer.summary(entries)
    alerts = _analyzer.analyse(entries)

    response_body = json.dumps({"stats": stats, "alerts": alerts})
    return {
              "statusCode": 200,
              "headers":    cors_headers,
              "body":       response_body,
    }
