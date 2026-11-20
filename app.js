const $ = (id) => document.getElementById(id);
const files = {
  Somerville: "maps/Somerville_Massachusetts_Detailed_Map.svg",
  Cambridge: "maps/Cambridge_Massachusetts_Detailed_Map.svg",
  Boston: "maps/Boston_Massachusetts_Detailed_Map.svg",
};
const presets = {
  fine: { roadWidth: 0.1, pathWidth: 0.07, railWidth: 0.12 },
  detailed: { roadWidth: 0.16, pathWidth: 0.1, railWidth: 0.18 },
  bold: { roadWidth: 0.28, pathWidth: 0.18, railWidth: 0.3 },
};
const groups = {
  roads: "road-edges",
  paths: "path-edges",
  rail: "railways",
  boundary: "city-boundary",
};
let svg = null,
  scale = 1,
  dx = 0,
  dy = 0,
  dragging = false,
  start = null,
  request = 0;
function setStatus(message) {
  $("status").textContent = message;
}
function styleMap() {
  if (!svg) return;
  for (const [control, id] of Object.entries(groups)) {
    const el = svg.querySelector(`[id="${id}"]`);
    if (!el) continue;
    el.style.display = $(control).checked ? "" : "none";
    if (control === "roads")
      el.setAttribute("stroke-width", $("roadWidth").value);
    if (control === "paths")
      el.setAttribute("stroke-width", $("pathWidth").value);
    if (control === "rail")
      el.setAttribute("stroke-width", $("railWidth").value);
  }
  for (const k of ["road", "path", "rail"])
    $(k + "Value").textContent =
      Number($(k + "Width").value).toFixed(2) + " mm";
}
function view() {
  $("mapMount").style.transform = `translate(${dx}px,${dy}px) scale(${scale})`;
}
function reset() {
  scale = 1;
  dx = dy = 0;
  view();
}
async function loadCity() {
  let token = ++request;
  const selected = $("city").selectedOptions[0];
  const city = selected.dataset.city || selected.value;
  const mapUrl = files[selected.value];
  $("mapTitle").textContent = city;
  $("export").disabled = true;
  $("mapMount").innerHTML = '<div class="loading">Loading ' + city + "…</div>";
  svg = null;
  reset();
  setStatus("Loading " + city + " map...");
  try {
    let resp = await fetch(mapUrl);
    if (!resp.ok) throw Error("HTTP " + resp.status);
    let source = await resp.text();
    if (token !== request) return;
    let doc = new DOMParser().parseFromString(source, "image/svg+xml");
    if (doc.querySelector("parsererror")) throw Error("Invalid SVG");
    let node = doc.documentElement;
    if (node.localName !== "svg") throw Error("Unexpected document");
    svg = document.importNode(node, true);

    const isCenterline = svg.querySelector('[id="road-centerlines"]') !== null;

    groups.roads = isCenterline ? "road-centerlines" : "road-edges";

    groups.paths = isCenterline ? "path-centerlines" : "path-edges";

    $("roads").parentElement.lastChild.textContent = isCenterline
      ? "Road centerlines"
      : "Road edges";

    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", city + " street map");
    $("mapMount").replaceChildren(svg);
    styleMap();
    $("export").disabled = false;
    setStatus(
      city +
        " loaded · " +
        svg.querySelectorAll("path").length.toLocaleString() +
        " paths",
    );
  } catch (e) {
    if (token !== request) return;
    setStatus("Error loading map: " + e.message);
    $("mapMount").textContent =
      "Unable to load map. Serve the site through a local web server.";
  }
}
$("city").addEventListener("change", loadCity);
for (const id of [
  ...Object.keys(groups),
  "roadWidth",
  "pathWidth",
  "railWidth",
])
  $(id).addEventListener("input", styleMap);
document.querySelectorAll("[data-preset]").forEach((btn) =>
  btn.addEventListener("click", () => {
    let p = presets[btn.dataset.preset];
    for (let k in p) $(k).value = p[k];
    document
      .querySelectorAll("[data-preset]")
      .forEach((b) => b.classList.toggle("active", b === btn));
    styleMap();
  }),
);
function zoom(factor) {
  scale = Math.max(0.5, Math.min(8, scale * factor));
  view();
}
$("zoomIn").onclick = () => zoom(1.25);
$("zoomOut").onclick = () => zoom(0.8);
$("zoomReset").onclick = reset;
$("canvas").addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    zoom(e.deltaY < 0 ? 1.12 : 0.89);
  },
  { passive: false },
);
$("canvas").addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  dragging = true;
  start = [e.clientX - dx, e.clientY - dy];
  $("canvas").setPointerCapture(e.pointerId);
  $("canvas").classList.add("dragging");
});
$("canvas").addEventListener("pointermove", (e) => {
  if (!dragging) return;
  dx = e.clientX - start[0];
  dy = e.clientY - start[1];
  view();
});
for (let ev of ["pointerup", "pointercancel", "lostpointercapture"])
  $("canvas").addEventListener(ev, () => {
    dragging = false;
    $("canvas").classList.remove("dragging");
  });

