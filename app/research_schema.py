"""The on-disk working state of a Map First research."""

from datetime import datetime
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .cadastre_experiment import CadastralNumber, NUMBER_ADAPTER
from .classification import CATEGORIES

SCHEMA_VERSION = 1
MIB = 1024 * 1024
FILE_LIMITS = {"research": 256 * 1024, "cadastre": 24 * MIB, "osm": 32 * MIB}
MAX_BODY = sum(FILE_LIMITS.values()) + MIB
MAX_FEATURES = 15_000
MAX_VERTICES = 500_000
Radius = Literal[500, 1000, 2000]
_SECRET_KEYS = {"apikey", "esriapikey", "token", "accesstoken", "refreshtoken", "password",
                "secret", "clientsecret", "authorization", "cookie", "credentials"}


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MapPoint(Schema):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class LabeledPoint(MapPoint):
    label: str | None = Field(default=None, max_length=1000)
    address: str | None = Field(default=None, max_length=1000)


class ReferencePoint(MapPoint):
    marker: bool = True


class Camera(MapPoint):
    zoom: float = Field(ge=0, le=19, allow_inf_nan=False)


class LayerFlags(Schema):
    boundaries: bool = True
    labels: bool = True
    fill: bool = False
    osm: bool = False


class WorkingState(Schema):
    camera: Camera = Field(default_factory=lambda: Camera(lat=55, lon=50, zoom=4))
    basemap: Literal["osm", "esri"] = "osm"
    tab: Literal["cadastre", "objects", "coordinates"] = "cadastre"
    panel_open: bool = False
    map_only: bool = False
    layers: LayerFlags = Field(default_factory=LayerFlags)
    cadastre_radius: Radius = 500
    osm_radius: Radius = 2000
    osm_categories: list[str] | None = Field(default=None, max_length=9)
    hide_unnamed: bool = False
    cadastre_selected: CadastralNumber | None = None
    osm_selected: str | None = Field(default=None, pattern=r"^(node|way|relation)/[0-9]{1,20}$")
    osm_origin: LabeledPoint | None = None
    location: LabeledPoint | None = None
    coordinates: ReferencePoint | None = None
    query: str = Field(default="", max_length=500)

    @field_validator("osm_categories")
    @classmethod
    def valid_categories(cls, values):
        if values is not None and (len(set(values)) != len(values) or any(value not in CATEGORIES for value in values)):
            raise ValueError("Неизвестные категории OSM")
        return values


class CadastreData(Schema):
    source: dict
    result: dict | None = None


class OsmData(Schema):
    result: dict
    requested_categories: list[str] = Field(default_factory=list, max_length=9)

    @field_validator("requested_categories")
    @classmethod
    def valid_categories(cls, values):
        return WorkingState.valid_categories(values)


class NewResearch(Schema):
    state: WorkingState = Field(default_factory=WorkingState)
    cadastre: CadastreData | None = None


class UpdateResearch(Schema):
    revision: int = Field(ge=1)
    state: WorkingState
    title: str | None = Field(default=None, min_length=1, max_length=600)
    cadastre: CadastreData | None = None
    osm: OsmData | None = None


class CloseResearch(Schema):
    revision: int = Field(ge=1)


class DatasetInfo(Schema):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=4, le=32 * MIB)
    present: bool
    radius_m: Radius | None = None
    retrieved_at: str | None = Field(default=None, max_length=64)


class Manifest(Schema):
    id: str
    title: str = Field(min_length=1, max_length=600)
    manual_title: bool
    address: str | None = Field(default=None, max_length=1000)
    number: CadastralNumber | None
    created_at: str = Field(max_length=64)
    updated_at: str = Field(max_length=64)
    schema_version: Literal[1]
    revision: int = Field(ge=1)
    state: WorkingState
    datasets: dict[Literal["cadastre", "osm"], DatasetInfo]

    @field_validator("created_at", "updated_at")
    @classmethod
    def date_with_zone(cls, value):
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Отсутствует часовой пояс даты")
        return value


def _required_dict(value):
    if not isinstance(value, dict):
        raise ValueError("Ожидается объект JSON")
    return value


def _number(value):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError("Ожидается конечное число")
    return value


