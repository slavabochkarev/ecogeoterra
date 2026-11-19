import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from shapely import transform
from shapely.geometry import MultiPolygon, Point, box, mapping, shape

from app.cadastre import CadastreService
from app.cadastre_experiment import CadastreExperiment
from app.geo import MetricContext
from app.main import app
from app.nspd import NspdFailure, NspdGateway, SecureNspd

SEED = "63:09:0102159:753"
NEAR = "63:09:0102159:754"
SOURCE = box(49.2800, 53.5500, 49.2805, 53.5505)


def parcel(number, geometry, category=None, layer=36368):
    return {"type": "Feature", "geometry": None if geometry is None else mapping(geometry), "properties": {
        "category": layer, "options": {"cad_num": number, "land_record_category_type": category},
    }}


class CadastreExperimentTests(unittest.TestCase):
    def setUp(self):
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        gateway = Mock(spec=NspdGateway)
        source = parcel(SEED, SOURCE, "Земли населённых пунктов")
        gateway.by_number.return_value = [source]
        gateway.search_land.return_value = [source]
        self.service = CadastreService(gateway=gateway, cache_dir=cache.name)
        self.gateway = gateway
        self.source = source
        self.experiment = CadastreExperiment(self.service)
        server = patch("app.main.cadastre_experiment", self.experiment)
        server.start()
        self.addCleanup(server.stop)
        self.client = TestClient(app)

    def research(self):
        return self.client.post("/api/cadastre-experiment/research", json={"number": SEED})

    @patch("app.cadastre_experiment.time.sleep")
    def test_fixed_buffer_filters_exact_contours_and_uses_existing_cache(self, _):
        context = MetricContext(SOURCE)
        _, south, east, north = context.source.bounds

        def east_of_source(distance):
            y = (south + north) / 2
            geometry = box(east + distance, y - 1, east + distance + 1, y + 1)
            return transform(geometry, context.inverse.transform, interleaved=False)

        near = parcel(NEAR, east_of_source(498))
        self.gateway.search_land.return_value = [self.source, near, near,
                                                 parcel("63:09:0102159:755", east_of_source(501)),
                                                 parcel("63:09:0102159:756", None),
                                                 parcel("63:09:0102159:757", SOURCE, layer=36369)]
        found = self.client.get("/api/cadastre-experiment/source", params={"number": SEED})
        self.assertEqual(found.status_code, 200)
        result = self.research().json()
        self.assertEqual(result["coverage"], "unknown")
        self.assertEqual(result["radius_m"], 500)
        self.assertEqual(result["source"]["id"], SEED)
        self.assertEqual(result["source"]["properties"]["measurement"], {"relation": "source"})
        self.assertEqual([f["id"] for f in result["parcels"]["features"]], [NEAR])
        self.assertIsNone(result["parcels"]["features"][0]["properties"]["area_m2"])
        measurement = result["parcels"]["features"][0]["properties"]["measurement"]
        self.assertEqual(measurement["relation"], "disjoint")
        self.assertAlmostEqual(measurement["distance_m"], 498, delta=0.2)
        self.assertEqual([result["stats"][k] for k in ("received", "valid_geometry", "mapped", "with_category",
                                                      "without_category", "invalid_geometry", "invalid_record",
                                                      "outside_buffer", "duplicate_neighbors")], [6, 3, 2, 1, 1, 1, 1, 1, 1])
        again = self.research().json()
        self.assertFalse(result["cache_hit"])
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["parcels"]["features"][0]["properties"]["measurement"], measurement)
        self.gateway.by_number.assert_called_once_with(SEED, land_only=True)
        self.gateway.search_land.assert_called_once()

    def test_source_without_polygon_never_starts_spatial_search(self):
        self.gateway.by_number.return_value = [parcel(SEED, Point(49.28, 53.55))]
        response = self.research()
        self.assertEqual(response.status_code, 422)
        self.assertIn("площадной контур", response.json()["detail"])
        self.gateway.search_land.assert_not_called()

    def test_label_point_is_inside_a_multipolygon_with_a_hole_and_works_from_cache(self):
        outline = box(49.280, 53.550, 49.284, 53.554)
        ring = outline.difference(box(49.281, 53.551, 49.283, 53.553))
        geometry = MultiPolygon([ring, box(49.285, 53.550, 49.2851, 53.5501)])
        self.assertFalse(geometry.covers(geometry.centroid))
        self.gateway.by_number.return_value = [parcel(SEED, geometry)]
        params = {"number": SEED}

        for expected_cache in (False, True):
            response = self.client.get("/api/cadastre-experiment/source", params=params).json()
            self.assertEqual(response["cache_hit"], expected_cache)
            feature = response["parcel"]
            self.assertEqual(feature["properties"]["number"], SEED)
            self.assertTrue(geometry.contains(Point(feature["properties"]["label_point"])))
            self.assertTrue(shape(feature["geometry"]).equals(geometry))

        cached = self.service.read_cache("experiment-source-v1-63_09_0102159_753")
        self.assertNotIn("label_point", cached["parcel"]["properties"])
        self.gateway.by_number.assert_called_once()

    def test_running_operation_prevents_concurrent_duplicate(self):
        with self.service.lock:
            self.assertEqual(self.research().status_code, 409)
        self.gateway.search_land.assert_not_called()

    def test_research_rejects_client_selected_radius(self):
        response = self.client.post("/api/cadastre-experiment/research", json={"number": SEED, "radius": 2000})
        self.assertEqual(response.status_code, 422)
        self.gateway.by_number.assert_not_called()

    @patch("app.cadastre_experiment.time.sleep")
    def test_api_propagates_throttling_and_applies_cooldown(self, _):
        self.gateway.search_land.side_effect = NspdFailure("Ограничение НСПД", "http_429", 120)
        first = self.research()
        second = self.research()
        self.assertEqual(first.status_code, 429)
        self.assertEqual(first.headers["Retry-After"], "120")
        self.assertEqual(second.status_code, 429)
        self.gateway.search_land.assert_called_once()

    def test_gateway_uses_only_land_scope_and_does_not_retry_spatial_request(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"data": {"type": "FeatureCollection", "features": [self.source]},
                                                 "meta": [{"totalCount": 1, "categoryId": 36368}]})
            return httpx.Response(503, json={"message": "unavailable"})

        def client():
            return httpx.Client(base_url="https://nspd.gov.ru", transport=httpx.MockTransport(handler))

        with patch.object(SecureNspd, "_build_client", side_effect=client):
            gateway = NspdGateway()
            self.assertEqual(gateway.by_number(SEED, land_only=True)[0]["properties"]["category"], 36368)
            with self.assertRaisesRegex(NspdFailure, "HTTP 503"):
                gateway.search_land(SOURCE)

        self.assertEqual(len(requests), 2)
        self.assertEqual(dict(requests[0].url.params), {"query": SEED, "layersId": "36048"})
        self.assertEqual(json.loads(requests[1].content)["categories"], [{"id": 36368}])
