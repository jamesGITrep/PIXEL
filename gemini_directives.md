# GEMINI_DIRECTIVES.md — Master Project Blueprint & Execution Tracker
> **Project:** Crowd-Sourced Collaborative Art & High-Compute Raster Platform
> **Architecture:** Local-First (Docker Compose + MinIO) to GCP Production (MIG + GCS + Load Balancer)
> **Core Constraints:** Zero Relational/SQL Databases (Bucket-Only Storage) • Zero Dedicated GPU Dependencies • Strict Admin-Only Telemetry Isolation

---

## 1. Context & Architecture Overview

This platform processes large-scale, crowd-sourced digital art and high-density raster transformations. It operates on a **Dual-Environment Architecture**:
1. **Local Development Plane (Testing/Verification):** Containerized in `docker-compose.yml`. Emulates object storage via MinIO (S3 API compatible with GCS), mocks health checks, and runs multiple worker container replicas on local CPU.
2. **GCP Production Plane (Live Scaled Deployment):** Identical codebase running on Google Cloud Storage (GCS) buckets, an External Application Load Balancer with HTTP Health Checks (`/health` -> 200 OK), and Compute Engine Managed Instance Groups (MIG) with Spot/Preemptible VMs for auto-scaled CPU raster computations.

### The Storage Abstraction Rule ("Write Once, Run Anywhere")
All backend microservices (`gateway`, `worker`, `assembler`) must use a unified storage client wrapper:
- When `USE_GCP=false`: Uses `boto3` or `@aws-sdk/client-s3` targeting `http://minio:9000` with local mock credentials.
- When `USE_GCP=true`: Uses `google-cloud-storage` using standard GCP Workload Identity / Service Account credentials.
- **Bucket Hierarchy (Strictly Bucket-Only, Zero SQL):**
  - `bucket-photo-library`: Public/event uploaded raw photos, precomputed 50x50 thumbnails, and `metadata/global_index.parquet`.
  - `bucket-target-posters`: Uploaded high-res target master posters (`{job_id}/original.ext`).
  - `bucket-task-coordination`: Job manifests, sub-task coordinate JSONs, and atomic task lock tokens (`locks/chunk_{x}_{y}.lock`).
  - `bucket-mosaic-outputs`: Incremental patches for live display screens, rendered chunk bitmaps, and final stitched artboards.

---

## 2. Microservices Architecture Map
[ User / Mobile / Big Screen Client ]
                                |
               +----------------+----------------+
               |                                 |
     [ / (Clean Homepage) ]             [ /api/v1/jobs/create ]
     [ /live (Live Event) ]             [ /health (200)       ]
     [ /studio (Studio)   ]                      |
               |                                 v
               v                    +--------------------------+
  +-------------------------+       |     GATEWAY SERVICE      |
  |    FRONTEND SERVICE     |       |  - FastAPI / Python      |
  |  - Next.js / Vite React |       |  - Issues Signed URLs    |
  |  - WebGL Display Wall   |       |  - Slices Target Images  |
  |  - No Cloud Vitals      |       |  - Dispatches Task JSONs |
  +-------------------------+       +------------+-------------+
                                                 |
                  +------------------------------+
                  | Writes Tasks & Images
                  v
    +----------------------------+
    | GCS BUCKETS / MINIO (MOCK) | <===================+
    +--------------+-------------+                     |
                   |                                   |
   Worker Claims   | Atomic Locks                      |
   Task via Lock   | (if-generation-match=0)           |
                   v                                   |
    +----------------------------+                     |
    |   DISTRIBUTED WORKER MIG   |                     |
    |   (CPU Compute Engine)     | --------------------+
    |   - Engine 1: Photomosaic  |  Writes Completed
    |   - Engine 2: ASCII Art    |  Chunks & Live Patches
    |   - Engine 3: Voronoi      |
    |   - Engine 4: Deep-Zoom    |
    +--------------+-------------+
                   |
                   | Triggers Completion
                   v
    +----------------------------+
    |  CANVAS ASSEMBLER SERVICE  |
    |  - Stitches Finished Chunks| --------------------+
    |  - Generates Final Zoomable| Writes Final Output
    +----------------------------+

    ---

## 3. UI/UX Specifications (Clean Slate Layout)

### Homepage (`/`)
- Minimalist, distraction-free hero section.
- **ONLY TWO CARDS:**
  - **Option 1: Live Event Arena:** Takes user to `/live` (Interactive Big-Screen Mural, Event QR Code, Mobile Attendee Camera Ingestion, "Find My Photo" tool).
  - **Option 2: Photo Mosaic & Studio Workshop:** Takes user to `/studio` (Full raster workstation with multi-engine selection, asset pool blend slider, tag filters, grid density, no-repeat buffer, and alpha-tint slider).
- **CRITICAL:** Zero developer test buttons, zero synthetic preset generators, and zero cloud/worker performance metrics on user-facing routes.

### Administrative Route (`/admin`)
- Accessible strictly via direct URL `/admin` (completely hidden from main navigation).
- Displays internal system manager telemetry: Grafana dashboards, active CPU worker topology, task throughput/sec, and Spot VM preemption simulation.

