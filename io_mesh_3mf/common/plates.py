"""Shared Orca/Bambu plate properties and grid geometry."""

import math
from typing import Tuple


PLATE_PROPERTY = "3mf_plate"
PLATE_NAME_PROPERTY = "3mf_plate_name"
ASSEMBLE_DELTA_PROPERTY = "3mf_assemble_transform"
MAX_PLATES = 36
PLATE_GAP = 1.0 / 5.0


def plate_column_count(plate_count: int) -> int:
    """Return Orca's grid-column count for the number of plates."""
    if plate_count <= 0:
        return 0
    value = math.sqrt(float(plate_count))
    rounded = round(value)
    return rounded + 1 if value > rounded else int(rounded)


def plate_origin(
    index: int, plate_count: int, bed_width: float, bed_depth: float
) -> Tuple[float, float]:
    """Return the zero-based plate's grid offset in millimeters."""
    cols = plate_column_count(plate_count)
    if cols <= 0 or index < 0:
        raise ValueError("plate index and count must be positive")
    row, col = divmod(index, cols)
    return (
        col * bed_width * (1.0 + PLATE_GAP),
        -row * bed_depth * (1.0 + PLATE_GAP),
    )


def printable_area_size(printable_area) -> Tuple[float, float]:
    """Return a printable area's bounding width and depth in millimeters."""
    if not isinstance(printable_area, (list, tuple)) or len(printable_area) < 3:
        raise ValueError("printable_area needs at least three points")
    try:
        points = [tuple(float(value) for value in point.split("x")) for point in printable_area]
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("printable_area contains an invalid point") from exc
    if any(len(point) != 2 or not all(math.isfinite(value) for value in point) for point in points):
        raise ValueError("printable_area contains an invalid point")
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    width, depth = max(xs) - min(xs), max(ys) - min(ys)
    if width <= 0 or depth <= 0:
        raise ValueError("printable_area must have positive width and depth")
    return width, depth
