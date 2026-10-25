"""
Crowd-Sourced Collaborative Art & High-Compute Raster Platform
Canvas Assembler & Output Pipeline Service (Tasks 4.1 & 4.2)
Monitors job chunk completions, stitches tiles into master artboards using OpenCV,
and applies dynamic alpha-tinting blending.
"""

import os
import sys
import time
import json
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("assembler")

storage = get_storage()


class CanvasAssembler:
    def __init__(self):
        self.is_running = True
        self.processed_jobs = set()
        logger.info("Canvas Assembler service initialized.")

    def run_poll_loop(self):
        """Monitors active jobs for full completion."""
        logger.info("Starting assembler monitoring loop...")
        while self.is_running:
            try:
                self.check_active_jobs()
                time.sleep(3.0)
            except Exception as e:
                logger.error(f"Error in assembler loop: {e}")
                time.sleep(4.0)

    def check_active_jobs(self):
        # Discover jobs from manifest keys
        manifest_keys = [k for k in storage.list_objects(BUCKET_TASK_COORDINATION, "jobs/") if k.endswith("manifest.json")]

        for m_key in manifest_keys:
            job_id = m_key.split("/")[1]
            if job_id in self.processed_jobs:
                continue

            try:
                manifest = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, m_key).decode("utf-8"))
            except Exception:
                continue

            total_chunks = manifest.get("total_chunks", 1)
            chunk_files = storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/")
            
            if len(chunk_files) >= total_chunks and total_chunks > 0:
                logger.info(f"All {total_chunks} chunks ready for {job_id}. Beginning assembly...")
                try:
                    self.assemble_mosaic(job_id, manifest, chunk_files)
                    self.processed_jobs.add(job_id)
                except Exception as ex:
                    logger.error(f"Assembly failed for {job_id}: {ex}")

    def assemble_mosaic(self, job_id: str, manifest: dict, chunk_files: list):
        """Stitches chunks into master canvas and applies dynamic alpha-tinting."""
        dims = manifest.get("target_dimensions", {"width": 2000, "height": 2000})
        w = dims.get("width", 2000)
        h = dims.get("height", 2000)
        alpha_tint = int(manifest.get("parameters", {}).get("alpha_tint_percentage", 20))

        master_canvas = np.zeros((h, w, 3), dtype=np.uint8)

        # Collect all cell matches
        all_cells = []
        for c_file in chunk_files:
            try:
                data = json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, c_file).decode("utf-8"))
                all_cells.extend(data.get("cells", []))
            except Exception as e:
                logger.warning(f"Failed reading chunk {c_file}: {e}")

        logger.info(f"Stitching {len(all_cells)} tiles for {job_id}...")

        # Image cache to avoid re-downloading identical tiles
        tile_cache = {}

        for cell in all_cells:
            px = cell["pixel_x"]
            py = cell["pixel_y"]
            cw = cell["width"]
            ch = cell["height"]
            target_rgb = cell.get("target_rgb", [128, 128, 128])
            matched_key = cell.get("matched_key")

            tile_bgr = None
            if matched_key:
                if matched_key in tile_cache:
                    tile_bgr = tile_cache[matched_key]
                else:
                    try:
                        raw_data = storage.download_file(BUCKET_PHOTO_LIBRARY, matched_key)
                        arr = np.frombuffer(raw_data, np.uint8)
                        decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if decoded is not None:
                            tile_bgr = cv2.resize(decoded, (cw, ch))
                            tile_cache[matched_key] = tile_bgr
                    except Exception:
                        pass

            if tile_bgr is not None:
                master_canvas[py : py + ch, px : px + cw] = tile_bgr
            else:
                # Fallback to target color fill
                bgr_color = [target_rgb[2], target_rgb[1], target_rgb[0]]
                master_canvas[py : py + ch, px : px + cw] = bgr_color

        # ----------------------------------------------------------------------
        # Task 4.2: Dynamic Alpha-Tinting Compositor
        # ----------------------------------------------------------------------
        if alpha_tint > 0:
            try:
                target_data = storage.download_file(BUCKET_TARGET_POSTERS, f"{job_id}/original.jpg")
                target_bgr = cv2.imdecode(np.frombuffer(target_data, np.uint8), cv2.IMREAD_COLOR)
                if target_bgr is not None:
                    target_resized = cv2.resize(target_bgr, (w, h))
                    alpha = min(0.5, alpha_tint / 100.0)
                    # I_final = (1 - alpha) * Master + alpha * Target
                    master_canvas = cv2.addWeighted(master_canvas, 1.0 - alpha, target_resized, alpha, 0)
                    logger.info(f"Applied {alpha_tint}% alpha-tint overlay.")
            except Exception as e:
                logger.warning(f"Alpha tinting skipped for {job_id}: {e}")

        # Upload final master mosaic
        _, png_bytes = cv2.imencode(".png", master_canvas, [int(cv2.IMWRITE_PNG_COMPRESSION), 6])
        final_key = f"jobs/{job_id}/final_mosaic.png"
        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, final_key, png_bytes.tobytes(), "image/png")

        # Also upload fast preview thumbnail
        thumb_canvas = cv2.resize(master_canvas, (800, int(800 * (h / w))))
        _, thumb_bytes = cv2.imencode(".jpg", thumb_canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        thumb_key = f"jobs/{job_id}/final_mosaic_thumb.jpg"
        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, thumb_key, thumb_bytes.tobytes(), "image/jpeg")

        logger.info(f"Final mosaic assembled and uploaded -> {final_key}")


if __name__ == "__main__":
    assembler = CanvasAssembler()
    assembler.run_poll_loop()
