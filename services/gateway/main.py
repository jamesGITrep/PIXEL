"""
Crowd-Sourced Collaborative Art & High-Compute Raster Platform
API Gateway (Tasks 2.1 & 2.2)

The single entry point for every client request. Users only reach the frontend; the frontend
forwards /api/* here. Storage buckets, workers and the assembler are private and never contacted
by browsers: all reads (thumbnails, artboards, exports) and writes (uploads) pass through this
service. Workers and the assembler accept no inbound traffic; they coordinate only through the
private task-coordination bucket.

Exposes:
- /health                         load balancer health check (HTTP 200)
- /api/v1/library/*               photo vault, community commons, upload relay
- /api/v1/jobs/*                  image slicer & task partitioner, status, results
- /api/v1/events/*                live event arena (bucket-backed state, safe on N gateway VMs)
- /api/v1/storage/*               read-only, allow-listed object proxy
- /api/v1/admin/telemetry         cluster observability
"""

import os
import re
import sys
import uuid
import json
import time
import io
import math
import secrets
import tempfile
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from PIL import Image, ImageOps

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
    COMMUNITY_PREFIX,
    CONTENT_TYPE_EXT,
    IMAGE_EXTS,
    INCOMING_PREFIX,
    clean_tags,
    community_tag_index,
    community_thumb_keys,
    incoming_pool_path,
    index_key_for,
    ingest_photo,
    pending_incoming,
    personal_prefix,
    personal_thumb_keys,
    photo_id_from_key,
    pool_photo_keys,
    raw_key_for,
    tile_key_for,
    resolve_pool,
    tag_index as pool_tag_index,
)
from services.common.telemetry import read_cluster_telemetry
from services.gateway.auth import (
    router as auth_router,
    clear_guest,
    clear_session,
    confirm_owner,
    current_user,
    delete_account_records,
    guest_id,
    public_profile,
    require_admin,
    require_user,
    vault_owner,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("gateway")

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "30")) * 1024 * 1024
VAULT_QUOTA_BYTES = int(os.getenv("VAULT_QUOTA_MB", "500")) * 1024 * 1024
GUEST_QUOTA_BYTES = int(os.getenv("GUEST_QUOTA_MB", "100")) * 1024 * 1024  # temporary, per browser session
UPLOAD_URL_TTL = 1800

app = FastAPI(
    title="Crowd-Sourced Raster Gateway",
    version="3.0.0",
    docs_url="/api/docs"
)

# Browsers reach the gateway same-origin through the frontend proxy, so CORS is off by default.
# ALLOWED_ORIGINS (comma-separated) re-enables it, e.g. for a Vite dev server on another port.
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag", "Content-Type", "Content-Length", "Content-Disposition"]
    )

storage = get_storage()
app.include_router(auth_router)


# ------------------------------------------------------------------------------
# Data Models
# ------------------------------------------------------------------------------

class JobCreateRequest(BaseModel):
    target_image_key: Optional[str] = None
    mode: str = "photomosaic"  # photomosaic, ascii, kuwahara, flowfield
    columns: int = 60
    rows: int = 60
    cell_size: int = 50
    metric: str = "CIELAB_DELTA_E_2000"
    blend_ratio_personal: float = 0.70
    selected_tags: List[str] = ["Landscapes", "Vehicles", "Selfies"]
    no_repeat_radius: int = 5
    alpha_tint_percentage: int = 20


@app.on_event("startup")
def startup_event():
    """
    Creates missing buckets when AUTO_CREATE_BUCKETS is on (local MinIO / emulator). In GCP the
    buckets are owned by Terraform and the gateway's service account has object access only.
    """
    default = "false" if storage.use_gcp else "true"
    if os.getenv("AUTO_CREATE_BUCKETS", default).lower() not in ("true", "1", "yes"):
        return
    try:
        storage.ensure_buckets_exist()
        logger.info("Storage buckets verified and ready.")
    except Exception as e:
        logger.warning(f"Storage init notice: {e}")


# ------------------------------------------------------------------------------
# Task 2.1: Health Check for Cloud Load Balancers
# ------------------------------------------------------------------------------

@app.get("/health")
def health_check():
    """Strict HTTP 200 endpoint for Google Cloud Load Balancer HTTP Health Checks."""
    return {"status": "healthy", "timestamp": time.time(), "plane": "gcp" if storage.use_gcp else "local_minio"}


# ------------------------------------------------------------------------------
# Task 2.2: Image Slicer & Task Partitioner
# ------------------------------------------------------------------------------

