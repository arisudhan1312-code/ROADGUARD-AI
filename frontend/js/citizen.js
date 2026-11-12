// Citizen reports and status are loaded from the Flask API, not browser storage.
const apiBaseUrl = window.ROADGUARD_API_BASE;
const reportForm = document.querySelector("#road-report-form");
const reportFeedback = document.querySelector("#report-feedback");
const reportsTableBody = document.querySelector("#reports-table-body");
const emptyReports = document.querySelector("#empty-reports");
const imageInput = document.querySelector("#road-image");
const imagePreview = document.querySelector("#image-preview");
const previewImage = document.querySelector("#preview-image");
const previewName = document.querySelector("#preview-name");
const gpsButton = document.querySelector("#gps-button");
const gpsCoordinates = document.querySelector("#gps-coordinates");
const locationInput = document.querySelector("#problem-location");
const detailsDialog = document.querySelector("#report-details-dialog");
const detailsContent = document.querySelector("#report-details-content");

const reports = [];
let reportsAvailable = false;
let imagePreviewUrl = "";
let currentGpsCoordinates = null;

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...options,
      credentials: "include"
    });
  } catch (error) {
    console.error("Could not reach the ROADGUARD AI API:", error);
    throw new Error("Cannot reach the Flask API. Start the backend and open this page through VS Code Live Server.");
  }

  let result;
  try {
    result = await response.json();
  } catch (error) {
    console.error("The API returned an unreadable response:", error);
    throw new Error(`The API returned an unreadable response (HTTP ${response.status}).`);
  }

  if (!response.ok) {
    const message = response.status === 401
      ? "Please log in as a citizen to load or submit reports."
      : (result.error || `The request failed (HTTP ${response.status}).`);
    throw new Error(message);
  }
  return result;
}

function showFeedback(message, isError) {
  reportFeedback.textContent = message;
  reportFeedback.hidden = false;
  reportFeedback.classList.toggle("is-error", isError);
  reportFeedback.focus();
}

function showApiError(message) {
  emptyReports.textContent = message;
  emptyReports.hidden = false;
}

function formatReportDate(value) {
  if (!value) {
    return "Not available";
  }
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function statusClass(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("pending")) {
    return "pending";
  }
  if (normalized.includes("approved") || normalized.includes("progress")) {
    return "approved";
  }
  if (normalized.includes("completed")) {
    return "completed";
  }
  return normalized.replace(/[^a-z0-9]+/g, "-") || "unknown";
}

function createCell(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text || "—";
  if (className) {
    cell.className = className;
  }
  return cell;
}

function renderReports() {
  reportsTableBody.replaceChildren();

  reports.forEach((report) => {
    const row = document.createElement("tr");
    row.append(
      createCell(report.report_id, "report-id-cell"),
      createCell(report.hazard_category),
      createCell(report.location_name),
      createCell(formatReportDate(report.report_datetime)),
      createCell(report.priority, `priority-${String(report.priority || "").toLowerCase()}`)
    );

    const statusCell = document.createElement("td");
    const statusBadge = document.createElement("span");
    statusBadge.className = `report-status status-${statusClass(report.status)}`;
    statusBadge.textContent = report.status;
    statusCell.append(statusBadge);
    row.append(statusCell);

    const detailsCell = document.createElement("td");
    const detailsButton = document.createElement("button");
    detailsButton.className = "report-details-button";
    detailsButton.type = "button";
    detailsButton.dataset.reportId = report.report_id;
    detailsButton.textContent = "View Details";
    detailsCell.append(detailsButton);
    row.append(detailsCell);
    reportsTableBody.append(row);
  });

  emptyReports.textContent = reportsAvailable
    ? "No reports are available yet. Submit a report to get started."
    : "Your reports could not be loaded from Flask.";
  emptyReports.hidden = reports.length > 0;
  document.querySelector("#table-report-count").textContent = reportsAvailable
    ? `${reports.length} ${reports.length === 1 ? "report" : "reports"}`
    : "Unavailable";
  updateOverview();
}

function updateOverview() {
  if (!reportsAvailable) {
    [
      "#total-reports",
      "#pending-reports",
      "#approved-reports",
      "#completed-repairs",
      "#status-pending-count",
      "#status-approved-count",
      "#status-completed-count"
    ].forEach((selector) => {
      document.querySelector(selector).textContent = "—";
    });
    document.querySelector("#status-progress-pending").style.width = "0%";
    document.querySelector("#status-progress-approved").style.width = "0%";
    document.querySelector("#status-progress-completed").style.width = "0%";
    return;
  }

  const counts = reports.reduce((result, report) => {
    const status = String(report.status || "").toLowerCase();
    if (status.includes("pending")) {
      result.pending += 1;
    } else if (status.includes("approved") || status.includes("in progress")) {
      result.approved += 1;
    } else if (status.includes("completed")) {
      result.completed += 1;
    }
    return result;
  }, { pending: 0, approved: 0, completed: 0 });

  document.querySelector("#total-reports").textContent = reports.length;
  document.querySelector("#pending-reports").textContent = counts.pending;
  document.querySelector("#approved-reports").textContent = counts.approved;
  document.querySelector("#completed-repairs").textContent = counts.completed;
  document.querySelector("#status-pending-count").textContent = counts.pending;
  document.querySelector("#status-approved-count").textContent = counts.approved;
  document.querySelector("#status-completed-count").textContent = counts.completed;

  const total = reports.length || 1;
  document.querySelector("#status-progress-pending").style.width = `${(counts.pending / total) * 100}%`;
  document.querySelector("#status-progress-approved").style.width = `${(counts.approved / total) * 100}%`;
  document.querySelector("#status-progress-completed").style.width = `${(counts.completed / total) * 100}%`;
}

