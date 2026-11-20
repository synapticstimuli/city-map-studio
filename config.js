/* ==========================================
   CITY MAP STUDIO CONFIGURATION
   ========================================== */

const isLocalDevelopment = ["localhost", "127.0.0.1"].includes(
  window.location.hostname,
);

const CONFIG = Object.freeze({
  APP_NAME: "City Map Studio",
  VERSION: "2.0.0-dev",

  ENVIRONMENT: isLocalDevelopment ? "development" : "production",

  API_BASE_URL: isLocalDevelopment ? "http://localhost:8001" : "",

  POLL_INTERVAL: 3000,
  MAX_POLL_ATTEMPTS: 400,
});
