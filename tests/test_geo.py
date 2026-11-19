"""Synthetic geometries are used only in tests, never as application data."""
import unittest

from pyproj import Geod
from shapely import transform
from shapely.geometry import LineString, Point, Polygon

from app.geo import MetricContext, direction


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.origin = Point(49.4, 53.5)
        self.context = MetricContext(self.origin)

    def from_meters(self, geometry):
        return transform(geometry, self.context.inverse.transform, interleaved=False)

    def test_all_eight_directions_and_geodesic_distances(self):
        geod = Geod(ellps="WGS84")
        for azimuth, expected in enumerate(("С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ")):
            with self.subTest(direction=expected):
                lon, lat, _ = geod.fwd(self.origin.x, self.origin.y, azimuth * 45, 1000)
                result = self.context.measure(Point(lon, lat))
                self.assertAlmostEqual(result["distance_m"], 1000, delta=.005)
                self.assertEqual(result["direction"], expected)

    def test_nearest_edge_not_centroid_or_vertex(self):
        target = self.from_meters(Polygon([(100, -500), (900, -500), (900, 500), (100, 500)]))
        result = self.context.measure(target)
        self.assertAlmostEqual(result["distance_m"], 100, delta=.001)
        self.assertEqual(result["direction"], "В")
        nearest = self.context.forward.transform(result["nearest_point"]["lon"], result["nearest_point"]["lat"])
        self.assertAlmostEqual(nearest[0], 100, delta=.001)
        self.assertAlmostEqual(nearest[1], 0, delta=.001)

    def test_source_inside_polygon_is_zero_without_direction(self):
        target = self.from_meters(Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)]))
        result = self.context.measure(target)
        self.assertAlmostEqual(result["distance_m"], 0, delta=.001)
        self.assertIsNone(result["direction"])
        self.assertIsNone(result["bearing_deg"])

    def test_polygon_hole_is_not_filled(self):
        target = self.from_meters(Polygon([(-500, -500), (500, -500), (500, 500), (-500, 500)],
                                         [[(-100, -100), (100, -100), (100, 100), (-100, 100)]]))
        self.assertAlmostEqual(self.context.measure(target)["distance_m"], 100, delta=.001)

    def test_nearest_point_on_line(self):
        target = self.from_meters(LineString([(-500, 250), (500, 250)]))
        result = self.context.measure(target)
        self.assertAlmostEqual(result["distance_m"], 250, delta=.001)
        self.assertEqual(result["direction"], "С")

    def test_future_polygon_source_uses_boundary(self):
        source = self.from_meters(Polygon([(-50, -50), (50, -50), (50, 50), (-50, 50)]))
        target = self.from_meters(Point(200, 0))
        result = MetricContext(source).measure(target)
        self.assertAlmostEqual(result["distance_m"], 150, delta=.02)
        self.assertEqual(result["direction"], "В")

    def test_sector_boundary_and_wrap(self):
        from math import radians, sin, cos
        for angle, expected in [(22.499, "С"), (22.501, "СВ"), (337.499, "СЗ"), (337.501, "С")]:
            self.assertEqual(direction(sin(radians(angle)), cos(radians(angle)))[0], expected)
        self.assertEqual(direction(0, 0), (None, None))

    def test_high_latitude_and_southern_hemisphere(self):
        for lat, lon in [(80, 25), (-33, 151), (0, 179.99)]:
            end_lon, end_lat, _ = Geod(ellps="WGS84").fwd(lon, lat, 90, 2000)
            result = MetricContext(Point(lon, lat)).measure(Point(end_lon, end_lat))
            self.assertAlmostEqual(result["distance_m"], 2000, delta=.01)
            self.assertEqual(result["direction"], "В")


if __name__ == "__main__":
    unittest.main()