async function loadReports() {
  emptyReports.textContent = "Loading your reports…";
  emptyReports.hidden = false;
  try {
    const result = await apiRequest("/reports/my");
    if (!Array.isArray(result.reports)) {
      throw new Error("The API returned an invalid report list.");
    }
    reports.splice(0, reports.length, ...result.reports);
    reportsAvailable = true;
    renderReports();
  } catch (error) {
    reports.splice(0, reports.length);
    reportsAvailable = false;
    renderReports();
    showApiError(error.message);
    console.error("Unable to load citizen reports:", error);
  }
}

function validateReport() {
  const requiredFields = Array.from(reportForm.querySelectorAll("[required]"));
  for (const field of requiredFields) {
    if (!field.value.trim()) {
      const label = field.labels?.[0]?.textContent.replace("*", "").trim().toLowerCase() || "required field";
      return { field, message: `Please complete the ${label} field.` };
    }
  }

  const description = reportForm.querySelector('[name="description"]');
  if (description.value.trim().length < 10 || description.value.trim().length > 2000) {
    return { field: description, message: "Description must be between 10 and 2000 characters." };
  }

  const location = locationInput.value.trim();
  if (location.length > 255) {
    return { field: locationInput, message: "Location must be 255 characters or fewer." };
  }

  const image = imageInput.files[0];
  const imageExtension = image ? image.name.toLowerCase().split(".").pop() : "";
  const imageIsJpeg = image && (image.type === "image/jpeg" || imageExtension === "jpg" || imageExtension === "jpeg");
  const imageIsPng = image && (image.type === "image/png" || imageExtension === "png");
  if (image && !imageIsJpeg && !imageIsPng) {
    return { field: imageInput, message: "Choose a JPG, JPEG, or PNG image." };
  }
  if (image && image.size > 5 * 1024 * 1024) {
    return { field: imageInput, message: "The image must be 5 MB or smaller." };
  }

  return null;
}

function clearImagePreview() {
  if (imagePreviewUrl) {
    URL.revokeObjectURL(imagePreviewUrl);
    imagePreviewUrl = "";
  }
  imageInput.value = "";
  previewImage.removeAttribute("src");
  previewName.textContent = "";
  imagePreview.hidden = true;
}

imageInput.addEventListener("change", () => {
  const file = imageInput.files[0];
  reportFeedback.hidden = true;
  if (!file) {
    clearImagePreview();
    return;
  }

  const imageExtension = file.name.toLowerCase().split(".").pop();
  const imageIsSupported = file.type === "image/jpeg"
    || file.type === "image/png"
    || ["jpg", "jpeg", "png"].includes(imageExtension);
  if (!imageIsSupported) {
    clearImagePreview();
    showFeedback("Choose a JPG, JPEG, or PNG image.", true);
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    clearImagePreview();
    showFeedback("The image must be 5 MB or smaller.", true);
    return;
  }

  if (imagePreviewUrl) {
    URL.revokeObjectURL(imagePreviewUrl);
  }
  imagePreviewUrl = URL.createObjectURL(file);
  previewImage.src = imagePreviewUrl;
  previewName.textContent = file.name;
  imagePreview.hidden = false;
});

document.querySelector("#remove-image").addEventListener("click", clearImagePreview);

function captureGps() {
  if (!navigator.geolocation) {
    return Promise.reject(new Error("Location services are not available in this browser."));
  }

  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const latitude = position.coords.latitude;
        const longitude = position.coords.longitude;
        currentGpsCoordinates = { latitude, longitude };
        const coordinateText = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
        gpsCoordinates.textContent = `Current coordinates: ${coordinateText}`;
        resolve(currentGpsCoordinates);
      },
      (error) => {
        currentGpsCoordinates = null;
        const messages = {
          1: "Location permission was denied. You can still submit with the location name.",
          2: "Your location could not be determined. Check location services or enter the location name.",
          3: "Getting your location timed out. You can retry or submit with the location name."
        };
        reject(new Error(messages[error.code] || "Unable to get your location."));
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 }
    );
  });
}

