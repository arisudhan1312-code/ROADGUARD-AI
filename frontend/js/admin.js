// Admin reports, hazards, worker allocation, verification, activities, and points use Flask APIs.
const adminApiBase = window.ROADGUARD_API_BASE;
const SEVERITIES = ["High", "Medium", "Low"];
const HAZARD_PRIORITIES = ["Critical", "High", "Medium", "Low"];
const TRAFFIC_IMPORTANCE = ["Unknown", "Low", "Medium", "High"];
const REPORT_STATUSES = ["Pending Review", "Approved", "Rejected", "Completed"];

const adminFeedback = document.querySelector("#admin-feedback");
const storageStatus = document.querySelector("#admin-storage-status");
const reportTableBody = document.querySelector("#report-table-body");
const hazardTableBody = document.querySelector("#hazard-table-body");
const workerTableBody = document.querySelector("#worker-table-body");
const activityTableBody = document.querySelector("#activity-table-body");
const rewardsTableBody = document.querySelector("#rewards-table-body");
const verificationList = document.querySelector("#verification-list");
const reportDialog = document.querySelector("#admin-report-dialog");
const reportDialogContent = document.querySelector("#report-dialog-content");
const mapError = document.querySelector("#admin-map-error");
const analyticsWarning = document.querySelector("#analytics-library-warning");

const state = {
  reports: [],
  hazards: [],
  workers: [],
  tasks: [],
  assignableHazards: [],
  evidence: [],
  activities: [],
  rewards: [],
  availability: {}
};
let adminMap = null;
let hazardMarkerLayer = null;
const charts = {};

async function adminApiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${adminApiBase}${path}`, {
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
    console.error("The admin API returned unreadable JSON:", error);
    throw new Error(`The API returned an unreadable response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(response.status === 401
      ? "Please log in as an administrator."
      : (result.error || `The request failed (HTTP ${response.status}).`));
  }
  return result;
}

function showAdminFeedback(message, isError) {
  adminFeedback.textContent = message;
  adminFeedback.hidden = false;
  adminFeedback.classList.toggle("is-error", isError);
}

function showInlineFeedback(element, message, isError) {
  element.textContent = message;
  element.hidden = false;
  element.classList.toggle("is-error", isError);
}

function createElement(tag, className = "", text) {
  const element = document.createElement(tag);
  if (className) {
    element.className = className;
  }
  if (text !== undefined) {
    element.textContent = text;
  }
  return element;
}

function createButton(label, action, id, styleClass, disabled = false) {
  const button = createElement("button", `button ${styleClass}`, label);
  button.type = "button";
  button.dataset.action = action;
  button.dataset.id = String(id);
  button.disabled = disabled;
  return button;
}

function createStatusBadge(status) {
  const normalized = String(status || "Unknown").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return createElement("span", `admin-status-badge status-${normalized}`, status || "Unknown");
}

function formatCoordinates(latitude, longitude, digits = 5) {
  if (!Number.isFinite(Number(latitude)) || !Number.isFinite(Number(longitude))) {
    return "Not provided";
  }
  return `${Number(latitude).toFixed(digits)}, ${Number(longitude).toFixed(digits)}`;
}

