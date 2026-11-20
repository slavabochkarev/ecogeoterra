"""Isolated NSPD gateway. No UI, OSM, implicit cache, or insecure TLS fallback.

Pinned pynspd supplies its request/error protocol and layer definitions. Raw
responses are retained so CRS and incomplete collections can be checked before
pynspd's geometry validators erase the original CRS. Private API use is limited
to overriding _build_client; upgrade only with the transport tests.
"""
import asyncio
from contextlib import suppress
import hashlib
import json
import ssl
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from pynspd import Nspd
from pynspd.errors import PynspdResponseError
from pynspd.map_types.enums import ThemeId
from pynspd.schemas import (
    Layer36048Feature, Layer36049Feature, Layer36328Feature, Layer36329Feature, Layer875845Feature,
)
from shapely.geometry import mapping

LAYERS = (Layer36048Feature, Layer36049Feature, Layer36328Feature, Layer36329Feature)
CATEGORIES = {layer.layer_meta.category_id: layer.layer_meta.title for layer in LAYERS}
LAND_CATEGORY = Layer36048Feature.layer_meta.category_id
OOPT_CATEGORY = Layer875845Feature.layer_meta.category_id
NSPD_ENDPOINT = str(Nspd.DNS_URL)
LAND_INTERSECTS_PATH = "/api/geoportal/v1/intersects"
NUMBER_SEARCH_PATH = "/api/geoportal/v2/search/geoportal"
NSPD_ROOT_CA = Path(__file__).resolve().parent / "certs" / "russian_trusted_root_ca.pem"
NSPD_ROOT_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


def nspd_tls_context(ca_file=None):
    """System trust plus an app-scoped CA, never global trust injection.

    The bundled root was independently downloaded over verified HTTPS from the
    official Gosuslugi CDN. Its provenance is in certs/README.md. Do not trust
    certificates copied from an unverified server handshake.
    """
    context = ssl.create_default_context()
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    else:
        pem = NSPD_ROOT_CA.read_text(encoding="ascii")
        der = ssl.PEM_cert_to_DER_cert(pem)
        if hashlib.sha256(der).hexdigest() != NSPD_ROOT_SHA256:
            raise ssl.SSLError("Bundled NSPD root CA fingerprint mismatch")
        context.load_verify_locations(cadata=pem)
    return context


class NspdFailure(Exception):
    def __init__(self, message, code="upstream_error", retry_after=None):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


class SecureNspd(Nspd):
    def __init__(self, timeout=12, ca_file=None):
        self.tls_context = nspd_tls_context(ca_file)
        super().__init__(client_timeout=timeout, client_retries=0,
                         client_retry_on_blocked_ip=False, client_dns_resolve=False,
                         trust_env=False)

    def _build_client(self):
        assert self.tls_context.check_hostname
        assert self.tls_context.verify_mode == ssl.CERT_REQUIRED
        return httpx.Client(base_url=self.DNS_URL, headers=self._get_headers(),
                            timeout=httpx.Timeout(self._timeout, connect=5),
                            transport=httpx.HTTPTransport(verify=self.tls_context, retries=0),
                            trust_env=False, follow_redirects=False)

    def _build_async_client(self):
        return httpx.AsyncClient(base_url=self.DNS_URL, headers=self._get_headers(),
                                 timeout=httpx.Timeout(self._timeout, connect=5),
                                 transport=httpx.AsyncHTTPTransport(verify=self.tls_context, retries=0),
                                 trust_env=False, follow_redirects=False)


