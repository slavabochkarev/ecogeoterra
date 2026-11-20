import { maxMapLatitude } from "./search-format.js";

// Keep the cadastral experiment's WGS 84 input and display conventions.
export function formatCoordinates(point) {
  return `${point.lat.toFixed(6)}, ${point.lng.toFixed(6)}`;
}

function parseCoordinate(value) {
  const decimal = value.trim().replace(",", ".");
  return /^[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$/.test(decimal) ? Number(decimal) : NaN;
}

export function installCoordinates(map, leaflet, element, onNavigate, changed, onSelect) {
  const cursor = element("cursor-coordinates");
  const latitude = element("latitude");
  const longitude = element("longitude");
  const selected = element("selected-coordinates");
  const copy = element("copy-coordinates");
  const status = element("coordinate-status");
  const marker = leaflet.marker([0, 0], { interactive: false, keyboard: false, alt: "Выбранная точка" });
  let point;

  function showStatus(message = "", error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
    status.hidden = !message;
  }

  function selectPoint(value, showMarker = true) {
    point = value.wrap();
    if (showMarker) marker.setLatLng(point).addTo(map);
    else marker.remove();
    selected.textContent = formatCoordinates(point);
    latitude.value = point.lat.toFixed(6);
    longitude.value = point.lng.toFixed(6);
    latitude.removeAttribute("aria-invalid");
    longitude.removeAttribute("aria-invalid");
    copy.disabled = false;
    showStatus();
    changed();
  }

  function pick(value, showMarker, label) {
    selectPoint(value, showMarker);
    onSelect({ lat: point.lat, lon: point.lng, label });
  }

  function goTo(lat, lon, showMarker = true, label) {
    pick(leaflet.latLng(lat, lon), showMarker, label);
    map.setView(point, Math.max(map.getZoom(), 15), { animate: false });
  }

  map.on("mousemove", ({ latlng }) => { cursor.textContent = formatCoordinates(latlng.wrap()); });
  map.getContainer().addEventListener("mouseleave", () => { cursor.textContent = "—"; });
  map.on("click", ({ latlng }) => pick(latlng));

  element("coordinate-form").addEventListener("submit", event => {
    event.preventDefault();
    const lat = parseCoordinate(latitude.value);
    const lon = parseCoordinate(longitude.value);
    const validLatitude = Number.isFinite(lat) && Math.abs(lat) <= maxMapLatitude;
    const validLongitude = Number.isFinite(lon) && Math.abs(lon) <= 180;
    latitude.setAttribute("aria-invalid", String(!validLatitude));
    longitude.setAttribute("aria-invalid", String(!validLongitude));
    if (!validLatitude || !validLongitude) {
      const message = Number.isFinite(lat) && Number.isFinite(lon)
        ? "Допустимая широта подложки — до ±85,05112878°, долгота — до ±180°."
        : "Введите широту и долготу в десятичных градусах.";
      showStatus(message, true);
      (validLatitude ? longitude : latitude).focus();
      return;
    }
    goTo(lat, lon);
    onNavigate({ lat, lon, label: "Координаты поиска" });
  });

  copy.addEventListener("click", async () => {
    if (!point) return;
    try {
      await navigator.clipboard.writeText(formatCoordinates(point));
      showStatus("Координаты скопированы.");
    } catch {
      showStatus("Копирование недоступно. Выделите координаты выбранной точки вручную.", true);
    }
  });

  function restore(saved) {
    if (saved) {
      selectPoint(leaflet.latLng(saved.lat, saved.lon), saved.marker);
      return;
    }
    point = undefined;
    marker.remove();
    selected.textContent = "—";
    latitude.value = longitude.value = "";
    latitude.removeAttribute("aria-invalid");
    longitude.removeAttribute("aria-invalid");
    copy.disabled = true;
    showStatus();
  }
  return { goTo, restore, snapshot: () => point ? { lat: point.lat, lon: point.lng, marker: map.hasLayer(marker) } : null };
}
