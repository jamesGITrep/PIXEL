"""
Mode 3: Kuwahara oil-paint filter.
For every pixel, four overlapping (R+1)x(R+1) quadrant windows (top-left, top-right,
bottom-left, bottom-right) are evaluated; the pixel takes the RGB mean of the quadrant with the
lowest colour variance. Flat regions become paint-like patches while edges stay sharp.

Fully vectorised with OpenCV: one float32 box filter gives every window's mean (mu), one gives
the mean of squares (mu2), and sigma^2 = mu2 - |mu|^2 is computed once for all windows. The four
quadrants of a pixel are that same map read at four offsets, so choosing the calmest quadrant is
an elementwise min over four slices, and one remap gathers the winning means.
Cost is O(pixels) whatever the radius.
"""

import cv2
import numpy as np

RADIUS_MIN, RADIUS_MAX, RADIUS_DEFAULT = 2, 8, 4
_SUM_CHANNELS = np.ones((1, 3), dtype=np.float32)


class KuwaharaEngine:
    def __init__(self, radius: int = RADIUS_DEFAULT):
        self.radius = int(min(RADIUS_MAX, max(RADIUS_MIN, radius)))

    def render(self, img_bgr: np.ndarray) -> np.ndarray:
        r = self.radius
        k = r + 1
        h, w = img_bgr.shape[:2]
        # Pad while still uint8 (cheap), then centre around 0 in one pass so float32
        # mu2 - mu^2 keeps its precision in flat areas
        padded_u8 = cv2.copyMakeBorder(img_bgr, r, r, r, r, cv2.BORDER_REFLECT_101)
        padded = np.subtract(padded_u8, np.float32(128.0), dtype=np.float32)

        # anchor (0, 0): window [y, y+k) x [x, x+k) of the padded image, i.e. down-right of (y, x)
        box = dict(ddepth=-1, ksize=(k, k), anchor=(0, 0), normalize=True, borderType=cv2.BORDER_REFLECT_101)
        mean = cv2.boxFilter(padded, **box)
        mean_sq = cv2.boxFilter(cv2.transform(cv2.multiply(padded, padded), _SUM_CHANNELS), **box)
        var = cv2.subtract(mean_sq, cv2.transform(cv2.multiply(mean, mean), _SUM_CHANNELS))

        # Quadrants of pixel (y, x) = windows anchored at these offsets (Q1 TL, Q2 TR, Q3 BL, Q4 BR)
        offsets = ((0, 0), (0, r), (r, 0), (r, r))
        best = var[0:h, 0:w].copy()
        pick = np.zeros((h, w), dtype=np.uint8)
        for i, (dy, dx) in enumerate(offsets[1:], start=1):
            v = var[dy:dy + h, dx:dx + w]
            better = v < best  # strict: ties keep the earlier quadrant, like argmin
            pick[better] = i
            np.minimum(best, v, out=best)

        # Gather the chosen quadrant's mean for every pixel in one nearest-neighbour remap.
        # Quadrant index -> (dy, dx) via lookup tables; the base grid is broadcast, not materialised.
        lut_x = np.zeros((256, 1), np.uint8)
        lut_y = np.zeros((256, 1), np.uint8)
        for i, (oy, ox) in enumerate(offsets):
            lut_y[i], lut_x[i] = oy, ox
        map_x = np.add(np.arange(w, dtype=np.float32)[None, :], cv2.LUT(pick, lut_x), dtype=np.float32)
        map_y = np.add(np.arange(h, dtype=np.float32)[:, None], cv2.LUT(pick, lut_y), dtype=np.float32)
        out = cv2.remap(mean, map_x, map_y, interpolation=cv2.INTER_NEAREST)
        return cv2.convertScaleAbs(out, alpha=1.0, beta=128.0)
