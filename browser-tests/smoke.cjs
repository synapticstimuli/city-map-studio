/* End-to-end smoke test against a locally served City Map Studio frontend.
 * Usage: node browser-tests/smoke.cjs  (requires Playwright/Chromium).
 */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto("http://127.0.0.1:8000", { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => {
      const svg = document.querySelector("#mapMount svg");
      return svg && svg.querySelectorAll("path").length > 50;
    }, { timeout: 20000 });
    assert.equal(await page.locator("#mapTitle").textContent(), "Somerville");
    assert.equal(await page.locator("#mapSubtitle").textContent(), "Massachusetts · vector artwork");
    assert.equal(await page.locator("#export").isEnabled(), true);
    assert.equal(await page.evaluate(() => window.CMS_GENERATOR_BUILD), "local-v23-dev1");
    assert.equal(await page.locator("#genScope").inputValue(), "city");
    assert.equal(await page.locator("#regionalLayer").isDisabled(), true);
    await page.locator("#genScope").selectOption("regional");
    assert.equal(await page.locator("#genScope").inputValue(), "regional");
    // Simulate a v2.1 backend: the Regional frontend must reject it before
    // posting a map job, rather than silently accepting a City-only map.
    let posted = false;
    await page.route("**/api/health", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "*" },
        body: JSON.stringify({ status: "ok", version: "2.1.0" }),
      })
    );
    await page.route("**/api/maps", (route) => {
      posted = true;
      return route.abort();
    });
    await page.locator("#genCity").fill("Watertown");
    await page.locator("#generateMap").click();
    await page.waitForFunction(() =>
      document.querySelector("#genStatus")?.textContent.includes("older City Map Studio")
    );
    assert.equal(posted, false, "Frontend should not post to an old backend");
    await page.unroute("**/api/health");
    await page.unroute("**/api/maps");

    // A v2.2 endpoint must receive a Regional request, never City-only.
    let submittedBody = null;
    await page.route("**/api/health", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "*" },
        body: JSON.stringify({
          status: "ok",
          version: "2.2.0",
          capabilities: { regional_context: true },
        }),
      })
    );
    await page.route("**/api/maps", (route) => {
      submittedBody = route.request().postDataJSON();
      return route.fulfill({
        status: 422,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "*" },
        body: JSON.stringify({ detail: "fixture request intercepted" }),
      });
    });
    await page.locator("#genScope").selectOption("regional");
    await page.locator("#generateMap").click();
    await page.waitForFunction(() =>
      document.querySelector("#genStatus")?.textContent.includes("fixture request intercepted")
    );
    assert.equal(submittedBody.regional_context, true);
    assert.equal(submittedBody.regional_buffer_km, 3);
    await page.unroute("**/api/health");
    await page.unroute("**/api/maps");
    await page.locator("#genScope").selectOption("city");


    await page.locator('[data-preset="bold"]').click();
    assert.equal(await page.locator("#roadWidth").inputValue(), "0.28");
    await page.locator("#boundary").check();
    const boundaryDisplay = await page.locator("#mapMount #city-boundary").evaluate(
      (el) => el.style.display
    );
    assert.notEqual(boundaryDisplay, "none");

    await page.locator("#paperSize").selectOption("18x24");
    await page.locator("#orientation").selectOption("portrait");
    assert.match(await page.locator("#paperDimensions").textContent(), /457.2/);

    const downloadPromise = page.waitForEvent("download", { timeout: 15000 });
    await page.locator("#export").click();
    const download = await downloadPromise;
    assert.match(download.suggestedFilename(), /18x24_portrait\.svg$/);
    const path = await download.path();
    const fs = require("node:fs");
    const exported = fs.readFileSync(path, "utf8");
    assert.match(exported, /<svg\b/);
    assert.match(exported, /city-boundary/);
    assert.match(exported, /457\.2mm/);

    await page.locator("#city").selectOption("Cambridge");
    await page.waitForFunction(() => document.querySelector("#mapTitle").textContent === "Cambridge"
      && document.querySelector("#mapMount svg")?.querySelectorAll("path").length > 50);
    assert.equal(await page.locator("#export").isEnabled(), true);
    await page.locator("#zoomIn").click();
    assert.match(await page.locator("#mapMount").getAttribute("style"), /scale\(1\.25\)/);
    await page.locator("#zoomReset").click();
    assert.match(await page.locator("#mapMount").getAttribute("style"), /scale\(1\)/);
    // Zoom slider: log2 scale, kept in step with the buttons.
    await page.locator("#zoomSlider").fill("1");
    assert.match(await page.locator("#mapMount").getAttribute("style"), /scale\(2\)/);
    await page.locator("#zoomIn").click();
    assert.equal(Number(await page.locator("#zoomSlider").inputValue()).toFixed(2), Math.log2(2.5).toFixed(2));
    await page.locator("#zoomReset").click();
    assert.equal(await page.locator("#zoomSlider").inputValue(), "0");

    // Fast zoom preview: a bitmap is shown while zooming; the live SVG
    // comes back when zoomed past the bitmap's sharp range.
    await page.evaluate(() => { SNAPSHOT_MIN_PATHS = 0; reset(); scheduleSnapshot(); });
    await page.waitForFunction(() => document.querySelector("#mapMount .map-snapshot"), null, { timeout: 20000 });
    await page.locator("#zoomIn").click();
    assert.equal(await page.locator("#mapMount .map-snapshot").isVisible(), true);
    assert.equal(await page.locator("#mapMount svg").isVisible(), false);
    const sharpUpTo = await page.evaluate(() => snapshot.sharpUpTo);
    await page.evaluate((limit) => { while (scale <= limit) zoom(1.25); }, sharpUpTo);
    await page.waitForFunction(() => getComputedStyle(document.querySelector("#mapMount svg")).display !== "none");
    assert.equal(await page.locator("#mapMount .map-snapshot").isVisible(), false);
    await page.locator("#zoomReset").click();
    const svgDownload = page.waitForEvent("download", { timeout: 15000 });
    await page.locator("#export").click();
    const snapshotExport = fs.readFileSync(await (await svgDownload).path(), "utf8");
    assert.doesNotMatch(snapshotExport, /<canvas/);
    assert.match(snapshotExport, /city-boundary/);
    await page.evaluate(() => { SNAPSHOT_MIN_PATHS = 20000; });

    // A generated or library map shows its own state, not Massachusetts.
    await page.evaluate(() => {
      const key = registerGeneratedMap(
        "maps/Boston_Massachusetts_Detailed_Map.svg",
        { city: "Los Angeles", county: "", state: "California", country: "USA" },
        "centerline",
      );
      document.querySelector("#city").value = key;
      return loadCity();
    });
    assert.equal(await page.locator("#mapTitle").textContent(), "Los Angeles");
    assert.equal(await page.locator("#mapSubtitle").textContent(), "California · vector artwork");
    await page.evaluate(() => {
      const key = registerGeneratedMap(
        "maps/Boston_Massachusetts_Detailed_Map.svg",
        { city: "Paris", county: "", state: "", country: "France" },
        "centerline",
      );
      document.querySelector("#city").value = key;
      return loadCity();
    });
    assert.equal(await page.locator("#mapSubtitle").textContent(), "France · vector artwork");
    await page.locator("#city").selectOption("Cambridge");
    await page.waitForFunction(() => document.querySelector("#mapTitle").textContent === "Cambridge");
    assert.equal(await page.locator("#mapSubtitle").textContent(), "Massachusetts · vector artwork");

    if (errors.length) throw new Error("Browser JavaScript errors: " + errors.join(" | "));
    console.log("PASS: city loading, map styling, layer visibility, export, paper orientation, zoom");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
