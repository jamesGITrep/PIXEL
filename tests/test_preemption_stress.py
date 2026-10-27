"""
PixelMosaic - Phase 8: Spot Preemption Fault-Tolerance Stress Test (Task 8.2)
Simulates worker container termination / Spot VM preemption mid-execution,
verifies that lease timeout releases the orphaned lock,
and validates that a failover worker reclaims and successfully completes the chunk.
"""

import os
import sys
import time
import json
import uuid
import numpy as np
import cv2

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure MinIO endpoint points to local MinIO if not set
if "MINIO_ENDPOINT" not in os.environ:
    os.environ["MINIO_ENDPOINT"] = "http://localhost:9000"

from services.common.storage import (
    get_storage,
    BUCKET_PHOTO_LIBRARY,
    BUCKET_TARGET_POSTERS,
    BUCKET_TASK_COORDINATION,
    BUCKET_MOSAIC_OUTPUTS
)
from services.worker.worker import ComputeWorker

def run_preemption_stress_test():
    print("=" * 70)
    print("TASK 8.2: SPOT PREEMPTION FAULT-TOLERANCE STRESS TEST")
    print("=" * 70)

    storage = get_storage()
    storage.ensure_buckets_exist()

    job_id = f"job_spot_preempt_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    chunk_id = "chunk_preempt_0001"
    lock_path = f"jobs/{job_id}/locks/{chunk_id}.lock"
    task_key = f"jobs/{job_id}/tasks/{chunk_id}.json"
    output_key = f"jobs/{job_id}/chunks/{chunk_id}.json"

    print(f"\n[STEP 1] Dispatching active test job '{job_id}' with task chunk '{chunk_id}'...")

    # Upload synthetic task chunk
    task_data = {
        "job_id": job_id,
        "chunk_id": chunk_id,
        "chunk_index": 0,
        "total_chunks": 1,
        "mode": "photomosaic",
        "metric": "CIELAB_DELTA_E_2000",
        "no_repeat_radius": 2,
        "cells": [
            {
                "cell_index": 0,
                "col": 0,
                "row": 0,
                "pixel_x": 0,
                "pixel_y": 0,
                "width": 50,
                "height": 50,
                "target_rgb": [240, 120, 80]
            },
            {
                "cell_index": 1,
                "col": 1,
                "row": 0,
                "pixel_x": 50,
                "pixel_y": 0,
                "width": 50,
                "height": 50,
                "target_rgb": [50, 200, 150]
            }
        ],
        "created_at": time.time()
    }
    storage.upload_file(
        BUCKET_TASK_COORDINATION,
        task_key,
        json.dumps(task_data).encode("utf-8"),
        "application/json"
    )
    print(f"  [OK] Uploaded task chunk -> {BUCKET_TASK_COORDINATION}/{task_key}")

    # --------------------------------------------------------------------------
    # Sub-Test A: Simulate Worker 1 claiming lock and active lease holding
    # --------------------------------------------------------------------------
    worker1_id = "SpotVM-Worker-Node-Alpha"
    worker2_id = "Failover-Worker-Node-Beta"

    # Use a short lease TTL (4 seconds) for crisp test execution, plus test 60s math
    test_ttl = 4
    print(f"\n[STEP 2] Worker 1 ('{worker1_id}') claims lock with {test_ttl}s lease...")
    acquired_1 = storage.acquire_lock(
        bucket_name=BUCKET_TASK_COORDINATION,
        lock_path=lock_path,
        worker_id=worker1_id,
        ttl_seconds=test_ttl
    )
    assert acquired_1 is True, "Worker 1 failed to acquire lock!"
    print(f"  [PASS] Worker 1 successfully locked {lock_path}")

    # Verify lock content in bucket
    raw_lock = storage.download_file(BUCKET_TASK_COORDINATION, lock_path).decode("utf-8")
    lock_json = json.loads(raw_lock)
    print(f"  * Lock metadata in storage: {lock_json}")
    assert lock_json["worker_id"] == worker1_id, "Lock worker_id mismatch"
    assert lock_json["expires_at"] > time.time(), "Lock already expired upon creation"

    # --------------------------------------------------------------------------
    # Sub-Test B: Concurrent contention while lease is valid (Must be REJECTED)
    # --------------------------------------------------------------------------
    print(f"\n[STEP 3] Worker 2 ('{worker2_id}') attempts to claim active lock (Must be REJECTED)...")
    acquired_2_early = storage.acquire_lock(
        bucket_name=BUCKET_TASK_COORDINATION,
        lock_path=lock_path,
        worker_id=worker2_id,
        ttl_seconds=test_ttl
    )
    assert acquired_2_early is False, "Collision bug! Worker 2 claimed an active lock held by Worker 1."
    print("  [PASS] Active lock collision correctly prevented. Worker 2 was rejected.")

    # --------------------------------------------------------------------------
    # Sub-Test C: Simulate Spot VM Preemption / Worker Termination
    # Worker 1 is abruptly terminated mid-execution WITHOUT releasing the lock.
    # --------------------------------------------------------------------------
    print(f"\n[STEP 4] Simulating SPOT VM PREEMPTION (Worker 1 terminated mid-execution)...")
    print(f"  * Worker 1 process killed. Lock {lock_path} is now orphaned.")
    time_remaining = lock_json["expires_at"] - time.time()
    print(f"  * Waiting {time_remaining + 0.5:.2f}s for lease expiration...")
    time.sleep(max(0.1, time_remaining + 0.5))

    # Verify lease is now expired
    assert time.time() >= lock_json["expires_at"], "Lease should be expired now"
    print("  * Lease timestamp has now elapsed (now > expires_at).")

    # --------------------------------------------------------------------------
    # Sub-Test D: Failover Worker claims orphaned lock and completes chunk
    # --------------------------------------------------------------------------
    print(f"\n[STEP 5] Failover Worker 2 ('{worker2_id}') attempts to acquire expired orphaned lock...")
    acquired_2_after_expiry = storage.acquire_lock(
        bucket_name=BUCKET_TASK_COORDINATION,
        lock_path=lock_path,
        worker_id=worker2_id,
        ttl_seconds=60
    )
    assert acquired_2_after_expiry is True, "Worker 2 failed to claim expired orphaned lock!"
    print(f"  [PASS] Worker 2 successfully overrode orphaned lock and acquired lease!")

    # Verify lock updated to Worker 2
    raw_lock_2 = storage.download_file(BUCKET_TASK_COORDINATION, lock_path).decode("utf-8")
    lock_json_2 = json.loads(raw_lock_2)
    assert lock_json_2["worker_id"] == worker2_id, "Lock was not updated to Worker 2"
    print(f"  * New lock metadata in storage: {lock_json_2}")

    # Worker 2 executes and finishes the chunk
    print(f"\n[STEP 6] Worker 2 processes chunk and writes result to {BUCKET_MOSAIC_OUTPUTS}...")
    w2 = ComputeWorker()
    w2.worker_id = worker2_id
    w2.execute_task_chunk(job_id, task_key, chunk_id)
    storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, worker2_id)
    print("  * Worker 2 finished execution and released lock.")

    # --------------------------------------------------------------------------
    # Sub-Test E: Validate Chunk Output & Zero Missing Data
    # --------------------------------------------------------------------------
    print("\n[STEP 7] Verifying chunk output in bucket-mosaic-outputs...")
    output_bytes = storage.download_file(BUCKET_MOSAIC_OUTPUTS, output_key)
    assert len(output_bytes) > 0, "Output chunk is missing or empty"
    output_result = json.loads(output_bytes.decode("utf-8"))
    
    print(f"  * Output Chunk metadata: job_id={output_result['job_id']}, worker_id={output_result['worker_id']}")
    assert output_result["worker_id"] == worker2_id, f"Expected worker_id {worker2_id}, got {output_result['worker_id']}"
    assert len(output_result["cells"]) == 2, f"Expected 2 cells, got {len(output_result['cells'])}"
    for cell in output_result["cells"]:
        assert "matched_rgb" in cell, "Cell missing matched_rgb"
        assert "delta_e" in cell, "Cell missing delta_e"
        print(f"    - Cell {cell['cell_index']}: target={cell['target_rgb']} -> matched={cell['matched_rgb']}, deltaE={cell['delta_e']}")

    # Also verify standard 60-second lease configuration logic in storage.py
    print("\n[STEP 8] Validating Production 60-Second Lease Timeout Logic...")
    sample_lock_path = f"jobs/{job_id}/locks/prod_60s_test.lock"
    storage.acquire_lock(BUCKET_TASK_COORDINATION, sample_lock_path, "ProdWorker-60s", ttl_seconds=60)
    prod_lock_data = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, sample_lock_path).decode("utf-8"))
    lease_delta = prod_lock_data["expires_at"] - prod_lock_data["acquired_at"]
    assert round(lease_delta) == 60, f"Expected 60s lease delta, got {lease_delta}"
    storage.release_lock(BUCKET_TASK_COORDINATION, sample_lock_path)
    print("  [PASS] Verified 60-second production lease TTL arithmetic.")

    print("\n" + "=" * 70)
    print(">>> TASK 8.2 SPOT PREEMPTION STRESS TEST: ALL INVARIANTS PASSED <<<")
    print("=" * 70)

if __name__ == "__main__":
    run_preemption_stress_test()
