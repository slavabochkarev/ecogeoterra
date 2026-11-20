import { createCadastreMap } from "./cadastre-map.js";
import { createCadastrePanel } from "./cadastre-panel.js";
import { isPosition } from "./cadastre-format.js";
import { request } from "./request.js";
import { isCadastralNumber } from "./search-format.js";

const experimentApi = "/api/cadastre-experiment";
const researchApi = "/api/cadastre/research";
const radiusLabel = value => `${value.toLocaleString("ru-RU")} м`;

function isPolygon(feature) {
  const geometry = feature?.geometry;
  const points = geometry?.coordinates;
  const polygons = geometry?.type === "Polygon" ? [points] : points;
  return feature?.type === "Feature" && ["Polygon", "MultiPolygon"].includes(geometry?.type) &&
    Array.isArray(polygons) && polygons.length > 0 && polygons.every(polygon =>
      Array.isArray(polygon) && polygon.length > 0 && polygon.every(ring =>
        Array.isArray(ring) && ring.length >= 4 && ring.every(isPosition) &&
        ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1]));
}

export function isParcel(feature, number) {
  return typeof number === "string" && isCadastralNumber(number) &&
    feature?.id === number && feature.properties?.number === number && isPolygon(feature);
}

function readNeighbors(data, sourceNumber, radius) {
  if (data.radius_m !== radius || data.buffer?.properties?.radius_m !== radius ||
      !["complete", "unknown", "partial"].includes(data.coverage) ||
      !isParcel(data.source, sourceNumber) || !isPolygon(data.buffer) ||
      data.parcels?.type !== "FeatureCollection" || !Array.isArray(data.parcels.features)) {
    throw new Error("Сервер вернул некорректные результаты исследования.");
  }
  const neighbors = new Map();
  for (const feature of data.parcels.features) {
    if (!isParcel(feature, feature?.id)) {
      throw new Error("Сервер вернул некорректный контур соседнего участка.");
    }
    if (feature.id !== sourceNumber) neighbors.set(feature.id, feature);
  }
  return neighbors;
}

