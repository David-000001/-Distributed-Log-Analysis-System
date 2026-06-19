"""
netlify/functions/generate.py
------------------------------
Netlify Python serverless function that generates synthetic log entries
for demo/testing purposes.

Endpoint : POST /api/generate   (mapped via netlify.toml redirect)

Request body (JSON):
    {
            "count":    50,           // number of log entries to generate (default 50, max 500)
                    "nodes":    ["node-1", "node-2", "node-3"],   // optional node list
                            "services": ["auth", "payment", "api"]        // optional service list
                                }

                                Response body (JSON):
                                    {
                                            "entries": [ { LogEntry dict }, ... ]
                                                }
                                                """

from __future__ import annotations

import json
import random
from datetime import datetime, timezone, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Synthetic log generation (extracted from log_agent.py)
# ---------------------------------------------------------------------------

LOG_LEVEL_CHOICES = ["DEBUG", "INFO", "INFO", "INFO", "INFO", "WARNING", "WARNING", "WARNING", "ERROR", "ERROR", "CRITICAL"]

SAMPLE_MESSAGES: dict[str, list[str]] = {
      "DEBUG":    ["Cache miss for key user:{uid}", "SQL query took {ms}ms", "Thread pool size: {n}"],
      "INFO":     ["Request handled in {ms}ms", "User u{uid} authenticated", "Service started on port {port}", "Health check passed", "Config reloaded"],
      "WARNING":  ["Memory usage at {pct}%", "Slow query detected: {ms}ms", "Retry {n}/3 for service {svc}", "Rate limit approaching for u{uid}"],
      "ERROR":    ["Connection refused to 10.0.{n}.{uid}", "NullPointerException in module {mod}", "Timeout after {ms}ms", "Failed to write to disk"],
      "CRITICAL": ["Disk space critically low: {pct}%", "Database connection pool exhausted", "Service crash detected in {svc}", "Out of memory - forcing restart"],
}

DEFAULT_NODES    = ["node-1", "node-2", "node-3"]
DEFAULT_SERVICES = ["auth-service", "payment-service", "api-gateway"]


def _render(template: str) -> str:
      return template.format(
                uid=random.randint(100, 9999),
                ms=random.randint(1, 5000),
                n=random.randint(1, 16),
                port=random.choice([8080, 8443, 3000, 5432, 6379]),
                pct=random.randint(70, 99),
                svc=random.choice(["auth", "payment", "api", "db"]),
                mod=random.choice(["UserService", "OrderHandler", "Cache", "DB"]),
      )


def _generate_entries(count: int, nodes: list[str], services: list[str]) -> list[dict]:
      entries = []
      now = datetime.now(timezone.utc)
      for i in range(count):
                level   = random.choice(LOG_LEVEL_CHOICES)
                node    = random.choice(nodes)
                service = random.choice(services)
                msg     = _render(random.choice(SAMPLE_MESSAGES[level]))
                # Spread timestamps over the last 60 seconds
                ts = now - timedelta(seconds=random.uniform(0, 60))
                entries.append({
                    "timestamp": ts.isoformat(),
                    "node_id":   node,
                    "service":   service,
                    "level":     level,
                    "message":   msg,
                    "trace_id":  f"{random.randint(0, 0xFFFFFF):06x}",
                })
            # Sort chronologically
            entries.sort(key=lambda e: e["timestamp"])
    return entries


# ---------------------------------------------------------------------------
# Netlify Function handler
# ---------------------------------------------------------------------------

def handler(event: dict, context: Any) -> dict:
      cors_headers = {
          "Access-Control-Allow-Origin":  "*",
          "Access-Control-Allow-Headers": "Content-Type",
          "Access-Control-Allow-Methods": "POST, OPTIONS",
          "Content-Type":                 "application/json",
}

    method = event.get("httpMethod", "").upper()

    if method == "OPTIONS":
              return {"statusCode": 204, "headers": cors_headers, "body": ""}

    if method != "POST":
              return {
                            "statusCode": 405,
                            "headers":    cors_headers,
                            "body":       json.dumps({"error": "Method not allowed. Use POST."}),
              }

    try:
              raw_body = event.get("body") or "{}"
              if event.get("isBase64Encoded"):
                            import base64
                            raw_body = base64.b64decode(raw_body).decode("utf-8")
                        payload = json.loads(raw_body)
except Exception as exc:
        return {
                      "statusCode": 400,
                      "headers":    cors_headers,
                      "body":       json.dumps({"error": f"Invalid JSON: {exc}"}),
        }

    count    = min(int(payload.get("count", 50)), 500)
    nodes    = payload.get("nodes",    DEFAULT_NODES)
    services = payload.get("services", DEFAULT_SERVICES)

    if not isinstance(nodes, list) or not nodes:
              nodes = DEFAULT_NODES
    if not isinstance(services, list) or not services:
              services = DEFAULT_SERVICES

    entries = _generate_entries(count, nodes, services)
    return {
              "statusCode": 200,
              "headers":    cors_headers,
              "body":       json.dumps({"entries": entries}),
    }
