"""
Point-in-polygon geographic filter using ray casting.

The polygon is a list of (lat, lng) tuples. No external dependencies.
For Tel Aviv-scale polygons (a few km across), treating lat/lng as flat
Cartesian coordinates introduces error well under one meter — fine here.
"""

from config import INCLUSION_POLYGON


def point_in_polygon(lat: float, lng: float, polygon: list[tuple[float, float]]) -> bool:
    """
    Ray-casting point-in-polygon test.
    polygon is a list of (lat, lng) tuples; the polygon is auto-closed.
    Returns True if the point is strictly inside (boundary handling is unspecified
    but consistent — fine for our use).
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygon[i]
        lat_j, lng_j = polygon[j]
        # A horizontal ray at `lat` going east — count edge crossings
        intersects = ((lat_i > lat) != (lat_j > lat)) and (
            lng < (lng_j - lng_i) * (lat - lat_i) / (lat_j - lat_i) + lng_i
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def is_in_target_area(lat: float, lng: float) -> bool:
    """Convenience: check the configured INCLUSION_POLYGON."""
    return point_in_polygon(lat, lng, INCLUSION_POLYGON)
