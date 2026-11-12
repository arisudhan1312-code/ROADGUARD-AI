// Driver GPS tracking and verified nearby hazards are loaded from the Flask API.
const apiBaseUrl = window.ROADGUARD_API_BASE;
const SEARCH_RADIUS_METERS = 500;
const MAX_ALERT_REPEAT_MS = 2 * 60 * 1000;
const MIN_HAZARD_REQUEST_INTERVAL_MS = 1000;
const ROUTE_HAZARD_BUFFER_METERS = 100;
const ROUTE_HAZARD_RISK_POINTS = {
  Critical: 8,
  High: 5,
  Medium: 3,
  Low: 1
};
const HAZARD_COLORS = {
  pothole: "#ed4f5c",
  "damaged road": "#f1767f",
  "broken streetlight": "#e9b64d",
  waterlogging: "#50b8dc",
  "road obstruction": "#a98ae8",
  other: "#93a5b5"
};

const mapElement = document.querySelector("#driver-map");
const mapError = document.querySelector("#map-error");
const gpsMessage = document.querySelector("#gps-message");
const startTrackingButton = document.querySelector("#start-tracking");
const stopTrackingButton = document.querySelector("#stop-tracking");
const gpsState = document.querySelector("#gps-state");
const warningPanel = document.querySelector("#proximity-warning");
const warningList = document.querySelector("#warning-list");
const historyTableBody = document.querySelector("#history-table-body");
const routeForm = document.querySelector("#route-form");
const routeFeedback = document.querySelector("#route-feedback");

let map = null;
let hazardMarkerLayer = null;
let routeHazardMarkerLayer = null;
let routeEndpointLayer = null;
let driverMarker = null;
let radiusCircle = null;
let watchId = null;
let currentPosition = null;
let routeLayers = [];
let currentRoutes = [];
let routeComparisons = [];
let recommendedRouteIndex = 0;
let routeRequestSequence = 0;
let nearbyHazards = [];
let routeHazards = [];
let routeHazardDataAvailable = false;
let routeStartCoordinates = null;
let routeDestinationCoordinates = null;
let selectingMapDestination = false;
let emptyHazardMessage = "Start GPS tracking to search for verified nearby hazards.";
let warningHazardSignature = "";
let history = [];
let searchTimer = null;
let requestController = null;
let requestSequence = 0;
let lastHazardRequestAt = 0;
let pendingSearch = false;
const lastAlertTimes = new Map();

function showGpsMessage(message, isError) {
  gpsMessage.textContent = message;
  gpsMessage.classList.toggle("is-error", isError);
}

function formatDistance(meters) {
  return meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`;
}

function formatAlertTime(date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function showRouteFeedback(message, isError) {
  routeFeedback.textContent = message;
  routeFeedback.hidden = false;
  routeFeedback.classList.toggle("is-error", isError);
}

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...options,
      credentials: "include",
      headers: { Accept: "application/json", ...(options.headers || {}) }
    });
  } catch (error) {
    console.error("Could not reach the ROADGUARD AI API:", error);
    throw new Error("Cannot reach Flask. Start the backend and open this page through VS Code Live Server.");
  }

  let result;
  try {
    result = await response.json();
  } catch (error) {
    console.error("The nearby-hazards API returned unreadable JSON:", error);
    throw new Error(`The API returned an unreadable response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const message = response.status === 401
      ? "Please log in as a driver to load nearby hazards."
      : (result.error || `The request failed (HTTP ${response.status}).`);
    throw new Error(message);
  }
  return result;
}

function getHazardColor(category) {
  return HAZARD_COLORS[String(category || "").toLowerCase()] || HAZARD_COLORS.other;
}

function createHazardPopup(hazard) {
  const popup = L.DomUtil.create("div", "hazard-popup");
  const title = L.DomUtil.create("strong", "", popup);
  title.textContent = `${hazard.hazard_priority || "Unrated"} priority · ${hazard.category}`;

  const location = L.DomUtil.create("p", "", popup);
  location.textContent = hazard.location_name || "Reported location";

  const priority = L.DomUtil.create("p", "", popup);
  priority.textContent = hazard.priority_explanation || "Priority recommendation unavailable.";

  const distance = L.DomUtil.create("p", "", popup);
  distance.textContent = `Approximately ${formatDistance(hazard.distance_meters)} away`;

  const id = L.DomUtil.create("small", "", popup);
  id.textContent = `Verified hazard ${hazard.hazard_id}`;
  return popup;
}