function formatDate(value) {
  if (!value) {
    return "Not provided";
  }
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function addTableCell(row, value, className = "") {
  row.append(createElement("td", className, value || "—"));
}

function filteredReports() {
  const query = document.querySelector("#report-search").value.trim().toLowerCase();
  const status = document.querySelector("#report-status-filter").value;
  return state.reports.filter((report) => {
    const matchesStatus = status === "all" || report.status === status;
    const searchable = [
      report.report_id,
      report.citizen_name,
      report.hazard_category,
      report.location_name,
      report.ai_hazard_category,
      report.ai_detection_status
    ].join(" ").toLowerCase();
    return matchesStatus && searchable.includes(query);
  });
}

function renderReports() {
  const reports = filteredReports();
  reportTableBody.replaceChildren();
  reports.forEach((report) => {
    const row = document.createElement("tr");
    addTableCell(row, report.report_id, "report-id-cell");
    addTableCell(row, report.citizen_name);
    addTableCell(row, report.hazard_category);
    addTableCell(row, report.location_name);
    addTableCell(row, formatCoordinates(report.latitude, report.longitude), "admin-coordinate");
    addTableCell(row, formatDate(report.report_datetime));

    const imageCell = document.createElement("td");
    const imageButton = createButton(
      report.image_url ? "View image" : "No image",
      report.image_url ? "view-image" : "view-report",
      report.report_id,
      "button-outline"
    );
    imageButton.disabled = !report.image_url;
    imageCell.append(imageButton);
    row.append(imageCell);

    const aiCell = document.createElement("td");
    const aiSummary = report.ai_detection_status
      ? `${report.ai_hazard_category || report.ai_detection_status} · ${report.ai_detection_count ?? "—"} object(s)`
      : "No linked AI analysis";
    aiCell.append(createElement("span", "admin-ai-label", aiSummary));
    if (report.ai_detection_id) {
      aiCell.append(createButton("View AI result", "view-ai-result", report.report_id, "button-outline"));
    }
    row.append(aiCell);
    const severityCell = document.createElement("td");
    severityCell.append(createElement(
      "span",
      `admin-severity admin-severity-${String(report.severity || "").toLowerCase()}`,
      report.severity || "Not assessed"
    ));
    row.append(severityCell);

    const statusCell = document.createElement("td");
    statusCell.append(createStatusBadge(report.status));
    const priorityBadge = createElement(
      "span",
      `admin-priority-badge admin-priority-${String(report.hazard_priority).toLowerCase()}`,
      `${report.hazard_priority} · ${report.priority_score}/100`
    );
    priorityBadge.title = report.priority_explanation;
    statusCell.append(priorityBadge);
    row.append(statusCell);

    const actionCell = document.createElement("td");
    const actions = createElement("div", "admin-action-group");
    actions.append(createButton("View Report", "view-report", report.report_id, "button-outline"));
    if (report.status !== "Rejected") {
      const prioritySelect = document.createElement("select");
      prioritySelect.className = "admin-priority-select";
      prioritySelect.dataset.reportPriority = report.report_id;
      prioritySelect.setAttribute("aria-label", `Priority for report ${report.report_id}`);
      [["auto", "Rule-based"], ...HAZARD_PRIORITIES.map((value) => [value, value])]
        .forEach(([value, label]) => {
          const option = new Option(label, value);
          option.selected = value === (
            report.priority_source === "Admin Override"
              ? report.hazard_priority
              : "auto"
          );
          prioritySelect.append(option);
        });
      const trafficSelect = document.createElement("select");
      trafficSelect.className = "admin-priority-select";
      trafficSelect.dataset.reportTraffic = report.report_id;
      trafficSelect.setAttribute("aria-label", `Traffic importance for report ${report.report_id}`);
      TRAFFIC_IMPORTANCE.forEach((value) => {
        const option = new Option(`Traffic: ${value}`, value);
        option.selected = value === report.traffic_importance;
        trafficSelect.append(option);
      });
      actions.append(prioritySelect, trafficSelect);
      actions.append(createButton("Save priority", "update-priority", report.report_id, "button-outline"));
    }
    if (report.status === "Pending Review") {
      const severitySelect = document.createElement("select");
      severitySelect.className = "admin-priority-select";
      severitySelect.dataset.reportSeverity = report.report_id;
      severitySelect.setAttribute("aria-label", `Severity for report ${report.report_id}`);
      SEVERITIES.forEach((severity) => {
        const option = new Option(severity, severity);
        option.selected = report.priority === severity;
        severitySelect.append(option);
      });
      actions.append(severitySelect);
      actions.append(createButton("Approve", "approve-report", report.report_id, "button-green"));
      actions.append(createButton("Reject", "reject-report", report.report_id, "button-outline admin-button-danger"));
    } else {
      actions.append(createButton("Approve", "approve-report", report.report_id, "button-green", true));
      actions.append(createButton("Reject", "reject-report", report.report_id, "button-outline admin-button-danger", true));
    }
    if (report.status === "Approved") {
      const hazard = state.hazards.find((item) => item.report_id === report.report_id);
      if (hazard && hazard.status === "Active") {
        actions.append(createButton("Allocate", "open-allocation", report.report_id, "button-outline"));
      }
    }
    actionCell.append(actions);
    row.append(actionCell);
    reportTableBody.append(row);
  });
  document.querySelector("#empty-reports").hidden = reports.length > 0;
  document.querySelector("#empty-reports").textContent = state.availability.reports
    ? "No reports match your search."
    : "Report data could not be loaded from Flask.";
  document.querySelector("#report-table-count").textContent = state.availability.reports
    ? `${state.reports.length} ${state.reports.length === 1 ? "report" : "reports"}`
    : "Unavailable";
  document.querySelector("#pending-report-nav").textContent = state.availability.reports
    ? state.reports.filter((report) => report.status === "Pending Review").length
    : "—";
}

function getVisibleHazards() {
  const category = document.querySelector("#hazard-category-filter").value;
  const severity = document.querySelector("#hazard-severity-filter").value;
  const status = document.querySelector("#hazard-status-filter").value;
  return state.hazards.filter((hazard) =>
    (category === "all" || hazard.category === category)
    && (severity === "all" || hazard.severity === severity)
    && (status === "all" || hazard.status === status)
  );
}

function hazardColor(severity) {
  return { High: "#ed5965", Medium: "#e9b65b", Low: "#62bbd6" }[severity] || "#62bbd6";
}

function renderHazards() {
  const hazards = getVisibleHazards();
  hazardTableBody.replaceChildren();
  hazards.forEach((hazard) => {
    const row = document.createElement("tr");
    addTableCell(row, hazard.hazard_id, "report-id-cell");
    addTableCell(row, hazard.category);
    addTableCell(row, hazard.location_name);
    addTableCell(row, formatCoordinates(hazard.latitude, hazard.longitude), "admin-coordinate");
    const priorityCell = document.createElement("td");
    priorityCell.append(createElement(
      "span",
      `admin-severity admin-severity-${String(hazard.severity).toLowerCase()}`,
      `${hazard.severity} severity`
    ));
    const priorityBadge = createElement(
      "span",
      `admin-priority-badge admin-priority-${String(hazard.hazard_priority).toLowerCase()}`,
      `${hazard.hazard_priority} · ${hazard.priority_score}/100`
    );
    priorityBadge.title = hazard.priority_explanation;
    priorityCell.append(priorityBadge);
    row.append(priorityCell);
    const statusCell = document.createElement("td");
    statusCell.append(createStatusBadge(hazard.status));
    row.append(statusCell);
    addTableCell(row, hazard.worker_name);
    hazardTableBody.append(row);
  });
  document.querySelector("#empty-hazards").hidden = hazards.length > 0;
  document.querySelector("#empty-hazards").textContent = state.availability.hazards
    ? "No hazards match the selected filters."
    : "Hazard data could not be loaded from Flask.";
  document.querySelector("#visible-hazard-count").textContent =
    state.availability.hazards ? hazards.length : "—";

  if (!hazardMarkerLayer || typeof L === "undefined") {
    return;
  }
  hazardMarkerLayer.clearLayers();
  hazards.forEach((hazard) => {
    if (!Number.isFinite(Number(hazard.latitude)) || !Number.isFinite(Number(hazard.longitude))) {
      return;
    }
    const marker = L.circleMarker([Number(hazard.latitude), Number(hazard.longitude)], {
      radius: hazard.severity === "High" ? 9 : 7,
      color: "#f0f7f8",
      weight: 2,
      fillColor: hazard.status === "Resolved" ? "#748995" : hazardColor(hazard.severity),
      fillOpacity: 0.95
    });
    const popup = L.DomUtil.create("div", "admin-hazard-popup");
    const title = L.DomUtil.create("strong", "", popup);
    title.textContent = hazard.category;
    const location = L.DomUtil.create("p", "", popup);
    location.textContent = hazard.location_name;
    const status = L.DomUtil.create("small", "", popup);
    status.textContent = `${hazard.hazard_priority} priority · ${hazard.severity} severity · ${hazard.status}`;
    marker.bindPopup(popup).addTo(hazardMarkerLayer);
  });
}

function initializeHazardMap() {
  if (typeof L === "undefined") {
    mapError.textContent = "Leaflet could not be loaded. Check your internet connection to view the map.";
    mapError.hidden = false;
    return;
  }
  adminMap = L.map("admin-hazard-map", { scrollWheelZoom: false }).setView([20, 0], 2);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>'
  }).addTo(adminMap);
  hazardMarkerLayer = L.layerGroup().addTo(adminMap);
}

