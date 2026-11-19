import { categories, formatDistance, isPosition } from "./cadastre-format.js";

function overlayPane(map, name, zIndex) {
  const pane = map.createPane(name);
  pane.style.zIndex = zIndex;
  pane.style.pointerEvents = "none";
  pane.setAttribute("aria-hidden", "true");
  return name;
}

// As in the cadastral experiment, keep label boxes inside the contour, including at holes.
function labelFits(map, leaflet, layer, anchor, halfWidth, halfHeight) {
  const center = map.project(anchor);
  const origin = leaflet.point(0, 0);
  const rings = layer.feature.geometry.type === "Polygon" ? layer.getLatLngs() : layer.getLatLngs().flat();
  const toLabelUnits = point => {
    const offset = map.project(point).subtract(center);
    return leaflet.point(offset.x / halfWidth, offset.y / halfHeight);
  };
  for (const ring of rings) {
    let previous = toLabelUnits(ring.at(-1));
    for (const vertex of ring) {
      const next = toLabelUnits(vertex);
      if (leaflet.LineUtil.pointToSegmentDistance(origin, previous, next) <= Math.SQRT2) return false;
      previous = next;
    }
  }
  return true;
}

function installLabels(map, leaflet, layerGroup, getParcels, layers, getSelection) {
  const pane = overlayPane(map, "cadastreLabels", 430);
  const text = document.createElement("canvas").getContext("2d");
  text.font = '650 11px "Segoe UI", system-ui, sans-serif';
  let frame;

  function render() {
    layerGroup.clearLayers();
    if (!layers.boundaries.visible || !layers.labels.visible) return;
    const { x, y } = map.getSize();
    const { sourceId, selectedId } = getSelection();
    const candidates = [];
    for (const layer of getParcels().getLayers()) {
      const { id, properties } = layer.feature;
      const point = properties.label_point;
      if (!isPosition(point)) continue;
      const anchor = leaflet.latLng(point[1], point[0]);
      const pixel = map.latLngToContainerPoint(anchor);
      const shortNumber = id.slice(id.lastIndexOf(":") + 1);
      const labelWidth = Math.ceil(text.measureText(shortNumber).width) + 6;
      const halfWidth = labelWidth / 2 + 1;
      const halfHeight = 9;
      const box = { left: pixel.x - halfWidth, right: pixel.x + halfWidth,
        top: pixel.y - halfHeight, bottom: pixel.y + halfHeight };
      if (box.left < 0 || box.right > x || box.top < 0 || box.bottom > y) continue;
      const bounds = layer.getBounds();
      const span = map.project(bounds.getSouthEast()).subtract(map.project(bounds.getNorthWest()));
      if (span.x < labelWidth || span.y < 16) continue;
      const priority = id === sourceId ? 2 : Number(id === selectedId);
      candidates.push({ layer, anchor, shortNumber, labelWidth, halfWidth, halfHeight, box, priority, area: span.x * span.y });
    }
    candidates.sort((left, right) => right.priority - left.priority || right.area - left.area);
    const taken = [];
    for (const candidate of candidates) {
      const { box, layer, anchor, shortNumber, labelWidth, halfWidth, halfHeight } = candidate;
      if (taken.some(other => box.left < other.right && box.right > other.left &&
          box.top < other.bottom && box.bottom > other.top)) continue;
      if (!labelFits(map, leaflet, layer, anchor, halfWidth, halfHeight)) continue;
      const caption = document.createElement("span");
      caption.textContent = shortNumber;
      const icon = leaflet.divIcon({ className: "cadastre-map-label", html: caption, iconSize: [labelWidth, 16] });
      leaflet.marker(anchor, { pane, icon, interactive: false, keyboard: false }).addTo(layerGroup);
      taken.push(box);
      if (taken.length === 100) break;
    }
  }

  function refresh() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(render);
  }
  map.on("zoomstart", () => layerGroup.clearLayers());
  map.on("moveend zoomend resize", refresh);
  return refresh;
}

