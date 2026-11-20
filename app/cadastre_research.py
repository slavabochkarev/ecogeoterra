"""Bounded research for Map First; the experimental 500 m API remains separate."""
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from threading import Event, Lock
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely import get_num_coordinates, normalize
from shapely.geometry import mapping, shape

from .cadastre_experiment import (
    CadastralNumber, GEOMETRY_ERRORS, attach_display_data, buffer_geometry, land_collection,
)
from .nspd import LAND_CATEGORY, LAND_INTERSECTS_PATH, NSPD_ENDPOINT, NspdFailure

RADII = (500, 1000, 2000)
RESEARCH_SCHEMA = "research-v2"
UPSTREAM_INTERVAL = 3
MAX_WAIT_SECONDS = 10


class CadastreResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: CadastralNumber
    radius_m: Literal[500, 1000, 2000]
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("radius_m", mode="before")
    @classmethod
    def integer_radius(cls, value):
        if type(value) is not int:
            raise ValueError("Радиус должен быть целым числом метров")
        return value


@dataclass(frozen=True)
class ResearchLimits:
    enabled_radii: tuple[int, ...] = (500,)
    max_area_km2: int = 60
    max_request_vertices: int = 6000
    max_request_bytes: int = 256 * 1024
    max_response_bytes: int = 16 * 1024 * 1024
    max_response_vertices: int = 250_000
    max_objects: int = 5000
    deadline_seconds: int = 35
    cache_max_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_env(cls):
        defaults = cls()
        values = {}
        for name in ("max_area_km2", "max_request_vertices", "max_request_bytes", "max_response_bytes",
                     "max_response_vertices", "max_objects", "deadline_seconds", "cache_max_bytes"):
            variable = f"NSPD_RESEARCH_{name.upper()}"
            value = int(os.getenv(variable, str(getattr(defaults, name))))
            if value <= 0:
                raise ValueError(f"{variable} must be a positive integer")
            values[name] = value
        radii = (500, *(radius for radius in RADII[1:]
                          if os.getenv(f"NSPD_RESEARCH_ENABLE_{radius}", "").strip().lower() in ("1", "true")))
        return cls(enabled_radii=radii, **values)

    def data_profile(self):
        return (self.max_area_km2, self.max_request_vertices, self.max_request_bytes,
                self.max_response_bytes, self.max_response_vertices, self.max_objects)


class ResearchBudget:
    def __init__(self, limits, *, strict=False):
        self.limits = limits
        self.max_response_bytes = limits.max_response_bytes
        self.deadline = time.monotonic() + limits.deadline_seconds
        self.strict = strict
        self.cancelled = Event()

    def remaining(self):
        if self.cancelled.is_set():
            raise NspdFailure("Кадастровое исследование отменено.", "cancelled")
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise NspdFailure("Время исследования истекло. Повторите позже или уменьшите радиус.", "deadline_exceeded")
        return seconds

    def check(self):
        self.remaining()

    def check_features(self, features, *, source=False):
        if len(features) > self.limits.max_objects:
            raise NspdFailure("Слишком много участков в ответе НСПД. Уменьшите радиус.", "response_budget_exceeded")
        limit = self.limits.max_request_vertices if source else self.limits.max_response_vertices
        vertices = 0
        for feature in features:
            self.check()
            geometry = feature.get("geometry") if isinstance(feature, dict) else None
            pending = [geometry.get("coordinates")] if isinstance(geometry, dict) else []
            while pending:
                node = pending.pop()
                if not isinstance(node, (list, tuple)) or not node:
                    continue
                if isinstance(node[0], (int, float)):
                    vertices += 1
                    if vertices > limit:
                        code = "request_budget_exceeded" if source else "response_budget_exceeded"
                        raise NspdFailure("Контуры превышают допустимое число координат.", code)
                    if vertices % 1024 == 0:
                        self.check()
                else:
                    pending.extend(node)

    def check_request_bytes(self, size):
        self.check()
        if size > self.limits.max_request_bytes:
            raise NspdFailure("Буфер превышает допустимый размер запроса. Уменьшите радиус.", "request_budget_exceeded")


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def source_revision(source):
    geometry = normalize(shape(source["geometry"]))
    return hashlib.sha256(geometry.wkb + source["id"].encode("ascii")).hexdigest()


