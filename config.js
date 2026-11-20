/* Public configuration only. Never place secrets here. */
const CONFIG = Object.freeze({
  VERSION: "2.1.0-dev",
  API_BASE_URL: ["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "http://localhost:8001"
    : "",
  POLL_INTERVAL: 3000,
  MAX_POLL_ATTEMPTS: 400,
});
