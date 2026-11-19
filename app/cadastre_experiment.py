"""Land parcels within 500 m of one confirmed cadastral contour."""

import hashlib
import logging
import math
import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError
from pyproj.exceptions import ProjError
from shapely import prepare, transform
from shapely.errors import GEOSException
from shapely.geometry import Point, mapping, shape
from shapely.ops import nearest_points

from .cadastre import first, wgs84_geometry
from .geo import MetricContext
from .nspd import LAND_CATEGORY, NspdFailure

RADIUS_M = 500
POINT_RADIUS_M = 15
POLYGON_TYPES = {"Polygon", "MultiPolygon"}
GEOMETRY_ERRORS = (ValueError, TypeError, KeyError, ArithmeticError, ProjError, GEOSException)
CadastralNumber = Annotated[str, StringConstraints(
    strip_whitespace=True, max_length=40, pattern=r"^[0-9]{2}:[0-9]{2}:[0-9]{1,12}:[0-9]{1,14}$",
)]
NUMBER_ADAPTER = TypeAdapter(CadastralNumber)


class ExperimentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: CadastralNumber


class SourceCandidatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-85, le=85, allow_inf_nan=False, strict=True)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)


def land_options(feature):
    props = feature.get("properties") if isinstance(feature, dict) and feature.get("type") == "Feature" else None
    if not isinstance(props, dict) or props.get("category") != LAND_CATEGORY:
        return None, None
    options = props.get("options")
    number = first(options, "cad_num", "cad_number") if isinstance(options, dict) else None
    return (number.strip(), options) if isinstance(number, str) and number.strip() else (None, None)


def category_bucket(category):
    label = " ".join(category.lower().replace("ё", "е").split()) if isinstance(category, str) else ""
    if not label or any(word in label for word in ("не установл", "не определ", "нет данных", "отсутствует")):
        return "unknown"
    if label.startswith("земли населенных пунктов"):
        return "settlement"
    if label.startswith(("земли промышленности", "земли специального назначения")):
        return "industrial"
    if label.startswith("земли сельскохозяйственного назначения"):
        return "agricultural"
    return "other"


def text_field(value):
    if isinstance(value, str):
        return value.strip() or None
    return None


def land_properties(number, options):
    category = text_field(options.get("land_record_category_type"))
    area = first(options, "specified_area", "declared_area", "land_record_area", "area")
    if isinstance(area, str):
        try:
            area = float(area.strip().replace(",", "."))
        except ValueError:
            area = None
    if type(area) not in (int, float) or not math.isfinite(area):
        area = None
    return {
        "number": number,
        "land_category": category,
        "category_bucket": category_bucket(category),
        "permitted_use": text_field(options.get("permitted_use_established_by_document")),
        "area_m2": area,
        "address": text_field(first(options, "readable_address", "address_readable_address")),
    }


def parcel_feature(raw, number, options):
    geometry = raw.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") not in POLYGON_TYPES:
        raise ValueError("Нет площадного контура")
    if geometry.get("crs") is None and raw.get("crs") is not None:
        geometry = {**geometry, "crs": raw["crs"]}
    polygon = wgs84_geometry(geometry)
    return polygon, {"type": "Feature", "id": number, "geometry": mapping(polygon), "properties": {
        **land_properties(number, options), "geometry_status": "Корректный площадной контур",
    }}


def buffer_geometry(source, radius=RADIUS_M):
    context = MetricContext(shape(source["geometry"]))
    region = context.source.buffer(radius, quad_segs=8)
    wgs84 = transform(region, context.inverse.transform, interleaved=False)
    if not region.is_valid or wgs84.is_empty or not wgs84.is_valid:
        raise ValueError("Буфер невалиден")
    return context, region, wgs84


def land_collection(features, source, context, region, *, budget=None):
    prepare(region)
    neighbors = {}
    valid_numbers = set()
    invalid = wrong_kind = outside = duplicates = 0
    for raw in features:
        if budget is not None:
            budget.check()
        number, options = land_options(raw)
        if budget is not None and number is not None:
            try:
                number = NUMBER_ADAPTER.validate_python(number)
            except ValidationError:
                number = None
        if number is None:
            wrong_kind += 1
            continue
        try:
            polygon, feature = parcel_feature(raw, number, options)
            metric = transform(polygon, context.forward.transform, interleaved=False)
            if metric.is_empty or not metric.is_valid:
                raise ValueError("Некорректная геометрия в метрической CRS")
            in_region = region.intersects(metric)
        except GEOMETRY_ERRORS:
            invalid += 1
            continue
        valid_numbers.add(number)
        if not in_region:
            outside += 1
        elif number == source["id"]:
            continue
        elif number in neighbors:
            if budget is not None and neighbors[number] != feature:
                raise NspdFailure("НСПД вернула противоречивые данные об одном участке.", "incomplete_response")
            duplicates += 1
        else:
            neighbors[number] = feature

    mapped = [source, *neighbors.values()]
    known_category = sum(f["properties"]["category_bucket"] != "unknown" for f in mapped)
    stats = {"received": len(features), "valid_geometry": len(valid_numbers), "mapped": len(mapped),
             "neighbors": len(neighbors), "with_category": known_category, "without_category": len(mapped) - known_category,
             "invalid_geometry": invalid, "invalid_record": wrong_kind, "outside_buffer": outside,
             "duplicate_neighbors": duplicates}
    return {"type": "FeatureCollection", "features": list(neighbors.values())}, stats


