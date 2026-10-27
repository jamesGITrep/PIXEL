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
import zlib
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any, Set

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Ensure common package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from services.common.storage import (
    get_storage,
    BUCKET_PHOTO_LIBRARY,
    BUCKET_TARGET_POSTERS,
    BUCKET_TASK_COORDINATION,
    BUCKET_MOSAIC_OUTPUTS
)
from services.common.photo_library import (
    IMAGE_EXTS,
    INCOMING_PREFIX,
    index_key_for,
    ingest_photo,
)
from services.common.telemetry import HeartbeatReporter
from services.common.service_runtime import ServiceRuntime
from services.worker.engines import (
    rgb_to_cielab,
    delta_e_cie76,
    delta_e_ciede2000,
    SpatialRepetitionBuffer,
    AsciiEngine,
    KuwaharaEngine,
    FlowFieldEngine
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("worker")

storage = get_storage()
WORKER_ID = f"{os.getenv('WORKER_ID_PREFIX', 'worker')}_{socket.gethostname()}_{uuid.uuid4().hex[:4]}"

LOCK_TTL_SECONDS = 120
FEATURE_CACHE_MAX = 50000
CANDIDATES = 256       # per cell (per source when blending): shortlisted by cheap Lab distance, then scored with CIEDE2000
CANDIDATE_BLOCK = 512  # cells per matrix block (bounds memory: 512 x pool size float32)

ASCII_THEMES = {
    "cyan": ((7, 9, 14), (56, 189, 248)),
    "green": ((4, 13, 6), (16, 185, 129)),
    "amber": ((18, 13, 3), (245, 158, 11)),
    "mono": ((0, 0, 0), (248, 250, 252)),
    "truecolor": ((6, 7, 10), None),  # each glyph drawn in its cell's source-photo colour
}
ASCII_RAMPS = {
    "standard": " .:-=+*#%@",
    "blocks": " ░▒▓█",
}


INCOMING_STALE_META_SEC = 3600  # signed URLs expire after 30 min; metadata without an upload is dropped after 1h


def _features_from_rgb(avg, sub) -> Dict[str, Any]:
    avg = np.asarray(avg, dtype=np.float64).reshape(3)
    sub = np.asarray(sub, dtype=np.float64).reshape(4, 3)
    return {
        "rgb": [int(round(v)) for v in avg],
        "lab": rgb_to_cielab(avg)[0],
        "sub_lab": rgb_to_cielab(sub),
        "raw": [[round(float(v), 2) for v in avg], [[round(float(v), 2) for v in s] for s in sub]],
    }


def tile_features(image_bytes: bytes) -> Dict[str, Any]:
    """Mean colour and 2x2 sub-region colours of a tile image, in RGB and CIELAB."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    avg = np.asarray(img.resize((1, 1), Image.Resampling.BOX), dtype=np.float64)
    sub = np.asarray(img.resize((2, 2), Image.Resampling.BOX), dtype=np.float64)
    return _features_from_rgb(avg, sub)


def fetch_features(thumb_key: str) -> Dict[str, Any]:
    """Reads the photo-index entry written at ingestion; falls back to analysing the thumbnail."""
    index_key = index_key_for(thumb_key)
    if index_key:
        try:
            entry = json.loads(storage.download_file(BUCKET_PHOTO_LIBRARY, index_key, quiet=True))
            return _features_from_rgb(entry["rgb"], entry["sub_rgb"])
        except Exception:
            pass
    return tile_features(storage.download_file(BUCKET_PHOTO_LIBRARY, thumb_key))


# Shared colour-feature cache for the community pool. Photos never change once ingested, so any
# worker that has fetched features publishes them here and a freshly started worker loads the
# whole community pool with one download instead of one request per photo.
FEATURE_SNAPSHOT_KEY = "cache/community-features.json"
FEATURE_SNAPSHOT_MIN_NEW = 50  # republish once this many new community features were fetched


class LeaseKeeper:
    """
    Renews a chunk lock while the chunk is being computed. The lease (LOCK_TTL_SECONDS) only has
    to outlive a crashed or preempted worker; a live worker keeps extending it, so long chunks are
    never picked up a second time by another worker.
    """

    def __init__(self, lock_path: str, worker_id: str):
        self.lock_path = lock_path
        self.worker_id = worker_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(LOCK_TTL_SECONDS / 4):
            try:
                storage.renew_lock(BUCKET_TASK_COORDINATION, self.lock_path, self.worker_id, LOCK_TTL_SECONDS)
            except Exception as e:
                logger.warning(f"Lease renewal failed for {self.lock_path}: {e}")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)
        return False


class ComputeWorker:
    def __init__(self):
        self.worker_id = WORKER_ID
        self.is_running = True
        self.feature_cache: Dict[str, Dict[str, Any]] = {}   # thumb key -> features (immutable objects)
        self.task_cache: Dict[str, Dict[str, Any]] = {}      # task key -> task json (immutable)
        self.finished_jobs: Set[str] = set()
        self.pool_cache: Dict[str, Dict[str, Any]] = {}      # job_id -> {"keys", "lib_lab", "lib_sub"}
        self._snapshot_lock = threading.Lock()
        self._snapshot_loaded = False
        # Warm the feature cache in the background so the first job doesn't pay for it
        threading.Thread(target=self.load_feature_snapshot, daemon=True).start()
        self.heartbeat = HeartbeatReporter(storage, BUCKET_TASK_COORDINATION, self.worker_id)
        self.heartbeat.start()
        logger.info(f"Compute Worker initialized with ID: {self.worker_id}")

    # ------------------------------------------------------------------
    # BRD 4.1: direct-upload ingestion (incoming/ -> analysed pool photo)
    # ------------------------------------------------------------------
    def process_incoming(self, limit: int = 25) -> int:
        """
        Picks up photos that browsers PUT directly into incoming/ via signed URLs, analyses them
        (tiles + colour-index entry + tags) and publishes them to their pool. On GCP the same
        handler runs on GCS object-finalize events; locally workers poll the prefix.
        """
        keys = storage.list_objects(BUCKET_PHOTO_LIBRARY, INCOMING_PREFIX)
        images = [k for k in keys if k.lower().endswith(IMAGE_EXTS)]
        image_stems = {k.rsplit(".", 1)[0] for k in images}
        now = time.time()

        for meta_key in (k for k in keys if k.endswith(".meta.json")):
            if meta_key[:-len(".meta.json")] not in image_stems:
                try:
                    meta = json.loads(storage.download_file(BUCKET_PHOTO_LIBRARY, meta_key))
                    if now - meta.get("issued_at", now) > INCOMING_STALE_META_SEC:
                        storage.delete_object(BUCKET_PHOTO_LIBRARY, meta_key)  # upload never arrived
                        storage.delete_object(BUCKET_TASK_COORDINATION, f"upload-tickets/{meta.get('photo_id')}.json")
                except Exception:
                    pass

        done = 0
        for key in images[:limit]:
            stem = key.rsplit(".", 1)[0]
            lock_path = f"ingest-locks/{stem.replace('/', '_')}.lock"
            if not storage.acquire_lock(BUCKET_TASK_COORDINATION, lock_path, self.worker_id, 60):
                continue
            try:
                if not storage.object_exists(BUCKET_PHOTO_LIBRARY, key):
                    continue  # another worker finished it just before we locked
                meta = {}
                try:
                    meta = json.loads(storage.download_file(BUCKET_PHOTO_LIBRARY, f"{stem}.meta.json", quiet=True))
                except Exception:
                    pass
                rel = key[len(INCOMING_PREFIX):]
                pool_path, filename = rel.rsplit("/", 1)
                photo_id = meta.get("photo_id") or filename.rsplit(".", 1)[0]
                self.heartbeat.set_busy(f"ingest {rel}")
                try:
                    ingest_photo(storage, BUCKET_PHOTO_LIBRARY, pool_path + "/", photo_id,
                                 storage.download_file(BUCKET_PHOTO_LIBRARY, key), tags=meta.get("tags"),
                                 contributor=meta.get("contributor"))
                    self.heartbeat.record_ingest()
                    done += 1
                except Exception as e:
                    logger.warning(f"Rejected incoming upload {key} ({meta.get('filename')}): {e}")
                storage.delete_object(BUCKET_PHOTO_LIBRARY, key)
                storage.delete_object(BUCKET_PHOTO_LIBRARY, f"{stem}.meta.json")
            finally:
                self.heartbeat.set_busy(None)
                storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, self.worker_id)
        if done:
            logger.info(f"Ingested {done} directly-uploaded photos into the photo index.")
        return done

    # ------------------------------------------------------------------
    # Tile index
    # ------------------------------------------------------------------
    def load_feature_snapshot(self) -> None:
        with self._snapshot_lock:
            if self._snapshot_loaded:
                return
            self._snapshot_loaded = True
            try:
                snap = json.loads(storage.download_file(BUCKET_PHOTO_LIBRARY, FEATURE_SNAPSHOT_KEY, quiet=True))
            except Exception:
                return
            loaded = 0
            for key, (avg, sub) in snap.items():
                if key not in self.feature_cache:
                    self.feature_cache[key] = _features_from_rgb(avg, sub)
                    loaded += 1
            logger.info(f"Loaded {loaded} community colour features from the shared cache")

    def publish_feature_snapshot(self) -> None:
        snap = {k: v["raw"] for k, v in self.feature_cache.items() if k.startswith("public/") and "raw" in v}
        storage.upload_file(BUCKET_PHOTO_LIBRARY, FEATURE_SNAPSHOT_KEY, json.dumps(snap).encode("utf-8"),
                            "application/json")
        logger.info(f"Published {len(snap)} community colour features to the shared cache")

    def load_job_pool(self, job_id: str, pool_key: Optional[str]) -> Dict[str, Any]:
        """
        Loads the job's pool snapshot (written by the gateway at dispatch) and its colour features.
        Features are cached per object key across jobs, so only newly added photos are downloaded.
        Photos deleted since dispatch (e.g. a cleared vault) are dropped from the pool.
        """
        if job_id in self.pool_cache:
            return self.pool_cache[job_id]

        keys: List[str] = []
        personal_keys: Set[str] = set()
        if pool_key:
            try:
                snapshot = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, pool_key))
                keys = snapshot.get("keys", [])
                personal_keys = set(snapshot.get("personal", []))
            except Exception as e:
                logger.error(f"Pool snapshot missing for {job_id}: {e}")

        self.load_feature_snapshot()  # no-op once loaded (normally already done at start-up)
        missing = [k for k in keys if k not in self.feature_cache]
        if missing:
            def fetch(key):
                try:
                    return key, fetch_features(key)
                except Exception:
                    return key, None
            with ThreadPoolExecutor(max_workers=32) as ex:
                for key, feats in ex.map(fetch, missing):
                    if feats is not None:
                        self.feature_cache[key] = feats
            if sum(1 for k in missing if k.startswith("public/")) >= FEATURE_SNAPSHOT_MIN_NEW:
                try:
                    self.publish_feature_snapshot()
                except Exception as e:
                    logger.warning(f"Could not publish the feature cache: {e}")
            if len(self.feature_cache) > FEATURE_CACHE_MAX:
                keep = set(keys)
                self.feature_cache = {k: v for k, v in self.feature_cache.items() if k in keep}

        usable = [k for k in keys if k in self.feature_cache]
        pool = {
            "keys": usable,
            "rgb": [self.feature_cache[k]["rgb"] for k in usable],
            "lib_lab": np.array([self.feature_cache[k]["lab"] for k in usable], dtype=np.float64).reshape(-1, 3),
            "lib_sub": np.array([self.feature_cache[k]["sub_lab"] for k in usable], dtype=np.float64).reshape(-1, 4, 3),
            "is_personal": np.array([k in personal_keys for k in usable], dtype=bool),
        }
        if usable:
            self.pool_cache = {job_id: pool}  # one active job's pool at a time is plenty
        logger.info(f"Job {job_id}: indexed {len(usable)}/{len(keys)} pool tiles ({len(missing)} newly fetched).")
        return pool

    # ------------------------------------------------------------------
    # Claim loop
    # ------------------------------------------------------------------
    def run_poll_loop(self, runtime: ServiceRuntime):
        """Continuously polls bucket-task-coordination for unreserved chunk tasks until SIGTERM."""
        logger.info("Starting distributed worker task claim loop...")
        while runtime.running:
            runtime.beat()
            try:
                ingested = self.process_incoming()
                task_claimed = self.find_and_claim_task()
                if not task_claimed and not ingested:
                    runtime.sleep(1.0)  # Backoff when queue is empty
            except Exception as e:
                logger.error(f"Error in worker poll loop: {e}")
                runtime.sleep(3.0)
        logger.info(f"Worker {self.worker_id} stopped cleanly.")

    def _load_task(self, task_key: str) -> Optional[Dict[str, Any]]:
        if task_key not in self.task_cache:
            try:
                self.task_cache[task_key] = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, task_key))
            except Exception:
                return None
        return self.task_cache[task_key]

    def find_and_claim_task(self) -> bool:
        """
        Scans bucket-task-coordination for runnable tasks (not done, dependencies done),
        attempts an atomic lock reservation, and executes the chunk if the lock is acquired.
        """
        all_keys = storage.list_objects(BUCKET_TASK_COORDINATION, "jobs/")
        tasks_by_job: Dict[str, List[str]] = {}
        for k in all_keys:
            if "/tasks/chunk_" in k and k.endswith(".json"):
                tasks_by_job.setdefault(k.split("/")[1], []).append(k)

        runnable = []
        for job_id in sorted(tasks_by_job):
            if job_id in self.finished_jobs:
                continue
            task_keys = sorted(tasks_by_job[job_id])
            done = {k.rsplit("/", 1)[-1][:-5]
                    for k in storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/")}
            if len(done) >= len(task_keys):
                self.finished_jobs.add(job_id)
                continue
            runnable.extend((job_id, k, done) for k in task_keys if k.rsplit("/", 1)[-1][:-5] not in done)
        self.heartbeat.set_queue(len(runnable))

        for job_id, task_key, done in runnable:
            chunk_id = task_key.rsplit("/", 1)[-1][:-5]
            task = self._load_task(task_key)
            if task is None or any(dep not in done for dep in task.get("depends_on", [])):
                continue

            lock_path = f"jobs/{job_id}/locks/{chunk_id}.lock"
            if not storage.acquire_lock(BUCKET_TASK_COORDINATION, lock_path, self.worker_id, LOCK_TTL_SECONDS):
                continue
            logger.info(f"Worker {self.worker_id} acquired atomic lock on {task_key}")
            try:
                # Re-check after locking: another worker may have finished it just before.
                if not storage.object_exists(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/{chunk_id}.json"):
                    self.heartbeat.set_busy(f"{job_id}/{chunk_id}")
                    with LeaseKeeper(lock_path, self.worker_id):
                        self.execute_task_chunk(job_id, task, chunk_id)
                return True
            except Exception as ex:
                logger.exception(f"Task chunk execution failed for {task_key}: {ex}")
                return False
            finally:
                self.heartbeat.set_busy(None)
                storage.release_lock(BUCKET_TASK_COORDINATION, lock_path, self.worker_id)
        return False

    # ------------------------------------------------------------------
    # Engines
    # ------------------------------------------------------------------
    def _target_bgr(self, job_id: str) -> np.ndarray:
        data = storage.download_file(BUCKET_TARGET_POSTERS, f"{job_id}/original.jpg")
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Target image could not be decoded")
        return img

    def _upload_png(self, key: str, img_bgr: np.ndarray):
        ok, buf = cv2.imencode(".png", img_bgr, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
        if not ok:
            raise ValueError("PNG encode failed")
        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, key, buf.tobytes(), "image/png")

    def run_photomosaic(self, job_id: str, task: Dict[str, Any], chunk_id: str) -> Dict[str, Any]:
        cells = task.get("cells", [])
        metric = task.get("metric", "CIELAB_DELTA_E_2000")
        radius = int(task.get("no_repeat_radius", 5))
        pool = self.load_job_pool(job_id, task.get("pool_key"))
        keys = pool["keys"]
        n = len(keys)

        rep_buffer = SpatialRepetitionBuffer(radius=radius)
        # Seed the no-repeat buffer with placements from the neighbouring bands we depend on.
        for dep in task.get("depends_on", []):
            try:
                dep_out = json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/{dep}.json"))
                for c in dep_out.get("cells", []):
                    if c.get("matched_key"):
                        rep_buffer.record_placement(c["matched_key"], c["col"], c["row"])
            except Exception as e:
                logger.warning(f"Could not seed no-repeat buffer from {dep}: {e}")

        use_2000 = metric == "CIELAB_DELTA_E_2000"
        de = delta_e_ciede2000 if use_2000 else delta_e_cie76
        is_personal = pool["is_personal"]
        mixed = bool(n) and bool(is_personal.any()) and bool((~is_personal).any())

        # Target colours per cell as 2x2 sub-region Lab (mean error over the four quadrants matches
        # tile structure, e.g. light-top/dark-bottom, not just the average colour).
        m = len(cells)
        t_sub = np.empty((m, 4, 3), dtype=np.float64)
        for i, c in enumerate(cells):
            sub_rgb = c.get("target_sub_rgb") or [c["target_rgb"]] * 4
            t_sub[i] = rgb_to_cielab(np.asarray(sub_rgb, dtype=np.float64))

        # Two-stage search. Stage 1: squared Lab distance to EVERY pool photo as one matrix
        # product (cheap), keeping the best CANDIDATES per cell (per source when blending, so both
        # pools are represented). Stage 2: exact CIEDE2000 only on those candidates. This is
        # ~50x fewer CIEDE2000 evaluations than scoring the whole pool for every cell.
        cand_idx: List[np.ndarray] = []
        cand_de: List[np.ndarray] = []
        if n:
            feat = pool["lib_sub"].reshape(n, 12).astype(np.float32)
            feat_sq = (feat * feat).sum(axis=1)
            tq_all = t_sub.reshape(m, 12).astype(np.float32)
            subsets = [np.flatnonzero(is_personal), np.flatnonzero(~is_personal)] if mixed else [None]
            for b0 in range(0, m, CANDIDATE_BLOCK):
                tq = tq_all[b0:b0 + CANDIDATE_BLOCK]
                d2 = (tq * tq).sum(axis=1)[:, None] + feat_sq[None, :] - 2.0 * (tq @ feat.T)
                parts = []
                for subset in subsets:
                    sd = d2 if subset is None else d2[:, subset]
                    k = min(CANDIDATES, sd.shape[1])
                    top = np.argpartition(sd, k - 1, axis=1)[:, :k] if k < sd.shape[1] else \
                        np.broadcast_to(np.arange(sd.shape[1]), (sd.shape[0], sd.shape[1]))
                    parts.append(top if subset is None else subset[top])
                cand = np.concatenate(parts, axis=1)                          # (block, C)
                lib = pool["lib_sub"][cand]                                   # (block, C, 4, 3)
                tgt = np.broadcast_to(t_sub[b0:b0 + len(tq), None], lib.shape)
                scores = de(tgt.reshape(-1, 3), lib.reshape(-1, 3)).reshape(cand.shape + (4,)).mean(axis=2)
                order_c = np.argsort(scores, axis=1)
                cand_idx.extend(np.take_along_axis(cand, order_c, axis=1))
                cand_de.extend(np.take_along_axis(scores, order_c, axis=1))

        def exact_distance(ci: int, idx: int) -> float:
            return float(np.mean(de(t_sub[ci], pool["lib_sub"][idx])))

        # BRD 4.2 Dual-source blending: exactly round(ratio * cells) cells use personal photos.
        # Personal photos go to the cells where they beat the best community match by the most,
        # so honouring the ratio costs as little colour accuracy as possible.
        ratio = float(task.get("blend_ratio_personal", 0.5))
        source = [None] * m  # None = whole pool, True = personal only, False = community only
        personal_cells = 0
        if mixed:
            want = int(round(ratio * m))
            advantage = np.array([d[~is_personal[c]].min() - d[is_personal[c]].min()
                                  for c, d in zip(cand_idx, cand_de)])
            chosen = set(np.argsort(-advantage)[:want].tolist())
            source = [i in chosen for i in range(m)]
            personal_cells = want

        results = []
        # Greedy matching in scan order lets each band's first row claim the best tiles and pushes
        # the radius penalty onto the rows below (visible horizontal banding). A seeded shuffle
        # spreads that evenly while keeping results reproducible.
        order = np.random.default_rng(int(task.get("chunk_index", 0)) + 1).permutation(m)
        for ci in order:
            cell = cells[ci]
            col, row = cell["col"], cell["row"]
            best_idx, best_distance = None, None

            if n:
                cands, scores = cand_idx[ci], cand_de[ci]
                if source[ci] is not None:
                    keep = is_personal[cands] == source[ci]
                    cands, scores = cands[keep], scores[keep]
                for idx, score in zip(cands, scores):
                    if rep_buffer.is_eligible(keys[idx], col, row):
                        best_idx, best_distance = int(idx), float(score)
                        break
                if best_idx is None:
                    # Every candidate is inside the no-repeat radius: walk the rest of the allowed
                    # pool in cheap-distance order.
                    tq = t_sub[ci].reshape(12).astype(np.float32)
                    cheap = feat_sq - 2.0 * (feat @ tq)
                    if source[ci] is not None:
                        cheap = np.where(is_personal == source[ci], cheap, np.inf)
                    tried = set(cand_idx[ci].tolist())
                    for idx in np.argsort(cheap):
                        if not np.isfinite(cheap[idx]):
                            break
                        if idx not in tried and rep_buffer.is_eligible(keys[idx], col, row):
                            best_idx = int(idx)
                            best_distance = exact_distance(ci, best_idx)
                            break
                if best_idx is None:
                    # Pool too small to satisfy the radius: take the closest tile anyway.
                    best_idx = int(cands[0]) if len(cands) else int(cand_idx[ci][0])
                    best_distance = exact_distance(ci, best_idx)
                rep_buffer.record_placement(keys[best_idx], col, row)

            results.append({
                "cell_index": cell["cell_index"],
                "col": col,
                "row": row,
                "pixel_x": cell["pixel_x"],
                "pixel_y": cell["pixel_y"],
                "width": cell["width"],
                "height": cell["height"],
                "target_rgb": cell["target_rgb"],
                "matched_id": os.path.basename(keys[best_idx]) if best_idx is not None else None,
                "matched_key": keys[best_idx] if best_idx is not None else None,
                "matched_source": ("personal" if is_personal[best_idx] else "community") if best_idx is not None else None,
                "matched_rgb": pool["rgb"][best_idx] if best_idx is not None else cell["target_rgb"],
                "delta_e": round(best_distance, 2) if best_distance is not None else None,
            })
        results.sort(key=lambda c: c["cell_index"])
        return {"cells": results, "pool_size": n, "personal_cells": personal_cells}

    def run_ascii(self, job_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
        params = task.get("engine_params", {})
        cols = min(240, max(20, int(params.get("columns", 100))))
        ramp = ASCII_RAMPS.get(params.get("ascii_ramp"))
        engine = AsciiEngine(char_ramp=ramp) if ramp else AsciiEngine()
        char_w, char_h = 8, 14
        res = engine.process_image(self._target_bgr(job_id), cols=cols, char_w=char_w, char_h=char_h)

        bg, fg = ASCII_THEMES.get(params.get("ascii_theme"), ASCII_THEMES["cyan"])
        scale = 2  # render at 2x for a crisp export
        canvas = Image.new("RGB", (res["pixel_width"] * scale, res["pixel_height"] * scale), bg)
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.load_default(size=(char_h - 1) * scale)
        except TypeError:
            font = ImageFont.load_default()
        for r, line in enumerate(res["matrix"]):
            for c, cell in enumerate(line):
                if cell["char"] != " ":
                    fill = fg or (cell["r"], cell["g"], cell["b"])
                    draw.text((c * char_w * scale, r * char_h * scale), cell["char"], fill=fill, font=font)

        render_key = f"jobs/{job_id}/render.png"
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, render_key, buf.getvalue(), "image/png")
        return {"render_key": render_key, "ascii_text": res["ascii_text"],
                "columns": res["columns"], "rows": res["rows"]}

    def _render_source(self, job_id: str, long_edge: int = 2400) -> np.ndarray:
        """Target image capped at long_edge; the whole-image filters run on a single worker."""
        img = self._target_bgr(job_id)
        h, w = img.shape[:2]
        if max(h, w) > long_edge:
            s = long_edge / max(h, w)
            img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        return img

    def run_kuwahara(self, job_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
        params = task.get("engine_params", {})
        engine = KuwaharaEngine(radius=int(params.get("kuwahara_radius", 4)))
        # Fast enough (~0.4 s) to paint at 4K instead of the 2400px used by the other filters
        canvas = engine.render(self._render_source(job_id, long_edge=3840))
        render_key = f"jobs/{job_id}/render.png"
        self._upload_png(render_key, canvas)
        return {"render_key": render_key, "radius": engine.radius}

    def run_flowfield(self, job_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
        params = task.get("engine_params", {})
        engine = FlowFieldEngine(
            step_length=int(params.get("step_length", 15)),
            num_particles=int(params.get("num_particles", 5000)),
            stroke_width=int(params.get("stroke_width", 2)),
            seed=zlib.crc32(job_id.encode("utf-8")),  # same job id -> same strokes on a retry
        )
        canvas = engine.render(self._render_source(job_id))
        render_key = f"jobs/{job_id}/render.png"
        self._upload_png(render_key, canvas)
        return {"render_key": render_key, "num_particles": engine.num_particles}

    def execute_task_chunk(self, job_id: str, task: Dict[str, Any], chunk_id: str):
        """Processes a task with the appropriate engine and writes the chunk result."""
        mode = task.get("mode", "photomosaic")
        started = time.time()
        logger.info(f"Executing {mode.upper()} engine for {job_id}/{chunk_id} ({len(task.get('cells', []))} cells)...")

        if mode == "photomosaic":
            result = self.run_photomosaic(job_id, task, chunk_id)
        elif mode == "ascii":
            result = self.run_ascii(job_id, task)
        elif mode == "kuwahara":
            result = self.run_kuwahara(job_id, task)
        elif mode == "flowfield":
            result = self.run_flowfield(job_id, task)
        else:
            raise ValueError(f"Unknown mode {mode}")

        output_payload = {
            "job_id": job_id,
            "chunk_id": chunk_id,
            "chunk_index": task.get("chunk_index"),
            "mode": mode,
            "worker_id": self.worker_id,
            "duration_sec": round(time.time() - started, 3),
            "completed_at": time.time(),
            **result,
        }
        out_key = f"jobs/{job_id}/chunks/{chunk_id}.json"
        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, out_key, json.dumps(output_payload).encode("utf-8"), "application/json")
        self.heartbeat.record_chunk(len(result.get("cells", [])))
        logger.info(f"Worker {self.worker_id} finished {chunk_id} in {output_payload['duration_sec']}s -> {out_key}")


if __name__ == "__main__":
    runtime = ServiceRuntime("worker", stall_after_sec=LOCK_TTL_SECONDS * 3)
    runtime.install_signal_handlers()
    runtime.start_health_server()
    worker = ComputeWorker()
    worker.run_poll_loop(runtime)
