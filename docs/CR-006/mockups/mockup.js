"use strict";

(() => {
  const scene = document.body.dataset.preview;
  const workspace = document.getElementById("prototype");
  workspace.className = "workspace";
  const icon = glyph => `<span class="icon" aria-hidden="true">${glyph}</span>`;
  workspace.innerHTML = `
    <canvas class="map" role="img" aria-label="Условная карта с буфером, исходным и выбранным участками"></canvas>
    <header class="top surface" id="top">
      <div class="brand"><span class="mark" aria-hidden="true">◇</span><strong>ЭкоГеоТерра</strong></div>
      <form class="search" id="search">
        ${icon("⌕")}<input aria-label="Адрес, кадастровый номер или координаты" placeholder="Адрес, кадастровый номер, координаты" value="63:09:0102159:100" type="search">
        <button class="button" type="submit">Найти</button>
      </form>
      <div class="actions">
        <select id="radius" aria-label="Радиус исследования"><option>500 м</option><option>1000 м</option><option>2000 м</option></select>
        <button class="button primary" id="research" type="button">${icon("◎")}Исследовать</button>
      </div>
      <div class="view-tools">
        <button class="button" id="panel-toggle" aria-label="Панель сведений" aria-controls="sidebar" aria-expanded="false" type="button">${icon("▥")}<span class="tool-label">Сведения</span></button>
        <button class="button" id="focus-map" aria-label="Вся карта" type="button">${icon("⛶")}<span class="tool-label">Вся карта</span></button>
      </div>
    </header>
    <div class="map-tools">
      <div class="basemaps surface" role="group" aria-label="Подложка">
        <button class="button" data-base="map" aria-pressed="true" type="button">Карта</button>
        <button class="button" data-base="satellite" aria-pressed="false" type="button">Спутник</button>
      </div>
      <div class="layers">
        <div class="layer-trigger surface"><button class="button" id="layers-toggle" aria-controls="layers" aria-expanded="false" type="button">${icon("▱")}Слои <span class="count" id="layer-count">2</span></button></div>
        <div id="layers" class="layer-popover surface" hidden>
          <label><input type="checkbox" name="boundaries" checked>Кадастровые границы</label>
          <label><input type="checkbox" name="labels" checked>Кадастровые номера</label>
          <label><input type="checkbox" name="fill">Заливка по категориям земель</label>
          <label><input type="checkbox" name="osm">Объекты карты (OSM)</label>
        </div>
      </div>
    </div>
    <div class="map-status" role="status"><span class="status-dot" aria-hidden="true"></span><strong id="scope">ЕГРН · 500 м</strong><span id="status-text">Полнота выдачи неизвестна</span></div>
    <div class="zoom surface" role="group" aria-label="Навигация карты">
      <button class="button" id="zoom-in" aria-label="Увеличить масштаб" type="button">${icon("+")}</button>
      <button class="button" id="zoom-out" aria-label="Уменьшить масштаб" type="button">${icon("−")}</button>
      <button class="button" id="fit" aria-label="Показать область исследования" type="button">${icon("⌖")}</button>
    </div>
    <button class="exit button surface" id="exit" aria-label="Выйти из режима Вся карта" hidden type="button">${icon("⛶")}<span class="exit-label">Выйти</span></button>
    <aside class="sidebar surface" id="sidebar" aria-label="Сведения" hidden>
      <div class="panel-head">
        <div class="tabs" role="tablist" aria-label="Вкладки сведений">
          <button class="button" id="tab-cadastre" role="tab" aria-controls="cadastre" aria-selected="true" type="button">Кадастр</button>
          <button class="button" id="tab-objects" role="tab" aria-controls="objects" aria-selected="false" tabindex="-1" type="button">Объекты</button>
          <button class="button" id="tab-coordinates" role="tab" aria-controls="coordinates" aria-selected="false" tabindex="-1" type="button">Координаты</button>
        </div>
        <button class="button" id="close-panel" aria-label="Свернуть сведения" type="button">${icon("×")}</button>
      </div>
      <div class="panel-scroll" id="cadastre" role="tabpanel" aria-labelledby="tab-cadastre" tabindex="0">
        <section class="panel-section">
          <p class="eyebrow">Исходный участок</p><h2>63:09:0102159:100</h2>
          <div class="measure">
            <div><b>500 м</b><span>Радиус</span></div>
            <div><b>18</b><span>На карте</span></div>
            <div><b>17</b><span>Соседи</span></div>
          </div>
          <p class="panel-warning"><span class="status-dot"></span>Полнота выдачи неизвестна</p>
        </section>
        <section class="panel-section">
          <h3>Категории земель</h3>
          <ul class="category-list">
            <li><i style="--swatch:#e6a650"></i><span>Населённые пункты</span><b>7</b></li>
            <li><i style="--swatch:#a688c8"></i><span>Промышленность и спецназначение</span><b>5</b></li>
            <li><i style="--swatch:#83ab54"></i><span>Сельскохозяйственные</span><b>3</b></li>
            <li><i style="--swatch:#62a9bd"></i><span>Прочие земли</span><b>2</b></li>
            <li><i style="--swatch:#8c969d"></i><span>Нет данных</span><b>1</b></li>
          </ul>
        </section>
        <section class="panel-section">
          <p class="eyebrow">Выбранный участок</p><h2>63:09:0102159:101</h2>
          <div class="distance"><b>24,6 м</b><span>Между границами</span></div>
          <dl class="detail-list">
            <dt>Отношение</dt><dd>Не пересекаются</dd>
            <dt>Площадь</dt><dd>2 480 м²</dd>
            <dt>Категория</dt><dd>Земли населённых пунктов</dd>
            <dt>ВРИ</dt><dd>Складские площадки</dd>
            <dt>Адрес</dt><dd>Нет данных</dd>
          </dl>
        </section>
      </div>
      <div class="panel-scroll" id="objects" role="tabpanel" aria-labelledby="tab-objects" tabindex="0" hidden>
        <h2 id="osm-heading">Объекты карты</h2>
        <p class="paragraph quiet" id="osm-description">Включите слой «Объекты карты».</p>
        <ul class="category-list" id="osm-categories" hidden>
          <li><i style="--swatch:#64748b"></i><span>Здания</span><b>1</b></li>
          <li><i style="--swatch:#9866c4"></i><span>Промышленность</span><b>2</b></li>
          <li><i style="--swatch:#d49a30"></i><span>Жилая среда</span><b>0</b></li>
          <li><i style="--swatch:#3585bd"></i><span>Коммерция</span><b>1</b></li>
          <li><i style="--swatch:#d37048"></i><span>Образование</span><b>0</b></li>
          <li><i style="--swatch:#cf6685"></i><span>Медицина</span><b>0</b></li>
          <li><i style="--swatch:#45966a"></i><span>Отдых и озеленение</span><b>1</b></li>
          <li><i style="--swatch:#b6814c"></i><span>АЗС</span><b>1</b></li>
          <li><i style="--swatch:#4d929d"></i><span>Инфраструктура</span><b>1</b></li>
        </ul>
      </div>
      <div class="panel-scroll" id="coordinates" role="tabpanel" aria-labelledby="tab-coordinates" tabindex="0" hidden>
        <h2>Координаты WGS84</h2>
        <dl class="detail-list"><dt>Курсор</dt><dd>—</dd><dt>Выбранная точка</dt><dd>53.555864, 49.265000</dd></dl>
        <form id="coords">
          <div class="coordinate-fields">
            <label>Широта<input inputmode="decimal" value="53.555864"></label>
            <label>Долгота<input inputmode="decimal" value="49.265000"></label>
          </div>
          <button class="button primary" type="submit">Перейти</button>
          <button class="button outlined" id="copy" type="button">Копировать</button>
          <p class="demo-notice" id="coord-notice" role="status"></p>
        </form>
      </div>
    </aside>
    <details class="legend surface">
      <summary>Легенда</summary>
      <ul>
        <li><i style="--swatch:#20B7ED"></i>Кадастровые границы</li>
        <li><i style="--swatch:#9333EA"></i>Исходный участок</li>
        <li><i style="--swatch:#0284C7"></i>Выбранный участок</li>
        <li><i class="dashed" style="--swatch:#DC2626"></i>Расстояние</li>
      </ul>
    </details>
    <div class="scale" aria-label="Масштабная линейка">100 м</div>
    <div class="credits"><span class="sample">Макет</span> <span id="credits">· © OpenStreetMap contributors</span></div>`;

  const byId = id => document.getElementById(id);
  const canvas = workspace.querySelector(".map");
  const tabs = [...workspace.querySelectorAll('[role="tab"]')];
  const checks = Object.fromEntries([...workspace.querySelectorAll("#layers input")].map(input => [input.name, input]));
  let panel = scene === "sidebar" || scene === "narrow";
  let full = scene === "full";
  let satellite = full;
  let zoom = 1;
  let frame;

  function paint() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const enabled = Object.fromEntries(Object.entries(checks).map(([key, input]) => [key, input.checked]));
      drawMapSketch(canvas, { ...enabled, satellite, zoom, osm: enabled.osm && byId("radius").selectedIndex === 0 });
    });
  }

  function updateLayers() {
    const bounds = checks.boundaries.checked;
    checks.labels.disabled = !bounds;
    checks.fill.disabled = !bounds;
    const osmCurrent = checks.osm.checked && byId("radius").selectedIndex === 0;
    byId("layer-count").textContent = Number(bounds) + Number(bounds && checks.labels.checked) +
      Number(bounds && checks.fill.checked) + Number(osmCurrent);
    byId("osm-heading").textContent = osmCurrent ? "7 объектов карты" : "Объекты карты";
    byId("osm-description").textContent = osmCurrent ? "500 м от исходной точки" :
      checks.osm.checked ? "Для выбранного радиуса в макете нет выборки." : "Включите слой «Объекты карты».";
    byId("osm-categories").hidden = !osmCurrent;
    for (const button of workspace.querySelectorAll("[data-base]")) {
      button.setAttribute("aria-pressed", String((button.dataset.base === "satellite") === satellite));
    }
    const esri = "Powered by Esri · Esri World Imagery";
    const osm = "© OpenStreetMap contributors";
    byId("credits").textContent = `· ${satellite ? [esri, ...(osmCurrent ? [osm] : [])].join(" · ") : osm}`;
    paint();
  }

  function showLayers(open, restoreFocus = false) {
    byId("layers").hidden = !open;
    byId("layers-toggle").setAttribute("aria-expanded", String(open));
    if (open) workspace.querySelector(".legend").open = false;
    if (!open && restoreFocus) byId("layers-toggle").focus();
  }

  function updateView() {
    workspace.classList.toggle("is-full", full);
    workspace.classList.toggle("is-panel", panel && !full);
    byId("top").hidden = full;
    byId("sidebar").hidden = full || !panel;
    byId("exit").hidden = !full;
    byId("panel-toggle").setAttribute("aria-expanded", String(panel && !full));
    showLayers(false);
    paint();
  }

  function selectTab(active, focus = false) {
    for (const tab of tabs) {
      const selected = tab === active;
      tab.tabIndex = selected ? 0 : -1;
      tab.setAttribute("aria-selected", String(selected));
      byId(tab.getAttribute("aria-controls")).hidden = !selected;
    }
    if (focus) active.focus();
  }

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", event => {
      const shift = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!shift) return;
      event.preventDefault();
      selectTab(tabs[(index + shift + tabs.length) % tabs.length], true);
    });
  }

  byId("panel-toggle").addEventListener("click", () => { panel = !panel; updateView(); });
  byId("close-panel").addEventListener("click", () => {
    panel = false;
    updateView();
    byId("panel-toggle").focus();
  });
  byId("focus-map").addEventListener("click", () => {
    full = true;
    updateView();
    byId("exit").focus();
  });
  function leaveFullMap() {
    full = false;
    updateView();
    byId("focus-map").focus();
  }
  byId("exit").addEventListener("click", leaveFullMap);
  byId("layers-toggle").addEventListener("click", () => showLayers(byId("layers").hidden));
  for (const input of Object.values(checks)) input.addEventListener("change", updateLayers);
  for (const button of workspace.querySelectorAll("[data-base]")) {
    button.addEventListener("click", () => { satellite = button.dataset.base === "satellite"; updateLayers(); });
  }

  function setZoom(next) {
    zoom = Math.min(1.6, Math.max(.8, next));
    workspace.querySelector(".scale").textContent = `${Math.round(10 / zoom) * 10} м`;
    paint();
  }
  byId("zoom-in").addEventListener("click", () => setZoom(zoom * 1.16));
  byId("zoom-out").addEventListener("click", () => setZoom(zoom / 1.16));
  byId("fit").addEventListener("click", () => setZoom(1));
  function radiusNotice() {
    const radius = byId("radius").value;
    byId("scope").textContent = radius === "500 м" ? "ЕГРН · 500 м" : `На карте 500 м · выбрано ${radius}`;
    byId("status-text").textContent = "Полнота выдачи неизвестна";
    updateLayers();
  }
  byId("radius").addEventListener("change", radiusNotice);
  byId("search").addEventListener("submit", event => {
    event.preventDefault();
    byId("status-text").textContent = "Поиск показан на условном участке";
  });
  byId("research").addEventListener("click", () => {
    byId("status-text").textContent = "В макете показана выборка 500 м";
  });
  byId("coords").addEventListener("submit", event => {
    event.preventDefault();
    byId("coord-notice").textContent = "Координаты отображены для примера";
  });
  byId("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText("53.555864, 49.265000");
      byId("coord-notice").textContent = "Координаты скопированы";
    } catch {
      byId("coord-notice").textContent = "Для ручного копирования выделите координаты точки";
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if (!byId("layers").hidden) showLayers(false, true);
    else if (full) leaveFullMap();
    else if (panel) byId("close-panel").click();
  });

  new ResizeObserver(paint).observe(workspace);
  updateView();
  updateLayers();
  if (scene === "laptop") {
    checks.fill.checked = true;
    showLayers(true);
    updateLayers();
  }
})();
