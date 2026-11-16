/* Add this whole file as a separate <script src="generator-panel.js"></script>
   after <script src="app.js"></script> near end of index.html.
   Available ONLY while local backend runs on port 8001. */
(() => {
  const base = "http://localhost:8001";
  const el = (id) => document.getElementById(id);
  const btn = el("generateMap");
  if (!btn) return;
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
      for (let attempt = 0; attempt < 400; attempt++) {
        await pause(3000);
        const response = await fetch(
          `${base}/api/maps/${encodeURIComponent(job.id)}`,
        );
        if (!response.ok) throw new Error(`Status failed (${response.status})`);
        const status = await response.json();
        el("genStatus").textContent = `${status.city}: ${status.status}…`;
        if (status.status === "error")
          throw new Error(status.error || "Generation failed");

        if (status.status === "complete") {
          const url = `${base}${status.download_url}`;

          el("genStatus").textContent =
            `${status.city} generated. Loading preview...`;

          await loadGeneratedMap(url, body, body.road_mode);

          el("genStatus").textContent = `${status.city} generated and loaded.`;

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
