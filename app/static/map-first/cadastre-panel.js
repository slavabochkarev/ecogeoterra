import { categories, formatDistance, numberFormat } from "./cadastre-format.js";

const missing = "Нет данных";
const dateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
const coverageLabels = {
  complete: "Полнота выдачи подтверждена.",
  unknown: "Полнота выдачи НСПД неизвестна.",
  partial: "Неполная выдача. Показаны только пригодные участки.",
};
const relations = {
  source: "",
  disjoint: "Не пересекаются",
  touches: "Участки соприкасаются",
  intersects: "Участки пересекаются",
};

export function descriptionList(list, rows) {
  const content = document.createDocumentFragment();
  for (const [label, value] of rows) {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    content.append(term, detail);
  }
  list.replaceChildren(content);
}

export function parcelDetails(feature) {
  const fields = feature.properties;
  return [
    ["Адрес", fields.address || missing],
    ["Площадь", Number.isFinite(fields.area_m2) ? `${numberFormat.format(fields.area_m2)} м²` : missing],
    ["Категория земель", fields.land_category || missing],
    ["ВРИ", fields.permitted_use || missing],
    ["Геометрия", fields.geometry_status || missing],
  ];
}

function measurementDistance(measurement) {
  const relation = measurement?.relation;
  if (relation === "source") return "Исходный участок";
  if (relation === "touches" || relation === "intersects") return "0 м";
  if (relation === "disjoint" && Number.isFinite(measurement.distance_m) && measurement.distance_m > 0) {
    return formatDistance(measurement.distance_m);
  }
  return "Расстояние не определено";
}

export function createCadastrePanel(element, onSelect) {
  const list = element("parcel-list");
  const more = element("show-more-parcels");
  const buttons = new Map();
  let current;
  let selectedId;
  let entries;
  let remaining;

  function select(feature) {
    selectedId = feature.id;
    const measurement = feature.properties.measurement;
    element("selected-empty").hidden = true;
    element("selected-parcel").hidden = false;
    element("selected-number").textContent = feature.id;
    element("selected-distance").textContent = measurementDistance(measurement);
    const relation = element("selected-relation");
    relation.textContent = relations[measurement?.relation] ?? "";
    relation.hidden = !relation.textContent;
    descriptionList(element("selected-fields"), parcelDetails(feature));
    current?.removeAttribute("aria-current");
    current = buttons.get(feature.id);
    current?.setAttribute("aria-current", "true");
  }

  function appendNeighbors() {
    const fragment = document.createDocumentFragment();
    for (let count = 0; count < 100 && remaining > 0; count++, remaining--) {
      const feature = entries.next().value;
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button parcel-choice";
      button.dataset.number = feature.id;
      const number = document.createElement("span");
      number.textContent = feature.id;
      const distance = document.createElement("span");
      const measurement = feature.properties.measurement;
      if (relations[measurement?.relation]) distance.textContent = measurementDistance(measurement);
      button.append(number, distance);
      buttons.set(feature.id, button);
      if (feature.id === selectedId) {
        button.setAttribute("aria-current", "true");
        current = button;
      }
      item.append(button);
      fragment.append(item);
    }
    list.append(fragment);
    more.hidden = remaining === 0;
  }

  function renderNeighbors(neighbors) {
    buttons.clear();
    entries = neighbors.values();
    remaining = neighbors.size;
    list.replaceChildren();
    if (!neighbors.size) {
      const empty = document.createElement("li");
      empty.className = "parcel-list-empty";
      empty.textContent = "В полученных данных соседних участков нет.";
      list.append(empty);
    }
    appendNeighbors();
    element("neighbor-count").textContent = `(${numberFormat.format(neighbors.size)})`;
  }

  list.addEventListener("click", event => {
    const button = event.target.closest("button[data-number]");
    if (button) onSelect(button.dataset.number);
  });
  more.addEventListener("click", appendNeighbors);

  function render(source, neighbors, result, selected) {
    element("source-empty").hidden = true;
    element("source-parcel").hidden = false;
    element("source-number").textContent = source.id;
    descriptionList(element("source-fields"), parcelDetails(source));
    element("research-empty").hidden = Boolean(result);
    element("research-results").hidden = !result;
    renderNeighbors(neighbors);
    select(selected);
    if (!result) return;

    const values = [...neighbors.values()];
    const date = new Date(result.retrieved_at ?? NaN);
    const rows = [
      ["Радиус от границ", `${numberFormat.format(result.radius_m)} м`],
      ["Всего участков", numberFormat.format(1 + values.length)],
      ["Соседние участки", numberFormat.format(values.length)],
    ];
    if (!values.length || values.some(feature => relations[feature.properties.measurement?.relation])) {
      const touches = values.filter(feature => feature.properties.measurement?.relation === "touches").length;
      rows.push(["Соприкасаются", numberFormat.format(touches)]);
    }
    rows.push(["Получено", Number.isNaN(date.getTime()) ? missing : dateFormat.format(date)]);
    descriptionList(element("research-summary"), rows);
    element("research-coverage").textContent = coverageLabels[result.coverage];

    const warningList = element("research-warnings");
    const warnings = Array.isArray(result.warnings) ? result.warnings.filter(note => typeof note === "string") : [];
    const invalid = (result.stats?.invalid_geometry ?? 0) + (result.stats?.invalid_record ?? 0);
    if (invalid > 0 && !result.coverage_details) warnings.push(`Пропущено записей с некорректными данными или геометрией: ${numberFormat.format(invalid)}.`);
    warningList.replaceChildren(...warnings.map(warning => {
      const item = document.createElement("li");
      item.textContent = warning;
      return item;
    }));
    warningList.hidden = !warnings.length;

    const totals = new Map(Object.keys(categories).map(key => [key, 0]));
    for (const feature of [source, ...values]) {
      const bucket = feature.properties.category_bucket;
      const key = totals.has(bucket) ? bucket : "unknown";
      totals.set(key, totals.get(key) + 1);
    }
    const entries = document.createDocumentFragment();
    for (const [key, count] of totals) {
      if (!count) continue;
      const category = categories[key];
      const row = document.createElement("li");
      const swatch = document.createElement("i");
      swatch.setAttribute("aria-hidden", "true");
      swatch.style.backgroundColor = category.color;
      const label = document.createElement("span");
      label.textContent = category.label;
      const quantity = document.createElement("strong");
      quantity.textContent = numberFormat.format(count);
      row.append(swatch, label, quantity);
      entries.append(row);
    }
    element("category-summary").replaceChildren(entries);
  }

  function setAttempt(message = "") {
    const attempt = element("research-attempt");
    attempt.textContent = message;
    attempt.hidden = !message;
  }

  function revealSelection() {
    requestAnimationFrame(() => {
      const panel = element("cadastre");
      const card = element("selected-parcel").parentElement;
      const top = card.getBoundingClientRect().top - panel.getBoundingClientRect().top;
      if (top < 0 || top + Math.min(card.offsetHeight, 110) > panel.clientHeight) {
        panel.scrollTop += top - 8;
      }
    });
  }

  function clear() {
    element("source-empty").hidden = false;
    element("source-parcel").hidden = true;
    element("research-empty").hidden = false;
    element("research-results").hidden = true;
    element("selected-empty").hidden = false;
    element("selected-parcel").hidden = true;
    renderNeighbors(new Map());
    selectedId = current = undefined;
    setAttempt();
  }

  return { render, select, revealSelection, setAttempt, clear };
}
