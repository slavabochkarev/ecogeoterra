import { isPosition } from "./cadastre-format.js";
import { formatCoordinates } from "./coordinates.js";
import { installOoptDetails } from "./oopt-details.js";
import { installOoptMap } from "./oopt-map.js";
import { request } from "./request.js";

const fmt = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const fmtKm = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const formatDistance = metres => fmt.format(metres) + " м (" + fmtKm.format(metres / 1000) + " км)";
const disclaimer = "Полнота выдачи и юридическая актуальность границ не подтверждены.";

export function parcelAnchor(feature) {
  if (!feature) return null;
  const point = feature.properties.label_point;
  return { kind: "cadastre", id: feature.id, label: feature.id,
    lat: isPosition(point) ? point[1] : null, lon: isPosition(point) ? point[0] : null };
}

export function osmAnchor(feature) {
  if (!feature) return null;
  const point = feature.geometry.type === "Point" && isPosition(feature.geometry.coordinates)
    ? { lat: feature.geometry.coordinates[1], lon: feature.geometry.coordinates[0] }
    : feature.properties.nearest_point;
  return { kind: "osm", id: feature.id, label: feature.properties.has_name ? feature.properties.name : feature.id,
    lat: point?.lat ?? null, lon: point?.lon ?? null };
}

export function coordinateAnchor(point, location) {
  if (!point) return null;
  const samePlace = location?.lat === point.lat && location?.lon === point.lon;
  return { kind: "coordinates", lat: point.lat, lon: point.lon,
    label: point.label || (samePlace && location.address) || "Выбранная точка" };
}

function validSource(source) {
  return Number.isFinite(source?.lat) && Math.abs(source.lat) <= 85.05112878 &&
    Number.isFinite(source.lon) && Math.abs(source.lon) <= 180;
}

