"use strict";

const worldImageryUrl = "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer";

function esriFailure(code) {
  switch (Number(code)) {
    case 498: return "Ключ Esri недействителен или срок его действия истёк (498).";
    case 401: return "Esri не принял авторизацию (401). Проверьте ключ и срок его действия.";
    case 499: return "Esri не получил действующий ключ (499). Проверьте конфигурацию.";
    case 403: return "Esri отклонил доступ (403). Проверьте права ключа и разрешённый адрес сайта.";
    case 429: return "Esri временно ограничил число запросов (429). Попробуйте позже.";
    default: return "";
  }
}

async function failedTileCode(url, signal) {
  try {
    const response = await fetch(url, { signal });
    if (response.headers.get("content-type")?.includes("json")) {
      const data = await response.json();
      return Number(data?.error?.code) || response.status;
    }
    return response.status;
  } catch {
    return 0;
  }
}

function esriWorldImagery(map, token, showStatus, creditHost) {
  const group = L.layerGroup();
  const container = L.DomUtil.create("div", "esri-credit");
  const poweredBy = L.DomUtil.create("a", "", container);
  poweredBy.href = "https://www.esri.com/";
  poweredBy.target = "_blank";
  poweredBy.rel = "noopener";
  poweredBy.textContent = "Powered by Esri";
  const dataCredit = L.DomUtil.create("span", "", container);

  group.on("add", () => {
    const layer = L.esri.tiledMapLayer({ url: worldImageryUrl, token, maxZoom: 19, errorTileUrl: "" });
    const active = () => map.hasLayer(group) && group.hasLayer(layer);
    const timeout = setTimeout(() => stop("Esri не ответил. Проверьте подключение к сети."), 12000);
    const loaded = new Set();
    let failedUrl;
    let diagnostic;
    let batch = 0;

    function stop(message) {
      if (!active()) return;
      group.removeLayer(layer);
      if (creditHost) container.remove();
      showStatus(`${message} Можно переключиться на OpenStreetMap.`, true);
    }

    layer.on({
      requestsuccess: ({ response }) => {
        clearTimeout(timeout);
        if (!active()) return;
        if (!response?.tileInfo || !response.copyrightText) {
          stop("Esri не предоставил сведения о снимках и их источниках.");
          return;
        }
        dataCredit.textContent = ` · ${response.copyrightText}`;
      },
      requesterror: ({ code }) => {
        clearTimeout(timeout);
        stop(esriFailure(code) || "Сервис снимков Esri недоступен. Проверьте подключение к сети.");
      },
      loading: () => {
        failedUrl = undefined;
        batch += 1;
      },
      tileload: ({ tile }) => loaded.add(tile),
      tileunload: ({ tile }) => loaded.delete(tile),
      tileerror: ({ tile }) => { failedUrl ??= tile.src; },
      load: async () => {
        if (!active()) return;
        if (!failedUrl) {
          showStatus();
          return;
        }
        const currentBatch = batch;
        const partial = loaded.size > 0;
        const message = partial ? "Часть снимков Esri не загрузилась." : "Снимки Esri не загрузились.";
        showStatus(`${message} Можно переключиться на OpenStreetMap.`, true);
        diagnostic ??= failedTileCode(failedUrl, AbortSignal.timeout(6000));
        const code = await diagnostic;
        if (!active()) return;
        const reason = esriFailure(code);
        if (reason && batch === currentBatch) {
          if (partial) showStatus(`${message} ${reason} Можно переключиться на OpenStreetMap.`, true);
          else stop(reason);
        }
      },
      remove: () => clearTimeout(timeout),
    });

    dataCredit.textContent = "";
    if (creditHost) creditHost.prepend(container);
    else map.getContainer().after(container);
    showStatus("Загружаем снимки Esri…");
    group.addLayer(layer);
  });
  group.on("remove", () => {
    group.clearLayers();
    container.remove();
  });
  return group;
}