function renderWorkers() {
  const query = document.querySelector("#worker-search").value.trim().toLowerCase();
  const workers = state.workers.filter((worker) =>
    [worker.worker_code, worker.name, worker.location_name, ...(worker.skills || [])]
      .some((value) => String(value).toLowerCase().includes(query))
  );
  workerTableBody.replaceChildren();
  workers.forEach((worker) => {
    const row = document.createElement("tr");
    addTableCell(row, worker.worker_code, "report-id-cell");
    addTableCell(row, worker.name, "admin-worker-name");
    addTableCell(row, worker.skills.join(", ") || "Skills not configured", "admin-skills");
    addTableCell(row, `${worker.current_workload} open task${worker.current_workload === 1 ? "" : "s"}`);
    const availability = document.createElement("td");
    availability.append(createElement(
      "span",
      `admin-worker-availability admin-worker-availability-${worker.availability.toLowerCase().replace(/\s+/g, "-")}`,
      worker.availability
    ));
    row.append(availability);
    addTableCell(row, worker.location_name);
    const action = document.createElement("td");
    action.append(
      createButton("Update profile", "configure-worker", worker.worker_id, "button-outline"),
      createButton("Choose hazard", "focus-allocation", "", "button-outline")
    );
    row.append(action);
    workerTableBody.append(row);
  });
  document.querySelector("#empty-workers").hidden = workers.length > 0;
  document.querySelector("#empty-workers").textContent = state.availability.workers
    ? "No active worker profiles are available in the database."
    : "Worker profiles could not be loaded from Flask.";
  document.querySelector("#worker-count").textContent = state.availability.workers
    ? `${state.workers.length} ${state.workers.length === 1 ? "worker" : "workers"}`
    : "Unavailable";
}

function renderAllocationHazards() {
  const select = document.querySelector("#allocation-hazard");
  const selected = select.value;
  const options = [new Option("Select an active, verified hazard", "")];
  state.assignableHazards.forEach((hazard) => {
    options.push(new Option(
      `${hazard.hazard_id} · ${hazard.category} · ${hazard.severity}`,
      hazard.hazard_id
    ));
  });
  select.replaceChildren(...options);
  if (state.assignableHazards.some((hazard) => hazard.hazard_id === selected)) {
    select.value = selected;
  }
  const emptyHazards = document.querySelector("#empty-allocation-hazards");
  emptyHazards.hidden = state.assignableHazards.length > 0;
  emptyHazards.textContent = state.availability.assignable_hazards
    ? "No active, verified hazards are available for assignment."
    : "Assignable hazards could not be loaded from Flask.";
}

async function renderAllocationCandidates(hazard) {
  const results = document.querySelector("#allocation-results");
  const feedback = document.querySelector("#allocation-feedback");
  results.replaceChildren();
  if (!hazard) {
    results.hidden = true;
    return;
  }
  showInlineFeedback(feedback, `Calculating recommendations for ${hazard.hazard_id}…`, false);
  try {
    const result = await adminApiRequest(
      `/admin/workers?hazard_id=${encodeURIComponent(hazard.hazard_id)}`
    );
    const workers = Array.isArray(result.workers) ? result.workers : [];
    if (!workers.some((worker) => worker.available)) {
      results.hidden = true;
      showInlineFeedback(feedback, "No worker is currently marked Available.", true);
      return;
    }
    workers.forEach((worker, index) => {
      const recommended = worker.worker_id === result.recommended_worker_id;
      const card = createElement("article", `allocation-candidate${recommended ? " is-recommended" : ""}`);
      card.append(createElement("span", "allocation-rank", String(index + 1)));
      const info = createElement("div", "allocation-candidate-info");
      info.append(createElement("strong", "", `${worker.name} · ${worker.worker_code}`));
      info.append(createElement("small", "", worker.recommendation_reason || worker.availability));
      const score = createElement("span", "allocation-score", String(worker.score));
      score.append(createElement("small", "", "rule score / 100"));
      card.append(info, score);
      const assign = createButton(
        worker.available ? "Assign Worker" : "Unavailable",
        "assign-worker",
        worker.worker_id,
        worker.available ? "button-green allocation-assign-button" : "button-outline allocation-assign-button",
        !worker.available
      );
      assign.dataset.hazardId = hazard.hazard_id;
      card.append(assign);
      results.append(card);
    });
    results.hidden = false;
    showInlineFeedback(
      feedback,
      "Recommendations use a transparent rule score, not a trained AI model. The server recalculates before assignment.",
      false
    );
  } catch (error) {
    results.hidden = true;
    showInlineFeedback(feedback, error.message, true);
  }
}