@app.post("/api/v1/jobs/create")
async def create_job(
    request: Request,
    target_file: Optional[UploadFile] = File(None),
    config_json: Optional[str] = Form(None)
):
    """
    Slices target master poster into an (N x M) grid of task chunks.
    Writes manifest.json and tasks/chunk_*.json to bucket-task-coordination.
    """
    job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    
    config = {}
    if config_json:
        try:
            config = json.loads(config_json)
        except Exception:
            pass

    mode = config.get("mode", "photomosaic")
    if mode not in ("photomosaic", "ascii", "kuwahara", "flowfield"):
        raise HTTPException(status_code=400, detail=f"Unknown mode '{mode}'")
    cols = min(200, max(5, int(config.get("columns", 60))))
    metric = config.get("metric", "CIELAB_DELTA_E_2000")
    radius = min(30, max(0, int(config.get("no_repeat_radius", 5))))
    alpha_tint = min(50, max(0, int(config.get("alpha_tint_percentage", 20))))
    selected_tags = clean_tags(config.get("selected_tags") or [])
    blend_ratio = min(1.0, max(0.0, float(config.get("blend_ratio_personal", 0.5))))
    # The personal pool is always the caller's own vault (the account's, or this browser session's
    # guest vault); a client can't name another one
    personal_session_id, _, user = await run_in_threadpool(vault_owner, request)
    use_personal = bool(config.get("use_personal_photos", personal_session_id is not None))
    use_community = bool(config.get("use_community_photos", True))

    # Snapshot the tile pool once so every chunk of this job matches against the same set
    # (and a vault purged after dispatch cannot leave chunks pointing at different pools).
    pool = None
    if mode == "photomosaic":
        pool = resolve_pool(storage, BUCKET_PHOTO_LIBRARY, personal_session_id,
                            use_personal, use_community, selected_tags)
        if not pool["all"]:
            detail = "The selected photo pool is empty. Add photos to your vault or enable the community gallery."
            if selected_tags:
                detail = (f"No photos in the selected pools are tagged {', '.join(selected_tags)}. "
                          "Clear or change the tag filters, or upload photos with those tags.")
            raise HTTPException(status_code=400, detail=detail)
        # BRD 4.2: the personal/community ratio only applies when both pools contribute photos.
        if not pool["personal"]:
            blend_ratio = 0.0
        elif not pool["community"]:
            blend_ratio = 1.0

    # Read and process target image
    if target_file:
        img_bytes = await target_file.read()
    else:
        # Check target_image_key
        key = config.get("target_image_key")
        if not key:
            raise HTTPException(status_code=400, detail="Target image file or key required")
        img_bytes = storage.download_file(BUCKET_TARGET_POSTERS, key)

    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(img_bytes))).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")

    # Upload original target poster
    target_obj_key = f"{job_id}/original.jpg"
    out_buf = io.BytesIO()
    img.save(out_buf, format="JPEG", quality=95)
    storage.upload_file(BUCKET_TARGET_POSTERS, target_obj_key, out_buf.getvalue(), "image/jpeg")

    # Grid follows the target's aspect ratio: `columns` is fixed, rows are derived.
    rows = max(1, round(cols * img.height / img.width))
    export_resolution = min(12000, max(1000, int(config.get("export_resolution", 4000))))
    # export_resolution bounds the long edge; tiles are square (>= 50px for zoom clarity).
    tile_size = max(50, round(export_resolution / max(cols, rows)))
    target_w = cols * tile_size
    target_h = rows * tile_size

    if mode == "photomosaic":
        # BOX resampling = exact area average per cell (and per 2x2 sub-cell for structure matching)
        cell_avg = np.asarray(img.resize((cols, rows), Image.Resampling.BOX), dtype=np.uint8)
        cell_sub = np.asarray(img.resize((cols * 2, rows * 2), Image.Resampling.BOX), dtype=np.uint8)
        cell_metadata = []
        for r in range(rows):
            for c in range(cols):
                sub = cell_sub[r * 2:r * 2 + 2, c * 2:c * 2 + 2].reshape(4, 3)
                cell_metadata.append({
                    "cell_index": r * cols + c,
                    "col": c,
                    "row": r,
                    "pixel_x": c * tile_size,
                    "pixel_y": r * tile_size,
                    "width": tile_size,
                    "height": tile_size,
                    "target_rgb": cell_avg[r, c].tolist(),
                    "target_sub_rgb": sub.tolist(),
                })

        pool_key = f"jobs/{job_id}/pool.json"
        storage.upload_file(BUCKET_TASK_COORDINATION, pool_key,
                            json.dumps({"keys": pool["all"], "personal": pool["personal"],
                                        "community": pool["community"]}).encode("utf-8"),
                            "application/json")

        # Row-band chunks at least `radius` rows tall. Even bands run first in parallel; odd
        # bands wait for both neighbours and seed their no-repeat buffer from them, so the
        # proximity rule holds across chunk boundaries no matter how many workers run.
        rows_per_chunk = max(radius, math.ceil(150 / cols), 1)
        bands = [list(range(r0, min(rows, r0 + rows_per_chunk))) for r0 in range(0, rows, rows_per_chunk)]
    else:
        # Whole-image engines run as a single task.
        cell_metadata = []
        pool_key = None
        bands = [None]

    total_chunks = len(bands)
    for chunk_idx, band in enumerate(bands):
        depends_on = []
        if mode == "photomosaic" and radius > 0 and chunk_idx % 2 == 1:
            depends_on = [f"chunk_{i + 1:04d}" for i in (chunk_idx - 1, chunk_idx + 1) if i < total_chunks]
        chunk_data = {
            "job_id": job_id,
            "chunk_id": f"chunk_{chunk_idx + 1:04d}",
            "chunk_index": chunk_idx,
            "total_chunks": total_chunks,
            "mode": mode,
            "metric": metric,
            "no_repeat_radius": radius,
            "depends_on": depends_on,
            "pool_key": pool_key,
            "blend_ratio_personal": blend_ratio,
            "cells": [cell_metadata[r * cols + c] for r in band for c in range(cols)] if band else [],
            "engine_params": {
                "columns": cols,
                "ascii_theme": config.get("ascii_theme", "cyan"),
                "ascii_ramp": config.get("ascii_ramp", "extended"),
                "kuwahara_radius": min(8, max(2, int(config.get("kuwahara_radius", 4)))),
                "step_length": min(80, max(2, int(config.get("step_length", 15)))),
                "num_particles": min(50000, max(100, int(config.get("num_particles", 5000)))),
                "stroke_width": min(10, max(1, int(config.get("stroke_width", 2)))),
            },
            "created_at": time.time()
        }
        chunk_key = f"jobs/{job_id}/tasks/chunk_{chunk_idx + 1:04d}.json"
        storage.upload_file(
            BUCKET_TASK_COORDINATION,
            chunk_key,
            json.dumps(chunk_data).encode("utf-8"),
            "application/json"
        )

    # Write Master Manifest (Schema conforming to GEMINI_DIRECTIVES.md Section 5)
    manifest = {
        "job_id": job_id,
        "mode": mode,
        "target_dimensions": {"width": target_w, "height": target_h},
        "grid": {"columns": cols, "rows": rows, "cell_size": tile_size},
        "total_cells": cols * rows,
        "total_chunks": total_chunks,
        "pool": {
            "total": len(pool["all"]) if pool else 0,
            "personal": len(pool["personal"]) if pool else 0,
            "community": len(pool["community"]) if pool else 0,
        },
        "parameters": {
            "export_resolution": export_resolution,
            "color_space": metric,
            "blend_ratio_personal": blend_ratio,
            "selected_tags": selected_tags,
            "no_repeat_radius": radius,
            "alpha_tint_percentage": alpha_tint,
            "use_personal_photos": use_personal,
            "use_community_photos": use_community,
            "personal_session_id": personal_session_id
        },
        "owner_username": user["username"] if user else None,
        "target_image_url": f"/{BUCKET_TARGET_POSTERS}/{target_obj_key}",
        "created_at": int(time.time())
    }

    manifest_key = f"jobs/{job_id}/manifest.json"
    storage.upload_file(
        BUCKET_TASK_COORDINATION,
        manifest_key,
        json.dumps(manifest, indent=2).encode("utf-8"),
        "application/json"
    )

    logger.info(f"Job {job_id} dispatched: {cols}x{rows} ({len(cell_metadata)} cells, {total_chunks} chunks, export_res={export_resolution}px)")

    return {
        "job_id": job_id,
        "manifest": manifest,
        "total_chunks": total_chunks,
        "status": "QUEUED"
    }


