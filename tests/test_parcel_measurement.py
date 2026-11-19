import unittest

from shapely import transform
from shapely.geometry import MultiPolygon, Point, Polygon, box

from app.cadastre_experiment import parcel_measurement
from app.geo import MetricContext


class ParcelMeasurementTests(unittest.TestCase):
    def check_gap(self, source, target, expected_m):
        context = MetricContext(source)
        result = parcel_measurement(source, context, target)
        self.assertEqual(result["relation"], "disjoint")
        self.assertAlmostEqual(result["distance_m"], expected_m, delta=0.25)

        projected_target = transform(target, context.forward.transform, interleaved=False)
        a, b = (Point(context.forward.transform(*p)) for p in result["endpoints"])
        self.assertAlmostEqual(a.distance(context.source.boundary), 0, places=6)
        self.assertAlmostEqual(b.distance(projected_target.boundary), 0, places=6)
        self.assertAlmostEqual(a.distance(b), result["distance_m"], places=6)
        self.assertGreater(context.source.centroid.distance(projected_target.centroid), result["distance_m"])
        return result

    def test_gap_uses_polygon_boundaries(self):
        self.check_gap(box(0, 0, .001, .001), box(.002, .0002, .003, .0006), 111.32)

    def test_inside_hole_measures_to_the_interior_ring(self):
        hole = box(.001, .001, .004, .004)
        source = box(0, 0, .005, .005).difference(hole)
        target = box(.0013, .002, .002, .003)
        result = self.check_gap(source, target, 33.40)
        start = Point(result["endpoints"][0])
        self.assertAlmostEqual(hole.boundary.distance(start), 0, places=9)

    def test_concave_polygon_uses_the_nearer_arm(self):
        source = box(0, 0, .004, .004).difference(box(.001, .001, .004, .003))
        target = box(.002, .0014, .003, .002)
        result = self.check_gap(source, target, 44.23)
        self.assertAlmostEqual(result["endpoints"][0][1], .001, places=9)

    def test_multipolygons_use_closest_pair_of_components(self):
        source = MultiPolygon([box(0, 0, .001, .001), box(.004, 0, .005, .001)])
        target = MultiPolygon([box(.0054, .0001, .0057, .0006), box(.008, 0, .009, .001)])
        result = self.check_gap(source, target, 44.53)
        self.assertAlmostEqual(result["endpoints"][0][0], .005, places=9)

    def test_shared_edge_and_vertex_are_touches(self):
        source = box(0, 0, .001, .001)
        context = MetricContext(source)
        for target in (box(.001, .0002, .002, .0008), box(.001, .001, .002, .002)):
            with self.subTest(target=target):
                self.assertEqual(parcel_measurement(source, context, target), {"relation": "touches", "distance_m": 0})

    def test_overlap_and_containment_are_intersections(self):
        source = box(0, 0, .001, .001)
        context = MetricContext(source)
        for target in (box(.0005, .0005, .0015, .0015), box(.0002, .0002, .0004, .0004)):
            with self.subTest(target=target):
                self.assertEqual(parcel_measurement(source, context, target), {"relation": "intersects", "distance_m": 0})

    def test_missing_invalid_and_non_polygonal_geometry_are_undefined(self):
        source = box(0, 0, .001, .001)
        context = MetricContext(source)
        bowtie = Polygon([(0, 0), (.001, .001), (.001, 0), (0, .001)])
        for target in (None, Point(1, 1), Polygon(), bowtie):
            with self.subTest(target=target):
                self.assertEqual(parcel_measurement(source, context, target), {"relation": "undefined"})