function initializeMap() {
  if (typeof L === "undefined") {
    mapError.textContent = "The Leaflet map library could not be loaded. Check your internet connection and reload.";
    mapError.hidden = false;
    return;
  }

  map = L.map(mapElement, { scrollWheelZoom: false }).setView([20, 0], 2);
  const tileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>'
  });
  tileLayer.on("tileerror", () => {
    mapError.textContent = "Some OpenStreetMap tiles could not be loaded. Check your connection or try again later.";
    mapError.hidden = false;
  });
  tileLayer.addTo(map);
  hazardMarkerLayer = L.layerGroup().addTo(map);
  routeHazardMarkerLayer = L.layerGroup().addTo(map);
  routeEndpointLayer = L.layerGroup().addTo(map);
  map.on("click", (event) => {
    if (!selectingMapDestination) {
      return;
    }
    selectingMapDestination = false;
    cancelPendingRouteSearch();
    clearRouteDisplay();
    routeDestinationCoordinates = {
      latitude: event.latlng.lat,
      longitude: event.latlng.lng
    };
    document.querySelector("#route-destination").value =
      `${routeDestinationCoordinates.latitude.toFixed(6)}, ${routeDestinationCoordinates.longitude.toFixed(6)}`;
    renderRouteEndpointMarkers();
    showRouteFeedback("Map destination selected. Submit to plan the route.", false);
  });
}

function renderNearbyHazards() {
  const list = document.querySelector("#hazard-list");
  list.replaceChildren();

  if (!nearbyHazards.length) {
    const emptyMessage = document.createElement("p");
    emptyMessage.className = "empty-history";
    emptyMessage.textContent = emptyHazardMessage;
    list.append(emptyMessage);
  }

  nearbyHazards.forEach((hazard) => {
    const card = document.createElement("article");
    card.className = "driver-hazard-card";

    const marker = document.createElement("span");
    marker.className = `hazard-marker hazard-${String(hazard.category).toLowerCase().replace(/[^a-z]+/g, "-")}`;
    marker.style.backgroundColor = getHazardColor(hazard.category);

    const details = document.createElement("div");
    details.className = "hazard-details";
    const title = document.createElement("strong");
    title.textContent = hazard.category;
    const location = document.createElement("small");
    location.textContent = hazard.location_name || "Reported location";
    const priority = document.createElement("small");
    priority.className = `driver-hazard-priority priority-${String(hazard.hazard_priority || "unknown").toLowerCase()}`;
    priority.textContent = `${hazard.hazard_priority || "Unrated"} priority · ${hazard.priority_score ?? "—"}/100`;
    const severity = document.createElement("small");
    severity.className = `pothole-severity severity-${String(hazard.severity).toLowerCase()}`;
    severity.textContent = `${hazard.severity} severity · verified`;
    details.append(title, location, priority, severity);

    const distance = document.createElement("span");
    distance.className = "hazard-distance";
    distance.textContent = formatDistance(hazard.distance_meters);
    card.append(marker, details, distance);
    list.append(card);
  });

  const countText = currentPosition
    ? `${nearbyHazards.length} verified ${nearbyHazards.length === 1 ? "hazard" : "hazards"}`
    : "Start GPS to search";
  document.querySelector("#hazard-record-count").textContent = countText;
  document.querySelector("#nearby-count").textContent = nearbyHazards.length;
  document.querySelector("#nearby-pothole-value").textContent =
    currentPosition
      ? nearbyHazards.filter((hazard) => hazard.category.toLowerCase() === "pothole").length
      : "—";
  document.querySelector("#nearby-other-value").textContent =
    currentPosition
      ? nearbyHazards.filter((hazard) => hazard.category.toLowerCase() !== "pothole").length
      : "—";
  document.querySelector("#active-alert-value").textContent = currentPosition ? nearbyHazards.length : "—";
  document.querySelector("#active-alert-note").textContent = !currentPosition
    ? "GPS tracking is off"
    : (nearbyHazards.length
      ? `Within ${SEARCH_RADIUS_METERS} m of your GPS location`
      : `No verified hazards within ${SEARCH_RADIUS_METERS} m`);
}

function addHazardMarkers() {
  if (!map || !hazardMarkerLayer) {
    return;
  }

  hazardMarkerLayer.clearLayers();
  nearbyHazards.forEach((hazard) => {
    const marker = L.circleMarker([hazard.latitude, hazard.longitude], {
      radius: 8,
      color: "#ffffff",
      weight: 2,
      fillColor: getHazardColor(hazard.category),
      fillOpacity: 0.95
    });
    marker.bindPopup(createHazardPopup(hazard));
    marker.addTo(hazardMarkerLayer);
  });
}

function addAlertHistory(hazard) {
  history.unshift({
    category: hazard.category,
    location: hazard.location_name || "Reported location",
    distance: formatDistance(hazard.distance_meters),
    time: formatAlertTime(new Date()),
    status: "Verified · active"
  });
  history = history.slice(0, 20);
  renderHistory();
}