@app.get("/api/v1/jobs/{job_id}/status")
def get_job_status(job_id: str):
    """Queries task completion and returns real-time progress."""
    manifest_key = f"jobs/{job_id}/manifest.json"
    try:
        manifest_data = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, manifest_key).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=404, detail="Job not found")

    total_chunks = manifest_data.get("total_chunks", 1)
    mode = manifest_data.get("mode", "photomosaic")

    # Check completed chunk results in bucket-mosaic-outputs
    completed_chunks = storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/")
    completed_count = len(completed_chunks)
    progress = round((completed_count / max(1, total_chunks)) * 100, 1)

    final_key = f"jobs/{job_id}/final_mosaic.png"
    final_exists = storage.object_exists(BUCKET_MOSAIC_OUTPUTS, final_key)

    error = None
    error_key = f"jobs/{job_id}/error.json"
    if storage.object_exists(BUCKET_MOSAIC_OUTPUTS, error_key):
        try:
            error = json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, error_key)).get("error")
        except Exception:
            error = "Assembly failed"

    # is_complete is True once all chunks are done AND final_mosaic has been stitched by assembler
    is_complete = completed_count >= total_chunks and final_exists
    output_url = None
    preview_url = None
    engine_label = {
        "photomosaic": "CIEDE2000 tile matching",
        "ascii": "Sobel ASCII rendering",
        "kuwahara": "Kuwahara oil-paint filtering",
        "flowfield": "Vector flow-field painting",
    }.get(mode, "rendering")
    status_text = f"Distributed workers: {engine_label} (chunk {completed_count}/{total_chunks})"

    if completed_count >= total_chunks and not final_exists:
        status_text = "Workers complete. Stitching master artboard..."

    if error:
        status_text = f"Assembly failed: {error}"

    if is_complete:
        status_text = "Master artboard assembled and ready"
        output_url = f"/api/v1/jobs/{job_id}/result"
        preview_url = f"/api/v1/jobs/{job_id}/preview"

    return {
        "job_id": job_id,
        "mode": mode,
        "progress_percent": progress,
        "completed_chunks": completed_count,
        "total_chunks": total_chunks,
        "is_complete": is_complete,
        "is_failed": bool(error) and not is_complete,
        "error": error,
        "status_text": status_text,
        "output_url": output_url,
        "preview_url": preview_url,
        "grid": manifest_data.get("grid"),
        "target_dimensions": manifest_data.get("target_dimensions"),
        "result_url": f"/api/v1/jobs/{job_id}/result"
    }


def _image_response(content: bytes, media_type: str, filename: str, download: bool):
    disposition = "attachment" if download else "inline"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Content-Disposition": f'{disposition}; filename="{filename}"'
        }
    )


def _load_output(key: str) -> bytes:
    try:
        return storage.download_file(BUCKET_MOSAIC_OUTPUTS, key)
    except Exception:
        raise HTTPException(status_code=404, detail="Artboard result not ready or job not found")


def _job_default_tint(job_id: str) -> int:
    try:
        manifest = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, f"jobs/{job_id}/manifest.json"))
        if manifest.get("mode", "photomosaic") != "photomosaic":
            return 0
        return int(manifest.get("parameters", {}).get("alpha_tint_percentage", 0))
    except Exception:
        return 0


def _apply_tint(job_id: str, mosaic_bytes: bytes, tint: int) -> Image.Image:
    """BRD 4.5 Alpha tint: I = (1 - a) * Mosaic + a * Target, a in [0, 0.5]."""
    mosaic = Image.open(io.BytesIO(mosaic_bytes)).convert("RGB")
    alpha = min(50, max(0, tint)) / 100.0
    if alpha <= 0:
        return mosaic
    target = Image.open(io.BytesIO(storage.download_file(BUCKET_TARGET_POSTERS, f"{job_id}/original.jpg"))).convert("RGB")
    target = target.resize(mosaic.size, Image.Resampling.BICUBIC)
    return Image.blend(mosaic, target, alpha)


@app.get("/api/v1/jobs/{job_id}/result")
def get_job_result(job_id: str, download: bool = False, tint: Optional[int] = None, format: str = "png"):
    """
    Full-resolution master artboard (PNG lossless, or JPEG q95 for a much smaller file).
    The stored artboard is the untinted mosaic; the alpha tint (the job's setting, or `tint` 0-50
    chosen afterwards with the slider) is applied here. Rendered exports are cached in the bucket,
    so exporting the same tint/format again is instant.
    """
    fmt = "jpg" if format.lower() in ("jpg", "jpeg") else "png"
    media_type = "image/jpeg" if fmt == "jpg" else "image/png"
    tint = _job_default_tint(job_id) if tint is None else min(50, max(0, tint))
    filename = f"pixelmosaic_{job_id}_tint{tint}.{fmt}"

    base_key = f"jobs/{job_id}/final_mosaic.png"
    if tint == 0 and fmt == "png":
        return _image_response(_load_output(base_key), media_type, filename, download)

    cache_key = f"jobs/{job_id}/exports/tint{tint}.{fmt}"
    try:
        return _image_response(storage.download_file(BUCKET_MOSAIC_OUTPUTS, cache_key, quiet=True),
                               media_type, filename, download)
    except Exception:
        pass

    img = _apply_tint(job_id, _load_output(base_key), tint)
    buf = io.BytesIO()
    if fmt == "jpg":
        img.save(buf, format="JPEG", quality=95, subsampling=0)
    else:
        img.save(buf, format="PNG", compress_level=3)
    content = buf.getvalue()
    try:
        storage.upload_file(BUCKET_MOSAIC_OUTPUTS, cache_key, content, media_type)
    except Exception as e:
        logger.warning(f"Could not cache export {cache_key}: {e}")
    return _image_response(content, media_type, filename, download)


@app.get("/api/v1/jobs/{job_id}/preview")
def get_job_preview(job_id: str, tint: Optional[int] = None):
    """Display-sized JPEG of the artboard (keeps the browser responsive at 8k-12k). tint=0 gives the raw mosaic."""
    base = _load_output(f"jobs/{job_id}/final_mosaic_preview.jpg")
    tint = _job_default_tint(job_id) if tint is None else tint
    if tint <= 0:
        return _image_response(base, "image/jpeg", f"pixelmosaic_{job_id}_preview.jpg", False)
    buf = io.BytesIO()
    _apply_tint(job_id, base, tint).save(buf, format="JPEG", quality=90)
    return _image_response(buf.getvalue(), "image/jpeg", f"pixelmosaic_{job_id}_preview.jpg", False)