gpsButton.addEventListener("click", async () => {
  reportFeedback.hidden = true;
  locationInput.removeAttribute("aria-invalid");
  gpsButton.disabled = true;
  gpsButton.querySelector("span").textContent = "Locating…";
  gpsCoordinates.textContent = "Waiting for location permission…";

  try {
    await captureGps();
    gpsButton.querySelector("span").textContent = "Update GPS";
  } catch (error) {
    gpsCoordinates.textContent = error.message;
    showFeedback(error.message, true);
    gpsButton.querySelector("span").textContent = "Try GPS again";
  } finally {
    gpsButton.disabled = false;
  }
});

reportForm.addEventListener("input", (event) => {
  event.target.removeAttribute("aria-invalid");
  reportFeedback.hidden = true;
});

reportForm.addEventListener("change", (event) => {
  event.target.removeAttribute("aria-invalid");
  reportFeedback.hidden = true;
});

reportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  reportForm.querySelectorAll("[aria-invalid='true']").forEach((field) => {
    field.removeAttribute("aria-invalid");
  });

  const validationError = validateReport();
  if (validationError) {
    validationError.field.setAttribute("aria-invalid", "true");
    showFeedback(validationError.message, true);
    validationError.field.focus();
    return;
  }

  const submitButton = reportForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");

  let gpsWarning = "";
  if (!currentGpsCoordinates) {
    gpsCoordinates.textContent = "Requesting current location before submission…";
    try {
      await captureGps();
    } catch (error) {
      gpsWarning = ` GPS was unavailable: ${error.message}`;
      gpsCoordinates.textContent = error.message;
    }
  }

  const formData = new FormData(reportForm);
  if (currentGpsCoordinates) {
    formData.set("latitude", String(currentGpsCoordinates.latitude));
    formData.set("longitude", String(currentGpsCoordinates.longitude));
  }

  try {
    const result = await apiRequest("/reports", {
      method: "POST",
      body: formData
    });
    const reportId = result.report?.report_id;
    if (!reportId) {
      throw new Error("The API accepted the request but did not return a report ID.");
    }

    reportForm.reset();
    clearImagePreview();
    currentGpsCoordinates = null;
    gpsCoordinates.textContent = "GPS coordinates have not been added.";
    gpsButton.querySelector("span").textContent = "Use GPS";
    await loadReports();
    showFeedback(`Report ${reportId} was submitted and is Pending Review.${gpsWarning}`, false);
    window.loadRoadguardNotifications?.();
  } catch (error) {
    showFeedback(error.message, true);
    console.error("Unable to submit citizen report:", error);
  } finally {
    submitButton.disabled = false;
    submitButton.removeAttribute("aria-busy");
  }
});

function addDetailLine(container, label, value) {
  const line = document.createElement("p");
  const labelElement = document.createElement("strong");
  labelElement.textContent = `${label}: `;
  line.append(labelElement, document.createTextNode(value || "Not provided"));
  container.append(line);
}

async function showReportDetails(reportId) {
  detailsContent.replaceChildren();
  const loading = document.createElement("p");
  loading.textContent = "Loading report details…";
  detailsContent.append(loading);
  detailsDialog.showModal();

  try {
    const result = await apiRequest(`/reports/${encodeURIComponent(reportId)}`);
    const report = result.report;
    detailsContent.replaceChildren();
    addDetailLine(detailsContent, "Report ID", report.report_id);
    addDetailLine(detailsContent, "Hazard type", report.hazard_category);
    addDetailLine(detailsContent, "Location", report.location_name);
    addDetailLine(detailsContent, "Description", report.problem_description);
    addDetailLine(detailsContent, "GPS coordinates",
      report.latitude === null || report.longitude === null
        ? "Not provided"
        : `${report.latitude}, ${report.longitude}`);
    addDetailLine(detailsContent, "Reported", formatReportDate(report.report_datetime));
    addDetailLine(detailsContent, "Priority", report.priority);
    addDetailLine(detailsContent, "Status", report.status);

    if (report.image_url) {
      const image = document.createElement("img");
      image.className = "report-details-image";
      image.alt = `Uploaded image for report ${report.report_id}`;
      image.src = new URL(report.image_url, apiOrigin).href;
      detailsContent.append(image);
    }
  } catch (error) {
    detailsContent.replaceChildren();
    const message = document.createElement("p");
    message.textContent = error.message;
    detailsContent.append(message);
    console.error("Unable to load report details:", error);
  }
}

reportsTableBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-report-id]");
  if (button) {
    showReportDetails(button.dataset.reportId);
  }
});

document.querySelector("#close-report-details").addEventListener("click", () => {
  detailsDialog.close();
});

detailsDialog.addEventListener("click", (event) => {
  if (event.target === detailsDialog) {
    detailsDialog.close();
  }
});

// Keep the sidebar usable on small screens.
const dashboardSidebar = document.querySelector("#dashboard-sidebar");
const sidebarToggle = document.querySelector(".sidebar-toggle");
const sidebarOverlay = document.querySelector(".sidebar-overlay");

function setSidebarOpen(isOpen) {
  dashboardSidebar.classList.toggle("is-open", isOpen);
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
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setSidebarOpen(false);
  }
});

loadReports();