export function installCadastre(map, leaflet, layers, element, showCadastre, changed, onSelect) {
  const field = element("cadastre-number");
  const radiusInput = element("radius");
  const availability = element("radius-availability");
  const research = element("research-cadastre");
  const status = element("cadastre-status");
  let source;
  let result;
  let neighbors = new Map();
  let selectedId;
  let sourceRevision;
  let generation = 0;
  let busy = false;
  let statusTimer;
  let researchRequest;

  function select(id, fromMap) {
    const feature = id === source?.id ? source : neighbors.get(id);
    if (!feature) return;
    selectedId = id;
    cadastreMap.highlight(feature);
    panel.select(feature);
    onSelect(feature);
    showCadastre();
    if (fromMap) panel.revealSelection();
    changed();
  }

  const cadastreMap = createCadastreMap(map, leaflet, layers, element, id => select(id, true));
  const panel = createCadastrePanel(element, id => select(id, false));

  function setStatus(message, error = false, { transient = false, warning = false } = {}) {
    clearTimeout(statusTimer);
    if (status.textContent !== message) status.textContent = message;
    status.classList.toggle("error", error);
    status.classList.toggle("warning", warning);
    status.classList.toggle("cadastre-toast", transient);
    status.hidden = !message;
    if (transient) statusTimer = setTimeout(() => setStatus(""), 5000);
  }

  function syncControls() {
    const entered = field.value.trim();
    research.disabled = busy || !source || Boolean(radiusInput.selectedOptions[0]?.disabled) ||
      (isCadastralNumber(entered) && entered !== source.id);
    research.title = source ? `Исходный участок ${source.id}` : "Сначала выберите исходный участок.";
  }

  async function run(message, operation) {
    if (busy) {
      setStatus("Дождитесь завершения кадастрового запроса.", true);
      return false;
    }
    busy = true;
    syncControls();
    setStatus(message);
    try {
      await operation();
      return true;
    } catch (error) {
      setStatus(error.message, true);
      return false;
    } finally {
      busy = false;
      syncControls();
    }
  }

  function display(area, prepared) {
    const selected = neighbors.get(selectedId) ?? source;
    selectedId = selected.id;
    cadastreMap.replace(source, neighbors, result?.buffer, selected, prepared);
    panel.render(source, neighbors, result, selected);
    if (area) cadastreMap.fit(area);
  }

  function adopt(parcel) {
    if (source?.id !== parcel.id || JSON.stringify(source.geometry) !== JSON.stringify(parcel.geometry)) {
      result = undefined;
      neighbors.clear();
      selectedId = parcel.id;
      sourceRevision = undefined;
      panel.setAttempt();
    }
    source = parcel;
    field.value = parcel.id;
    field.removeAttribute("aria-invalid");
    display("source");
    onSelect(parcel);
    syncControls();
    changed();
  }

  function findNumber(number) {
    return run("Ищем исходный участок в ЕГРН…", async () => {
      const data = await request(`${experimentApi}/source?number=${encodeURIComponent(number)}`);
      if (!isParcel(data.parcel, number)) {
        throw new Error("Сервер не вернул корректный площадной контур для указанного номера.");
      }
      adopt(data.parcel);
      const notes = Array.isArray(data.warnings) ? data.warnings : [];
      setStatus([`Исходный участок ${number} найден.`, data.cache_hit ? "Ответ из кеша." : "", ...notes]
        .filter(Boolean).join(" "), false, { transient: !notes.length, warning: Boolean(notes.length) });
    });
  }

  function confirmSource(parcel, number) {
    if (busy) throw new Error("Дождитесь завершения кадастрового запроса.");
    if (!isParcel(parcel, number)) throw new Error("Для этого участка нет проверенного контура.");
    adopt({ ...parcel, properties: { ...parcel.properties, measurement: { relation: "source" } } });
    showCadastre();
    setStatus(`Исходный участок ${number} выбран. Можно запустить исследование.`, false, { transient: true });
  }

  syncControls();
  field.addEventListener("input", syncControls);
  radiusInput.addEventListener("change", () => {
    generation += 1;
    researchRequest?.abort();
    panel.setAttempt();
    setStatus(`Для следующего исследования выбрано ${radiusLabel(Number(radiusInput.value))}.`, false, { transient: true });
    syncControls();
    changed();
  });

  request("/api/cadastre/capabilities", {}, 5000).then(data => {
    if (!Array.isArray(data.radii) || !data.radii.some(item => item?.radius_m === 500 && item.enabled === true)) return;
    const disabled = [];
    for (const option of radiusInput.options) {
      const value = Number(option.value);
      option.disabled = !data.radii.some(item => item?.radius_m === value && item.enabled === true);
      if (option.disabled) disabled.push(value);
    }
    availability.textContent = disabled.length
      ? `Радиусы ${disabled.join(" и ")} м выключены на сервере до проверки НСПД.` : "";
    syncControls();
  }).catch(() => {
    availability.textContent = "Настройки радиусов недоступны, можно исследовать 500 м.";
  });

  research.addEventListener("click", () => {
    if (research.disabled) return;
    const radius = Number(radiusInput.value);
    const token = generation;
    const current = () => token === generation;
    const number = source.id;
    panel.setAttempt();
    run(`Загружаем участки в пределах ${radiusLabel(radius)} от границ исходного участка…`, async () => {
      const controller = new AbortController();
      researchRequest = controller;
      try {
        const body = { number, radius_m: radius, source_revision: sourceRevision };
        const data = await request(researchApi, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        }, 40000);
        if (!current()) return;
        const nextNeighbors = readNeighbors(data, number, radius);
        if (!/^[0-9a-f]{64}$/.test(data.source_revision) ||
            (sourceRevision && data.source_revision !== sourceRevision) ||
            JSON.stringify(data.source.geometry) !== JSON.stringify(source.geometry)) {
          throw new Error("Исходный контур изменился. Найдите участок снова перед исследованием.");
        }
        const prepared = await cadastreMap.prepare(data.source, nextNeighbors, current);
        if (!current()) return;
        source = data.source;
        sourceRevision = data.source_revision;
        result = data;
        neighbors = nextNeighbors;
        display("buffer", prepared);
        onSelect(neighbors.get(selectedId) ?? source, true);
        changed();
        const warning = data.coverage === "partial" || Boolean(data.warnings?.length);
        const count = `${radiusLabel(radius)}. Соседних участков: ${neighbors.size}.`;
        const cache = data.cache_hit ? " Ответ из кеша." : "";
        if (warning) {
          const heading = data.coverage === "partial" ? "Получена неполная выборка." : "Исследование завершено с предупреждениями.";
          setStatus(`${heading} ${count} Подробнее во вкладке «Кадастр».`, false, { warning: true });
        } else {
          setStatus(`Кадастровое исследование завершено. ${count}${cache} Полнота выдачи НСПД не подтверждена.`,
            false, { transient: true });
        }
      } catch (error) {
        if (!current()) return;
        const title = error.coverage === "partial" ? "Неполная выдача" : "Ошибка исследования";
        const message = `${title} (${radiusLabel(radius)}). ${error.message}`;
        panel.setAttempt(message);
        throw new Error(message);
      } finally {
        researchRequest = undefined;
      }
    });
  });

  function getOsmOrigin() {
    const point = source?.properties.label_point;
    return isPosition(point) ? { lat: point[1], lon: point[0], label: `Внутренняя точка участка ${source.id}` } : null;
  }

  async function restore(data, state) {
    generation += 1;
    researchRequest?.abort();
    radiusInput.value = String(state.cadastre_radius);
    field.value = state.query;
    field.removeAttribute("aria-invalid");
    setStatus("");
    panel.setAttempt();
    source = data?.source;
    result = data?.result ? { ...data.result, source } : undefined;
    selectedId = state.cadastre_selected;
    sourceRevision = result?.source_revision;
    neighbors = result ? readNeighbors(result, source.id, result.radius_m) : new Map();
    if (source) {
      const prepared = await cadastreMap.prepare(source, neighbors, () => true);
      display(null, prepared);
    } else {
      selectedId = undefined;
      cadastreMap.clear();
      panel.clear();
    }
    syncControls();
  }

  return {
    findNumber, confirmSource, setStatus, getOsmOrigin, refreshVisibility: cadastreMap.refreshVisibility,
    selection: () => neighbors.get(selectedId) ?? source,
    parcelsAt: cadastreMap.parcelsAt, selectOnMap: id => select(id, true), restore, busy: () => busy,
    snapshot: () => ({ source, result, selectedId, radius: Number(radiusInput.value), query: field.value }),
  };
}