@app.get("/api/v1/jobs/{job_id}/progress-patches")
def get_job_progress_patches(job_id: str, since_chunk: int = -1, have: str = ""):
    """
    Returns finished chunk patches with cell coordinates and matched photo info.
    `have` is a comma-separated list of chunk indices the client already drew (chunks can finish
    out of order across workers, so a high-water mark alone would skip some).
    """
    have_set = {int(x) for x in have.split(",") if x.strip().lstrip("-").isdigit()}
    manifest_key = f"jobs/{job_id}/manifest.json"
    try:
        manifest_data = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, manifest_key).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=404, detail="Job manifest not found")

    total_chunks = manifest_data.get("total_chunks", 1)
    target_dims = manifest_data.get("target_dimensions", {"width": 2000, "height": 2000})

    chunk_keys = sorted(storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/"))

    patches = []
    for c_key in chunk_keys:
        filename = c_key.split("/")[-1].replace(".json", "")
        try:
            chunk_num = int(filename.replace("chunk_", ""))
            chunk_idx = chunk_num - 1
        except ValueError:
            chunk_idx = len(patches)

        if chunk_idx in have_set or (not have_set and chunk_idx <= since_chunk):
            continue

        try:
            raw_data = storage.download_file(BUCKET_MOSAIC_OUTPUTS, c_key)
            chunk_json = json.loads(raw_data.decode("utf-8"))
            cells = chunk_json.get("cells", [])
            patch_cells = []
            for c in cells:
                matched_key = c.get("matched_key")
                thumb_url = f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{matched_key}" if matched_key else None
                patch_cells.append({
                    "cell_index": c.get("cell_index"),
                    "delta_e": c.get("delta_e"),
                    "col": c.get("col", 0),
                    "row": c.get("row", 0),
                    "px": c.get("pixel_x", 0),
                    "py": c.get("pixel_y", 0),
                    "w": c.get("width", 50),
                    "h": c.get("height", 50),
                    "rgb": c.get("matched_rgb", c.get("target_rgb", [128, 128, 128])),
                    "thumb": thumb_url,
                    "source": c.get("matched_source")
                })
            patches.append({
                "chunk_id": filename,
                "chunk_index": chunk_idx,
                "cells": patch_cells
            })
        except Exception as e:
            logger.warning(f"Error reading patch {c_key}: {e}")

    return {
        "job_id": job_id,
        "completed_chunks": len(chunk_keys),
        "total_chunks": total_chunks,
        "target_dimensions": target_dims,
        "patches": patches
    }


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_cell_cache: Dict[str, Dict[int, dict]] = {}


def _job_cells(job_id: str) -> Dict[int, dict]:
    """cell_index -> matched cell for a finished job (cached: a finished job never changes)."""
    if not _JOB_ID_RE.match(job_id or ""):
        raise HTTPException(status_code=404, detail="Job not found")
    if job_id in _cell_cache:
        return _cell_cache[job_id]
    cells: Dict[int, dict] = {}
    for key in storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/chunks/"):
        for c in json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, key)).get("cells", []):
            if c.get("cell_index") is not None:
                cells[int(c["cell_index"])] = c
    if not cells:
        raise HTTPException(status_code=404, detail="No tiles for this job")
    if storage.object_exists(BUCKET_MOSAIC_OUTPUTS, f"jobs/{job_id}/final_mosaic.png"):
        if len(_cell_cache) >= 16:
            _cell_cache.pop(next(iter(_cell_cache)))
        _cell_cache[job_id] = cells
    return cells


def _job_cell(job_id: str, cell_index: int) -> dict:
    cell = _job_cells(job_id).get(cell_index)
    if not cell or not cell.get("matched_key"):
        raise HTTPException(status_code=404, detail="No photo in this tile")
    return cell


@app.get("/api/v1/jobs/{job_id}/cells/{cell_index}")
def inspect_tile(job_id: str, cell_index: int):
    """
    Tile inspector: which photo fills one cell of a job, who contributed it and how closely it
    matched (CIEDE2000). Only photos actually used by this job can be looked up here.
    """
    cell = _job_cell(job_id, cell_index)
    key = cell["matched_key"]
    info: Dict[str, Any] = {}
    try:
        info = json.loads(storage.download_file(BUCKET_PHOTO_LIBRARY, index_key_for(key) or "", quiet=True))
    except Exception:
        pass
    contributor = info.get("contributor")
    if not contributor and cell.get("matched_source") == "personal":
        try:
            manifest = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, f"jobs/{job_id}/manifest.json"))
            contributor = manifest.get("owner_username")
        except Exception:
            pass
    proxy = f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/"
    return {
        "job_id": job_id,
        "cell_index": cell_index,
        "col": cell.get("col"),
        "row": cell.get("row"),
        "photo_id": photo_id_from_key(key),
        "source": cell.get("matched_source"),
        "contributor": contributor,
        "tags": info.get("tags", []),
        "delta_e": cell.get("delta_e"),
        "matched_rgb": cell.get("matched_rgb"),
        "target_rgb": cell.get("target_rgb"),
        "thumb_url": proxy + key,
        "tile_url": proxy + tile_key_for(key),
        "original_url": f"/api/v1/jobs/{job_id}/cells/{cell_index}/original",
    }


@app.get("/api/v1/jobs/{job_id}/cells/{cell_index}/original")
def tile_original(job_id: str, cell_index: int):
    """Full-resolution original of the photo in one tile (EXIF/location data was stripped at ingest)."""
    key = _job_cell(job_id, cell_index)["matched_key"]
    for candidate in dict.fromkeys((raw_key_for(key), tile_key_for(key), key)):
        if not candidate:
            continue
        try:
            data = storage.download_file(BUCKET_PHOTO_LIBRARY, candidate, quiet=True)
        except Exception:
            continue
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
    raise HTTPException(status_code=404, detail="This photo has been removed from the library")


# ------------------------------------------------------------------------------
# Live Event Arena: Organizer, Audience Ingestion, and "Find My Photo"
# ------------------------------------------------------------------------------

# Event state lives in the bucket (not gateway memory), so any number of gateway VMs can serve
# the same event and nothing is lost when a gateway restarts.
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOKEN_RE = re.compile(r"^#?T-?(\d{4,8})$")


def _check_event_id(event_id: str) -> str:
    if not _EVENT_ID_RE.match(event_id or ""):
        raise HTTPException(status_code=400, detail="Invalid event id")
    return event_id


def _event_record(event_id: str) -> Dict[str, Any]:
    try:
        return json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, f"events/{event_id}/event.json", quiet=True))
    except Exception:
        # Ad-hoc events (e.g. the demo stage) work without an explicit create call
        return {"event_id": event_id, "title": event_id, "grid_size": 50}


def _event_patch_keys(event_id: str) -> List[str]:
    return sorted(k for k in storage.list_objects(BUCKET_MOSAIC_OUTPUTS, f"events/{event_id}/patches/") if k.endswith(".json"))


@app.post("/api/v1/events/create")
async def create_event(
    title: str = Form(...),
    grid_size: int = Form(50),
    target_file: UploadFile = File(...)
):
    """Organizer creates collaborative live mural."""
    event_id = f"evt_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    img_bytes = await target_file.read()
    try:
        Image.open(io.BytesIO(img_bytes)).verify()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")

    target_key = f"events/{event_id}/target.jpg"
    storage.upload_file(BUCKET_TARGET_POSTERS, target_key, img_bytes, "image/jpeg")

    event_record = {
        "event_id": event_id,
        "title": title[:200],
        "grid_size": min(200, max(5, grid_size)),
        "target_key": target_key,
        "created_at": time.time()
    }
    storage.upload_file(BUCKET_MOSAIC_OUTPUTS, f"events/{event_id}/event.json",
                        json.dumps(event_record).encode("utf-8"), "application/json")
    return {**event_record, "attendee_count": 0, "photos_placed": 0, "patches_version": 0}