def parcel_measurement(source, context, target):
    undefined = {"relation": "undefined"}
    if target is None or target.geom_type not in POLYGON_TYPES or target.is_empty or not target.is_valid:
        return undefined
    if source.intersects(target):
        relation = "touches" if source.touches(target) else "intersects"
        return {"relation": relation, "distance_m": 0}

    metric = transform(target, context.forward.transform, interleaved=False)
    if not metric.is_valid or context.source.intersects(metric):
        # A reprojection of straight segments can change topology near a boundary.
        return undefined
    start, end = nearest_points(context.source.boundary, metric.boundary)
    return {"relation": "disjoint", "distance_m": start.distance(end),
            "endpoints": [context.inverse.transform(p.x, p.y) for p in (start, end)]}


def attach_display_data(source, neighbors=(), *, context=None, budget=None):
    source_geometry = shape(source["geometry"])
    for feature in (source, *neighbors):
        if budget is not None:
            budget.check()
        geometry = source_geometry if feature is source else shape(feature["geometry"])
        point = geometry.representative_point()
        feature["properties"].update(
            label_point=[point.x, point.y],
            measurement={"relation": "source"} if feature is source else
            parcel_measurement(source_geometry, context, geometry),
        )


def point_candidates(features, point, context):
    candidates = {}
    ignored = 0
    for raw in features:
        number, options = land_options(raw)
        try:
            number = NUMBER_ADAPTER.validate_python(number)
        except ValidationError:
            ignored += 1
            continue

        unchecked = {
            "number": number, "relation": "unverified", "distance_m": None, "parcel": None,
            "properties": {**land_properties(number, options), "geometry_status": "Нет проверенного площадного контура"},
        }
        candidate = unchecked
        try:
            polygon, parcel = parcel_feature(raw, number, options)
            if polygon.contains(point):
                relation, distance = "inside", None
            elif polygon.covers(point):
                relation, distance = "boundary", None
            else:
                metric = transform(polygon, context.forward.transform, interleaved=False)
                if metric.is_empty or not metric.is_valid:
                    raise ValueError("Некорректная геометрия в метрической CRS")
                distance = context.source.distance(metric)
                if not math.isfinite(distance):
                    raise ValueError("Некорректное расстояние до ориентира")
                relation = "near"
            # Keep out-of-range contours until duplicate numbers have been checked.
            anchor = polygon.representative_point()
            parcel["properties"]["label_point"] = [anchor.x, anchor.y]
            candidate = {"number": number, "relation": relation, "distance_m": distance, "parcel": parcel}
        except GEOMETRY_ERRORS as exc:
            logging.debug("Candidate contour could not be verified: %s", type(exc).__name__)

        previous = candidates.get(number)
        if previous is not None and previous != candidate:
            unchecked["properties"]["geometry_status"] = "НСПД вернула противоречивые записи для этого номера"
            candidate = unchecked
        candidates[number] = candidate

    order = {"inside": 0, "boundary": 1, "near": 2, "unverified": 3}
    eligible = [item for item in candidates.values()
                if item["relation"] != "near" or item["distance_m"] <= POINT_RADIUS_M]
    eligible.sort(key=lambda item: (order[item["relation"]], item["distance_m"] or 0, item["number"]))
    warnings = []
    if ignored:
        warnings.append(f"Пропущено записей без корректного кадастрового номера: {ignored}.")
    unverified = sum(item["relation"] == "unverified" for item in eligible)
    if unverified:
        warnings.append(f"Кандидаты без проверенного контура: {unverified}. Их нельзя выбрать исходным участком.")
    return eligible, warnings


