"""On-demand cadastral enrichment, independent from the application's UI."""
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely import get_coordinates, transform
from shapely.errors import GEOSException
from shapely.geometry import Point, mapping, shape

from .geo import MetricContext
from .nspd import CATEGORIES, NspdFailure, NspdGateway
from .services import default_cache_dir


def wgs84_geometry(raw, default_crs="EPSG:4326"):
    if not isinstance(raw, dict):
        raise ValueError("Геометрия отсутствует")
    source_crs = raw.get("crs")
    if source_crs is not None:
        if not isinstance(source_crs, dict) or source_crs.get("type") != "name":
            raise ValueError("Неизвестный формат CRS")
        source_crs = source_crs.get("properties", {}).get("name")
        if not source_crs:
            raise ValueError("CRS не указана")
    crs = CRS.from_user_input(source_crs or default_crs)
    geom = shape(raw)
    if not crs.equals(CRS.from_epsg(4326)):
        converter = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        geom = transform(geom, converter.transform, interleaved=False)
    coords = get_coordinates(geom, include_z=geom.has_z)
    if geom.is_empty or not geom.is_valid or not len(coords) or not all(math.isfinite(float(v)) for row in coords for v in row):
        raise ValueError("Пустая или невалидная геометрия")
    if any(not (-180 <= row[0] <= 180 and -90 <= row[1] <= 90) for row in coords):
        raise ValueError("Координаты вне EPSG:4326; исходная CRS не определена")
    return geom