@app.post("/api/v1/events/{event_id}/upload")
async def upload_attendee_photo(
    event_id: str,
    photo: UploadFile = File(...)
):
    """
    Audience Mobile Ingestion:
    - Strips EXIF metadata, resizes to max 1024px, precomputes a 50x50 tile
    - Issues a unique Contributor Token (#T-XXXX) for "Find My Photo"
    """
    _check_event_id(event_id)
    img_bytes = await photo.read()
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(img_bytes))).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")

    max_dim = 1024
    if img.width > max_dim or img.height > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    # Unique per event: random 4-digit token, widened if the space gets crowded
    token_digits = None
    for attempt in range(30):
        width = 4 if attempt < 20 else 6
        candidate = str(secrets.randbelow(9 * 10 ** (width - 1)) + 10 ** (width - 1))
        if not storage.object_exists(BUCKET_MOSAIC_OUTPUTS, f"events/{event_id}/tokens/T-{candidate}.json"):
            token_digits = candidate
            break
    if token_digits is None:
        raise HTTPException(status_code=503, detail="Could not allocate a contributor token, please retry")
    contributor_token = f"#T-{token_digits}"
    photo_uuid = uuid.uuid4().hex[:12]

    raw_buf = io.BytesIO()
    img.save(raw_buf, format="JPEG", quality=88)
    storage.upload_file(BUCKET_PHOTO_LIBRARY, f"events/{event_id}/raw/{photo_uuid}.jpg", raw_buf.getvalue(), "image/jpeg")

    thumb = img.resize((50, 50), Image.Resampling.LANCZOS)
    thumb_buf = io.BytesIO()
    thumb.save(thumb_buf, format="JPEG", quality=85)
    thumb_key = f"events/{event_id}/thumbs-50x50/{photo_uuid}.jpg"
    storage.upload_file(BUCKET_PHOTO_LIBRARY, thumb_key, thumb_buf.getvalue(), "image/jpeg")

    event = _event_record(event_id)
    grid_size = int(event.get("grid_size", 50))
    version = len(_event_patch_keys(event_id)) + 1
    assigned_cell = (version * 17) % (grid_size * grid_size)
    col = assigned_cell % grid_size
    row = assigned_cell // grid_size
    image_url = f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{thumb_key}"
    now = time.time()

    patch = {
        "version": version,
        "cell_index": assigned_cell,
        "col": col,
        "row": row,
        "contributor_token": contributor_token,
        "image_url": image_url,
        "timestamp": now
    }
    # Time-ordered, globally unique key: safe when several gateway VMs write concurrently
    patch_key = f"events/{event_id}/patches/patch_{int(now * 1000):013d}_{photo_uuid}.json"
    storage.upload_file(BUCKET_MOSAIC_OUTPUTS, patch_key, json.dumps(patch).encode("utf-8"), "application/json")

    token_record = {
        "event_id": event_id,
        "token": contributor_token,
        "cell_index": assigned_cell,
        "col": col,
        "row": row,
        "image_url": image_url,
        "placed_at": now
    }
    storage.upload_file(BUCKET_MOSAIC_OUTPUTS, f"events/{event_id}/tokens/T-{token_digits}.json",
                        json.dumps(token_record).encode("utf-8"), "application/json")

    return {
        "success": True,
        "contributor_token": contributor_token,
        "cell_index": assigned_cell,
        "col": col,
        "row": row,
        "message": "Photo uploaded and assigned to big-screen mural!"
    }


@app.get("/api/v1/events/{event_id}/patches")
def get_event_patches(
    event_id: str,
    if_none_match: Optional[str] = Header(None)
):
    """
    Stage Screen Display Wall Polling:
    Periodically polls for incremental updates with ETag conditional headers.
    Returns attendee count and photo counter ONLY. Never shows cloud vitals.
    """
    _check_event_id(event_id)
    patch_keys = _event_patch_keys(event_id)
    current_version = len(patch_keys)
    current_etag = f'"{current_version}"'

    if if_none_match == current_etag:
        return Response(status_code=304)

    latest_patches = []
    for k in patch_keys[-20:]:
        try:
            latest_patches.append(json.loads(storage.download_file(BUCKET_MOSAIC_OUTPUTS, k)))
        except Exception:
            pass

    response_data = {
        "event_id": event_id,
        "version": current_version,
        "attendee_count": current_version,
        "photos_placed": current_version,
        "patches": latest_patches
    }

    return Response(
        content=json.dumps(response_data),
        media_type="application/json",
        headers={"ETag": current_etag, "Cache-Control": "no-cache, max-age=2"}
    )


@app.get("/api/v1/events/{event_id}/find/{token}")
def find_attendee_photo(event_id: str, token: str):
    """
    'Find My Photo' Tool:
    Attendee inputs their Contributor Token, and this endpoint returns the exact (X, Y)
    coordinate on the master mural.
    """
    _check_event_id(event_id)
    clean_token = token.strip().upper()
    m = _TOKEN_RE.match(clean_token)
    if m:
        try:
            return json.loads(storage.download_file(
                BUCKET_MOSAIC_OUTPUTS, f"events/{event_id}/tokens/T-{m.group(1)}.json", quiet=True))
        except Exception:
            pass
    raise HTTPException(status_code=404, detail=f"Token {clean_token} not found for this event.")


# ------------------------------------------------------------------------------
# Task 5.4: Administrative Lab Telemetry (/admin backend)
# ------------------------------------------------------------------------------

@app.get("/api/v1/admin/telemetry")
def get_admin_telemetry():
    """
    BRD 4.6 System Observability (admin only, unlisted /admin route).
    Real cluster vitals aggregated from worker heartbeats in the coordination bucket:
    worker CPU load, node count / scaling events over time, and tiles processed per second.
    """
    data = read_cluster_telemetry(storage, BUCKET_TASK_COORDINATION)
    data["plane"] = "gcp" if storage.use_gcp else "local_minio"
    return data


# Community pool moderation. Destructive, so unlike the read-only telemetry it requires a
# signed-in account listed in ADMIN_USERNAMES.
_PHOTO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@app.get("/api/v1/admin/community/photos")
def admin_list_community_photos(request: Request, offset: int = 0, limit: int = 60):
    admin = require_admin(request)
    limit = min(200, max(1, limit))
    offset = max(0, offset)
    keys = sorted(community_thumb_keys(storage, BUCKET_PHOTO_LIBRARY))
    page = keys[offset:offset + limit]
    tags_by_id = community_tag_index(storage, BUCKET_PHOTO_LIBRARY)

    def describe(key):
        info = {}
        idx = index_key_for(key)
        if idx:
            try:
                info = json.loads(storage.download_file(BUCKET_PHOTO_LIBRARY, idx, quiet=True))
            except Exception:
                pass
        pid = photo_id_from_key(key)
        return {"id": pid, "key": key, "thumb_url": f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{key}",
                "tile_url": f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{tile_key_for(key)}",
                "tags": sorted(tags_by_id.get(pid, [])), "contributor": info.get("contributor")}

    with ThreadPoolExecutor(max_workers=16) as ex:
        photos = list(ex.map(describe, page))
    return {"total": len(keys), "offset": offset, "limit": limit, "photos": photos, "moderator": admin["username"]}