function updateWarnings() {
  const signature = nearbyHazards.map((hazard) => hazard.hazard_id).sort().join("|");
  if (signature === warningHazardSignature) {
    return;
  }
  warningHazardSignature = signature;
  warningList.replaceChildren();
  nearbyHazards.forEach((hazard) => {
    const message = document.createElement("p");
    message.textContent =
      `${hazard.hazard_priority || "Unrated"} priority ${hazard.category} (${hazard.severity}) detected approximately ${formatDistance(hazard.distance_meters)} from your GPS location. Please slow down and watch the road.`;
    warningList.append(message);
  });
  warningPanel.hidden = nearbyHazards.length === 0;
}

function validateHazardsResponse(result, requireDistance = true) {
  if (!result || !Array.isArray(result.hazards)) {
    throw new Error("The API returned an invalid hazard list.");
  }
  return result.hazards.filter((hazard) =>
    hazard
    && typeof hazard.hazard_id === "string"
    && typeof hazard.category === "string"
    && typeof hazard.severity === "string"
    && typeof hazard.location_name === "string"
    && Number.isFinite(hazard.latitude)
    && hazard.latitude >= -90
    && hazard.latitude <= 90
    && Number.isFinite(hazard.longitude)
    && hazard.longitude >= -180
    && hazard.longitude <= 180
    && (
      !requireDistance
      || (
        Number.isFinite(hazard.distance_meters)
        && hazard.distance_meters >= 0
      )
    )
    && ["Active", "Assigned"].includes(hazard.status)
  );
}

function clearLiveHazards() {
  nearbyHazards = [];
  renderNearbyHazards();
  addHazardMarkers();
  updateWarnings();
}

async function loadNearbyHazards(position) {
  if (requestController) {
    pendingSearch = true;
    return;
  }
  requestController = new AbortController();
  const controller = requestController;
  const sequence = ++requestSequence;
  lastHazardRequestAt = Date.now();
  const query = new URLSearchParams({
    latitude: position.latitude.toFixed(7),
    longitude: position.longitude.toFixed(7),
    radius: String(SEARCH_RADIUS_METERS)
  });
  document.querySelector("#hazard-record-count").textContent = "Loading verified hazards…";
  document.querySelector("#active-alert-note").textContent = "Checking the nearby API";

  try {
    const result = await apiRequest(`/hazards/nearby?${query}`, { signal: controller.signal });
    if (sequence !== requestSequence) {
      return;
    }

    const hazards = validateHazardsResponse(result);
    const previousIds = new Set(nearbyHazards.map((hazard) => hazard.hazard_id));
    const now = Date.now();
    hazards.forEach((hazard) => {
      const lastAlert = lastAlertTimes.get(hazard.hazard_id) || 0;
      if (!previousIds.has(hazard.hazard_id) && now - lastAlert >= MAX_ALERT_REPEAT_MS) {
        addAlertHistory(hazard);
        lastAlertTimes.set(hazard.hazard_id, now);
        window.sendRoadguardHazardNotification?.(
          hazard,
          position,
          SEARCH_RADIUS_METERS
        ).catch((error) => {
          console.error("Could not save the driver hazard notification:", error);
        });
      }
    });

    nearbyHazards = hazards;
    emptyHazardMessage = "No verified unresolved hazards were returned within 500 metres.";
    renderNearbyHazards();
    addHazardMarkers();
    updateWarnings();
    showGpsMessage(
      `${hazards.length} verified unresolved ${hazards.length === 1 ? "hazard" : "hazards"} returned within ${SEARCH_RADIUS_METERS} m.`,
      false
    );
  } catch (error) {
    if (error.name === "AbortError" || sequence !== requestSequence) {
      return;
    }
    emptyHazardMessage = "Nearby hazard data is unavailable. Check your connection and try again.";
    clearLiveHazards();
    document.querySelector("#hazard-record-count").textContent = "API unavailable";
    document.querySelector("#nearby-count").textContent = "—";
    document.querySelector("#nearby-pothole-value").textContent = "—";
    document.querySelector("#nearby-other-value").textContent = "—";
    document.querySelector("#active-alert-value").textContent = "—";
    document.querySelector("#active-alert-note").textContent = "Nearby data is unavailable";
    showGpsMessage(error.message, true);
  } finally {
    if (requestController === controller) {
      requestController = null;
    }
    if (pendingSearch && currentPosition && watchId !== null) {
      pendingSearch = false;
      scheduleNearbySearch();
    }
  }
}

function scheduleNearbySearch() {
  if (!currentPosition || watchId === null) {
    return;
  }
  if (requestController) {
    pendingSearch = true;
    return;
  }
  if (searchTimer !== null) {
    return;
  }
  const waitTime = Math.max(
    350,
    MIN_HAZARD_REQUEST_INTERVAL_MS - (Date.now() - lastHazardRequestAt)
  );
  searchTimer = window.setTimeout(() => {
    searchTimer = null;
    if (currentPosition && watchId !== null) {
      loadNearbyHazards(currentPosition);
    }
  }, waitTime);
}

