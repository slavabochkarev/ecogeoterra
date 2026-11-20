import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

from app.main import app
from app.research_store import ResearchStore
from app.services import CachedService, ServiceError


class APITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_start_page_health_and_local_assets(self):
        for path in ("/", "/map"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('src="/static/map-first/main.js"', response.text)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        for path in ("/api/health", "/api/config", "/static/map-first/main.js", "/static/vendor/leaflet.js"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        for path in ("/legacy/osm", "/legacy/cadastre-experiment", "/cadastre-experiment",
                     "/static/app.js", "/static/cadastre-experiment.html", "/static/styles.css"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in ("/api/cadastre/enrich", "/api/cadastre-experiment/research"):
            self.assertTrue(paths[path]["post"]["deprecated"])
        self.assertEqual(self.client.get("/static/map-first/main.js").headers["Cache-Control"], "no-store")

    def test_research_accepts_current_state_and_rejects_stale_revision(self):
        with TemporaryDirectory() as directory, patch("app.research_api.store", ResearchStore(directory)):
            created = self.client.post("/api/researches", json={"state": {"oopt_anchor": None}})
            self.assertEqual(created.status_code, 201)
            path = "/api/researches/" + created.json()["research"]["id"]
            state = {"tab": "oopt", "oopt_anchor": "coordinates"}
            invalid = self.client.put(path, json={"revision": 1, "state": {"oopt_anchor": "invalid"}})
            self.assertEqual(invalid.status_code, 422)
            self.assertIn("state.oopt_anchor", invalid.json()["detail"])
            updated = self.client.put(path, json={"revision": 1, "state": state})
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["research"]["revision"], 2)
            self.assertEqual(self.client.put(path, json={"revision": 1, "state": {}}).status_code, 409)
            self.assertEqual(self.client.post(path + "/close", json={"revision": 2}).status_code, 200)
            restored = self.client.post(path + "/open").json()
            self.assertIsNone(restored["cadastre"])
            self.assertEqual(restored["research"]["state"]["tab"], "oopt")
            self.assertEqual(restored["research"]["state"]["oopt_anchor"], "coordinates")

    def test_validation_prevents_network_requests(self):
        with patch("app.main.fetch_objects") as fetch:
            for payload in ({"lat": 91, "lon": 49}, {"lat": 53, "lon": 181}, {"lat": 53, "lon": 49, "radius": 3000}):
                self.assertEqual(self.client.post("/api/research", json=payload).status_code, 422)
            fetch.assert_not_called()

    def test_real_pipeline_on_synthetic_fixture_and_error(self):
        raw = {"elements": [
            {"type": "node", "id": 1, "lat": 53.5001, "lon": 49.4, "tags": {"amenity": "school", "name": "<test>"}},
            {"type": "node", "id": 1, "lat": 53.5001, "lon": 49.4, "tags": {"amenity": "school"}},
            {"type": "node", "id": 2, "lat": 54, "lon": 49.4, "tags": {"building": "yes"}},
            {"type": "way", "id": 3, "tags": {"building": "yes"}},
        ]}
        with patch("app.main.fetch_objects", return_value=(raw, {"hit": False, "fetched_at": "test"})):
            response = self.client.post("/api/research", json={"lat": 53.5, "lon": 49.4, "radius": 500})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["metadata"]["total"], 1)
        self.assertEqual(data["metadata"]["counts"]["education"], 1)
        self.assertEqual(data["metadata"]["skipped_geometry"], 1)
        self.assertEqual(data["features"][0]["properties"]["direction"], "С")
        self.assertEqual(data["features"][0]["properties"]["tags"]["name"], "<test>")
        with patch("app.main.fetch_objects", side_effect=ServiceError("Тестовая ошибка API", 504)):
            response = self.client.post("/api/research", json={"lat": 53.5, "lon": 49.4})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["detail"], "Тестовая ошибка API")

    def test_ambiguous_and_empty_geocoding_are_preserved(self):
        with patch("app.main.geocode", return_value={"results": [{"lat": 53, "lon": 49}, {"lat": 54, "lon": 50}]}):
            self.assertEqual(len(self.client.get("/api/geocode", params={"q": "тест"}).json()["results"]), 2)
        with patch("app.main.geocode", return_value={"results": []}):
            self.assertEqual(self.client.get("/api/geocode", params={"q": "тест"}).json()["results"], [])


class CacheTests(unittest.TestCase):
    def test_disk_cache_survives_client_restart_and_ignores_corrupt_file(self):
        with TemporaryDirectory() as folder:
            service = CachedService("Nominatim", "https://example.invalid/search", 100, 0, Path(folder))
            response = httpx.Response(200, json=[{"display_name": "test"}])
            with patch("app.services.httpx.Client") as client:
                client.return_value.__enter__.return_value.get.return_value = response
                first, meta = service.request({"q": "test"})
                self.assertFalse(meta["hit"])
                restarted = CachedService("Nominatim", service.url, 100, 0, Path(folder))
                second, meta = restarted.request({"q": "test"})
                self.assertTrue(meta["hit"])
                self.assertEqual(first, second)
                self.assertEqual(client.return_value.__enter__.return_value.get.call_count, 1)
                next(Path(folder).rglob("*.json")).write_text("broken", encoding="utf-8")
                service.request({"q": "test"})
                self.assertEqual(client.return_value.__enter__.return_value.get.call_count, 2)

    def test_overpass_partial_reply_is_not_cached(self):
        with TemporaryDirectory() as folder:
            service = CachedService("Overpass", "https://example.invalid", 100, 0, Path(folder))
            with patch("app.services.httpx.Client") as client:
                client.return_value.__enter__.return_value.post.return_value = httpx.Response(200, json={"elements": [], "remark": "timeout"})
                with self.assertRaises(ServiceError):
                    service.request({"data": "test"}, "POST")
            self.assertEqual(list(Path(folder).rglob("*.json")), [])

    def test_429_enforces_cooldown_without_retry(self):
        with TemporaryDirectory() as folder:
            service = CachedService("Nominatim", "https://example.invalid", 100, 0, Path(folder))
            with patch("app.services.httpx.Client") as client:
                client.return_value.__enter__.return_value.get.return_value = httpx.Response(429, headers={"Retry-After": "120"})
                for _ in range(2):
                    with self.assertRaises(ServiceError) as raised:
                        service.request({"q": "test"})
                    self.assertEqual(raised.exception.status, 429)
                self.assertEqual(client.return_value.__enter__.return_value.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