---

## 4. Master Phased TODO Checklist

> **Instruction for AI Agents:** When completing a task, change `[ ]` to `[x]` and document the implementation date and verified output. Do NOT skip steps or deviate from the bucket-only architecture.

### Phase 1: Environment Setup & Data Purge
- [x] **Task 1.1: Clean Slate Purge** *(Verified: 2026-10-26)*
  - Deleted all legacy mock photo files, temporary coordinate JSONs, test outputs, and unused static assets.
  - Purged toy client-side scripts, synthetic presets, and mock workers in `src/`.
- [x] **Task 1.2: Local Storage Abstraction Layer** *(Verified: 2026-10-26)*
  - Implemented `services/common/storage.py` (Unified interface for MinIO and GCS).
  - Supports operations: `upload_file`, `download_file`, `generate_signed_url`, `acquire_lock`, `release_lock`, `list_objects`, `ensure_buckets_exist`.
- [x] **Task 1.3: Docker Compose Environment** *(Verified: 2026-10-26)*
  - Configured `docker-compose.yml` defining services: `minio`, `minio-init`, `gateway`, `worker` (scalable with `--scale worker=3`), `assembler`, `frontend`.
  - Created MinIO auto-provisioning script initializing the 4 required buckets (`bucket-photo-library`, `bucket-target-posters`, `bucket-task-coordination`, `bucket-mosaic-outputs`).

### Phase 2: Gateway Microservice & Task Dispatcher
- [x] **Task 2.1: Gateway Health & Ingestion API** *(Verified: 2026-10-26)*
  - Implemented `/health` returning HTTP 200 `{"status": "healthy"}` for Cloud Load Balancer health checks.
  - Implemented direct upload endpoint issuing V4 Signed URLs and MinIO presigned PUT URLs (`/api/v1/upload-url`).
  - Implemented Live Event upload with EXIF orientation stripping and Contributor Token generator (`#T-XXXX`).
- [x] **Task 2.2: Image Slicer & Task Partitioner** *(Verified: 2026-10-26)*
  - Implemented master target image processor that downsamples/normalizes master poster and divides it into an $(N \times M)$ grid of task chunks (`/api/v1/jobs/create`).
  - Writes task descriptors (`chunk_001.json`, etc.) and `manifest.json` into `bucket-task-coordination/jobs/{job_id}/tasks/`.

### Phase 3: Distributed Worker Compute Core (CPU-Only)
- [x] **Task 3.1: Atomic Bucket Locking Engine** *(Verified: 2026-10-26)*
  - Implemented worker task reservation using conditional atomic creation (`if-generation-match=0` on GCS, atomic lock object on MinIO).
  - Implemented stale lock detection and lease renewal timeout (60-second recovery for preemption).
- [x] **Task 3.2: Mode 1 — Photomosaic Engine** *(Verified: 2026-10-26)*
  - Implemented vectorized sRGB to CIE $L^*a^*b^*$ batch transformation in `services/worker/engines/color_math.py`.
  - Implemented CIEDE2000 ($\Delta E_{00}$) perceptual color difference calculation.
  - Implemented spatial No-Repeat proximity buffer (Chebyshev radius rejection).
- [x] **Task 3.3: Mode 2 — High-Density Structural ASCII Engine** *(Verified: 2026-10-26)*
  - Implemented 3x3 Sobel directional edge gradient filter in `services/worker/engines/ascii_engine.py`.
  - Correlated edge orientation angles ($\theta = \arctan(G_y / G_x)$) to directional character glyphs (`─`, `│`, `╱`, `╲`) and density ramp.
- [x] **Task 3.4: Mode 3 — Geometric Voronoi Engine** *(Verified: 2026-10-26)*
  - Implemented edge-weighted seed point distribution based on Canny/Sobel contours in `services/worker/engines/voronoi_engine.py`.
  - Implemented Delaunay triangulation and Lloyd's centroidal relaxation iterations with stained-glass leaded seam rasterization.
- [x] **Task 3.5: Mode 4 — Deep-Zoom Pyramid Slicer** *(Verified: 2026-10-26)*
  - Slices final outputs into multi-scale Deep Zoom tiles ($256 \times 256$ pyramid levels) and OpenSeadragon DZI XML in `services/worker/engines/deepzoom_engine.py`.
  - Progressive assembly time-lapse animation frames generator.
- [x] **Task 3.6: Live Event Delta-Matcher** *(Verified: 2026-10-26)*
  - Worker sub-routine that matches newly uploaded crowd photos against current grid tiles and writes diff patches to `bucket-mosaic-outputs/{job_id}/patches/`.

### Phase 4: Canvas Assembler & Output Pipeline
- [x] **Task 4.1: Chunk Stitcher** *(Verified: 2026-10-26)*
  - Worker in `services/assembler/assembler.py` that watches `bucket-task-coordination/jobs/{job_id}/tasks/` for completion.
  - Stitches completed chunk bitmaps into a unified master canvas using OpenCV.