@app.delete("/api/v1/admin/community/photos/{photo_id}")
def admin_delete_community_photo(photo_id: str, request: Request):
    """Removes one community photo everywhere: raw original, tiles, thumbnail, colour index, tag markers."""
    admin = require_admin(request)
    if not _PHOTO_ID_RE.match(photo_id):
        raise HTTPException(status_code=400, detail="Invalid photo id")
    candidates = pool_photo_keys(COMMUNITY_PREFIX, photo_id)
    candidates += [f"{COMMUNITY_PREFIX}tags/{tag}/{photo_id}"
                   for tag in community_tag_index(storage, BUCKET_PHOTO_LIBRARY).get(photo_id, [])]
    candidates += [f"thumbs-50x50/{photo_id}{ext}" for ext in IMAGE_EXTS]  # legacy root-level commons
    existing = [k for k in candidates if storage.object_exists(BUCKET_PHOTO_LIBRARY, k)]
    if not any("thumbs-50x50/" in k for k in existing):
        raise HTTPException(status_code=404, detail="No community photo with that id")
    # Thumbnail first: it is what makes a photo visible to new jobs and listings
    existing.sort(key=lambda k: "thumbs-50x50/" not in k)
    for k in existing:
        storage.delete_object(BUCKET_PHOTO_LIBRARY, k)
    logger.info(f"Moderation: {admin['username']} deleted community photo {photo_id} ({len(existing)} objects)")
    return {"status": "deleted", "photo_id": photo_id, "deleted_objects": len(existing),
            "total_photos": len(community_thumb_keys(storage, BUCKET_PHOTO_LIBRARY))}

# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# Storage Proxy (Stream objects directly to frontend with caching)

# Buckets are private; this proxy is the only read path for browsers, so it serves an explicit
# allow-list of display assets. Raw uploads, task coordination, telemetry, upload tickets and
# anything else in the buckets are never readable from outside.
_READABLE_OBJECTS = {
    BUCKET_PHOTO_LIBRARY: re.compile(
        r"^(?:(?:public|personal/[A-Za-z0-9_-]+|events/[A-Za-z0-9_-]+)/)?(?:thumbs-50x50|tiles-256)/[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp)$"),
    BUCKET_MOSAIC_OUTPUTS: re.compile(
        r"^jobs/[A-Za-z0-9_-]+/(?:final_mosaic\.png|final_mosaic_preview\.jpg|final_mosaic_thumb\.jpg|render\.png)$"),
}
_CONTENT_TYPES = {".png": "image/png", ".webp": "image/webp"}


@app.get("/api/v1/storage/{bucket_name}/{object_key:path}")
def get_storage_object(bucket_name: str, object_key: str):
    """Streams an allow-listed display asset (thumbnails, tiles, finished artboards) to the browser."""
    pattern = _READABLE_OBJECTS.get(bucket_name)
    if not pattern or ".." in object_key or not pattern.match(object_key):
        raise HTTPException(status_code=404, detail="Object not found")
    try:
        data = storage.download_file(bucket_name, object_key, quiet=True)
    except Exception:
        raise HTTPException(status_code=404, detail="Object not found")
    content_type = _CONTENT_TYPES.get(os.path.splitext(object_key)[1].lower(), "image/jpeg")
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------------------------
# Section 2.2: Persistent Personal Photo Vault
# ------------------------------------------------------------------------------

@app.get("/api/v1/library/personal")
def get_personal_library(request: Request):
    """
    Lists the caller's vault: the account's (personal/{user_id}/, follows the user everywhere) or,
    for guests, this browser session's temporary one (personal/gst_*/).
    """
    clean_session, is_guest, _ = vault_owner(request)
    if not clean_session:
        return {"session_id": None, "guest": True, "count": 0, "pending_count": 0, "photos": []}
    tags_by_id = pool_tag_index(storage, BUCKET_PHOTO_LIBRARY, personal_prefix(clean_session))
    photos = [{
        "id": photo_id_from_key(k),
        "key": k,
        "thumb_url": f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{k}",
        "filename": os.path.basename(k),
        "tags": sorted(tags_by_id.get(photo_id_from_key(k), []))
    } for k in personal_thumb_keys(storage, BUCKET_PHOTO_LIBRARY, clean_session)]

    return {
        "session_id": clean_session,
        "guest": is_guest,
        "count": len(photos),
        "pending_count": pending_incoming(storage, BUCKET_PHOTO_LIBRARY, personal_prefix(clean_session)),
        "photos": photos
    }


@app.delete("/api/v1/library/personal")
def delete_personal_library(request: Request):
    """Purges the caller's vault (account or guest session) from bucket-photo-library/personal/{id}/."""
    clean_session, _, _ = vault_owner(request)
    if not clean_session:
        return {"session_id": None, "status": "purged", "deleted_photos": 0, "deleted_count": 0, "remaining_count": 0}
    photo_count = len(personal_thumb_keys(storage, BUCKET_PHOTO_LIBRARY, clean_session))
    try:
        # Uploads still waiting for ingestion are part of the vault too
        storage.delete_prefix(BUCKET_PHOTO_LIBRARY, INCOMING_PREFIX + personal_prefix(clean_session))
        deleted_count = storage.delete_prefix(BUCKET_PHOTO_LIBRARY, personal_prefix(clean_session))
    except Exception as e:
        logger.error(f"Vault purge failed for {clean_session}: {e}")
        raise HTTPException(status_code=500, detail=f"Vault purge failed: {e}")
    remaining = len(storage.list_objects(BUCKET_PHOTO_LIBRARY, personal_prefix(clean_session)))
    if remaining:
        raise HTTPException(status_code=500, detail=f"Vault purge incomplete: {remaining} objects remain")
    logger.info(f"Purged personal library for session {clean_session}: {photo_count} photos ({deleted_count} objects) removed.")
    return {
        "session_id": clean_session,
        "status": "purged",
        "deleted_photos": photo_count,
        "deleted_count": deleted_count,
        "remaining_count": 0
    }


def _vault_bytes(user_id: str) -> int:
    """Everything the vault stores: originals, tiles, thumbnails, index, plus uploads awaiting ingestion."""
    return (storage.prefix_usage(BUCKET_PHOTO_LIBRARY, personal_prefix(user_id))
            + storage.prefix_usage(BUCKET_PHOTO_LIBRARY, INCOMING_PREFIX + personal_prefix(user_id)))


def _purge_vault(user_id: str) -> int:
    storage.delete_prefix(BUCKET_PHOTO_LIBRARY, INCOMING_PREFIX + personal_prefix(user_id))
    return storage.delete_prefix(BUCKET_PHOTO_LIBRARY, personal_prefix(user_id))


@app.get("/api/v1/users/me")
def account_summary(request: Request):
    """Account page summary: profile, stored photo count and vault storage against the quota."""
    user = require_user(request)
    uid = user["user_id"]
    return {
        "user": public_profile(user),
        "photo_count": len(personal_thumb_keys(storage, BUCKET_PHOTO_LIBRARY, uid)),
        "pending_count": pending_incoming(storage, BUCKET_PHOTO_LIBRARY, personal_prefix(uid)),
        "bytes_used": _vault_bytes(uid),
        "quota_bytes": VAULT_QUOTA_BYTES,
    }


