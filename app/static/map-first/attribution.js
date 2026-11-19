export function installAttribution(element) {
  const footer = element("map-attributions");
  const workspace = element("workspace");
  const credit = element("osm-credit");
  const use = element("osm-credit-use");
  const details = element("details");
  const objectsTab = element("objects");
  const sources = { osm: true, geocoder: false, objectOrigin: false, objects: false, listedObjects: false };

  function render() {
    const objectsPanelOpen = !details.hidden && !objectsTab.hidden;
    const objects = sources.objects || (objectsPanelOpen && sources.listedObjects);
    const geocoder = sources.geocoder || (sources.objectOrigin && (objectsPanelOpen || sources.objects));
    credit.hidden = !sources.osm && !geocoder && !objects;
    use.textContent = sources.osm ? "" : objects && geocoder
      ? "Объекты и адреса " : objects ? "Объекты " : "Адреса ";
  }

  const observer = new MutationObserver(render);
  for (const panel of [details, objectsTab]) observer.observe(panel, { attributeFilter: ["hidden"] });
  new ResizeObserver(() => {
    workspace.style.setProperty("--attribution-height", `${Math.ceil(footer.getBoundingClientRect().height)}px`);
  }).observe(footer);

  return (key, visible) => {
    sources[key] = visible;
    render();
  };
}