export function installOopt(element) {
  const form = element("oopt-form");
  const diameter = element("oopt-diameter");
  const status = element("oopt-status");
  const sourceLabel = element("oopt-source");
  const coordinates = element("oopt-coordinates");
  const results = element("oopt-results");
  const list = element("oopt-list");
  const filter = element("oopt-filter");
  const map = installOoptMap(element, number => select(number, true), formatDistance);
  const details = installOoptDetails(element, formatDistance, number => {
    if (rowButtons.has(number)) renderList();
  });
  const rowButtons = new Map();
  let source, key, dataset, inFlight, failure, selected;
  let visible = [];

  function radius() { return Number(diameter.value) * 500; }

  function coveringDataset() {
    return dataset && (dataset.radius_m === radius() ||
      (dataset.radius_m > radius() && dataset.coverage !== "partial")) ? dataset : null;
  }

  function textLine(container, tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    container.append(node);
  }

  function renderList() {
    const scroll = list.scrollTop;
    const focused = list.contains(document.activeElement) ? document.activeElement.dataset.number : null;
    rowButtons.clear();
    const query = filter.value.trim();
    const rows = document.createDocumentFragment();
    for (const zone of visible) {
      if (!zone.registry_number.includes(query)) continue;
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button oopt-row";
      button.dataset.number = zone.registry_number;
      button.setAttribute("aria-pressed", String(selected === zone.registry_number));
      const name = details.nameFor(zone);
      textLine(button, "strong", name || zone.registry_number);
      textLine(button, "span", [name && zone.registry_number, formatDistance(zone.distance_m)].filter(Boolean).join(" · "));
      if (zone.inside) textLine(button, "span", "Точка внутри ООПТ · до границы " + formatDistance(zone.boundary_distance_m));
      row.append(button);
      rows.append(row);
      rowButtons.set(zone.registry_number, button);
    }
    list.replaceChildren(rows);
    rowButtons.get(focused)?.focus({ preventScroll: true });
    list.scrollTop = scroll;
    element("oopt-filter-empty").hidden = !query || rowButtons.size > 0;
  }

  function select(number, reveal = false) {
    const zone = visible.find(item => item.registry_number === number);
    if (!zone) return;
    rowButtons.get(selected)?.setAttribute("aria-pressed", "false");
    selected = number;
    details.setZone(zone, dataset?.retrieved_at);
    if (reveal && !rowButtons.has(number)) {
      filter.value = "";
      renderList();
    }
    const row = rowButtons.get(number);
    row?.setAttribute("aria-pressed", "true");
    if (reveal) row?.scrollIntoView({ block: "nearest" });
    map.select(zone);
  }

  function render() {
    const covered = coveringDataset();
    const canSearch = validSource(source);
    sourceLabel.textContent = source?.label || "Сначала выберите объект или точку на карте.";
    coordinates.hidden = !canSearch;
    coordinates.textContent = canSearch ? formatCoordinates({ lat: source.lat, lng: source.lon }) : "";
    element("oopt-radius").textContent = "Радиус " + radius() / 1000 + " км";
    element("oopt-run").disabled = !canSearch || Boolean(inFlight) || Boolean(covered && covered.coverage !== "partial");
    diameter.disabled = Boolean(inFlight);
    form.setAttribute("aria-busy", String(Boolean(inFlight)));

    let message = "";
    if (inFlight) message = "Ищем ООПТ в НСПД…";
    else if (failure) message = failure;
    else if (source && !canSearch) message = "У выбранного объекта нет доступной опорной точки для исследования.";
    else if (covered && covered.radius_m > radius()) {
      message = "Локальный отбор из выборки радиуса " + covered.radius_m / 1000 + " км.";
    } else if (canSearch && !covered) message = dataset
      ? "Диаметр изменён. Запустите поиск для нового радиуса."
      : "Выберите диаметр и запустите исследование.";

    visible = covered ? covered.zones.filter(zone => zone.distance_m <= radius() + 0.000001) : [];
    if (covered && !visible.length) message = [message, covered.coverage === "partial"
      ? "Пригодных контуров для этого радиуса не получено."
      : "В полученной выборке ООПТ для этого радиуса не найдены."].filter(Boolean).join(" ");
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle("error", Boolean(failure));
    results.hidden = !covered;
    element("oopt-count").textContent = visible.length;
    element("oopt-coverage").textContent = [disclaimer, ...(covered?.warnings ?? [])].join(" ");
    filter.hidden = element("oopt-filter-label").hidden = !visible.length;
    if (!visible.some(zone => zone.registry_number === selected)) selected = undefined;
    if (!selected) details.setZone();
    renderList();
    map.render(covered ? visible : null, radius());
    if (selected) select(selected);
  }

  function setSource(next) {
    const nextKey = next ? JSON.stringify([next.kind, next.id, next.lat, next.lon]) : null;
    source = next;
    if (key !== nextKey) {
      key = nextKey;
      dataset = inFlight = failure = selected = undefined;
      filter.value = "";
      render();
    } else {
      sourceLabel.textContent = source?.label || "Сначала выберите объект или точку на карте.";
    }
  }

  diameter.addEventListener("change", () => {
    failure = selected = undefined;
    filter.value = "";
    render();
  });
  filter.addEventListener("input", renderList);
  list.addEventListener("click", event => {
    const button = event.target.closest("button[data-number]");
    if (button) select(button.dataset.number);
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const covered = coveringDataset();
    if (!validSource(source) || inFlight || (covered && covered.coverage !== "partial")) return;
    const payload = { lat: source.lat, lon: source.lon, diameter_km: Number(diameter.value) };
    const token = {};
    failure = undefined;
    inFlight = token;
    render();
    try {
      const data = await request("/api/oopt/research", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }, 40000);
      if (inFlight !== token) return;
      if (data.center?.lat !== payload.lat || data.center?.lon !== payload.lon ||
          data.radius_m !== payload.diameter_km * 500 || !Array.isArray(data.zones)) {
        throw new Error("Сервер вернул результат для другой точки или радиуса.");
      }
      dataset = data;
    } catch (error) {
      if (inFlight === token) failure = "Поиск ООПТ не завершён. " + error.message;
    } finally {
      if (inFlight === token) {
        inFlight = undefined;
        render();
      }
    }
  });

  render();
  return { setSource, anchor: () => source?.kind ?? null, busy: () => Boolean(inFlight),
    reset() { setSource(null); diameter.value = "40"; render(); } };
}
