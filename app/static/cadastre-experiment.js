"use strict";

const element = id => document.getElementById(id);
const formatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const numberInput = element("cadastral-number");
const findButton = element("find-parcel");
const researchButton = element("research-area");
const showBoundaries = element("show-boundaries");
const showLabels = element("show-labels");
const showFill = element("show-fill");
const numberPattern = /^[0-9]{2}:[0-9]{2}:[0-9]{1,12}:[0-9]{1,14}$/;

function setStatus(message, error = false) {
  const status = element("status");
  status.textContent = message;
  status.classList.toggle("error", error);
}

async function request(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error("Нет связи с локальным приложением. Проверьте, что сервер запущен.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    const message = typeof data?.detail === "string" ? data.detail : "Сервер вернул неожиданный ответ.";
    throw new Error(message);
  }
  return data;
}

function formatDistance(meters) {
  if (meters >= 1000) return `${formatter.format(meters / 1000)} км`;
  if (meters > 0 && meters < 0.01) return "< 0,01 м";
  return `${formatter.format(meters)} м`;
}

function showDetails(feature) {
  const details = feature.properties;
  const measurement = details.measurement;
  const distance = element("detail-distance");
  const relation = element("detail-distance-status");
  if (measurement?.relation === "source") distance.textContent = "Исходный участок";
  else if (Number.isFinite(measurement?.distance_m)) distance.textContent = formatDistance(measurement.distance_m);
  else distance.textContent = "Расстояние не определено";
  relation.textContent = {
    disjoint: "Не пересекаются", touches: "Участки соприкасаются", intersects: "Участки пересекаются",
  }[measurement?.relation] ?? "";
  relation.hidden = !relation.textContent;
  element("detail-number").textContent = details.number;
  element("detail-category").textContent = details.land_category ?? "Нет данных";
  element("detail-use").textContent = details.permitted_use ?? "Нет данных";
  element("detail-area").textContent = details.area_m2 == null ? "Нет данных" : `${formatter.format(details.area_m2)} м²`;
  element("detail-address").textContent = details.address ?? "Нет данных";
  element("detail-geometry").textContent = details.geometry_status ?? "Нет данных";
  element("detail-empty").hidden = true;
  element("parcel-details").hidden = false;
}

function labelFits(map, layer, anchor, halfWidth, halfHeight) {
  const projected = map.project(anchor);
  const origin = L.point(0, 0);
  const ringsByPolygon = layer.feature.geometry.type === "Polygon" ? [layer.getLatLngs()] : layer.getLatLngs();
  const toLabelUnits = coordinate => {
    const offset = map.project(coordinate).subtract(projected);
    return L.point(offset.x / halfWidth, offset.y / halfHeight);
  };

  // The ellipse through the label's corners must clear exterior and interior rings.
  for (const rings of ringsByPolygon) {
    for (const ring of rings) {
      let previous = toLabelUnits(ring.at(-1));
      for (const coordinate of ring) {
        const next = toLabelUnits(coordinate);
        if (L.LineUtil.pointToSegmentDistance(origin, previous, next) <= Math.SQRT2) return false;
        previous = next;
      }
    }
  }
  return true;
}