function renderActivities() {
  activityTableBody.replaceChildren();
  state.activities.forEach((activity) => {
    const row = document.createElement("tr");
    const title = document.createElement("td");
    title.append(
      createElement("strong", "admin-worker-name", activity.name),
      createElement("small", "admin-record-subtext", activity.activity_id)
    );
    row.append(title);
    const when = document.createElement("td");
    when.append(
      createElement("span", "", activity.location_name),
      createElement("small", "admin-record-subtext", activity.activity_date)
    );
    row.append(when);
    addTableCell(row, `${activity.participant_count} / ${activity.required_volunteers}`);

    const participantCell = createElement("td", "registered-volunteers");
    const pending = activity.participants.filter((participant) => participant.status === "Submitted");
    if (!activity.participants.length) {
      participantCell.textContent = "No registrations";
    } else {
      activity.participants.forEach((participant) => {
        const item = createElement(
          "span",
          "admin-registered-person",
          `${participant.worker_name} · ${participant.status}`
        );
        if (participant.completion_details) {
          item.title = participant.completion_details;
        }
        participantCell.append(item);
      });
    }
    row.append(participantCell);
    const statusCell = document.createElement("td");
    statusCell.append(createStatusBadge(activity.status));
    row.append(statusCell);
    const actions = document.createElement("td");
    pending.forEach((participant) => {
      actions.append(
        createButton("Approve", "approve-activity", participant.participant_id, "button-green"),
        createButton("Reject", "reject-activity", participant.participant_id, "button-outline admin-button-danger")
      );
    });
    if (!pending.length) {
      actions.textContent = "—";
    }
    row.append(actions);
    activityTableBody.append(row);
  });
  document.querySelector("#empty-activities").hidden = state.activities.length > 0;
  document.querySelector("#empty-activities").textContent = state.availability.activities
    ? "No volunteer activities have been created."
    : "Volunteer activities could not be loaded from Flask.";
  document.querySelector("#activity-count").textContent = state.availability.activities
    ? `${state.activities.length} ${state.activities.length === 1 ? "activity" : "activities"}`
    : "Unavailable";
}

function createEvidenceImage(label, imageUrl) {
  const figure = createElement("figure", "verification-image");
  if (imageUrl) {
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = `${label} repair evidence`;
    figure.append(image);
  } else {
    figure.append(createElement("div", "verification-image-placeholder", "No photo attached"));
  }
  figure.append(createElement("figcaption", "", label));
  return figure;
}

function renderVerification() {
  verificationList.replaceChildren();
  state.evidence.forEach((item) => {
    const card = createElement("article", "admin-verification-card");
    const header = createElement("div", "verification-card-header");
    const title = createElement("div");
    title.append(createElement("h3", "", `${item.task_id} · ${item.category}`));
    title.append(createElement("p", "", `${item.worker_name} · ${item.location_name}`));
    header.append(title, createStatusBadge("Awaiting Admin Verification"));
    card.append(header);

    const meta = createElement("div", "verification-meta");
    [
      ["Hazard location", item.location_name],
      ["Completion GPS", formatCoordinates(item.latitude, item.longitude, 6)],
      ["Completion date and time", formatDate(item.completion_datetime)],
      ["Priority", item.priority],
      ["Repair description", item.repair_description]
    ].forEach(([label, value]) => {
      const entry = createElement("div");
      entry.append(createElement("span", "", label), createElement("strong", "", value || "Not provided"));
      meta.append(entry);
    });
    card.append(meta);

    const photos = createElement("div", "verification-images");
    photos.append(
      createEvidenceImage("Before repair", item.before_image_url),
      createEvidenceImage("After repair", item.after_image_url)
    );
    card.append(photos);
    const actions = createElement("div", "verification-actions");
    actions.append(
      createButton("Approve Repair", "approve-repair", item.evidence_id, "button-green"),
      createButton("Reject Repair", "reject-repair", item.evidence_id, "button-outline admin-button-danger"),
      createButton("Request More Evidence", "request-evidence", item.evidence_id, "button-outline")
    );
    card.append(actions);
    verificationList.append(card);
  });
  document.querySelector("#empty-verifications").hidden = state.evidence.length > 0;
  document.querySelector("#empty-verifications").textContent = state.availability.evidence
    ? "No repair submissions are awaiting verification. Workers can submit completion evidence from the Worker Dashboard."
    : "Repair evidence could not be loaded from Flask.";
  document.querySelector("#verification-count").textContent = state.availability.evidence
    ? `${state.evidence.length} awaiting review`
    : "Unavailable";
  document.querySelector("#verification-nav-count").textContent = state.availability.evidence
    ? state.evidence.length
    : "—";
}

function renderRewards() {
  rewardsTableBody.replaceChildren();
  state.rewards.forEach((worker) => {
    const row = document.createElement("tr");
    addTableCell(row, `${worker.worker_name} · RGW-${worker.worker_id}`, "admin-worker-name");
    addTableCell(row, worker.completed_tasks);
    addTableCell(row, worker.verified_repairs);
    const activityCount = state.activities.reduce(
      (count, activity) => count + activity.participants.filter(
        (participant) => participant.worker_id === worker.worker_id && participant.status === "Approved"
      ).length,
      0
    );
    addTableCell(row, activityCount);
    addTableCell(row, worker.repair_points, "reward-number");
    addTableCell(row, worker.volunteer_points, "reward-volunteer-number");
    addTableCell(row, worker.total_points, "admin-worker-name");
    rewardsTableBody.append(row);
  });
  document.querySelector("#empty-rewards").hidden = state.rewards.length > 0;
  document.querySelector("#empty-rewards").textContent = state.availability.rewards
    ? "No worker reward records are available."
    : "Worker reward totals could not be loaded from Flask.";
}

