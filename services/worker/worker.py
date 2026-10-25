"""
Crowd-Sourced Collaborative Art & High-Compute Raster Platform
Distributed Compute Worker Core (Tasks 3.1, 3.2, 3.3, 3.4, 3.5, 3.6)
Runs on CPU-bound Managed Instance Groups (MIG) with Spot VMs.
Zero GPU dependencies • Atomic bucket locking (if-generation-match=0).
"""

import os
import sys
import time
import json
import uuid
import socket
import logging
import io
import cv2
import numpy as np
from PIL import Image

# Ensure common package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from services.common.storage import (
    get_storage,
    BUCKET_PHOTO_LIBRARY,
    BUCKET_TARGET_POSTERS,
    BUCKET_TASK_COORDINATION,
    BUCKET_MOSAIC_OUTPUTS
)
from services.worker.engines import (
    rgb_to_cielab,
    delta_e_cie76,
    delta_e_ciede2000,
    SpatialRepetitionBuffer,
    AsciiEngine,
    VoronoiEngine,
    DeepZoomEngine
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("worker")

storage = get_storage()
WORKER_ID = f"{os.getenv('WORKER_ID_PREFIX', 'worker')}_{socket.gethostname()}_{uuid.uuid4().hex[:4]}"


class ComputeWorker:
    def __init__(self):
        self.worker_id = WORKER_ID
        self.is_running = True
        self.cached_library = []  # In-memory index of available library thumbnails
        self.last_library_scan = 0
        logger.info(f"Compute Worker initialized with ID: {self.worker_id}")

    def refresh_library_index(self):
        """Refreshes list of community photo tiles from bucket-photo-library."""
        now = time.time()
        if now - self.last_library_scan < 30 and len(self.cached_library) > 0:
            return

        thumb_keys = storage.list_objects(BUCKET_PHOTO_LIBRARY, "thumbs-50x50/")
        if not thumb_keys:
            # Also check events/ thumbnails
            thumb_keys = [k for k in storage.list_objects(BUCKET_PHOTO_LIBRARY, "events/") if "thumbs-50x50" in k]

        library = []
        for key in thumb_keys[:500]:  # Index candidate tiles
            try:
                # Precompute average RGB from image
                data = storage.download_file(BUCKET_PHOTO_LIBRARY, key)
                img = Image.open(io.BytesIO(data)).convert("RGB")
                avg_rgb = list(img.resize((1, 1)).getpixel((0, 0)))
                library.append({
                    "id": os.path.basename(key),
                    "key": key,
                    "rgb": avg_rgb,
                    "lab": rgb_to_cielab(np.array(avg_rgb))[0].tolist()
                })
            except Exception:
                pass

        self.cached_library = library
        self.last_library_scan = now
        logger.info(f"Indexed {len(self.cached_library)} community library tiles.")

    def run_poll_loop(self):
        """Continuously polls bucket-task-coordination for unreserved chunk tasks."""
        logger.info("Starting distributed worker task claim loop...")
        while self.is_running:
            try:
                task_claimed = self.find_and_claim_task()
                if not task_claimed:
                    time.sleep(2.0)  # Backoff when queue is empty
            except Exception as e:
                logger.error(f"Error in worker poll loop: {e}")
                time.sleep(3.0)

    def find_and_claim_task(self) -> bool:
        """
        Scans bucket-task-coordination for tasks, attempts atomic lock reservation,
        and executes chunk if lock is acquired.
        """
        # List all job task folders
        all_task_keys = storage.list_objects(BUCKET_TASK_COORDINATION, "jobs/")
        task_json_keys = [k for k in all_task_keys if "/tasks/chunk_" in k and k.endswith(".json")]

        if not task_json_keys:
            return False

        for task_key in task_json_keys:
            # e.g., jobs/job_123/tasks/chunk_0001.json
            parts = task_key.split("/")
            job_id = parts[1]
            chunk_file = parts[-1]
            chunk_id = chunk_file.replace(".json", "")

            # Check if output chunk already exists
            output_chunk_key = f"jobs/{job_id}/chunks/{chunk_id}.json"
            existing_outputs = storage.list_objects(BUCKET_MOSAIC_OUTPUTS, output_chunk_key)
            if existing_outputs:
                continue

            # Task 3.1: Attempt Atomic Bucket Lock
            lock_path = f"jobs/{job_id}/locks/{chunk_id}.lock"
            acquired = storage.acquire_lock(
                bucket_name=BUCKET_TASK_COORDINATION,
                lock_path=lock_path,
                worker_id=self.worker_id,
                ttl_seconds=60
            )

            if acquired:
                logger.info(f"Worker {self.worker_id} acquired atomic lock on {task_key}")
                try:
                    self.execute_task_chunk(job_id, task_key, chunk_id)
                    # Task completed: write output and release lock
                    return True
                except Exception as ex:
                    logger.error(f"Task chunk execution failed: {ex}")
                    storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, self.worker_id)
                    return False

        return False

    def execute_task_chunk(self, job_id: str, task_key: str, chunk_id: str):
        """Downloads task definition, processes cells with appropriate engine, and writes result."""
        task_data = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, task_key).decode("utf-8"))
        mode = task_data.get("mode", "photomosaic")
        cells = task_data.get("cells", [])
        metric = task_data.get("metric", "CIELAB_DELTA_E_2000")
        no_repeat_radius = int(task_data.get("no_repeat_radius", 5))

        logger.info(f"Executing {mode.upper()} engine for {chunk_id} ({len(cells)} cells)...")

        # ----------------------------------------------------------------------
        # Mode 1: CIELAB Photomosaic Engine (Task 3.2)
        # ----------------------------------------------------------------------
        if mode == "photomosaic":
            self.refresh_library_index()
            rep_buffer = SpatialRepetitionBuffer(radius=no_repeat_radius)

            # Pre-convert library colors into matrix
            if self.cached_library:
                lib_labs = np.array([item["lab"] for item in self.cached_library], dtype=np.float64)
            else:
                lib_labs = None

            chunk_results = []
            for cell in cells:
                col = cell["col"]
                row = cell["row"]
                target_rgb = np.array(cell["target_rgb"], dtype=np.float64)
                target_lab = rgb_to_cielab(target_rgb)

                best_match = None
                best_distance = 999999.0

                if lib_labs is not None and len(lib_labs) > 0:
                    # Vectorized Delta-E computation across entire library in 1 pass
                    if metric == "CIELAB_DELTA_E_2000":
                        distances = delta_e_ciede2000(target_lab, lib_labs)
                    else:
                        distances = delta_e_cie76(target_lab, lib_labs)

                    # Sorted candidates for spatial no-repeat checking
                    sorted_indices = np.argsort(distances)
                    
                    for idx in sorted_indices:
                        candidate = self.cached_library[idx]
                        if rep_buffer.is_eligible(candidate["id"], col, row):
                            best_match = candidate
                            best_distance = float(distances[idx])
                            rep_buffer.record_placement(candidate["id"], col, row)
                            break

                    # Soft fallback if exhausted
                    if best_match is None and len(sorted_indices) > 0:
                        best_match = self.cached_library[sorted_indices[0]]
                        best_distance = float(distances[sorted_indices[0]])

                chunk_results.append({
                    "cell_index": cell["cell_index"],
                    "col": col,
                    "row": row,
                    "pixel_x": cell["pixel_x"],
                    "pixel_y": cell["pixel_y"],
                    "width": cell["width"],
                    "height": cell["height"],
                    "target_rgb": cell["target_rgb"],
                    "matched_id": best_match["id"] if best_match else None,
                    "matched_key": best_match["key"] if best_match else None,
                    "matched_rgb": best_match["rgb"] if best_match else cell["target_rgb"],
                    "delta_e": round(best_distance, 2)
                })

            output_payload = {
                "job_id": job_id,
                "chunk_id": chunk_id,
                "mode": mode,
                "worker_id": self.worker_id,
                "cells": chunk_results,
                "completed_at": time.time()
            }

        # ----------------------------------------------------------------------
        # Mode 2: Structural ASCII Engine (Task 3.3)
        # ----------------------------------------------------------------------
        elif mode == "ascii":
            ascii_engine = AsciiEngine()
            # Fetch target poster and process
            target_data = storage.download_file(BUCKET_TARGET_POSTERS, f"{job_id}/original.jpg")
            img_bgr = cv2.imdecode(np.frombuffer(target_data, np.uint8), cv2.IMREAD_COLOR)
            ascii_res = ascii_engine.process_image(img_bgr, cols=80)
            
            output_payload = {
                "job_id": job_id,
                "chunk_id": chunk_id,
                "mode": mode,
                "worker_id": self.worker_id,
                "ascii_text": ascii_res["ascii_text"],
                "completed_at": time.time()
            }

        # ----------------------------------------------------------------------
        # Mode 3: Voronoi Stained Glass (Task 3.4)
        # ----------------------------------------------------------------------
        elif mode == "voronoi":
            voronoi_engine = VoronoiEngine()
            target_data = storage.download_file(BUCKET_TARGET_POSTERS, f"{job_id}/original.jpg")
            img_bgr = cv2.imdecode(np.frombuffer(target_data, np.uint8), cv2.IMREAD_COLOR)
            canvas_bgr = voronoi_engine.render(img_bgr, border_width=1)
            
            # Save raster output
            _, encoded_png = cv2.imencode(".png", canvas_bgr)
            voronoi_key = f"jobs/{job_id}/voronoi_render.png"
            storage.upload_file(BUCKET_MOSAIC_OUTPUTS, voronoi_key, encoded_png.tobytes(), "image/png")

            output_payload = {
                "job_id": job_id,
                "chunk_id": chunk_id,
                "mode": mode,
                "worker_id": self.worker_id,
                "render_url": f"/{BUCKET_MOSAIC_OUTPUTS}/{voronoi_key}",
                "completed_at": time.time()
            }

        # ----------------------------------------------------------------------
        # Mode 4: Deep-Zoom Pyramid Slicer (Task 3.5)
        # ----------------------------------------------------------------------
        elif mode == "deepzoom":
            deepzoom_engine = DeepZoomEngine()
            target_data = storage.download_file(BUCKET_TARGET_POSTERS, f"{job_id}/original.jpg")
            img_bgr = cv2.imdecode(np.frombuffer(target_data, np.uint8), cv2.IMREAD_COLOR)
            h, w = img_bgr.shape[:2]

            dzi_xml = deepzoom_engine.generate_dzi_xml(w, h)
            dzi_key = f"jobs/{job_id}/deepzoom/{job_id}.dzi"
            storage.upload_file(BUCKET_MOSAIC_OUTPUTS, dzi_key, dzi_xml.encode("utf-8"), "application/xml")

            # Slice pyramid levels
            pyramid = deepzoom_engine.slice_pyramid(img_bgr)
            for tile_rel_path, tile_bytes in pyramid.items():
                tile_key = f"jobs/{job_id}/deepzoom/{job_id}_files/{tile_rel_path}"
                storage.upload_file(BUCKET_MOSAIC_OUTPUTS, tile_key, tile_bytes, "image/jpeg")

            output_payload = {
                "job_id": job_id,
                "chunk_id": chunk_id,
                "mode": mode,
                "dzi_url": f"/{BUCKET_MOSAIC_OUTPUTS}/{dzi_key}",
                "completed_at": time.time()
            }

        else:
            output_payload = {"job_id": job_id, "chunk_id": chunk_id, "status": "COMPLETED"}

        # Write chunk output to bucket-mosaic-outputs
        out_key = f"jobs/{job_id}/chunks/{chunk_id}.json"
        storage.upload_file(
            BUCKET_MOSAIC_OUTPUTS,
            out_key,
            json.dumps(output_payload).encode("utf-8"),
            "application/json"
        )

        logger.info(f"Worker {self.worker_id} successfully finished {chunk_id} -> {out_key}")


if __name__ == "__main__":
    worker = ComputeWorker()
    worker.run_poll_loop()
