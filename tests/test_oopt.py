import json
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely.geometry import Point, mapping, shape

from app import main
from app.geo import MetricContext
from app.nspd import LAND_INTERSECTS_PATH, NUMBER_SEARCH_PATH, NspdFailure, NspdGateway, OOPT_CATEGORY
from app.oopt import OOPT_REGISTRY_CATEGORY, zone_attributes
from app.research_schema import WorkingState


POINT = {"lat": 53.093542, "lon": 49.891426}
RD010 = Path(__file__).resolve().parents[1] / "docs/research/geo-rd-010/nspd-oopt.json"


def encoded_feature(number, geometry, **options):
    return {"type": "Feature", "properties": {"category": OOPT_CATEGORY,
            "options": {"reg_numb_border": number, **options}}, "geometry": geometry}


def dataset(features, **kwargs):
    return {"type": "FeatureCollection", "features": features, **kwargs}


class OoptTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.gateway = NspdGateway()
        cadastre = SimpleNamespace(gateway=self.gateway, lock=Lock(), next_allowed=0)
        switch = patch.object(main, "cadastre", cadastre)
        switch.start()
        self.addCleanup(switch.stop)
        self.addCleanup(self.client.close)
        self.cadastre = cadastre

    def run_search(self, diameter=40, **point):
        self.cadastre.next_allowed = 0
        return self.client.post("/api/oopt/research", json={**POINT, **point, "diameter_km": diameter})

    @unittest.skipUnless(RD010.is_file(), "Локальная выборка GEO-RD-010 не установлена")
    def test_saved_circles(self):
        saved = json.loads(RD010.read_text(encoding="utf-8"))
        for radius_km, expected in ((5, 0), (20, 11), (50, 38), (100, 123)):
            with self.subTest(radius=radius_km):
                numbers = set(saved["radius_results"][str(radius_km)]["registry_numbers"])
                source = 50 if radius_km <= 50 else 100
                features = [encoded_feature(row["registry_number"], next(
                    variant["geometry"] for variant in row["geometries"] if source in variant["observed_in_km"]
                )) for row in saved["objects"] if row["registry_number"] in numbers]
                with patch.object(self.gateway, "_bounded_json", return_value=dataset(features)):
                    result = self.run_search(radius_km * 2)
                self.assertEqual(result.status_code, 200, result.text[:500])
                data = result.json()
                self.assertEqual(data["coverage"], "unknown")
                self.assertEqual(data["legal_status"], "unverified")
                self.assertEqual(data["radius_m"], radius_km * 1000)
                self.assertEqual(len(data["zones"]), expected)
                self.assertEqual({zone["registry_number"] for zone in data["zones"]}, numbers)
                self.assertEqual([zone["distance_m"] for zone in data["zones"]],
                                 sorted(zone["distance_m"] for zone in data["zones"]))
                if expected:
                    self.assertAlmostEqual(data["zones"][0]["distance_m"], 5512.98, places=2)
                    self.assertTrue(all(zone["name"] is None for zone in data["zones"]))
                    self.assertTrue(all(shape(zone["geometry"]).is_valid for zone in data["zones"]))

    def test_request_diameters_and_empty_collection(self):
        for radius_km in (5, 20, 50, 100):
            with self.subTest(radius=radius_km):
                with patch.object(self.gateway, "_bounded_json", return_value=dataset([])) as http:
                    result = self.run_search(radius_km * 2)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["radius_m"], radius_km * 1000)
                self.assertEqual(result.json()["coverage"], "unknown")
                self.assertEqual(result.json()["zones"], [])
                http.assert_called_once()
                self.assertEqual(http.call_args.args[:2], ("post", LAND_INTERSECTS_PATH))
                kwargs = http.call_args.kwargs
                self.assertEqual(kwargs["params"], {"typeIntersect": "fullObject"})
                sent = json.loads(kwargs["content"])
                self.assertEqual(sent["categories"], [{"id": 472825}])
                feature = sent["geom"]["features"][0]
                self.assertEqual(feature["properties"], {})
                coordinates = feature["geometry"]["coordinates"][0]
                self.assertEqual(feature["geometry"]["crs"]["properties"]["name"], "EPSG:4326")
                self.assertEqual(len(coordinates), 361)
                centre = MetricContext(Point(POINT["lon"], POINT["lat"]))
                for coordinate in coordinates:
                    self.assertAlmostEqual(Point(centre.forward.transform(*coordinate)).distance(Point(0, 0)),
                                           radius_km * 1000, places=4)

    def test_inside_duplicate_and_invalid_contour(self):
        project = Transformer.from_crs(4326, 3857, always_xy=True)
        x, y = project.transform(POINT["lon"], POINT["lat"])
        contour = {**mapping(Point(x, y).buffer(2000)),
                   "crs": {"type": "name", "properties": {"name": "EPSG:3857"}}}
        feature = encoded_feature("63:00-9.9", contour, name="Зона из ответа")
        malformed = encoded_feature("63:00-9.8", {**contour, "crs": None})
        with patch.object(self.gateway, "_bounded_json", return_value=dataset([feature, feature, malformed])):
            response = self.run_search()
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["coverage"], "partial")
        self.assertEqual(len(data["zones"]), 1)
        zone = data["zones"][0]
        self.assertTrue(zone["inside"])
        self.assertEqual(zone["distance_m"], 0)
        self.assertGreater(zone["boundary_distance_m"], 1000)
        self.assertEqual(zone["name"], "Зона из ответа")

    def test_invalid_input_upstream_error_and_truncation(self):
        with patch.object(self.gateway, "_bounded_json") as http:
            self.assertEqual(self.run_search(20).status_code, 422)
            self.assertEqual(self.run_search(lat=91).status_code, 422)
            http.assert_not_called()
            for code, status in (("http_403", 403), ("http_429", 429),
                                 ("tls_verification_failed", 503), ("deadline_exceeded", 504)):
                with self.subTest(code=code):
                    http.side_effect = NspdFailure("Тестовый ответ НСПД", code, 23 if status == 429 else None)
                    result = self.run_search()
                    self.assertEqual(result.status_code, status)
                    self.assertEqual(result.json()["detail"]["code"], code)
                    if status == 429:
                        self.assertEqual(result.headers["Retry-After"], "23")
            http.side_effect = None
            http.return_value = dataset([], numberMatched=10)
            result = self.run_search()
            self.assertEqual(result.status_code, 502)
            self.assertEqual(result.json()["coverage"], "partial")

    def test_circle_crosses_antimeridian_in_one_request(self):
        with patch.object(self.gateway, "_bounded_json", return_value=dataset([])) as http:
            response = self.run_search(200, lon=179.9)
        self.assertEqual(response.status_code, 200)
        raw = json.loads(http.call_args.kwargs["content"])["geom"]["features"][0]["geometry"]
        geom = shape(raw)
        self.assertEqual(geom.geom_type, "MultiPolygon")
        self.assertTrue(geom.is_valid)
        self.assertEqual(len(geom.geoms), 2)
        self.assertTrue(all(part.bounds[2] - part.bounds[0] < 5 for part in geom.geoms))

    def test_tabs_and_compatible_saved_state(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        order = [page.text.index(f'id="{name}-tab"') for name in ("cadastre", "objects", "oopt", "coordinates")]
        self.assertEqual(order, sorted(order))
        self.assertIsNone(WorkingState.model_validate({"tab": "coordinates"}).oopt_anchor)
        saved = WorkingState.model_validate({"tab": "oopt", "oopt_anchor": "cadastre"})
        self.assertEqual(saved.model_dump()["oopt_anchor"], "cadastre")

    def test_spatial_name_is_only_taken_from_explicit_name_fields(self):
        number = "63:00-9.2"
        properties = {"label": number, "descr": number, "categoryName": "ПКК. ООПТ",
                      "systemInfo": {"updated": "2026-10-22T17:56:48.310577", "updatedBy": "internal"}}
        attrs = zone_attributes(properties, {"reg_numb_border": number, "name": number}, number)
        self.assertEqual(attrs, {"layer_name": "ПКК. ООПТ", "updated_at": "2026-10-22T17:56:48.310577"})
        named = zone_attributes(properties, {"name_by_doc": "Национальный парк «Самарская Лука»"}, number)
        self.assertEqual(named["name"], "Национальный парк «Самарская Лука»")

    def test_card_joins_only_matching_registry_record_and_requests_once(self):
        number = "63:09-6.96"
        sparse = encoded_feature(number, None, name="Не использовать пространственную копию")
        wrong_number = encoded_feature("63:09-6.97", None, name_by_doc="Другой номер")
        wrong_number["properties"]["category"] = OOPT_REGISTRY_CATEGORY
        record = encoded_feature(number, None, name_by_doc="Памятник природы «Ставропольский сосняк»",
                                 type_boundary_value="Граница особо охраняемой природной территории",
                                 type_zone="", cadastral_district="63:09", registration_date="2013-04-19",
                                 legal_act_document_date="29.12.2012", legal_act_document_number="838",
                                 legal_act_document_issuer="Правительство Самарской области",
                                 content_restrict_encumbrances="Режим особой охраны")
        record["properties"]["category"] = OOPT_REGISTRY_CATEGORY
        raw = {"data": dataset([sparse, wrong_number, record]),
               "meta": [{"totalCount": 1}, {"totalCount": 2}]}
        with patch.object(self.gateway, "_bounded_json", return_value=raw) as http:
            response = self.client.get("/api/oopt/details", params={"number": number})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        result = response.json()
        self.assertEqual(result["registry_number"], number)
        attrs = result["details"]
        self.assertEqual(attrs["name"], "Памятник природы «Ставропольский сосняк»")
        self.assertEqual(attrs["registration_date"], "2013-04-19")
        self.assertEqual(attrs["cadastral_district"], "63:09")
        self.assertEqual(attrs["document"]["date"], "2012-12-29")
        self.assertEqual(attrs["document"]["number"], "838")
        self.assertEqual(attrs["restrictions"], "Режим особой охраны")
        self.assertNotIn("zone_type", attrs)
        self.assertNotIn("geometry", attrs)
        http.assert_called_once()
        self.assertEqual(http.call_args.args[:2], ("get", NUMBER_SEARCH_PATH))
        self.assertEqual(http.call_args.kwargs["params"], {"query": number, "thematicSearchId": 5})

    def test_card_absent_invalid_and_rate_limited(self):
        number = "63:00-9.2"
        feature = encoded_feature(number, None)
        with patch.object(self.gateway, "_bounded_json") as http:
            self.assertEqual(self.client.get("/api/oopt/details", params={"number": "63:09:8"}).status_code, 422)
            http.assert_not_called()
            http.return_value = {"data": dataset([feature]), "meta": [{"totalCount": 1}]}
            response = self.client.get("/api/oopt/details", params={"number": number})
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()["details"])
            self.cadastre.next_allowed = 0
            http.side_effect = NspdFailure("Лимит внешнего сервиса", "http_429", 23)
            response = self.client.get("/api/oopt/details", params={"number": number})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["Retry-After"], "23")
            followup = self.client.get("/api/oopt/details", params={"number": number})
            self.assertEqual(followup.status_code, 429)
            self.assertEqual(http.call_count, 2)