def _text(value, limit=1000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Некорректное текстовое поле")
    return value


def _radius(value):
    if type(value) is not int or value not in (500, 1000, 2000):
        raise ValueError("Некорректный радиус")
    return value


def _warnings(values):
    if not isinstance(values, list) or len(values) > 50:
        raise ValueError("Некорректные предупреждения")
    return [_text(value, 4000) for value in values]


def _point(value, *, altitude=False):
    if not isinstance(value, list) or len(value) not in ((2, 3) if altitude else (2,)):
        raise ValueError("Некорректная координата")
    lon, lat, *_ = map(_number, value)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("Координата вне WGS 84")
    return value


def _geometry(value, budget, *, polygon=False):
    raw = _required_dict(value)
    kind = raw.get("type")
    depth = {"Point": 0, "MultiPoint": 1, "LineString": 1, "MultiLineString": 2,
             "Polygon": 2, "MultiPolygon": 3}.get(kind)
    if depth is None or (polygon and kind not in ("Polygon", "MultiPolygon")):
        raise ValueError("Некорректный тип геометрии")
    def visit(values, level):
        if not level:
            budget[0] += 1
            if budget[0] > MAX_VERTICES:
                raise ValueError("Выборка превышает предел числа координат")
            _point(values, altitude=True)
        elif not isinstance(values, list) or not values:
            raise ValueError("Пустой контур")
        else:
            if level == 1 and kind in ("Polygon", "MultiPolygon"):
                if len(values) < 4 or values[0] != values[-1]:
                    raise ValueError("Некорректное кольцо контура")
            if level == 1 and kind.endswith("LineString") and len(values) < 2:
                raise ValueError("Некорректная линия")
            for item in values:
                visit(item, level - 1)
    coordinates = raw.get("coordinates")
    visit(coordinates, depth)
    return {"type": kind, "coordinates": coordinates}


def _collection(value):
    data = _required_dict(value)
    features = data.get("features")
    if data.get("type") != "FeatureCollection" or not isinstance(features, list) or len(features) > MAX_FEATURES:
        raise ValueError("Некорректный размер или тип набора объектов")
    return features


def _feature(item, budget, *, polygon=False):
    raw = _required_dict(item)
    if raw.get("type") != "Feature":
        raise ValueError("Некорректный объект GeoJSON")
    return {"type": "Feature", "geometry": _geometry(raw.get("geometry"), budget, polygon=polygon)}


def _parcel(raw, budget):
    record = _feature(raw, budget, polygon=True)
    props = _required_dict(raw.get("properties"))
    number = NUMBER_ADAPTER.validate_python(raw.get("id"))
    if props.get("number") != number:
        raise ValueError("Номер участка расходится с геометрией")
    filtered = {"number": number}
    for field in ("address", "land_category", "permitted_use", "geometry_status", "category_bucket"):
        if props.get(field) is not None:
            filtered[field] = _text(props[field])
    if props.get("area_m2") is not None:
        filtered["area_m2"] = _number(props["area_m2"])
    if props.get("label_point") is not None:
        filtered["label_point"] = _point(props["label_point"])
    if props.get("measurement") is not None:
        measurement = _required_dict(props["measurement"])
        relation = measurement.get("relation")
        if relation not in ("source", "disjoint", "touches", "intersects", "undefined"):
            raise ValueError("Некорректное измерение")
        saved = {"relation": relation}
        distance = measurement.get("distance_m")
        if distance is not None:
            saved["distance_m"] = _number(distance)
        endpoints = measurement.get("endpoints")
        if endpoints is not None:
            if not isinstance(endpoints, list) or len(endpoints) != 2:
                raise ValueError("Некорректная линия измерения")
            saved["endpoints"] = [_point(point) for point in endpoints]
        filtered["measurement"] = saved
    return {**record, "id": number, "properties": filtered}


def clean_cadastre(data):
    if data is None:
        return None
    payload = CadastreData.model_validate(data)
    budget = [0]
    source = _parcel(payload.source, budget)
    raw = payload.result
    if raw is None:
        return {"source": source, "result": None}
    radius = _radius(raw.get("radius_m"))
    coverage = raw.get("coverage")
    if coverage not in ("complete", "unknown", "partial"):
        raise ValueError("Некорректная полнота кадастровой выборки")
    buffer = _feature(raw.get("buffer"), budget, polygon=True)
    if raw["buffer"].get("properties", {}).get("radius_m") != radius:
        raise ValueError("Радиус буфера не совпадает с радиусом результата")
    buffer["properties"] = {"radius_m": radius}
    features = [_parcel(item, budget) for item in _collection(raw.get("parcels"))]
    ids = {item["id"] for item in features}
    if source["id"] in ids or len(ids) != len(features):
        raise ValueError("Повтор кадастровых номеров в выборке")
    stats = _required_dict(raw.get("stats", {}))
    names = ("received", "valid_geometry", "mapped", "neighbors", "with_category", "without_category",
             "invalid_geometry", "invalid_record", "outside_buffer", "duplicate_neighbors", "geojson_prepare_ms")
    result = {"radius_m": radius, "coverage": coverage, "buffer": buffer,
              "parcels": {"type": "FeatureCollection", "features": features},
              "stats": {key: _number(stats[key]) for key in names if key in stats},
              "retrieved_at": _text(raw.get("retrieved_at", ""), 64), "warnings": _warnings(raw.get("warnings", []))}
    revision = raw.get("source_revision")
    if revision is not None:
        if not isinstance(revision, str) or len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision):
            raise ValueError("Некорректная ревизия контура")
        result["source_revision"] = revision
    coverage_details = raw.get("coverage_details")
    if isinstance(coverage_details, dict):
        result["coverage_details"] = {
            "reason": _text(coverage_details.get("reason", ""), 120),
            "confirmed": coverage_details.get("confirmed") is True,
            "records_skipped": _number(coverage_details.get("records_skipped", 0)),
        }
    return {"source": source, "result": result}