function renderStats() {
  const activeHazards = state.hazards.filter((hazard) => ["Active", "Assigned"].includes(hazard.status));
  document.querySelector("#stat-total-reports").textContent = state.reports.length;
  document.querySelector("#stat-pending-reports").textContent =
    state.reports.filter((report) => report.status === "Pending Review").length;
  document.querySelector("#stat-approved-reports").textContent =
    state.reports.filter((report) => ["Approved", "Completed"].includes(report.status)).length;
  document.querySelector("#stat-high-hazards").textContent =
    activeHazards.filter((hazard) => ["Critical", "High"].includes(hazard.hazard_priority)).length;
  document.querySelector("#stat-active-workers").textContent =
    state.workers.filter((worker) => worker.availability !== "Off Duty").length;
  document.querySelector("#stat-completed-repairs").textContent =
    state.tasks.filter((task) => task.status === "Verified").length;
  document.querySelector("#stat-awaiting-verification").textContent = state.evidence.length;
  document.querySelector("#stat-active-activities").textContent =
    state.activities.filter((activity) => activity.status === "Open").length;
  document.querySelector("#pending-report-nav").textContent =
    state.reports.filter((report) => report.status === "Pending Review").length;
}

function markStatsUnavailable() {
  [
    "#stat-total-reports",
    "#stat-pending-reports",
    "#stat-approved-reports",
    "#stat-high-hazards",
    "#stat-active-workers",
    "#stat-completed-repairs",
    "#stat-awaiting-verification",
    "#stat-active-activities",
    "#pending-report-nav",
    "#verification-nav-count"
  ].forEach((selector) => {
    document.querySelector(selector).textContent = "—";
  });
}

function updateChart(key, canvasId, config) {
  const canvas = document.querySelector(`#${canvasId}`);
  if (!canvas || typeof Chart === "undefined") {
    analyticsWarning.hidden = false;
    return;
  }
  analyticsWarning.hidden = true;
  if (charts[key]) {
    charts[key].destroy();
  }
  charts[key] = new Chart(canvas, config);
}