function drawMeasurement(map, leaflet, layer, pane, measurement) {
  layer.clearLayers();
  if (measurement?.relation !== "disjoint" || !Number.isFinite(measurement.distance_m) ||
      measurement.distance_m <= 0 || measurement.endpoints?.length !== 2 ||
      !Array.isArray(measurement.endpoints) || !measurement.endpoints.every(isPosition)) return;

  const points = measurement.endpoints.map(([lon, lat]) => leaflet.latLng(lat, lon));
  const color = "#DC2626";
  const options = { pane, interactive: false };
  leaflet.polyline(points, { ...options, color, weight: 2.5, dashArray: "7 5" }).addTo(layer);
  for (const point of points) {
    leaflet.circleMarker(point, { ...options, radius: 4, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: 1 })
      .addTo(layer);
  }
  const start = map.project(points[0]);
  const end = map.project(points[1]);
  const midpoint = map.unproject(start.add(end).divideBy(2));
  const dx = Math.abs(end.x - start.x);
  const dy = Math.abs(end.y - start.y);
  const caption = document.createElement("span");
  caption.textContent = formatDistance(measurement.distance_m);
  const horizontal = dx >= dy;
  const gap = 9 + Math.min(dx, dy) / (Math.max(dx, dy) || 1) * (horizontal ? caption.textContent.length * 4 + 6 : 14);
  leaflet.tooltip({ ...options, className: "cadastre-map-distance", opacity: 1,
    direction: horizontal ? "top" : "right", offset: horizontal ? [0, -gap] : [gap, 0] })
    .setLatLng(midpoint).setContent(caption).addTo(layer);
}

