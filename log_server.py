"""
log_server.py
-------------
Central log aggregation server.

Accepts TCP connections from multiple LogAgent instances, receives
JSON log entries, and stores them in an in-memory ring buffer.
A background analyser thread periodically scans the buffer and
emits alerts for anomalies (error spikes, repeated messages, etc.).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone

from log_agent import LogEntry
from analyzer import LogAnalyzer


class LogServer:
    """
    Multi-threaded TCP log aggregation server.

    Parameters
    ----------
    host         : Bind address (default "127.0.0.1").
    port         : Listen port (default 9999).
    buffer_size  : Maximum log entries kept in memory.
    analysis_interval : Seconds between analysis sweeps.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9999,
        buffer_size: int = 10_000,
        analysis_interval: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.buffer: deque[LogEntry] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._analyzer = LogAnalyzer()
        self._analysis_interval = analysis_interval
        self._connected_nodes: set[str] = set()
        self._total_received = 0

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start listening and launch the analyser thread."""
        analyser_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        analyser_thread.start()

        srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv_sock.bind((self.host, self.port))
        srv_sock.listen(50)
        srv_sock.settimeout(1.0)

        print(f"[Server] Listening on {self.host}:{self.port}")
        try:
            while not self._stop_event.is_set():
                try:
                    conn, addr = srv_sock.accept()
                    t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
        finally:
            srv_sock.close()
            print("[Server] Stopped.")

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Client handler
    # ------------------------------------------------------------------

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
        buf = ""
        with conn:
            while not self._stop_event.is_set():
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        self._ingest(line.strip())
                except OSError:
                    break

    def _ingest(self, raw: str) -> None:
        if not raw:
            return
        try:
            entry = LogEntry.from_json(raw)
        except (json.JSONDecodeError, KeyError):
            return
        with self._lock:
            self.buffer.append(entry)
            self._connected_nodes.add(entry.node_id)
            self._total_received += 1

    # ------------------------------------------------------------------
    # Analysis loop
    # ------------------------------------------------------------------

    def _analysis_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self._analysis_interval)
            with self._lock:
                snapshot = list(self.buffer)
            if snapshot:
                alerts = self._analyzer.analyse(snapshot)
                for alert in alerts:
                    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    print(f"  [ALERT {ts}] {alert}")

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        with self._lock:
            snapshot = list(self.buffer)
        return self._analyzer.summary(snapshot) | {
            "total_received": self._total_received,
            "buffer_count": len(self.buffer),
            "connected_nodes": sorted(self._connected_nodes),
        }