function renderCharts() {
  if (typeof Chart === "undefined") {
    analyticsWarning.hidden = false;
    return;
  }
  const categories = ["Pothole", "Damaged Road", "Broken Streetlight", "Waterlogging", "Road Obstruction", "Other"];
  const priorities = ["Critical", ...SEVERITIES];
  const activeHazards = state.hazards.filter((hazard) => ["Active", "Assigned"].includes(hazard.status));
  const baseOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#a9bbc2", boxWidth: 9, boxHeight: 9 } },
      tooltip: { backgroundColor: "#061521", titleColor: "#edf4f5", bodyColor: "#c5d3d8" }
    },
    scales: {
      x: { ticks: { color: "#8da4ae" }, grid: { color: "rgba(160,194,206,.06)" } },
      y: { beginAtZero: true, ticks: { precision: 0, color: "#8da4ae" }, grid: { color: "rgba(160,194,206,.07)" } }
    }
  };
  const colors = ["#35df91", "#f0bf67", "#73c9ed", "#b89aff", "#f1767f", "#62bbd6"];
  updateChart("category", "chart-hazard-category", {
    type: "doughnut",
    data: { labels: categories, datasets: [{ data: categories.map((category) => state.hazards.filter((hazard) => hazard.category === category).length), backgroundColor: colors, borderColor: "#0b2030", borderWidth: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: baseOptions.plugins }
  });
  updateChart("reports", "chart-report-status", {
    type: "bar",
    data: { labels: REPORT_STATUSES, datasets: [{ label: "Reports", data: REPORT_STATUSES.map((status) => state.reports.filter((report) => report.status === status).length), backgroundColor: ["#e6b149", "#35df91", "#f1767f", "#73c9ed"], borderRadius: 5 }] },
    options: baseOptions
  });

  const months = [];
  const monthCounts = [];
  for (let offset = 5; offset >= 0; offset -= 1) {
    const date = new Date();
    date.setDate(1);
    date.setMonth(date.getMonth() - offset);
    months.push(date.toLocaleString(undefined, { month: "short" }));
    monthCounts.push(state.tasks.filter((task) => {
      if (task.status !== "Verified" || !task.updated_at) {
        return false;
      }
      const updated = new Date(task.updated_at.replace(" ", "T"));
      return updated.getFullYear() === date.getFullYear() && updated.getMonth() === date.getMonth();
    }).length);
  }
  updateChart("monthly", "chart-monthly-repairs", {
    type: "line",
    data: { labels: months, datasets: [{ label: "Verified repairs", data: monthCounts, borderColor: "#35df91", backgroundColor: "rgba(53,223,145,.1)", fill: true, tension: .35, pointRadius: 3 }] },
    options: baseOptions
  });
  updateChart("priority", "chart-priority", {
    type: "doughnut",
    data: { labels: priorities, datasets: [{ data: priorities.map((priority) => activeHazards.filter((hazard) => hazard.hazard_priority === priority).length), backgroundColor: ["#ed5965", "#f0a75c", "#e9b65b", "#62bbd6"], borderColor: "#0b2030", borderWidth: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: baseOptions.plugins }
  });
  updateChart("workers", "chart-worker-performance", {
    type: "bar",
    data: {
      labels: state.workers.map((worker) => worker.name),
      datasets: [
        { label: "Verified repairs", data: state.workers.map((worker) => state.tasks.filter((task) => task.worker_id === worker.worker_id && task.status === "Verified").length), backgroundColor: "#35df91", borderRadius: 4 },
        { label: "Approved volunteer activities", data: state.workers.map((worker) => state.activities.reduce((count, activity) => count + activity.participants.filter((participant) => participant.worker_id === worker.worker_id && participant.status === "Approved").length, 0)), backgroundColor: "#a987f5", borderRadius: 4 }
      ]
    },
    options: { ...baseOptions, indexAxis: "y" }
  });
}

function renderDashboard() {
  renderStats();
  renderReports();
  renderHazards();
  renderWorkers();
  renderAllocationHazards();
  renderActivities();
  renderVerification();
  renderRewards();
  renderCharts();
}

async function refreshDashboard() {
  storageStatus.textContent = "Loading database records…";
  const requests = await Promise.allSettled([
    adminApiRequest("/admin/reports"),
    adminApiRequest("/admin/hazards"),
    adminApiRequest("/admin/workers"),
    adminApiRequest("/admin/repair-tasks"),
    adminApiRequest("/admin/verification"),
    adminApiRequest("/admin/activities"),
    adminApiRequest("/admin/rewards")
  ]);
  const failures = [];
  const loadArray = (index, key, responseKey) => {
    const result = requests[index];
    if (result.status !== "fulfilled" || !Array.isArray(result.value[responseKey])) {
      state[key] = [];
      state.availability[key] = false;
      failures.push(result.status === "rejected"
        ? result.reason.message
        : `The ${responseKey} API returned an invalid response.`);
      return null;
    }
    state[key] = result.value[responseKey];
    state.availability[key] = true;
    return result.value;
  };
  loadArray(0, "reports", "reports");
  loadArray(1, "hazards", "hazards");
  loadArray(2, "workers", "workers");
  const taskResult = loadArray(3, "tasks", "repair_tasks");
  if (taskResult && Array.isArray(taskResult.assignable_hazards)) {
    state.assignableHazards = taskResult.assignable_hazards;
    state.availability.assignable_hazards = true;
  } else {
    state.assignableHazards = [];
    state.availability.assignable_hazards = false;
    if (taskResult) {
      failures.push("The assignable_hazards API returned an invalid list.");
    }
  }
  loadArray(4, "evidence", "evidence");
  const activityResult = loadArray(5, "activities", "activities");
  if (activityResult) {
    state.activities.forEach((activity) => {
      if (!Array.isArray(activity.participants)) {
        activity.participants = [];
      }
    });
  }
  loadArray(6, "rewards", "workers");
  renderDashboard();
  storageStatus.textContent = failures.length ? "Some API sections unavailable" : "Live API data";
  if (failures.length) {
    markStatsUnavailable();
    showAdminFeedback([...new Set(failures)].join(" "), true);
  } else {
    adminFeedback.hidden = true;
  }
  document.querySelector("#repair-task-summary").textContent =
    state.availability.tasks
      ? `${state.tasks.length} repair task${state.tasks.length === 1 ? "" : "s"} loaded from the database.`
      : "Repair tasks could not be loaded from Flask.";
  return failures.length === 0;
}

async function refreshAfterAction(message) {
  const refreshed = await refreshDashboard();
  if (refreshed) {
    showAdminFeedback(message, false);
  }
  window.loadRoadguardNotifications?.();
}

function showReportDetails(report, showImage) {
  document.querySelector("#report-dialog-title").textContent =
    `${report.report_id} · ${report.hazard_category}`;
  reportDialogContent.replaceChildren();
  const details = createElement("div", "admin-dialog-details");
  [
    ["Citizen", report.citizen_name],
    ["Location", report.location_name],
    ["GPS coordinates", formatCoordinates(report.latitude, report.longitude, 6)],
    ["Reported", formatDate(report.report_datetime)],
    ["Priority", report.priority],
    ["Hazard priority", report.hazard_priority],
    ["Calculated recommendation", report.calculated_priority],
    ["Priority score", report.priority_score === null ? null : `${report.priority_score}/100`],
    ["Priority source", report.priority_source],
    ["Traffic importance", report.traffic_importance],
    ["Why this priority", report.priority_explanation],
    ["Status", report.status],
    ["AI detection status", report.ai_detection_status || "No linked AI analysis"],
    ["AI category", report.ai_hazard_category],
    ["AI detection count", report.ai_detection_count],
    ["AI pothole count", report.ai_pothole_count],
    ["AI confidence", report.ai_confidence_score === null || report.ai_confidence_score === undefined
      ? null
      : `${(Number(report.ai_confidence_score) * 100).toFixed(1)}%`],
    ["AI bounding boxes", Array.isArray(report.ai_bounding_boxes)
      ? JSON.stringify(report.ai_bounding_boxes)
      : null],
    ["Description", report.problem_description]
  ].forEach(([label, value]) => {
    const item = createElement("div", "admin-dialog-detail");
    item.append(createElement("span", "", label), createElement("strong", "", value || "Not provided"));
    details.append(item);
  });
  reportDialogContent.append(details);
  if (showImage && report.image_url) {
    const image = document.createElement("img");
    image.className = "admin-dialog-photo";
    image.src = report.image_url;
    image.alt = `Uploaded image for report ${report.report_id}`;
    reportDialogContent.append(image);
  }
  if (report.ai_detection_id && report.ai_original_image_url) {
    const original = document.createElement("img");
    original.className = "admin-dialog-photo";
    original.src = report.ai_original_image_url;
    original.alt = `Original AI analysis image for report ${report.report_id}`;
    reportDialogContent.append(original);
  }
  if (report.ai_detection_id && report.ai_annotated_image_url) {
    const annotated = document.createElement("img");
    annotated.className = "admin-dialog-photo";
    annotated.src = report.ai_annotated_image_url;
    annotated.alt = `Unverified model annotations for report ${report.report_id}`;
    reportDialogContent.append(annotated);
  }
  reportDialog.showModal();
}

async function approveReport(report) {
  const severity = Array.from(reportTableBody.querySelectorAll("[data-report-severity]"))
    .find((element) => element.dataset.reportSeverity === report.report_id)?.value || "Medium";
  try {
    const result = await adminApiRequest("/admin/reports/approve", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ report_id: report.report_id, severity })
    });
    await refreshAfterAction(result.message);
  } catch (error) {
    showAdminFeedback(error.message, true);
  }
}

