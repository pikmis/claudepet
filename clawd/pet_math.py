"""Tiny helpers shared by the pet and the games."""
from __future__ import annotations


def clamp(v: float, lo: float, hi: float) -> float:
    if hi < lo or v < lo:
        return lo
    return hi if v > hi else v
