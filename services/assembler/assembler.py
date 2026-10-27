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
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

# Ensure common package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from services.common.storage import (
    get_storage,
    BUCKET_PHOTO_LIBRARY,
    BUCKET_TARGET_POSTERS,
    BUCKET_TASK_COORDINATION,
    BUCKET_MOSAIC_OUTPUTS
)
from services.common.photo_library import render_tile_candidates
from services.common.telemetry import HISTORY_SAMPLE_EVERY, read_cluster_telemetry
from services.common.service_runtime import ServiceRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("assembler")

storage = get_storage()

PREVIEW_LONG_EDGE = 2400
THUMB_LONG_EDGE = 800
ASSEMBLY_LOCK_TTL = 600  # a 12000px canvas takes well under a minute; generous for slow VMs
ASSEMBLER_ID = f"assembler_{socket.gethostname()}_{uuid.uuid4().hex[:4]}"


def _decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None


def _square(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    side = min(h, w)
    y0, x0 = (h - side) // 2, (w - side) // 2
    return img[y0:y0 + side, x0:x0 + side]


def _fit(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = long_edge / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


class CanvasAssembler:
    def __init__(self):
        self.is_running = True
        self.processed_jobs = set()
        self.failed_attempts = {}
        logger.info("Canvas Assembler service initialized.")

    def run_poll_loop(self, runtime: ServiceRuntime):
        """Monitors active jobs for full completion until SIGTERM."""
        logger.info("Starting assembler monitoring loop...")
        last_sample = 0.0
        while runtime.running:
            runtime.beat()
            try:
                self.check_active_jobs()
                # Keeps the telemetry history sampled even when no admin is watching
                if time.time() - last_sample >= HISTORY_SAMPLE_EVERY:
                    last_sample = time.time()
                    read_cluster_telemetry(storage, BUCKET_TASK_COORDINATION)
                runtime.sleep(1.0)
            except Exception as e:
                logger.error(f"Error in assembler loop: {e}")
                runtime.sleep(2.0)
        logger.info("Assembler stopped cleanly.")

    def check_active_jobs(self):
        # Discover jobs from manifest keys
        manifest_keys = [k for k in storage.list_objects(BUCKET_TASK_COORDINATION, "jobs/") if k.endswith("manifest.json")]

        for m_key in manifest_keys:
            job_id = m_key.split("/")[1]
            if job_id in self.processed_jobs:
                continue
            # Already assembled (e.g. before an assembler restart) or permanently failed
            if storage.object_exists(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/final_mosaic.png") or \
                    storage.object_exists(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/error.json"):
                self.processed_jobs.add(job_id)
                continue

            try:
                manifest = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, m_key).decode("utf-8"))
            except Exception:
                continue

            total_chunks = manifest.get("total_chunks", 1)
            chunk_files = storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/")

            if len(chunk_files) >= total_chunks and total_chunks > 0:
                # Several assembler VMs may run (e.g. during a rolling update): only one stitches a job.
                lock_path = f"jobs/{job_id}/locks/assembly.lock"
                if not storage.acquire_lock(BUCKET_TASK_COORDINATION, lock_path, ASSEMBLER_ID, ASSEMBLY_LOCK_TTL):
                    continue
                if storage.object_exists(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/final_mosaic.png"):
                    storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, ASSEMBLER_ID)
                    self.processed_jobs.add(job_id)
                    continue
                logger.info(f"All {total_chunks} chunks ready for {job_id}. Beginning assembly...")
                try:
                    started = time.time()
                    if manifest.get("mode", "photomosaic") == "photomosaic":
                        self.assemble_mosaic(job_id, manifest, chunk_files)
                    else:
                        self.finalize_render(job_id, chunk_files)
                    self.processed_jobs.add(job_id)
                    logger.info(f"Job {job_id} assembled in {time.time() - started:.1f}s")
                except Exception as ex:
                    attempts = self.failed_attempts.get(job_id, 0) + 1
                    self.failed_attempts[job_id] = attempts
                    logger.exception(f"Assembly failed for {job_id} (attempt {attempts}): {ex}")
                    if attempts >= 3:
                        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/error.json",
                                            json.dumps({"error": str(ex), "at": time.time()}).encode("utf-8"),
                                            "application/json")
                        self.processed_jobs.add(job_id)
                finally:
                    storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, ASSEMBLER_ID)

    def _load_tiles(self, keys, tile_size: int):
        """Downloads each distinct matched photo once (best available resolution) and sizes it."""
        def fetch(thumb_key):
            for candidate in render_tile_candidates(thumb_key, tile_size):
                try:
                    img = _decode(storage.download_file(BUCKET_PHOTO_LIBRARY, candidate))
                except Exception:
                    img = None
                if img is not None:
                    img = _square(img)
                    interp = cv2.INTER_AREA if img.shape[0] > tile_size else cv2.INTER_CUBIC
                    return thumb_key, cv2.resize(img, (tile_size, tile_size), interpolation=interp)
            return thumb_key, None

        with ThreadPoolExecutor(max_workers=16) as ex:
            return {k: v for k, v in ex.map(fetch, keys) if v is not None}

    def _publish(self, job_id: str, master_canvas: np.ndarray):
        """Uploads the full-resolution PNG plus display preview and thumbnail JPEGs."""
        ok, png_bytes = cv2.imencode(".png", master_canvas, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
        if not ok:
            raise ValueError("PNG encode failed")
        for name, edge in (("final_mosaic_preview.jpg", PREVIEW_LONG_EDGE), ("final_mosaic_thumb.jpg", THUMB_LONG_EDGE)):
            _, jpg = cv2.imencode(".jpg", _fit(master_canvas, edge), [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            storage.upload_file(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/{name}", jpg.tobytes(), "image/jpeg")
        # Final PNG last: its existence is the job-complete signal, so previews must already exist.
        final_key = f"jobs/{job_id}/final_mosaic.png"
        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, final_key, png_bytes.tobytes(), "image/png")
        logger.info(f"Final artboard uploaded -> {final_key} ({master_canvas.shape[1]}x{master_canvas.shape[0]})")

    def finalize_render(self, job_id: str, chunk_files: list):
        """Whole-image engines (ASCII / Kuwahara / flow field) render in the worker; publish that render."""
        chunk = json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, sorted(chunk_files)[0]))
        render_key = chunk.get("render_key")
        if not render_key:
            raise ValueError("Worker output has no render")
        img = _decode(storage.download_file(BUCKET_MOSAIC_OUTPUTS, render_key))
        if img is None:
            raise ValueError("Worker render could not be decoded")
        self._publish(job_id, img)

    def assemble_mosaic(self, job_id: str, manifest: dict, chunk_files: list):
        """Stitches chunks into master canvas and applies dynamic alpha-tinting."""
        dims = manifest.get("target_dimensions", {"width": 2000, "height": 2000})
        w = dims.get("width", 2000)
        h = dims.get("height", 2000)

        master_canvas = np.zeros((h, w, 3), dtype=np.uint8)

        # Collect all cell matches
        all_cells = []
        for c_file in chunk_files:
            data = json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, c_file).decode("utf-8"))
            all_cells.extend(data.get("cells", []))

        tile_size = all_cells[0]["width"] if all_cells else manifest.get("grid", {}).get("cell_size", 50)
        unique_keys = sorted({c["matched_key"] for c in all_cells if c.get("matched_key")})
        logger.info(f"Stitching {len(all_cells)} cells for {job_id} from {len(unique_keys)} distinct photos...")
        tiles = self._load_tiles(unique_keys, tile_size)
        missing = len(unique_keys) - len(tiles)
        if missing:
            logger.warning(f"{missing} matched photos were unavailable (deleted?); using colour fill for those cells.")

        for cell in all_cells:
            px, py = cell["pixel_x"], cell["pixel_y"]
            cw, ch = cell["width"], cell["height"]
            actual_ch = min(ch, h - py)
            actual_cw = min(cw, w - px)
            if actual_ch <= 0 or actual_cw <= 0:
                continue
            tile = tiles.get(cell.get("matched_key"))
            if tile is not None:
                if tile.shape[0] != ch or tile.shape[1] != cw:
                    tile = cv2.resize(tile, (cw, ch), interpolation=cv2.INTER_AREA)
                master_canvas[py:py + actual_ch, px:px + actual_cw] = tile[:actual_ch, :actual_cw]
            else:
                r, g, b = cell.get("target_rgb", [128, 128, 128])
                master_canvas[py:py + actual_ch, px:px + actual_cw] = (b, g, r)

        # The artboard is stored untinted. The alpha tint (BRD 4.5) is composited by the gateway when
        # the artboard is viewed or exported, so the slider can still be adjusted after completion.
        self._publish(job_id, master_canvas)


if __name__ == "__main__":
    runtime = ServiceRuntime("assembler", stall_after_sec=ASSEMBLY_LOCK_TTL * 2)
    runtime.install_signal_handlers()
    runtime.start_health_server()
    assembler = CanvasAssembler()
    assembler.run_poll_loop(runtime)