def collection(data, *, budget=None):
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise NspdFailure("НСПД вернула неполный или неожиданный ответ.", "invalid_response")
    if budget is not None:
        if any(data.get(key) for key in ("error", "errors", "message")):
            raise NspdFailure("НСПД вернула ошибку вместо кадастровых данных.", "invalid_response")
        meta = data.get("meta")
        pagination = data.get("pagination")
        for scope in (data, meta, pagination):
            if isinstance(scope, dict) and (
                any(scope.get(key) for key in ("partial", "truncated", "hasMore", "next", "nextPage", "exceededTransferLimit"))
                or (isinstance(scope.get("links"), list) and
                    any(isinstance(link, dict) and link.get("rel") == "next" for link in scope["links"]))
            ):
                raise NspdFailure("НСПД сообщила о неполной выдаче.", "incomplete_response")
        budget.check_features(data["features"])
    if any(data.get(key) for key in ("error", "errors", "message", "partial", "truncated", "hasMore", "next")):
        raise NspdFailure("НСПД сообщила об ошибке или неполной выдаче.", "incomplete_response")
    for counts in (data, data.get("meta") or {}):
        if not isinstance(counts, dict):
            raise NspdFailure("НСПД вернула некорректные метаданные.", "invalid_response")
        for key in ("totalFeatures", "numberMatched", "totalCount"):
            total = counts.get(key)
            if total is not None:
                if not isinstance(total, int) or total < 0:
                    raise NspdFailure("Не удалось проверить полноту ответа НСПД.", "incomplete_response")
                if total > len(data["features"]):
                    raise NspdFailure("НСПД вернула только часть объектов.", "incomplete_response")
                if budget is not None and (type(total) is not int or total != len(data["features"])):
                    raise NspdFailure("Счётчики ответа НСПД расходятся с числом объектов.", "incomplete_response")
        if budget is not None and "numberReturned" in counts:
            returned = counts["numberReturned"]
            if type(returned) is not int or returned != len(data["features"]):
                raise NspdFailure("Счётчики ответа НСПД расходятся с числом объектов.", "incomplete_response")
    features = data["features"]
    if data.get("crs") is not None:
        # Older GeoJSON can declare CRS on the collection rather than geometry.
        features = [{**feature, "geometry": {**feature["geometry"], "crs": data["crs"]}}
                    if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
                    and feature["geometry"].get("crs") is None else feature
                    for feature in features]
    return features


