"""
Mode 2: High-Density Structural ASCII & Glyph Art Engine (Task 3.3)
Applies 3x3 Sobel edge kernels to determine gradient orientation angles (theta = arctan(Gy / Gx))
and maps directional character glyphs alongside luminance density ramps.
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
    def __init__(self, char_ramp: str = LUMINANCE_RAMP_EXTENDED, edge_threshold: float = 35.0):
        self.char_ramp = char_ramp
        self.edge_threshold = edge_threshold

    def process_image(self, img_bgr: np.ndarray, cols: int = 100) -> Dict[str, Any]:
        """
        Converts input image to ASCII matrix with Sobel directional edge orientation.
        Returns:
            - ascii_text: Multi-line plain text string
            - char_matrix: 2D array of {char, r, g, b, is_edge}
        """
        h, w = img_bgr.shape[:2]
        char_aspect = 0.5  # Monospace fonts are roughly 1:2 aspect ratio
        rows = int(cols * (h / w) * char_aspect)
        rows = max(10, rows)

        # Resize image for grid analysis
        resized_bgr = cv2.resize(img_bgr, (cols, rows), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(resized_bgr, cv2.COLOR_BGR2GRAY)

        # 3x3 Sobel edge gradient computation
        sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)

        magnitude = np.sqrt(sobel_x**2 + sobel_y**2)
        orientation = (np.degrees(np.arctan2(sobel_y, sobel_x)) + 180.0) % 180.0

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
                if mag > self.edge_threshold:
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
            "ascii_text": "\n".join(ascii_lines),
            "matrix": char_matrix
        }
