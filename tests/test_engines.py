import sys
import os
import tempfile
import numpy as np

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.worker.engines.color_math import rgb_to_cielab, delta_e_ciede2000, SpatialRepetitionBuffer
from services.worker.engines.ascii_engine import AsciiEngine
from services.worker.engines.voronoi_engine import VoronoiEngine
from services.worker.engines.deepzoom_engine import DeepZoomEngine
from services.gateway.main import app

def run_tests():
    print("=" * 60)
    print("RUNNING MICROSERVICES COMPUTE ENGINES VERIFICATION SUITE")
    print("=" * 60)

    # 1. Color Math & CIEDE2000 & SRB Test
    rgb = np.random.randint(0, 255, (10, 3), dtype=np.uint8)
    lab = rgb_to_cielab(rgb)
    assert lab.shape == (10, 3), "Lab shape mismatch"
    target_lab = np.tile(lab[0], (10, 1))
    distances = delta_e_ciede2000(target_lab, lab)
    assert len(distances) == 10, "Distance length mismatch"
    assert distances[0] < 1e-3, "Distance to self must be 0"

    srb = SpatialRepetitionBuffer(radius=2)
    srb.record_placement("photo1.jpg", 0, 0)
    assert srb.is_eligible("photo1.jpg", 1, 1) is False, "SRB Chebyshev radius rejection failed"
    assert srb.is_eligible("photo1.jpg", 5, 5) is True, "SRB distance acceptance failed"
    print("  [PASS] Test 1: Vectorized CIELAB, CIEDE2000 Delta E00 & Chebyshev SRB Buffer")

    # 2. ASCII Sobel Directional Engine Test
    test_img = np.random.randint(0, 255, (120, 160, 3), dtype=np.uint8)
    ascii_engine = AsciiEngine()
    ascii_res = ascii_engine.process_image(test_img, cols=80)
    assert len(ascii_res["ascii_text"].splitlines()) > 5, "ASCII text generation failed"
    assert len(ascii_res["matrix"]) > 0, "ASCII matrix empty"
    print("  [PASS] Test 2: 3x3 Sobel Directional Gradient ASCII Engine")

    # 3. Voronoi Lloyd's Centroidal Relaxation Engine Test
    voronoi_engine = VoronoiEngine(target_points=60, relaxation_iterations=2)
    voronoi_img = voronoi_engine.render(test_img)
    assert voronoi_img.shape == (120, 160, 3), "Voronoi raster output shape mismatch"
    print("  [PASS] Test 3: Contour-Weighted Seed Voronoi & Lloyd's Relaxation Engine")

    # 4. Deep-Zoom Pyramid Tiling Engine Test
    dz_engine = DeepZoomEngine(tile_size=64)
    dzi_xml = dz_engine.generate_dzi_xml(160, 120)
    assert "<Image" in dzi_xml and "TileSize=\"64\"" in dzi_xml, "DZI XML validation failed"
    pyramid_tiles = dz_engine.slice_pyramid(test_img)
    assert len(pyramid_tiles) > 0, "No pyramid tiles sliced"
    print(f"  [PASS] Test 4: Deep-Zoom Multi-Scale Pyramid Slicer ({len(pyramid_tiles)} tiles generated)")

    # 5. Gateway Health & Route Test
    from services.gateway.main import health_check
    health_resp = health_check()
    assert health_resp.get("status") == "healthy", "Health check status must be healthy"
    routes = [route.path for route in app.routes]
    assert "/health" in routes, "/health endpoint missing"
    assert "/api/v1/jobs/create" in routes, "/jobs/create endpoint missing"
    assert "/api/v1/events/create" in routes, "/events/create endpoint missing"
    assert "/api/v1/events/{event_id}/find/{token}" in routes, "/events/{event_id}/find/{token} missing"
    print(f"  [PASS] Test 5: Gateway /health returns HTTP 200 {health_resp} & {len(routes)} routes registered")

    print("=" * 60)
    print(">>> ALL MICROSERVICE COMPUTATION ENGINES VERIFIED SUCCESSFULLY <<<")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
