import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from shapely import transform
from shapely.geometry import MultiPolygon, box, shape

from app.cadastre import CadastreService
from app.cadastre_experiment import CadastreExperiment, buffer_geometry
from app.cadastre_research import CadastreResearch, ResearchBudget, ResearchLimits, encode
from app.geo import MetricContext
from app.main import app
from app.nspd import NspdFailure, NspdGateway, SecureNspd, collection
from app.research_store import ResearchStore
from test_cadastre_experiment import SEED, SOURCE, parcel


class Cadastre2000Tests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.gateway = Mock(spec=NspdGateway)
        geometry = MultiPolygon([SOURCE, box(49.301, 53.55, 49.3014, 53.5505)])
        self.context = MetricContext(geometry)
        self.seed = parcel(SEED, geometry)
        self.gateway.by_number.return_value = [self.seed]
        self.service = CadastreService(gateway=self.gateway, cache_dir=self.root / "cache")
        self.limits = ResearchLimits(enabled_radii=(500, 1000, 2000))
        research = CadastreResearch(CadastreExperiment(self.service), self.limits)
        for name, replacement in (
            ("app.main.cadastre_research", research),
            ("app.research_api.store", ResearchStore(self.root / "researches")),
            ("app.cadastre_research.UPSTREAM_INTERVAL", 0),
        ):
            fixture = patch(name, replacement)
            fixture.start()
            self.addCleanup(fixture.stop)
        self.client = TestClient(app)

    def neighbor(self, suffix, distance, use=None):
        _, south, east, north = self.context.source.bounds
        y = (south + north) / 2
        metric = box(east + distance, y - 1, east + distance + 3, y + 1)
        raw = parcel(f"63:09:0102159:{suffix}", transform(metric, self.context.inverse.transform, interleaved=False))
        raw["properties"]["options"]["permitted_use_established_by_document"] = use
        return raw

    def run_radius(self, radius):
        return self.client.post("/api/cadastre/research", json={"number": SEED, "radius_m": radius})

    def test_full_buffer_across_radii_and_cache_keeps_original_date(self):
        inner = self.neighbor(760, 498)
        crossing1000 = self.neighbor(761, 999)
        crossing2000 = self.neighbor(762, 1999)
        self.gateway.search_land.return_value = [
            self.seed, inner, crossing1000, crossing2000, crossing1000, self.neighbor(763, 2001),
        ]
        for radius, suffixes in ((500, [760]), (1000, [760, 761]), (2000, [760, 761, 762])):
            response = self.run_radius(radius)
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            sent = self.gateway.search_land.call_args.args[0]
            expected = buffer_geometry(data["source"], radius)[2]
            self.assertTrue(sent.equals_exact(expected, 1e-12))
            self.assertTrue(sent.covers(shape(data["source"]["geometry"])))
            self.assertFalse(sent.interiors if sent.geom_type == "Polygon" else ())
            self.assertEqual([int(f["id"].split(":")[-1]) for f in data["parcels"]["features"]], suffixes)
            self.assertEqual(data["coverage"], "unknown")
            self.assertFalse(data["coverage_details"]["confirmed"])
            self.assertFalse(data["cache_hit"])
            for feature, distance in zip(data["parcels"]["features"], (498, 999, 1999)):
                self.assertAlmostEqual(feature["properties"]["measurement"]["distance_m"], distance, delta=0.2)
        self.assertEqual(data["stats"]["duplicate_neighbors"], 1)
        replay = self.run_radius(2000).json()
        self.assertTrue(replay["cache_hit"])
        self.assertEqual(replay["retrieved_at"], data["retrieved_at"])
        self.assertEqual(replay["parcels"], data["parcels"])
        self.assertEqual(self.gateway.search_land.call_count, 3)
        self.gateway.by_number.assert_called_once()

    def test_partial_cache_is_rejected_and_failed_expansion_keeps_previous_save(self):
        self.gateway.search_land.return_value = [self.neighbor(761, 999)]
        previous = self.run_radius(1000).json()
        path = self.client.post("/api/researches", json={"state": {}}).json()["research"]["id"]
        endpoint = "/api/researches/" + path
        extracted = {key: value for key, value in previous.items() if key != "source"}
        saved = self.client.put(endpoint, json={"revision": 1, "state": {"cadastre_radius": 1000},
                                               "cadastre": {"source": previous["source"], "result": extracted}})
        self.assertEqual(saved.status_code, 200)
        folder = self.root / "researches" / path
        before = [(folder / name).read_bytes() for name in ("research.json", "cadastre.json")]

        # A well-formed file carrying a partial result must still be ignored.
        cache_key = next((self.service.directory / "research-v2").glob("*.json"))
        body = encode({**previous, "coverage": "partial"})
        cache_key.write_bytes(sha256(body).hexdigest().encode("ascii") + b"\n" + body)
        self.gateway.search_land.side_effect = NspdFailure("provider truncated", "incomplete_response")
        self.assertEqual(self.run_radius(1000).status_code, 502)
        failed = self.run_radius(2000)
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["coverage"], "partial")
        self.assertEqual(self.client.get(endpoint + "/reports").json()["report"]["radius_m"], 1000)
        self.assertEqual([(folder / name).read_bytes() for name in ("research.json", "cadastre.json")], before)

    def test_restore_and_both_reports_preserve_long_vri_and_actual_radius(self):
        use = "Действует по указанным условиям. " * 40
        self.assertGreater(len(use.strip()), 1000)
        self.gateway.search_land.return_value = [self.neighbor(761, 999, "2.1"), self.neighbor(762, 1999, use)]
        data = self.run_radius(2000).json()
        created = self.client.post("/api/researches", json={"state": {}}).json()["research"]
        endpoint = "/api/researches/" + created["id"]
        source = data.pop("source")
        result = self.client.put(endpoint, json={"revision": 1, "state": {"cadastre_radius": 2000},
                                                "cadastre": {"source": source}})
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(self.client.get(endpoint + "/reports").json()["report"])
        updated = self.client.put(endpoint, json={"revision": 2, "state": {"cadastre_radius": 500},
                                                "cadastre": {"source": source, "result": data}})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.client.post(endpoint + "/close", json={"revision": 3}).status_code, 200)
        reopened = self.client.post(endpoint + "/open").json()
        self.assertEqual(reopened["research"]["state"]["cadastre_radius"], 500)
        restored = reopened["cadastre"]["result"]["parcels"]["features"]
        for saved_feature, incoming in zip(restored, data["parcels"]["features"], strict=True):
            self.assertEqual(saved_feature["id"], incoming["id"])
            self.assertEqual(saved_feature["geometry"], incoming["geometry"])
            self.assertEqual(saved_feature["properties"]["measurement"], incoming["properties"]["measurement"])
        report = self.client.get(endpoint + "/reports").json()["report"]
        self.assertEqual((report["radius_m"], report["retrieved_at"], report["coverage"]),
                         (2000, data["retrieved_at"], "unknown"))
        self.assertEqual(len(report["all"]), 2)
        review = next(row for row in report["normalized"] if row["classification"] == "review")
        self.assertEqual(review["use"], use.strip())
        self.assertAlmostEqual(review["distance_m"], 1999, delta=0.2)
        self.assertTrue(review["direction"])

    def test_truncation_and_limits_are_rejected(self):
        for metadata in ({"pagination": {"nextPage": 2}}, {"numberMatched": 2}):
            with self.subTest(metadata=metadata), self.assertRaises(NspdFailure) as error:
                collection({"type": "FeatureCollection", "features": [self.seed], **metadata},
                           budget=ResearchBudget(self.limits))
            self.assertEqual(error.exception.code, "incomplete_response")
        self.gateway.search_land.side_effect = NspdFailure("limit", "response_budget_exceeded")
        self.assertEqual(self.run_radius(2000).json()["detail"]["code"], "response_budget_exceeded")

    def test_api_deadline_bounds_wait_and_stops_cache_commit(self):
        limits = ResearchLimits(enabled_radii=(2000,), deadline_seconds=0.08)
        service = CadastreResearch(CadastreExperiment(self.service), limits)
        stopped = Event()

        def slow_search(geometry, *, budget):
            budget.cancelled.wait(timeout=1)
            stopped.set()
            return [self.seed]

        self.gateway.search_land.side_effect = slow_search
        started = time.monotonic()
        with patch("app.main.cadastre_research", service):
            response = self.run_radius(2000)
        self.assertEqual(response.status_code, 504, response.text)
        self.assertLess(time.monotonic() - started, 0.65)
        self.assertTrue(stopped.wait(1))
        self.assertEqual(list(self.service.directory.glob("research-v2/*.json")), [])

    def test_client_disconnect_cancels_research(self):
        started, stopped = Event(), Event()

        def slow_search(geometry, *, budget):
            started.set()
            if budget.cancelled.wait(timeout=2):
                stopped.set()
            return [self.seed]

        self.gateway.search_land.side_effect = slow_search

        async def disconnected_request():
            path = "/api/cadastre/research"
            scope = {"type": "http", "method": "POST", "scheme": "http", "path": path,
                     "root_path": "", "query_string": b"", "headers": [(b"content-type", b"application/json")]}
            first = True
            sent = []

            async def receive():
                nonlocal first
                if first:
                    first = False
                    return {"type": "http.request", "body": encode({"number": SEED, "radius_m": 2000})}
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            await asyncio.wait_for(app(scope, receive, send), 2)
            return sent[0]["status"]

        self.assertEqual(asyncio.run(disconnected_request()), 499)
        self.assertTrue(stopped.wait(1))
        self.assertEqual(list(self.service.directory.glob("research-v2/*.json")), [])

    def test_network_deadline_and_cancellation_close_the_stream(self):
        for cancel, expected in ((False, "deadline_exceeded"), (True, "cancelled")):
            with self.subTest(cancel=cancel):
                closed = Event()

                class StalledBody(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        while True:
                            await asyncio.sleep(.04)
                            yield b" "

                    async def aclose(self):
                        closed.set()

                def respond(request):
                    return httpx.Response(200, stream=StalledBody())

                limits = ResearchLimits(deadline_seconds=1.0 if cancel else .15)
                budget = ResearchBudget(limits, strict=True)
                client = httpx.AsyncClient(base_url="https://mock.invalid", transport=httpx.MockTransport(respond))
                trigger = Timer(.06, budget.cancelled.set)
                if cancel:
                    trigger.start()
                try:
                    with patch.object(SecureNspd, "_build_async_client", return_value=client):
                        with self.assertRaises(NspdFailure) as error:
                            NspdGateway().search_land(SOURCE, budget=budget)
                    self.assertEqual(error.exception.code, expected)
                    self.assertTrue(closed.is_set())
                finally:
                    trigger.cancel()
