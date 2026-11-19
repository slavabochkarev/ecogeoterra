import unittest

from shapely.geometry import Point

from app.classification import classify
from app.osm import GeometryError, element_geometry


def geometry(points):
    return [{"lon": x, "lat": y} for x, y in points]


class OSMTests(unittest.TestCase):
    def test_unknown_building_not_inferred_from_name(self):
        result = classify({"building": "yes", "name": "Жилой дом / больница"})
        self.assertEqual(result["category"], "building")
        self.assertIn("не указано", result["type"])
        self.assertIsNone(classify({"name": "Школа"}))

    def test_specific_function_precedes_building(self):
        result = classify({"building": "yes", "amenity": "school"})
        self.assertEqual(result["category"], "education")
        self.assertEqual(result["classification_basis"], "amenity=school")
        self.assertIsNone(classify({"building": "no"}))

    def test_multipolygon_split_rings_and_hole(self):
        raw = {"type": "relation", "tags": {"type": "multipolygon"}, "members": [
            {"type": "way", "role": "outer", "geometry": geometry([(0, 0), (4, 0), (4, 4)])},
            {"type": "way", "role": "outer", "geometry": geometry([(0, 0), (0, 4), (4, 4)])},
            {"type": "way", "role": "inner", "geometry": geometry([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)])},
        ]}
        result = element_geometry(raw)
        self.assertAlmostEqual(result.area, 12)
        self.assertFalse(result.covers(Point(2, 2)))
        self.assertTrue(result.covers(Point(.5, .5)))

    def test_incomplete_geometry_never_uses_center(self):
        with self.assertRaises(GeometryError):
            element_geometry({"type": "way", "center": {"lat": 53, "lon": 49}, "tags": {"building": "yes"}})
        with self.assertRaises(GeometryError):
            element_geometry({"type": "relation", "tags": {"type": "multipolygon"}, "members": [
                {"type": "way", "role": "outer", "geometry": geometry([(0, 0), (1, 1)])}]})

    def test_explicit_linear_area_is_preserved(self):
        raw = {"type": "way", "tags": {"building": "yes", "area": "no"},
               "geometry": geometry([(0, 0), (1, 0), (1, 1), (0, 0)])}
        self.assertEqual(element_geometry(raw).geom_type, "LineString")


if __name__ == "__main__":
    unittest.main()
