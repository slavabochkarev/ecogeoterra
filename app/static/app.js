"use strict";

const $ = (id) => document.getElementById(id);
const node = (tag, className, text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
};
const state = { origin: null, result: null, categories: {}, active: new Set(),
  selected: null, editing: false, busy: false, limit: 100, layers: new Map(), map: null };
const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const cadastreResults = new WeakMap();
let cadastreRequest = null;
const radius = () => Number(document.querySelector('input[name="radius"]:checked').value);

function status(message, kind = "") {
  $("status-text").textContent = message;
  $("status").className = `status ${kind}`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); }
  catch { throw new Error("Сервер вернул неожиданный ответ. Проверьте, что локальное приложение запущено."); }
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "Проверьте адрес, координаты и радиус исследования.");
  }
  return data;
}

function busy(value) {
  state.busy = value;
  $("spinner").hidden = !value;
  for (const el of document.querySelectorAll('.controls input, .controls button, #coordinate-form input, #coordinate-form button, #edit-point, .candidate')) el.disabled = value;
  $("research").disabled = value || !state.origin;
  if (state.marker) value ? state.marker.dragging.disable() : state.marker.dragging.enable();
}

function initializeMap() {
  if (!window.L) {
    status("Не удалось загрузить Leaflet. Можно задать координаты вручную и получить список объектов.", "error");
    $("edit-point").disabled = true;
    return;
  }
  // Neutral overview: the starting view does not select an enterprise point.
  state.map = L.map("map", { preferCanvas: true }).setView([55, 50], 4);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(state.map).on("tileerror", () => {
    if (!state.tileWarning) {
      state.tileWarning = true;
      const note = node("div", "tile-warning", "Подложка OSM недоступна. Расчёты и объекты работают независимо от неё.");
      note.style.cssText = "position:absolute;bottom:0;left:0;right:0;z-index:460;background:#fff7df;padding:7px 12px;font-size:10px;color:#80652e";
      document.querySelector(".map-wrap").append(note);
    }
  });
  state.objects = L.layerGroup().addTo(state.map);
  state.connection = L.layerGroup().addTo(state.map);
  state.map.on("click", (event) => {
    if (state.editing && !state.busy) setOrigin(event.latlng.lat, event.latlng.lng, "Точка уточнена вручную");
  });
}

function setEditing(value) {
  state.editing = value;
  $("edit-point").setAttribute("aria-pressed", String(value));
  $("edit-point").textContent = value ? "✓ Выбрать на карте" : "⌖ Указать точку";
  document.querySelector(".map-column").classList.toggle("editing-map", value);
  if (value) status("Нажмите на карту в точке предприятия. Позже маркер можно перетащить мышью.");
}

function clearResults() {
  state.result = null;
  state.selected = null;
  state.layers.clear();
  state.objects?.clearLayers();
  state.connection?.clearLayers();
  $("selected-object").hidden = true;
  $("export-json").disabled = $("export-csv").disabled = true;
  renderFilters();
  renderList();
}

function setOrigin(lat, lon, label) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 85 || Math.abs(lon) > 180) {
    status("Допустимая широта: от −85 до 85, долгота: от −180 до 180.", "error"); return;
  }
  state.origin = { lat, lon, source_label: label };
  clearResults();
  setEditing(false);
  $("map-hint").hidden = true;
  $("search-results").hidden = true;
  $("point-label").textContent = `${lat.toFixed(6)}, ${lon.toFixed(6)} · ${label}`;
  $("point-label").title = label;
  $("latitude").value = lat.toFixed(7);
  $("longitude").value = lon.toFixed(7);
  $("research").disabled = false;
  if (state.map) {
    if (state.marker) state.marker.setLatLng([lat, lon]);
    else {
      const icon = L.divIcon({ className: "plant-icon", html: "<span>⌖</span>", iconSize: [30, 30], iconAnchor: [15, 15] });
      state.marker = L.marker([lat, lon], { draggable: true, icon, title: "Точка предприятия — перетащите для уточнения" }).addTo(state.map);
      state.marker.on("dragend", () => {
        const point = state.marker.getLatLng();
        setOrigin(point.lat, point.lng, "Точка уточнена вручную");
      });
    }
    drawCircle();
    fitMap();
  }
  status("Точка выбрана. Проверьте её положение на карте и нажмите «Исследовать».", "success");
}

