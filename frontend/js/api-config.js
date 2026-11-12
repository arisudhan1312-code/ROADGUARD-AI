// Use the local Flask port for VS Code Live Server and same-origin APIs in production.
(function configureRoadguardApi() {
  const localHosts = ["localhost", "127.0.0.1"];
  const isLiveServer = localHosts.includes(window.location.hostname)
    && ["5500", "5501"].includes(window.location.port);
  const isLocalFile = window.location.protocol === "file:";
  const apiHost = window.location.hostname || "127.0.0.1";
  const defaultBase = isLiveServer || isLocalFile
    ? `http://${apiHost}:5000/api`
    : `${window.location.origin}/api`;
  const configuredBase = typeof window.ROADGUARD_API_BASE === "string"
    ? window.ROADGUARD_API_BASE.trim()
    : "";
  const metaBase = document.querySelector('meta[name="roadguard-api-base"]')?.content.trim() || "";

  window.ROADGUARD_API_BASE = (configuredBase || metaBase || defaultBase).replace(/\/+$/, "");
})();
