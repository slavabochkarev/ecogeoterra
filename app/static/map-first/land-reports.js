import { request } from "./request.js";

const missing = "Нет сведений";
const formats = {
  all: { title: "Земельные участки в радиусе исследования", fields: ["Категория земель", "ВРИ"] },
  normalized: { title: "Земельные участки с нормируемыми видами использования", fields: ["ВРИ", "Группа использования / причина проверки", "Статус ВРИ"] },
};
const classification = {
  matched: "Подтверждённые соответствия правилам отбора",
  review: "Требует проверки",
};
const reviewNote = "«Требует проверки» означает, что по исходному ВРИ нельзя однозначно выбрать группу. Причина указана в строке.";
const coverage = {
  complete: "Полнота выдачи НСПД не подтверждена",
  unknown: "Полнота выдачи НСПД не подтверждена",
  partial: "Неполная выборка",
};
const distanceFormat = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false });
const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  dateStyle: "short", timeStyle: "medium", timeZone: "UTC",
});

function date(value) {
  const moment = new Date(value);
  return value && Number.isFinite(moment.valueOf()) ? dateFormat.format(moment) + " UTC" : missing;
}

function overview({ research, report }) {
  const metadata = [
    ["Исследование", research.title],
    ["Исходный участок", research.source_number ?? missing],
  ];
  if (report) metadata.push(
    ["Радиус", report.radius_m + " м"],
    ["Дата получения данных", date(report.retrieved_at)],
    ["Статус полноты", coverage[report.coverage]],
  );
  return metadata;
}

function columns(kind) {
  return ["№", "Кадастровый номер", ...formats[kind].fields, "Направление", "Расстояние, м"];
}

function cells(row, index, kind) {
  const fields = kind === "all" ? [row.category ?? missing, row.use ?? missing]
    : [row.use ?? missing, row.review_reason ?? row.group,
      row.classification === "review" ? classification.review : "Соответствует правилам отбора"];
  return [index + 1, row.number, ...fields, row.direction ?? "Не определено",
    row.distance_m === null ? missing : distanceFormat.format(row.distance_m)];
}

function sections(report, kind) {
  if (kind === "all") return [{ rows: report.all }];
  return Object.entries(classification).map(([key, title]) => ({
    title, rows: report.normalized.filter(row => row.classification === key), review: key === "review",
  }));
}

function summary(report, kind) {
  if (kind === "all") return "Участков в сохранённом буфере: " + report.all.length + ".";
  const { matched, review, outside } = report.classification_counts;
  return "Совпадений по справочнику: " + matched + ". Требуют проверки: " + review
    + ". Вне групп отбора: " + outside + ". Справочник ВРИ " + report.rules_version
    + ". По ВРИ выделяются кандидаты для проверки применимых нормативов.";
}

function contourNote(report) {
  return report.invalid_contours ? "Пропущено участков с некорректным контуром: " + report.invalid_contours + "." : "";
}

