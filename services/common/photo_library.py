"""
Photo Library Layout (bucket-photo-library)
Shared by gateway (upload URLs, listing), worker (ingestion, pool selection) and assembler (tile rendering).

Direct ingestion (BRD 4.1): browsers PUT original files straight to the bucket via signed URLs:
    incoming/{pool_path}{id}{ext}          original upload (pool_path = "public/" or "personal/{session}/")
    incoming/{pool_path}{id}.meta.json     upload metadata (tags, filename) written when the URL is issued
Workers pick up each new incoming object, analyse it and publish it into the pool:

Per pool prefix (``public/``, ``personal/{session_id}/``, ``events/{event_id}/``):
    raw/{id}.jpg            original aspect, max 2048px (tile inspector, large export cells)
    tiles-256/{id}.jpg      256x256 centre-cropped tile used when stitching the master canvas
    thumbs-50x50/{id}.jpg   50x50 centre-cropped tile used for previews
    index/{id}.json         colour features (mean + 2x2 sub-region RGB) used for matching, plus the
                            tags and the contributor's username (None for anonymous donations)
    tags/{Tag}/{id}         empty marker objects: bucket-only tag index
"""

import io
import json
import re
from typing import Dict, Iterable, List, Optional, Set

import numpy as np
from PIL import Image, ImageOps

THUMB_DIR = "thumbs-50x50/"
TILE_DIR = "tiles-256/"
RAW_DIR = "raw/"
INDEX_DIR = "index/"
TAGS_DIR = "tags/"
COMMUNITY_PREFIX = "public/"
LEGACY_THUMB_PREFIX = "thumbs-50x50/"
INCOMING_PREFIX = "incoming/"

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
CONTENT_TYPE_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_TAG_RE = re.compile(r"[^A-Za-z0-9 _-]")
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def valid_session_id(session_id: Optional[str]) -> bool:
    return bool(session_id) and bool(_SESSION_RE.match(session_id))


def personal_prefix(session_id: str) -> str:
    return f"personal/{session_id}/"


def clean_tags(tags: Optional[Iterable[str]]) -> List[str]:
    """Normalises user-supplied tags into safe object-key segments."""
    out = []
    for t in tags or []:
        t = _TAG_RE.sub("", str(t)).strip()
        if t and t not in out:
            out.append(t[:40])
    return out


def center_crop_square(img: Image.Image) -> Image.Image:
    side = min(img.width, img.height)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    return img.crop((left, top, left + side, top + side))


def tile_features(square_img: Image.Image) -> Dict[str, list]:
    """Mean colour and 2x2 sub-region colours (RGB). BOX resampling = exact area averages."""
    img = square_img.convert("RGB")
    avg = np.asarray(img.resize((1, 1), Image.Resampling.BOX), dtype=np.float64).reshape(3)
    sub = np.asarray(img.resize((2, 2), Image.Resampling.BOX), dtype=np.float64).reshape(4, 3)
    return {"rgb": [round(float(v), 2) for v in avg], "sub_rgb": [[round(float(v), 2) for v in s] for s in sub]}


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def ingest_photo(storage, bucket: str, base_prefix: str, photo_id: str, image_bytes: bytes,
                 tags: Optional[List[str]] = None, raw_max_dim: int = 2048,
                 contributor: Optional[str] = None) -> Dict[str, str]:
    """
    Analyses one photo (EXIF orientation applied, metadata stripped) and publishes it into a pool:
    raw / tiles-256 / thumbs-50x50 variants, its colour-feature index entry and tag markers.
    The thumbnail is written last: it is what makes the photo visible in pool listings.
    Raises on undecodable input.
    """
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")

    raw = img.copy()
    raw.thumbnail((raw_max_dim, raw_max_dim), Image.Resampling.LANCZOS)
    square = center_crop_square(img)
    tile = square.resize((256, 256), Image.Resampling.LANCZOS)
    thumb = square.resize((50, 50), Image.Resampling.LANCZOS)
    clean = clean_tags(tags)

    keys = {
        "raw": f"{base_prefix}{RAW_DIR}{photo_id}.jpg",
        "tile": f"{base_prefix}{TILE_DIR}{photo_id}.jpg",
        "index": f"{base_prefix}{INDEX_DIR}{photo_id}.json",
        "thumb": f"{base_prefix}{THUMB_DIR}{photo_id}.jpg",
    }
    storage.upload_file(bucket, keys["raw"], _jpeg(raw, 90), "image/jpeg")
    storage.upload_file(bucket, keys["tile"], _jpeg(tile, 90), "image/jpeg")
    features = {"id": photo_id, "tags": clean, "contributor": contributor, **tile_features(tile)}
    storage.upload_file(bucket, keys["index"], json.dumps(features).encode("utf-8"), "application/json")
    for tag in clean:
        # Non-empty body: zero-length PUTs stall with MinIO + urllib3 (HeaderParsingError, ~30s timeout)
        storage.upload_file(bucket, f"{base_prefix}{TAGS_DIR}{tag}/{photo_id}", b"1", "text/plain")
    storage.upload_file(bucket, keys["thumb"], _jpeg(thumb, 90), "image/jpeg")
    return keys


def photo_id_from_key(key: str) -> str:
    return key.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def pool_prefix_of(thumb_key: str) -> str:
    return thumb_key.split(THUMB_DIR, 1)[0] if THUMB_DIR in thumb_key else ""


