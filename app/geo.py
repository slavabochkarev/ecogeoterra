"""Metric measurements independent of transport and UI; source may later be a polygon."""
from math import atan2, degrees, hypot

from pyproj import CRS, Transformer
from shapely import transform
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

DIRECTIONS = ("С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ")


def direction(dx: float, dy: float) -> tuple[str | None, float | None]:
    if hypot(dx, dy) < 1e-6:
        return None, None  # Overlap: an arbitrary north direction would be misleading.
    bearing = degrees(atan2(dx, dy)) % 360
    return DIRECTIONS[int((bearing + 22.5) // 45) % 8], bearing


class MetricContext:
    def __init__(self, source: BaseGeometry):
        if source.is_empty or not source.is_valid:
            raise ValueError("Исходная геометрия пуста или некорректна")
        center = source.representative_point()
        self.crs = CRS.from_proj4(
            f"+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m +no_defs"
        )
        self.forward = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        self.inverse = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        self.source = transform(source, self.forward.transform, interleaved=False)

    def measure(self, target: BaseGeometry) -> dict:
        metric_target = transform(target, self.forward.transform, interleaved=False)
        start, end = nearest_points(self.source, metric_target)
        dx, dy = end.x - start.x, end.y - start.y
        compass, bearing = direction(dx, dy)
        lon, lat = self.inverse.transform(end.x, end.y)
        return {"distance_m": hypot(dx, dy), "direction": compass,
                "bearing_deg": bearing, "nearest_point": {"lat": lat, "lon": lon}}
