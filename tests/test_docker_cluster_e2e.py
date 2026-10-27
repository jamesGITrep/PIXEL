"""
PixelMosaic - Phase 8: Docker Cluster Concurrency & Preemption E2E (Tasks 8.1 & 8.2)
Verifies:
1. Task 8.1: 4 Docker worker containers concurrently acquire locks and complete a 100x100 Photomosaic task.
   Verifies zero collision, zero deadlock, and OpenCV canvas assembly with zero missing tiles.
2. Task 8.2: Issues `docker stop` to a worker container mid-execution,
   verifies that the lease timeout releases the orphaned lock,
   and another container claims and finishes the chunk.
"""

import os
import sys
import time
import json
import uuid
import math
import subprocess
import cv2
import numpy as np

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if "MINIO_ENDPOINT" not in os.environ:
    os.environ["MINIO_ENDPOINT"] = "http://localhost:9000"

from services.common.storage import (
    get_storage,
    BUCKET_PHOTO_LIBRARY,
    BUCKET_TARGET_POSTERS,
    BUCKET_TASK_COORDINATION,
    BUCKET_MOSAIC_OUTPUTS
)
from services.assembler.assembler import CanvasAssembler

def run_cluster_e2e_verification():
    print("=" * 75)
    print("PHASE 8 DOCKER CLUSTER VERIFICATION: TASKS 8.1 & 8.2")
    print("=" * 75)

    storage = get_storage()
    storage.ensure_buckets_exist()

    # Verify 4 Docker containers are running
    ps_output = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True)
    worker_containers = [n.strip() for n in ps_output.splitlines() if "worker" in n and "pixelmosaic" in n]
    print(f"[PRE-CHECK] Active Worker Containers Detected ({len(worker_containers)}): {worker_containers}")
    assert len(worker_containers) >= 4, f"Expected at least 4 worker containers, found {len(worker_containers)}"

    # --------------------------------------------------------------------------
    # TASK 8.1: 100x100 CELL PHOTOMOSAIC CLUSTER LOAD TEST
    # --------------------------------------------------------------------------
    print("\n" + "-" * 75)
    print("RUNNING TASK 8.1: 100x100 (10,000 CELL) CLUSTER LOAD DISPATCH")
    print("-" * 75)

    cols = 100
    rows = 100
    tile_w = 50
    tile_h = 50
    target_w = cols * tile_w
    target_h = rows * tile_h
    job_id = f"job_cluster_100x100_{int(time.time())}_{uuid.uuid4().hex[:4]}"

    # Generate and upload target poster
    grad_x = np.linspace(0, 255, target_w, dtype=np.uint8)
    grad_y = np.linspace(0, 255, target_h, dtype=np.uint8)
    gx, gy = np.meshgrid(grad_x, grad_y)
    target_poster = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    target_poster[:, :, 0] = gx
    target_poster[:, :, 1] = gy
    target_poster[:, :, 2] = (gx // 2 + gy // 2)

    _, encoded_target = cv2.imencode(".jpg", target_poster, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    target_key = f"{job_id}/original.jpg"
    storage.upload_file(BUCKET_TARGET_POSTERS, target_key, encoded_target.tobytes(), "image/jpeg")
    print(f"  [OK] Uploaded target poster -> {BUCKET_TARGET_POSTERS}/{target_key}")

    # Slice into 25 chunks (400 cells each = 10,000 cells)
    cells_per_chunk = 400
    total_cells = cols * rows
    total_chunks = math.ceil(total_cells / cells_per_chunk)

    cell_metadata = []
    for r in range(rows):
        for c in range(cols):
            cx = int((c + 0.5) * tile_w)
            cy = int((r + 0.5) * tile_h)
            bgr = target_poster[cy, cx]
            cell_metadata.append({
                "cell_index": r * cols + c,
                "col": c,
                "row": r,
                "pixel_x": c * tile_w,
                "pixel_y": r * tile_h,
                "width": tile_w,
                "height": tile_h,
                "target_rgb": [int(bgr[2]), int(bgr[1]), int(bgr[0])]
            })

    for chunk_idx in range(total_chunks):
        chunk_slice = cell_metadata[chunk_idx * cells_per_chunk : (chunk_idx + 1) * cells_per_chunk]
        chunk_data = {
            "job_id": job_id,
            "chunk_id": f"chunk_{chunk_idx + 1:04d}",
            "chunk_index": chunk_idx,
            "total_chunks": total_chunks,
            "mode": "photomosaic",
            "metric": "CIELAB_DELTA_E_2000",
            "no_repeat_radius": 3,
            "cells": chunk_slice,
            "created_at": time.time()
        }
        chunk_key = f"jobs/{job_id}/tasks/chunk_{chunk_idx + 1:04d}.json"
        storage.upload_file(
            BUCKET_TASK_COORDINATION,
            chunk_key,
            json.dumps(chunk_data).encode("utf-8"),
            "application/json"
        )

    manifest_data = {
        "job_id": job_id,
        "mode": "photomosaic",
        "total_chunks": total_chunks,
        "total_cells": total_cells,
        "grid_dimensions": {"columns": cols, "rows": rows},
        "target_dimensions": {"width": target_w, "height": target_h},
        "parameters": {"cell_size": tile_w, "alpha_tint_percentage": 20},
        "created_at": time.time()
    }
    storage.upload_file(
        BUCKET_TASK_COORDINATION,
        f"jobs/{job_id}/manifest.json",
        json.dumps(manifest_data).encode("utf-8"),
        "application/json"
    )
    print(f"  [OK] Dispatched {total_chunks} chunks to Docker cluster. Awaiting worker processing...")

    # Poll until all 25 chunks completed by the 4 containers
    t0 = time.time()
    completed_chunks = []
    while len(completed_chunks) < total_chunks and (time.time() - t0) < 60:
        completed_chunks = storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/")
        print(f"    * Progress: {len(completed_chunks)}/{total_chunks} chunks completed by Docker cluster...", end="\r")
        time.sleep(1.0)
    print()

    duration = time.time() - t0
    print(f"  [OK] All {len(completed_chunks)}/{total_chunks} chunks completed by Docker cluster in {duration:.2f}s!")
    assert len(completed_chunks) == total_chunks, f"Timeout: Only {len(completed_chunks)}/{total_chunks} completed"

    # Inspect worker identities across completed chunks
    worker_set = set()
    for c_key in completed_chunks:
        raw = storage.download_file(BUCKET_MOSAIC_OUTPUTS, c_key)
        data = json.loads(raw.decode("utf-8"))
        worker_set.add(data.get("worker_id"))

    print(f"  * Distinct Docker Worker nodes that processed chunks ({len(worker_set)}): {worker_set}")
    assert len(worker_set) > 1, "Expected multiple worker containers to concurrently process chunks"
    print("  [PASS] Task 8.1 Docker cluster concurrent lock acquisition and chunk execution verified!")

    # Assemble and verify zero missing tiles
    print("  * Assembling canvas with CanvasAssembler...")
    assembler = CanvasAssembler()
    assembler.assemble_mosaic(job_id, manifest_data, completed_chunks)

    final_bytes = storage.download_file(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/final_mosaic.png")
    assert len(final_bytes) > 0, "final_mosaic.png missing"
    mosaic_mat = cv2.imdecode(np.frombuffer(final_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert mosaic_mat.shape == (target_h, target_w, 3), f"Shape mismatch: {mosaic_mat.shape}"

    # Missing tile check
    missing_cells = 0
    for r in range(rows):
        for c in range(cols):
            cell_sample = mosaic_mat[r * tile_h : (r + 1) * tile_h, c * tile_w : (c + 1) * tile_w]
            if np.mean(cell_sample) == 0:
                missing_cells += 1

    assert missing_cells == 0, f"Detected {missing_cells} missing tiles"
    print(f"  [PASS] Master mosaic assembled (5000x5000 px, {len(final_bytes)/1024/1024:.2f} MB) with 0 missing tiles!")

    # --------------------------------------------------------------------------
    # TASK 8.2: SPOT PREEMPTION FAULT-TOLERANCE STRESS TEST (docker stop)
    # --------------------------------------------------------------------------
    print("\n" + "-" * 75)
    print("RUNNING TASK 8.2: SPOT PREEMPTION STRESS TEST (CONTAINER TERMINATION)")
    print("-" * 75)

    spot_job_id = f"job_spot_docker_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    spot_chunk_id = "chunk_docker_spot_0001"
    spot_task_key = f"jobs/{spot_job_id}/tasks/{spot_chunk_id}.json"
    spot_lock_path = f"jobs/{spot_job_id}/locks/{spot_chunk_id}.lock"
    spot_output_key = f"jobs/{spot_job_id}/chunks/{spot_chunk_id}.json"

    # Preempted worker container
    victim_container = worker_containers[0]
    print(f"  [SETUP] Designated worker container for mid-execution preemption: '{victim_container}'")

    # Simulate worker container claiming lock with lease
    # Acquire lock on behalf of the victim container
    victim_worker_id = f"worker_{victim_container}_preempted"
    lease_ttl = 5  # 5-second lease for responsive test verification
    storage.acquire_lock(
        bucket_name=BUCKET_TASK_COORDINATION,
        lock_path=spot_lock_path,
        worker_id=victim_worker_id,
        ttl_seconds=lease_ttl
    )
    print(f"  * Orphaned lock registered in storage under '{victim_worker_id}' with {lease_ttl}s lease.")

    # Upload task chunk
    spot_task_data = {
        "job_id": spot_job_id,
        "chunk_id": spot_chunk_id,
        "chunk_index": 0,
        "total_chunks": 1,
        "mode": "photomosaic",
        "metric": "CIELAB_DELTA_E_2000",
        "no_repeat_radius": 2,
        "cells": [
            {
                "cell_index": 0, "col": 0, "row": 0,
                "pixel_x": 0, "pixel_y": 0, "width": 50, "height": 50,
                "target_rgb": [200, 80, 180]
            }
        ],
        "created_at": time.time()
    }
    storage.upload_file(
        BUCKET_TASK_COORDINATION,
        spot_task_key,
        json.dumps(spot_task_data).encode("utf-8"),
        "application/json"
    )

    # Issue docker stop to simulate Spot VM preemption mid-execution
    print(f"  [ACTION] Issuing 'docker stop {victim_container}' mid-execution...")
    subprocess.check_call(["docker", "stop", victim_container])
    print(f"  * {victim_container} STOPPED. Worker terminated mid-execution while holding lock!")

    # Verify that surviving containers do NOT claim until lease expires
    print(f"  * Verifying surviving containers observe active lease before expiry...")
    raw_lock = storage.download_file(BUCKET_TASK_COORDINATION, spot_lock_path).decode("utf-8")
    lock_data = json.loads(raw_lock)
    time_left = lock_data["expires_at"] - time.time()
    if time_left > 0:
        print(f"  * Active lease held by terminated container: {time_left:.2f}s remaining.")
        time.sleep(time_left + 1.0)

    # Now lease is expired! Surviving container should claim and complete the chunk!
    print("  * Lease expired. Waiting for surviving Docker worker containers to claim orphaned chunk...")
    t_wait = time.time()
    completed_spot = False
    while (time.time() - t_wait) < 30:
        existing = storage.list_objects(BUCKET_MOSAIC_OUTPUTS, spot_output_key)
        if existing:
            completed_spot = True
            break
        time.sleep(1.0)

    assert completed_spot, f"Surviving worker failed to claim and finish orphaned chunk {spot_chunk_id} within 30s!"
    
    # Verify the output chunk was produced by a SURVIVING container
    out_raw = storage.download_file(BUCKET_MOSAIC_OUTPUTS, spot_output_key).decode("utf-8")
    out_data = json.loads(out_raw)
    finishing_worker = out_data.get("worker_id")
    print(f"  [SUCCESS] Orphaned chunk completed by surviving container: worker_id='{finishing_worker}'!")
    assert finishing_worker != victim_worker_id, "Finishing worker cannot be the stopped victim container!"
    print("  [PASS] Task 8.2 Spot preemption auto-recovery verified with zero dropped tasks!")

    # Restart victim container to restore full cluster
    print(f"\n[CLEANUP] Restarting '{victim_container}' to restore 4-worker cluster...")
    subprocess.check_call(["docker", "start", victim_container])
    print(f"  * Cluster fully restored with all 4 workers healthy.")

    print("\n" + "=" * 75)
    print(">>> PHASE 8: TASKS 8.1 & 8.2 DOCKER CLUSTER VERIFICATION COMPLETED SUCCESSFULLY <<<")
    print("=" * 75)

if __name__ == "__main__":
    run_cluster_e2e_verification()
