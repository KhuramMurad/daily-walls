"""Aspect-preserving preview geometry, independent of GTK."""
from __future__ import annotations


def image_placement(image_width: int, image_height: int, box_width: int,
                    box_height: int, *, fill: bool = False) -> tuple[float, float, float]:
    """Return scale and centered offsets; fit keeps all image edges visible."""
    if min(image_width, image_height, box_width, box_height) <= 0:
        return 0.0, 0.0, 0.0
    ratios = (box_width / image_width, box_height / image_height)
    scale = max(ratios) if fill else min(ratios)
    return scale, (box_width - image_width * scale) / 2, (box_height - image_height * scale) / 2