function escapeMarkdown(value) {
  return String(value).replace(/[\p{Cc}\p{Zl}\p{Zp}]+/gu, " ")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replace(/[\\\x60*_[\]{}()#+!|~]/g, "\\$&");
}

export function markdown(reportData, kind, generatedAt = new Date()) {
  const { report } = reportData;
  const lines = [
    "# " + formats[kind].title, "",
    ...[...overview(reportData), ["Дата формирования отчёта", date(generatedAt)]]
      .map(([label, value]) => "**" + label + ":** " + escapeMarkdown(value) + "  "),
    "", "Отчёт сформирован по сохранённым данным исследования.", "",
    summary(report, kind), "",
  ];
  if (contourNote(report)) lines.push(contourNote(report), "");
  if (kind === "normalized") lines.push(reviewNote, "");
  const tableLine = values => "| " + values.map(escapeMarkdown).join(" | ") + " |";
  const headers = columns(kind);
  let index = 0;
  for (const section of sections(report, kind)) {
    if (section.title) lines.push("## " + section.title, "");
    lines.push(tableLine(headers), "| " + headers.map(() => "---").join(" | ") + " |");
    section.rows.forEach(row => lines.push(tableLine(cells(row, index++, kind))));
    lines.push("");
  }
  return lines.join("\n") + "\n";
}

function download(data, kind) {
  const url = URL.createObjectURL(new Blob([markdown(data, kind)], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "lands_" + kind + "_" + data.report.radius_m + "m.md";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderTable(panel, report, kind) {
  const intro = document.createElement("p");
  intro.className = "land-report-summary";
  intro.textContent = summary(report, kind);
  const description = document.createElement("p");
  description.className = "land-report-summary";
  description.textContent = reviewNote;
  const table = document.createElement("table");
  table.className = "land-report-table";
  table.setAttribute("aria-label", formats[kind].title);
  const header = table.createTHead().insertRow();
  const headers = columns(kind);
  for (const label of headers) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    header.append(cell);
  }
  let index = 0;
  for (const section of sections(report, kind)) {
    const body = table.createTBody();
    if (section.title) {
      const label = document.createElement("th");
      label.className = "land-report-section";
      label.scope = "rowgroup";
      label.colSpan = headers.length;
      label.textContent = section.title + " · " + section.rows.length;
      body.insertRow().append(label);
    }
    for (const row of section.rows) {
      const line = body.insertRow();
      for (const value of cells(row, index++, kind)) line.insertCell().textContent = value;
      if (section.review) line.cells[4].className = "land-review";
    }
  }
  panel.replaceChildren(intro);
  if (kind === "normalized") panel.append(description);
  panel.append(table);
}

export function installLandReports(element, retrySave) {
  const dialog = element("land-reports");
  const meta = element("land-report-meta");
  const status = element("land-report-status");
  const note = element("land-report-note");
  const retry = element("land-report-retry");
  const actions = element("land-report-actions");
  const kinds = Object.keys(formats);
  const tabs = kinds.map(kind => element("land-" + kind + "-tab"));
  const panels = kinds.map(kind => element("land-" + kind + "-panel"));
  let current = 0;
  let researchId;
  let data;
  let readError;
  let loading;
  let savedKey;
  let saveState;

  function select(index, focus = false) {
    if (!data?.report) return;
    current = index;
    tabs.forEach((tab, position) => {
      tab.setAttribute("aria-selected", String(position === current));
      tab.tabIndex = position === current ? 0 : -1;
      panels[position].hidden = position !== current;
    });
    const panel = panels[current];
    if (!panel.firstChild) renderTable(panel, data.report, kinds[current]);
    if (focus) tabs[current].focus();
  }

  function renderStatus() {
    const { pending, error } = saveState ?? {};
    let text = "";
    if (!researchId) text = "Откройте или создайте исследование и выполните кадастровое исследование.";
    else if (error) text = error;
    else if (pending) text = "Сохранение исследования…";
    else if (loading) text = "Читаем сохранённую выборку…";
    else if (readError) text = readError;
    else if (!data?.report) text = "Кадастровое исследование ещё не выполнено. Выберите участок и нажмите «Исследовать».";
    status.textContent = text;
    status.hidden = !text;
    retry.hidden = !error;
    note.textContent = [
      data?.report && (pending || loading || readError)
        ? "Показана последняя загруженная сохранённая выборка." : "",
      data?.report ? contourNote(data.report) : "",
      readError && (pending || error) ? readError : "",
    ].filter(Boolean).join(" ");
    note.hidden = !note.textContent;
  }

  function renderData(saved) {
    data = saved;
    meta.replaceChildren(...overview(saved).map(([label, value]) => {
      const item = document.createElement("div");
      const key = document.createElement("dt");
      const detail = document.createElement("dd");
      key.textContent = label;
      detail.textContent = value;
      item.append(key, detail);
      return item;
    }));
    meta.hidden = false;
    actions.hidden = !data.report;
    for (const panel of panels) {
      panel.replaceChildren();
      panel.hidden = true;
    }
    select(current);
  }

  async function readSaved() {
    loading?.abort();
    const controller = new AbortController();
    loading = controller;
    readError = "";
    renderStatus();
    try {
      const saved = await request("/api/researches/" + encodeURIComponent(researchId) + "/reports",
        { cache: "no-store", signal: controller.signal });
      if (dialog.open && loading === controller) renderData(saved);
    } catch (error) {
      if (dialog.open && loading === controller && !controller.signal.aborted) readError = error.message;
    } finally {
      if (loading === controller) {
        loading = undefined;
        if (dialog.open) renderStatus();
      }
    }
  }

  function open(id) {
    if (dialog.open) return;
    loading?.abort();
    researchId = id;
    current = 0;
    data = readError = loading = savedKey = saveState = undefined;
    meta.hidden = actions.hidden = true;
    for (const panel of panels) {
      panel.replaceChildren();
      panel.hidden = true;
    }
    dialog.showModal();
    renderStatus();
  }

  function sync(state) {
    if (!dialog.open || !researchId) return;
    saveState = state;
    if (state.savedKey !== savedKey) {
      savedKey = state.savedKey;
      readSaved();
    } else {
      renderStatus();
    }
  }

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => select(index));
    tab.addEventListener("keydown", event => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      select(next, true);
    });
  }
  dialog.addEventListener("keydown", event => {
    if (event.key === "Escape") event.stopPropagation();
  });
  dialog.addEventListener("close", () => {
    if (!dialog.open) loading?.abort();
  });
  retry.addEventListener("click", () => retrySave());
  element("land-report-download").addEventListener("click", () => download(data, kinds[current]));
  return { open, sync, isOpen: () => dialog.open };
}
