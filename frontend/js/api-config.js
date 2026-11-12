// Auto-detect API base URL:
// - In production (Render): same origin as the page
// - In development (Live Server): Flask on port 5000
(function () {
  var loc = window.location;
  if (loc.port === "5500" || loc.port === "5501") {
    window.ROADGUARD_API_BASE = "http://127.0.0.1:5000/api";
  } else {
    window.ROADGUARD_API_BASE = loc.origin + "/api";
  }
})();
