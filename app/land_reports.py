"""Report rows derived from one committed cadastral result."""

from functools import lru_cache
import json
from math import isfinite
from pathlib import Path
import re
from unicodedata import normalize

from pyproj import Geod
from pyproj.exceptions import ProjError
from shapely import prepare, transform
from shapely.errors import GEOSException
from shapely.geometry import shape

from .cadastre_experiment import buffer_geometry
from .geo import DIRECTIONS
from .research_store import ResearchError

_RULES = Path(__file__).with_name("static") / "map-first" / "land-use-rules.v1.json"
_GEOD = Geod(ellps="WGS84")
_CODE = r"[0-9]+\.[0-9]+(?:\.[0-9]+)?"


def _key(value):
    value = normalize("NFKC", value).casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", value.translate(str.maketrans("‐‑–—", "----"))).strip(" .")


@lru_cache(maxsize=1)
def _rules():
    data = json.loads(_RULES.read_text(encoding="utf-8"))
    by_code, by_name = {}, {}

    def register(entries, status, label):
        for code, name in entries.items():
            key = _key(name)
            if not re.fullmatch(_CODE, code) or code in by_code or key in by_name:
                raise ValueError("Повтор или некорректный код ВРИ")
            by_code[code] = by_name[key] = (code, status, label)

    for group in data["groups"]:
        register(group["entries"], "matched", group["label"])
    for rule in data["review"]:
        register(rule["entries"], "review", rule["reason"])
    register(data["outside_selection"], "outside", None)
    return data["version"], by_code, by_name


def _classification(use, by_code, by_name):
    if use is None:
        return "review", "ВРИ отсутствует."
    key = _key(use)
    if record := by_name.get(key):
        return record[1:]
    if re.fullmatch(_CODE, key.removeprefix("код ")):
        if record := by_code.get(key.removeprefix("код ")):
            return record[1:]
    else:
        before = re.fullmatch(rf"(?:код )?({_CODE})\s*[-:]?\s+(.+)", key)
        after = re.fullmatch(rf"(.+)\s*\((?:код )?({_CODE})\)", key)
        if before or after:
            code, name = before.groups() if before else after.groups()[::-1]
            if (record := by_code.get(code)) and record == by_name.get(name.strip()):
                return record[1:]
            return "review", "Сочетание кода и наименования не подтверждено."
    return "review", "Точного правила для записи ВРИ нет."


def _polygon(geometry):
    polygon = shape(geometry)
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError("Некорректный площадной контур")
    return polygon


def _direction(origin, target):
    point = target.representative_point()
    bearing, _, length = _GEOD.inv(origin.x, origin.y, point.x, point.y)
    if not isfinite(bearing) or not isfinite(length) or length <= 0.001:
        return None
    return DIRECTIONS[int((bearing + 22.5) % 360 // 45)]


def _distance(measurement):
    relation = measurement.get("relation")
    value = measurement.get("distance_m")
    if relation in ("touches", "intersects"):
        return 0
    return value if relation == "disjoint" and value is not None and value >= 0 else None


def _row_order(row):
    distance = row["distance_m"]
    number = row["number"]
    return distance is None, distance or 0, tuple(map(int, number.split(":"))), number


def build_reports(document):
    research = document["research"]
    cadastre = document["cadastre"]
    metadata = {"title": research["title"], "source_number": research["number"], "revision": research["revision"]}
    if not cadastre or not cadastre["result"]:
        return {"research": metadata, "report": None}

    saved = cadastre["result"]
    try:
        _polygon(saved["buffer"]["geometry"])
        source = cadastre["source"]
        origin = _polygon(source["geometry"]).representative_point()
        context, region, _ = buffer_geometry(source, saved["radius_m"])
        prepare(region)
    except (GEOSException, ProjError, ValueError) as exc:
        raise ResearchError(422, "В сохранённой выборке повреждена геометрия исходного участка или буфера.") from exc
    try:
        version, by_code, by_name = _rules()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ResearchError(503, "Не удалось прочитать локальный справочник ВРИ.") from exc
    skipped = 0
    rows = {}
    for feature in saved["parcels"]["features"]:
        number = feature["id"]
        if number == metadata["source_number"] or number in rows:
            continue
        try:
            polygon = _polygon(feature["geometry"])
            metric = transform(polygon, context.forward.transform, interleaved=False)
            if metric.is_empty or not metric.is_valid:
                raise ValueError("Некорректный контур в метрической CRS")
            if not region.intersects(metric):
                continue
            direction = _direction(origin, polygon)
        except (GEOSException, ProjError, ValueError):
            skipped += 1
            continue
        props = feature["properties"]
        use = props.get("permitted_use") or ""
        rows[number] = {
            "number": number,
            "category": (props.get("land_category") or "").strip() or None,
            "use": use if use.strip() else None,
            "direction": direction,
            "distance_m": _distance(props.get("measurement") or {}),
        }

    all_rows = sorted(rows.values(), key=_row_order)
    selected = []
    counts = dict.fromkeys(("matched", "review", "outside"), 0)
    for row in all_rows:
        status, label = _classification(row["use"], by_code, by_name)
        counts[status] += 1
        if status != "outside":
            group = label if status == "matched" else f"Требует проверки — {label}"
            selected.append({**row, "group": group, "classification": status,
                             "review_reason": label if status == "review" else None})
    coverage = saved["coverage"]
    if skipped:
        coverage = "partial"
    elif coverage == "complete":
        coverage = "unknown"
    return {"research": metadata, "report": {
        "radius_m": saved["radius_m"], "retrieved_at": saved["retrieved_at"] or None,
        "coverage": coverage, "invalid_contours": skipped, "rules_version": version,
        "all": all_rows, "normalized": selected, "classification_counts": counts,
    }}
