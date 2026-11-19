import { descriptionList } from "./cadastre-panel.js";
import { formatDistance, numberFormat } from "./cadastre-format.js";
import { formatCoordinates } from "./coordinates.js";

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

export function createOsmPanel(element, categories, selectObject) {
  const list = element("osm-list");
  const card = element("osm-card");
  const more = element("osm-more");
  const rows = new Map();
  const categoryInputs = new Map();
  let visible = [];
  let selected;
  let rendered = 0;
  let activeRow;

  function appendRows() {
    const fragment = document.createDocumentFragment();
    for (const feature of visible.slice(rendered, rendered + 100)) {
      const p = feature.properties;
      const row = node("button", null, "button parcel-choice osm-object-row");
      row.type = "button";
      row.dataset.osmId = feature.id;
      row.style.setProperty("--category-color", categories[p.category].color);
      const name = node("span");
      name.append(node("strong", p.name), node("small", p.type));
      row.append(name, node("span", formatDistance(p.distance_m)));
      if (selected === feature) {
        row.setAttribute("aria-current", "true");
        activeRow = row;
      }
      const item = node("li");
      item.append(row);
      fragment.append(item);
      rows.set(feature.id, row);
      rendered += 1;
    }
    list.append(fragment);
    more.hidden = rendered >= visible.length;
  }

  function renderList(features) {
    rows.clear();
    visible = features;
    rendered = 0;
    activeRow = undefined;
    list.replaceChildren();
    appendRows();
  }

  function renderCategories(active, counts) {
    const panel = element("osm-categories");
    for (const [key, category] of Object.entries(categories)) {
      if (!categoryInputs.has(key)) {
        const label = node("label", null, "osm-check");
        const input = node("input");
        input.type = "checkbox";
        input.value = key;
        const swatch = node("i");
        swatch.style.backgroundColor = category.color;
        swatch.setAttribute("aria-hidden", "true");
        const count = node("small");
        label.append(input, swatch, node("span", category.label), count);
        panel.append(label);
        categoryInputs.set(key, { input, count });
      }
      const { input, count } = categoryInputs.get(key);
      input.checked = active.has(key);
      count.textContent = counts ? numberFormat.format(counts[key] ?? 0) : "";
    }
  }

  function select(feature) {
    selected = feature;
    activeRow?.removeAttribute("aria-current");
    activeRow = rows.get(feature?.id);
    activeRow?.setAttribute("aria-current", "true");
    card.replaceChildren();
    card.hidden = !feature;
    if (!feature) return;

    const p = feature.properties;
    const heading = node("div", null, "osm-card-heading");
    const close = node("button", "×", "button icon");
    close.type = "button";
    close.setAttribute("aria-label", "Закрыть сведения об объекте OSM");
    close.addEventListener("click", () => selectObject(null, false));
    heading.append(node("h2", p.name), close);
    const link = node("a", `OSM ${p.osm_key} ↗`);
    link.href = `https://www.openstreetmap.org/${p.osm_type}/${p.osm_id}`;
    link.target = "_blank";
    link.rel = "noopener";
    const data = node("dl", null, "cadastre-fields");
    const angle = Number.isFinite(p.bearing_deg) ? ` · ${numberFormat.format(p.bearing_deg)}°` : "";
    const fields = [
      ["Тип", p.type], ["От опорной точки", formatDistance(p.distance_m)],
      ["Направление", p.direction ? p.direction + angle : "Не определено"],
      ["Основание", p.classification_basis],
    ];
    const nearest = p.nearest_point;
    if (feature.geometry.type === "Point") {
      const [lng, lat] = feature.geometry.coordinates;
      fields.push(["Координаты", formatCoordinates({ lat, lng })]);
    } else if (Number.isFinite(nearest?.lat) && Number.isFinite(nearest?.lon)) {
      fields.push(["Ближайшая точка геометрии", formatCoordinates({ lat: nearest.lat, lng: nearest.lon })]);
    }
    descriptionList(data, fields);
    const details = node("details", null, "cadastre-disclosure");
    const tags = Object.entries(p.tags ?? {}).sort(([a], [b]) => a.localeCompare(b));
    const tagsList = node("dl", null, "cadastre-fields");
    descriptionList(tagsList, tags);
    details.append(node("summary", `Исходные теги OSM (${tags.length})`), tagsList);
    card.append(heading, link, data, details);
  }

  more.addEventListener("click", appendRows);
  list.addEventListener("click", event => {
    const row = event.target.closest("button[data-osm-id]");
    if (row) selectObject(row.dataset.osmId, true);
  });
  return { renderList, renderCategories, select };
}