def _osm_feature(raw, budget):
    record = _feature(raw, budget)
    p = _required_dict(raw.get("properties"))
    kind, number = p.get("osm_type"), p.get("osm_id")
    if kind not in ("node", "way", "relation") or type(number) is not int or not 0 < number < 10**20:
        raise ValueError("Некорректный ID OSM")
    key = f"{kind}/{number}"
    if raw.get("id") != key or p.get("osm_key") != key or p.get("category") not in CATEGORIES:
        raise ValueError("Некорректная категория или ключ OSM")
    tags = _required_dict(p.get("tags", {}))
    if len(tags) > 256:
        raise ValueError("Слишком много тегов OSM")
    filtered = {field: _text(p.get(field, ""), 4096) for field in ("name", "category", "type", "classification_basis")}
    filtered.update(osm_type=kind, osm_id=number, osm_key=key, distance_m=_number(p.get("distance_m")),
                    has_name=p.get("has_name") is True, tags={
                        _text(tag, 256): _text(value, 4096) for tag, value in tags.items()
                        if tag.casefold().replace("_", "").replace("-", "") not in _SECRET_KEYS
                    })
    if p.get("direction") is not None:
        filtered["direction"] = _text(p["direction"], 40)
    if p.get("bearing_deg") is not None:
        filtered["bearing_deg"] = _number(p["bearing_deg"])
    if p.get("nearest_point") is not None:
        filtered["nearest_point"] = MapPoint.model_validate(p["nearest_point"]).model_dump()
    return {**record, "id": key, "properties": filtered}


def clean_osm(data):
    if data is None:
        return None
    payload = OsmData.model_validate(data)
    raw = payload.result
    meta = _required_dict(raw.get("metadata"))
    origin = _required_dict(meta.get("source"))
    point = MapPoint(lat=origin.get("lat"), lon=origin.get("lon")).model_dump()
    point.update(radius=_radius(origin.get("radius")), source_label=_text(origin.get("source_label", "")))
    budget = [0]
    features = [_osm_feature(item, budget) for item in _collection(raw)]
    if len({item["id"] for item in features}) != len(features):
        raise ValueError("Повтор ID в выборке OSM")
    cache = _required_dict(meta.get("cache", {}))
    counts = dict.fromkeys(CATEGORIES, 0)
    for item in features:
        counts[item["properties"]["category"]] += 1
    metadata = {"source": point, "total": len(features), "counts": counts,
                "cache": {"hit": cache.get("hit") is True, "fetched_at": _text(cache.get("fetched_at", ""), 64)},
                "warnings": _warnings(meta.get("warnings", []))}
    for key in ("osm_base_timestamp", "projection", "distance_method", "attribution", "disclaimer"):
        if meta.get(key) is not None:
            metadata[key] = _text(meta[key])
    if meta.get("skipped_geometry") is not None:
        metadata["skipped_geometry"] = _number(meta["skipped_geometry"])
    return {"result": {"type": "FeatureCollection", "features": features, "metadata": metadata},
            "requested_categories": payload.requested_categories}