export function createCadastreMap(map, leaflet, layers, element, onSelect) {
  const bufferPane = overlayPane(map, "cadastreBuffer", 422);
  const bufferStyle = { dashArray: "11 7", fill: false };
  const halo = leaflet.geoJSON(null, {
    pane: bufferPane, interactive: false, smoothFactor: 0,
    style: { ...bufferStyle, color: "#fff9eb", weight: 7, opacity: 0.95 },
  }).addTo(map);
  const buffer = leaflet.geoJSON(null, {
    pane: bufferPane, interactive: false, smoothFactor: 0,
    style: { ...bufferStyle, color: "#f05219", weight: 4, opacity: 1 },
  }).addTo(map);
  const stroke = { color: "#9333EA", weight: 5, opacity: 1 };
  const outline = leaflet.geoJSON(null, {
    pane: overlayPane(map, "cadastreSource", 415), interactive: false, smoothFactor: 0,
    style: { ...stroke, fill: false },
  });
  const measurementPane = overlayPane(map, "cadastreMeasurement", 440);
  const measurements = leaflet.layerGroup();
  const labels = leaflet.layerGroup();
  let sourceId;
  let selectedId;
  const renderer = leaflet.canvas({ padding: 0.15 });

  function style(feature, origin = sourceId, selection = selectedId, fill = layers.fill.visible) {
    if (feature.id === origin) return { ...stroke, stroke: false, fillColor: "#A855F7", fillOpacity: 0.30 };
    if (feature.id === selection) {
      return { color: "#0284C7", weight: 5, opacity: 1, fillColor: "#38BDF8", fillOpacity: 0.16 };
    }
    return {
      color: "#20B7ED", weight: 2.5, opacity: 1,
      fillColor: (categories[feature.properties.category_bucket] ?? categories.unknown).color,
      fillOpacity: fill ? 0.46 : 0,
    };
  }

  function dataset(origin) {
    const paths = new Map();
    const fill = layers.fill.visible;
    const parcels = leaflet.geoJSON(null, {
      renderer, smoothFactor: 0, bubblingMouseEvents: false,
      style: feature => style(feature, origin, null, fill),
      onEachFeature(feature, path) {
        paths.set(feature.id, path);
        path.on("click", () => onSelect(feature.id));
        path.bindTooltip(() => {
          const caption = document.createElement("span");
          caption.textContent = feature.id;
          return caption;
        }, { sticky: true, className: "cadastre-hover-label" });
      },
    });
    return { paths, parcels, fill };
  }

  let shown = dataset(null);
  const overlay = leaflet.layerGroup([shown.parcels, outline, measurements, labels]);
  const refreshLabels = installLabels(map, leaflet, labels, () => shown.parcels, layers, () => ({ sourceId, selectedId }));
  const legend = element("cadastre-legend");
  for (const { color, label } of Object.values(categories)) {
    const row = document.createElement("li");
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = color;
    swatch.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = label;
    row.append(swatch, text);
    element("cadastre-legend-entries").append(row);
  }

  function refreshVisibility() {
    if (layers.boundaries.visible && !map.hasLayer(overlay)) {
      overlay.addTo(map);
      shown.paths.get(sourceId)?.bringToBack();
      if (selectedId !== sourceId) shown.paths.get(selectedId)?.bringToFront();
    } else if (!layers.boundaries.visible) {
      overlay.remove();
    }
    map.getContainer().classList.toggle("hide-cadastral-labels", !layers.labels.visible);
    legend.hidden = !layers.boundaries.visible || !layers.fill.visible;
    if (shown.fill !== layers.fill.visible) {
      shown.parcels.setStyle(feature => style(feature));
      shown.fill = layers.fill.visible;
    }
    refreshLabels();
  }

  function highlight(feature) {
    const previous = shown.paths.get(selectedId);
    selectedId = feature.id;
    previous?.setStyle(style(previous.feature));
    const path = shown.paths.get(selectedId);
    path.setStyle(style(feature));
    if (selectedId !== sourceId) path.bringToFront();
    drawMeasurement(map, leaflet, measurements, measurementPane, feature.properties.measurement);
    refreshLabels();
  }

  async function prepare(source, neighbors, current) {
    const next = dataset(source.id);
    next.parcels.addData(source);
    let count = 0;
    for (const feature of neighbors.values()) {
      next.parcels.addData(feature);
      if (++count % 100 === 0) {
        await new Promise(resolve => setTimeout(resolve, 0));
        if (!current()) return null;
      }
    }
    return next;
  }

  function replace(source, neighbors, region, selected, prepared) {
    sourceId = source.id;
    selectedId = undefined;
    overlay.removeLayer(shown.parcels);
    shown = prepared ?? dataset(sourceId);
    if (!prepared) shown.parcels.addData(source).addData([...neighbors.values()]);
    if (shown.fill !== layers.fill.visible) {
      shown.parcels.setStyle(feature => style(feature));
      shown.fill = layers.fill.visible;
    }
    buffer.clearLayers();
    halo.clearLayers();
    if (region) {
      halo.addData(region);
      buffer.addData(region);
    }
    outline.clearLayers().addData(source);
    overlay.addLayer(shown.parcels);
    shown.paths.get(sourceId).bringToBack();
    highlight(selected);
  }

  function fit(area) {
    const toolbar = element("toolbar");
    const panel = element("details");
    const viewport = map.getContainer().getBoundingClientRect();
    const top = toolbar.hidden ? 24 : toolbar.getBoundingClientRect().bottom - viewport.top + 22;
    const panelBox = panel.getBoundingClientRect();
    const bottomSheet = !panel.hidden && panelBox.width > viewport.width / 2;
    const right = panel.hidden || bottomSheet ? 24 : viewport.right - panelBox.left + 8;
    const bottom = bottomSheet ? viewport.bottom - panelBox.top + 8 : 24;
    const bounds = area === "buffer" ? buffer.getBounds() : outline.getBounds();
    const hasRoom = viewport.width - right - 24 >= 80 && viewport.height - top - bottom >= 80;
    map.fitBounds(bounds, hasRoom
      ? { paddingTopLeft: [24, top], paddingBottomRight: [right, bottom], maxZoom: 17, animate: false }
      : { padding: [24, 24], maxZoom: 17, animate: false });
  }

  function parcelsAt(latlng) {
    if (!layers.boundaries.visible) return [];
    const point = map.latLngToLayerPoint(latlng);
    // Match the hit test of the bundled Leaflet 1.9.4 Canvas, including polygon holes.
    return [...shown.paths.values()].reverse()
      .filter(path => map.hasLayer(path) && path._containsPoint(point))
      .map(path => path.feature.id);
  }

  function clear() {
    overlay.removeLayer(shown.parcels);
    shown = dataset(null);
    overlay.addLayer(shown.parcels);
    sourceId = selectedId = undefined;
    buffer.clearLayers();
    halo.clearLayers();
    outline.clearLayers();
    measurements.clearLayers();
    labels.clearLayers();
  }

  refreshVisibility();
  return { prepare, replace, fit, highlight, refreshVisibility, parcelsAt, clear };
}
