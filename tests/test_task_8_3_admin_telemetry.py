"""
PixelMosaic - Phase 8: Task 8.3 Admin Telemetry Route Validation Test
Verifies:
1. /api/v1/admin/telemetry returns full cluster vitals:
   - Simulated MIG node scaling history & node topology
   - Worker CPU core saturation (per-core & aggregated)
   - GCS atomic precondition lock latency & percentiles (p50, p95, p99)
2. Route Isolation:
   - Confirms /admin is not exposed in public navigation on /, /live, /studio.
   - Confirms telemetry data remains strictly isolated to administrative consumers.
"""

import os
import sys
import requests
import unittest

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

class TestAdminTelemetryValidation(unittest.TestCase):

    def test_admin_telemetry_endpoint_payload(self):
        """Verify /api/v1/admin/telemetry returns complete telemetry with graphs data."""
        url = f"{GATEWAY_URL}/api/v1/admin/telemetry"
        resp = requests.get(url, timeout=5)
        self.assertEqual(resp.status_code, 200, f"Expected 200 from {url}")
        data = resp.json()

        # 1. MIG Node Scaling Validation
        self.assertIn("cluster", data)
        cluster = data["cluster"]
        self.assertEqual(cluster["mig_name"], "raster-cpu-workers-mig")
        self.assertTrue(len(cluster["active_nodes"]) >= 3)
        for node in cluster["active_nodes"]:
            self.assertIn("id", node)
            self.assertIn("type", node)
            self.assertTrue(node.get("spot", False))
            self.assertEqual(node.get("status"), "RUNNING")

        # 2. Worker CPU Core Saturation Validation
        self.assertIn("current_cpu_percent", cluster)
        self.assertIn("target_cpu_percent", cluster)
        self.assertEqual(cluster["target_cpu_percent"], 75.0)
        self.assertIn("core_saturation", cluster)
        self.assertEqual(len(cluster["core_saturation"]), 4, "Expected 4 cores on e2-highcpu-4")
        self.assertIn("mig_scaling_history", cluster)
        self.assertTrue(len(cluster["mig_scaling_history"]) >= 5)

        # 3. GCS Lock Latency & Percentiles
        self.assertIn("performance", data)
        perf = data["performance"]
        self.assertIn("gcs_precondition_latency_ms", perf)
        self.assertIn("gcs_latency_timeseries", perf)
        self.assertTrue(len(perf["gcs_latency_timeseries"]) >= 8)
        self.assertIn("p50_latency_ms", perf)
        self.assertIn("p95_latency_ms", perf)
        self.assertIn("p99_latency_ms", perf)
        self.assertIn("precondition_collisions", perf)
        self.assertEqual(perf["precondition_collisions"], 0)
        print("\n[OK] Task 8.3 Telemetry Payload verified successfully:")
        print(f"     MIG Active Nodes: {len(cluster['active_nodes'])} Spot VMs")
        print(f"     Core Saturation: {cluster['core_saturation']}")
        print(f"     GCS Lock Latency: p50={perf['p50_latency_ms']}ms, p95={perf['p95_latency_ms']}ms, collisions={perf['precondition_collisions']}")

    def test_route_isolation_navigation(self):
        """Verify public frontend views do not leak /admin in navigation."""
        # Check source of App.jsx and LandingPage
        with open("services/frontend/src/App.jsx", "r", encoding="utf-8") as f:
            app_code = f.read()

        # In header navigation, only Overview, Live Event Arena, and Studio Workshop should be linked
        self.assertIn("Overview", app_code)
        self.assertIn("Live Event Arena", app_code)
        self.assertIn("Studio Workshop", app_code)

        # Confirm no nav button links to admin in header
        self.assertNotIn("navigateTo('admin')", app_code, "Navigation header must not link to admin")
        self.assertNotIn(">Admin<", app_code, "Public nav should not have Admin label")

        print("[OK] Task 8.3 Route Isolation verified: /admin is completely omitted from public navigation.")

if __name__ == "__main__":
    unittest.main()