function updateDriverPosition(position) {
  const latitude = position.coords.latitude;
  const longitude = position.coords.longitude;
  if (
    !Number.isFinite(latitude) || latitude < -90 || latitude > 90
    || !Number.isFinite(longitude) || longitude < -180 || longitude > 180
  ) {
    showGpsMessage("The browser returned invalid GPS coordinates. Waiting for a valid location fix.", true);
    return;
  }

  currentPosition = { latitude, longitude };
  const latitudeText = latitude.toFixed(6);
  const longitudeText = longitude.toFixed(6);
  document.querySelector("#driver-latitude").textContent = latitudeText;
  document.querySelector("#driver-longitude").textContent = longitudeText;
  document.querySelector("#current-location-value").textContent = "Tracking active";
  document.querySelector("#location-summary-note").textContent = `${latitudeText}, ${longitudeText}`;
  gpsState.classList.add("is-tracking");
  gpsState.querySelector("span").textContent = "Tracking active";

  if (map) {
    if (!radiusCircle) {
      radiusCircle = L.circle([latitude, longitude], {
        radius: SEARCH_RADIUS_METERS,
        color: "#35df91",
        weight: 2,
        dashArray: "6 6",
        fillColor: "#35df91",
        fillOpacity: 0.06
      }).addTo(map);
    } else {
      radiusCircle.setLatLng([latitude, longitude]);
    }

    if (!driverMarker) {
      driverMarker = L.circleMarker([latitude, longitude], {
        radius: 8,
        color: "#ffffff",
        weight: 3,
        fillColor: "#1687ff",
        fillOpacity: 1
      }).bindPopup("Your current device GPS location.");
      driverMarker.addTo(map);
      map.setView([latitude, longitude], 16);
    } else {
      driverMarker.setLatLng([latitude, longitude]);
      map.panTo([latitude, longitude]);
    }
  }

  scheduleNearbySearch();
}

function clearCurrentPosition() {
  currentPosition = null;
  if (driverMarker && map) {
    map.removeLayer(driverMarker);
    driverMarker = null;
  }
  if (radiusCircle && map) {
    map.removeLayer(radiusCircle);
    radiusCircle = null;
  }
  document.querySelector("#driver-latitude").textContent = "—";
  document.querySelector("#driver-longitude").textContent = "—";
  document.querySelector("#current-location-value").textContent = "Tracking off";
  document.querySelector("#location-summary-note").textContent = "Start GPS to search nearby";
}

function stopTracking(showStatus) {
  if (watchId !== null && navigator.geolocation) {
    navigator.geolocation.clearWatch(watchId);
  }
  watchId = null;
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer);
    searchTimer = null;
  }
  pendingSearch = false;
  requestSequence += 1;
  if (requestController) {
    requestController.abort();
    requestController = null;
  }

  clearCurrentPosition();
  clearLiveHazards();
  startTrackingButton.disabled = false;
  stopTrackingButton.disabled = true;
  gpsState.classList.remove("is-tracking");
  gpsState.querySelector("span").textContent = "Tracking off";
  document.querySelector("#hazard-record-count").textContent = "Start GPS to search";
  document.querySelector("#active-alert-note").textContent = "GPS tracking is off";
  if (showStatus) {
    showGpsMessage("GPS tracking stopped. Nearby results have been cleared.", false);
  }
  emptyHazardMessage = "Start GPS tracking to search for verified nearby hazards.";
  renderNearbyHazards();
}

function describeGeolocationError(error) {
  const messages = {
    1: "Location permission was denied. Allow location access in your browser, then start GPS again.",
    2: "Your location is unavailable. Check your device location services and try again.",
    3: "GPS took too long to respond. Keep the page open and try again."
  };
  return messages[error.code] || "GPS could not determine your location. Please try again.";
}

startTrackingButton.addEventListener("click", () => {
  if (!navigator.geolocation) {
    showGpsMessage("This browser does not support geolocation. Try a current browser with location access.", true);
    return;
  }
  if (watchId !== null) {
    return;
  }

  startTrackingButton.disabled = true;
  stopTrackingButton.disabled = false;
  gpsState.classList.add("is-tracking");
  gpsState.querySelector("span").textContent = "Requesting GPS…";
  showGpsMessage("Requesting location permission. Your browser may ask you to allow access.", false);

  try {
    watchId = navigator.geolocation.watchPosition(
      updateDriverPosition,
      (error) => {
        const message = describeGeolocationError(error);
        if (error.code === 1) {
          stopTracking(false);
          showGpsMessage(message, true);
          return;
        }
        emptyHazardMessage = "GPS is unavailable. No nearby hazard results can be shown.";
        clearLiveHazards();
        document.querySelector("#hazard-record-count").textContent = "Location unavailable";
        document.querySelector("#nearby-count").textContent = "—";
        document.querySelector("#nearby-pothole-value").textContent = "—";
        document.querySelector("#nearby-other-value").textContent = "—";
        document.querySelector("#active-alert-value").textContent = "—";
        document.querySelector("#active-alert-note").textContent = "Location unavailable";
        showGpsMessage(message, true);
      },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 15000 }
    );
  } catch (error) {
    stopTracking(false);
    showGpsMessage(`Unable to start GPS tracking: ${error.message}`, true);
  }
});

