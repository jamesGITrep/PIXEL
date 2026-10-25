"""
Mode 4: Deep-Zoom Image (DZI) Pyramid Slicer & Time-Lapse Slicer (Task 3.5)
Recursively downsamples master outputs into multi-scale 256x256 image pyramids
compatible with OpenSeadragon and generates progressive assembly animation frames.
"""

import os
import io
import math
import cv2
import numpy as np
from PIL import Image
from typing import Dict, Any, List


class DeepZoomEngine:
    def __init__(self, tile_size: int = 256, overlap: int = 1, fmt: str = "jpg"):
        self.tile_size = tile_size
        self.overlap = overlap
        self.fmt = fmt

    def generate_dzi_xml(self, width: int, height: int) -> str:
        """Generates OpenSeadragon DZI XML descriptor."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Image xmlns="http://schemas.microsoft.com/deepzoom/2008"
  Format="{self.fmt}"
  Overlap="{self.overlap}"
  TileSize="{self.tile_size}">
  <Size Width="{width}" Height="{height}"/>
</Image>"""

    def slice_pyramid(self, img_bgr: np.ndarray) -> Dict[str, bytes]:
        """
        Recursively slices image into pyramid levels.
        Returns dict of {relative_path: image_bytes}.
        """
        h, w = img_bgr.shape[:2]
        max_dim = max(w, h)
        num_levels = int(math.ceil(math.log2(max_dim))) + 1

        pyramid_tiles = {}

        # Top level down to 0
        current_img = img_bgr.copy()
        
        for level in range(num_levels - 1, -1, -1):
            cur_h, cur_w = current_img.shape[:2]
            cols = int(math.ceil(cur_w / self.tile_size))
            rows = int(math.ceil(cur_h / self.tile_size))

            for r in range(rows):
                for c in range(cols):
                    # Coordinates with overlap
                    x = max(0, c * self.tile_size - (self.overlap if c > 0 else 0))
                    y = max(0, r * self.tile_size - (self.overlap if r > 0 else 0))
                    x2 = min(cur_w, (c + 1) * self.tile_size + self.overlap)
                    y2 = min(cur_h, (r + 1) * self.tile_size + self.overlap)

                    tile = current_img[y:y2, x:x2]
                    _, buf = cv2.imencode(".jpg", tile, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    tile_path = f"{level}/{c}_{r}.{self.fmt}"
                    pyramid_tiles[tile_path] = buf.tobytes()

            # Downsample by 2 for next level
            if level > 0:
                next_w = max(1, cur_w // 2)
                next_h = max(1, cur_h // 2)
                current_img = cv2.resize(current_img, (next_w, next_h), interpolation=cv2.INTER_AREA)

        return pyramid_tiles