@app.post("/api/v1/library/guest/claim")
def claim_guest_vault(request: Request, response: Response):
    """
    Called right after sign-in / sign-up: moves the photos this browser uploaded as a guest into the
    account's vault (server-side copies), credits them to the user, and ends the guest session.
    """
    user = require_user(request)
    gid = guest_id(request)
    clear_guest(response)
    if not gid:
        return {"moved_photos": 0}
    src, dst = personal_prefix(gid), personal_prefix(user["user_id"])
    keys = storage.list_objects(BUCKET_PHOTO_LIBRARY, src)
    # Thumbnails last: a photo appears in the account's vault only once all its variants are there
    keys.sort(key=lambda k: "thumbs-50x50/" in k)

    def move(key):
        target = dst + key[len(src):]
        if "/index/" in key:
            info = json.loads(storage.download_file(BUCKET_PHOTO_LIBRARY, key))
            info["contributor"] = user["username"]
            storage.upload_file(BUCKET_PHOTO_LIBRARY, target, json.dumps(info).encode("utf-8"), "application/json")
        else:
            storage.copy_object(BUCKET_PHOTO_LIBRARY, key, target)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(move, [k for k in keys if "thumbs-50x50/" not in k]))
        list(ex.map(move, [k for k in keys if "thumbs-50x50/" in k]))
    moved = len([k for k in keys if "thumbs-50x50/" in k])
    _purge_vault(gid)
    logger.info(f"Moved {moved} guest photos from {gid} into {user['username']}'s vault")
    return {"moved_photos": moved}


class PhotoDeleteRequest(BaseModel):
    photo_ids: List[str]


@app.post("/api/v1/library/personal/delete")
def delete_personal_photos(req: PhotoDeleteRequest, request: Request):
    """Deletes selected photos from the caller's vault (every stored variant + tag markers)."""
    uid, _, _ = vault_owner(request)
    if not uid:
        raise HTTPException(status_code=404, detail="This browser has no vault")
    ids = list(dict.fromkeys(req.photo_ids))
    if not ids or len(ids) > 1000:
        raise HTTPException(status_code=400, detail="Select between 1 and 1000 photos")
    if not all(_PHOTO_ID_RE.match(i) for i in ids):
        raise HTTPException(status_code=400, detail="Invalid photo id")
    prefix = personal_prefix(uid)
    tags_by_id = pool_tag_index(storage, BUCKET_PHOTO_LIBRARY, prefix)
    present = {photo_id_from_key(k) for k in personal_thumb_keys(storage, BUCKET_PHOTO_LIBRARY, uid)}

    def purge(pid):
        keys = pool_photo_keys(prefix, pid) + [f"{prefix}tags/{t}/{pid}" for t in tags_by_id.get(pid, [])]
        keys.sort(key=lambda k: "thumbs-50x50/" not in k)  # hide it from listings first
        for k in keys:
            storage.delete_object(BUCKET_PHOTO_LIBRARY, k)

    targets = [i for i in ids if i in present]
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(purge, targets))
    return {"deleted": targets, "not_found": [i for i in ids if i not in present],
            "photo_count": len(present) - len(targets)}


class PurgeRequest(BaseModel):
    confirm: str  # the username typed out, or the account password


@app.delete("/api/v1/users/me/purge")
def purge_account(req: PurgeRequest, request: Request, response: Response):
    """
    Danger zone: permanently deletes the account and every personal photo. Photos go first (so a
    failure can be retried while still signed in), then the account records, then a final sweep
    for uploads that were in flight.
    """
    user = require_user(request)
    if not confirm_owner(user, req.confirm):
        raise HTTPException(status_code=403, detail="Type your username or password exactly to confirm")
    uid = user["user_id"]
    photo_count = len(personal_thumb_keys(storage, BUCKET_PHOTO_LIBRARY, uid))
    try:
        deleted = _purge_vault(uid)
    except Exception as e:
        logger.error(f"Account purge failed for {user['username']}: {e}")
        raise HTTPException(status_code=500, detail="Deleting your photos failed; nothing else was removed. Try again.")
    delete_account_records(user)
    deleted += _purge_vault(uid)
    clear_session(response)
    return {"status": "deleted", "deleted_photos": photo_count, "deleted_objects": deleted}


class DirectUploadFile(BaseModel):
    filename: str
    content_type: str = "image/jpeg"


class DirectUploadRequest(BaseModel):
    pool: str  # "personal" (signed-in user's vault) | "community"
    tags: List[str] = []
    files: List[DirectUploadFile]


UPLOAD_TICKET_PREFIX = "upload-tickets/"
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")