stopTrackingButton.addEventListener("click", () => stopTracking(true));
window.addEventListener("beforeunload", () => stopTracking(false));

function renderHistory() {
  historyTableBody.replaceChildren();
  history.forEach((record) => {
    const row = document.createElement("tr");
    [record.category, record.location, record.distance, record.time].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    status.className = "driver-history-status";
    status.textContent = record.status;
    statusCell.append(status);
    row.append(statusCell);
    historyTableBody.append(row);
  });
  document.querySelector("#history-count").textContent = `${history.length} records`;
  document.querySelector("#empty-history").hidden = history.length > 0;
}

function parseCoordinates(value) {
  const match = value.trim().match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
  if (!match) {
    return null;
  }

  const latitude = Number(match[1]);
  const longitude = Number(match[2]);
  if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
    throw new Error("Coordinates are out of range. Latitude must be -90 to 90 and longitude -180 to 180.");
  }
  return { latitude, longitude };
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function fetchJson(url) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
      signal: controller.signal
    });
    if (!response.ok) {
      throw new Error(`The public mapping service returned HTTP ${response.status}.`);
    }
    return await response.json();
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("The mapping service took too long to respond. Please try again.");
    }
    if (error instanceof TypeError) {
      throw new Error("Could not reach the public map search or routing service. Check your internet connection.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function resolveRouteLocation(value) {
  const coordinates = parseCoordinates(value);
  if (coordinates) {
    return coordinates;
  }

  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.searchParams.set("format", "jsonv2");
  url.searchParams.set("limit", "1");
  url.searchParams.set("q", value);
  const results = await fetchJson(url.toString());
  if (!results.length) {
    throw new Error(`No location was found for "${value}". Try a more specific place or enter coordinates.`);
  }
  const geocodedCoordinates = {
    latitude: Number(results[0].lat),
    longitude: Number(results[0].lon)
  };
  if (
    !Number.isFinite(geocodedCoordinates.latitude)
    || geocodedCoordinates.latitude < -90
    || geocodedCoordinates.latitude > 90
    || !Number.isFinite(geocodedCoordinates.longitude)
    || geocodedCoordinates.longitude < -180
    || geocodedCoordinates.longitude > 180
  ) {
    throw new Error("The place lookup returned invalid coordinates. Try another location.");
  }
  return geocodedCoordinates;
}

function formatRouteDistance(meters) {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`;
}

function formatRouteDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "estimate unavailable";
  }
  if (seconds < 30) {
    return "under 1 min";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return remainder ? `${hours} hr ${remainder} min` : `${hours} hr`;
  }
  return `${minutes} min`;
}

function drawRoute(selectedIndex) {
  if (!map || !currentRoutes.length) {
    return;
  }

  routeLayers.forEach((layer) => map.removeLayer(layer));
  routeLayers = currentRoutes.map((route, index) => {
    const isSelected = index === selectedIndex;
    return L.geoJSON(route.geometry, {
      style: {
        color: isSelected ? "#35df91" : "#62bde4",
        weight: isSelected ? 7 : 4,
        opacity: isSelected ? 0.94 : 0.48
      }
    }).addTo(map);
  });

  const selectedLayer = routeLayers[selectedIndex];
  if (selectedLayer) {
    map.fitBounds(selectedLayer.getBounds(), { padding: [32, 32] });
  }
}

function selectRoute(index) {
  const route = currentRoutes[index];
  if (!route) {
    return;
  }
  drawRoute(index);
  document.querySelector("#route-status-value").textContent = routeStatusLabel(index);
  document.querySelectorAll(".route-option").forEach((option, optionIndex) => {
    option.classList.toggle("is-selected", optionIndex === index);
  });
  addRouteHazardMarkers(route);
}

function routeStatusLabel(index) {
  if (index === recommendedRouteIndex) {
    if (!routeHazardDataAvailable) {
      return "Suggested route · hazards unavailable";
    }
    return index === 0
      ? "Suggested original selected"
      : `Suggested alternative ${index} selected`;
  }
  return index === 0 ? "Original selected" : `Alternate ${index} selected`;
}

function renderRouteEndpointMarkers() {
  if (!map || !routeEndpointLayer) {
    return;
  }
  routeEndpointLayer.clearLayers();
  if (routeStartCoordinates) {
    L.circleMarker(
      [routeStartCoordinates.latitude, routeStartCoordinates.longitude],
      {
        radius: 8,
        color: "#e9fff4",
        weight: 2,
        fillColor: "#35df91",
        fillOpacity: 1
      }
    ).bindPopup("Selected route start").addTo(routeEndpointLayer);
  }
  if (routeDestinationCoordinates) {
    L.circleMarker(
      [routeDestinationCoordinates.latitude, routeDestinationCoordinates.longitude],
      {
        radius: 8,
        color: "#e9f8ff",
        weight: 2,
        fillColor: "#62bde4",
        fillOpacity: 1
      }
    ).bindPopup("Selected destination").addTo(routeEndpointLayer);
  }
}

function clearRouteDisplay() {
  routeLayers.forEach((layer) => {
    if (map) {
      map.removeLayer(layer);
    }
  });
  routeLayers = [];
  currentRoutes = [];
  routeComparisons = [];
  recommendedRouteIndex = 0;
  routeHazards = [];
  routeHazardDataAvailable = false;
  routeStartCoordinates = null;
  routeDestinationCoordinates = null;
  if (routeHazardMarkerLayer) {
    routeHazardMarkerLayer.clearLayers();
  }
  if (routeEndpointLayer) {
    routeEndpointLayer.clearLayers();
  }
  document.querySelector("#route-results").hidden = true;
  document.querySelector("#route-status-value").textContent = "Not planned";
}

function cancelPendingRouteSearch() {
  routeRequestSequence += 1;
  const button = document.querySelector("#find-route");
  if (button && button.disabled) {
    button.disabled = false;
    button.innerHTML = '<svg><use href="#driver-icon-search"></use></svg>Find route';
  }
}

function distancePointToSegmentMeters(hazard, first, second) {
  const metersPerLatitude = 111_320;
  const metersPerLongitude = metersPerLatitude * Math.cos(hazard.latitude * Math.PI / 180);
  const x1 = (first[0] - hazard.longitude) * metersPerLongitude;
  const y1 = (first[1] - hazard.latitude) * metersPerLatitude;
  const x2 = (second[0] - hazard.longitude) * metersPerLongitude;
  const y2 = (second[1] - hazard.latitude) * metersPerLatitude;
  const deltaX = x2 - x1;
  const deltaY = y2 - y1;
  const lengthSquared = deltaX * deltaX + deltaY * deltaY;
  const ratio = lengthSquared === 0
    ? 0
    : Math.max(0, Math.min(1, -(x1 * deltaX + y1 * deltaY) / lengthSquared));
  return Math.hypot(x1 + ratio * deltaX, y1 + ratio * deltaY);
}

function getHazardsNearRoute(route) {
  if (
    !routeHazardDataAvailable
    || !Array.isArray(route.geometry?.coordinates)
    || route.geometry.coordinates.length < 2
  ) {
    return [];
  }
  return routeHazards.map((hazard) => {
    let nearestDistance = Infinity;
    const coordinates = route.geometry.coordinates;
    for (let index = 1; index < coordinates.length; index += 1) {
      nearestDistance = Math.min(
        nearestDistance,
        distancePointToSegmentMeters(hazard, coordinates[index - 1], coordinates[index])
      );
    }
    return { ...hazard, route_distance_meters: nearestDistance };
  }).filter((hazard) => hazard.route_distance_meters <= ROUTE_HAZARD_BUFFER_METERS);
}

function getRouteComparison(route, index) {
  const nearby = getHazardsNearRoute(route);
  const riskScore = nearby.reduce(
    (total, hazard) => total + (ROUTE_HAZARD_RISK_POINTS[hazard.hazard_priority] || 2),
    0
  );
  return {
    index,
    hazardCount: nearby.length,
    riskScore,
    distance: route.distance,
    duration: route.duration
  };
}

function chooseRecommendedRoute(comparisons) {
  if (!comparisons.length) {
    return 0;
  }
  return [...comparisons].sort((first, second) => {
    if (routeHazardDataAvailable) {
      return (
        first.riskScore - second.riskScore
        || first.hazardCount - second.hazardCount
        || first.duration - second.duration
        || first.distance - second.distance
      );
    }
    return first.duration - second.duration || first.distance - second.distance;
  })[0].index;
}

function addRouteHazardMarkers(route) {
  if (!map || !routeHazardMarkerLayer) {
    return;
  }
  routeHazardMarkerLayer.clearLayers();
  getHazardsNearRoute(route).forEach((hazard) => {
    const marker = L.circleMarker([hazard.latitude, hazard.longitude], {
      radius: 9,
      color: "#ffffff",
      weight: 2,
      fillColor: getHazardColor(hazard.category),
      fillOpacity: 1
    });
    marker.bindPopup(createHazardPopup({
      ...hazard,
      distance_meters: hazard.route_distance_meters
    }));
    marker.addTo(routeHazardMarkerLayer);
  });
}

function isValidOsrmRoute(route) {
  return Boolean(
    route
    && Number.isFinite(route.distance)
    && route.distance >= 0
    && Number.isFinite(route.duration)
    && route.duration >= 0
    && Array.isArray(route.geometry?.coordinates)
    && route.geometry.coordinates.length >= 2
    && route.geometry.coordinates.every((coordinate) =>
      Array.isArray(coordinate)
      && coordinate.length >= 2
      && Number.isFinite(coordinate[0])
      && coordinate[0] >= -180
      && coordinate[0] <= 180
      && Number.isFinite(coordinate[1])
      && coordinate[1] >= -90
      && coordinate[1] <= 90
    )
  );
}

function renderRouteOptions(routes) {
  const options = document.querySelector("#route-options");
  options.replaceChildren();
  routeComparisons = routes.map((route, index) => getRouteComparison(route, index));
  recommendedRouteIndex = chooseRecommendedRoute(routeComparisons);
  routes.forEach((route, index) => {
    const label = document.createElement("label");
    label.className = `route-option${index === recommendedRouteIndex ? " is-selected" : ""}`;
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "route-choice";
    radio.value = index;
    radio.checked = index === recommendedRouteIndex;
    radio.addEventListener("change", () => selectRoute(index));

    const content = document.createElement("span");
    content.className = "route-option-content";
    const title = document.createElement("strong");
    const routeName = index === 0 ? "Original route" : `Alternate route ${index}`;
    title.textContent = index === recommendedRouteIndex
      ? `${routeName} · suggested`
      : routeName;
    const details = document.createElement("small");
    const hazardNote = routeHazardDataAvailable
      ? `${routeComparisons[index].hazardCount} verified active hazard(s) within ${ROUTE_HAZARD_BUFFER_METERS} m · risk score ${routeComparisons[index].riskScore}`
      : "Verified hazard comparison unavailable";
    details.textContent =
      `${formatRouteDistance(route.distance)} · OSRM estimate ${formatRouteDuration(route.duration)} · ${hazardNote}`;
    content.append(title, details);

    const action = document.createElement("span");
    action.className = "route-option-action";
    action.textContent = index === recommendedRouteIndex
      ? (index === 0 ? "SUGGESTED ORIGINAL" : "SUGGESTED ALTERNATIVE")
      : "SELECT ROUTE";
    label.append(radio, content, action);
    options.append(label);
  });

  document.querySelector("#route-result-note").textContent = routes.length > 1
    ? `${routes.length} options returned by OSRM`
    : "OSRM returned one route; no alternative is available for this request.";
  document.querySelector("#route-results").hidden = false;
  document.querySelector("#route-status-value").textContent =
    routeStatusLabel(recommendedRouteIndex);
  drawRoute(recommendedRouteIndex);
  addRouteHazardMarkers(routes[recommendedRouteIndex]);
}

routeForm.addEventListener("input", () => {
  cancelPendingRouteSearch();
  routeFeedback.hidden = true;
  clearRouteDisplay();
});

routeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const startInput = document.querySelector("#route-start");
  let startValue = startInput.value.trim();
  const destinationValue = document.querySelector("#route-destination").value.trim();

  if (!startValue && currentPosition) {
    startValue = `${currentPosition.latitude.toFixed(6)}, ${currentPosition.longitude.toFixed(6)}`;
    startInput.value = startValue;
  }
  if (!startValue || !destinationValue) {
    showRouteFeedback(
      !startValue
        ? "Start GPS or enter a starting location, then enter a destination."
        : "Enter a destination before finding a route.",
      true
    );
    return;
  }

  const findRouteButton = document.querySelector("#find-route");
  const requestId = ++routeRequestSequence;
  clearRouteDisplay();
  findRouteButton.disabled = true;
  findRouteButton.textContent = "Finding routes…";
  routeFeedback.hidden = true;
  document.querySelector("#route-status-value").textContent = "Searching…";

  try {
    const start = await resolveRouteLocation(startValue);
    if (requestId !== routeRequestSequence) {
      return;
    }
    routeStartCoordinates = start;
    renderRouteEndpointMarkers();
    const destinationIsCoordinates = parseCoordinates(destinationValue);
    if (!destinationIsCoordinates) {
      await wait(1000);
    }
    const destination = await resolveRouteLocation(destinationValue);
    if (requestId !== routeRequestSequence) {
      return;
    }
    routeDestinationCoordinates = destination;
    renderRouteEndpointMarkers();
    const coordinateString = `${start.longitude},${start.latitude};${destination.longitude},${destination.latitude}`;
    const routeUrl = `https://router.project-osrm.org/route/v1/driving/${coordinateString}?alternatives=true&overview=full&geometries=geojson&steps=false`;
    const response = await fetchJson(routeUrl);
    if (requestId !== routeRequestSequence) {
      return;
    }

    if (
      response.code !== "Ok"
      || !Array.isArray(response.routes)
      || !response.routes.length
      || !response.routes.every(isValidOsrmRoute)
    ) {
      throw new Error("OSRM did not return a usable route for those locations. Try another start or destination.");
    }
    try {
      const hazardResult = await apiRequest("/hazards");
      if (requestId !== routeRequestSequence) {
        return;
      }
      routeHazards = validateHazardsResponse(hazardResult, false);
      routeHazardDataAvailable = true;
    } catch (hazardError) {
      if (requestId !== routeRequestSequence) {
        return;
      }
      routeHazards = [];
      routeHazardDataAvailable = false;
      console.error("Could not load hazard data for route comparison:", hazardError);
    }
    currentRoutes = response.routes;
    renderRouteOptions(currentRoutes);
    if (currentRoutes.length === 1) {
      showRouteFeedback(
        routeHazardDataAvailable
          ? "No alternative route is available from OSRM for this request. The single route is shown with known verified hazards; this is not a guarantee of road safety."
          : "No alternative route is available from OSRM, and verified hazard data could not be loaded.",
        !routeHazardDataAvailable
      );
    } else {
      showRouteFeedback(
        routeHazardDataAvailable
          ? "The suggestion prioritizes lower known verified-hazard exposure, then OSRM estimates. It is not guaranteed hazard-free."
          : "Hazard data is unavailable. The suggestion uses OSRM-reported duration and distance only; it is not a safety assessment.",
        !routeHazardDataAvailable
      );
    }
  } catch (error) {
    if (requestId !== routeRequestSequence) {
      return;
    }
    const failedStart = routeStartCoordinates;
    const failedDestination = routeDestinationCoordinates;
    clearRouteDisplay();
    routeStartCoordinates = failedStart;
    routeDestinationCoordinates = failedDestination;
    renderRouteEndpointMarkers();
    document.querySelector("#route-status-value").textContent = "Not available";
    showRouteFeedback(`Could not find a route: ${error.message}`, true);
  } finally {
    if (requestId === routeRequestSequence) {
      findRouteButton.disabled = false;
      findRouteButton.innerHTML = '<svg><use href="#driver-icon-search"></use></svg>Find route';
    }
  }
});

