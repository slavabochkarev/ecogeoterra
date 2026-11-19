"""Deterministic NSPD fixtures; no Internet access in the unit suite."""
import copy
import json
import os
import ssl
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pyproj import Transformer
from shapely import transform
from shapely.geometry import Point, box, mapping, shape

from app.cadastre import CadastreService, EnrichInput, candidate, wgs84_geometry
from app.main import app
from app.nspd import (NspdFailure, NspdGateway, SecureNspd, collection,
                      NSPD_ROOT_CA, NSPD_ROOT_SHA256, nspd_tls_context)

POLYGON = box(49.28, 53.55, 49.281, 53.551)


def fixture(category=36368, number="63:09:0102159:753", geometry=POLYGON):
    options = {"cad_num": number, "status": "Учтенный", "cost_value": 0}
    if category == 36368:
        options.update(land_record_category_type="Земли населенных пунктов", area=100,
                       permitted_use_established_by_document="Для производственной деятельности")
    else:
        options.update(purpose="Нежилое", building_name="Корпус", build_record_area=80, floors="2")
    return {"type": "Feature", "id": 1, "geometry": mapping(geometry) if geometry is not None else None,
            "properties": {"category": category, "options": options}}


def payload(geometry=POLYGON, **extra):
    return EnrichInput(osm_type="way", osm_id=135674075, geometry=mapping(geometry) if geometry is not None else None,
                       **extra)


class CadastreTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.gateway = Mock()
        self.service = CadastreService(self.gateway, Path(self.folder.name), ttl=86400)

    def enrich(self, features, request=None):
        self.service.next_allowed = 0
        self.gateway.search.return_value = (features, "polygon_intersects", [])
        return self.service.enrich(request or payload())

    def test_land_success_missing_values_and_zero_cost(self):
        result = self.enrich([fixture()])
        self.assertEqual(result["status"], "matched")
        item = result["candidates"][0]
        self.assertEqual(result["selected_candidate"], item["id"])
        self.assertEqual(item["characteristics"]["permitted_use"], "Для производственной деятельности")
        self.assertIsNone(item["characteristics"]["address"])
        self.assertEqual(item["characteristics"]["cadastral_cost_rub"], 0)
        self.assertAlmostEqual(item["intersection"]["osm_fraction"], 1, places=6)
        self.assertIsNotNone(result["retrieved_at"])

    def test_oks_never_inherits_land_use(self):
        building = fixture(36369, "63:09:0102159:754")
        building["properties"]["options"]["permitted_use_established_by_document"] = "Чужой ВРИ"
        result = self.enrich([building])
        fields = result["candidates"][0]["characteristics"]
        self.assertEqual(fields["purpose"], "Нежилое")
        self.assertEqual(fields["floors"], "2")
        self.assertIsNone(fields["year_built"])
        self.assertNotIn("permitted_use", fields)
        self.assertNotIn("land_category", fields)

    def test_structures_and_unfinished_objects_keep_distinct_fields(self):
        for cat in (36383, 36384):
            f = fixture(cat, str(cat))
            f["properties"]["options"] = {"cad_number" if cat == 36383 else "cad_num": str(cat),
                "params_name": "Сооружение", "params_area": 5, "params_purpose": "Производственное"}
            item = candidate(f, POLYGON)
            self.assertEqual(item["category_id"], cat)
            self.assertNotIn("permitted_use", item["characteristics"])

    def test_empty_collection_is_not_found(self):
        result = self.enrich([])
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["message"], "Кадастровые сведения не найдены")

    def test_multiple_candidates_no_automatic_first_choice(self):
        result = self.enrich([fixture(), fixture(36369, "63:09:0102159:754")])
        self.assertEqual(result["status"], "multiple_matches")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertIsNone(result["selected_candidate"])

    def test_partial_overlap_reports_ratio_without_ownership_claim(self):
        target = box(49.2805, 53.55, 49.2815, 53.551)
        item = candidate(fixture(geometry=target), POLYGON)
        self.assertAlmostEqual(item["intersection"]["osm_fraction"], .5, places=4)
        self.assertIn("не подтверждение", item["match_basis"])
        self.assertIn("aeqd", item["intersection"]["metric_crs"])

    def test_unrelated_and_hole_geometries_are_not_matches(self):
        self.assertIsNone(candidate(fixture(geometry=box(50, 54, 50.1, 54.1)), POLYGON))
        hole = box(49, 53, 50, 54).difference(POLYGON.buffer(.001))
        self.assertIsNone(candidate(fixture(geometry=hole), POLYGON))

    def test_point_checks_actual_coverage(self):
        point = Point(49.2805, 53.5505)
        self.assertEqual(candidate(fixture(), point)["spatial_relation"], "point_covered")
        self.assertIsNone(candidate(fixture(), Point(40, 50)))

    def test_missing_candidate_geometry_requires_user_confirmation(self):
        result = self.enrich([fixture(geometry=None)])
        self.assertEqual(result["status"], "multiple_matches")
        self.assertIsNone(result["selected_candidate"])
        self.assertEqual(result["candidates"][0]["spatial_relation"], "unverified")

    def test_candidate_represented_by_point_is_not_lost_or_auto_selected(self):
        result = self.enrich([fixture(geometry=POLYGON.representative_point())])
        self.assertEqual(result["status"], "multiple_matches")
        self.assertIsNone(result["selected_candidate"])
        self.assertEqual(result["candidates"][0]["spatial_relation"], "geometry_intersection")
        self.assertIsNone(result["candidates"][0]["intersection"])

    def test_null_crs_is_default_geojson_and_feature_crs_is_respected(self):
        f = copy.deepcopy(fixture())
        f["geometry"]["crs"] = None
        self.assertEqual(candidate(f, POLYGON)["source_crs"], "EPSG:4326 (GeoJSON)")
        converter = Transformer.from_crs(4326, 3857, always_xy=True)
        f["geometry"] = mapping(transform(POLYGON, converter.transform, interleaved=False))
        f["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}
        self.assertEqual(candidate(f, POLYGON)["source_crs"], "EPSG:3857")

    def test_invalid_ca_configuration_is_unavailable(self):
        self.gateway.search.side_effect = FileNotFoundError("missing CA")
        self.assertEqual(self.service.enrich(payload())["error_code"], "tls_configuration")

    def test_invalid_upstream_geometry_and_missing_fields_are_errors(self):
        for mutate in (
            lambda f: f.update(geometry={"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}),
            lambda f: f["properties"].update(options={}),
            lambda f: f["geometry"].update(crs={"type": "name", "properties": {"name": "EPSG:999999"}}),
            lambda f: f["properties"]["options"].update(area=float("nan")),
        ):
            f = copy.deepcopy(fixture())
            mutate(f)
            self.assertEqual(self.enrich([f])["status"], "error")
        self.assertEqual(list(Path(self.folder.name).glob("*.json")), [])

    def test_crs_3857_conversion_longitude_first(self):
        converter = Transformer.from_crs(4326, 3857, always_xy=True)
        projected = transform(POLYGON, converter.transform, interleaved=False)
        raw = {**mapping(projected), "crs": {"type": "name", "properties": {"name": "EPSG:3857"}}}
        normalized = wgs84_geometry(raw)
        self.assertLess(normalized.hausdorff_distance(POLYGON), 1e-10)
        f = fixture()
        f["geometry"] = raw
        item = candidate(f, POLYGON)
        self.assertEqual(item["source_crs"], "EPSG:3857")
        self.assertLess(shape(item["geometry"]).hausdorff_distance(POLYGON), 1e-10)
        self.assertNotIn("crs", item["geometry"])

    def test_projected_coordinates_without_crs_are_rejected(self):
        with self.assertRaises(ValueError):
            wgs84_geometry({"type": "Point", "coordinates": [5_000_000, 7_000_000]})

    def test_metric_matching_uses_local_crs_across_russia(self):
        for x, y in ((30, 60), (131, 43), (170, 68)):
            polygon = box(x, y, x + .001, y + .001)
            item = candidate(fixture(geometry=polygon), polygon)
            self.assertAlmostEqual(item["intersection"]["osm_fraction"], 1)
            self.assertIn(f"lon_0={x + .0005}", item["intersection"]["metric_crs"])

    def test_disk_cache_restart_ttl_geometry_and_tags_key(self):
        first = self.enrich([fixture()])
        restarted = CadastreService(self.gateway, Path(self.folder.name), ttl=86400)
        result = restarted.enrich(payload())
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["retrieved_at"], first["retrieved_at"])
        self.assertEqual(self.gateway.search.call_count, 1)
        self.assertNotEqual(self.service.key(payload()), self.service.key(payload(box(49, 53, 49.1, 53.1))))
        self.assertNotEqual(self.service.key(payload()), self.service.key(payload(tags={"building": "yes"})))
        expired = CadastreService(self.gateway, Path(self.folder.name), ttl=0)
        self.assertFalse(expired.enrich(payload())["cache_hit"])
        self.assertEqual(self.gateway.search.call_count, 2)

    def test_corrupt_cache_is_ignored(self):
        self.enrich([fixture()])
        next(Path(self.folder.name).glob("*.json")).write_text("broken")
        self.assertFalse(self.enrich([fixture()])["cache_hit"])
        self.assertEqual(self.gateway.search.call_count, 2)

    def test_errors_are_not_cached_and_429_honors_cooldown(self):
        for code in ("http_403", "http_429", "connection_error"):
            self.service.next_allowed = 0
            self.gateway.search.side_effect = NspdFailure("Ошибка НСПД", code, 120 if code == "http_429" else None)
            result = self.service.enrich(payload())
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["retrieved_at"])
            self.assertEqual(result["error_code"], code)
            self.assertEqual(list(Path(self.folder.name).glob("*.json")), [])
            if code == "http_429":
                self.assertEqual(self.service.enrich(payload())["error_code"], "cooldown")

    def test_concurrent_request_returns_busy_without_extra_upstream_call(self):
        entered, release = threading.Event(), threading.Event()
        def blocking(_):
            entered.set()
            release.wait(3)
            return [], "polygon_intersects", []
        self.gateway.search.side_effect = blocking
        thread = threading.Thread(target=lambda: self.service.enrich(payload()))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.service.enrich(payload())["error_code"], "busy")
            self.assertEqual(self.gateway.search.call_count, 1)
        finally:
            release.set()
            thread.join(3)

    def test_linear_geometry_and_large_contour_do_not_call_nspd(self):
        from shapely.geometry import LineString
        for geom, error in ((LineString([(49, 53), (49.1, 53)]), "unsupported_geometry"),
                            (box(49, 53, 50, 54), "contour_too_large")):
            result = self.service.enrich(payload(geom))
            self.assertEqual(result["error_code"], error)
        self.gateway.search.assert_not_called()

    def test_validation_missing_invalid_geometry_and_non_finite_coordinates(self):
        for extra in ({}, {"coordinates": [49, 95]}, {"coordinates": [float("nan"), 53]},
                      {"geometry": {"type": "Point", "coordinates": [float("inf"), 53]}},
                      {"geometry": {"type": "Polygon", "coordinates": []}, "coordinates": [49, 53]}):
            with self.assertRaises(ValidationError):
                EnrichInput(osm_type="way", osm_id=1, **extra)
        self.assertIsNotNone(payload(None, coordinates=[49, 53]))

    def test_route_validation_and_response_schema(self):
        client = TestClient(app)
        with patch("app.main.cadastre", self.service):
            self.gateway.search.return_value = ([fixture()], "polygon_intersects", [])
            result = client.post("/api/cadastre/enrich", json=payload().model_dump())
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["status"], "matched")
            for body in ({"osm_type": "way", "osm_id": 1}, {"osm_type": "other", "osm_id": 1, "coordinates": [49, 53]}):
                self.assertEqual(client.post("/api/cadastre/enrich", json=body).status_code, 422)


