/* Add this whole file as a separate <script src="generator-panel.js"></script>
   after <script src="app.js"></script> near end of index.html.
   Available ONLY while local backend runs on port 8001. */
(() => {
  window.CMS_GENERATOR_BUILD = "local-v23-dev2";
  const base = CONFIG.API_BASE_URL;
  const el = (id) => document.getElementById(id);
  const btn = el("generateMap");
  if (!btn) return;
  if (!base) {
    btn.disabled = true;

    el("genStatus").textContent =
      "Online map generation is currently unavailable.";

    return;
  }
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    el("genStatus").textContent = "Submitting map generation…";
    try {
      const body = {
        city: el("genCity").value.trim(),
        state: el("genState").value.trim(),
        county: el("genCounty").value.trim(),
        country: el("genCountry").value.trim(),
        road_mode: el("genMode").value,
        regional_context: el("genScope").value === "regional",
        regional_buffer_km: 3.0,
        style: "fine",
        include_paths: true,
        show_boundary: false,
      };
      if (body.regional_context) {
        const healthResponse = await fetch(`${base}/api/health`, { cache: "no-store" });
        if (!healthResponse.ok) {
          throw new Error("Regional generation requires a running v2.2 backend.");
        }
        const health = await healthResponse.json();
        if (!health.capabilities?.regional_context) {
          throw new Error(
            "The frontend is using the Regional feature, but the backend on port 8001 is an older City Map Studio version. Stop old servers, then restart the Regional preview launcher."
          );
        }
      }
      const created = await fetch(`${base}/api/maps`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const job = await created.json();
      if (!created.ok) throw new Error(JSON.stringify(job.detail || job));
      const renderComplete = async (status) => {
        if (body.regional_context && status.regional_context !== true) {
          throw new Error(
            "The backend returned a City-only map for a Regional request. Restart the v2.2 backend and retry."
          );
        }
        const url = `${base}${status.download_url}`;
        el("genStatus").textContent = `${status.city} ready. Loading preview...`;
        await loadGeneratedMap(url, body, body.road_mode);
        if (body.regional_context && !document.querySelector("#mapMount svg #regional-context")) {
          throw new Error(
            "The returned SVG has no regional-context layer. Check the generator log and backend version."
          );
        }
        const timing = status.total_seconds != null
          ? ` (queue ${status.queue_seconds ?? "?"}s; generation ${status.generation_seconds ?? "?"}s)`
          : status.cache_hit ? " (cached)" : "";
        el("genStatus").textContent = `${status.city} loaded${timing}.`;
      };
      if (job.status === "complete") {
        await renderComplete(job);
        return;
      }
      for (let attempt = 0; attempt < CONFIG.MAX_POLL_ATTEMPTS; attempt++) {
        await pause(CONFIG.POLL_INTERVAL);
        const response = await fetch(
          `${base}/api/maps/${encodeURIComponent(job.id)}`,
        );
        if (!response.ok) throw new Error(`Status failed (${response.status})`);
        const status = await response.json();
        const queueText = status.status === "queued" && status.queue_position
          ? ` (queue position ${status.queue_position})`
          : "";
        const stageText = status.status === "running" && status.stage
          ? ` — ${status.stage}`
          : "";
        el("genStatus").textContent =
          `${status.city}: ${status.status}${queueText}${stageText}…`;
        if (status.status === "error")
          throw new Error(status.error || "Generation failed");

        if (status.status === "complete") {
          await renderComplete(status);
          return;
        }
      }
      throw new Error(
        "Generation is still running; check the backend terminal",
      );
    } catch (e) {
      el("genStatus").textContent = `Error: ${e.message}`;
    } finally {
      btn.disabled = false;
    }
  });
})();
