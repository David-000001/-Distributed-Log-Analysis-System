# Distributed Log Analysis System

A Python-based distributed log analysis system that simulates real-world log collection, aggregation, and anomaly detection across multiple services and nodes.

---

## Overview

In distributed systems, logs are generated across many nodes and services simultaneously. This project implements a lightweight but complete pipeline:

```
[Agent: node-1 / auth]       ──┐
[Agent: node-2 / payment]    ──┼──► [Log Server] ──► [Analyzer] ──► Alerts & Reports
[Agent: node-3 / api]        ──┘
```

Each **agent** collects or generates log entries and streams them over TCP to the central **server**, which aggregates entries in a ring buffer. A background **analyzer** thread periodically scans the buffer for anomalies and emits alerts.

---

## Features

- Multi-agent TCP log streaming (multiple nodes → one server)
- Realistic synthetic log generation (weighted level distribution)
- Plain-text log file tailing (tail-mode for real log files)
- Anomaly detection: high error rate, repeated messages, CRITICAL events, node silence
- Per-level, per-node, per-service breakdown statistics
- Coloured CLI output and tabular reports
- Four operation modes: `demo`, `server`, `agent`, `analyse`

---

## Project Structure

```
Distributed-Log-Analysis-System/
├── main.py           # CLI entry point (demo / server / agent / analyse)
├── log_agent.py      # Log agent: synthetic generator + file tailer
├── log_server.py     # Central TCP aggregation server
├── analyzer.py       # Anomaly detection & statistics engine
├── requirements.txt  # Dependencies
└── README.md
```

---

## Installation

```bash
git clone https://github.com/David-000001/-Distributed-Log-Analysis-System.git
cd -Distributed-Log-Analysis-System

pip install -r requirements.txt
```

---

## Usage

### Run the built-in demo (server + 3 agents, 15 seconds)
```bash
python main.py demo
python main.py demo --duration 30
```

### Start the server separately
```bash
python main.py server --port 9999
```

### Start an agent (connects to the server)
```bash
python main.py agent --node node-1 --service auth-service --port 9999
```

### Tail a real log file
```python
from log_agent import LogAgent
agent = LogAgent("node-1", "nginx", server_host="127.0.0.1", server_port=9999)
agent.tail_file("/var/log/nginx/error.log")
```

### Analyse a saved `.jsonl` log file
```bash
python main.py analyse --input logs.jsonl
```

---

## Example Output

```
[Server] Listening on 127.0.0.1:19999
[node-1] Generating synthetic logs → 127.0.0.1:19999
[node-2] Generating synthetic logs → 127.0.0.1:19999
[node-3] Generating synthetic logs → 127.0.0.1:19999

  [ALERT 14:32:05] High error rate detected: 23.4% (47/201 entries are ERROR/CRITICAL)
  [ALERT 14:32:05] CRITICAL on [node-2/payment-service]: Database connection pool exhausted
  [ALERT 14:32:05] Repeated message (7×): "Connection refused to 10.0.3.201"

════════════════════════════════════════════════════════════
  LOG ANALYSIS REPORT
════════════════════════════════════════════════════════════

  Total entries     : 201
  Error rate        : 23.4%
  Avg response time : 1842.3 ms

  By Level:
    CRITICAL        2  ██
    ERROR          45  ████████████████████████
    WARNING        52  ████████████████████████████
    INFO          102  ████████████████████████████████████████████
    DEBUG           0

  By Node:
  Node      Count
  ------  -------
  node-3       74
  node-1       68
  node-2       59

  Top Error Messages:
    [  7×] Connection refused to 10.0.3.201
    [  5×] Timeout after 4821ms
```

---

## Anomaly Detection Rules

| Rule | Threshold | Alert |
|------|-----------|-------|
| High error rate | > 20% ERROR/CRITICAL | ⚠ Error rate alert |
| Repeated message | ≥ 5 occurrences | ⚠ Repeated message alert |
| CRITICAL event | Any | ⚠ CRITICAL alert per event |
| Node silence | No logs in last 30s | ⚠ Node silence alert |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.9+ |
| Transport | TCP sockets (`socket`) |
| Concurrency | `threading` |
| Log format | JSON Lines (`.jsonl`) |
| CLI | `argparse`, `colorama` |
| Reporting | `tabulate` |

---

## Author

**Avijit Chandra Dey**  
B.Sc. Computer Science (AI) — University of Malaya  
[GitHub](https://github.com/David-000001) · [LinkedIn](https://linkedin.com/in/avijit-chandra-dey-340b61294)
