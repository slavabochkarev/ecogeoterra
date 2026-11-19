const osmUrl = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

export function installBasemaps(map, leaflet, element, setAttribution, changed) {
  const osmButton = element("base-osm");
  const esriButton = element("base-esri");
  const fallback = element("back-to-osm");
  const status = element("tile-status");
  const message = element("tile-message");
  const osm = leaflet.tileLayer(osmUrl, { maxZoom: 19 });
  let active = osm;
  let esri;
  let esriUnavailable = "";
  let loadedTiles = 0;
  let failedTiles = 0;

  function showStatus(text = "", offerOsm = false) {
    message.textContent = text;
    fallback.hidden = !offerOsm;
    status.hidden = !text;
  }

  function activate(layer) {
    if (layer === active) return;
    map.removeLayer(active);
    active = layer;
    const isOsm = layer === osm;
    osmButton.setAttribute("aria-pressed", String(isOsm));
    esriButton.setAttribute("aria-pressed", String(!isOsm));
    setAttribution("osm", isOsm);
    showStatus();
    layer.addTo(map);
    changed();
  }

  osm.on({
    loading: () => { loadedTiles = 0; failedTiles = 0; },
    tileload: () => { loadedTiles += 1; },
    tileerror: () => { failedTiles += 1; },
    load: () => {
      if (active !== osm) return;
      if (failedTiles && !loadedTiles) showStatus("Подложка OpenStreetMap временно недоступна.");
      else if (!esriUnavailable || message.textContent !== esriUnavailable) showStatus();
    },
  });
  osm.addTo(map);

  osmButton.addEventListener("click", () => {
    if (active === osm) showStatus();
    else activate(osm);
  });
  fallback.addEventListener("click", () => {
    activate(osm);
    osmButton.focus();
  });
  esriButton.addEventListener("click", () => {
    if (esri) activate(esri);
    else showStatus(esriUnavailable);
  });

  // The cadastral experiment's adapter owns SDK, imagery errors and provider credits.
  const ready = fetch("/api/cadastre-experiment/basemaps", { signal: AbortSignal.timeout(6000) })
    .then(response => {
      if (!response.ok) throw new Error("Basemap configuration unavailable");
      return response.json();
    })
    .then(({ esriApiKey }) => {
      if (esriApiKey && leaflet.esri?.tiledMapLayer && typeof window.esriWorldImagery === "function") {
        esri = window.esriWorldImagery(map, esriApiKey, showStatus, element("map-attributions"));
      } else {
        esriUnavailable = "Спутник Esri недоступен. Проверьте настройку подложки Esri.";
      }
    })
    .catch(() => {
      esriUnavailable = "Не удалось получить настройки спутника Esri. Можно использовать карту OpenStreetMap.";
    })
    .finally(() => { esriButton.disabled = false; });

  async function restore(name) {
    if (name === "esri") {
      await ready;
      if (esri) {
        activate(esri);
        return;
      }
      activate(osm);
      showStatus(esriUnavailable);
    } else {
      activate(osm);
    }
  }
  return { restore, current: () => active === osm ? "osm" : "esri" };
}