def request_nspd(cadastre, budget, operation, *args, **kwargs):
    """Apply the shared NSPD pause and cooldown while the caller holds the lock."""
    delay = cadastre.next_allowed - time.monotonic()
    if delay > UPSTREAM_INTERVAL:
        seconds = math.ceil(delay)
        raise NspdFailure(f"Дождитесь окончания ограничения НСПД. Повторите через {seconds} с.", "http_429", seconds)
    if delay > 0:
        if delay >= budget.remaining():
            raise NspdFailure("Время ожидания запроса к НСПД истекло.", "deadline_exceeded")
        budget.cancelled.wait(delay)
    budget.check()
    cooldown = UPSTREAM_INTERVAL
    try:
        return operation(*args, budget=budget, **kwargs)
    except NspdFailure as exc:
        cooldown = max(cooldown, exc.retry_after or 0)
        raise
    finally:
        cadastre.next_allowed = time.monotonic() + cooldown


class CadastreResearch:
    def __init__(self, experiment, limits=None):
        self.experiment = experiment
        self.cadastre = experiment.cadastre
        self.limits = limits or ResearchLimits.from_env()
        self.flights = {}
        self.flights_lock = Lock()

    def capabilities(self):
        allowed = self.limits.enabled_radii
        return {"source_candidates": True, "radii": [
            {"radius_m": radius, "enabled": radius in allowed,
             "reason": None if radius in allowed else "Радиус выключен до проверки условий НСПД."}
            for radius in RADII
        ]}

    def execute(self, payload, budget=None):
        if payload.radius_m not in self.limits.enabled_radii:
            raise NspdFailure("Этот радиус выключен на сервере. Выберите доступный радиус.", "radius_disabled")
        budget = budget or ResearchBudget(self.limits, strict=payload.radius_m == 2000)
        key = (payload.number, payload.radius_m, payload.source_revision)
        with self.flights_lock:
            flight = self.flights.get(key)
            leader = flight is None
            if leader:
                if len(self.flights) >= 5:
                    raise NspdFailure("Очередь кадастровых исследований занята. Повторите позже.", "busy")
                flight = self.flights[key] = Future()
        if not leader:
            try:
                while True:
                    try:
                        return flight.result(timeout=min(0.2, budget.remaining()) if budget.strict else budget.remaining())
                    except TimeoutError:
                        if not budget.strict or flight.done():
                            raise
            except TimeoutError as exc:
                raise NspdFailure("Время ожидания исследования истекло.", "deadline_exceeded") from exc

        try:
            if not self.cadastre.lock.acquire(timeout=min(MAX_WAIT_SECONDS, budget.remaining())):
                raise NspdFailure("Кадастровый сервис занят. Повторите позже.", "busy")
            try:
                response = self._research(payload, budget)
                budget.check()
            finally:
                self.cadastre.lock.release()
            flight.set_result(response)
            return response
        except BaseException as exc:
            flight.set_exception(exc)
            raise
        finally:
            with self.flights_lock:
                del self.flights[key]

    def _upstream(self, budget, operation, *args, **kwargs):
        return request_nspd(self.cadastre, budget, operation, *args, **kwargs)

    def _cache_path(self, number, radius, revision):
        key = [RESEARCH_SCHEMA, NSPD_ENDPOINT, LAND_INTERSECTS_PATH, LAND_CATEGORY, "fullObject",
               number, radius, revision, self.limits.data_profile()]
        digest = hashlib.sha256(encode(key)).hexdigest()
        return self.cadastre.directory / RESEARCH_SCHEMA / f"{digest}.json"

    def _read_cache(self, path, budget, radius, revision):
        try:
            age = time.time() - path.stat().st_mtime
            if not 0 <= age < self.cadastre.ttl:
                return None
            with path.open("rb") as stream:
                saved = stream.read(self.limits.max_response_bytes + 66)
            if len(saved) > self.limits.max_response_bytes + 65:
                return None
            digest, _, body = saved.partition(b"\n")
            if digest != hashlib.sha256(body).hexdigest().encode("ascii"):
                return None
            budget.check()
            result = json.loads(body)
            if (result["radius_m"] != radius or result["source_revision"] != revision or
                    source_revision(result["source"]) != revision or
                    result["coverage"] not in ("unknown", "complete")):
                return None
            if result["coverage"] == "complete":
                # No stored cache entry contains independent proof of NSPD coverage.
                result["coverage"] = "unknown"
                result["coverage_details"] = {"reason": "provider_coverage_unverified",
                                              "confirmed": False, "records_skipped": 0}
            result["cache_hit"] = True
            return self._encode_result(result, budget)
        except (OSError, *GEOMETRY_ERRORS, RecursionError):
            return None

    def _write_cache(self, path, body, budget):
        size = len(body) + 65
        if self.cadastre.ttl <= 0 or size > self.limits.cache_max_bytes:
            return
        temp = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            records = []
            total = 0
            for file in path.parent.glob("*.json"):
                budget.check()
                if file == path:
                    continue
                stat = file.stat()
                if time.time() - stat.st_mtime >= self.cadastre.ttl:
                    file.unlink(missing_ok=True)
                    continue
                records.append((stat.st_mtime, stat.st_size, file))
                total += stat.st_size
            for _, length, file in sorted(records):
                if total + size <= self.limits.cache_max_bytes:
                    break
                file.unlink(missing_ok=True)
                total -= length
            budget.check()
            with temp.open("wb") as stream:
                stream.write(hashlib.sha256(body).hexdigest().encode("ascii") + b"\n")
                stream.write(body)
            budget.check()
            temp.replace(path)
        except OSError:
            logging.warning("Could not store bounded cadastral research cache")
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _encode_result(self, result, budget):
        budget.check()
        body = encode(result)
        budget.check()
        if len(body) > budget.max_response_bytes:
            raise NspdFailure("Готовая выдача превышает допустимый объём. Уменьшите радиус.", "response_budget_exceeded")
        return body

    def _research(self, payload, budget):
        def find_source(number):
            features = self._upstream(budget, self.cadastre.gateway.by_number, number, land_only=True)
            budget.check_features(features, source=True)
            return features

        started = time.perf_counter()
        try:
            initial = self.experiment.resolve_source(payload.number, fetch=find_source, max_cache_bytes=budget.max_response_bytes)
            source = initial["parcel"]
            budget.check_features([source], source=True)
            revision = source_revision(source)
            if payload.source_revision and payload.source_revision != revision:
                raise NspdFailure("Контур исходного участка изменился. Найдите участок снова перед исследованием.", "source_changed")
            radius = payload.radius_m
            path = self._cache_path(payload.number, radius, revision)
            cached = self._read_cache(path, budget, radius, revision)
            if cached is not None:
                return cached

            context, region, geographic = buffer_geometry(source, radius)
            west, south, east, north = geographic.bounds
            if (region.area > self.limits.max_area_km2 * 1_000_000 or
                    get_num_coordinates(geographic) > self.limits.max_request_vertices or
                    east - west >= 180 or not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90)):
                raise NspdFailure("Буфер выходит за допустимые размеры или границы. Уменьшите радиус.", "request_budget_exceeded")
            preparation = time.perf_counter() - started
            features = self._upstream(budget, self.cadastre.gateway.search_land, geographic)
            retrieved_at = datetime.now(timezone.utc).isoformat()
            started = time.perf_counter()
            parcels, stats = land_collection(features, source, context, region, budget=budget)
            excluded = stats["invalid_geometry"] + stats["invalid_record"]
            if excluded and not stats["valid_geometry"]:
                raise NspdFailure("В ответе НСПД отсутствуют проверенные контуры.", "incomplete_response")
            budget.check_features([source, *parcels["features"]])
            attach_display_data(source, parcels["features"], context=context, budget=budget)
            stats["geojson_prepare_ms"] = round((preparation + time.perf_counter() - started) * 1000, 2)
            coverage = "partial" if excluded else "unknown"
            reason = "invalid_records_skipped" if excluded else "provider_coverage_unverified"
            warnings = list(initial["warnings"])
            if excluded:
                warnings.append(f"Пропущено записей с некорректными данными или геометрией: {excluded}.")
            result = {"source": source, "source_revision": revision,
                      "buffer": {"type": "Feature", "geometry": mapping(geographic), "properties": {"radius_m": radius}},
                      "parcels": parcels, "stats": stats, "radius_m": radius, "retrieved_at": retrieved_at,
                      "cache_hit": False, "coverage": coverage,
                      "coverage_details": {"reason": reason, "confirmed": False, "records_skipped": excluded},
                      "warnings": warnings}
            body = self._encode_result(result, budget)
            if coverage != "partial":
                self._write_cache(path, body, budget)
            return body
        except OSError as exc:
            raise NspdFailure("Не удалось открыть локальные данные или доверенные сертификаты НСПД.", "tls_configuration") from exc
        except (*GEOMETRY_ERRORS, RecursionError) as exc:
            logging.warning("Invalid bounded cadastral research data: %s", type(exc).__name__)
            raise NspdFailure("Не удалось обработать геометрию или данные НСПД.", "invalid_response") from exc