function drawCircle() {
  if (!state.map || !state.origin) return;
  state.circle?.remove();
  state.circle = L.circle([state.origin.lat, state.origin.lon], { radius: radius(), color: "#597c40", weight: 1.5,
    dashArray: "6 6", fillColor: "#9ebd79", fillOpacity: .055, interactive: false }).addTo(state.map);
  state.circle.bringToBack();
}
function fitMap() {
  if (state.circle) state.map.fitBounds(state.circle.getBounds(), { padding: [30, 30], animate: false });
}

function renderFilters() {
  const container = $("category-filters");
  container.replaceChildren();
  for (const [key, category] of Object.entries(state.categories)) {
    const button = node("button", "category-chip");
    button.type = "button";
    button.style.setProperty("--category-color", category.color);
    button.setAttribute("aria-pressed", String(state.active.has(key)));
    button.dataset.category = key;
    button.append(node("span", "category-dot"), node("span", "", category.label), node("b", "", number.format(state.result?.metadata.counts[key] || 0)));
    button.addEventListener("click", () => {
      state.active.has(key) ? state.active.delete(key) : state.active.add(key);
      state.limit = 100; renderFilters(); renderObjects();
    });
    container.append(button);
  }
  $("total-count").textContent = number.format(state.result?.features.length || 0);
}

function visibleFeatures() {
  return (state.result?.features || []).filter(f => state.active.has(f.properties.category) && (!$("hide-unnamed").checked || f.properties.has_name));
}

function renderList() {
  const container = $("object-list");
  container.replaceChildren();
  const features = visibleFeatures();
  $("visible-count").textContent = state.result ? `Показано ${number.format(features.length)} из ${number.format(state.result.features.length)}` : "Ожидаем исследование";
  if (!features.length) {
    const empty = node("div", "empty-state");
    empty.append(node("span", "empty-icon", "◉"), node("h3", "", state.result ? "Объекты не найдены" : "Территория ещё не исследована"), node("p", "", state.result ? "Проверьте фильтры или измените точку и радиус. Отсутствие объектов в OSM не означает их отсутствия на местности." : "Выберите точку предприятия и радиус. Здесь появятся объекты из OpenStreetMap."));
    container.append(empty); return;
  }
  const fragment = document.createDocumentFragment();
  for (const feature of features.slice(0, state.limit)) {
    const p = feature.properties;
    const row = node("button", `object-row${state.selected === feature.id ? " selected" : ""}`);
    row.dataset.osmKey = feature.id;
    row.setAttribute("aria-pressed", String(state.selected === feature.id));
    row.style.setProperty("--category-color", state.categories[p.category].color);
    const text = node("span", "object-text");
    text.append(node("strong", "", p.name), node("small", "", p.type));
    const distance = node("span", "object-distance", `${number.format(p.distance_m)} м`);
    distance.append(node("small", "", p.direction || "Пересечение"));
    row.append(node("span", "object-symbol", p.category === "building" ? "▥" : "◇"), text, distance);
    row.addEventListener("click", () => selectObject(feature));
    fragment.append(row);
  }
  if (features.length > state.limit) {
    const more = node("button", "load-more", `Показать ещё · осталось ${number.format(features.length - state.limit)}`);
    more.addEventListener("click", () => { state.limit += 100; renderList(); });
    fragment.append(more);
  }
  container.append(fragment);
}

function styleFor(feature) {
  const selected = feature.id === state.selected;
  return { color: selected ? "#173e35" : state.categories[feature.properties.category].color,
    weight: selected ? 4 : 1.8, opacity: 1, fillOpacity: selected ? .4 : .17,
    radius: selected ? 8 : 5, fillColor: state.categories[feature.properties.category].color };
}

function renderObjects() {
  const features = visibleFeatures();
  if (!features.some(f => f.id === state.selected)) closeSelection();
  state.objects?.clearLayers(); state.layers.clear();
  if (state.map) {
    for (const feature of features) {
      const layer = L.geoJSON(feature, {
        style: () => styleFor(feature),
        pointToLayer: (f, point) => L.circleMarker(point, styleFor(f))
      });
      const tooltip = node("span", "", feature.properties.name);
      layer.bindTooltip(tooltip);
      layer.on("click", (event) => {
        if (state.editing) return;
        if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
        selectObject(feature, false);
      });
      layer.addTo(state.objects);
      state.layers.set(feature.id, layer);
    }
  }
  $("export-json").disabled = $("export-csv").disabled = !state.result;
  renderList();
}

function closeSelection() {
  const previous = state.selected;
  state.selected = null;
  if (previous && state.result) {
    const f = state.result.features.find(item => item.id === previous);
    if (f) state.layers.get(previous)?.setStyle(styleFor(f));
  }
  $("selected-object").hidden = true;
  state.connection?.clearLayers();
}

