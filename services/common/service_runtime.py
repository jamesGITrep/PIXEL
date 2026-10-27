"""
Runtime helpers for headless services (worker, assembler) running on their own VMs.

- A tiny HTTP health endpoint (HEALTH_PORT, default 8080) for Managed Instance Group
  auto-healing. It reports unhealthy when the service's main loop stops making progress.
  These services accept no other inbound traffic.
- SIGTERM/SIGINT handling: Spot VM preemption and rolling updates send SIGTERM; the service
  finishes or abandons its current unit cleanly (locks are released / expire) and exits.
"""

import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("runtime")


class ServiceRuntime:
    def __init__(self, name: str, stall_after_sec: float = 300.0):
        self.name = name
        self.stall_after_sec = stall_after_sec
        self.started_at = time.time()
        self.last_progress = time.time()
        self.stopping = threading.Event()

    def beat(self):
        """Call from the main loop on every iteration."""
        self.last_progress = time.time()

    @property
    def running(self) -> bool:
        return not self.stopping.is_set()

    def sleep(self, seconds: float):
        """Interruptible sleep: returns early when shutdown is requested."""
        self.stopping.wait(seconds)

    def install_signal_handlers(self):
        def handle(signum, _frame):
            logger.info(f"{self.name}: received signal {signum}, shutting down after the current unit of work")
            self.stopping.set()
        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)

    def start_health_server(self):
        port = int(os.getenv("HEALTH_PORT", "8080"))
        runtime = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/health", "/healthz"):
                    self.send_response(404)
                    self.end_headers()
                    return
                stalled = time.time() - runtime.last_progress > runtime.stall_after_sec
                body = json.dumps({
                    "service": runtime.name,
                    "status": "stalled" if stalled else ("stopping" if runtime.stopping.is_set() else "healthy"),
                    "uptime_sec": round(time.time() - runtime.started_at),
                    "last_progress_sec": round(time.time() - runtime.last_progress, 1),
                }).encode("utf-8")
                self.send_response(503 if stalled else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass  # keep health probes out of the service log

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f"{self.name}: health endpoint listening on :{port}/health")