- [x] **Task 4.2: Dynamic Alpha-Tinting Blender** *(Verified: 2026-10-26)*
  - Blends the original target image over assembled tiles with a configurable opacity slider ($0\%$ to $50\%$) using OpenCV `addWeighted`.
  - Generates master `final_mosaic.png` and fast preview thumbnail `final_mosaic_thumb.jpg`.

### Phase 5: Clean Client-Side Web Application
- [x] **Task 5.1: Two-Choice Landing Page (`/`)** *(Verified: 2026-10-26)*
  - Clean hero interface with Option 1: Live Event (`/live`) and Option 2: Photo Mosaic Studio (`/studio`).
  - Zero cloud infrastructure metrics, zero synthetic preset generators, zero developer test buttons.
- [x] **Task 5.2: Live Event Arena (`/live`)** *(Verified: 2026-10-26)*
  - Host event creation (target image upload, event code, QR code generator).
  - Mobile Attendee Capture View (PWA-ready camera upload, Contributor Token generator).
  - Stage Big-Screen Wall: Fullscreen Canvas polling patch updates with spotlight zoom animations.
  - "Find My Photo" token search and coordinate locator.
- [x] **Task 5.3: Photo Mosaic Studio Workshop (`/studio`)** *(Verified: 2026-10-26)*
  - Full creative suite: Master image upload, 4-mode selector, blend slider (Personal vs. Public pool), tag filters, grid density slider, and alpha-tint slider.
  - Interactive zoomable canvas viewer with high-resolution export.
- [x] **Task 5.4: Administrative Lab (`/admin`)** *(Verified: 2026-10-26)*
  - Unlisted route with embedded Grafana telemetry.
  - Displays real-time CPU saturation, MIG worker count, throughput, GCS lock latency, and interactive Spot VM preemption simulation.

### Phase 6: Cloud Deployment & Verification (GCP Target)
- [x] **Task 6.1: Containerization & Artifact Registry** *(Verified: 2026-10-26)*
  - Built and verified production Dockerfiles for `gateway`, `worker`, `assembler`, and `frontend`.
- [x] **Task 6.2: Terraform / GCP Deployment Scripts** *(Verified: 2026-10-26)*
  - Created `terraform/main.tf`, `terraform/variables.tf`, `terraform/cors.json`, `terraform/outputs.tf`.
  - Provisioned 4 GCS buckets with CORS configuration.
  - Set up Cloud Load Balancer routing to Frontend and Gateway services with HTTP Health Checks (`/health` returning 200).
  - Set up Compute Engine Managed Instance Group (MIG) with Spot VMs configured with CPU autoscaling (target 75% CPU utilization).
- [x] **Task 6.3: End-to-End System Verification** *(Verified: 2026-10-26)*
  - Verified Docker Compose configuration syntax with `docker compose config`.
  - Verified all 4 microservices compute engines, CIELAB math, and Gateway `/health` with Python test suite.
  - Compiled and built frontend production bundle via Vite (`dist/index.html`, `dist/assets/`).

---

### Phase 7: UI/UX Polish, Motion Design & Runtime Fixes (ACTIVE)

- [ ] **Task 7.1: Resolve Docker Compose MinIO Registry Image 404**
  - Update `docker-compose.yml` to pull MinIO and the MinIO Client from `quay.io` instead of the deprecated Docker Hub repositories:
    ```yaml
    minio:
      image: quay.io/minio/minio:RELEASE.2024-11-07T00-52-20Z
      # ...
    minio-init:
      image: quay.io/minio/mc:RELEASE.2024-11-05T11-41-28Z
      # ...
    ```
  - Verify container startup via `docker compose up -d --scale worker=3`.

- [ ] **Task 7.2: Glassmorphic Visual Polish & Landing Page Animations**
  - Refine `/` with animated gradient backdrop, micro-interactions on the 2 primary cards, hover states, and smooth navigation transitions.
  - Ensure zero cloud vitals or test buttons appear anywhere on public paths.

- [ ] **Task 7.3: Studio Workshop Interactive Workspace Enhancements**
  - Smooth drag-and-drop zone with instant thumbnail preview and dimensions check.
  - Interactive mode switch tabs (Photomosaic, ASCII Glyphs, Voronoi, Deep-Zoom) with real-time parameter cards.
  - Canvas preview viewport supporting drag-pan, pinch/wheel-zoom, and instant alpha-tint blending ($0\%$ to $50\%$) with before/after comparison slider.
  - Progress tracker showing stage completion (Uploading -> Chunk Slicing -> Distributed Rendering -> Assembly).

- [ ] **Task 7.4: Live Event Arena Big-Screen Wall & Spotlight Animations**
  - Implement smooth cross-fade and scale-in animations for arriving patches in `/live`.
  - Implement the **Spotlight Loop**: automatically pans and zooms into freshly placed attendee photos, showing their Contributor Badge and coordinate tag before easing back to full canvas.
  - Contributor counter with increment animations.

- [ ] **Task 7.5: Mobile Attendee Flow Polish**
  - Mobile-optimized upload interface for smartphones scanning the stage QR code.
  - Image preview, auto-orientation normalization, and instant generation of shareable Contributor Card (`#T-XXXX`).
  - Interactive "Find My Photo" interface panning and spotlighting the user's specific tile on the canvas.
  