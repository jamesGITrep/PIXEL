"""
Mode 4: Vector flow-field painter.
Sobel gradients of the greyscale image give an edge direction field; strokes follow the tangent
theta = atan2(Gy, Gx) + pi/2, so they run along edges rather than across them. Each particle is
integrated with RK2 (midpoint) steps and drawn as an anti-aliased polyline in the source colour
sampled at its seed, over a softened underpainting so the canvas has no bare gaps.
"""

import cv2
import numpy as np

STEP_LENGTH_DEFAULT = 15     # integration steps per stroke (L)
NUM_PARTICLES_DEFAULT = 5000
STROKE_WIDTH_DEFAULT = 2
STEP_SIZE_PX = 2.0           # d: distance moved per step


class FlowFieldEngine:
    def __init__(self, step_length: int = STEP_LENGTH_DEFAULT, num_particles: int = NUM_PARTICLES_DEFAULT,
                 stroke_width: int = STROKE_WIDTH_DEFAULT, seed: int = 0):
        self.step_length = int(min(80, max(2, step_length)))
        self.num_particles = int(min(50000, max(100, num_particles)))
        self.stroke_width = int(min(10, max(1, stroke_width)))
        self.seed = seed

    @staticmethod
    def tangent_field(img_bgr: np.ndarray) -> np.ndarray:
        """Per-pixel stroke angle: gradient direction + pi/2 (tangent to edges)."""
        gray = cv2.GaussianBlur(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 2.0)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        # Smooth orientations in the doubled-angle domain so opposite gradients (+/-) reinforce
        # instead of cancelling, then halve back: a stable field even in low-contrast areas.
        mag2 = gx * gx + gy * gy
        c2 = cv2.GaussianBlur((gx * gx - gy * gy), (0, 0), 3.0)
        s2 = cv2.GaussianBlur(2 * gx * gy, (0, 0), 3.0)
        theta = 0.5 * np.arctan2(s2, c2)
        # Where there is no structure at all, fall back to the raw per-pixel gradient angle
        flat = cv2.GaussianBlur(mag2, (0, 0), 3.0) < 1e-6
        theta[flat] = np.arctan2(gy, gx)[flat]
        return theta + np.pi / 2

    def trace(self, theta: np.ndarray, seeds: np.ndarray) -> np.ndarray:
        """RK2-integrates all particles at once; returns paths of shape (N, L+1, 2) as (x, y)."""
        h, w = theta.shape
        cos_t, sin_t = np.cos(theta), np.sin(theta)

        def direction(p, ref):
            xi = np.clip(np.rint(p[:, 0]).astype(int), 0, w - 1)
            yi = np.clip(np.rint(p[:, 1]).astype(int), 0, h - 1)
            v = np.stack([cos_t[yi, xi], sin_t[yi, xi]], axis=1)
            # The field is an orientation (theta and theta+pi are the same line): keep heading
            flip = (v * ref).sum(axis=1) < 0
            v[flip] *= -1
            return v

        d = STEP_SIZE_PX
        p = seeds.astype(np.float64)
        heading = direction(p, np.ones_like(p))
        paths = [p.copy()]
        for _ in range(self.step_length):
            k1 = direction(p, heading)
            k2 = direction(p + 0.5 * d * k1, k1)
            p = p + d * k2
            p[:, 0] = np.clip(p[:, 0], 0, w - 1)
            p[:, 1] = np.clip(p[:, 1], 0, h - 1)
            heading = k2
            paths.append(p.copy())
        return np.stack(paths, axis=1)

    def render(self, img_bgr: np.ndarray) -> np.ndarray:
        h, w = img_bgr.shape[:2]
        rng = np.random.default_rng(self.seed)
        theta = self.tangent_field(img_bgr)

        canvas = cv2.GaussianBlur(img_bgr, (0, 0), 3.0 + self.stroke_width)
        seeds = np.column_stack([rng.uniform(0, w - 1, self.num_particles),
                                 rng.uniform(0, h - 1, self.num_particles)])
        paths = self.trace(theta, seeds)
        colors = img_bgr[seeds[:, 1].astype(int), seeds[:, 0].astype(int)]

        # Fixed-point coordinates (shift=4) keep sub-pixel precision for anti-aliasing
        pts = np.rint(paths * 16).astype(np.int32)
        for path, color in zip(pts, colors):
            cv2.polylines(canvas, [path.reshape(-1, 1, 2)], False, tuple(int(c) for c in color),
                          thickness=self.stroke_width, lineType=cv2.LINE_AA, shift=4)
        return canvas
