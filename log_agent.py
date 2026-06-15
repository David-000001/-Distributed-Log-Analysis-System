"""
log_agent.py
------------
Simulates a distributed log agent that collects, parses, and forwards
log entries from a local source (file or synthetic generator) to the
central LogServer via a simple socket connection.

Each agent is identified by a unique node_id and service_name.
"""

from __future__ import annotations

import json
import random
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Log entry data model
# ---------------------------------------------------------------------------

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

LOG_LEVEL_WEIGHTS = [10, 50, 25, 12, 3]   # realistic distribution

SAMPLE_MESSAGES = {
    "DEBUG":    ["Cache miss for key {key}", "SQL query took {ms}ms", "Thread pool size: {n}"],
    "INFO":     ["Request handled in {ms}ms", "User {uid} authenticated", "Service started on port {port}"],
    "WARNING":  ["Memory usage at {pct}%", "Slow query detected: {ms}ms", "Retry {n}/3 for service {svc}"],
    "ERROR":    ["Connection refused to {host}", "NullPointerException in module {mod}", "Timeout after {ms}ms"],
    "CRITICAL": ["Disk space critically low: {pct}%", "Database connection pool exhausted", "Service crash detected in {svc}"],
}


def _render(template: str) -> str:
    return template.format(
        key=f"user:{random.randint(1000,9999)}",
        ms=random.randint(1, 5000),
        n=random.randint(1, 16),
        uid=f"u{random.randint(100,999)}",
        port=random.choice([8080, 8443, 3000, 5432, 6379]),
        pct=random.randint(70, 99),
        svc=random.choice(["auth", "payment", "api", "db"]),
        host=f"10.0.{random.randint(0,10)}.{random.randint(1,254)}",
        mod=random.choice(["UserService", "OrderHandler", "Cache", "DB"]),
    )


class LogEntry:
    __slots__ = ("timestamp", "node_id", "service", "level", "message", "trace_id")

    def __init__(self, node_id: str, service: str, level: str, message: str):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.node_id = node_id
        self.service = service
        self.level = level
        self.message = message
        self.trace_id = f"{random.randint(0, 0xFFFFFF):06x}"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "node_id":   self.node_id,
            "service":   self.service,
            "level":     self.level,
            "message":   self.message,
            "trace_id":  self.trace_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @staticmethod
    def from_json(raw: str) -> "LogEntry":
        d = json.loads(raw)
        entry = LogEntry.__new__(LogEntry)
        for k in LogEntry.__slots__:
            setattr(entry, k, d.get(k, ""))
        return entry

    @staticmethod
    def parse_line(line: str, node_id: str, service: str) -> "LogEntry | None":
        """
        Parse a plain-text log line in common formats:
          [LEVEL] message
          YYYY-MM-DD HH:MM:SS LEVEL message
        Returns None if the line cannot be parsed.
        """
        line = line.strip()
        if not line:
            return None

        # Pattern: [LEVEL] message
        m = re.match(r"^\[?(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]?\s+(.*)", line, re.I)
        if m:
            return LogEntry(node_id, service, m.group(1).upper(), m.group(2))

        # Pattern: timestamp LEVEL message
        m = re.match(r"^\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}.*?\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)", line, re.I)
        if m:
            return LogEntry(node_id, service, m.group(1).upper(), m.group(2))

        # Fallback: treat as INFO
        return LogEntry(node_id, service, "INFO", line)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class LogAgent:
    """
    Collects log entries and forwards them to a LogServer.

    Parameters
    ----------
    node_id      : Unique identifier for this node (e.g. "node-1").
    service_name : Name of the service being monitored.
    server_host  : LogServer hostname.
    server_port  : LogServer port.
    interval     : Seconds between synthetic log bursts (synthetic mode only).
    """

    def __init__(
        self,
        node_id: str,
        service_name: str,
        server_host: str = "127.0.0.1",
        server_port: int = 9999,
        interval: float = 1.0,
    ):
        self.node_id = node_id
        self.service_name = service_name
        self.server_host = server_host
        self.server_port = server_port
        self.interval = interval
        self._stop_event = threading.Event()
        self._sock: socket.socket | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self.server_host, self.server_port))
            return True
        except OSError as e:
            print(f"[{self.node_id}] Cannot connect to server: {e}")
            return False

    def _send(self, entry: LogEntry) -> bool:
        if self._sock is None:
            return False
        try:
            payload = (entry.to_json() + "\n").encode()
            self._sock.sendall(payload)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tail_file(self, path: str) -> None:
        """
        Tail a log file and forward new lines to the server in real time.
        Runs until stop() is called.
        """
        p = Path(path)
        if not p.exists():
            print(f"[{self.node_id}] File not found: {path}")
            return

        if not self._connect():
            return

        print(f"[{self.node_id}] Tailing '{path}' → {self.server_host}:{self.server_port}")
        with open(p, "r", encoding="utf-8") as f:
            f.seek(0, 2)  # seek to end
            while not self._stop_event.is_set():
                line = f.readline()
                if line:
                    entry = LogEntry.parse_line(line, self.node_id, self.service_name)
                    if entry:
                        self._send(entry)
                else:
                    time.sleep(0.1)

    def run_synthetic(self, burst: int = 3) -> None:
        """
        Generate synthetic log entries and forward them to the server.
        Runs until stop() is called.
        """
        if not self._connect():
            return

        print(f"[{self.node_id}] Generating synthetic logs → {self.server_host}:{self.server_port}")
        while not self._stop_event.is_set():
            for _ in range(burst):
                level = random.choices(LOG_LEVELS, weights=LOG_LEVEL_WEIGHTS, k=1)[0]
                msg_template = random.choice(SAMPLE_MESSAGES[level])
                entry = LogEntry(self.node_id, self.service_name, level, _render(msg_template))
                ok = self._send(entry)
                if not ok:
                    print(f"[{self.node_id}] Send failed — reconnecting...")
                    time.sleep(1)
                    self._connect()
                    break
            time.sleep(self.interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass


if __name__ == "__main__":
    # Quick standalone test: print synthetic entries to stdout instead
    print("=== LogAgent standalone test (no server) ===")
    for _ in range(10):
        level = random.choices(LOG_LEVELS, weights=LOG_LEVEL_WEIGHTS, k=1)[0]
        tmpl = random.choice(SAMPLE_MESSAGES[level])
        entry = LogEntry("node-0", "test-service", level, _render(tmpl))
        print(entry.to_json())
