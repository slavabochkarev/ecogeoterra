import { installBasemaps } from "./basemaps.js";
import { installCoordinates } from "./coordinates.js";
import { installLayers, installPanels } from "./interface.js";
import { installCadastre } from "./cadastre.js";
import { installSearch } from "./search.js";
import { installAttribution } from "./attribution.js";
import { installOsm } from "./osm.js";
import { installResearches } from "./researches.js";
import { coordinateAnchor, installOopt, osmAnchor, parcelAnchor } from "./oopt.js";

const element = id => document.getElementById(id);
const workspace = element("workspace");
const toolbar = element("toolbar");
const mapElement = element("map");
const leaflet = window.L;
const emptyResearchState = () => ({
  camera: { lat: 55, lon: 50, zoom: 4 }, basemap: "osm", tab: "cadastre", panel_open: false, map_only: false,
  layers: { boundaries: true, labels: true, fill: false, osm: false },
  cadastre_radius: 500, osm_radius: 2000, osm_categories: null, hide_unnamed: false,
  cadastre_selected: null, osm_selected: null, oopt_anchor: null, osm_origin: null, location: null, coordinates: null, query: "",
});

if (!leaflet) {
  const status = element("startup-status");
  status.textContent = "Не удалось загрузить карту. Обновите страницу.";
  status.hidden = false;
} else {
  const map = leaflet.map(mapElement, {
    zoomControl: false,
    attributionControl: false,
    trackResize: false,
    maxZoom: 19,
    zoomSnap: 0.25,
    zoomAnimation: false,
    fadeAnimation: false,
  }).setView([55, 50], 4);
  leaflet.control.scale({ position: "bottomleft", metric: true, imperial: false, maxWidth: 100 }).addTo(map);

  const state = {
    panelOpen: false,
    mapOnly: false,
    tab: "cadastre",
    layersOpen: false,
    layers: {
      boundaries: { available: true, visible: true },
      labels: { available: true, visible: true },
      fill: { available: true, visible: false },
      osm: { available: true, visible: false },
    },
  };

  let layoutFrame;
  let researches;
  const changed = () => researches?.schedule();
  function updateLayout() {
    cancelAnimationFrame(layoutFrame);
    layoutFrame = requestAnimationFrame(() => {
      const viewport = window.visualViewport;
      const visibleHeight = viewport?.scale === 1 ? viewport.height : null;
      if (visibleHeight) workspace.style.setProperty("--visible-height", `${visibleHeight}px`);
      else workspace.style.removeProperty("--visible-height");

      const height = toolbar.getBoundingClientRect().bottom;
      workspace.style.setProperty("--toolbar-bottom", `${Math.ceil(height) + 9}px`);
      map.invalidateSize({ animate: false, debounceMoveend: true });
    });
  }

  const observer = new ResizeObserver(updateLayout);
  observer.observe(workspace);
  observer.observe(toolbar);
  window.visualViewport?.addEventListener("resize", updateLayout);

  element("zoom-in").addEventListener("click", () => map.zoomIn());
  element("zoom-out").addEventListener("click", () => map.zoomOut());

  const attribution = installAttribution(element);
  const layerControls = installLayers(state, element, () => {
    cadastre.refreshVisibility();
    search.refreshVisibility();
    osm.refreshVisibility();
    changed();
  });
  const panels = installPanels(workspace, state, element, layerControls.show, updateLayout, changed);
  const oopt = installOopt(element);
  const cadastre = installCadastre(map, leaflet, state.layers, element, () => panels.showTab("cadastre"), changed,
    (feature, refresh = false) => {
      if (!refresh || oopt.anchor() === "cadastre") oopt.setSource(parcelAnchor(feature));
    });
  const osm = installOsm(map, leaflet, element, state, layerControls, panels.showTab, cadastre, attribution, changed,
    feature => { if (feature || oopt.anchor() === "osm") oopt.setSource(osmAnchor(feature)); });
  const coordinates = installCoordinates(map, leaflet, element, osm.setOrigin, changed,
    point => oopt.setSource(coordinateAnchor(point)));
  const search = installSearch(map, leaflet, state.layers, element, cadastre, coordinates.goTo,
    () => panels.showTab("coordinates"), osm.setOrigin, attribution, changed);
  const basemaps = installBasemaps(map, leaflet, element, attribution, changed);

  function snapshot() {
    const center = map.wrapLatLng(map.getCenter());
    const parcels = cadastre.snapshot();
    const objects = osm.snapshot();
    return { cadastre: parcels, osm: objects, state: {
      camera: { lat: center.lat, lon: center.lng, zoom: map.getZoom() }, basemap: basemaps.current(),
      tab: state.tab, panel_open: state.panelOpen, map_only: state.mapOnly,
      layers: Object.fromEntries(Object.entries(state.layers).map(([name, layer]) => [name, layer.visible])),
      cadastre_radius: parcels.radius, osm_radius: objects.radius, osm_categories: objects.categories,
      hide_unnamed: objects.hideUnnamed, cadastre_selected: parcels.selectedId ?? null, osm_selected: objects.selectedId ?? null,
      oopt_anchor: oopt.anchor(),
      osm_origin: objects.origin ?? null, location: search.snapshot(), coordinates: coordinates.snapshot(), query: parcels.query,
    } };
  }

  async function restore(document) {
    const saved = document.research?.state ?? document.state;
    map.closePopup();
    await cadastre.restore(document.cadastre, saved);
    await osm.restore(document.osm, saved);
    search.restore(saved.location);
    coordinates.restore(saved.coordinates);
    const anchors = {
      cadastre: parcelAnchor(cadastre.selection()),
      osm: osmAnchor(osm.selection()),
      coordinates: coordinateAnchor(saved.coordinates ?? saved.location, saved.location),
    };
    oopt.reset();
    oopt.setSource((saved.oopt_anchor ? anchors[saved.oopt_anchor] : null)
      ?? (saved.tab === "objects" ? anchors.osm : anchors[saved.tab])
      ?? anchors.cadastre ?? anchors.osm ?? anchors.coordinates);
    layerControls.restore(saved.layers);
    panels.restore(saved);
    map.setView([saved.camera.lat, saved.camera.lon], saved.camera.zoom, { animate: false });
    await basemaps.restore(saved.basemap);
  }

  researches = installResearches(element, workspace, { snapshot, restore, emptyState: emptyResearchState,
    prepareOpen: osm.ensureReady, isBusy: () => cadastre.busy() || osm.busy() || search.busy() || oopt.busy() });
  map.on("moveend zoomend", changed);
  updateLayout();
}
