"""Convert Overpass full geometry to Shapely. Never substitute object centers."""
from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize_full, unary_union


class GeometryError(ValueError):
    pass


def coordinates(items: list) -> list[tuple[float, float]]:
    if not items or any("lon" not in p or "lat" not in p for p in items):
        raise GeometryError("Неполная геометрия")
    result = [(float(p["lon"]), float(p["lat"])) for p in items]
    if any(not (-180 <= x <= 180 and -90 <= y <= 90) for x, y in result):
        raise GeometryError("Некорректные координаты")
    return result


def polygonal(geometry):
    valid = make_valid(geometry)
    if valid.geom_type in {"Polygon", "MultiPolygon"} and not valid.is_empty:
        return valid
    raise GeometryError("Некорректный полигон")


def rings(lines: list):
    if not lines:
        raise GeometryError("Нет внешних колец")
    polygons, cuts, dangles, invalid = polygonize_full(lines)
    if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty or polygons.is_empty:
        raise GeometryError("Незамкнутые кольца relation")
    return unary_union(polygons)


def element_geometry(element: dict):
    kind = element["type"]
    tags = element.get("tags", {})
    if kind == "node":
        return Point(coordinates([element])[0])
    if kind == "way":
        coords = coordinates(element.get("geometry", []))
        if len(coords) < 2:
            raise GeometryError("Недостаточно точек")
        # Closed linear infrastructure remains a line unless explicitly area=yes.
        linear = any(k in tags for k in ("highway", "railway", "barrier")) or tags.get("power") in {"line", "minor_line"}
        area = tags.get("area") != "no" and (not linear or tags.get("area") == "yes")
        if coords[0] == coords[-1] and len(coords) >= 4 and area:
            return polygonal(Polygon(coords))
        return LineString(coords)
    if kind == "relation" and tags.get("type") in {"multipolygon", "boundary"}:
        outer, inner = [], []
        for member in element.get("members", []):
            role = member.get("role", "")
            if role not in {"outer", "inner", ""}:
                continue
            if member["type"] == "node":
                continue  # label/admin_centre nodes do not define polygon rings.
            if member["type"] != "way":
                raise GeometryError("Вложенные relations не поддерживаются")
            coords = coordinates(member.get("geometry", []))
            if len(coords) < 2:
                raise GeometryError("Неполная геометрия участника")
            (inner if role == "inner" else outer).append(LineString(coords))
        outside = rings(outer)
        if inner:
            holes = rings(inner)
            if not outside.covers(holes):
                raise GeometryError("Внутреннее кольцо вне внешнего")
            outside = outside.difference(holes)
        return polygonal(outside)
    raise GeometryError("Тип relation не поддерживается")