function selectObject(feature, moveMap = true) {
  closeSelection();
  state.selected = feature.id;
  const p = feature.properties;
  const layer = state.layers.get(feature.id);
  if (layer) {
    layer.setStyle(styleFor(feature));
    layer.eachLayer(child => { child.setRadius?.(8); child.bringToFront?.(); });
    if (moveMap) state.map.fitBounds(layer.getBounds(), { maxZoom: 17, padding: [45, 45], animate: false });
    if (p.distance_m > .001) L.polyline([[state.origin.lat, state.origin.lon], [p.nearest_point.lat, p.nearest_point.lon]], {
      color: "#31523a", weight: 1.5, dashArray: "4 5", interactive: false
    }).addTo(state.connection);
  }
  const detail = $("selected-object");
  detail.replaceChildren();
  const heading = node("div", "detail-heading");
  const title = node("div");
  const link = node("a", "", `OSM ${p.osm_key} ↗`);
  link.href = `https://www.openstreetmap.org/${p.osm_type}/${p.osm_id}`;
  link.target = "_blank"; link.rel = "noopener";
  title.append(node("h3", "", p.name), link);
  const close = node("button", "detail-close", "×");
  close.setAttribute("aria-label", "Закрыть сведения об объекте");
  close.addEventListener("click", () => { closeSelection(); renderList(); });
  heading.append(title, close);
  const summary = node("p", "detail-summary", `${p.type} · ${number.format(p.distance_m)} м · ${p.direction || "Направление не определено: геометрии пересекаются"}. Основание: ${p.classification_basis}.`);
  const tags = node("details", "tag-details");
  tags.append(node("summary", "", `Исходные OSM-теги (${Object.keys(p.tags).length})`));
  const table = node("table", "tag-table");
  const body = node("tbody");
  for (const [key, value] of Object.entries(p.tags).sort(([a], [b]) => a.localeCompare(b))) {
    const row = node("tr"); row.append(node("td", "", key), node("td", "", value)); body.append(row);
  }
  table.append(body); tags.append(table); detail.append(heading, summary, tags); detail.hidden = false;
  const cadastreSection = node("section", "cadastre");
  cadastreSection.id = "cadastre-section";
  detail.append(cadastreSection);
  renderCadastre(feature);
  renderList();
  const selectedRow = Array.from($("object-list").querySelectorAll(".object-row")).find(row => row.dataset.osmKey === feature.id);
  selectedRow?.scrollIntoView({ block: "nearest" });
}

