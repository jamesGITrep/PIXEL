# ❖ Crowd-Sourced Collaborative Art & High-Compute Raster Platform — Master Architecture & Progress Tracker

> **Directives Document:** `gemini_directives.md` & `ANTIGRAVITY_PROJECT_SPEC_AND_TODO.txt`  
> **Core Mandate:** **Zero-Backend • Zero-GPU Server • Pure Client-Side Visual Engine + GCP Bucket Data Plane**  
> **Hosting Target:** 100% Static Web Hosting (Google Cloud Storage Static Website / Cloud CDN, Firebase Hosting, Cloudflare Pages)  
> **Status Overview:** Complete Core Implementation Active • Verified in Browser & Production Static Bundle

---

## 🏗️ Architectural Topology

```mermaid
graph TD
    subgraph Attendees ["Live Event Attendees"]
        MobilePWA[Attendee Mobile PWA] -->|1. Direct PUT Signed URL / CORS Policy| GCS_RAW[(gs://bucket-photo-library/raw/)]
        MobilePWA -->|2. Receive Contributor Token #T-XXXX| ContributorBadge[Selfie Confirmation Badge]
    end

    subgraph GCP_Storage ["GCP Bucket-Only Data Plane"]
        GCS_RAW
        GCS_THUMBS[(gs://bucket-photo-library/thumbs-50x50/)]
        GCS_INDEX[(gs://bucket-photo-library/metadata/global_index.parquet)]
        GCS_TARGET[(gs://bucket-target-posters/{job_id}/original.ext)]
        GCS_COORD[(gs://bucket-task-coordination/jobs/{job_id}/)]
        GCS_OUTPUT[(gs://bucket-mosaic-outputs/{job_id}/)]
    end

    subgraph Client_Engine ["Pure Client-Side Visual Engine (Browser & Kiosk)"]
        Hero[2-Option Landing Page: /live vs /studio]
        Kiosk[Stage Wall Kiosk & 'Find My Photo' Tool]
        Studio[Photo Mosaic Studio Workshop]
        Admin[Admin Telemetry & MIG Lab]
        
        subgraph Workers ["Multi-Threaded Web Workers (Zero UI Freezing)"]
            ColorWorker[CIE L*a*b* Converter & LUT]
            KDTreeWorker[3D KD-Tree Nearest Neighbor]
            SobelWorker[Sobel Edge Detector]
            VoronoiWorker[Delaunay / Voronoi Generator]
        end

        subgraph Modes ["4 Comprehensive Compute Modes"]
            M1[Mode 1: CIE LAB Delta-E 2000 Photomosaic]
            M2[Mode 2: Structural ASCII / Unicode Matrix]
            M3[Mode 3: Geometric Voronoi Glass]
            M4[Mode 4: Gigapixel Deep-Zoom & Time-Lapse]
        end

        Kiosk -->|Conditional GET If-None-Match ETag| GCS_OUTPUT
        Kiosk <--> Workers
        Studio <--> Workers
        Workers --> Modes
    end
```

---

## 📋 Comprehensive Implementation Checklist (TODO)

### 🧹 PHASE 1: Purge & Initial Repository Sanitization (Clean Slate Purge)
- [x] **1.1 Clean Slate Data & Asset Purge**
  - [x] Delete all pre-bundled photo datasets (`storage/bucket-photo-library/*`, mock/sample photos).
  - [x] Delete all pre-computed thumbnails (`storage/bucket-thumbnails/*`).
  - [x] Delete legacy metadata catalogs and precomputed feature index blobs (`storage/metadata/image_index.csv`, `jobs.json`, `cluster_state.json`).
  - [x] Delete legacy output renders, mappings, and task files.
  - [x] Remove legacy ONNX deep learning neural models (`models/mobilenetv2-7.onnx`).
  - [x] Ensure system initializes from absolute zero state with no stale local cache dependencies.

- [x] **1.2 Legacy Backend Framework & Microservice Retirement**
  - [x] Deprecate Python microservice scripts into `legacy_backend/`.
  - [x] Deprecate legacy Python HTTP server (`web/server.py`).
  - [x] Isolate Docker server orchestration (`docker-compose.yml`, `Dockerfile`) in favor of static edge hosting.

- [x] **1.3 Clean Client-First Modular Directory Architecture**
  - [x] Setup `/src/core` (compute engines, color spaces, KD-tree, distance metrics).
  - [x] Setup `/src/workers` (Web Workers for multi-threaded non-blocking processing).
  - [x] Setup `/src/modes` (photomosaic, ascii, voronoi, deepzoom).
  - [x] Setup `/src/cloud` (GCP bucket readers, direct upload managers, manifest/patch pollers, task coordinator).
  - [x] Setup `/src/ui` (unified responsive kiosk display, attendee upload mobile PWA, parameter controls).

