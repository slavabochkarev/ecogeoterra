"""Search NSPD protected areas from a selected WGS 84 reference point."""

from datetime import date, datetime, timezone
from math import isfinite
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from shapely import get_num_coordinates, transform
from shapely.affinity import translate
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import nearest_points

from .cadastre_research import MAX_WAIT_SECONDS, ResearchBudget, ResearchLimits, request_nspd
from .geo import MetricContext
from .nspd import OOPT_CATEGORY, NspdFailure

WEB_MERCATOR = CRS.from_epsg(3857)
MERCATOR_LIMIT = 20_037_509
POLYGONS = ("Polygon", "MultiPolygon")
# The accompanying registry record returned for the same number in theme 5.
OOPT_REGISTRY_CATEGORY = 36948
BOUNDARY_NUMBER = r"^[0-9]{1,3}:[0-9]{1,3}-[0-9]{1,4}\.[0-9]{1,18}$"


class OoptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lat: float = Field(ge=-85.05112878, le=85.05112878, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    diameter_km: Literal[10, 40, 100, 200] = 40

    @property
    def radius_m(self):
        return self.diameter_km * 500


def circle_request(context, circle, longitude):
    geographic = transform(circle, context.inverse.transform, interleaved=False)
    ring = [(longitude + (lon - longitude + 180) % 360 - 180, lat)
            for lon, lat in geographic.exterior.coords]
    unwrapped = Polygon(ring)
    west, _, east, _ = unwrapped.bounds
    if west >= -180 and east <= 180:
        return unwrapped

    # Intersect the ring in continuous longitude before wrapping its pieces.
    seam = -180 if west < -180 else 180
    center = unwrapped.intersection(box(-180, -90, 180, 90))
    outer = unwrapped.intersection(box(seam - 360, -90, seam, 90) if west < -180
                                   else box(seam, -90, seam + 360, 90))
    wrapped = translate(outer, xoff=360 if west < -180 else -360)
    return MultiPolygon([part for part in (center, wrapped) if part.area > 0])


def read_geometry(raw):
    if not isinstance(raw, dict) or raw.get("type") not in POLYGONS:
        raise ValueError("Ожидается площадная геометрия ООПТ")
    crs = raw.get("crs")
    if not isinstance(crs, dict) or crs.get("type") != "name":
        raise ValueError("Не указана CRS геометрии ООПТ")
    properties = crs.get("properties")
    if not isinstance(properties, dict) or not WEB_MERCATOR.equals(CRS.from_user_input(properties.get("name"))):
        raise ValueError("Ожидается CRS EPSG:3857")
    geometry = shape(raw)
    if geometry.is_empty or not geometry.is_valid or not all(
        isfinite(value) and abs(value) <= MERCATOR_LIMIT for value in geometry.bounds
    ):
        raise ValueError("Некорректный контур ООПТ")
    # Keep long source edges accurate after projection while bounding inserted vertices.
    segment = max(50, geometry.length / max(1, 100_000 - get_num_coordinates(geometry)))
    return geometry.segmentize(segment)


def text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def record_date(value, *, document=False):
    if not isinstance(value, str):
        return None
    try:
        if document and "." in value:
            return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def zone_attributes(properties, options, number):
    name = next((label for value in (options.get("name_by_doc"), options.get("name"), properties.get("name"))
                 if (label := text(value, 1000)) and label != number and not re.fullmatch(BOUNDARY_NUMBER, label)),
                None)
    system = properties.get("systemInfo")
    updated = system.get("updated") if isinstance(system, dict) else None
    try:
        updated = datetime.fromisoformat(updated).isoformat() if isinstance(updated, str) else None
    except ValueError:
        updated = None
    district = text(options.get("cadastral_district"), 32)
    district_code = properties.get("cadastralDistrictsCode")
    if not district and type(district_code) is int and district_code > 0:
        district = str(district_code)
    doc = {
        "name": text(options.get("legal_act_document_name"), 2000),
        "issuer": text(options.get("legal_act_document_issuer"), 1000),
        "number": text(options.get("legal_act_document_number"), 100),
        "date": record_date(options.get("legal_act_document_date"), document=True),
    }
    attrs = {
        "name": name, "layer_name": text(properties.get("categoryName"), 200),
        "boundary_type": text(options.get("type_boundary_value"), 300),
        "zone_type": text(options.get("type_zone"), 300),
        "cadastral_district": district,
        "registration_date": record_date(options.get("registration_date")),
        "updated_at": updated,
        "old_account_number": text(options.get("old_account_number"), 100),
        "document": {key: value for key, value in doc.items() if value},
        "restrictions": text(options.get("content_restrict_encumbrances"), 64001),
    }
    if attrs["restrictions"] and len(attrs["restrictions"]) > 64000:
        attrs["restrictions"] = attrs["restrictions"][:64000] + "…"
    return {key: value for key, value in attrs.items() if value}


def display_polygon(geometry):
    if geometry.geom_type in POLYGONS:
        return geometry
    parts = [part for item in getattr(geometry, "geoms", ())
             for part in (item.geoms if item.geom_type == "MultiPolygon" else (item,))
             if part.geom_type == "Polygon"]
    return MultiPolygon(parts) if parts else None


def analyze_oopt(payload, context, circle, features, budget):
    converter = Transformer.from_crs(WEB_MERCATOR, context.crs, always_xy=True)
    origin = context.source
    zones, contours, conflicts = {}, {}, set()
    skipped = 0
    for feature in features:
        budget.check()
        try:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError("Некорректная запись ООПТ")
            properties = feature.get("properties")
            if not isinstance(properties, dict) or properties.get("category") != OOPT_CATEGORY:
                raise ValueError("Некорректная категория ООПТ")
            options = properties.get("options")
            number = text(options.get("reg_numb_border"), 100) if isinstance(options, dict) else None
            if not number:
                raise ValueError("Отсутствует реестровый номер ООПТ")

            native = read_geometry(feature.get("geometry"))
            metric = transform(native, converter.transform, interleaved=False)
            if not metric.is_valid or not metric.intersects(circle):
                raise ValueError("Контур ООПТ не пересекает область или некорректен в AEQD")
            if number in contours:
                if not native.equals(contours[number]):
                    conflicts.add(number)
                continue
            contours[number] = native
            clipped = metric.intersection(circle)
            boundary = nearest_points(origin, metric.boundary)[1]
            marker = clipped.representative_point()
            polygon = display_polygon(clipped)
            attributes = zone_attributes(properties, options, number)
            zones[number] = {
                "registry_number": number,
                "name": attributes.pop("name", None),
                "attributes": attributes,
                "distance_m": metric.distance(origin),
                "boundary_distance_m": origin.distance(boundary),
                "inside": metric.contains(origin),
                "nearest_boundary": [boundary.x, boundary.y],
                "marker": [marker.x, marker.y],
                "geometry": mapping(polygon.simplify(1)) if polygon is not None else None,
            }
        except (GEOSException, ProjError, TypeError, ValueError, KeyError, OverflowError):
            skipped += 1

    warnings = []
    if skipped:
        warnings.append(f"Пропущено записей с некорректными данными: {skipped}.")
    if conflicts:
        warnings.append(f"Исключено номеров с разными контурами в одном ответе: {len(conflicts)}.")
    budget.check()
    return {
        "center": {"lat": payload.lat, "lon": payload.lon},
        "diameter_km": payload.diameter_km, "radius_m": payload.radius_m,
        "projection": context.crs.to_string(), "source_crs": "EPSG:3857",
        "category": OOPT_CATEGORY, "source": "НСПД",
        "coverage": "partial" if warnings else "unknown", "legal_status": "unverified",
        "warnings": warnings,
        "zones": sorted((value for key, value in zones.items() if key not in conflicts),
                        key=lambda zone: (zone["distance_m"], zone["registry_number"])),
    }


def research_oopt(payload, cadastre):
    budget = ResearchBudget(ResearchLimits())
    context = MetricContext(Point(payload.lon, payload.lat))
    circle = context.source.buffer(payload.radius_m, quad_segs=90)
    geographic = circle_request(context, circle, payload.lon)
    if not cadastre.lock.acquire(timeout=min(MAX_WAIT_SECONDS, budget.remaining())):
        raise NspdFailure("Сервис НСПД занят. Повторите запрос позже.", "busy")
    try:
        features = request_nspd(cadastre, budget, cadastre.gateway.search_oopt, geographic)
    finally:
        cadastre.lock.release()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    return {**analyze_oopt(payload, context, circle, features, budget), "retrieved_at": retrieved_at}


def oopt_details(number, cadastre):
    budget = ResearchBudget(ResearchLimits())
    if not cadastre.lock.acquire(timeout=min(MAX_WAIT_SECONDS, budget.remaining())):
        raise NspdFailure("Сервис НСПД занят. Повторите запрос позже.", "busy")
    try:
        features = request_nspd(cadastre, budget, cadastre.gateway.search_oopt_details, number)
    finally:
        cadastre.lock.release()
    details = None
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict) or properties.get("category") != OOPT_REGISTRY_CATEGORY:
            continue
        options = properties.get("options")
        if not isinstance(options, dict) or options.get("reg_numb_border") != number:
            continue
        attrs = zone_attributes(properties, options, number)
        if details is not None and attrs != details:
            raise NspdFailure("НСПД вернула разные сведения для одного номера.", "incomplete_response")
        details = attrs
    budget.check()
    return {"registry_number": number, "details": details,
            "retrieved_at": datetime.now(timezone.utc).isoformat()}
