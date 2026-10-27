"""
PixelMosaic - Phase 8: Concurrency Load Test (Task 8.1)
Verifies multi-worker distributed locking, zero lock collision,
zero deadlock, and complete OpenCV canvas assembly for a 100x100 (10,000 cell) grid.
"""

import os
import sys
import time
import json
import uuid
import math
import cv2
import numpy as np
from PIL import Image
import concurrent.futures
from collections import defaultdict

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
from services.assembler.assembler import CanvasAssembler

def setup_photo_library(storage, num_tiles=40):
    """Generates synthetic library tiles if needed to ensure CIELAB matching works."""
    existing = storage.list_objects(BUCKET_PHOTO_LIBRARY, "thumbs-50x50/")
    if len(existing) >= 20:
        print(f"  [INFO] Found {len(existing)} existing community tiles in {BUCKET_PHOTO_LIBRARY}")
        return

    print(f"  [INFO] Seeding {num_tiles} distinct color tiles into {BUCKET_PHOTO_LIBRARY}/thumbs-50x50/...")
    # Generate varied color swatches (HSV rainbow palette)
    for i in range(num_tiles):
        hue = int((i / num_tiles) * 180)
        hsv_color = np.uint8([[[hue, 220, 220]]])
        bgr_color = cv2.cvtColor(hsv_color, cv2.COLOR_HSV2BGR)[0][0]
        tile_arr = np.full((50, 50, 3), bgr_color, dtype=np.uint8)
        
        # Add slight texture/pattern so it's a realistic tile
        cv2.circle(tile_arr, (25, 25), 10, (int(bgr_color[0]*0.8), int(bgr_color[1]*0.8), int(bgr_color[2]*0.8)), -1)
        
        _, encoded = cv2.imencode(".jpg", tile_arr)
        key = f"thumbs-50x50/tile_{i:04d}.jpg"
        storage.upload_file(BUCKET_PHOTO_LIBRARY, key, encoded.tobytes(), "image/jpeg")
    print(f"  [SUCCESS] Seeded {num_tiles} tiles successfully.")