- [x] **1.4 Modern Build & Bundler Tooling Setup**
  - [x] Configure bundler (Vite 5.4+) supporting native ES modules and zero-backend preview.
  - [x] Setup multi-worker bundling support (`new Worker(new URL(...), { type: 'module' })`).
  - [x] Validate static production build (`dist/`) deployable to GCS static hosting, Cloudflare Pages, or Firebase Hosting.

---

### 🧮 PHASE 2: Core Client-Side Mathematical & Color Engine
- [x] **2.1 sRGB to CIE $L^*a^*b^*$ Color Space Pipeline**
  - [x] Implement sRGB -> linear RGB -> CIE XYZ (D65 standard illuminant, $2^\circ$ standard observer) -> CIE $L^*a^*b^*$ conversion functions.
  - [x] Optimize with 256-entry lookup tables (LUTs) for gamma expansion and chromaticity adaptation.
  - [x] Batch transformation utilities for raw image buffers (`Uint8ClampedArray` to Float32 arrays).

- [x] **2.2 Perceptually Uniform Delta-E Metric Engine**
  - [x] Implement CIE $\Delta E^*_{76}$ Euclidean color distance:
    $$\Delta E^*_{76} = \sqrt{(\Delta L^*)^2 + (\Delta a^*)^2 + (\Delta b^*)^2}$$
  - [x] Implement advanced CIE $\Delta E_{00}$ (CIEDE2000) with lightness ($S_L$), chroma ($S_C$), hue ($S_H$), and rotation ($R_T$) weighting terms for maximum perceptual fidelity.
  - [x] Client throughput benchmarked at >500,000 distance evaluations per second.

- [x] **2.3 Spatial 3D KD-Tree in $L^*a^*b^*$ Space**
  - [x] Implement balanced 3D KD-Tree with splitting planes along $L^*$, $a^*$, and $b^*$ dimensions.
  - [x] $O(\log K)$ nearest-neighbor query retrieval across candidate library assets.
  - [x] Support custom rejection predicates for dynamic spatial repetition avoidance.

- [x] **2.4 Dynamic Repetition Penalty Matrix & Tracking Map**
  - [x] Implement Strict "No-Repeat" mode (each tile used at most once across the entire mosaic).
  - [x] Implement "Distance Repulsion" mode: dynamic coordinate penalty added to $\Delta E$ when a candidate tile has been used within radius $R$ of its prior placement.
  - [x] Implement soft fallback mechanism when tile candidate pool is exhausted to prevent mosaic holes.

- [x] **2.5 Dedicated Multi-Threaded Matching Web Worker**
  - [x] Package KD-Tree building, color space transformation, and nearest-neighbor matching loops into a background Web Worker (`photomosaic.worker.js`).
  - [x] Guarantee smooth 60fps main UI responsiveness during heavy calculations.

---

### 🎨 PHASE 3: Four Comprehensive Compute Modes Implementation

#### 🖼️ Mode 1: Advanced Photomosaic (CIE LAB Delta-E & No-Repeat Dynamics)
- [x] **3.1.1 Grid Segmentation & Color Feature Extraction**
  - [x] Configurable target density (from 25x25 up to 128x128 tiles).
  - [x] OffscreenCanvas cell average extraction into CIE $L^*a^*b^*$.
- [x] **3.1.2 Fast Nearest-Neighbor Matching Worker Pipeline**
  - [x] Client-side tile ingestion (local drag-and-drop or remote GCS bucket assets).
  - [x] Standardized tile generation (64x64 / 50x50 px) via OffscreenCanvas.
  - [x] Dynamic insertion into in-memory KD-Tree.
- [x] **3.1.3 Constraint Solver & Real-Time Tile Placement**
  - [x] Multi-pass matching with distance repulsion and usage frequency bounds.
  - [x] Progressive canvas rendering with live scanline/fly-in transitions.
  - [x] Alpha-tinting overlay slider (0% to 50%) blending original poster over tiles.

#### 🔤 Mode 2: High-Density ASCII / Unicode Matrix Engine
- [x] **3.2.1 Monospace Font Metric Calibrator**
  - [x] Strict monospace font metrics calculated via `measureText` on OffscreenCanvas to eliminate drift on deep zoom.
