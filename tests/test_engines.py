"""
Unit tests for the worker render engines (no services needed):

    python -m pytest tests/test_engines.py -v
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.worker.engines.color_math import rgb_to_cielab, delta_e_ciede2000, SpatialRepetitionBuffer
from services.worker.engines.ascii_engine import AsciiEngine
from services.worker.engines.kuwahara_engine import KuwaharaEngine
from services.worker.engines.flowfield_engine import FlowFieldEngine


def reference_kuwahara(img, r):
    """Direct per-pixel implementation of the quadrant rule, used to check the vectorised one."""
    p = np.pad(img.astype(np.float64), ((r, r), (r, r), (0, 0)), mode="reflect")
    h, w = img.shape[:2]
    out = np.zeros_like(img, dtype=np.float64)
    for y in range(h):
        for x in range(w):
            py, px = y + r, x + r
            quads = [p[py - r:py + 1, px - r:px + 1], p[py - r:py + 1, px:px + r + 1],
                     p[py:py + r + 1, px - r:px + 1], p[py:py + r + 1, px:px + r + 1]]
            best_var, best_mu = None, None
            for q in quads:
                q = q.reshape(-1, 3)
                mu = q.mean(axis=0)
                var = ((q - mu) ** 2).sum(axis=1).mean()
                if best_var is None or var < best_var:
                    best_var, best_mu = var, mu
            out[y, x] = best_mu
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


class ColorMathTests(unittest.TestCase):
    def test_ciede2000_and_repetition_buffer(self):
        rgb = np.random.default_rng(0).integers(0, 255, (10, 3)).astype(np.uint8)
        lab = rgb_to_cielab(rgb)
        self.assertEqual(lab.shape, (10, 3))
        self.assertLess(delta_e_ciede2000(np.tile(lab[0], (10, 1)), lab)[0], 1e-3)
        srb = SpatialRepetitionBuffer(radius=2)
        srb.record_placement("photo1.jpg", 0, 0)
        self.assertFalse(srb.is_eligible("photo1.jpg", 1, 1))
        self.assertTrue(srb.is_eligible("photo1.jpg", 5, 5))


class AsciiTests(unittest.TestCase):
    def test_ascii_grid(self):
        img = np.random.default_rng(1).integers(0, 255, (120, 160, 3)).astype(np.uint8)
        res = AsciiEngine().process_image(img, cols=80)
        self.assertEqual(res["columns"], 80)
        self.assertEqual(len(res["ascii_text"].splitlines()), res["rows"])

    def test_clahe_spreads_low_contrast_midtones_over_the_ramp(self):
        # A soft, skin-like midtone range (luminance 120..150) used to collapse onto a few glyphs
        yy, xx = np.mgrid[0:300, 0:400]
        lum = 120 + 30 * (0.5 + 0.5 * np.sin(xx / 23.0) * np.cos(yy / 31.0))
        img = np.dstack([lum * 0.85, lum * 0.95, lum * 1.15]).clip(0, 255).astype(np.uint8)  # BGR
        res = AsciiEngine().process_image(img, cols=100)
        chars = [cell["char"] for row in res["matrix"] for cell in row if not cell["is_edge"]]
        counts = np.unique(chars, return_counts=True)[1]
        self.assertGreaterEqual(len(counts), 20)                 # many distinct density glyphs
        self.assertLess(counts.max() / counts.sum(), 0.2)        # no single glyph dominates

    def test_edge_glyphs_follow_contours(self):
        img = np.zeros((200, 200, 3), np.uint8)
        img[:, 100:] = 255                                       # one vertical edge
        res = AsciiEngine().process_image(img, cols=50)
        edge_cells = [(c, cell["char"]) for row in res["matrix"] for c, cell in enumerate(row) if cell["is_edge"]]
        self.assertTrue(edge_cells)
        self.assertTrue(all(ch == "│" and 22 <= c <= 27 for c, ch in edge_cells))
        flat = np.full((200, 200, 3), 90, np.uint8)
        self.assertFalse(any(cell["is_edge"] for row in AsciiEngine().process_image(flat, cols=50)["matrix"] for cell in row))

    def test_cells_carry_source_colour(self):
        img = np.zeros((160, 160, 3), np.uint8)
        img[:, :80] = (30, 60, 200)    # BGR: red-ish left half
        img[:, 80:] = (200, 160, 20)   # BGR: blue-ish right half
        m = AsciiEngine().process_image(img, cols=20)["matrix"]
        left, right = m[len(m) // 2][2], m[len(m) // 2][17]
        self.assertEqual((left["r"], left["g"], left["b"]), (200, 60, 30))
        self.assertEqual((right["r"], right["g"], right["b"]), (20, 160, 200))


class KuwaharaTests(unittest.TestCase):
    def test_matches_reference_implementation(self):
        img = np.random.default_rng(2).integers(0, 255, (23, 31, 3)).astype(np.uint8)
        for r in (2, 4):
            with self.subTest(radius=r):
                np.testing.assert_array_equal(KuwaharaEngine(radius=r).render(img), reference_kuwahara(img, r))

    def test_radius_clamped_and_shape_kept(self):
        img = np.zeros((40, 60, 3), np.uint8)
        self.assertEqual(KuwaharaEngine(radius=1).radius, 2)
        self.assertEqual(KuwaharaEngine(radius=20).radius, 8)
        self.assertEqual(KuwaharaEngine().radius, 4)
        self.assertEqual(KuwaharaEngine().render(img).shape, img.shape)

    def test_4k_frame_under_half_a_second(self):
        import time
        import cv2
        img = cv2.resize(np.random.default_rng(5).integers(0, 255, (54, 96, 3)).astype(np.uint8), (3840, 2160),
                         interpolation=cv2.INTER_CUBIC)
        eng = KuwaharaEngine(radius=4)
        eng.render(img[:64, :64])  # warm-up
        t = time.perf_counter()
        out = eng.render(img)
        self.assertEqual(out.shape, img.shape)
        self.assertLess(time.perf_counter() - t, 0.5)

    def test_flat_image_unchanged(self):
        img = np.full((30, 30, 3), (10, 120, 200), np.uint8)
        np.testing.assert_array_equal(KuwaharaEngine(radius=5).render(img), img)

    def test_preserves_hard_edge_and_smooths_noise(self):
        rng = np.random.default_rng(3)
        img = np.zeros((60, 60, 3), np.float64)
        img[:, 30:] = 200
        noisy = np.clip(img + rng.normal(0, 20, img.shape), 0, 255).astype(np.uint8)
        out = KuwaharaEngine(radius=4).render(noisy).astype(np.float64)
        # Edge stays sharp: columns either side of the boundary keep their own side's level
        self.assertLess(out[:, 26:29].mean(), 40)
        self.assertGreater(out[:, 31:34].mean(), 160)
        # Noise is reduced inside each half
        self.assertLess(out[:, 5:25].std(), noisy[:, 5:25].astype(np.float64).std() * 0.6)


class FlowFieldTests(unittest.TestCase):
    def test_tangent_field_runs_along_edges(self):
        # Vertical stripes: the gradient is horizontal, so strokes must run vertically
        img = np.zeros((80, 80, 3), np.uint8)
        img[:, ::16] = 255
        img[:, 1::16] = 255
        theta = FlowFieldEngine.tangent_field(img)[10:-10, 10:-10]
        self.assertGreater(np.abs(np.sin(theta)).mean(), 0.95)

    def test_streamlines_follow_field(self):
        eng = FlowFieldEngine(step_length=10, num_particles=100)
        theta = np.zeros((50, 100))                       # field pointing along +x everywhere
        seeds = np.array([[10.0, 25.0], [20.0, 5.0]])
        paths = eng.trace(theta, seeds)
        self.assertEqual(paths.shape, (2, 11, 2))
        np.testing.assert_allclose(paths[:, -1, 1], seeds[:, 1])          # y unchanged
        np.testing.assert_allclose(np.abs(paths[:, -1, 0] - seeds[:, 0]), 20.0)  # L * d

    def test_streamlines_stay_on_canvas(self):
        eng = FlowFieldEngine(step_length=60, num_particles=100)
        theta = np.full((30, 40), np.pi / 4)
        paths = eng.trace(theta, np.array([[39.0, 29.0], [0.0, 0.0]]))
        self.assertTrue((paths[..., 0] >= 0).all() and (paths[..., 0] <= 39).all())
        self.assertTrue((paths[..., 1] >= 0).all() and (paths[..., 1] <= 29).all())

    def test_render_uses_source_colours_and_is_deterministic(self):
        img = np.zeros((120, 160, 3), np.uint8)
        img[:60] = (30, 60, 220)
        img[60:] = (40, 200, 40)
        a = FlowFieldEngine(num_particles=1500, seed=7).render(img)
        b = FlowFieldEngine(num_particles=1500, seed=7).render(img)
        self.assertEqual(a.shape, img.shape)
        np.testing.assert_array_equal(a, b)
        # Away from the boundary each half keeps its own colour
        np.testing.assert_allclose(a[10:40].reshape(-1, 3).mean(0), (30, 60, 220), atol=12)
        np.testing.assert_allclose(a[80:110].reshape(-1, 3).mean(0), (40, 200, 40), atol=12)

    def test_parameters_clamped(self):
        eng = FlowFieldEngine(step_length=0, num_particles=10, stroke_width=99)
        self.assertEqual((eng.step_length, eng.num_particles, eng.stroke_width), (2, 100, 10))
        d = FlowFieldEngine()
        self.assertEqual((d.step_length, d.num_particles, d.stroke_width), (15, 5000, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
