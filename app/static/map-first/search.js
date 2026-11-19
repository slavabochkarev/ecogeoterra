import { createPointSearch } from "./point-candidates.js";
import { request } from "./request.js";
import { parseSearch, validMapPoint } from "./search-format.js";

export function installSearch(map, leaflet, layers, element, cadastre, goTo, showCoordinates, setOrigin, setAttribution, changed) {
  const form = element("cadastre-search");
  const field = element("cadastre-number");
  const find = element("find-parcel");
  const addressSection = element("address-search-results");
  const addressStatus = element("address-status");
  const addressOptions = element("address-options");
  let addresses = [];
  let activeLocation;
  let searching = false;

  const pointSearch = createPointSearch(map, leaflet, layers, element, candidate => {
    if (searching) throw new Error("Дождитесь завершения поиска.");
    cadastre.confirmSource(candidate.parcel, candidate.number);
    activeLocation = undefined;
    clearAddresses();
    changed();
    element("cadastre-tab").focus();
  }, showCoordinates);

  function clearAddresses() {
    addressSection.hidden = true;
    addressOptions.hidden = true;
    addressOptions.replaceChildren();
    addresses = [];
    setAttribution("geocoder", Boolean(activeLocation?.address));
  }

  function locate(location) {
    activeLocation = location;
    clearAddresses();
    showCoordinates();
    pointSearch.place(location);
    goTo(location.lat, location.lon, false);
    setOrigin({ lat: location.lat, lon: location.lon, label: location.address ?? "Координаты поиска",
      ...(location.address ? { address: location.address } : {}) });
    const panel = element("details");
    const viewport = map.getContainer().getBoundingClientRect();
    const bounds = panel.getBoundingClientRect();
    if (!panel.hidden && bounds.width > viewport.width / 2 && bounds.top > viewport.top + 60) {
      map.panInside([location.lat, location.lon], {
        paddingTopLeft: [20, 20], paddingBottomRight: [20, viewport.bottom - bounds.top + 22], animate: false,
      });
    }
  }

  function addressMessage(message, error = false) {
    addressStatus.textContent = message;
    addressStatus.classList.toggle("error", error);
  }

  async function searchAddress(query) {
    clearAddresses();
    showCoordinates();
    addressSection.hidden = false;
    addressMessage("Ищем адрес…");
    try {
      const data = await request(`/api/geocode?q=${encodeURIComponent(query)}`, {}, 15000);
      if (!Array.isArray(data.results)) throw new Error("Сервер вернул некорректный список адресов.");
      addresses = data.results
        .filter(item => item && typeof item.display_name === "string" && validMapPoint(item.lat, item.lon))
        .map(item => ({ address: item.display_name, lat: item.lat, lon: item.lon }));
      if (!addresses.length) {
        addressMessage("Адрес не найден. Уточните запрос или введите координаты.");
        return;
      }
      if (addresses.length === 1) {
        locate(addresses[0]);
        return;
      }
      const entries = document.createDocumentFragment();
      addresses.forEach((item, index) => {
        const row = document.createElement("li");
        const button = document.createElement("button");
        button.className = "button location-option";
        button.type = "button";
        button.dataset.index = String(index);
        button.textContent = item.address;
        row.append(button);
        entries.append(row);
      });
      addressOptions.replaceChildren(entries);
      addressOptions.hidden = false;
      setAttribution("geocoder", true);
      addressMessage("Найдено несколько адресов. Выберите нужный.");
    } catch (error) {
      addressMessage(error.message, true);
    }
  }

  addressOptions.addEventListener("click", event => {
    const button = event.target.closest("button[data-index]");
    const location = button && addresses[Number(button.dataset.index)];
    if (location) locate(location);
  });
  field.addEventListener("input", () => field.removeAttribute("aria-invalid"));
  field.addEventListener("change", changed);
  field.disabled = false;
  find.disabled = false;
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (searching) return;
    let input;
    try {
      input = parseSearch(field.value);
    } catch (error) {
      field.setAttribute("aria-invalid", "true");
      cadastre.setStatus(error.message, true);
      field.focus();
      return;
    }
    field.removeAttribute("aria-invalid");
    if (input.kind === "point") {
      cadastre.setStatus("");
      locate(input);
      return;
    }

    searching = true;
    find.disabled = true;
    field.readOnly = true;
    form.setAttribute("aria-busy", "true");
    try {
      if (input.kind === "parcel") {
        if (await cadastre.findNumber(input.number)) {
          activeLocation = undefined;
          pointSearch.clear();
          clearAddresses();
          setOrigin(cadastre.getOsmOrigin());
        }
      } else {
        cadastre.setStatus("");
        await searchAddress(input.query);
      }
    } finally {
      searching = false;
      find.disabled = false;
      field.readOnly = false;
      form.setAttribute("aria-busy", "false");
    }
  });
  function restore(location) {
    activeLocation = location ?? undefined;
    clearAddresses();
    pointSearch.clear();
    if (activeLocation) pointSearch.place(activeLocation);
  }
  function snapshot() {
    if (!activeLocation) return null;
    const { lat, lon, address } = activeLocation;
    return { lat, lon, address: address ?? null };
  }
  return { restore, snapshot, busy: () => searching || pointSearch.busy(), refreshVisibility: pointSearch.refreshVisibility };
}
