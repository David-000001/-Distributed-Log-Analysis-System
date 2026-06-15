"""
main.py
-------
Entry point for the Distributed Log Analysis System.

Modes
-----
  server   : Start the central log server
  agent    : Start one or more synthetic log agents
  demo     : Run a full self-contained local demo (server + 3 agents)
  analyse  : Analyse a local JSON log file and print a report

Usage
-----
  python main.py demo
  python main.py server --port 9999
  python main.py agent --node node-1 --service auth --port 9999
  python main.py analyse --input logs.jsonl
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

from log_agent import LogAgent, LogEntry
from log_server import LogServer
from analyzer import LogAnalyzer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _c(text: str, color: str) -> str:
    return f"{color}{text}{Style.RESET_ALL}" if HAS_COLOR else text


def _print_report(stats: dict) -> None:
    print()
    print(_c("═" * 60, Fore.CYAN if HAS_COLOR else ""))
    print(_c("  LOG ANALYSIS REPORT", Fore.CYAN if HAS_COLOR else ""))
    print(_c("═" * 60, Fore.CYAN if HAS_COLOR else ""))

    print(f"\n  Total entries     : {stats.get('total', 0)}")
    print(f"  Error rate        : {stats.get('error_rate', 0)*100:.1f}%")
    avg = stats.get("avg_response_ms")
    print(f"  Avg response time : {avg} ms" if avg else "  Avg response time : N/A")

    # Breakdown by level
    by_level = stats.get("by_level", {})
    if by_level:
        print("\n  By Level:")
        for lvl in ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]:
            n = by_level.get(lvl, 0)
            if n:
                color = {
                    "CRITICAL": Fore.RED,
                    "ERROR": Fore.RED,
                    "WARNING": Fore.YELLOW,
                    "INFO": Fore.GREEN,
                    "DEBUG": Fore.WHITE,
                }.get(lvl, "") if HAS_COLOR else ""
                bar = "█" * min(n // max(stats["total"] // 20, 1), 30)
                print(f"    {_c(f'{lvl:<10}', color)} {n:>6}  {bar}")

    # Breakdown by node
    by_node = stats.get("by_node", {})
    if by_node and HAS_TABULATE:
        print("\n  By Node:")
        rows = [[k, v] for k, v in by_node.items()]
        print(tabulate(rows, headers=["Node", "Count"], tablefmt="simple",
                       colalign=("left", "right")))

    # Top errors
    top_errors = stats.get("top_errors", [])
    if top_errors:
        print("\n  Top Error Messages:")
        for msg, count in top_errors:
            short = msg[:70] + "..." if len(msg) > 70 else msg
            print(f"    [{count:>3}×] {short}")

    print()


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------

def run_demo(duration: int = 15) -> None:
    """Launch a server + 3 agents in-process for demonstration."""
    HOST, PORT = "127.0.0.1", 19999

    server = LogServer(host=HOST, port=PORT, analysis_interval=5.0)
    srv_thread = threading.Thread(target=server.start, daemon=True)
    srv_thread.start()
    time.sleep(0.3)   # let server bind

    agents = [
        LogAgent("node-1", "auth-service",    HOST, PORT, interval=0.5),
        LogAgent("node-2", "payment-service", HOST, PORT, interval=0.7),
        LogAgent("node-3", "api-gateway",     HOST, PORT, interval=0.4),
    ]
    threads = []
    for ag in agents:
        t = threading.Thread(target=ag.run_synthetic, kwargs={"burst": 2}, daemon=True)
        t.start()
        threads.append(t)

    print(_c(f"\n[Demo] Running for {duration} seconds — press Ctrl+C to stop early.\n", Fore.MAGENTA if HAS_COLOR else ""))
    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        pass

    for ag in agents:
        ag.stop()
    server.stop()
    time.sleep(0.5)

    stats = server.get_stats()
    print(f"\n[Demo] Nodes connected: {', '.join(stats.get('connected_nodes', []))}")
    _print_report(stats)


# ---------------------------------------------------------------------------
# Analyse file mode
# ---------------------------------------------------------------------------

def run_analyse(path: str) -> None:
    entries: list[LogEntry] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = LogEntry.from_json(line)
                    entries.append(entry)
                except Exception:
                    continue
    except FileNotFoundError:
        print(f"Error: '{path}' not found.", file=sys.stderr)
        sys.exit(1)

    analyzer = LogAnalyzer()
    stats = analyzer.summary(entries)
    alerts = analyzer.analyse(entries)

    _print_report(stats)

    if alerts:
        print(_c("  ALERTS DETECTED:", Fore.RED if HAS_COLOR else ""))
        for a in alerts:
            print(f"    ⚠  {a}")
        print()
    else:
        print(_c("  No anomalies detected.\n", Fore.GREEN if HAS_COLOR else ""))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Distributed Log Analysis System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # demo
    p_demo = sub.add_parser("demo", help="Run a local demo (server + 3 agents)")
    p_demo.add_argument("--duration", type=int, default=15, help="Demo duration in seconds")

    # server
    p_srv = sub.add_parser("server", help="Start the central log server")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=9999)
    p_srv.add_argument("--buffer", type=int, default=10000)
    p_srv.add_argument("--interval", type=float, default=5.0)

    # agent
    p_ag = sub.add_parser("agent", help="Start a synthetic log agent")
    p_ag.add_argument("--node", default="node-1")
    p_ag.add_argument("--service", default="my-service")
    p_ag.add_argument("--host", default="127.0.0.1")
    p_ag.add_argument("--port", type=int, default=9999)
    p_ag.add_argument("--interval", type=float, default=1.0)
    p_ag.add_argument("--burst", type=int, default=3)

    # analyse
    p_an = sub.add_parser("analyse", help="Analyse a local .jsonl log file")
    p_an.add_argument("--input", required=True)

    args = parser.parse_args()

    if args.mode == "demo":
        run_demo(duration=args.duration)

    elif args.mode == "server":
        server = LogServer(args.host, args.port, args.buffer, args.interval)
        def _sig(*_):
            print("\n[Server] Shutting down...")
            server.stop()
            sys.exit(0)
        signal.signal(signal.SIGINT, _sig)
        server.start()

    elif args.mode == "agent":
        agent = LogAgent(args.node, args.service, args.host, args.port, args.interval)
        def _sig(*_):
            print(f"\n[{args.node}] Stopping...")
            agent.stop()
            sys.exit(0)
        signal.signal(signal.SIGINT, _sig)
        agent.run_synthetic(burst=args.burst)

    elif args.mode == "analyse":
        run_analyse(args.input)


if __name__ == "__main__":
    main()
