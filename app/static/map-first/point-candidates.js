import { isParcel } from "./cadastre.js";
import { formatDistance } from "./cadastre-format.js";
import { descriptionList, parcelDetails } from "./cadastre-panel.js";
import { formatCoordinates } from "./coordinates.js";
import { request } from "./request.js";

const relations = {
  inside: "Точка внутри участка",
  boundary: "Точка на границе",
  near: "Рядом с точкой",
  unverified: "Контур не подтверждён",
};

function relationText(candidate) {
  const title = relations[candidate.relation];
  return candidate.relation === "near" && Number.isFinite(candidate.distance_m)
    ? `${title} · ${formatDistance(candidate.distance_m)}` : title;
}

export function createPointSearch(map, leaflet, layers, element, onConfirm, showCoordinates) {
  const section = element("point-search");
  const list = element("source-candidates");
  const find = element("find-near-point");
  const confirm = element("confirm-source");
  const card = element("candidate-card");
  const status = element("point-status");
  const pane = map.createPane("sourceCandidates");
  pane.style.zIndex = 418;
  const marker = leaflet.circleMarker([0, 0], {
    pane: "markerPane", radius: 8, color: "#fff", weight: 3, fillColor: "#136957",
    fillOpacity: 1, interactive: false,
  });
  const rows = new Map();
  let anchor;
  let selected;
  let currentRequest;
  let candidates = new Map();

  const outlines = leaflet.geoJSON(null, {
    pane: "sourceCandidates", bubblingMouseEvents: false, smoothFactor: 0,
    style(feature) {
      const choice = candidates.get(feature.id);
      const contains = choice.relation === "inside" || choice.relation === "boundary";
      const active = choice === selected;
      return { color: active ? "#087463" : contains ? "#258458" : "#a36317",
        weight: active ? 4.5 : contains ? 3 : 2.5, dashArray: contains ? "" : "6 4",
        fillOpacity: active ? 0.23 : contains ? 0.16 : 0.08 };
    },
    onEachFeature(feature, layer) {
      layer.on("click", () => {
        showCoordinates();
        select(feature.id);
      });
    },
  });

  function setStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
  }

  function refreshVisibility() {
    if (layers.boundaries.visible) outlines.addTo(map);
    else outlines.remove();
  }

  function syncAction() {
    find.disabled = !anchor || Math.abs(anchor.lat) > 85 || Boolean(currentRequest);
    confirm.disabled = !selected?.parcel || Boolean(currentRequest);
  }

  function select(number) {
    rows.get(selected?.number)?.removeAttribute("aria-current");
    selected = candidates.get(number);
    card.hidden = !selected;
    if (selected) {
      rows.get(selected.number)?.setAttribute("aria-current", "true");
      element("candidate-number").textContent = selected.number;
      element("candidate-relation").textContent = relationText(selected);
      descriptionList(element("candidate-fields"), parcelDetails(selected.parcel ?? selected));
    }
    outlines.resetStyle();
    outlines.eachLayer(layer => {
      if (layer.feature.id === selected?.number) layer.bringToFront();
    });
    syncAction();
  }

  function display(items) {
    candidates = new Map(items.map(item => [item.number, item]));
    rows.clear();
    selected = undefined;
    const contents = document.createDocumentFragment();
    for (const item of candidates.values()) {
      const row = document.createElement("li");
      const button = document.createElement("button");
      const number = document.createElement("strong");
      const relation = document.createElement("span");
      button.type = "button";
      button.className = `button location-option relation-${item.relation}`;
      button.dataset.number = item.number;
      number.textContent = item.number;
      relation.textContent = relationText(item);
      button.append(number, relation);
      rows.set(item.number, button);
      row.append(button);
      contents.append(row);
    }
    list.replaceChildren(contents);
    list.hidden = !candidates.size;
    card.hidden = true;
    outlines.clearLayers().addData(items.filter(item => item.parcel).map(item => item.parcel));
    syncAction();
  }

  function clear() {
    anchor = undefined;
    // Any late response belongs to the previous point.
    currentRequest = undefined;
    marker.remove();
    display([]);
    section.hidden = true;
  }

  function place(location) {
    clear();
    anchor = location;
    marker.setLatLng([location.lat, location.lon]).addTo(map);
    const address = element("location-address");
    address.textContent = location.address ?? "";
    address.hidden = !location.address;
    element("location-coordinates").textContent = formatCoordinates(marker.getLatLng());
    setStatus(Math.abs(location.lat) > 85 ? "Подбор участка доступен для широт в пределах ±85°." : "");
    section.hidden = false;
    syncAction();
  }

  find.addEventListener("click", async () => {
    if (find.disabled) return;
    const { lat, lon } = anchor;
    const token = {};
    currentRequest = token;
    syncAction();
    setStatus("Ищем земельные участки в пределах 15 м от точки…");
    try {
      const response = await request("/api/cadastre/source-candidates", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lat, lon }),
      }, 35000);
      if (currentRequest !== token) return;
      const items = response.candidates;
      if (response.point?.lat !== lat || response.point?.lon !== lon || !Array.isArray(items) ||
          items.some(item => !item || typeof item.number !== "string" || !Object.hasOwn(relations, item.relation) ||
            (item.relation === "unverified" ? item.parcel !== null : !isParcel(item.parcel, item.number)))) {
        throw new Error("Сервер вернул некорректные данные кадастровых кандидатов.");
      }
      display(items);
      if (items.some(item => item.parcel) && map.getZoom() < 18) {
        map.setZoomAround(marker.getLatLng(), 18, { animate: false });
      }
      const summary = [
        items.length ? `Найдено кандидатов: ${items.length}. Выберите участок для просмотра.`
          : "В полученных данных участки у точки не найдены. Уточните ориентир или введите кадастровый номер.",
        response.coverage === "unknown" ? "Полнота выдачи НСПД неизвестна." : "",
        ...(Array.isArray(response.warnings) ? response.warnings.filter(note => typeof note === "string") : []),
      ];
      setStatus(summary.filter(Boolean).join(" "));
    } catch (error) {
      if (currentRequest === token) setStatus(error.message, true);
    } finally {
      if (currentRequest === token) {
        currentRequest = undefined;
        syncAction();
      }
    }
  });

  list.addEventListener("click", event => {
    const button = event.target.closest("button[data-number]");
    if (button) select(button.dataset.number);
  });
  confirm.addEventListener("click", () => {
    if (confirm.disabled) return;
    try {
      onConfirm(selected);
      clear();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  refreshVisibility();
  return { place, clear, refreshVisibility, busy: () => Boolean(currentRequest) };
}