function renderCadastre(feature) {
  const section = $("cadastre-section");
  if (!section || state.selected !== feature.id) return;
  section.replaceChildren(node("h4", "", "Кадастровые сведения"));
  const button = node("button", "secondary cadastre-fetch", "Получить кадастровые сведения");
  button.type = "button";
  button.disabled = Boolean(cadastreRequest);
  button.addEventListener("click", () => fetchCadastre(feature));
  section.append(button);
  const output = node("div", "cadastre-output");
  output.setAttribute("aria-live", "polite");
  output.setAttribute("aria-busy", String(cadastreRequest === feature));
  section.append(output);
  if (cadastreRequest) {
    const progress = node("p", "cadastre-progress");
    progress.append(node("span", "spinner"), node("span", "", cadastreRequest === feature
      ? "Запрашиваем НСПД… Можно продолжать работу с картой."
      : "Выполняется запрос для другого объекта. Дождитесь завершения."));
    output.append(progress);
  }
  const saved = cadastreResults.get(feature);
  if (!saved) {
    if (!cadastreRequest) output.append(node("p", "cadastre-note", "Данные НСПД запрашиваются только для выбранного объекта по кнопке."));
    return;
  }
  const data = saved.data;
  const meta = `Источник: ${data.source}${data.retrieved_at ? " · Получено: " + new Date(data.retrieved_at).toLocaleString("ru-RU") : " · Дата получения: Нет данных"} · ${data.cache_hit ? "Из локального кеша" : "Без кеша"}`;
  output.append(node("p", "cadastre-note", meta));
  if (data.message) output.append(node("p", `cadastre-message ${["unavailable", "error"].includes(data.status) ? "cadastre-error" : ""}`, data.message));
  if (data.status === "multiple_matches") {
    output.append(node("p", "cadastre-message", "Выберите кадастровый объект для просмотра. Соответствие OSM требует проверки."));
    for (const candidate of data.candidates) {
      const choice = node("button", "cadastre-choice");
      choice.type = "button";
      choice.setAttribute("aria-pressed", String(saved.selected === candidate.id));
      choice.append(node("strong", "", `${candidate.cadastral_number} · ${candidate.object_type}`), node("span", "", candidate.match_basis));
      if (candidate.intersection) choice.append(node("span", "", `Пересечение: ${(candidate.intersection.osm_fraction * 100).toFixed(1)}% площади OSM · ${number.format(candidate.intersection.area_m2)} м²`));
      choice.addEventListener("click", () => { saved.selected = candidate.id; renderCadastre(feature); });
      output.append(choice);
    }
  }
  const selected = data.candidates.find(c => c.id === saved.selected);
  if (selected) {
    output.append(node("h5", "", `${selected.object_type} · ${selected.cadastral_number}`), node("p", "cadastre-note", selected.match_basis));
    const labels = { object_kind: "Вид объекта", land_category: "Категория земель", permitted_use: "ВРИ участка", purpose: "Назначение ОКС", name: "Наименование", area_m2: "Площадь, м²", address: "Адресное описание", cadastral_cost_rub: "Кадастровая стоимость, ₽", status: "Статус", floors: "Этажность", year_built: "Год постройки" };
    const fields = node("dl", "cadastre-fields");
    for (const [key, value] of Object.entries(selected.characteristics)) {
      fields.append(node("dt", "", labels[key] || key), node("dd", "", value === null || value === "" ? "Нет данных" : typeof value === "object" ? JSON.stringify(value) : String(value)));
    }
    if (selected.intersection) fields.append(node("dt", "", "Пересечение с OSM"), node("dd", "", `${(selected.intersection.osm_fraction * 100).toFixed(1)}% · ${number.format(selected.intersection.area_m2)} м²`));
    fields.append(node("dt", "", "Геометрия"), node("dd", "", selected.geometry ? "Доступна · EPSG:4326" : "Нет данных"));
    output.append(fields);
    const raw = node("details", "tag-details");
    raw.append(node("summary", "", "Другие характеристики и геометрия НСПД"), node("pre", "cadastre-raw", JSON.stringify({ characteristics: selected.raw_options, geometry: selected.geometry }, null, 2)));
    output.append(raw);
  }
  for (const warning of data.warnings) output.append(node("p", "cadastre-note", warning));
}

async function fetchCadastre(feature) {
  if (cadastreRequest) return;
  cadastreRequest = feature;
  renderCadastre(feature);
  try {
    const p = feature.properties;
    const data = await api("/api/cadastre/enrich", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ osm_type: p.osm_type, osm_id: p.osm_id, geometry: feature.geometry,
        coordinates: feature.geometry?.type === "Point" ? feature.geometry.coordinates : null, tags: p.tags }) });
    cadastreResults.set(feature, { data, selected: data.selected_candidate });
  } catch (error) {
    cadastreResults.set(feature, { selected: null, data: { status: "error", source: "NSPD", retrieved_at: null, cache_hit: false, candidates: [], warnings: [], message: error.message } });
  } finally {
    cadastreRequest = null;
    // Keep the response attached to its feature if selection or research changes.
    const current = state.result?.features.find(f => f.id === state.selected);
    if (current) renderCadastre(current);
  }
}

function download(content, mime, extension) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = node("a"); link.href = url;
  link.download = `ecogeoterra-${new Date().toISOString().replace(/[:.]/g, "-")}.${extension}`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Protect spreadsheet users from formula interpretation without altering JSON.