document.querySelector("#use-current-location").addEventListener("click", () => {
  if (!currentPosition) {
    showRouteFeedback("Start GPS tracking first to use your current location as the route start.", true);
    return;
  }
  cancelPendingRouteSearch();
  clearRouteDisplay();
  document.querySelector("#route-start").value =
    `${currentPosition.latitude.toFixed(6)}, ${currentPosition.longitude.toFixed(6)}`;
  routeFeedback.hidden = true;
});

document.querySelector("#choose-map-destination").addEventListener("click", () => {
  if (!map) {
    showRouteFeedback("The map is not available. Enter destination coordinates instead.", true);
    return;
  }
  selectingMapDestination = true;
  showRouteFeedback("Click a point on the map to set the destination.", false);
  document.querySelector("#live-map").scrollIntoView({ behavior: "smooth", block: "start" });
});

// Keep the sidebar accessible on narrow screens.
const driverSidebar = document.querySelector("#driver-sidebar");
const sidebarToggle = document.querySelector(".sidebar-toggle");
const sidebarOverlay = document.querySelector(".sidebar-overlay");

function setSidebarOpen(isOpen) {
  driverSidebar.classList.toggle("is-open", isOpen);
  sidebarOverlay.classList.toggle("is-visible", isOpen);
  sidebarToggle.setAttribute("aria-expanded", String(isOpen));
  sidebarToggle.setAttribute("aria-label", isOpen ? "Close dashboard menu" : "Open dashboard menu");
}

sidebarToggle.addEventListener("click", () => {
  setSidebarOpen(sidebarToggle.getAttribute("aria-expanded") !== "true");
});
sidebarOverlay.addEventListener("click", () => setSidebarOpen(false));

document.querySelectorAll(".dashboard-nav-link[href^='#']").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".dashboard-nav-link[href^='#']").forEach((navLink) => {
      navLink.classList.remove("is-active");
      navLink.removeAttribute("aria-current");
    });
    link.classList.add("is-active");
    link.setAttribute("aria-current", "page");
    setSidebarOpen(false);
    if (link.hash === "#live-map" && map) {
      setTimeout(() => map.invalidateSize(), 200);
    }
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setSidebarOpen(false);
  }
});

initializeMap();
renderNearbyHazards();
renderHistory();
