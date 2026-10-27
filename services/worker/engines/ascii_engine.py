"""
Mode 2: High-Density Structural ASCII & Glyph Art Engine (Task 3.3)
Applies 3x3 Sobel edge kernels to determine gradient orientation angles (theta = arctan(Gy / Gx))
and maps directional character glyphs alongside luminance density ramps.

Luminance is equalised with CLAHE (contrast-limited adaptive histogram equalisation) before glyph
selection, so midtones such as skin spread across the whole ramp instead of collapsing onto one
character. Directional glyphs (─ │ ╱ ╲) override the ramp on the strongest contours. Every cell
also carries the average colour of its patch of the source photo, for true-colour rendering.
"""

import math
import numpy as np
import cv2
from typing import Tuple, List, Dict, Any

DIRECTIONAL_GLYPHS = {
    "horizontal": "─",
    "vertical": "│",
    "diag_up": "╱",
    "diag_down": "╲",
}

LUMINANCE_RAMP_STANDARD = " .:-=+*#%@"
LUMINANCE_RAMP_EXTENDED = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"


class AsciiEngine:
    def __init__(self, char_ramp: str = LUMINANCE_RAMP_EXTENDED, edge_threshold: float = 35.0,
                 edge_fraction: float = 0.18, clahe_clip: float = 2.0):
        self.char_ramp = char_ramp
        self.edge_threshold = edge_threshold  # absolute floor: flat images get no edge glyphs
        self.edge_fraction = edge_fraction    # at most this share of cells become edge glyphs
        self.clahe_clip = clahe_clip

    def process_image(
        self,
        img_bgr: np.ndarray,
        cols: int = 100,
        char_w: int = 8,
        char_h: int = 16
    ) -> Dict[str, Any]:
        """
        Converts input image to ASCII matrix with Sobel directional edge orientation.
        Derived strictly from target aspect ratio (W / H) and font glyph bounding box (char_w, char_h):
        R = round(C * (H / W) * (char_w / char_h))
        """
        h, w = img_bgr.shape[:2]
        char_aspect = char_w / float(char_h)
        rows = max(4, int(round(cols * (h / float(w)) * char_aspect)))

        pixel_width = cols * char_w
        pixel_height = rows * char_h

        # Per-cell average colour of the source (INTER_AREA = exact area mean)
        resized_bgr = cv2.resize(img_bgr, (cols, rows), interpolation=cv2.INTER_AREA)
        # CLAHE on the full-resolution luminance, then averaged down to one value per cell
        full_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        tiles = (max(1, min(8, cols // 8)), max(1, min(8, rows // 8)))
        equalised = cv2.createCLAHE(clipLimit=self.clahe_clip, tileGridSize=tiles).apply(full_gray)
        gray = cv2.resize(equalised, (cols, rows), interpolation=cv2.INTER_AREA)

        # 3x3 Sobel edge gradient computation
        sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)

        magnitude = np.sqrt(sobel_x**2 + sobel_y**2)
        orientation = (np.degrees(np.arctan2(sobel_y, sobel_x)) + 180.0) % 180.0
        # Only the strongest contours become line glyphs (top edge_fraction of gradients)
        edge_cut = max(self.edge_threshold, float(np.percentile(magnitude, 100 * (1 - self.edge_fraction))))

        char_matrix = []
        ascii_lines = []

        for r in range(rows):
            line_chars = []
            row_data = []
            for c in range(cols):
                mag = magnitude[r, c]
                b, g, red = resized_bgr[r, c]
                lum = gray[r, c] / 255.0

                char = " "
                is_edge = False

                # Directional edge detection
                if mag > edge_cut:
                    ang = orientation[r, c]
                    is_edge = True
                    if (ang >= 67.5) and (ang < 112.5):
                        char = DIRECTIONAL_GLYPHS["horizontal"]
                    elif (ang >= 22.5) and (ang < 67.5):
                        char = DIRECTIONAL_GLYPHS["diag_up"]
                    elif (ang >= 112.5) and (ang < 157.5):
                        char = DIRECTIONAL_GLYPHS["diag_down"]
                    else:
                        char = DIRECTIONAL_GLYPHS["vertical"]
                else:
                    # Density ramp mapping
                    idx = int(lum * (len(self.char_ramp) - 1))
                    char = self.char_ramp[max(0, min(len(self.char_ramp) - 1, idx))]

                line_chars.append(char)
                row_data.append({
                    "char": char,
                    "r": int(red),
                    "g": int(g),
                    "b": int(b),
                    "is_edge": is_edge
                })

            ascii_lines.append("".join(line_chars))
            char_matrix.append(row_data)

        return {
            "columns": cols,
            "rows": rows,
            "pixel_width": pixel_width,
            "pixel_height": pixel_height,
            "char_w": char_w,
            "char_h": char_h,
            "ascii_text": "\n".join(ascii_lines),
            "matrix": char_matrix
        }