/* ==========================================
   SVG EXPORT
   ========================================== */

$("export").onclick = () => {
  if (!svg) return;

  // Get user-selected physical page dimensions.
  const { width, height } = getPaperDimensions();

  // Clone the current map with its selected
  // styling and visible/hidden layers.
  const clone = svg.cloneNode(true);

  // Read the map's original internal coordinates.
  const sourceViewBox = svg.viewBox.baseVal;

  const sourceWidth = sourceViewBox.width;
  const sourceHeight = sourceViewBox.height;

  // Initial uniform fit: preserve aspect ratio.
  // The map occupies up to 90% of the page.
  const marginFactor = 0.9;

  const fitScale = Math.min(
    (width * marginFactor) / sourceWidth,
    (height * marginFactor) / sourceHeight,
  );

  const fittedWidth = sourceWidth * fitScale;
  const fittedHeight = sourceHeight * fitScale;

  // Center the artwork on the selected page.
  const offsetX = (width - fittedWidth) / 2;
  const offsetY = (height - fittedHeight) / 2;

  // Create a new SVG document for printing.
  const ns = "http://www.w3.org/2000/svg";

  const exportSvg = document.createElementNS(ns, "svg");

  exportSvg.setAttribute("xmlns", ns);
  exportSvg.setAttribute("width", `${width}mm`);
  exportSvg.setAttribute("height", `${height}mm`);
  exportSvg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  // White page background.
  const background = document.createElementNS(ns, "rect");

  background.setAttribute("x", 0);
  background.setAttribute("y", 0);
  background.setAttribute("width", width);
  background.setAttribute("height", height);
  background.setAttribute("fill", "#ffffff");

  exportSvg.appendChild(background);

  // Position and scale the original map artwork.
  const mapGroup = document.createElementNS(ns, "g");

  mapGroup.setAttribute(
    "transform",
    `translate(${offsetX} ${offsetY}) scale(${fitScale}) ` +
      `translate(${-sourceViewBox.x} ${-sourceViewBox.y})`,
  );

  // The original SVG contains its own white
  // background. Remove it from the cloned artwork.
  const originalBackground = clone.querySelector("rect");

  if (originalBackground) {
    originalBackground.remove();
  }

  // Copy all remaining SVG elements into the group.
  while (clone.firstChild) {
    mapGroup.appendChild(clone.firstChild);
  }

  exportSvg.appendChild(mapGroup);

  // Preserve current layer visibility and widths.
  for (const [control, id] of Object.entries(groups)) {
    const element = exportSvg.querySelector(`[id="${id}"]`);

    if (!element) continue;

    element.removeAttribute("style");

    element.setAttribute("display", $(control).checked ? "inline" : "none");

    if (control === "roads") {
      element.setAttribute("stroke-width", $("roadWidth").value);
    }

    if (control === "paths") {
      element.setAttribute("stroke-width", $("pathWidth").value);
    }

    if (control === "rail") {
      element.setAttribute("stroke-width", $("railWidth").value);
    }
  }

  // Serialize the SVG into a downloadable file.
  const xml =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    new XMLSerializer().serializeToString(exportSvg);

  const blob = new Blob([xml], {
    type: "image/svg+xml;charset=utf-8",
  });

  const url = URL.createObjectURL(blob);

  const link = document.createElement("a");

  link.href = url;
  link.download =
    `${$("city").value}_${$("paperSize").value}_` +
    `${$("orientation").value}.svg`;

  document.body.appendChild(link);
  link.click();
  link.remove();

  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

$("resetStyle").addEventListener("click", () => {
  document.querySelector('[data-preset="fine"]').click();

  $("roads").checked = true;
  $("paths").checked = true;
  $("rail").checked = true;
  $("boundary").checked = false;

  styleMap();
});

/* ==========================================
   PRINT SETTINGS
   ========================================== */

// Paper dimensions in millimeters
const paperSizes = {
  A3: [297, 420],
  "11x14": [279.4, 355.6],
  "16x20": [406.4, 508],
  "18x24": [457.2, 609.6],
  "24x36": [609.6, 914.4],
};

// Calculate selected paper dimensions
function getPaperDimensions() {
  const paper = $("paperSize").value;
  const orientation = $("orientation").value;

  const [shortSide, longSide] = paperSizes[paper];

  if (orientation === "landscape") {
    return {
      width: longSide,
      height: shortSide,
    };
  }

  return {
    width: shortSide,
    height: longSide,
  };
}

/* Resize the visible print page to match
   the selected paper dimensions. */

function updatePrintPreview() {
  const { width, height } = getPaperDimensions();

  const canvas = $("canvas");
  const mount = $("mapMount");

  // Available space inside the workspace.
  const availableWidth = Math.min(canvas.clientWidth * 0.9, 1100);

  const availableHeight = Math.min(canvas.clientHeight * 0.8, 750);

  // Scale the page uniformly to fit.
  const previewScale = Math.min(
    availableWidth / width,
    availableHeight / height,
  );

  const previewWidth = width * previewScale;
  const previewHeight = height * previewScale;

  // Apply physical page proportions to
  // the white preview surface.
  mount.style.width = `${previewWidth}px`;
  mount.style.height = `${previewHeight}px`;
  mount.style.aspectRatio = `${width} / ${height}`;

  // Recenter and fit the preview.
  reset();
}

window.addEventListener("resize", updatePrintPreview);

// Update the dimensions displayed in the sidebar
function updatePaperDimensions() {
  const { width, height } = getPaperDimensions();

  $("paperDimensions").textContent =
    `${width.toFixed(1)} × ${height.toFixed(1)} mm`;

  updatePrintPreview();
}

// Listen for changes to the dropdowns
$("paperSize").addEventListener("change", updatePaperDimensions);
$("orientation").addEventListener("change", updatePaperDimensions);

// Initialize the displayed dimensions
updatePaperDimensions();

/* ==========================================
   GENERATED MAP LOADER
   ========================================== */

function registerGeneratedMap(url, location, mode) {
  const selector = $("city");

  const key = getGeneratedMapKey(location, mode);

  files[key] = url;

  let option = Array.from(selector.options).find((item) => item.value === key);

  if (!option) {
    option = document.createElement("option");
    option.value = key;

    $("savedMaps").appendChild(option);
  }

  option.dataset.city = location.city;
  option.dataset.state = location.state;
  option.dataset.country = location.country;
  option.dataset.mode = mode;

  const modeLabel = mode === "centerline" ? "Centerlines" : "Road outlines";

  option.textContent =
    `${location.city}, ${location.state}, ` +
    `${location.country} (${modeLabel})`;

  return key;
}

async function loadGeneratedMap(url, location, mode) {
  const key = registerGeneratedMap(url, location, mode);

  $("city").value = key;

  await loadCity();

  if (!svg || $("city").value !== key) {
    throw new Error("Generated map could not be loaded.");
  }

  await loadMapLibrary();
}

/* ==========================================
   PERSISTENT MAP LIBRARY
   ========================================== */

const LIBRARY_API = CONFIG.API_BASE_URL;

function getGeneratedMapKey(location, mode) {
  return [
    location.city,
    location.county,
    location.state,
    location.country,
    mode,
  ].join("_");
}

async function loadMapLibrary() {
  if (!LIBRARY_API) {
    $("libraryStatus").textContent =
      "Online map library is not configured yet.";
    return;
  }
  try {
    const response = await fetch(`${LIBRARY_API}/api/library`);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const savedMaps = await response.json();

    // Records are already sorted newest first.
    // Retain the newest map of each location and mode.
    const seen = new Set();

    for (const map of savedMaps) {
      const key = getGeneratedMapKey(map, map.road_mode);

      if (seen.has(key)) continue;
      seen.add(key);

      const url = new URL(map.download_url, LIBRARY_API).href;

      registerGeneratedMap(url, map, map.road_mode);
    }

    $("libraryStatus").textContent =
      `${seen.size} saved map versions available.`;
  } catch (error) {
    console.error("Map library error:", error);

    $("libraryStatus").textContent =
      "Saved map library unavailable. Original maps remain accessible.";
  }
}

loadCity();
loadMapLibrary();
