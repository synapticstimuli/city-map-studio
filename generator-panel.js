/* Add this whole file as a separate <script src="generator-panel.js"></script>
   after <script src="app.js"></script> near end of index.html.
   Available ONLY while local backend runs on port 8001. */
(() => {
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
        style: "fine",
        include_paths: true,
        show_boundary: false,
      };
      const created = await fetch(`${base}/api/maps`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const job = await created.json();
      if (!created.ok) throw new Error(JSON.stringify(job.detail || job));
      const renderComplete = async (status) => {
        const url = `${base}${status.download_url}`;
        el("genStatus").textContent = `${status.city} ready. Loading preview...`;
        await loadGeneratedMap(url, body, body.road_mode);
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
        el("genStatus").textContent =
          `${status.city}: ${status.status}${queueText}…`;
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