async function rejectReport(report) {
  try {
    const result = await adminApiRequest("/admin/reports/reject", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ report_id: report.report_id })
    });
    await refreshAfterAction(result.message);
  } catch (error) {
    showAdminFeedback(error.message, true);
  }
}

async function updateReportPriority(report) {
  const prioritySelect = Array.from(
    reportTableBody.querySelectorAll("[data-report-priority]")
  ).find((element) => element.dataset.reportPriority === report.report_id);
  const trafficSelect = Array.from(
    reportTableBody.querySelectorAll("[data-report-traffic]")
  ).find((element) => element.dataset.reportTraffic === report.report_id);
  if (!prioritySelect || !trafficSelect) {
    showAdminFeedback("Priority controls could not be found. Refresh the report list and try again.", true);
    return;
  }
  try {
    const result = await adminApiRequest("/admin/reports/priority", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        report_id: report.report_id,
        priority: prioritySelect.value,
        traffic_importance: trafficSelect.value
      })
    });
    await refreshAfterAction(result.message);
  } catch (error) {
    showAdminFeedback(error.message, true);
  }
}

function openAllocationForReport(report) {
  const hazard = state.assignableHazards.find((item) => item.report_id === report.report_id)
    || state.hazards.find((item) => item.report_id === report.report_id && item.status === "Active");
  if (!hazard || !state.assignableHazards.some((item) => item.hazard_id === hazard.hazard_id)) {
    showAdminFeedback("This report has no active, verified hazard available for assignment.", true);
    return;
  }
  document.querySelector("#allocation-hazard").value = hazard.hazard_id;
  void renderAllocationCandidates(hazard);
  document.querySelector("#task-allocation").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function assignWorker(hazardId, workerId, button) {
  button.disabled = true;
  try {
    const result = await adminApiRequest("/admin/assign-worker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hazard_id: hazardId, worker_id: Number(workerId) })
    });
    await refreshAfterAction(
      `${result.message} Server-recomputed rule score: ${result.task.allocation_score}/100.`
    );
  } catch (error) {
    button.disabled = false;
    showAdminFeedback(error.message, true);
  }
}

async function configureWorker(workerId) {
  const worker = state.workers.find((item) => String(item.worker_id) === String(workerId));
  if (!worker) {
    return;
  }
  const skillsText = window.prompt(
    "Enter supported hazard skills separated by commas (Pothole, Damaged Road, Broken Streetlight, Waterlogging, Road Obstruction, Other):",
    (worker.skills || []).join(", ")
  );
  if (skillsText === null) {
    return;
  }
  const locationName = window.prompt("Enter the worker's base location:", worker.location_name || "");
  if (locationName === null) {
    return;
  }
  const latitudeText = window.prompt("Enter base latitude, or leave blank:", worker.latitude ?? "");
  if (latitudeText === null) {
    return;
  }
  const longitudeText = window.prompt("Enter base longitude, or leave blank:", worker.longitude ?? "");
  if (longitudeText === null) {
    return;
  }
  const skills = skillsText.split(",").map((skill) => skill.trim()).filter(Boolean);
  const latitude = latitudeText.trim() === "" ? null : Number(latitudeText);
  const longitude = longitudeText.trim() === "" ? null : Number(longitudeText);
  try {
    const result = await adminApiRequest("/admin/workers/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        worker_id: Number(workerId),
        skills,
        location_name: locationName.trim(),
        latitude,
        longitude
      })
    });
    await refreshAfterAction(result.message);
  } catch (error) {
    showAdminFeedback(error.message, true);
  }
}

async function reviewRepair(button, action) {
  const evidenceId = Number(button.dataset.id);
  let reviewNotes = "";
  if (action !== "approve") {
    reviewNotes = window.prompt(
      action === "reject" ? "Reason for rejecting this repair:" : "What additional evidence is required?"
    );
    if (reviewNotes === null) {
      return;
    }
    if (!reviewNotes.trim()) {
      showAdminFeedback("Review notes are required for this action.", true);
      return;
    }
  }
  button.disabled = true;
  const endpoint = action === "approve"
    ? "/admin/verification/approve"
    : action === "reject"
      ? "/admin/verification/reject"
      : "/admin/verification/request-more";
  try {
    const result = await adminApiRequest(endpoint, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ evidence_id: evidenceId, review_notes: reviewNotes })
    });
    await refreshAfterAction(result.message);
  } catch (error) {
    button.disabled = false;
    showAdminFeedback(error.message, true);
  }
}

