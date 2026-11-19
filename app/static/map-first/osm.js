import { request } from "./request.js";
import { createOsmMap } from "./osm-map.js";
import { createOsmPanel } from "./osm-panel.js";
import { exportOsm } from "./osm-export.js";
import { numberFormat } from "./cadastre-format.js";
import { formatCoordinates } from "./coordinates.js";

const cacheTtl = 24 * 60 * 60 * 1000;
const geometryTypes = new Set(["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"]);
const resultKey = (point, radius) => JSON.stringify([point.lat, point.lon, radius]);
const fresh = entry => Date.now() - entry.fetchedAt < cacheTtl;

export function installOsm(map, leaflet, element, state, layers, showTab, cadastre, setAttribution, changed) {
  const radius = element("osm-radius");
  const load = element("osm-load");
  const unnamed = element("osm-hide-unnamed");
  const status = element("osm-status");
  const categories = {};
  const memory = new Map();
  let active = new Set();
  let requestedCategories = [];
  let origin;
  let result;
  let selected;
  let busy = false;
  let configRequest;
  let visible = [];
  let objectsPopup;

  const renderer = createOsmMap(map, leaflet, element, categories, selectAt);
  const panel = createOsmPanel(element, categories, select);

  function key() {
    return origin && Math.abs(origin.lat) <= 85 ? resultKey(origin, Number(radius.value)) : null;
  }

  function current() {
    return result && key() === result.key && (result.restored || fresh(result));
  }

  function setStatus(message, error = false) {
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle("error", error);
  }

  function syncControls() {
    load.disabled = !key() || busy || (Object.keys(categories).length > 0 && !active.size);
    element("osm-export-csv").disabled = !result;
    element("osm-export-geojson").disabled = !result;
  }

  function closePopup() {
    if (objectsPopup) map.closePopup(objectsPopup);
    objectsPopup = undefined;
  }

  function refreshVisibility() {
    const show = Boolean(current() && state.layers.osm.visible);
    renderer.show(show);
    setAttribution("objects", show && visible.length > 0);
    setAttribution("listedObjects", visible.length > 0);
    if (!show) closePopup();
    const notes = [];
    if (result && !current()) {
      notes.push(key() === result.key ? "Срок актуальности выборки истёк. Загрузите объекты снова."
        : "Показана предыдущая выборка. Точка или радиус изменены. Старые объекты скрыты на карте.");
    } else if (result && !state.layers.osm.visible) {
      notes.push("Объекты скрыты на карте переключателем слоя.");
    }
    if (result?.restored && current() && !fresh(result)) {
      notes.push("Сохранённая выборка старше суток. При необходимости загрузите объекты снова.");
    }
    if (result && (requestedCategories.length !== active.size || requestedCategories.some(item => !active.has(item)))) {
      notes.push("Выбор категорий изменён. Фильтры применены к загруженным данным.");
    }
    const note = element("osm-mismatch");
    note.textContent = notes.join(" ");
    note.hidden = !notes.length;
    syncControls();
  }

  function select(id, focus) {
    selected = visible.find(feature => feature.id === id);
    if (selected) showTab("objects");
    renderer.select(selected, focus && Boolean(current()));
    panel.select(selected);
    changed();
    if (selected) requestAnimationFrame(() => {
      const scroll = element("objects");
      const card = element("osm-card");
      const top = card.getBoundingClientRect().top - scroll.getBoundingClientRect().top;
      if (top < 0 || top + 105 > scroll.clientHeight) scroll.scrollTop += top - 8;
    });
  }

  function selectAt(feature, latlng) {
    closePopup();
    const parcels = cadastre.parcelsAt(latlng);
    if (parcels.length && state.tab === "cadastre") cadastre.selectOnMap(parcels[0]);
    else select(feature.id, false);
    if (!parcels.length) return;
    const menu = document.createElement("div");
    menu.className = "map-hit-menu";
    const title = document.createElement("strong");
    title.textContent = `Объекты в точке (${parcels.length + 1})`;
    menu.append(title);
    const button = (label, action) => {
      const control = document.createElement("button");
      control.type = "button";
      control.className = "button location-option";
      control.textContent = label;
      control.addEventListener("click", () => { closePopup(); action(); });
      menu.append(control);
    };
    button(`Объекты · ${feature.properties.name}`, () => select(feature.id, false));
    for (const id of parcels) button(`Кадастр · ${id}`, () => cadastre.selectOnMap(id));
    objectsPopup = leaflet.popup({ autoPan: false, maxWidth: 270, className: "osm-overlap" })
      .setLatLng(latlng).setContent(menu).openOn(map);
  }

  function render() {
    closePopup();
    const data = result?.data;
    visible = (data?.features ?? []).filter(({ properties: p }) =>
      active.has(p.category) && (!unnamed.checked || p.has_name));
    if (!visible.includes(selected)) {
      selected = undefined;
      panel.select();
    }
    renderer.render(data, visible);
    renderer.select(selected);
    panel.renderList(visible);
    panel.renderCategories(active, data?.metadata.counts);
    if (data) {
      const source = data.metadata.source;
      const { fetched_at: fetchedAt } = data.metadata.cache ?? {};
      const date = fetchedAt ? ` · ${new Date(fetchedAt).toLocaleString("ru-RU")}` : "";
      const skipped = data.metadata.warnings?.filter(text => typeof text === "string").join(" ") ?? "";
      const empty = visible.length ? "" : " По текущим фильтрам объектов нет.";
      element("osm-summary").textContent = `Показано ${numberFormat.format(visible.length)} из ${numberFormat.format(data.features.length)} · ${source.radius} м от точки ${formatCoordinates({ lat: source.lat, lng: source.lon })}${date}.${empty} ${skipped}`.trim();
    } else {
      element("osm-summary").textContent = "Ожидает загрузки.";
    }
    refreshVisibility();
    changed();
  }

  function setOrigin(point) {
    origin = point ? { ...point, label: point.label || "Опорная точка OSM",
      lat: Number(point.lat.toFixed(7)), lon: Number(point.lon.toFixed(7)) } : null;
    setAttribution("objectOrigin", Boolean(origin?.address));
    element("osm-origin").textContent = origin
      ? `${origin.label} · ${formatCoordinates({ lat: origin.lat, lng: origin.lon })}${key() ? "" : " · для поиска OSM допустима широта ±85°."}`
      : "Задайте точку поиском или найдите исходный кадастровый участок.";
    refreshVisibility();
    changed();
  }

  function getCategories() {
    if (Object.keys(categories).length) return Promise.resolve();
    if (!configRequest) {
      configRequest = request("/api/config", {}, 6000).then(data => {
        const entries = Object.entries(data.categories ?? {});
        if (entries.length !== 9 || entries.some(([, item]) =>
          typeof item?.label !== "string" || typeof item.color !== "string")) {
          throw new Error("Не удалось прочитать существующие категории OSM.");
        }
        Object.assign(categories, data.categories);
        active = new Set(Object.keys(categories));
        panel.renderCategories(active);
        element("osm-category-status").textContent = "";
      }).finally(() => { configRequest = undefined; });
    }
    return configRequest;
  }

  function save(key, data) {
    const timestamp = Date.parse(data.metadata.cache?.fetched_at);
    const entry = { key, data, fetchedAt: Number.isFinite(timestamp) ? timestamp : Date.now() };
    memory.delete(key);
    memory.set(key, entry);
    if (memory.size > 5) memory.delete(memory.keys().next().value);
    return entry;
  }

  function accept(entry, filters) {
    if (result !== entry) {
      selected = undefined;
      panel.select();
    }
    result = entry;
    requestedCategories = filters;
    layers.setVisible("osm", true);
    render();
  }

  load.addEventListener("click", async () => {
    const requestedKey = key();
    if (!requestedKey || load.disabled) return;
    const payload = { lat: origin.lat, lon: origin.lon, radius: Number(radius.value), source_label: origin.label.slice(0, 1000) };
    busy = true;
    syncControls();
    setStatus("Загружаем объекты OpenStreetMap…");
    try {
      await getCategories();
      const filters = [...active];
      let entry = memory.get(requestedKey);
      if (entry && fresh(entry)) {
        memory.delete(requestedKey);
        memory.set(requestedKey, entry);
      } else {
        const data = await request("/api/research", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        }, 110000);
        const source = data.metadata?.source;
        if (data.type !== "FeatureCollection" || !Array.isArray(data.features) ||
            source?.lat !== payload.lat || source?.lon !== payload.lon || source?.radius !== payload.radius ||
            data.features.some(feature => typeof feature?.id !== "string" ||
              !Object.hasOwn(categories, feature.properties?.category) ||
              !Number.isFinite(feature.properties.distance_m) || !geometryTypes.has(feature.geometry?.type))) {
          throw new Error("Сервер вернул некорректную выборку OSM.");
        }
        entry = save(requestedKey, data);
      }
      if (key() === requestedKey) {
        accept(entry, filters);
        setStatus("Объекты OSM загружены.");
      } else {
        setStatus("Ответ сохранён для предыдущих параметров. Для текущей точки и радиуса запустите загрузку.");
      }
    } catch (error) {
      setStatus(`${error.message}${result ? " Предыдущая выборка сохранена." : ""}`, true);
    } finally {
      busy = false;
      syncControls();
    }
  });

  radius.addEventListener("change", () => { refreshVisibility(); changed(); });
  unnamed.addEventListener("change", render);
  element("osm-categories").addEventListener("change", event => {
    const input = event.target;
    if (!Object.hasOwn(categories, input.value)) return;
    if (input.checked) active.add(input.value);
    else active.delete(input.value);
    render();
  });
  element("osm-reset").addEventListener("click", () => {
    active = new Set(Object.keys(categories));
    unnamed.checked = false;
    render();
  });
  for (const format of ["geojson", "csv"]) {
    element(`osm-export-${format}`).addEventListener("click", () => {
      if (!result) return;
      exportOsm(result.data, visible, { categories: [...active], hide_unnamed: unnamed.checked }, format);
      setStatus(`Выгружено объектов: ${numberFormat.format(visible.length)}. Применены текущие фильтры.`);
    });
  }

  getCategories().catch(error => {
    element("osm-category-status").textContent = `${error.message} Повторите загрузку после выбора точки.`;
  });
  async function restore(data, saved) {
    if (data) await getCategories();
    memory.clear();
    requestedCategories = data?.requested_categories ?? [];
    radius.value = String(saved.osm_radius);
    active = new Set(saved.osm_categories ?? Object.keys(categories));
    unnamed.checked = saved.hide_unnamed;
    setStatus("");
    if (data) {
      const source = data.result.metadata.source;
      result = save(resultKey(source, source.radius), data.result);
      result.restored = true;
    } else {
      result = undefined;
    }
    setOrigin(saved.osm_origin);
    selected = data?.result.features.find(feature => feature.id === saved.osm_selected);
    panel.select(selected);
    render();
  }

  return {
    setOrigin, refreshVisibility, restore, ensureReady: getCategories, busy: () => busy,
    snapshot: () => ({
      data: result?.data, requestedCategories, origin, radius: Number(radius.value), selectedId: selected?.id,
      categories: Object.keys(categories).length ? [...active] : null, hideUnnamed: unnamed.checked,
    }),
  };
}
