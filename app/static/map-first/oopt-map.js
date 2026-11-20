const svgNS = "http://www.w3.org/2000/svg";
const directions = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"];
const gridSteps = { 5000: 1000, 20000: 5000, 50000: 10000, 100000: 20000 };
const colors = [
  ["#72ad94", "#326453"], ["#e5a467", "#83541f"], ["#81a3c9", "#395d88"],
  ["#b68ecc", "#66477e"], ["#c0ba69", "#656021"], ["#71b8b2", "#306f6e"],
  ["#d58f9c", "#8d4452"],
];

function svgNode(tag, attributes, text) {
  const node = document.createElementNS(svgNS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text != null) node.textContent = text;
  return node;
}

function pathData(geometry, scale) {
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.map(polygon => polygon.map(ring => ring.map(
    ([x, y], index) => (index ? "L" : "M") + (x * scale).toFixed(4) + " " + (-y * scale).toFixed(4)
  ).join("") + "Z").join("")).join("");
}

export function installOoptMap(element, onSelect, formatDistance) {
  const svg = element("oopt-map");
  const caption = element("oopt-map-caption");
  const empty = element("oopt-map-empty");
  const key = element("oopt-map-key");
  const shapes = new Map();
  let zonesLayer, selectionLayer, selected, radius, captionText;

  svg.addEventListener("click", event => {
    const number = event.target.closest("[data-oopt-number]")?.dataset.ooptNumber;
    if (number) onSelect(number);
  });

  function render(zones, radiusM) {
    svg.toggleAttribute("hidden", !zones);
    key.hidden = !zones;
    empty.hidden = Boolean(zones);
    caption.textContent = "";
    svg.replaceChildren();
    shapes.clear();
    selected = undefined;
    if (!zones) return;

    radius = radiusM;
    const scale = 100 / radius;
    const step = gridSteps[radius];
    captionText = "Радиус " + radius / 1000 + " км · сетка " + step / 1000 + " км · НСПД";
    caption.textContent = captionText;
    const clip = svgNode("clipPath", { id: "oopt-circle-clip" });
    clip.append(svgNode("circle", { r: 100 }));
    const inside = svgNode("g", { "clip-path": "url(#oopt-circle-clip)" });
    const gridSegments = [];
    for (let offset = -radius + step; offset < radius; offset += step) {
      const v = (offset * scale).toFixed(3);
      gridSegments.push("M" + v + " -100V100M-100 " + v + "H100");
    }
    inside.append(svgNode("circle", { r: 100, class: "oopt-disc" }),
      svgNode("path", { d: gridSegments.join(""), class: "oopt-grid" }));
    zonesLayer = svgNode("g", {});
    inside.append(zonesLayer);
    for (const [index, zone] of zones.entries()) {
      const number = zone.registry_number;
      const group = svgNode("g", { class: "oopt-zone", "data-oopt-number": number });
      const [color, edge] = colors[index % colors.length];
      group.style.setProperty("--oopt-color", color);
      group.style.setProperty("--oopt-edge", edge);
      group.append(svgNode("title", {}, number));
      if (zone.geometry) {
        group.append(svgNode("path", {
          class: "oopt-shape", d: pathData(zone.geometry, scale), "fill-rule": "evenodd",
        }));
      }
      const [x, y] = zone.marker;
      // The list remains available when very small or overlapping polygons are hard to hit.
      group.append(svgNode("circle", { cx: x * scale, cy: -y * scale, r: 1.15, class: "oopt-mark" }));
      shapes.set(number, group);
      zonesLayer.append(group);
    }
    selectionLayer = svgNode("g", {});
    inside.append(selectionLayer, svgNode("circle", { r: 1.75, class: "oopt-center" }));
    svg.append(clip, inside, svgNode("circle", { r: 100, class: "oopt-edge" }));
    directions.forEach((name, index) => {
      const angle = index * Math.PI / 4;
      svg.append(svgNode("text", { x: Math.sin(angle) * 107, y: -Math.cos(angle) * 107,
        class: "oopt-axis-label" }, name));
    });
    svg.append(svgNode("text", { x: 58, y: 77, class: "oopt-radius-label" }, radius / 1000 + " км"));
  }

  function select(zone) {
    if (!selectionLayer || svg.hasAttribute("hidden")) return;
    if (selected) shapes.get(selected)?.classList.remove("is-selected");
    selected = zone?.registry_number;
    selectionLayer.replaceChildren();
    caption.textContent = captionText;
    if (!zone) return;
    const shape = shapes.get(selected);
    shape.classList.add("is-selected");
    zonesLayer.append(shape);
    const distance = formatDistance(zone.distance_m);
    caption.textContent = selected + " · " + distance + (zone.inside
      ? " · точка внутри ООПТ · до границы " + formatDistance(zone.boundary_distance_m) : "");
    const [x, y] = zone.nearest_boundary.map((value, index) => value * 100 / radius * (index ? -1 : 1));
    if (Math.hypot(x, y) > 100) return;
    selectionLayer.append(svgNode("line", { x1: 0, y1: 0, x2: x, y2: y, class: "oopt-leader" }),
      svgNode("circle", { cx: x, cy: y, r: 1.35, class: "oopt-distance-point" }),
      svgNode("text", { x: x / 2, y: y / 2 - 2.5, class: "oopt-distance-label" },
        zone.inside ? formatDistance(zone.boundary_distance_m) : distance));
  }

  return { render, select };
}