def index_key_for(thumb_key: str) -> Optional[str]:
    if THUMB_DIR not in thumb_key:
        return None
    return f"{pool_prefix_of(thumb_key)}{INDEX_DIR}{photo_id_from_key(thumb_key)}.json"


def render_tile_candidates(thumb_key: str, cell_px: int = 256) -> List[str]:
    """
    Best-to-worst source objects for stitching a tile matched via its thumbnail key. Cells larger
    than the 256px tile (high-res / gigapixel exports) are cut from the original photo instead, so
    zooming into the export shows real detail rather than an upscaled thumbnail.
    """
    candidates = []
    if THUMB_DIR in thumb_key:
        tile, raw = thumb_key.replace(THUMB_DIR, TILE_DIR), thumb_key.replace(THUMB_DIR, RAW_DIR)
        candidates += [raw, tile] if cell_px > 256 else [tile, raw]
    candidates.append(thumb_key)
    return candidates


def _has_variants(thumb_key: str) -> bool:
    # Legacy commons photos live at the bucket root (thumbs-50x50/{id}) with no raw or 256px tile
    return THUMB_DIR in thumb_key and not thumb_key.startswith(LEGACY_THUMB_PREFIX)


def raw_key_for(thumb_key: str) -> Optional[str]:
    return thumb_key.replace(THUMB_DIR, RAW_DIR) if _has_variants(thumb_key) else None


def tile_key_for(thumb_key: str) -> str:
    """The 256px tile for a thumbnail key (the thumbnail itself for legacy photos without one)."""
    return thumb_key.replace(THUMB_DIR, TILE_DIR) if _has_variants(thumb_key) else thumb_key


def pool_photo_keys(base_prefix: str, photo_id: str) -> List[str]:
    """Every derived object of one photo in a pool (tag markers are listed separately)."""
    return [f"{base_prefix}{RAW_DIR}{photo_id}.jpg", f"{base_prefix}{TILE_DIR}{photo_id}.jpg",
            f"{base_prefix}{INDEX_DIR}{photo_id}.json", f"{base_prefix}{THUMB_DIR}{photo_id}.jpg"]


def _thumb_keys(storage, bucket: str, prefix: str) -> List[str]:
    return [k for k in storage.list_objects(bucket, prefix) if k.lower().endswith(IMAGE_EXTS)]


def tag_index(storage, bucket: str, base_prefix: str) -> Dict[str, Set[str]]:
    """photo_id -> set of tags for one pool, built from a single listing of its tag markers."""
    root = f"{base_prefix}{TAGS_DIR}"
    index: Dict[str, Set[str]] = {}
    for k in storage.list_objects(bucket, root):
        parts = k[len(root):].split("/")
        if len(parts) == 2 and parts[1]:
            index.setdefault(parts[1], set()).add(parts[0])
    return index


def community_tag_index(storage, bucket: str) -> Dict[str, Set[str]]:
    return tag_index(storage, bucket, COMMUNITY_PREFIX)


def _filter_by_tags(keys: List[str], index: Dict[str, Set[str]], tags: Optional[List[str]]) -> List[str]:
    """BRD 4.3: with a tag selection, only photos carrying at least one selected tag are used."""
    selected = set(clean_tags(tags))
    if not selected:
        return keys
    return [k for k in keys if index.get(photo_id_from_key(k), set()) & selected]


def community_thumb_keys(storage, bucket: str, tags: Optional[List[str]] = None) -> List[str]:
    keys = _thumb_keys(storage, bucket, COMMUNITY_PREFIX + THUMB_DIR)
    keys += _thumb_keys(storage, bucket, LEGACY_THUMB_PREFIX)
    if not clean_tags(tags):
        return keys
    return _filter_by_tags(keys, community_tag_index(storage, bucket), tags)


def personal_thumb_keys(storage, bucket: str, session_id: Optional[str], tags: Optional[List[str]] = None) -> List[str]:
    if not valid_session_id((session_id or "").strip()):
        return []
    prefix = personal_prefix(session_id.strip())
    keys = _thumb_keys(storage, bucket, prefix + THUMB_DIR)
    if not clean_tags(tags):
        return keys
    return _filter_by_tags(keys, tag_index(storage, bucket, prefix), tags)


def resolve_pool(storage, bucket: str, session_id: Optional[str], use_personal: bool,
                 use_community: bool, tags: Optional[List[str]] = None) -> Dict[str, List[str]]:
    personal = personal_thumb_keys(storage, bucket, session_id, tags) if use_personal else []
    community = community_thumb_keys(storage, bucket, tags) if use_community else []
    return {"personal": personal, "community": community,
            "all": list(dict.fromkeys(personal + community))}


# ----------------------------------------------------------------------------
# Direct ingestion (incoming/)
# ----------------------------------------------------------------------------

def incoming_pool_path(pool: str, session_id: Optional[str] = None) -> str:
    if pool == "community":
        return COMMUNITY_PREFIX
    if pool == "personal" and valid_session_id(session_id):
        return personal_prefix(session_id)
    raise ValueError("pool must be 'community' or 'personal' with a valid session_id")


def pending_incoming(storage, bucket: str, pool_path: str) -> int:
    """Uploads accepted for this pool that a worker has not ingested yet."""
    return len([k for k in storage.list_objects(bucket, INCOMING_PREFIX + pool_path)
                if k.lower().endswith(IMAGE_EXTS) and "/" not in k[len(INCOMING_PREFIX + pool_path):]])
