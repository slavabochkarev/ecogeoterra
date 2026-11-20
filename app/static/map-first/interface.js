export function installLayers(state, element, onVisibilityChange) {
  const button = element("show-layers");
  const popup = element("layers");
  const count = element("layer-count");
  const switches = [...popup.querySelectorAll("input")];

  function updateSwitches() {
    const boundaries = state.layers.boundaries.visible;
    let active = 0;
    for (const input of switches) {
      const layer = state.layers[input.name];
      const requiresBoundaries = input.name === "labels" || input.name === "fill";
      input.disabled = !layer.available || (requiresBoundaries && !boundaries);
      input.checked = layer.available && layer.visible;
      if (!input.disabled && input.checked) active += 1;
    }
    count.textContent = String(active);
    count.setAttribute("aria-label", `Активных слоёв: ${active}`);
    element("cadastre-layer-note").hidden = boundaries;
  }

  function show(open, restoreFocus = false) {
    state.layersOpen = open;
    popup.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (restoreFocus) button.focus();
  }

  button.addEventListener("click", () => show(!state.layersOpen));
  function setVisible(name, visible) {
    if (!state.layers[name].available || state.layers[name].visible === visible) return;
    state.layers[name].visible = visible;
    updateSwitches();
    onVisibilityChange();
  }

  for (const input of switches) {
    input.addEventListener("change", () => {
      if (!input.disabled) setVisible(input.name, input.checked);
    });
  }
  document.addEventListener("pointerdown", event => {
    if (state.layersOpen && !popup.contains(event.target) && !button.contains(event.target)) show(false);
  });
  updateSwitches();
  function restore(flags) {
    for (const [name, layer] of Object.entries(state.layers)) layer.visible = flags[name];
    show(false);
    updateSwitches();
    onVisibilityChange();
  }
  return { show, setVisible, restore };
}

export function installPanels(workspace, state, element, showLayers, updateLayout, changed) {
  const toolbar = element("toolbar");
  const details = element("details");
  const panelButton = element("show-panel");
  const showMap = element("show-map");
  const exitMap = element("exit-map");
  const tabs = [...details.querySelectorAll('[role="tab"]')];
  let modeReturnFocus;

  function renderMapPane() {
    const showingOopt = state.tab === "oopt" && state.panelOpen && !state.mapOnly;
    workspace.classList.toggle("is-oopt-view", showingOopt);
    element("oopt-map-pane").hidden = !showingOopt;
    updateLayout();
  }

  function renderMode() {
    const panelVisible = state.panelOpen && !state.mapOnly;
    toolbar.hidden = state.mapOnly;
    details.hidden = !panelVisible;
    exitMap.hidden = !state.mapOnly;
    panelButton.setAttribute("aria-expanded", String(panelVisible));
    workspace.classList.toggle("is-panel-open", panelVisible);
    workspace.classList.toggle("is-map-only", state.mapOnly);
    renderMapPane();
    changed();
  }

  function selectTab(tab, focus = false) {
    state.tab = tab.getAttribute("aria-controls");
    for (const candidate of tabs) {
      const selected = candidate === tab;
      candidate.setAttribute("aria-selected", String(selected));
      candidate.tabIndex = selected ? 0 : -1;
      element(candidate.getAttribute("aria-controls")).hidden = !selected;
    }
    if (focus) tab.focus();
    renderMapPane();
    changed();
  }

  function showPanel(open) {
    state.panelOpen = open;
    renderMode();
    if (open) tabs.find(tab => tab.getAttribute("aria-controls") === state.tab).focus();
    else panelButton.focus();
  }

  function leaveMapOnly() {
    state.mapOnly = false;
    showLayers(false);
    renderMode();
    modeReturnFocus?.focus();
  }

  panelButton.addEventListener("click", () => showPanel(!state.panelOpen));
  element("close-panel").addEventListener("click", () => showPanel(false));
  showMap.addEventListener("click", () => {
    modeReturnFocus = document.activeElement;
    state.mapOnly = true;
    showLayers(false);
    renderMode();
    exitMap.focus();
  });
  exitMap.addEventListener("click", leaveMapOnly);
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", event => {
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      selectTab(tabs[next], true);
    });
  }
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (state.layersOpen) showLayers(false, true);
    else if (state.mapOnly) leaveMapOnly();
    else if (state.panelOpen) showPanel(false);
    else return;
    event.preventDefault();
  });

  function showTab(name) {
    selectTab(element(`${name}-tab`));
    state.panelOpen = true;
    renderMode();
  }

  function restore(saved) {
    state.panelOpen = saved.panel_open;
    state.mapOnly = saved.map_only;
    selectTab(element(`${saved.tab}-tab`));
    renderMode();
  }
  return { showTab, restore };
}