function parcelLabels(map, parcels) {
  const pane = map.createPane("cadastralLabels");
  pane.style.zIndex = 420;
  pane.style.pointerEvents = "none";
  pane.setAttribute("aria-hidden", "true");
  const labels = L.layerGroup().addTo(map);
  const text = document.createElement("canvas").getContext("2d");
  text.font = '600 11px "Segoe UI", system-ui, sans-serif';
  const height = 16;
  let frame = null;

  function render(sourceId) {
    frame = null;
    labels.clearLayers();
    if (!showBoundaries.checked || !showLabels.checked) return;
    const size = map.getSize();
    const candidates = [];
    parcels.eachLayer(layer => {
      const { label_point: point, number } = layer.feature.properties;
      if (!point) return;
      const anchor = L.latLng(point[1], point[0]);
      const position = map.latLngToContainerPoint(anchor);
      const shortNumber = number.slice(number.lastIndexOf(":") + 1);
      const width = Math.ceil(text.measureText(shortNumber).width) + 6;
      const halfWidth = width / 2 + 2;
      const halfHeight = height / 2 + 2;
      const box = { left: position.x - halfWidth, right: position.x + halfWidth,
        top: position.y - halfHeight, bottom: position.y + halfHeight };
      if (box.left < 0 || box.top < 0 || box.right > size.x || box.bottom > size.y) return;

      const bounds = layer.getBounds();
      const span = map.project(bounds.getSouthEast()).subtract(map.project(bounds.getNorthWest()));
      if (span.x < width || span.y < height) return;
      candidates.push({ layer, anchor, shortNumber, width, box, footprint: span.x * span.y,
        isSource: layer.feature.id === sourceId });
    });
    candidates.sort((a, b) => Number(b.isSource) - Number(a.isSource) || b.footprint - a.footprint);

    const viewport = map.getContainer().getBoundingClientRect();
    const placed = [...map.getContainer().querySelectorAll(".leaflet-control")].map(control => {
      const box = control.getBoundingClientRect();
      return { left: box.left - viewport.left, right: box.right - viewport.left,
        top: box.top - viewport.top, bottom: box.bottom - viewport.top };
    });
    for (const candidate of candidates) {
      const { box, width, layer, anchor, shortNumber } = candidate;
      if (placed.some(other => box.left < other.right && box.right > other.left &&
        box.top < other.bottom && box.bottom > other.top)) continue;
      if (!labelFits(map, layer, anchor, width / 2 + 1, height / 2 + 1)) continue;

      const caption = document.createElement("span");
      caption.textContent = shortNumber;
      const icon = L.divIcon({ className: "parcel-label", html: caption, iconSize: [width, height] });
      L.marker(anchor, { pane: "cadastralLabels", icon, interactive: false, keyboard: false }).addTo(labels);
      placed.push(box);
    }
  }

  map.on("zoomstart", () => labels.clearLayers());
  return sourceId => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => render(sourceId));
  };
}

function installBasemaps(map) {
  const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 });
  const status = element("tile-status");
  const message = element("tile-message");
  const osmButton = element("switch-to-osm");
  function showTileStatus(text = "", offerOsm = false) {
    message.textContent = text;
    osmButton.hidden = !offerOsm;
    status.hidden = !text;
  }
  let esri;
  let loaded = 0;
  let failed = 0;
  osm.on({
    loading: () => { loaded = 0; failed = 0; },
    tileload: () => {
      loaded += 1;
      if (map.hasLayer(osm)) showTileStatus();
    },
    tileerror: () => { failed += 1; },
    load: () => {
      if (map.hasLayer(osm) && failed > 0 && loaded === 0) {
        showTileStatus("Подложка OpenStreetMap временно недоступна.");
      }
    },
  });

  map.on("baselayerchange", ({ layer }) => {
    element("osm-credit").setAttribute("aria-hidden", String(layer !== osm));
    element("esri-source").setAttribute("aria-hidden", String(layer !== esri));
    showTileStatus();
  });
  osm.addTo(map);
  const control = L.control.layers({ "Карта (OpenStreetMap)": osm }, {}, { collapsed: false }).addTo(map);
  element("basemap-control").append(control.getContainer());

  osmButton.addEventListener("click", () => {
    if (esri) map.removeLayer(esri);
    osm.addTo(map);
  });
  request("/api/cadastre-experiment/basemaps").then(({ esriApiKey }) => {
    if (esriApiKey && L.esri?.tiledMapLayer) {
      esri = esriWorldImagery(map, esriApiKey, showTileStatus);
      control.addBaseLayer(esri, "Спутник (Esri World Imagery)");
    }
  }).catch(() => { /* OSM is available without basemap configuration. */ });
}

function formatCoordinates(point) {
  return `${point.lat.toFixed(6)}, ${point.lng.toFixed(6)}`;
}

