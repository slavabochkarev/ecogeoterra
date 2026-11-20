import { request } from "./request.js";

const missing = "Нет данных";

function displayDate(value) {
  const parts = typeof value === "string" && value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return parts ? [parts[3], parts[2], parts[1]].join(".") : null;
}

function describeDocument(document) {
  if (!document) return null;
  const number = document.number && "№ " + document.number;
  const date = displayDate(document.date);
  return [document.name, [number, date && "от " + date].filter(Boolean).join(" "), document.issuer]
    .filter(Boolean).join("\n");
}

export function installOoptDetails(element, formatDistance, onData) {
  const panel = element("oopt");
  const list = element("oopt-list-view");
  const selection = element("oopt-selection");
  const selectionNumber = element("oopt-selection-number");
  const openButton = element("oopt-details-open");
  const backButton = element("oopt-details-back");
  const retryButton = element("oopt-details-retry");
  const card = element("oopt-card");
  const title = element("oopt-card-title");
  const status = element("oopt-card-status");
  const fields = element("oopt-card-fields");
  const restrictions = element("oopt-card-restrictions");
  const entries = new Map();
  let zone, receivedAt, opened = false, listScroll = 0;

  function nameFor(item) {
    return entries.get(item.registry_number)?.data?.details?.name || item.name;
  }

  function render() {
    panel.classList.toggle("is-card-open", opened);
    list.hidden = opened;
    selection.hidden = !zone;
    card.hidden = !opened;
    selectionNumber.textContent = zone?.registry_number ?? "";
    if (!opened || !zone) return;

    const entry = entries.get(zone.registry_number);
    const remote = entry?.data?.details;
    const attributes = { ...zone.attributes, ...remote };
    const name = nameFor(zone);
    title.textContent = name || "Карточка ООПТ";
    card.setAttribute("aria-busy", String(Boolean(entry?.pending)));
    const message = entry?.pending ? "Загружаем сведения НСПД…"
      : entry?.error || (entry?.data && !remote ? "Дополнительная запись НСПД по этому номеру не найдена." : "");
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle("error", Boolean(entry?.error));
    retryButton.hidden = !entry?.error;

    const rows = [
      ...(!name ? [["Название ООПТ", null]] : []),
      ["Реестровый номер границы", zone.registry_number],
      ["Расстояние от выбранного объекта", formatDistance(zone.distance_m)],
      ...(zone.inside ? [
        ["Положение точки", "Внутри ООПТ"],
        ["Расстояние до границы", formatDistance(zone.boundary_distance_m)],
      ] : []),
      ["Вид ООПТ", attributes.zone_type],
      ["Вид объекта реестра", attributes.boundary_type],
      ["Дата регистрации границы", displayDate(attributes.registration_date)],
      ["Документ в записи ЕГРН", describeDocument(attributes.document)],
      ["Местоположение", null],
      ["Код кадастрового округа или района", attributes.cadastral_district],
      ["Площадь по сведениям НСПД", null],
      ["Прежний учётный номер", attributes.old_account_number],
      ["Обновление записи НСПД", displayDate(attributes.updated_at)],
      ["Дата получения сведений", displayDate(remote ? entry.data.retrieved_at : receivedAt)],
      ["Источник", remote ? "НСПД · Зоны и территории" : "НСПД · пространственный поиск"],
      ["Слой НСПД", attributes.layer_name],
    ];
    const content = document.createDocumentFragment();
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      description.textContent = value || missing;
      row.append(term, description);
      content.append(row);
    }
    fields.replaceChildren(content);
    restrictions.hidden = !attributes.restrictions;
    element("oopt-card-restrictions-text").textContent = attributes.restrictions || "";
  }

  function setZone(next, retrievedAt) {
    const switchingCard = opened && next?.registry_number !== zone?.registry_number;
    if (next?.registry_number !== zone?.registry_number) {
      opened = false;
      restrictions.open = false;
    }
    zone = next;
    receivedAt = retrievedAt;
    render();
    if (switchingCard) panel.scrollTop = listScroll;
  }

  function close() {
    opened = false;
    render();
    panel.scrollTop = listScroll;
    openButton.focus({ preventScroll: true });
  }

  async function load(retry = false) {
    const number = zone.registry_number;
    const previous = entries.get(number);
    if (previous && (!retry || previous.pending || previous.data)) return;
    // The spatial response can already contain the registry fields.
    const inline = zone.attributes;
    if (!previous && zone.name && inline?.boundary_type && inline.registration_date && inline.document) return;
    const entry = { pending: true };
    entries.set(number, entry);
    render();
    try {
      const data = await request("/api/oopt/details?number=" + encodeURIComponent(number), {}, 40000);
      if (data.registry_number !== number ||
          (data.details !== null && (!data.details || typeof data.details !== "object" || Array.isArray(data.details)))) {
        throw new Error("Сервер вернул карточку для другого объекта или неполные сведения.");
      }
      entry.data = data;
      onData(number);
    } catch (error) {
      entry.error = "Подробные сведения не загружены. " + error.message;
    } finally {
      entry.pending = false;
      if (zone?.registry_number === number) render();
    }
  }

  openButton.addEventListener("click", () => {
    if (!zone) return;
    listScroll = panel.scrollTop;
    opened = true;
    render();
    panel.scrollTop = 0;
    title.focus({ preventScroll: true });
    load();
  });
  backButton.addEventListener("click", close);
  retryButton.addEventListener("click", () => load(true));
  card.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  });
  return { setZone, nameFor };
}