class NspdGateway:
    def __init__(self, timeout=12, ca_file=None):
        self.timeout, self.ca_file = timeout, ca_file

    def _json(self, client, method, url, *, attempts=2, **kwargs):
        # One retry only for temporary connection/time-out/5xx failures.
        for attempt in range(attempts):
            try:
                response = client.request(method, url, **kwargs)
                return response.json()
            except PynspdResponseError as exc:
                code = exc.response.status_code
                if code == 403:
                    raise NspdFailure("НСПД ограничила доступ (HTTP 403). Повторите позже; обход блокировки не выполняется.", "http_403") from exc
                if code == 429:
                    try:
                        pause = max(1, min(3600, int(exc.response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        pause = 60
                    raise NspdFailure(f"НСПД ограничила частоту запросов (HTTP 429). Подождите {pause} с.", "http_429", pause) from exc
                if code < 500 or attempt + 1 == attempts:
                    raise NspdFailure(f"НСПД недоступна: HTTP {code}. Это не означает отсутствие объекта.", f"http_{code}") from exc
            except httpx.TransportError as exc:
                # Certificate validation failures must never trigger insecure fallback.
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    raise NspdFailure("Не удалось проверить TLS-сертификат НСПД с доверенным CA. Проверка сертификата и имени сервера включена. Проверьте дату системы, цепочку сертификатов и NSPD_CA_FILE, если он задан.", "tls_verification_failed") from exc
                if attempt + 1 == attempts:
                    raise NspdFailure("НСПД не ответила вовремя или соединение недоступно. Повторите позже.", "connection_error") from exc
            except ValueError as exc:
                raise NspdFailure("НСПД вернула ответ, который не является JSON.", "invalid_response") from exc
            time.sleep(.5)

    @staticmethod
    def _check_bounded_response(response, budget):
        status = response.status_code
        if status == 429:
            value = response.headers.get("Retry-After", "60")
            try:
                pause = int(value)
            except ValueError:
                try:
                    pause = int(parsedate_to_datetime(value).timestamp() - time.time()) + 1
                except (ValueError, TypeError, OverflowError, OSError):
                    pause = 60
            pause = max(1, min(3600, pause))
            raise NspdFailure(f"НСПД ограничила частоту запросов. Подождите {pause} с.", "http_429", pause)
        if status == 403:
            raise NspdFailure("НСПД ограничила доступ. Повторите позже.", "http_403")
        if not response.is_success:
            raise NspdFailure(f"НСПД недоступна: HTTP {status}. Повторите позже.", f"http_{status}")
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding not in ("", "identity"):
            raise NspdFailure("НСПД вернула неподдерживаемое сжатие ответа.", "invalid_response")
        length = response.headers.get("Content-Length", "")
        if length.isdecimal() and int(length) > budget.max_response_bytes:
            raise NspdFailure("Ответ НСПД превышает допустимый объём. Уменьшите радиус.", "response_budget_exceeded")

    @staticmethod
    def _append_chunk(body, chunk, budget):
        budget.check()
        if len(body) + len(chunk) > budget.max_response_bytes:
            raise NspdFailure("Ответ НСПД превышает допустимый объём. Уменьшите радиус.", "response_budget_exceeded")
        body.extend(chunk)

    async def _strict_body(self, client, method, url, budget, **kwargs):
        async def watch_cancellation(task):
            while not budget.cancelled.is_set():
                await asyncio.sleep(.1)
            task.cancel()

        watcher = asyncio.create_task(watch_cancellation(asyncio.current_task()))
        try:
            async with asyncio.timeout(budget.remaining()):
                async with client:
                    async with client.stream(method, url, **kwargs) as response:
                        self._check_bounded_response(response, budget)
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            self._append_chunk(body, chunk, budget)
                        return body
        except asyncio.CancelledError:
            budget.check()
            raise
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    def _bounded_json(self, method, url, budget, **kwargs):
        timeout = min(self.timeout, budget.remaining())
        kwargs["headers"] = {**kwargs.pop("headers", {}), "Accept-Encoding": "identity"}
        kwargs["timeout"] = httpx.Timeout(timeout, connect=min(5, timeout))
        try:
            nspd = SecureNspd(timeout, self.ca_file)
            if getattr(budget, "strict", False):
                budget.check()
                body = asyncio.run(self._strict_body(nspd._build_async_client(), method, url, budget, **kwargs))
            else:
                with nspd._build_client() as client:
                    with client.stream(method, url, **kwargs) as response:
                        self._check_bounded_response(response, budget)
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            self._append_chunk(body, chunk, budget)
            budget.check()
            return json.loads(body)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise NspdFailure("НСПД не ответила вовремя. Повторите позже.", "deadline_exceeded") from exc
        except httpx.TransportError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                raise NspdFailure("Не удалось проверить TLS-сертификат НСПД.", "tls_verification_failed") from exc
            raise NspdFailure("Нет связи с НСПД. Повторите позже.", "connection_error") from exc
        except (ValueError, RecursionError) as exc:
            raise NspdFailure("НСПД вернула неожиданный формат ответа.", "invalid_response") from exc

    @staticmethod
    def _intersects_payload(geometry, categories):
        geom = {**mapping(geometry), "crs": {"type": "name", "properties": {"name": "EPSG:4326"}}}
        return {"categories": [{"id": category} for category in categories],
                "geom": {"type": "FeatureCollection", "features": [
                    {"type": "Feature", "properties": {}, "geometry": geom}]}}

    def _intersects(self, client, geometry, categories, attempts):
        raw = self._json(client, "post", LAND_INTERSECTS_PATH, attempts=attempts,
                         params={"typeIntersect": "fullObject"}, json=self._intersects_payload(geometry, categories))
        return collection(raw)

    def _bounded_intersects(self, geometry, categories, budget):
        payload = json.dumps(self._intersects_payload(geometry, categories),
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        budget.check_request_bytes(len(payload))
        raw = self._bounded_json("post", LAND_INTERSECTS_PATH, budget, params={"typeIntersect": "fullObject"},
                                 content=payload, headers={"Content-Type": "application/json"})
        return collection(raw, budget=budget)

    def search_land(self, geometry, *, budget=None):
        if budget is not None:
            return self._bounded_intersects(geometry, (LAND_CATEGORY,), budget)
        with SecureNspd(self.timeout, self.ca_file) as client:
            return self._intersects(client, geometry, (LAND_CATEGORY,), attempts=1)

    def search_oopt(self, geometry, *, budget):
        return self._bounded_intersects(geometry, (OOPT_CATEGORY,), budget)

    def search(self, geometry):
        with SecureNspd(self.timeout, self.ca_file) as client:
            if geometry.geom_type in {"Polygon", "MultiPolygon"}:
                return self._intersects(client, geometry, CATEGORIES, attempts=2), "polygon_intersects", []
            # WMS 1.3 EPSG:4326 BBOX uses latitude/longitude axis order.
            # A point query has a pixel tolerance; local covers() verifies hits.
            x, y = geometry.x, geometry.y
            params = {"REQUEST": "GetFeatureInfo", "SERVICE": "WMS", "VERSION": "1.3.0",
                      "INFO_FORMAT": "application/json", "FORMAT": "image/png", "STYLES": "",
                      "TRANSPARENT": "true", "WIDTH": 512, "HEIGHT": 512,
                      "I": 256, "J": 256, "CRS": "EPSG:4326",
                      "BBOX": f"{y - .000001},{x - .000001},{y + .000001},{x + .000001}",
                      "FEATURE_COUNT": 10}
            features = []
            for layer in LAYERS:
                layer_id = layer.layer_meta.layer_id
                raw = self._json(client, "get", f"/api/aeggis/v3/{layer_id}/wms",
                                 params={**params, "LAYERS": layer_id, "QUERY_LAYERS": layer_id})
                found = collection(raw)
                if len(found) >= 10:
                    raise NspdFailure("Достигнут лимит WMS: выдача в точке может быть неполной.", "incomplete_response")
                features.extend(found)
            return features, "point_wms", ["Поиск WMS в точке ограничен 10 объектами на слой; совпадения проверены по геометрии."]

    def by_number(self, number, *, land_only=False, budget=None):
        """Diagnostic/explicit-number lookup; never substitutes spatial matching."""
        scope = {"layersId": Layer36048Feature.layer_meta.layer_id} if land_only else {"thematicSearchId": 1}
        return self._number_search({"query": number, **scope}, budget=budget, attempts=1 if land_only else 2)

    def search_oopt_details(self, number, *, budget):
        return self._number_search({"query": number, "thematicSearchId": ThemeId.ZONES_AND_TERRITORIES.value},
                                   budget=budget)

    def _number_search(self, params, *, budget, attempts=1):
        if budget is not None:
            raw = self._bounded_json("get", NUMBER_SEARCH_PATH, budget, params=params)
        else:
            with SecureNspd(self.timeout, self.ca_file) as client:
                raw = self._json(client, "get", NUMBER_SEARCH_PATH, attempts=attempts, params=params)
        if not isinstance(raw, dict) or not isinstance(raw.get("meta"), (dict, list)):
            raise NspdFailure("Неполный ответ поиска по номеру.", "invalid_response")
        found = collection(raw.get("data"), budget=budget)
        # NSPD returns one metadata entry per category, not necessarily a dict.
        groups = raw["meta"] if isinstance(raw["meta"], list) else [raw["meta"]]
        if not groups or any(not isinstance(group, dict) or type(group.get("totalCount")) is not int
                             or group["totalCount"] < 0 for group in groups):
            raise NspdFailure("Некорректные метаданные поиска по номеру.", "invalid_response")
        if sum(group["totalCount"] for group in groups) > len(found):
            raise NspdFailure("Неполная выдача поиска по номеру.", "incomplete_response")
        return found