- [x] **3.2.2 Glyph Luminance & Edge Orientation Ramping**
  - [x] Support custom density character ramps: Standard 10-char, Extended 70-character grayscale, Binary, Matrix, Unicode density blocks.
  - [x] 3x3 Sobel filter edge detection pass to map dominant gradient angles ($\theta = \arctan(G_y / G_x)$) to directional glyphs (`|`, `-`, `/`, `\`, `+`).
- [x] **3.2.3 Color, Contrast & Inversion Modes**
  - [x] Monochromatic CRT modes: Phosphor Green (`#00ff66`), Amber CRT (`#ffb000`), Cyber Cyan (`#00f0ff`), High-Contrast B&W.
  - [x] Full Chromatic Matrix mode: Foreground character RGB sampling with background luminance thresholding.
  - [x] Retro CRT scanline effect overlay and plain-text export.

#### 📐 Mode 3: Geometric Voronoi Cellular Decomposition
- [x] **3.3.1 Sampling Strategy**
  - [x] Blue-noise / Poisson-disc distribution for organic stippling.
  - [x] Sobel filter edge-weighted feature detection pass to concentrate Voronoi seed points along high-frequency image edges and contours.
- [x] **3.3.2 Cell Construction & Triangulation**
  - [x] Real-time Bowyer-Watson Delaunay triangulation and dual Voronoi diagram generation.
  - [x] Per-cell color sampling: Area-weighted average or geometric centroid color sampling from source imagery.
  - [x] Lloyd's relaxation iterations for harmonious cell equalization.
- [x] **3.3.3 Dual Vector & Canvas Rendering**
  - [x] High-performance HTML5 Canvas rasterization + Scalable SVG vector export.
  - [x] Configurable cell borders (width, color, alpha).

#### 🔍 Mode 4: Deep-Zoom & Temporal Time-Lapse Reconstruction
- [x] **3.4.1 Deep-Zoom Navigation**
  - [x] Smooth pan-and-zoom from aggregate master portrait down to microscopic individual attendee selfies up to 1400% magnification.
  - [x] Interactive Tile Inspector: Clicking any tile reveals coordinates, $\Delta E$ score, and source thumbnail.
- [x] **3.4.2 Temporal Time-Lapse Reconstruction**
  - [x] Chronological reconstruction timeline tracking image arrival timestamps.
  - [x] Interactive timeline slider scrubbing mosaic progression from 0% to 100%.
  - [x] Media playback controls: Play, Pause, Speed Multiplier (0.5x, 1.0x, 2.5x, 5.0x), Step Forward/Back, Scrub.

---

### ☁️ PHASE 4: GCP Bucket Data Plane & Real-Time Event Workflow
- [x] **4.1 Standardized Bucket Topography & Schema**
  - [x] Documented and supported GCS bucket topography (`bucket-photo-library`, `bucket-target-posters`, `bucket-task-coordination`, `bucket-mosaic-outputs`).
  - [x] Implemented `TaskCoordinator` with manifest schema and task partitioner.
- [x] **4.2 Attendee Mobile Web Ingestion Client (PWA)**
  - [x] Mobile-first responsive QR-code accessible web application.
  - [x] Client-side image normalization, JPEG/WebP compression (max 1024px dimension), and EXIF metadata stripping.
  - [x] Direct HTTP PUT streaming straight to GCP Cloud Storage using Signed URLs or CORS bucket policies.
  - [x] Real-time upload progress, Contributor Token (`#T-XXXX`), and selfie confirmation badge.
- [x] **4.3 Event Display Big-Screen Kiosk Engine (Patch Polling & WebSocket-Free)**
  - [x] Zero-WebSocket polling architecture: Continuously polls `manifest.json` using conditional HTTP headers (`If-None-Match` / ETag, `Cache-Control: no-cache, max-age=2`).
  - [x] Incremental patch fetcher retrieving only newly appended patch JSON files.
  - [x] Dynamic animation subsystem with fly-in visual arrival transition.
  - [x] Spotlight zoom loop and "Find My Photo" coordinate locator.

---

### ⚡ PHASE 5: Frontend Experience, Admin Observability & Benchmarks
- [x] **5.1 Clean Two-Option Landing Page**
  - [x] Minimalist modern hero interface with Option 1: Live Event Arena and Option 2: Photo Mosaic Studio Workshop.
- [x] **5.2 Admin Observability & Benchmarking Lab (`/admin`)**
  - [x] Isolated telemetry dashboard simulating GCP Managed Instance Group (MIG) nodes (`c2-standard-4`).
  - [x] CPU saturation meter, cell processing throughput gauge, and GCS lock contention latency.
  - [x] Spot VM preemption simulation control demonstrating automatic lock recovery.
- [x] **5.3 Zero Residual Server Audit**
  - [x] Verified zero runtime backend servers (0 Python servers, 0 Node servers, 0 GPU instances).
  - [x] 100% static hosting bundle built and verified via Vite in `dist/`.
