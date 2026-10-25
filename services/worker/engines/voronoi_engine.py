"""
Mode 3: Geometric Voronoi & Stained Glass Painting Engine (Task 3.4)
Implements edge-weighted seed point distribution along image contours,
Delaunay triangulation, iterative Lloyd's centroidal relaxation, and polygon rendering.
"""

import numpy as np
import cv2
from scipy.spatial import Voronoi, Delaunay
from typing import List, Tuple, Dict, Any


class VoronoiEngine:
    def __init__(self, target_points: int = 1200, relaxation_iterations: int = 5):
        self.target_points = target_points
        self.relaxation_iterations = relaxation_iterations

    def generate_seed_points(self, img_bgr: np.ndarray) -> np.ndarray:
        """Concentrates seed points along high-frequency image edges using Canny/Sobel."""
        h, w = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        
        # Edge magnitude detection
        edges = cv2.Canny(gray, 50, 150)
        edge_weight = cv2.GaussianBlur(edges.astype(np.float32), (5, 5), 0)
        edge_weight /= (edge_weight.max() + 1e-6)

        points = []
        # Frame boundary anchors to keep Voronoi cells bounded
        points.extend([
            [0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1],
            [w // 2, 0], [w // 2, h - 1], [0, h // 2], [w - 1, h // 2]
        ])

        # Hybrid sampling: uniform random + edge-weighted acceptance
        attempts = 0
        max_attempts = self.target_points * 6
        while len(points) < self.target_points and attempts < max_attempts:
            attempts += 1
            x = np.random.randint(0, w)
            y = np.random.randint(0, h)
            prob = 0.2 + 0.8 * edge_weight[y, x]
            if np.random.rand() < prob:
                points.append([x, y])

        return np.array(points, dtype=np.float64)

    def lloyd_relaxation(self, points: np.ndarray, width: int, height: int) -> np.ndarray:
        """Performs Lloyd's centroidal relaxation iterations to smooth cell distributions."""
        pts = points.copy()
        for _ in range(self.relaxation_iterations):
            try:
                vor = Voronoi(pts)
                new_pts = []
                for idx, region_idx in enumerate(vor.point_region):
                    region = vor.regions[region_idx]
                    if -1 in region or len(region) == 0:
                        new_pts.append(pts[idx])
                        continue
                    # Compute polygon centroid
                    poly = np.array([vor.vertices[i] for i in region])
                    cx = np.clip(np.mean(poly[:, 0]), 0, width - 1)
                    cy = np.clip(np.mean(poly[:, 1]), 0, height - 1)
                    new_pts.append([cx, cy])
                pts = np.array(new_pts, dtype=np.float64)
            except Exception:
                break
        return pts

    def render(self, img_bgr: np.ndarray, border_width: int = 1) -> np.ndarray:
        """Renders stained glass Voronoi canvas with dark leaded seams."""
        h, w = img_bgr.shape[:2]
        points = self.generate_seed_points(img_bgr)
        points = self.lloyd_relaxation(points, w, h)

        vor = Voronoi(points)
        canvas = np.zeros_like(img_bgr)

        for idx, region_idx in enumerate(vor.point_region):
            region = vor.regions[region_idx]
            if -1 in region or len(region) == 0:
                continue

            poly = np.array([vor.vertices[i] for i in region], dtype=np.int32)
            poly[:, 0] = np.clip(poly[:, 0], 0, w - 1)
            poly[:, 1] = np.clip(poly[:, 1], 0, h - 1)

            # Sample color from geometric centroid
            seed_x = int(np.clip(points[idx][0], 0, w - 1))
            seed_y = int(np.clip(points[idx][1], 0, h - 1))
            color = [int(c) for c in img_bgr[seed_y, seed_x]]

            cv2.fillPoly(canvas, [poly], color)
            if border_width > 0:
                cv2.polylines(canvas, [poly], isClosed=True, color=(20, 20, 20), thickness=border_width)

        return canvas