function parseCoordinate(value) {
  const normalized = value.trim().replace(",", ".");
  return /^[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$/.test(normalized) ? Number(normalized) : NaN;
}

function installCoordinates(map) {
  const cursor = element("cursor-coordinates");
  const latitude = element("latitude");
  const longitude = element("longitude");
  const copy = element("copy-coordinates");
  const status = element("coordinate-status");
  const marker = L.marker([0, 0], { interactive: false, keyboard: false, alt: "Выбранная точка" });
  let selectedPoint = null;

  function coordinateStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
    status.hidden = !message;
  }

  function selectPoint(point) {
    selectedPoint = point.wrap();
    marker.setLatLng(selectedPoint).addTo(map);
    element("selected-coordinates").textContent = formatCoordinates(selectedPoint);
    latitude.value = selectedPoint.lat.toFixed(6);
    longitude.value = selectedPoint.lng.toFixed(6);
    latitude.removeAttribute("aria-invalid");
    longitude.removeAttribute("aria-invalid");
    copy.disabled = false;
    coordinateStatus("");
  }

  const container = map.getContainer();
  container.addEventListener("mousemove", event => {
    cursor.textContent = formatCoordinates(map.mouseEventToLatLng(event).wrap());
  });
  container.addEventListener("mouseleave", () => { cursor.textContent = "—"; });
  // Parcel paths stop click bubbling. A map click selects only a reference point.
  map.on("click", ({ latlng }) => selectPoint(latlng));

  element("coordinate-form").addEventListener("submit", event => {
    event.preventDefault();
    const lat = parseCoordinate(latitude.value);
    const lng = parseCoordinate(longitude.value);
    const maxLatitude = 85.05112878; // Web Mercator tile coverage.
    const validLatitude = Number.isFinite(lat) && Math.abs(lat) <= maxLatitude;
    const validLongitude = Number.isFinite(lng) && Math.abs(lng) <= 180;
    latitude.setAttribute("aria-invalid", String(!validLatitude));
    longitude.setAttribute("aria-invalid", String(!validLongitude));
    if (!validLatitude || !validLongitude) {
      const message = Number.isFinite(lat) && Number.isFinite(lng)
        ? "Допустимая широта подложки — до ±85,05112878°, долгота — до ±180°."
        : "Введите широту и долготу в десятичных градусах.";
      coordinateStatus(message, true);
      (validLatitude ? longitude : latitude).focus();
      return;
    }
    const point = L.latLng(lat, lng);
    selectPoint(point);
    map.setView(point, Math.max(map.getZoom(), 15), { animate: false });
  });

  copy.addEventListener("click", async () => {
    if (!selectedPoint) return;
    try {
      await navigator.clipboard.writeText(formatCoordinates(selectedPoint));
      coordinateStatus("Координаты скопированы.");
    } catch {
      coordinateStatus("Не удалось скопировать. Выделите координаты выбранной точки.", true);
    }
  });
}

function renderMeasurement(map, layer, measurement) {
  layer.clearLayers();
  if (measurement?.relation !== "disjoint" || !(measurement.distance_m > 0)) return;

  const points = measurement.endpoints.map(([lng, lat]) => L.latLng(lat, lng));
  const color = "#DC2626";
  const options = { pane: "parcelMeasurement", interactive: false };
  L.polyline(points, { ...options, color, weight: 2.5, dashArray: "7 5" }).addTo(layer);
  for (const point of points) {
    L.circleMarker(point, { ...options, radius: 4, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: 1 }).addTo(layer);
  }
  const start = map.project(points[0]);
  const end = map.project(points[1]);
  const midpoint = map.unproject(start.add(end).divideBy(2));
  const dx = Math.abs(end.x - start.x);
  const dy = Math.abs(end.y - start.y);
  const caption = document.createElement("span");
  caption.textContent = formatDistance(measurement.distance_m);
  const horizontal = dx >= dy;
  const slope = Math.min(dx, dy) / (Math.max(dx, dy) || 1);
  const gap = 9 + slope * (horizontal ? caption.textContent.length * 4 + 6 : 14);
  L.tooltip({ ...options, className: "measurement-label", opacity: 1,
    direction: horizontal ? "top" : "right", offset: horizontal ? [0, -gap] : [gap, 0] })
    .setLatLng(midpoint).setContent(caption).addTo(layer);
}