function csvCell(value) {
  let text = value === null || value === undefined ? "" : String(value);
  if (typeof value === "string" && /^[\s]*[=+@-]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function exportResults(format) {
  if (!state.result) return;
  const features = visibleFeatures();
  const filters = { categories: [...state.active], hide_unnamed: $("hide-unnamed").checked };
  if (format === "json") {
    const output = { ...state.result, features, metadata: { ...state.result.metadata, exported_count: features.length,
      exported_at: new Date().toISOString(), filters } };
    download(JSON.stringify(output, null, 2), "application/geo+json;charset=utf-8", "json");
  } else {
    const headers = ["name", "category", "type", "distance_m", "direction", "osm_id", "classification_basis", "nearest_lat", "nearest_lon", "tags_json", "geometry_json", "source_lat", "source_lon", "radius_m", "source_label", "fetched_at", "osm_base_timestamp", "attribution", "disclaimer"];
    const meta = state.result.metadata;
    const rows = features.map(f => {
      const p = f.properties;
      return [p.name, p.category, p.type, p.distance_m.toFixed(3), p.direction, p.osm_key, p.classification_basis,
        p.nearest_point.lat, p.nearest_point.lon, JSON.stringify(p.tags), JSON.stringify(f.geometry), meta.source.lat,
        meta.source.lon, meta.source.radius, meta.source.source_label, meta.cache.fetched_at, meta.osm_base_timestamp,
        meta.attribution, meta.disclaimer];
    });
    download("\uFEFF" + [headers, ...rows].map(row => row.map(csvCell).join(";")).join("\r\n"), "text/csv;charset=utf-8", "csv");
  }
  status(`Выгружено объектов: ${number.format(features.length)}. Применены текущие фильтры.`, "success");
}

$("address-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (state.busy) return;
  const query = $("address").value.trim();
  if (query.length < 3) { status("Введите хотя бы три символа адреса.", "error"); return; }
  busy(true); $("search-results").hidden = true;
  status("Ищем адрес в Nominatim… Точный адрес может отсутствовать в OpenStreetMap.");
  try {
    const data = await api(`/api/geocode?q=${encodeURIComponent(query)}`);
    const results = $("search-results"); results.replaceChildren();
    if (!data.results.length) {
      status("Адрес не найден. Попробуйте сократить его до улицы и номера либо укажите точку вручную. Координаты предприятия не подставлялись.", "error");
    } else {
      results.append(node("h3", "", `Выберите результат · ${data.results.length}`), node("p", "", "Проверьте адрес и точность результата. Nominatim может вернуть улицу или район вместо здания."));
      data.results.forEach((item, index) => {
        const button = node("button", "candidate"); button.type = "button";
        const text = node("span", "", item.display_name);
        text.append(node("small", "", `${item.osm_type || "объект"}/${item.osm_id || "—"} · ${item.type || "тип не указан"} · ${item.lat.toFixed(6)}, ${item.lon.toFixed(6)}`));
        button.append(node("span", "candidate-number", index + 1), text);
        button.addEventListener("click", () => setOrigin(item.lat, item.lon, item.display_name));
        results.append(button);
      });
      results.hidden = false;
      status(`Найдено вариантов: ${data.results.length}. Выберите подходящий.${data.cache.hit ? " Ответ из файлового кеша." : ""}${data.cache.warning ? " " + data.cache.warning : ""}`);
    }
  } catch (error) { status(error.message, "error"); }
  finally { busy(false); }
});

$("research").addEventListener("click", async () => {
  if (!state.origin || state.busy) return;
  clearResults(); busy(true); setEditing(false);
  status("Загружаем геометрию из Overpass и рассчитываем расстояния… Это может занять до 100 секунд.");
  try {
    state.result = await api("/api/research", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...state.origin, radius: radius() }) });
    state.limit = 100; renderFilters(); renderObjects(); fitMap();
    const meta = state.result.metadata;
    status(`Найдено объектов: ${number.format(meta.total)}. ${meta.cache.hit ? "Использован файловый кеш" : "Получены данные Overpass"} от ${new Date(meta.cache.fetched_at).toLocaleString("ru-RU")}.${meta.osm_base_timestamp ? " Срез OSM: " + new Date(meta.osm_base_timestamp).toLocaleString("ru-RU") + "." : ""}${meta.warnings.length ? " " + meta.warnings.join(" ") : ""}`, "success");
  } catch (error) { status(error.message, "error"); }
  finally { busy(false); }
});

$("edit-point").addEventListener("click", () => setEditing(!state.editing));
$("fit-map").addEventListener("click", fitMap);
$("coordinate-form").addEventListener("submit", (event) => {
  event.preventDefault(); if (state.busy) return;
  setOrigin(Number($("latitude").value), Number($("longitude").value), "Точка задана координатами вручную");
});
document.querySelectorAll('input[name="radius"]').forEach(input => input.addEventListener("change", () => {
  clearResults(); drawCircle(); fitMap();
  status("Радиус изменён. Нажмите «Исследовать», чтобы получить актуальные результаты.");
}));
$("hide-unnamed").addEventListener("change", () => { state.limit = 100; renderObjects(); });
$("reset-filters").addEventListener("click", () => {
  state.active = new Set(Object.keys(state.categories)); $("hide-unnamed").checked = false;
  state.limit = 100; renderFilters(); renderObjects();
});
$("export-json").addEventListener("click", () => exportResults("json"));
$("export-csv").addEventListener("click", () => exportResults("csv"));

initializeMap();
api("/api/config").then(config => {
  state.categories = config.categories; state.active = new Set(Object.keys(config.categories)); renderFilters();
}).catch(error => status(error.message, "error"));
