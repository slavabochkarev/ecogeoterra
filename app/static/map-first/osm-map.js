export function createOsmMap(map, leaflet, element, categories, onClick) {
  const shapePane = map.createPane("osmObjects");
  shapePane.style.zIndex = 412;
  const linePane = map.createPane("osmDistance");
  linePane.style.zIndex = 435;
  linePane.style.pointerEvents = "none";
  const objects = leaflet.layerGroup();
  const connection = leaflet.layerGroup();
  const overlay = leaflet.layerGroup([objects, connection]);
  const shapes = new Map();
  let dataset;
  let selected;

  function style(feature) {
    const active = selected?.id === feature.id;
    const color = categories[feature.properties.category].color;
    const point = feature.geometry.type.endsWith("Point");
    return { color: active ? "#145342" : point ? "#fff" : color, weight: active ? 4 : point ? 1.5 : 2.5,
      fillColor: color, fillOpacity: point ? 0.86 : active ? 0.3 : 0.14,
      dashArray: feature.geometry.type.endsWith("Polygon") ? "5 3" : undefined };
  }

  function show(visible) {
    if (visible) overlay.addTo(map);
    else overlay.remove();
  }

  function render(current, features) {
    if (dataset !== current) {
      objects.clearLayers();
      shapes.clear();
      dataset = current;
      selected = undefined;
    }
    const ids = new Set(features.map(feature => feature.id));
    for (const [id, layer] of shapes) {
      if (!ids.has(id)) objects.removeLayer(layer);
    }
    for (const feature of features) {
      if (!shapes.has(feature.id)) {
        const layer = leaflet.geoJSON(feature, {
          pane: "osmObjects", bubblingMouseEvents: false, style,
          pointToLayer: (item, point) => leaflet.circleMarker(point, {
            ...style(item), pane: "osmObjects", bubblingMouseEvents: false, radius: 6, fillOpacity: 0.86,
          }),
        });
        const tooltip = document.createElement("span");
        tooltip.textContent = feature.properties.name;
        layer.bindTooltip(tooltip);
        layer.on("click", event => onClick(feature, event.latlng));
        shapes.set(feature.id, layer);
      }
      objects.addLayer(shapes.get(feature.id));
    }
  }

  function restyle(feature) {
    const layer = shapes.get(feature?.id);
    if (!layer) return;
    layer.setStyle(style(feature));
    layer.eachLayer(part => {
      if (feature.geometry.type.endsWith("Point")) {
        part.setRadius?.(feature === selected ? 8 : 6);
      }
      if (feature === selected) part.bringToFront?.();
    });
  }

  function select(feature, focus = false) {
    const previous = selected;
    selected = feature;
    restyle(previous);
    restyle(feature);
    connection.clearLayers();
    if (!feature) return;
    const source = dataset.metadata.source;
    const { distance_m: distance, nearest_point: nearest } = feature.properties;
    if (distance > 0.001 && Number.isFinite(nearest?.lat) && Number.isFinite(nearest?.lon)) {
      leaflet.polyline([[source.lat, source.lon], [nearest.lat, nearest.lon]], {
        pane: "osmDistance", color: "#23664e", dashArray: "4 5", weight: 2, interactive: false,
      }).addTo(connection);
    }
    if (!focus) return;
    const bounds = shapes.get(feature.id).getBounds();
    const panel = element("details");
    const panelBox = panel.getBoundingClientRect();
    const viewport = map.getContainer().getBoundingClientRect();
    const sheet = !panel.hidden && panelBox.width > viewport.width / 2;
    const right = panel.hidden || sheet ? 32 : viewport.right - panelBox.left + 10;
    const bottom = sheet ? viewport.bottom - panelBox.top + 12 : 32;
    const toolbar = element("toolbar");
    const top = toolbar.hidden ? 30 : toolbar.getBoundingClientRect().bottom - viewport.top + 20;
    const room = viewport.width - right > 115 && viewport.height - top - bottom > 80;
    map.fitBounds(bounds, room ? {
      paddingTopLeft: [32, top], paddingBottomRight: [right, bottom], maxZoom: 17, animate: false,
    } : { padding: [32, 32], maxZoom: 17, animate: false });
  }

  return { render, select, show };
}