@app.post("/api/v1/library/upload-urls")
def create_direct_upload_urls(req: DirectUploadRequest, request: Request, response: Response):
    """
    BRD 4.1 ingestion through the API Gateway: issues one single-use, expiring upload URL per
    file. The browser PUTs each file to the gateway, which streams it into the private bucket's
    incoming/ prefix (spooled to disk, never held in memory). Workers pick up each new object,
    analyse it and add it to the photo index.
    """
    try:
        if req.pool == "personal":
            vault_id, is_guest, user = vault_owner(request, response, create=True)
        else:
            vault_id, is_guest, user = "", False, current_user(request)
        pool_path = incoming_pool_path(req.pool, vault_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not req.files:
        raise HTTPException(status_code=400, detail="No files")
    if len(req.files) > 500:
        raise HTTPException(status_code=400, detail="At most 500 files per request")

    quota = GUEST_QUOTA_BYTES if is_guest else VAULT_QUOTA_BYTES
    if req.pool == "personal" and quota > 0 and _vault_bytes(vault_id) >= quota:
        hint = "Sign in to get a larger, permanent vault." if is_guest else "Delete some photos on the Account page first."
        raise HTTPException(status_code=413, detail=f"Your vault is full ({quota // (1024 * 1024)} MB). {hint}")

    tags = clean_tags(req.tags)
    uploads = []
    for f in req.files:
        content_type = f.content_type.lower()
        ext = CONTENT_TYPE_EXT.get(content_type)
        if not ext:
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in IMAGE_EXTS:
                uploads.append({"filename": f.filename, "error": "Unsupported file type"})
                continue
            content_type = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
        photo_id = uuid.uuid4().hex[:12]
        object_key = f"{INCOMING_PREFIX}{pool_path}{photo_id}{ext}"
        meta = {"photo_id": photo_id, "pool_path": pool_path, "tags": tags,
                "filename": f.filename[:200], "issued_at": time.time(),
                "contributor": user["username"] if user else None}
        storage.upload_file(BUCKET_PHOTO_LIBRARY, f"{INCOMING_PREFIX}{pool_path}{photo_id}.meta.json",
                            json.dumps(meta).encode("utf-8"), "application/json")
        ticket = secrets.token_urlsafe(24)
        storage.upload_file(BUCKET_TASK_COORDINATION, f"{UPLOAD_TICKET_PREFIX}{photo_id}.json", json.dumps({
            "ticket": ticket,
            "object_key": object_key,
            "content_type": content_type,
            "expires_at": time.time() + UPLOAD_URL_TTL,
        }).encode("utf-8"), "application/json")
        uploads.append({
            "photo_id": photo_id,
            "filename": f.filename,
            "content_type": content_type,
            "upload_url": f"/api/v1/library/uploads/{photo_id}?ticket={ticket}",
        })
    return {"pool": req.pool, "expires_in": UPLOAD_URL_TTL, "max_bytes": MAX_UPLOAD_BYTES, "uploads": uploads}


@app.put("/api/v1/library/uploads/{photo_id}")
async def relay_upload(photo_id: str, ticket: str, request: Request):
    """
    Receives one photo issued by /upload-urls and streams it into the private bucket.
    The body is spooled to a temp file (memory only for the first 1 MB) and size-capped.
    """
    if not re.fullmatch(r"[a-f0-9]{12}", photo_id):
        raise HTTPException(status_code=404, detail="Unknown upload")
    ticket_key = f"{UPLOAD_TICKET_PREFIX}{photo_id}.json"
    try:
        info = json.loads(storage.download_file(BUCKET_TASK_COORDINATION, ticket_key, quiet=True))
    except Exception:
        raise HTTPException(status_code=404, detail="Unknown or already used upload URL")
    if not secrets.compare_digest(info.get("ticket", ""), ticket):
        raise HTTPException(status_code=403, detail="Invalid upload ticket")
    if time.time() > info.get("expires_at", 0):
        storage.delete_object(BUCKET_TASK_COORDINATION, ticket_key)
        raise HTTPException(status_code=410, detail="Upload URL expired; request a new one")

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    size = 0
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
            spool.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Empty upload")
        spool.seek(0)
        head = spool.read(12)
        if not any(head.startswith(m) for m in _IMAGE_MAGIC):
            raise HTTPException(status_code=415, detail="Not a JPEG, PNG or WebP image")
        spool.seek(0)
        # Blocking storage I/O runs in the threadpool so the event loop keeps serving requests
        await run_in_threadpool(storage.upload_stream, BUCKET_PHOTO_LIBRARY, info["object_key"], spool,
                                info.get("content_type"))
    storage.delete_object(BUCKET_TASK_COORDINATION, ticket_key)  # single use
    return {"photo_id": photo_id, "bytes": size, "status": "accepted"}


@app.post("/api/v1/library/personal/upload")
async def upload_personal_photos(
    request: Request,
    response: Response,
    photos: List[UploadFile] = File(...),
    tags: str = Form("")
):
    """Server-side ingestion fallback (the web app uses direct signed-URL uploads)."""
    clean_session, _, _ = await run_in_threadpool(lambda: vault_owner(request, response, create=True))

    uploaded_records = []
    failed = []
    for photo in photos:
        try:
            content = await photo.read()
            photo_uuid = uuid.uuid4().hex[:12]
            keys = ingest_photo(storage, BUCKET_PHOTO_LIBRARY, personal_prefix(clean_session), photo_uuid, content,
                                tags=_split_tags(tags))
            uploaded_records.append({
                "id": photo_uuid,
                "key": keys["thumb"],
                "thumb_url": f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{keys['thumb']}",
                "filename": photo.filename
            })
        except Exception as ex:
            failed.append(photo.filename)
            logger.warning(f"Failed to ingest personal photo {photo.filename}: {ex}")

    return {
        "session_id": clean_session,
        "uploaded_count": len(uploaded_records),
        "failed_count": len(failed),
        "failed_files": failed,
        "total_count": len(personal_thumb_keys(storage, BUCKET_PHOTO_LIBRARY, clean_session)),
        "uploaded_photos": uploaded_records
    }


# ------------------------------------------------------------------------------
# Section 3.2: Community Gallery & Public Commons Ingestion
# ------------------------------------------------------------------------------

def _split_tags(tags: str) -> List[str]:
    return clean_tags(tags.split(",")) if tags else []


@app.get("/api/v1/library/community/stats")
def get_community_stats(tags: str = ""):
    """Community commons size; with `tags` (comma-separated) also the count eligible under that filter."""
    all_keys = community_thumb_keys(storage, BUCKET_PHOTO_LIBRARY)
    selected = _split_tags(tags)
    tags_by_id = community_tag_index(storage, BUCKET_PHOTO_LIBRARY)
    tag_counts: Dict[str, int] = {}
    for photo_tags in tags_by_id.values():
        for t in photo_tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    return {
        "total_photos": len(all_keys),
        "filtered_photos": len(community_thumb_keys(storage, BUCKET_PHOTO_LIBRARY, selected)) if selected else len(all_keys),
        "untagged_photos": sum(1 for k in all_keys if photo_id_from_key(k) not in tags_by_id),
        "pending_count": pending_incoming(storage, BUCKET_PHOTO_LIBRARY, COMMUNITY_PREFIX),
        "tag_counts": tag_counts,
        "active_contributors": max(8, int(len(all_keys) * 0.4)),
        "status": "active"
    }


@app.get("/api/v1/library/community/recent")
def get_community_recent(limit: int = 40):
    """Returns grid of recent community submissions."""
    keys = community_thumb_keys(storage, BUCKET_PHOTO_LIBRARY)
    tag_index = community_tag_index(storage, BUCKET_PHOTO_LIBRARY)

    recent_keys = keys[-limit:] if len(keys) > limit else keys
    items = []
    for k in reversed(recent_keys):
        photo_id = photo_id_from_key(k)
        items.append({
            "id": photo_id,
            "key": k,
            "thumb_url": f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{k}",
            "filename": os.path.basename(k),
            "tags": sorted(tag_index.get(photo_id, []))
        })
    return {
        "total": len(keys),
        "recent": items
    }


@app.post("/api/v1/library/community/upload")
async def upload_community_photos(
    request: Request,
    photos: List[UploadFile] = File(...),
    tags: str = Form("")
):
    """Accepts donation photos, normalises tiles, and adds them (with optional category tags) to the commons."""
    tag_list = _split_tags(tags)
    donor = await run_in_threadpool(current_user, request)
    uploaded = []
    failed = []
    for photo in photos:
        try:
            content = await photo.read()
            photo_uuid = uuid.uuid4().hex[:12]
            keys = ingest_photo(storage, BUCKET_PHOTO_LIBRARY, COMMUNITY_PREFIX, photo_uuid, content, tags=tag_list,
                                contributor=donor["username"] if donor else None)
            uploaded.append({
                "id": photo_uuid,
                "thumb_url": f"/api/v1/storage/{BUCKET_PHOTO_LIBRARY}/{keys['thumb']}",
                "filename": photo.filename,
                "tags": tag_list
            })
        except Exception as ex:
            failed.append(photo.filename)
            logger.warning(f"Failed to process community donation {photo.filename}: {ex}")

    return {
        "status": "success" if uploaded else "failed",
        "donated_count": len(uploaded),
        "failed_count": len(failed),
        "total_community_photos": len(community_thumb_keys(storage, BUCKET_PHOTO_LIBRARY)),
        "donated_items": uploaded
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