function initialize() {
  if (typeof L === "undefined") {
    setStatus("Не удалось загрузить Leaflet.", true);
    findButton.disabled = true;
    element("parcel-form").addEventListener("submit", event => event.preventDefault());
    return;
  }

  const map = L.map("cadastre-map", { attributionControl: false, maxZoom: 19, zoomSnap: 0.25,
    zoomAnimation: false, fadeAnimation: false }).setView([55, 50], 4);
  installBasemaps(map);
  installCoordinates(map);
  L.control.scale({ position: "bottomleft", metric: true, imperial: false, maxWidth: 150 }).addTo(map);

  const parcelRenderer = L.canvas({ padding: 0.15 });
  const bufferPane = map.createPane("researchBoundary");
  bufferPane.style.zIndex = 410;
  bufferPane.style.pointerEvents = "none";
  const bufferLayer = L.geoJSON(null, {
    pane: "researchBoundary", interactive: false, smoothFactor: 0,
    style: { color: "#1760bd", weight: 2.4, opacity: 0.95, dashArray: "7 6", fill: false },
  }).addTo(map);
  // Keep the source outline above neighboring and buffer paths, without changing click targets.
  const sourcePane = map.createPane("sourceBoundary");
  sourcePane.style.zIndex = 415;
  sourcePane.style.pointerEvents = "none";
  const sourceStroke = { color: "#9333EA", weight: 5, opacity: 1 };
  const sourceOutline = L.geoJSON(null, {
    pane: "sourceBoundary", interactive: false, smoothFactor: 0,
    style: { ...sourceStroke, fill: false },
  });
  const measurementPane = map.createPane("parcelMeasurement");
  measurementPane.style.zIndex = 430;
  measurementPane.style.pointerEvents = "none";
  const measurementLayer = L.layerGroup();
  const cadastreDecorations = L.layerGroup([sourceOutline, measurementLayer]).addTo(map);

  const rootStyle = getComputedStyle(document.documentElement);
  const categories = new Map(["settlement", "industrial", "agricultural", "other", "unknown"]
    .map(bucket => [bucket, rootStyle.getPropertyValue(`--${bucket}`).trim()]));
  let source = null;
  let selected = null;
  let busy = false;
  const selectedLegend = element("selected-parcel-legend");

  function style(feature) {
    if (feature.id === source?.id) {
      return { ...sourceStroke, stroke: false, fillColor: "#A855F7", fillOpacity: 0.30 };
    }
    if (feature.id === selected?.feature.id) {
      return { color: "#0284C7", weight: 5, opacity: 1, fillColor: "#38BDF8", fillOpacity: 0.16 };
    }
    return {
      color: "#20B7ED", weight: 2.5, opacity: 1,
      fillColor: categories.get(feature.properties.category_bucket) || categories.get("unknown"),
      fillOpacity: showFill.checked ? 0.46 : 0,
    };
  }

  function selectParcel(layer) {
    const previous = selected;
    selected = layer;
    if (previous && previous !== layer) previous.setStyle(style(previous.feature));
    layer.setStyle(style(layer.feature));
    const neighborSelected = layer.feature.id !== source?.id;
    if (neighborSelected) layer.bringToFront();
    selectedLegend.hidden = !neighborSelected;
    showDetails(layer.feature);
    renderMeasurement(map, measurementLayer, layer.feature.properties.measurement);
  }

  const parcels = L.geoJSON(null, {
    renderer: parcelRenderer, smoothFactor: 0, bubblingMouseEvents: false, style,
    onEachFeature: (feature, layer) => {
      layer.bindTooltip(() => {
        const caption = document.createElement("span");
        caption.textContent = feature.properties.number;
        return caption;
      }, { sticky: true, opacity: 1 });
      layer.on("click", () => selectParcel(layer));
    },
  }).addTo(map);
  const refreshLabels = parcelLabels(map, parcels);
  const updateLabels = () => refreshLabels(source?.id);
  map.on("zoomend moveend resize", updateLabels);
  showLabels.addEventListener("change", updateLabels);

  function updateDisplay() {
    if (showBoundaries.checked) {
      parcels.addTo(map);
      cadastreDecorations.addTo(map);
      if (selected && selected.feature.id !== source?.id) selected.bringToFront();
    } else {
      parcels.remove();
      cadastreDecorations.remove();
    }
    parcels.setStyle(style);
    element("category-legend").hidden = !showFill.checked || !showBoundaries.checked;
  }

  function replaceParcels(feature, collection = null) {
    source = feature;
    selected = null;
    parcels.clearLayers().addData(source);
    const sourcePath = parcels.getLayers()[0];
    if (collection) parcels.addData(collection);
    sourceOutline.clearLayers().addData(source.geometry);
    selectParcel(sourcePath);
    // Keep small neighboring parcels selectable where contours overlap the source.
    sourcePath.bringToBack();
  }

  function syncControls() {
    numberInput.disabled = busy;
    findButton.disabled = busy;
    researchButton.disabled = busy || !source || numberInput.value.trim() !== source.id;
    element("parcel-form").setAttribute("aria-busy", String(busy));
  }

  function setBusy(value) {
    busy = value;
    syncControls();
  }

  showBoundaries.addEventListener("change", updateDisplay);
  showBoundaries.addEventListener("change", updateLabels);
  showFill.addEventListener("change", updateDisplay);
  numberInput.addEventListener("input", syncControls);

  element("parcel-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    const number = numberInput.value.trim();
    if (!numberPattern.test(number)) {
      setStatus("Проверьте формат кадастрового номера, например 63:09:0102159:753.", true);
      numberInput.focus();
      return;
    }
    setBusy(true);
    setStatus("Ищем исходный участок в НСПД…");
    try {
      const data = await request(`/api/cadastre-experiment/source?number=${encodeURIComponent(number)}`);
      bufferLayer.clearLayers();
      replaceParcels(data.parcel);
      map.fitBounds(parcels.getBounds(), { padding: [22, 22], maxZoom: 17 });
      updateLabels();
      element("parcel-count").textContent = "1";
      element("coverage").textContent = "Исходный участок показан. Запустите исследование, чтобы загрузить окрестности.";
      setStatus([`Исходный участок ${source.id} найден.`, data.cache_hit ? "Ответ из кеша." : "", ...data.warnings]
        .filter(Boolean).join(" "));
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      setBusy(false);
    }
  });

  researchButton.addEventListener("click", async () => {
    if (researchButton.disabled) return;
    setBusy(true);
    setStatus("Загружаем участки в пределах 500 м…");
    try {
      const data = await request("/api/cadastre-experiment/research", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ number: source.id }),
      });
      const started = performance.now();
      replaceParcels(data.source, data.parcels);
      bufferLayer.clearLayers().addData(data.buffer);
      map.fitBounds(bufferLayer.getBounds(), { padding: [24, 24], maxZoom: 17 });
      updateLabels();
      element("parcel-count").textContent = formatter.format(data.stats.mapped);
      element("coverage").textContent = `Соседних участков: ${formatter.format(data.stats.neighbors)}. Полнота выдачи НСПД неизвестна.`;
      const skipped = data.stats.invalid_geometry + data.stats.invalid_record + data.stats.outside_buffer;
      const notes = ["Исследование 500 м завершено.", data.cache_hit ? "Ответ из кеша." : "",
        skipped ? `Записей пропущено после проверки: ${formatter.format(skipped)}.` : "", ...data.warnings];
      setStatus(notes.filter(Boolean).join(" "));
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      performance.measure("cadastre-map-render", { start: started, end: performance.now() });
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      setBusy(false);
    }
  });
}

initialize();
