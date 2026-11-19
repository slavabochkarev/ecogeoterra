from collections import Counter
import os
from pathlib import Path
import sys
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from shapely.errors import GEOSException
from shapely.geometry import Point, mapping

from .classification import CATEGORIES, classify
from .cadastre import CadastreService, EnrichInput, EnrichResponse
from .cadastre_experiment import CadastreExperiment, CadastralNumber, ExperimentInput, SourceCandidatesInput
from .cadastre_research import CadastreResearch, CadastreResearchInput
from .geo import MetricContext
from .osm import GeometryError, element_geometry
from .nspd import NspdFailure
from .research_api import router as research_router
from .services import ServiceError, fetch_objects, geocode

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="ЭкоГеоТерра v0.2", version="0.2.0")
app.include_router(research_router)
cadastre = CadastreService()
cadastre_experiment = CadastreExperiment(cadastre)
cadastre_research = CadastreResearch(cadastre_experiment)


@app.post("/api/cadastre/enrich", response_model=EnrichResponse)
def enrich_cadastre(payload: EnrichInput):
    return cadastre.enrich(payload)


@app.get("/cadastre-experiment")
def cadastre_experiment_page():
    return FileResponse(STATIC / "cadastre-experiment.html")


def esri_api_key():
    configured = os.environ.get("ESRI_API_KEY")
    if configured is not None:
        return configured.strip()

    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else STATIC.parents[1]
    try:
        lines = (root / ".env").read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return ""
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and name.strip() == "ESRI_API_KEY":
            return value.strip().strip("\"'")
    return ""


@app.get("/api/cadastre-experiment/basemaps")
def experiment_basemaps():
    return JSONResponse(
        {"esriApiKey": esri_api_key() or None},
        headers={"Cache-Control": "no-store", "Cross-Origin-Resource-Policy": "same-origin"},
    )


@app.get("/api/cadastre-experiment/source")
def experiment_source(number: CadastralNumber):
    return cadastre_experiment.execute(number)


@app.post("/api/cadastre-experiment/research")
def experiment_research(payload: ExperimentInput):
    return cadastre_experiment.execute(payload.number, research=True)


@app.post("/api/cadastre/source-candidates")
def source_candidates(payload: SourceCandidatesInput):
    return cadastre_experiment.find_source_candidates(payload)


@app.get("/api/cadastre/capabilities")
def cadastre_capabilities():
    return cadastre_research.capabilities()


def research_error(status, code, message, *, retry_after=None):
    return JSONResponse(status_code=status, content={
        "detail": {"code": code, "message": message},
        "coverage": "partial" if code == "incomplete_response" else "failed",
        "request_id": uuid4().hex,
    }, headers={"Retry-After": str(retry_after)} if retry_after else None)


@app.post("/api/cadastre/research", response_class=JSONResponse)
def research_cadastre(payload: CadastreResearchInput):
    try:
        return Response(cadastre_research.execute(payload), media_type="application/json")
    except NspdFailure as exc:
        code = {"http_403": "upstream_forbidden", "http_429": "cooldown", "not_found": "source_not_found",
                "connection_error": "upstream_unavailable", "tls_verification_failed": "tls_configuration"}.get(exc.code, exc.code)
        if code.startswith("http_5"):
            code = "upstream_unavailable"
        status = {"source_not_found": 404, "missing_contour": 422, "radius_disabled": 422,
                  "request_budget_exceeded": 422, "busy": 409, "source_changed": 409,
                  "upstream_forbidden": 403, "cooldown": 429, "tls_configuration": 503,
                  "upstream_unavailable": 503, "deadline_exceeded": 504}.get(code, 502)
        return research_error(status, code, str(exc), retry_after=exc.retry_after)


@app.exception_handler(RequestValidationError)
async def research_validation_error(request: Request, exc: RequestValidationError):
    if request.scope.get("endpoint") is research_cadastre:
        return research_error(422, "invalid_input", "Укажите кадастровый номер и радиус 500, 1000 или 2000 м.")
    return await request_validation_exception_handler(request, exc)


class ResearchInput(BaseModel):
    lat: float = Field(ge=-85, le=85, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    radius: Literal[500, 1000, 2000] = 2000
    source_label: str = Field(default="Точка предприятия", max_length=1000)


@app.exception_handler(ServiceError)
def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/config")
def config():
    return {"categories": CATEGORIES}


@app.get("/api/geocode")
def search_address(q: str = Query(min_length=3, max_length=500)):
    if len(q.strip()) < 3:
        return JSONResponse(status_code=422, content={"detail": "Введите хотя бы три символа адреса."})
    return geocode(q)


def analyze(payload: ResearchInput, raw: dict, cache: dict) -> dict:
    context = MetricContext(Point(payload.lon, payload.lat))
    features, seen, skipped = [], set(), Counter()
    for element in raw["elements"]:
        osm_type, osm_id = element.get("type"), element.get("id")
        key = f"{osm_type}/{osm_id}"
        if key in seen:
            continue
        seen.add(key)
        tags = element.get("tags", {})
        classification = classify(tags)
        if not classification:
            continue
        try:
            geometry = element_geometry(element)
            measurement = context.measure(geometry)
        except (GeometryError, GEOSException, KeyError, ValueError, TypeError, OverflowError):
            skipped["geometry"] += 1
            continue
        if measurement["distance_m"] > payload.radius:
            continue
        properties = {"osm_type": osm_type, "osm_id": osm_id, "osm_key": key,
                      "name": tags.get("name:ru") or tags.get("name") or "Без названия",
                      "has_name": bool(tags.get("name:ru") or tags.get("name")),
                      **classification, **measurement, "tags": tags}
        features.append({"type": "Feature", "id": key, "geometry": mapping(geometry), "properties": properties})
    features.sort(key=lambda f: (f["properties"]["distance_m"], f["id"]))
    counts = Counter(f["properties"]["category"] for f in features)
    warnings = []
    if skipped["geometry"]:
        warnings.append(f"Пропущено объектов с неполной или неподдерживаемой геометрией: {skipped['geometry']}. Центры вместо геометрии не использовались.")
    if cache.get("warning"):
        warnings.append(cache["warning"])
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"source": payload.model_dump(), "total": len(features),
                         "counts": {key: counts[key] for key in CATEGORIES},
                         "cache": cache, "osm_base_timestamp": raw.get("osm3s", {}).get("timestamp_osm_base"),
                         "projection": context.crs.to_string(), "distance_method": "nearest_geometry_point_local_aeqd_meters",
                         "warnings": warnings, "skipped_geometry": skipped["geometry"],
                         "attribution": "© OpenStreetMap contributors, ODbL 1.0",
                         "disclaimer": "OSM не является официальным кадастровым источником. Классификация по тегам не определяет юридический ВРИ."}}


@app.post("/api/research")
def research(payload: ResearchInput):
    raw, cache = fetch_objects(payload.lat, payload.lon, payload.radius)
    return analyze(payload, raw, cache)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/map")
def map_first_page():
    return FileResponse(STATIC / "map-first" / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
