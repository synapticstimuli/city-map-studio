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
  let city = $("city").value;
  $("mapTitle").textContent = city;
  $("export").disabled = true;
  $("mapMount").innerHTML = '<div class="loading">Loading ' + city + "…</div>";
  svg = null;
  reset();
  setStatus("Loading " + city + " map...");
  try {
    let resp = await fetch(files[city]);
    if (!resp.ok) throw Error("HTTP " + resp.status);
    let source = await resp.text();
    if (token !== request) return;
    let doc = new DOMParser().parseFromString(source, "image/svg+xml");
    if (doc.querySelector("parsererror")) throw Error("Invalid SVG");
    let node = doc.documentElement;
    if (node.localName !== "svg") throw Error("Unexpected document");
    svg = document.importNode(node, true);
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
$("export").onclick = () => {
  if (!svg) return;
  let clone = svg.cloneNode(true);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", "420mm");
  clone.setAttribute("height", "297mm");
  const white = clone.querySelector("rect"); // preserve original white background
  for (const [control, id] of Object.entries(groups)) {
    let el = clone.querySelector(`[id="${id}"]`);
    if (!el) continue;
    el.setAttribute("display", $(control).checked ? "inline" : "none");
    if (control === "roads")
      el.setAttribute("stroke-width", $("roadWidth").value);
    if (control === "paths")
      el.setAttribute("stroke-width", $("pathWidth").value);
    if (control === "rail")
      el.setAttribute("stroke-width", $("railWidth").value);
    el.removeAttribute("style");
  }
  let xml =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    new XMLSerializer().serializeToString(clone);
  let url = URL.createObjectURL(
    new Blob([xml], { type: "image/svg+xml;charset=utf-8" }),
  );
  let link = document.createElement("a");
  link.href = url;
  link.download = $("city").value + "_Custom_Map.svg";
  document.body.append(link);
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
loadCity();