def run_concurrency_load_test():
    print("=" * 70)
    print("TASK 8.1: MULTI-WORKER CONCURRENCY LOAD TEST (100x100 CELL PHOTOMOSAIC)")
    print("=" * 70)

    storage = get_storage()
    storage.ensure_buckets_exist()

    # 1. Setup Library
    setup_photo_library(storage)

    # 2. Dispatch a 100x100 Photomosaic Job (10,000 Cells)
    cols = 100
    rows = 100
    tile_w = 50
    tile_h = 50
    target_w = cols * tile_w  # 5000 px
    target_h = rows * tile_h  # 5000 px
    job_id = f"job_loadtest_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    print(f"\n[STEP 1] Generating 100x100 target image ({cols} cols x {rows} rows = 10,000 cells)...")

    # Generate synthetic target poster with colorful gradients
    grad_x = np.linspace(0, 255, target_w, dtype=np.uint8)
    grad_y = np.linspace(0, 255, target_h, dtype=np.uint8)
    gx, gy = np.meshgrid(grad_x, grad_y)
    target_poster = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    target_poster[:, :, 0] = gx
    target_poster[:, :, 1] = gy
    target_poster[:, :, 2] = (gx // 2 + gy // 2)

    # Upload original target poster
    _, encoded_target = cv2.imencode(".jpg", target_poster, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    target_key = f"{job_id}/original.jpg"
    storage.upload_file(BUCKET_TARGET_POSTERS, target_key, encoded_target.tobytes(), "image/jpeg")
    print(f"  [OK] Uploaded target poster -> {BUCKET_TARGET_POSTERS}/{target_key}")

    # Partition into chunks of 400 cells each (25 chunks total for 10,000 cells)
    cells_per_chunk = 400
    total_cells = cols * rows
    total_chunks = math.ceil(total_cells / cells_per_chunk)
    print(f"\n[STEP 2] Slicing into {total_chunks} task chunks ({cells_per_chunk} cells/chunk)...")

    cell_metadata = []
    # Sample average colors per cell (grid of 100x100)
    for r in range(rows):
        for c in range(cols):
            # Center coordinate RGB
            cx = int((c + 0.5) * tile_w)
            cy = int((r + 0.5) * tile_h)
            bgr = target_poster[cy, cx]
            rgb = [int(bgr[2]), int(bgr[1]), int(bgr[0])]
            cell_metadata.append({
                "cell_index": r * cols + c,
                "col": c,
                "row": r,
                "pixel_x": c * tile_w,
                "pixel_y": r * tile_h,
                "width": tile_w,
                "height": tile_h,
                "target_rgb": rgb
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

    # Write job manifest
    manifest_data = {
        "job_id": job_id,
        "mode": "photomosaic",
        "total_chunks": total_chunks,
        "total_cells": total_cells,
        "grid_dimensions": {"columns": cols, "rows": rows},
        "target_dimensions": {"width": target_w, "height": target_h},
        "parameters": {
            "cell_size": tile_w,
            "metric": "CIELAB_DELTA_E_2000",
            "alpha_tint_percentage": 20
        },
        "created_at": time.time()
    }
    manifest_key = f"jobs/{job_id}/manifest.json"
    storage.upload_file(
        BUCKET_TASK_COORDINATION,
        manifest_key,
        json.dumps(manifest_data).encode("utf-8"),
        "application/json"
    )
    print(f"  [OK] Uploaded manifest with {total_chunks} chunks -> {BUCKET_TASK_COORDINATION}/{manifest_key}")

    # 3. Spin Up 4 Concurrent Worker Nodes
    worker_names = ["Worker-Alpha", "Worker-Beta", "Worker-Gamma", "Worker-Delta"]
    workers = []
    for name in worker_names:
        w = ComputeWorker()
        w.worker_id = name
        workers.append(w)

    print(f"\n[STEP 3] Launching 4 concurrent worker nodes: {worker_names}")
    worker_chunk_claims = defaultdict(list)
    lock_acquisition_log = []
    start_time = time.time()

    def worker_runner(worker: ComputeWorker):
        worker_id = worker.worker_id
        claimed_count = 0
        consecutive_idle = 0
        while consecutive_idle < 6:
            # Check remaining unfinished chunks
            chunk_claimed = False
            task_json_keys = [
                k for k in storage.list_objects(BUCKET_TASK_COORDINATION, f"jobs/{job_id}/tasks/")
                if k.endswith(".json")
            ]
            for task_key in task_json_keys:
                chunk_id = os.path.basename(task_key).replace(".json", "")
                output_key = f"jobs/{job_id}/chunks/{chunk_id}.json"
                if storage.list_objects(BUCKET_MOSAIC_OUTPUTS, output_key):
                    continue

                lock_path = f"jobs/{job_id}/locks/{chunk_id}.lock"
                acquired = storage.acquire_lock(
                    bucket_name=BUCKET_TASK_COORDINATION,
                    lock_path=lock_path,
                    worker_id=worker_id,
                    ttl_seconds=60
                )
                if acquired:
                    lock_acquisition_log.append({
                        "worker_id": worker_id,
                        "chunk_id": chunk_id,
                        "timestamp": time.time()
                    })
                    worker_chunk_claims[worker_id].append(chunk_id)
                    print(f"  --> [{worker_id}] LOCKED & PROCESSING {chunk_id}")
                    try:
                        worker.execute_task_chunk(job_id, task_key, chunk_id)
                        storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, worker_id)
                        claimed_count += 1
                        chunk_claimed = True
                        consecutive_idle = 0
                    except Exception as ex:
                        print(f"  [!] [{worker_id}] Execution error on {chunk_id}: {ex}")
                        storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, worker_id)
                    break

            if not chunk_claimed:
                consecutive_idle += 1
                time.sleep(0.5)

        return claimed_count

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(worker_runner, w): w.worker_id for w in workers}
        results = {futures[f]: f.result() for f in concurrent.futures.as_completed(futures)}

    duration = time.time() - start_time
    print(f"\n[STEP 4] Multi-Worker Load Test Results (Completed in {duration:.2f}s):")
    for wid, count in results.items():
        print(f"  * {wid}: processed {count} chunks ({worker_chunk_claims[wid]})")

    # 4. Concurrency Invariant Validations
    print("\n[STEP 5] Verifying Concurrency & Lock Invariants:")
    
    # Invariant A: All 4 workers must participate
    active_workers = [wid for wid, count in results.items() if count > 0]
    print(f"  * Participating concurrent workers: {len(active_workers)}/4 ({active_workers})")
    assert len(active_workers) == 4, f"Expected all 4 workers to participate, got {len(active_workers)}"
    print("  [PASS] All 4 worker nodes concurrently acquired locks!")

    # Invariant B: Zero Lock Collisions
    all_claimed_chunks = []
    for wid, chunks in worker_chunk_claims.items():
        all_claimed_chunks.extend(chunks)
    unique_claimed_chunks = set(all_claimed_chunks)
    print(f"  * Total chunk lock executions: {len(all_claimed_chunks)}")
    print(f"  * Unique chunks processed: {len(unique_claimed_chunks)}")
    assert len(all_claimed_chunks) == len(unique_claimed_chunks), "Collision detected! Duplicate chunk processed."
    print("  [PASS] Zero Lock Collisions! Every chunk was exclusively held by exactly one worker.")

    # Invariant C: Zero Deadlocks - All 25 chunks completed
    output_chunks = storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/")
    print(f"  * Output chunks in {BUCKET_MOSAIC_OUTPUTS}: {len(output_chunks)}/{total_chunks}")
    assert len(output_chunks) == total_chunks, f"Deadlock or missing chunks: expected {total_chunks}, got {len(output_chunks)}"
    print("  [PASS] Zero Deadlocks! All 25 chunks completed and uploaded.")

    # 5. Canvas Assembler Verification
    print("\n[STEP 6] Running Canvas Assembler Stitching & Missing Tile Check...")
    assembler = CanvasAssembler()
    assembler.assemble_mosaic(job_id, manifest_data, output_chunks)

    # Verify final mosaic output in bucket-mosaic-outputs
    final_mosaic_key = f"jobs/{job_id}/final_mosaic.png"
    final_thumb_key = f"jobs/{job_id}/final_mosaic_thumb.jpg"
    
    mosaic_bytes = storage.download_file(BUCKET_MOSAIC_OUTPUTS, final_mosaic_key)
    thumb_bytes = storage.download_file(BUCKET_MOSAIC_OUTPUTS, final_thumb_key)
    assert len(mosaic_bytes) > 0, "final_mosaic.png is empty or missing"
    assert len(thumb_bytes) > 0, "final_mosaic_thumb.jpg is empty or missing"

    # Decode and verify dimensions and tile coverage
    mosaic_mat = cv2.imdecode(np.frombuffer(mosaic_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert mosaic_mat is not None, "Failed to decode final_mosaic.png"
    assert mosaic_mat.shape == (target_h, target_w, 3), f"Shape mismatch: expected {(target_h, target_w, 3)}, got {mosaic_mat.shape}"

    # Check for unrendered black/blank tiles (zero missing tiles assertion)
    # Target poster was non-zero everywhere; stitched mosaic should have valid non-zero content across all 10,000 cells
    print(f"  * Final Master Mosaic Dimensions: {mosaic_mat.shape[1]}x{mosaic_mat.shape[0]} px ({len(mosaic_bytes)/1024/1024:.2f} MB)")
    print("  * Validating zero missing tiles across all 10,000 cells...")
    
    missing_cells = 0
    for r in range(rows):
        for c in range(cols):
            cell_sample = mosaic_mat[r * tile_h : (r + 1) * tile_h, c * tile_w : (c + 1) * tile_w]
            if np.mean(cell_sample) == 0:
                missing_cells += 1

    assert missing_cells == 0, f"Detected {missing_cells} missing or unrendered cells!"
    print(f"  [PASS] Zero missing tiles! Complete 10,000 cell photorealistic raster assembly verified.")

    print("\n" + "=" * 70)
    print(">>> TASK 8.1 CONCURRENCY LOAD TEST: ALL INVARIANTS PASSED <<<")
    print("=" * 70)

if __name__ == "__main__":
    run_concurrency_load_test()
