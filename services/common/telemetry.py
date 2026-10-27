"""
Cluster Telemetry (BRD 4.6 System Observability) - bucket-only.

Workers publish a heartbeat every few seconds to
    bucket-task-coordination/telemetry/workers/{worker_id}.json
with real CPU readings and throughput counters. The gateway aggregates those into the admin
dashboard view and appends a sample to telemetry/history.json (a bounded ring buffer) so node
scaling and throughput can be charted over time.
"""

import json
import os
import socket
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

TELEMETRY_PREFIX = "telemetry/workers/"
HISTORY_KEY = "telemetry/history.json"
HEARTBEAT_INTERVAL = 3.0
ACTIVE_WINDOW = 15.0        # a node is "up" if it reported within this many seconds
FORGET_AFTER = 600.0        # heartbeats older than this are removed
HISTORY_SAMPLE_EVERY = 5.0
HISTORY_MAX_SAMPLES = 360   # 30 minutes at 5s
RATE_WINDOW = 60.0


def _read_proc_stat() -> Optional[List[List[int]]]:
    """Per-CPU jiffy counters from /proc/stat: [aggregate, cpu0, cpu1, ...]."""
    try:
        with open("/proc/stat") as f:
            rows = [line.split() for line in f if line.startswith("cpu")]
        return [[int(v) for v in r[1:9]] for r in rows]
    except Exception:
        return None


def _busy_percent(prev: List[int], cur: List[int]) -> float:
    # user nice system idle iowait irq softirq steal
    idle = (cur[3] + cur[4]) - (prev[3] + prev[4])
    total = sum(cur) - sum(prev)
    return round(100.0 * (total - idle) / total, 1) if total > 0 else 0.0


class HeartbeatReporter:
    """Background thread that publishes this worker's vitals; counters are updated by the worker."""

    def __init__(self, storage, bucket: str, worker_id: str):
        self.storage = storage
        self.bucket = bucket
        self.worker_id = worker_id
        self.started_at = time.time()
        self.cores = os.cpu_count() or 1
        self.lock = threading.Lock()
        self.state = "idle"
        self.current_task: Optional[str] = None
        self.cells_total = 0
        self.chunks_total = 0
        self.photos_ingested_total = 0
        self.queue_seen = 0
        self._recent = deque()  # (timestamp, cells)
        self._prev_stat = _read_proc_stat()
        self._prev_proc = (time.time(), sum(os.times()[:2]))
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    # -- counters (called from the worker thread) ------------------------------------------
    def set_busy(self, task: Optional[str]):
        with self.lock:
            self.state = "busy" if task else "idle"
            self.current_task = task

    def record_chunk(self, cells: int):
        with self.lock:
            self.chunks_total += 1
            self.cells_total += cells
            self._recent.append((time.time(), cells))

    def record_ingest(self, n: int = 1):
        with self.lock:
            self.photos_ingested_total += n

    def set_queue(self, n: int):
        with self.lock:
            self.queue_seen = n

    # -- publishing -------------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        stat = _read_proc_stat()
        node_cpu, per_core = None, []
        if stat and self._prev_stat and len(stat) == len(self._prev_stat):
            node_cpu = _busy_percent(self._prev_stat[0], stat[0])
            per_core = [_busy_percent(p, c) for p, c in zip(self._prev_stat[1:], stat[1:])]
        self._prev_stat = stat

        proc_cpu = sum(os.times()[:2])
        prev_t, prev_cpu = self._prev_proc
        process_cpu = round(100.0 * (proc_cpu - prev_cpu) / max(1e-6, now - prev_t), 1)  # % of one core
        self._prev_proc = (now, proc_cpu)

        with self.lock:
            while self._recent and now - self._recent[0][0] > RATE_WINDOW:
                self._recent.popleft()
            window = min(RATE_WINDOW, max(1.0, now - self.started_at))
            return {
                "worker_id": self.worker_id,
                "host": socket.gethostname(),
                "cores": self.cores,
                "started_at": self.started_at,
                "last_seen": now,
                "state": self.state,
                "current_task": self.current_task,
                "process_cpu_percent": process_cpu,
                "node_cpu_percent": node_cpu,
                "per_core_cpu_percent": per_core,
                "cells_total": self.cells_total,
                "chunks_total": self.chunks_total,
                "photos_ingested_total": self.photos_ingested_total,
                "tiles_per_sec": round(sum(c for _, c in self._recent) / window, 1),
                "queue_seen": self.queue_seen,
            }

    def publish(self):
        payload = json.dumps(self.snapshot()).encode("utf-8")
        self.storage.upload_file(self.bucket, f"{TELEMETRY_PREFIX}{self.worker_id}.json", payload, "application/json")

    def _loop(self):
        while True:
            try:
                self.publish()
            except Exception:
                pass
            time.sleep(HEARTBEAT_INTERVAL)


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else 0.0


