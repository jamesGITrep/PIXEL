"""
Mode 1: CIE L*a*b* & CIEDE2000 (Delta E 00) Mathematical Color Engine (Task 3.2)
Pure CPU-optimized vector math with NumPy and SciPy.
"""

import math
import numpy as np
from typing import Tuple, List, Dict, Any, Optional

# D65 Reference White
XN = 0.95047
YN = 1.00000
ZN = 1.08883

EPSILON = 216.0 / 24389.0  # (6/29)^3
KAPPA = 24389.0 / 27.0     # (29/3)^3


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    """Converts 8-bit sRGB [0-255] to linear RGB [0-1]."""
    c = rgb / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_cielab(rgb: np.ndarray) -> np.ndarray:
    """
    Vectorized batch conversion from sRGB (N, 3) to CIE L*a*b* (N, 3).
    L* in [0, 100], a* in [-128, 127], b* in [-128, 127]
    """
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.ndim == 1:
        rgb = rgb.reshape(1, 3)
    
    lin_rgb = srgb_to_linear(rgb)

    # Linear RGB to XYZ (D65)
    r = lin_rgb[:, 0]
    g = lin_rgb[:, 1]
    b = lin_rgb[:, 2]

    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / XN
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / YN
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / ZN

    # Non-linear XYZ to Lab transform
    fx = np.where(x > EPSILON, np.cbrt(x), (KAPPA * x + 16.0) / 116.0)
    fy = np.where(y > EPSILON, np.cbrt(y), (KAPPA * y + 16.0) / 116.0)
    fz = np.where(z > EPSILON, np.cbrt(z), (KAPPA * z + 16.0) / 116.0)

    L = np.maximum(0.0, 116.0 * fy - 16.0)
    a = 500.0 * (fx - fy)
    b_val = 200.0 * (fy - fz)

    return np.column_stack([L, a, b_val])


def delta_e_cie76(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Euclidean distance in CIE L*a*b* space."""
    return np.sqrt(np.sum((lab1 - lab2) ** 2, axis=-1))


def delta_e_ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """
    CIEDE2000 (Delta E 00) perceptual color difference implementation.
    Standardized by CIE for human visual perception accuracy.
    Supports single or vectorized (N, 3) pairwise evaluations.
    """
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    if lab1.ndim == 1:
        lab1 = lab1.reshape(1, 3)
    if lab2.ndim == 1:
        lab2 = lab2.reshape(1, 3)

    L1, a1, b1 = lab1[:, 0], lab1[:, 1], lab1[:, 2]
    L2, a2, b2 = lab2[:, 0], lab2[:, 1], lab2[:, 2]

    # Mean L
    L_bar = (L1 + L2) / 2.0

    # Chroma
    C1 = np.sqrt(a1**2 + b1**2)
    C2 = np.sqrt(a2**2 + b2**2)
    C_bar = (C1 + C2) / 2.0

    # a' correction factor G
    G = 0.5 * (1.0 - np.sqrt((C_bar**7) / (C_bar**7 + 25.0**7 + 1e-9)))
    a1_prime = (1.0 + G) * a1
    a2_prime = (1.0 + G) * a2

    C1_prime = np.sqrt(a1_prime**2 + b1**2)
    C2_prime = np.sqrt(a2_prime**2 + b2**2)
    C_bar_prime = (C1_prime + C2_prime) / 2.0

    # Hue angles in degrees
    h1_prime = np.degrees(np.arctan2(b1, a1_prime)) % 360.0
    h2_prime = np.degrees(np.arctan2(b2, a2_prime)) % 360.0

    # Delta h'
    diff_h = h2_prime - h1_prime
    abs_diff_h = np.abs(diff_h)

    delta_h_prime = np.where(
        (C1_prime == 0) | (C2_prime == 0),
        0.0,
        np.where(
            abs_diff_h <= 180.0,
            diff_h,
            np.where(h2_prime <= h1_prime, diff_h + 360.0, diff_h - 360.0)
        )
    )

    delta_L_prime = L2 - L1
    delta_C_prime = C2_prime - C1_prime
    delta_H_prime = 2.0 * np.sqrt(C1_prime * C2_prime) * np.sin(np.radians(delta_h_prime / 2.0))

    # Mean Hue angle H_bar_prime
    sum_h = h1_prime + h2_prime
    H_bar_prime = np.where(
        (C1_prime == 0) | (C2_prime == 0),
        sum_h,
        np.where(
            abs_diff_h <= 180.0,
            sum_h / 2.0,
            np.where(sum_h < 360.0, (sum_h + 360.0) / 2.0, (sum_h - 360.0) / 2.0)
        )
    )

    # Weighting functions: S_L, S_C, S_H
    T = (
        1.0
        - 0.17 * np.cos(np.radians(H_bar_prime - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * H_bar_prime))
        + 0.32 * np.cos(np.radians(3.0 * H_bar_prime + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * H_bar_prime - 63.0))
    )

    S_L = 1.0 + (0.015 * (L_bar - 50.0)**2) / np.sqrt(20.0 + (L_bar - 50.0)**2)
    S_C = 1.0 + 0.045 * C_bar_prime
    S_H = 1.0 + 0.015 * C_bar_prime * T

    # Rotation factor R_T
    delta_theta = 30.0 * np.exp(-(((H_bar_prime - 275.0) / 25.0)**2))
    R_C = 2.0 * np.sqrt((C_bar_prime**7) / (C_bar_prime**7 + 25.0**7 + 1e-9))
    R_T = -np.sin(np.radians(2.0 * delta_theta)) * R_C

    # Total CIEDE2000
    term_L = delta_L_prime / S_L
    term_C = delta_C_prime / S_C
    term_H = delta_H_prime / S_H

    dE2000 = np.sqrt(term_L**2 + term_C**2 + term_H**2 + R_T * term_C * term_H)
    return dE2000


class SpatialRepetitionBuffer:
    """
    Enforces Chebyshev / Euclidean spatial No-Repeat radius R constraints.
    Prevents repetitive photo clustering by checking coordinate proximity.
    """
    def __init__(self, radius: int = 5):
        self.radius = max(0, radius)
        # asset_id -> list of (col, row)
        self.placements: Dict[str, List[Tuple[int, int]]] = {}

    def is_eligible(self, asset_id: str, col: int, row: int) -> bool:
        if self.radius <= 0:
            return True
        if asset_id not in self.placements:
            return True
        for (pc, pr) in self.placements[asset_id]:
            # Chebyshev distance
            if max(abs(col - pc), abs(row - pr)) < self.radius:
                return False
        return True

    def record_placement(self, asset_id: str, col: int, row: int) -> None:
        if asset_id not in self.placements:
            self.placements[asset_id] = []
        self.placements[asset_id].append((col, row))
