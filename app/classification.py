"""Explicit OSM tag rules. Names never participate in classification."""

CATEGORIES = {
    "building": {"label": "Здания", "color": "#64748b"},
    "industrial": {"label": "Промышленность", "color": "#9866c4"},
    "residential": {"label": "Жилая среда", "color": "#d49a30"},
    "commercial": {"label": "Коммерция", "color": "#3585bd"},
    "education": {"label": "Образование", "color": "#d37048"},
    "healthcare": {"label": "Медицина", "color": "#cf6685"},
    "recreation": {"label": "Отдых и озеленение", "color": "#45966a"},
    "fuel": {"label": "АЗС", "color": "#b6814c"},
    "infrastructure": {"label": "Инфраструктура", "color": "#4d929d"},
}

# First matching rule wins. Specific functions precede general building/landuse.
# (category, Russian type, key, accepted values; None means any non-negative tag)
RULES = [
    ("education", "Образовательное учреждение", "amenity", {"school", "kindergarten", "college", "university", "music_school", "language_school", "training"}),
    ("education", "Образовательное здание", "building", {"school", "kindergarten", "college", "university"}),
    ("education", "Образовательная территория", "landuse", {"education"}),
    ("healthcare", "Медицинское учреждение", "amenity", {"hospital", "clinic", "doctors", "dentist", "pharmacy", "nursing_home"}),
    ("healthcare", "Медицинское учреждение", "healthcare", None),
    ("healthcare", "Медицинское здание", "building", {"hospital"}),
    ("recreation", "Парк / зона отдыха", "leisure", {"park", "garden", "playground", "recreation_ground", "sports_centre", "stadium", "pitch", "nature_reserve"}),
    ("recreation", "Озеленённая / рекреационная территория", "landuse", {"recreation_ground", "village_green", "forest"}),
    ("fuel", "АЗС", "amenity", {"fuel"}),
    ("industrial", "Промышленная территория", "landuse", {"industrial"}),
    ("industrial", "Промышленный объект", "industrial", None),
    ("industrial", "Промышленный объект", "man_made", {"works"}),
    ("industrial", "Промышленное / складское здание", "building", {"industrial", "warehouse"}),
    ("residential", "Жилая территория", "landuse", {"residential"}),
    ("residential", "Жилое здание", "building", {"residential", "apartments", "house", "detached", "semidetached_house", "terrace", "dormitory", "bungalow"}),
    ("commercial", "Коммерческая территория", "landuse", {"commercial", "retail"}),
    ("commercial", "Торговый объект", "shop", None),
    ("commercial", "Офис", "office", None),
    ("commercial", "Объект торговли / услуг", "amenity", {"restaurant", "cafe", "fast_food", "bar", "pub", "food_court", "bank", "atm", "marketplace", "car_wash", "car_rental"}),
    ("commercial", "Коммерческое здание", "building", {"commercial", "retail", "office", "supermarket", "kiosk"}),
    ("infrastructure", "Объект электроснабжения", "power", {"substation", "plant", "generator", "tower", "pole", "line", "minor_line"}),
    ("infrastructure", "Инженерная инфраструктура", "man_made", {"water_tower", "wastewater_plant", "water_works", "pumping_station", "tower", "communications_tower", "chimney", "storage_tank"}),
    ("infrastructure", "Объект инфраструктуры", "amenity", {"parking", "parking_entrance", "charging_station", "bus_station", "recycling", "waste_disposal", "waste_transfer_station"}),
    ("infrastructure", "Пожарная станция", "amenity", {"fire_station"}),
    ("infrastructure", "Транспортная инфраструктура", "highway", {"bus_stop", "services", "rest_area"}),
    ("infrastructure", "Железнодорожная инфраструктура", "railway", {"station", "halt", "tram_stop"}),
    ("infrastructure", "Общественный транспорт", "public_transport", {"platform", "station"}),
    ("building", "Здание · назначение по тегу building", "building", None),
]


def classify(tags: dict) -> dict | None:
    for category, label, key, values in RULES:
        value = tags.get(key)
        if not value or value in {"no", "false", "0"}:
            continue
        if values is None or value in values:
            if key == "building" and category == "building":
                label = ("Здание · назначение не указано" if value == "yes"
                         else f"Здание · building={value}")
            return {"category": category, "type": label,
                    "classification_basis": f"{key}={value}"}
    return None


def overpass_filters() -> list[str]:
    """Use the same rule table for retrieval and classification."""
    filters = []
    any_value_keys = {key for _, _, key, values in RULES if values is None}
    for _, _, key, values in RULES:
        if key in any_value_keys and values is not None:
            continue
        expression = (f'["{key}"]' if values is None else
                      f'["{key}"~"^({"|".join(sorted(values))})$"]')
        if expression not in filters:
            filters.append(expression)
    return filters