class GatewayTests(unittest.TestCase):
    def test_official_bundled_ca_fingerprint_and_effective_trust(self):
        import hashlib
        der = ssl.PEM_cert_to_DER_cert(NSPD_ROOT_CA.read_text(encoding="ascii"))
        self.assertEqual(hashlib.sha256(der).hexdigest(), NSPD_ROOT_SHA256)
        context = nspd_tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIn(der, context.get_ca_certs(binary_form=True))

    def test_changed_bundled_ca_fails_closed(self):
        with patch("app.nspd.NSPD_ROOT_SHA256", "0" * 64):
            with self.assertRaises(ssl.SSLError):
                nspd_tls_context()

    def test_explicit_ca_override_uses_system_context_without_bundled_root(self):
        context = Mock()
        with patch("app.nspd.ssl.create_default_context", return_value=context):
            self.assertIs(nspd_tls_context("explicit.pem"), context)
        context.load_verify_locations.assert_called_once_with(cafile="explicit.pem")

    def run_gateway(self, handler, action):
        network = httpx.Client(base_url="https://nspd.gov.ru", transport=httpx.MockTransport(handler))
        with patch.object(SecureNspd, "_build_client", return_value=network), patch("app.nspd.time.sleep"):
            return action(NspdGateway())

    def test_effective_transport_tls_is_verified_and_env_cannot_override(self):
        with patch.dict(os.environ, {"PYNSPD_CLIENT_RETRY_ON_BLOCKED_IP": "true", "PYNSPD_CLIENT_RETRIES": "100", "PYNSPD_CLIENT_DNS_RESOLVE": "true"}):
            nspd = SecureNspd()
            client = nspd._build_client()
            try:
                context = client._transport._pool._ssl_context
                self.assertIs(context, nspd.tls_context)
                self.assertTrue(context.check_hostname)
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertFalse(nspd._retry_on_blocked_ip)
                self.assertFalse(nspd._dns_resolve)
                self.assertEqual(nspd._retries, 0)
            finally:
                client.close()

    def test_polygon_request_has_all_categories_holes_and_4326(self):
        requests = []
        polygon = POLYGON.difference(box(49.2802, 53.5502, 49.2804, 53.5504))
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [fixture()]})
        result = self.run_gateway(handler, lambda gateway: gateway.search(polygon))
        self.assertEqual(result[1], "polygon_intersects")
        self.assertEqual(len(requests), 1)
        req = requests[0]
        self.assertEqual(req.url.path, "/api/geoportal/v1/intersects")
        body = json.loads(req.content)
        self.assertEqual([c["id"] for c in body["categories"]], [36368, 36369, 36383, 36384])
        geometry = body["geom"]["features"][0]["geometry"]
        self.assertEqual(len(geometry["coordinates"]), 2)
        self.assertEqual(geometry["crs"]["properties"]["name"], "EPSG:4326")

    def test_point_query_four_layers_axis_order_and_no_implicit_first(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        self.run_gateway(handler, lambda gateway: gateway.search(Point(49, 53)))
        self.assertEqual(len(requests), 4)
        params = requests[0].url.params
        south, west, north, east = map(float, params["BBOX"].split(","))
        self.assertLess(south, 53)
        self.assertGreater(north, 53)
        self.assertLess(west, 49)
        self.assertGreater(east, 49)
        self.assertEqual(params["I"], params["J"])

    def test_http_403_404_429_never_become_empty_and_do_not_retry(self):
        for code in (403, 404, 429):
            count = []
            def handler(request):
                count.append(request)
                return httpx.Response(code, json={"message": "test"}, headers={"Retry-After": "120"})
            with self.assertRaises(NspdFailure) as raised:
                self.run_gateway(handler, lambda gateway: gateway.search(POLYGON))
            self.assertEqual(raised.exception.code, f"http_{code}")
            self.assertEqual(len(count), 1)
            if code == 429:
                self.assertEqual(raised.exception.retry_after, 120)

    def test_temporary_errors_have_only_one_retry(self):
        for code in (500, 503):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(code, json={"message": "test"})
            with self.assertRaises(NspdFailure):
                self.run_gateway(handler, lambda gateway: gateway.search(POLYGON))
            self.assertEqual(len(calls), 2)

    def test_timeouts_retry_once_and_tls_never_falls_back(self):
        for exception, expected_count, expected_code in (
            (httpx.ReadTimeout("timeout"), 2, "connection_error"),
            (httpx.ConnectError("CERTIFICATE_VERIFY_FAILED"), 1, "tls_verification_failed"),
        ):
            calls = []
            def handler(request):
                calls.append(request)
                raise exception
            with self.assertRaises(NspdFailure) as raised:
                self.run_gateway(handler, lambda gateway: gateway.search(POLYGON))
            self.assertEqual(raised.exception.code, expected_code)
            self.assertEqual(len(calls), expected_count)

    def test_partial_malformed_and_non_json_responses_are_not_not_found(self):
        for data in ({}, {"features": []}, {"type": "FeatureCollection", "features": [], "totalCount": 2},
                     {"type": "FeatureCollection", "features": [], "partial": True},
                     {"type": "FeatureCollection", "features": [], "meta": {"totalCount": 3}},
                     {"type": "FeatureCollection", "features": [], "totalCount": "unknown"}):
            with self.assertRaises(NspdFailure):
                collection(data)
        with self.assertRaises(NspdFailure):
            self.run_gateway(lambda r: httpx.Response(200, text="<html>captcha</html>"), lambda g: g.search(POLYGON))

    def test_collection_crs_propagates_to_geometry(self):
        crs = {"type": "name", "properties": {"name": "EPSG:3857"}}
        found = collection({"type": "FeatureCollection", "features": [fixture()], "crs": crs})
        self.assertEqual(found[0]["geometry"]["crs"], crs)

    def test_wms_limit_is_incomplete_not_success(self):
        with self.assertRaises(NspdFailure) as raised:
            self.run_gateway(lambda r: httpx.Response(200, json={"type": "FeatureCollection", "features": [fixture()] * 10}),
                             lambda g: g.search(Point(49, 53)))
        self.assertEqual(raised.exception.code, "incomplete_response")

    def test_control_number_lookup_preserves_collection(self):
        def handler(request):
            self.assertEqual(request.url.params["query"], "63:09:0102159:753")
            return httpx.Response(200, json={"data": {"type": "FeatureCollection", "features": [fixture()]}, "meta": {"totalCount": 1}})
        found = self.run_gateway(handler, lambda g: g.by_number("63:09:0102159:753"))
        self.assertEqual(len(found), 1)

    def test_control_number_metadata_array_matches_real_nspd_schema(self):
        def handler(request):
            return httpx.Response(200, json={"data": {"type": "FeatureCollection", "features": [fixture()]},
                                             "meta": [{"totalCount": 1, "categoryId": 36368}]})
        found = self.run_gateway(handler, lambda g: g.by_number("63:09:0102159:753"))
        self.assertEqual(len(found), 1)

    def test_control_number_incomplete_category_metadata_is_rejected(self):
        def handler(request):
            return httpx.Response(200, json={"data": {"type": "FeatureCollection", "features": [fixture()]},
                                             "meta": [{"totalCount": 2, "categoryId": 36368}]})
        with self.assertRaises(NspdFailure) as raised:
            self.run_gateway(handler, lambda g: g.by_number("63:09:0102159:753"))
        self.assertEqual(raised.exception.code, "incomplete_response")


if __name__ == "__main__":
    unittest.main()