async function reviewVolunteer(button, action) {
  const participantId = Number(button.dataset.id);
  let reviewNotes = "";
  if (action === "reject") {
    reviewNotes = window.prompt("Reason for rejecting this activity completion:");
    if (reviewNotes === null) {
      return;
    }
    if (!reviewNotes.trim()) {
      showAdminFeedback("Review notes are required to reject an activity.", true);
      return;
    }
  }
  button.disabled = true;
  try {
    const result = await adminApiRequest(
      `/admin/activities/${action}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ participant_id: participantId, review_notes: reviewNotes })
      }
    );
    await refreshAfterAction(result.message);
  } catch (error) {
    button.disabled = false;
    showAdminFeedback(error.message, true);
  }
}

async function createVolunteerActivity(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    name: form.elements.name.value.trim(),
    location: form.elements.location.value.trim(),
    date: form.elements.date.value,
    description: form.elements.description.value.trim(),
    required_volunteers: Number(form.elements.requiredVolunteers.value),
    volunteer_points: Number(form.elements.points.value || 0)
  };
  const feedback = document.querySelector("#activity-form-feedback");
  if (!payload.name || !payload.location || !payload.date || !payload.description) {
    showInlineFeedback(feedback, "Complete all required activity fields.", true);
    return;
  }
  if (!Number.isInteger(payload.required_volunteers) || payload.required_volunteers < 1 || payload.required_volunteers > 500) {
    showInlineFeedback(feedback, "Required volunteers must be between 1 and 500.", true);
    return;
  }
  if (!Number.isInteger(payload.volunteer_points) || payload.volunteer_points < 0 || payload.volunteer_points > 500) {
    showInlineFeedback(feedback, "Volunteer points must be between 0 and 500.", true);
    return;
  }
  try {
    const result = await adminApiRequest("/admin/activities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    form.reset();
    form.elements.requiredVolunteers.value = "4";
    form.elements.points.value = "20";
    await refreshDashboard();
    showInlineFeedback(feedback, result.message, false);
    window.loadRoadguardNotifications?.();
  } catch (error) {
    showInlineFeedback(feedback, error.message, true);
  }
}

function handleReportClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const report = state.reports.find((item) => item.report_id === button.dataset.id);
  if (!report) {
    return;
  }
  if (button.dataset.action === "view-report") {
    showReportDetails(report, false);
  } else if (button.dataset.action === "view-image") {
    showReportDetails(report, true);
  } else if (button.dataset.action === "view-ai-result") {
    showReportDetails(report, false);
  } else if (button.dataset.action === "approve-report") {
    void approveReport(report);
  } else if (button.dataset.action === "reject-report") {
    void rejectReport(report);
  } else if (button.dataset.action === "update-priority") {
    void updateReportPriority(report);
  } else if (button.dataset.action === "open-allocation") {
    openAllocationForReport(report);
  }
}

function handleAdminAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  switch (button.dataset.action) {
    case "configure-worker":
      void configureWorker(button.dataset.id);
      break;
    case "focus-allocation":
      document.querySelector("#task-allocation").scrollIntoView({ behavior: "smooth", block: "start" });
      document.querySelector("#allocation-hazard").focus({ preventScroll: true });
      break;
    case "assign-worker":
      void assignWorker(button.dataset.hazardId, button.dataset.id, button);
      break;
    case "approve-repair":
      void reviewRepair(button, "approve");
      break;
    case "reject-repair":
      void reviewRepair(button, "reject");
      break;
    case "request-evidence":
      void reviewRepair(button, "request");
      break;
    case "approve-activity":
      void reviewVolunteer(button, "approve");
      break;
    case "reject-activity":
      void reviewVolunteer(button, "reject");
      break;
    default:
      break;
  }
}

function closeReportDialog() {
  if (reportDialog.open) {
    reportDialog.close();
  }
}

const sidebar = document.querySelector("#admin-sidebar");
const sidebarToggle = document.querySelector(".sidebar-toggle");
const sidebarOverlay = document.querySelector(".sidebar-overlay");

function setSidebarOpen(isOpen) {
  sidebar.classList.toggle("is-open", isOpen);
  sidebarOverlay.classList.toggle("is-visible", isOpen);
  sidebarToggle.setAttribute("aria-expanded", String(isOpen));
  sidebarToggle.setAttribute("aria-label", isOpen ? "Close navigation menu" : "Open dashboard menu");
}

sidebarToggle.addEventListener("click", () => {
  setSidebarOpen(sidebarToggle.getAttribute("aria-expanded") !== "true");
});
sidebarOverlay.addEventListener("click", () => setSidebarOpen(false));
document.querySelectorAll(".dashboard-nav-link[href^='#']").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".dashboard-nav-link[href^='#']").forEach((item) => {
      item.classList.remove("is-active");
      item.removeAttribute("aria-current");
    });
    link.classList.add("is-active");
    link.setAttribute("aria-current", "page");
    setSidebarOpen(false);
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setSidebarOpen(false);
    closeReportDialog();
  }
});
document.querySelector("#report-search").addEventListener("input", renderReports);
document.querySelector("#report-status-filter").addEventListener("change", renderReports);
document.querySelector("#hazard-category-filter").addEventListener("change", renderHazards);
document.querySelector("#hazard-severity-filter").addEventListener("change", renderHazards);
document.querySelector("#hazard-status-filter").addEventListener("change", renderHazards);
document.querySelector("#worker-search").addEventListener("input", renderWorkers);
workerTableBody.addEventListener("click", handleAdminAction);
reportTableBody.addEventListener("click", handleReportClick);
verificationList.addEventListener("click", handleAdminAction);
activityTableBody.addEventListener("click", handleAdminAction);
document.querySelector("#allocation-results").addEventListener("click", handleAdminAction);
document.querySelector("#allocation-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const hazardId = document.querySelector("#allocation-hazard").value;
  const hazard = state.assignableHazards.find((item) => item.hazard_id === hazardId);
  if (!hazard) {
    showInlineFeedback(
      document.querySelector("#allocation-feedback"),
      "Choose an active, verified database hazard first.",
      true
    );
    return;
  }
  void renderAllocationCandidates(hazard);
});
document.querySelector("#volunteer-create-form").addEventListener("submit", createVolunteerActivity);
document.querySelector("#admin-report-dialog .admin-dialog-close").addEventListener("click", closeReportDialog);
document.querySelector("#report-dialog-close").addEventListener("click", closeReportDialog);

initializeHazardMap();
void refreshDashboard();
window.setInterval(refreshDashboard, 60_000);