class EnrichInput(BaseModel):
    osm_type: Literal["node", "way", "relation"]
    osm_id: int = Field(gt=0, strict=True)
    geometry: dict | None = None
    coordinates: tuple[float, float] | None = None  # longitude, latitude
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("coordinates")
    @classmethod
    def valid_coordinates(cls, value):
        if value is not None and not (-180 <= value[0] <= 180 and -85 <= value[1] <= 85):
            raise ValueError("Ожидаются конечные longitude/latitude в EPSG:4326")
        return value

    @model_validator(mode="after")
    def valid_geometry(self):
        if self.geometry is None:
            if self.coordinates is None:
                raise ValueError("Передайте геометрию или координаты объекта")
        else:
            try:
                geom = wgs84_geometry(self.geometry)
                if geom.geom_type not in {"Point", "Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
                    raise ValueError("Тип геометрии не поддерживается")
                self.geometry = json.loads(json.dumps(mapping(geom)))
            except Exception as exc:
                raise ValueError(f"Некорректная геометрия OSM: {exc}") from exc
        return self


class EnrichResponse(BaseModel):
    status: Literal["matched", "multiple_matches", "not_found", "unavailable", "error"]
    source: Literal["NSPD"] = "NSPD"
    retrieved_at: str | None = None
    cache_hit: bool = False
    candidates: list[dict] = Field(default_factory=list)
    selected_candidate: str | None = None
    warnings: list[str] = Field(default_factory=list)
    search_method: str | None = None
    error_code: str | None = None
    message: str | None = None


def first(options, *keys):
    return next((options[k] for k in keys if options.get(k) is not None and options[k] != ""), None)


def candidate(feature, source):
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError("Неполный объект НСПД")
    props = feature.get("properties")
    if not isinstance(props, dict) or not isinstance(props.get("options"), dict):
        raise ValueError("Отсутствуют характеристики НСПД")
    options = props["options"]
    json.dumps(options, allow_nan=False)
    category = props.get("category")
    if category not in CATEGORIES:
        raise ValueError("Неожиданная категория НСПД")
    number = first(options, "cad_num", "cad_number")
    if not isinstance(number, str) or not number.strip():
        raise ValueError("Отсутствует кадастровый номер НСПД")
    raw_geom = feature.get("geometry")
    if isinstance(raw_geom, dict) and raw_geom.get("crs") is None and feature.get("crs") is not None:
        raw_geom = {**raw_geom, "crs": feature["crs"]}
    # An absent contour is different from a broken contour. Neither proves a match.
    geom = wgs84_geometry(raw_geom) if raw_geom is not None else None
    intersection = None
    relation = "unverified"
    basis = "НСПД вернула кандидата; геометрия отсутствует, совпадение не подтверждено"
    if geom is not None:
        if source.geom_type == "Point":
            if not geom.covers(source):
                return None
            relation, basis = "point_covered", "Геометрия НСПД покрывает точку OSM"
        else:
            context = MetricContext(source)
            metric = transform(geom, context.forward.transform, interleaved=False)
            if geom.geom_type not in {"Polygon", "MultiPolygon"}:
                if not context.source.intersects(metric):
                    return None
                relation = "geometry_intersection"
                basis = "Геометрия НСПД пересекает контур OSM, но полигон НСПД отсутствует; требуется проверка"
            else:
                area = context.source.intersection(metric).area
                if area <= 0:
                    return None
                ratio = area / context.source.area
                intersection = {"area_m2": round(area, 3), "osm_fraction": min(1., ratio),
                                "candidate_fraction": min(1., area / metric.area) if metric.area else None,
                                "metric_crs": context.crs.to_string()}
                relation, basis = "polygon_overlap", "Пересечение полигонов OSM и НСПД; это не подтверждение юридической принадлежности"
    is_land = category == 36368
    characteristics = {
        "object_kind": first(options, "land_record_type", "build_record_type_value", "object_type_value", "object_under_construction_record_record_type_value"),
        "area_m2": first(options, "specified_area", "declared_area", "area") if is_land else first(options, "build_record_area", "params_area"),
        "address": first(options, "readable_address", "address_readable_address"),
        "cadastral_cost_rub": first(options, "cost_value"),
        "status": first(options, "status", "object_previously_posted", "common_data_status"),
    }
    if is_land:
        characteristics.update(land_category=first(options, "land_record_category_type"),
                               permitted_use=first(options, "permitted_use_established_by_document"))
    else:
        characteristics.update(purpose=first(options, "purpose", "params_purpose"),
                               name=first(options, "building_name", "params_name", "object_under_construction_record_name"),
                               floors=first(options, "floors", "params_floors"),
                               year_built=first(options, "year_built", "params_year_built"))
    return {"id": f"{category}:{number}", "cadastral_number": number,
            "category_id": category, "object_type": CATEGORIES[category],
            "characteristics": characteristics, "raw_options": options,
            "geometry": mapping(geom) if geom is not None else None,
            "geometry_crs": "EPSG:4326" if geom is not None else None,
            "source_crs": (raw_geom.get("crs") or {}).get("properties", {}).get("name", "EPSG:4326 (GeoJSON)") if raw_geom else None,
            "match_basis": basis, "spatial_relation": relation, "intersection": intersection}


class CadastreService:
    def __init__(self, gateway=None, cache_dir=None, ttl=None):
        self.gateway = gateway or NspdGateway(timeout=int(os.getenv("NSPD_TIMEOUT_SECONDS", "12")),
                                              ca_file=os.getenv("NSPD_CA_FILE"))
        self.directory = Path(cache_dir) if cache_dir is not None else Path(os.getenv("CACHE_DIR", str(default_cache_dir()))) / "NSPD"
        self.ttl = max(0, int(os.getenv("NSPD_CACHE_TTL_SECONDS", "86400"))) if ttl is None else ttl
        self.lock = threading.Lock()
        self.next_allowed = 0.

    def key(self, payload):
        # Includes identity, canonical geometry/coordinates, tags and query schema.
        body = ["nspd-enrich-v1", "https://nspd.gov.ru", sorted(CATEGORIES), payload.model_dump()]
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def read_cache(self, key, *, max_bytes=None):
        path = self.directory / f"{key}.json"
        try:
            if max_bytes is None:
                saved = json.loads(path.read_text(encoding="utf-8"))
            else:
                with path.open("rb") as stream:
                    body = stream.read(max_bytes + 1)
                if len(body) > max_bytes:
                    return None
                saved = json.loads(body)
            if 0 <= time.time() - saved["saved_at"] < self.ttl and isinstance(saved["result"], dict):
                return saved["result"]
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass
        return None

    def write_cache(self, key, result):
        path = self.directory / f"{key}.json"
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"saved_at": time.time(), "result": result}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(path)
        except (OSError, ValueError):
            result["warnings"].append("Не удалось записать локальный кеш НСПД.")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def enrich(self, payload: EnrichInput):
        if not self.lock.acquire(blocking=False):
            return EnrichResponse(status="unavailable", error_code="busy", message="Уже выполняется запрос кадастровых сведений. Дождитесь его завершения.").model_dump()
        try:
            return self._enrich(payload)
        finally:
            self.lock.release()

    def _enrich(self, payload):
        source = wgs84_geometry(payload.geometry) if payload.geometry else Point(payload.coordinates)
        if source.geom_type not in {"Point", "Polygon", "MultiPolygon"}:
            return EnrichResponse(status="unavailable", error_code="unsupported_geometry", message="Поиск для линейной геометрии пока не поддерживается. Контур не заменялся центром или буфером.").model_dump()
        # A single object, no recursive subdivision or territory scanning.
        if source.geom_type != "Point" and MetricContext(source).source.area > 10_000_000:
            return EnrichResponse(status="unavailable", error_code="contour_too_large", message="Контур превышает 10 км². Поиск территории и разбиение на массовые запросы отключены.").model_dump()
        key = self.key(payload)
        saved = self.read_cache(key)
        try:
            if saved is not None:
                result = EnrichResponse.model_validate(saved)
                if result.status in {"matched", "multiple_matches", "not_found"}:
                    return result.model_copy(update={"cache_hit": True}).model_dump()
        except ValueError:
            pass
        if time.monotonic() < self.next_allowed:
            return EnrichResponse(status="unavailable", error_code="cooldown", message=f"Повторите запрос через {int(self.next_allowed - time.monotonic()) + 1} с.").model_dump()
        self.next_allowed = time.monotonic() + 2
        try:
            features, method, warnings = self.gateway.search(source)
            candidates = {}
            for feature in features:
                item = candidate(feature, source)
                if item is not None:
                    if item["id"] in candidates and candidates[item["id"]] != item:
                        raise ValueError("Противоречивые дубликаты в ответе НСПД")
                    candidates[item["id"]] = item
            candidates = list(candidates.values())
            uncertain = any(c["spatial_relation"] in {"unverified", "geometry_intersection"} for c in candidates)
            if uncertain:
                warnings.append("У части кандидатов отсутствует геометрия или полигон НСПД. Соответствие контуров не подтверждено; требуется выбор и проверка пользователем.")
            warnings.append("OSM и кадастр описывают разные сущности. ВРИ участка не присваивается зданию и не определяет деятельность организации.")
            status = "multiple_matches" if len(candidates) > 1 or uncertain else "matched" if candidates else "not_found"
            result = EnrichResponse(status=status, retrieved_at=datetime.now(timezone.utc).isoformat(),
                                    candidates=candidates, selected_candidate=candidates[0]["id"] if status == "matched" else None,
                                    warnings=warnings, search_method=method,
                                    message="Кадастровые сведения не найдены" if not candidates else None).model_dump()
        except NspdFailure as exc:
            if exc.retry_after:
                self.next_allowed = time.monotonic() + exc.retry_after
            return EnrichResponse(status="unavailable", error_code=exc.code, message=str(exc)).model_dump()
        except (ValueError, TypeError, KeyError, OverflowError, CRSError, ProjError, GEOSException) as exc:
            logging.warning("Invalid NSPD response: %s", exc)
            return EnrichResponse(status="error", error_code="invalid_response", message="НСПД вернула неполные характеристики или невалидную геометрию/CRS. Отсутствие объекта не установлено.").model_dump()
        except OSError:
            return EnrichResponse(status="unavailable", error_code="tls_configuration", message="Не удалось загрузить доверенные сертификаты НСПД. Проверьте комплектность app/certs и NSPD_CA_FILE, если он задан.").model_dump()
        self.write_cache(key, result)
        return result
