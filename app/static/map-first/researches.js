import { request } from "./request.js";
import { installLandReports } from "./land-reports.js";

const api = "/api/researches";
const debounceMs = 480;
const send = (path, method, data) => request(path, {
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
});

function sameDatasets(left, right) {
  return {
    cadastre: left.cadastre.source === right.cadastre.source && left.cadastre.result === right.cadastre.result,
    osm: left.osm.data === right.osm.data && left.osm.requestedCategories === right.osm.requestedCategories,
  };
}

function cadastrePayload(cadastre) {
  if (!cadastre.source) return null;
  let result = null;
  if (cadastre.result) {
    const { source, ...withoutSource } = cadastre.result;
    result = withoutSource;
  }
  return { source: cadastre.source, result };
}

export function installResearches(element, workspace, { snapshot, restore, prepareOpen, isBusy, emptyState }) {
  const tools = element("study-tools");
  const toggle = element("study-toggle");
  const actions = element("study-actions");
  const name = element("study-name");
  const title = element("study-title");
  const status = element("study-status");
  const indicator = element("study-indicator");
  const alert = element("study-alert");
  const alertDialog = element("study-alert-dialog");
  const alertMessage = element("study-alert-message");
  const rename = element("study-rename");
  const renameDialog = element("study-rename-dialog");
  const save = element("study-save");
  const close = element("study-close");
  const retry = element("study-retry");
  const resume = element("study-continue");
  const dialog = element("study-dialog");
  const list = element("study-list");
  const listStatus = element("study-list-status");
  const reports = installLandReports(element);
  let active;
  let pendingResume;
  let baseline;
  let timer;
  let saving;
  let switching = false;
  let dirty = false;
  let blocked = false;
  let explicitSave = false;
  let nameDraft;

  function message(text = "", kind = "") {
    status.textContent = text;
    indicator.hidden = kind !== "saving" && kind !== "saved";
    indicator.dataset.state = kind;
    indicator.textContent = kind === "saved" ? "✓" : "";
    indicator.title = text;
    alert.hidden = kind !== "alert";
    alert.title = kind === "alert" ? text : "";
    alertMessage.textContent = alert.title;
    retry.hidden = !blocked || !dirty;
    if (alert.hidden && document.activeElement === alert) toggle.focus();
  }

  function showListMessage(text = "") {
    listStatus.textContent = text;
    listStatus.hidden = !text;
  }

  function activeControls() {
    name.hidden = !active;
    name.textContent = name.title = active?.title ?? "";
    rename.disabled = save.disabled = close.disabled = !active;
    resume.hidden = !pendingResume || Boolean(active);
    resume.title = pendingResume?.title ?? "";
  }

  function showActions(open, restoreFocus = false) {
    actions.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (restoreFocus) toggle.focus();
  }

  function schedule() {
    if (!active || switching) return;
    dirty = true;
    if (blocked) return;
    message("Сохранение…", "saving");
    clearTimeout(timer);
    timer = setTimeout(flush, debounceMs);
  }

  async function savePending() {
    while (active && dirty) {
      const next = snapshot();
      const same = sameDatasets(baseline, next);
      const draft = nameDraft;
      const force = explicitSave;
      dirty = false;
      explicitSave = false;
      if (!force && same.cadastre && same.osm && !draft &&
          JSON.stringify(baseline.state) === JSON.stringify(next.state)) continue;
      const payload = { revision: active.revision, state: next.state };
      if (draft) payload.title = draft;
      if (!same.cadastre) payload.cadastre = cadastrePayload(next.cadastre);
      if (!same.osm) payload.osm = next.osm.data ? {
        result: next.osm.data, requested_categories: next.osm.requestedCategories,
      } : null;
      message("Сохранение…", "saving");
      try {
        ({ research: active } = await send(`${api}/${active.id}`, "PUT", payload));
        baseline = next;
        if (nameDraft === draft) nameDraft = undefined;
        activeControls();
      } catch (error) {
        dirty = blocked = true;
        explicitSave ||= force;
        message(`Ошибка сохранения. ${error.message}`, "alert");
        return false;
      }
    }
    blocked = false;
    if (active) message("Сохранено", "saved");
    return true;
  }

  function flush(force = false) {
    clearTimeout(timer);
    if (!active) return Promise.resolve(true);
    if (force) dirty = explicitSave = true;
    if (!saving) saving = savePending().finally(() => { saving = undefined; });
    return saving;
  }

  async function transition(action, focus = toggle) {
    if (switching) return;
    if (isBusy()) {
      const text = "Дождитесь завершения текущего поиска или исследования.";
      if (dialog.open) showListMessage(text);
      else if (!blocked) message(text, "alert");
      return;
    }
    switching = true;
    workspace.inert = true;
    try {
      if (!await flush()) {
        if (dialog.open) showListMessage("Сначала повторите сохранение текущего исследования.");
        return;
      }
      await action();
    } catch (error) {
      if (dialog.open) showListMessage(error.message);
      else message(error.message, "alert");
    } finally {
      switching = false;
      workspace.inert = false;
      if (!dialog.open) {
        if (!element("exit-map").hidden) element("exit-map").focus();
        else focus.focus();
      }
    }
  }

  async function display(document) {
    await restore(document);
    active = document.research;
    baseline = active ? snapshot() : undefined;
    nameDraft = pendingResume = undefined;
    dirty = blocked = explicitSave = false;
    activeControls();
    if (active) message("Сохранено", "saved");
    else message();
  }

  function open(id) {
    if (!id) return;
    transition(async () => {
      await prepareOpen();
      const document = await send(`${api}/${id}/open`, "POST");
      await display(document);
      if (dialog.open) dialog.close();
    });
  }

  toggle.addEventListener("click", () => showActions(actions.hidden));
  tools.addEventListener("focusout", event => {
    if (!actions.hidden && event.relatedTarget && !tools.contains(event.relatedTarget)) showActions(false);
  });
  document.addEventListener("pointerdown", event => {
    if (!actions.hidden && !tools.contains(event.target)) showActions(false);
  });
  tools.addEventListener("keydown", event => {
    if (event.key !== "Escape" || actions.hidden) return;
    event.preventDefault();
    showActions(false, true);
  });
  actions.addEventListener("click", event => {
    if (event.target.closest(".button:not(:disabled)")) showActions(false, true);
  }, { capture: true });

  element("study-new").addEventListener("click", () => transition(async () => {
    const { camera, basemap } = snapshot().state;
    const state = { ...emptyState(), camera, basemap };
    await display(await send(api, "POST", { state }));
  }, element("cadastre-number")));
  close.addEventListener("click", () => transition(async () => {
    if (!active) return;
    await send(`${api}/${active.id}/close`, "POST", { revision: active.revision });
    await display({ research: null, state: emptyState(), cadastre: null, osm: null });
  }));
  alert.addEventListener("click", () => {
    showActions(false);
    alertDialog.showModal();
  });
  retry.addEventListener("click", () => {
    alertDialog.close();
    toggle.focus();
    flush();
  });
  save.addEventListener("click", () => flush(true));
  resume.addEventListener("click", () => open(pendingResume?.id));
  element("study-reports").addEventListener("click", () => reports.open(active?.id, dirty || Boolean(saving)));

  rename.addEventListener("click", () => {
    if (!active) return;
    title.setCustomValidity("");
    title.value = nameDraft ?? active.title;
    renameDialog.showModal();
    title.select();
  });
  element("study-rename-cancel").addEventListener("click", () => renameDialog.close());
  title.addEventListener("input", () => title.setCustomValidity(""));
  element("study-rename-form").addEventListener("submit", event => {
    event.preventDefault();
    const value = title.value.trim();
    if (!value) {
      title.setCustomValidity("Введите название исследования.");
      title.reportValidity();
      return;
    }
    nameDraft = value;
    dirty = true;
    renameDialog.close();
    flush();
  });

  element("study-open").addEventListener("click", async () => {
    if (dialog.open) return;
    list.replaceChildren();
    showListMessage("Читаем список…");
    dialog.showModal();
    try {
      const data = await request(api, { cache: "no-store" });
      if (!dialog.open) return;
      showListMessage(data.items.length
        ? data.skipped ? `Повреждённых или неподдерживаемых записей: ${data.skipped}.` : ""
        : data.skipped ? "Пригодных исследований нет. Проверьте файлы хранилища." : "Сохранённых исследований пока нет.");
      for (const item of data.items) {
        const row = document.createElement("li");
        const button = document.createElement("button");
        const name = document.createElement("strong");
        const meta = document.createElement("span");
        button.type = "button";
        button.className = "button location-option research-list-row";
        button.dataset.id = item.id;
        name.textContent = item.title;
        const date = new Date(item.updated_at).toLocaleString("ru-RU");
        meta.textContent = [item.number ?? "Без участка", date, item.cadastre_radius ? `${item.cadastre_radius} м` : "Без выборки ЕГРН",
          item.has_osm ? "Есть OSM" : "Без OSM"].join(" · ");
        button.append(name, meta);
        row.append(button);
        list.append(row);
      }
    } catch (error) {
      if (dialog.open) showListMessage(error.message);
    }
  });
  list.addEventListener("click", event => open(event.target.closest("button[data-id]")?.dataset.id));
  dialog.addEventListener("cancel", event => { if (switching) event.preventDefault(); });
  for (const modal of [dialog, renameDialog, alertDialog]) {
    modal.addEventListener("keydown", event => {
      if (event.key === "Escape") event.stopPropagation();
    });
  }
  window.addEventListener("beforeunload", event => {
    if (dirty || saving) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && dirty && !blocked && !switching) flush();
  });
  request(`${api}/active`, { cache: "no-store" }).then(data => {
    if (active || switching) return;
    pendingResume = data.active;
    activeControls();
    if (data.warning) message(data.warning, "alert");
  }).catch(error => {
    if (!active && !switching) message(error.message, "alert");
  });
  return { schedule };
}
