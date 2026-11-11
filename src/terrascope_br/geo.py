"""Outlines on the braille grid. Rings are lon/lat polygons kept as numpy
arrays with a bounding box, so rings outside the view are skipped cheaply. A
ring is drawn once for each wrap shift (-360, 0, +360 degrees) at which it falls
inside the view, so the outlines show on both sides of the antimeridian."""

from __future__ import annotations

import numpy as np
from PIL import ImageDraw

from terrascope.core.view import WRAP_SHIFTS, View

class Ring:
    __slots__ = ("points", "min_lon", "min_lat", "max_lon", "max_lat")

    def __init__(self, coordinates) -> None:
        self.points = np.asarray(coordinates, dtype=np.float64)
        self.min_lon, self.min_lat = self.points.min(axis=0)
        self.max_lon, self.max_lat = self.points.max(axis=0)

    def touches(self, view: View, shift: float = 0.0) -> bool:
        """True when the ring, moved by `shift` degrees of longitude, overlaps the view."""
        west = view.origin_lon
        east = view.origin_lon + view.lon_span
        north = view.origin_lat
        south = view.origin_lat - view.lat_span
        return not (self.max_lon + shift < west or self.min_lon + shift > east
                    or self.max_lat < south or self.min_lat > north)

def rings_from_geojson(payload: dict) -> list[Ring]:
    """Every ring (outer and holes) of a FeatureCollection of Polygon /
    MultiPolygon geometries. Invalid features are skipped."""
    rings: list[Ring] = []
    for feature in payload.get("features", []):
        geometry = (feature or {}).get("geometry") or {}
        kind = geometry.get("type")
        coordinates = geometry.get("coordinates") or []
        polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
        for polygon in polygons:
            for ring in polygon:
                if len(ring) >= 2:
                    rings.append(Ring(ring))
    return rings

def draw_outlines(rings: list[Ring], view: View) -> list[str]:
    """Braille lines of the rings that show in the view, wrapped where needed."""
    image = view.new_image()
    draw = ImageDraw.Draw(image)
    scale_x, scale_y = view.scale_x, view.scale_y
    for ring in rings:
        for shift in WRAP_SHIFTS:
            if not ring.touches(view, shift):
                continue
            points = ring.points
            xs = (points[:, 0] + shift - view.origin_lon) * scale_x
            ys = (view.origin_lat - points[:, 1]) * scale_y
            pixels = list(zip(xs.tolist(), ys.tolist()))
            if len(pixels) >= 2:
                draw.line(pixels, fill=255, width=1)
    return view.to_lines(image)

def view_key(view: View) -> tuple:
    return (round(view.origin_lon, 6), round(view.origin_lat, 6),
            round(view.lon_span, 6), round(view.lat_span, 6), view.width, view.height)