class CadastreExperiment:
    def __init__(self, cadastre):
        self.cadastre = cadastre

    def _upstream(self, action, *args, **kwargs):
        delay = self.cadastre.next_allowed - time.monotonic()
        if delay > 2:
            retry_after = math.ceil(delay)
            raise NspdFailure(f"НСПД ограничила частоту запросов. Повторите через {retry_after} с.", "http_429", retry_after)
        if delay > 0:
            time.sleep(delay)
        self.cadastre.next_allowed = time.monotonic() + 2
        try:
            return action(*args, **kwargs)
        except NspdFailure as exc:
            if exc.retry_after:
                self.cadastre.next_allowed = time.monotonic() + exc.retry_after
            raise

    def resolve_source(self, number, *, fetch=None, max_cache_bytes=None):
        key = f"experiment-source-v1-{number.replace(':', '_')}"
        cached = self.cadastre.read_cache(key, max_bytes=max_cache_bytes) if max_cache_bytes else self.cadastre.read_cache(key)
        cached_parcel = cached.get("parcel") if cached is not None else None
        if isinstance(cached_parcel, dict) and cached_parcel.get("id") == number:
            return {**cached, "cache_hit": True}
        features = fetch(number) if fetch else self._upstream(self.cadastre.gateway.by_number, number, land_only=True)
        seed = None
        for raw in features:
            found, options = land_options(raw)
            if found != number:
                continue
            try:
                _, feature = parcel_feature(raw, found, options)
            except GEOMETRY_ERRORS as exc:
                raise NspdFailure("У исходного участка отсутствует корректный площадной контур.", "missing_contour") from exc
            if seed is not None and seed != feature:
                raise NspdFailure("НСПД вернула разные контуры или характеристики для одного исходного номера.", "invalid_response")
            seed = feature
        if seed is None:
            raise NspdFailure("Земельный участок с таким кадастровым номером не найден.", "not_found")
        result = {"parcel": seed, "retrieved_at": datetime.now(timezone.utc).isoformat(), "cache_hit": False, "warnings": []}
        self.cadastre.write_cache(key, result)
        return result

    def _research(self, number):
        upstream_source = self.resolve_source(number)
        source = upstream_source["parcel"]
        digest = hashlib.sha256(shape(source["geometry"]).wkb + number.encode("ascii")).hexdigest()
        key = f"experiment-buffer-v1-{RADIUS_M}-{digest}"
        cached = self.cadastre.read_cache(key)
        cached_parcels = cached.get("parcels") if cached is not None else None
        if isinstance(cached_parcels, dict) and cached_parcels.get("type") == "FeatureCollection":
            return {**cached, "cache_hit": True}

        started = time.perf_counter()
        context, region, wgs84 = buffer_geometry(source)
        buffer_ms = (time.perf_counter() - started) * 1000
        features = self._upstream(self.cadastre.gateway.search_land, wgs84)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        parcels, stats = land_collection(features, source, context, region)
        buffer = {"type": "Feature", "geometry": mapping(wgs84), "properties": {"radius_m": RADIUS_M}}
        stats["geojson_prepare_ms"] = round(buffer_ms + (time.perf_counter() - started) * 1000, 2)
        result = {"source": source, "buffer": buffer, "parcels": parcels, "stats": stats, "radius_m": RADIUS_M,
                  "retrieved_at": retrieved_at, "cache_hit": False, "coverage": "unknown",
                  "warnings": upstream_source["warnings"]}
        self.cadastre.write_cache(key, result)
        return result

    def _source_candidates(self, payload):
        point_data = {"lat": payload.lat, "lon": payload.lon}
        digest = hashlib.sha256(f"{payload.lat!r},{payload.lon!r}".encode("ascii")).hexdigest()
        key = f"experiment-point-v1-{POINT_RADIUS_M}-{digest}"
        cached = self.cadastre.read_cache(key)
        if cached is not None and cached.get("point") == point_data and isinstance(cached.get("candidates"), list):
            return {**cached, "cache_hit": True}

        point = Point(payload.lon, payload.lat)
        context = MetricContext(point)
        region = transform(context.source.buffer(POINT_RADIUS_M, quad_segs=16),
                           context.inverse.transform, interleaved=False)
        # A wrapped polygon crossing the antimeridian could be interpreted as a very large request.
        west, _, east, _ = region.bounds
        if region.is_empty or not region.is_valid or not -180 <= west <= east <= 180 or east - west >= 1:
            raise HTTPException(422, "Для этой точки нельзя безопасно построить область поиска. Попробуйте другую точку.")
        features = self._upstream(self.cadastre.gateway.search_land, region)
        candidates, warnings = point_candidates(features, point, context)
        result = {
            "point": point_data, "candidates": candidates, "radius_m": POINT_RADIUS_M,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "cache_hit": False,
            "coverage": "unknown", "warnings": warnings,
        }
        if not warnings:
            self.cadastre.write_cache(key, result)
        return result

    def find_source_candidates(self, payload):
        return self._execute(self._source_candidates, payload)

    def execute(self, number, *, research=False):
        return self._execute(self._prepare_result, number, research)

    def _prepare_result(self, number, research):
        result = self._research(number) if research else self.resolve_source(number)
        source = result["source"] if research else result["parcel"]
        # Attach display data after the cache lookup, including for existing entries.
        context = MetricContext(shape(source["geometry"])) if research else None
        neighbors = result["parcels"]["features"] if research else ()
        attach_display_data(source, neighbors, context=context)
        return result

    def _execute(self, operation, *args):
        if not self.cadastre.lock.acquire(blocking=False):
            raise HTTPException(409, "Уже выполняется запрос кадастровых сведений. Дождитесь его завершения.")
        try:
            return operation(*args)
        except NspdFailure as exc:
            status = {"http_403": 403, "http_429": 429, "not_found": 404,
                      "missing_contour": 422, "connection_error": 503}.get(exc.code, 502)
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(status, str(exc), headers=headers) from exc
        except OSError as exc:
            raise HTTPException(503, "Не удалось загрузить доверенные сертификаты НСПД.") from exc
        except GEOMETRY_ERRORS as exc:
            logging.warning("Invalid cadastral experiment data: %s", exc)
            raise HTTPException(502, "Не удалось обработать геометрию и данные НСПД.") from exc
        finally:
            self.cadastre.lock.release()