def read_cluster_telemetry(storage, bucket: str) -> Dict[str, Any]:
    """Aggregates worker heartbeats into the admin dashboard payload and records a history sample."""
    now = time.time()
    beats = []
    for key in storage.list_objects(bucket, TELEMETRY_PREFIX):
        try:
            beats.append(json.loads(storage.download_file(bucket, key)))
        except Exception:
            continue

    active, departed = [], []
    for b in beats:
        age = now - b.get("last_seen", 0)
        if age <= ACTIVE_WINDOW:
            active.append(b)
        elif age > FORGET_AFTER:
            storage.delete_object(bucket, f"{TELEMETRY_PREFIX}{b.get('worker_id')}.json")
        else:
            departed.append(b)

    active.sort(key=lambda b: b.get("started_at", 0))
    nodes = [{
        "id": b["worker_id"],
        "host": b.get("host"),
        "cores": b.get("cores"),
        "status": b.get("state", "idle").upper(),
        "current_task": b.get("current_task"),
        "process_cpu_percent": b.get("process_cpu_percent"),
        "node_cpu_percent": b.get("node_cpu_percent"),
        "tiles_per_sec": b.get("tiles_per_sec", 0),
        "cells_total": b.get("cells_total", 0),
        "chunks_total": b.get("chunks_total", 0),
        "photos_ingested_total": b.get("photos_ingested_total", 0),
        "uptime_sec": round(now - b.get("started_at", now)),
        "last_seen_sec": round(now - b.get("last_seen", now), 1),
    } for b in active]

    # Node CPU is a machine-level reading; workers sharing one host report the same machine.
    per_core = next((b.get("per_core_cpu_percent") for b in active if b.get("per_core_cpu_percent")), [])
    summary = {
        "active_nodes": len(active),
        "busy_nodes": sum(1 for b in active if b.get("state") == "busy"),
        "avg_process_cpu_percent": _mean([b.get("process_cpu_percent") for b in active]),
        "node_cpu_percent": _mean([b.get("node_cpu_percent") for b in active]),
        "per_core_cpu_percent": per_core,
        "tiles_per_sec": round(sum(b.get("tiles_per_sec", 0) for b in active), 1),
        "queue_depth": max([b.get("queue_seen", 0) for b in active] or [0]),
        "cells_total": sum(b.get("cells_total", 0) for b in beats),
        "chunks_total": sum(b.get("chunks_total", 0) for b in beats),
        "photos_ingested_total": sum(b.get("photos_ingested_total", 0) for b in beats),
    }

    # Scaling events: joins (worker start) and departures (heartbeat stopped) within the last 30 min
    events = [{"type": "join", "node": b["worker_id"], "at": b.get("started_at")}
              for b in beats if now - b.get("started_at", 0) <= 1800]
    events += [{"type": "leave", "node": b["worker_id"], "at": b.get("last_seen")} for b in departed]
    events.sort(key=lambda e: e["at"] or 0, reverse=True)

    history = _update_history(storage, bucket, now, summary)
    return {
        "generated_at": now,
        "summary": summary,
        "nodes": nodes,
        "scale_events": events[:30],
        "history": history,
    }


def _update_history(storage, bucket: str, now: float, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        history = json.loads(storage.download_file(bucket, HISTORY_KEY, quiet=True))
    except Exception:
        history = []
    if not history or now - history[-1]["t"] >= HISTORY_SAMPLE_EVERY:
        history.append({
            "t": round(now, 1),
            "nodes": summary["active_nodes"],
            "busy": summary["busy_nodes"],
            "cpu": summary["node_cpu_percent"],
            "process_cpu": summary["avg_process_cpu_percent"],
            "tiles_per_sec": summary["tiles_per_sec"],
            "queue": summary["queue_depth"],
        })
        history = history[-HISTORY_MAX_SAMPLES:]
        try:
            storage.upload_file(bucket, HISTORY_KEY, json.dumps(history).encode("utf-8"), "application/json")
        except Exception:
            pass
    return history
