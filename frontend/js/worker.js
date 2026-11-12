// Worker repair tasks, evidence, optional activities, and rewards use Flask APIs.
const workerApiBase = window.ROADGUARD_API_BASE;
const MAX_IMAGE_SIZE = 5 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png"];

const workerFeedback = document.querySelector("#worker-feedback");
const storageStatus = document.querySelector("#storage-status");
const taskList = document.querySelector("#official-task-list");
const completedTaskList = document.querySelector("#completed-task-list");
const activityList = document.querySelector("#volunteer-activity-list");
const evidenceForm = document.querySelector("#evidence-form");
const evidenceTaskSelect = document.querySelector("#evidence-task");
const beforePhotoInput = document.querySelector("#before-repair-photo");
const afterPhotoInput = document.querySelector("#after-repair-photo");
const evidenceGpsMessage = document.querySelector("#completion-gps-coordinates");
const evidenceFeedback = document.querySelector("#evidence-feedback");
const captureGpsButton = document.querySelector("#capture-completion-gps");
const submitEvidenceButton = document.querySelector("#submit-evidence");

let workerProfile = null;
let workerTasks = [];
let volunteerActivities = [];
let rewardTotals = null;
let workerTasksAvailable = false;
let activitiesAvailable = false;
let completionGps = null;
const previewUrls = new Map();

async function workerApiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${workerApiBase}${path}`, {
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
    console.error("The worker API returned unreadable JSON:", error);
    throw new Error(`The API returned an unreadable response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const message = response.status === 401
      ? "Please log in as a worker to load this dashboard."
      : (result.error || `The request failed (HTTP ${response.status}).`);
    throw new Error(message);
  }
  return result;
}

function showFeedback(element, message, isError) {
  element.textContent = message;
  element.hidden = false;
  element.classList.toggle("is-error", isError);
}

function formatDate(value) {
  if (!value) {
    return "Not provided";
  }
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
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

function addDetailItem(list, label, value, valueClass = "") {
  const item = createElement("div", "worker-detail-item");
  item.append(createElement("dt", "", label));
  item.append(createElement("dd", valueClass, value || "Not provided"));
  list.append(item);
}

function actionButton(label, action, id, secondary = false) {
  const button = createElement(
    "button",
    `button ${secondary ? "button-outline" : "button-green"} worker-action-button`,
    label
  );
  button.type = "button";
  button.dataset.taskAction = action;
  button.dataset.taskId = id;
  return button;
}

function taskStatusClass(status) {
  return {
    Assigned: "assigned",
    Accepted: "accepted",
    "In Progress": "in-progress",
    "Awaiting Admin Verification": "awaiting",
    Verified: "verified",
    Declined: "declined"
  }[status] || "assigned";
}

function createTaskCard(task, completedView) {
  const card = createElement("article", "worker-record-card");
  const header = createElement("div", "worker-record-header");
  const title = createElement("div", "worker-record-title");
  title.append(createElement("p", "worker-record-id", `TASK ${task.task_id}`));
  title.append(createElement("h3", "", task.hazard_category));
  header.append(
    title,
    createElement("span", `worker-status worker-status-${taskStatusClass(task.status)}`, task.status)
  );
  card.append(header);

  const details = createElement("dl", "worker-detail-grid");
  addDetailItem(details, "Location", task.location_name);
  const coords = Number.isFinite(task.latitude) && Number.isFinite(task.longitude)
    ? `${task.latitude.toFixed(5)}, ${task.longitude.toFixed(5)}`
    : "Not provided";
  addDetailItem(details, "GPS coordinates", coords, "worker-coordinate");
  addDetailItem(details, "Priority", task.priority || "Not specified");
  addDetailItem(details, "Assigned date", formatDate(task.assigned_at));
  addDetailItem(details, "Task status", task.status);
  card.append(details);

  if (task.review_notes) {
    card.append(createElement("p", "worker-verification-note", task.review_notes));
  }
  if (completedView && task.completion_datetime) {
    const completionDetails = createElement("dl", "worker-detail-grid worker-completion-meta");
    addDetailItem(completionDetails, "Completion date and time", formatDate(task.completion_datetime));
    const completionCoords = Number.isFinite(task.completion_latitude)
      && Number.isFinite(task.completion_longitude)
      ? `${task.completion_latitude.toFixed(6)}, ${task.completion_longitude.toFixed(6)}`
      : "Not provided";
    addDetailItem(completionDetails, "Completion GPS", completionCoords, "worker-coordinate");
    card.append(completionDetails);
    if (task.repair_description) {
      card.append(createElement("p", "worker-record-description", task.repair_description));
    }
    if (task.before_image_url || task.after_image_url) {
      const evidence = createElement("div", "worker-saved-evidence");
      [[task.before_image_url, "Before repair"], [task.after_image_url, "After repair"]]
        .forEach(([url, label]) => {
          if (!url) {
            return;
          }
          const figure = createElement("figure", "worker-evidence-thumbnail");
          const image = document.createElement("img");
          image.src = url;
          image.alt = `${label} evidence for task ${task.task_id}`;
          figure.append(image, createElement("figcaption", "", label));
          evidence.append(figure);
        });
      card.append(evidence);
    }
  }

  if (!completedView) {
    const actions = createElement("div", "worker-record-actions");
    if (task.status === "Assigned") {
      actions.append(
        actionButton("Accept Task", "accept", task.task_id),
        actionButton("Decline Task", "decline", task.task_id, true)
      );
    } else if (task.status === "Accepted") {
      actions.append(
        actionButton("Start Repair", "start", task.task_id),
        actionButton("Decline Task", "decline", task.task_id, true)
      );
    } else if (task.status === "In Progress") {
      const button = actionButton("Submit Repair Evidence", "evidence", task.task_id);
      actions.append(button);
    } else {
      actions.append(createElement("span", "worker-readonly-note", "No further action is available."));
    }
    card.append(actions);
  }
  return card;
}

function renderTasks() {
  taskList.replaceChildren();
  completedTaskList.replaceChildren();
  const activeTasks = workerTasks.filter((task) =>
    !["Awaiting Admin Verification", "Verified"].includes(task.status)
  );
  const completedTasks = workerTasks.filter((task) =>
    ["Awaiting Admin Verification", "Verified"].includes(task.status)
  );
  activeTasks.forEach((task) => taskList.append(createTaskCard(task, false)));
  completedTasks.forEach((task) => completedTaskList.append(createTaskCard(task, true)));

  const emptyTasks = document.querySelector("#empty-task-list");
  emptyTasks.hidden = activeTasks.length > 0;
  emptyTasks.textContent = workerTasksAvailable
    ? "No active repair tasks are currently assigned."
    : "Assigned repair tasks could not be loaded from Flask.";
  const emptyCompleted = document.querySelector("#empty-completed-list");
  emptyCompleted.hidden = completedTasks.length > 0;
  emptyCompleted.textContent = workerTasksAvailable
    ? "No completed repair tasks yet."
    : "Completed repair tasks could not be loaded from Flask.";
  const activeCount = workerTasksAvailable ? activeTasks.length : "—";
  const completedCount = workerTasksAvailable ? completedTasks.length : "—";
  document.querySelector("#task-list-count").textContent =
    workerTasksAvailable ? `${activeCount} ${activeTasks.length === 1 ? "task" : "tasks"}` : "Unavailable";
  document.querySelector("#completed-task-count").textContent =
    workerTasksAvailable ? `${completedCount} ${completedTasks.length === 1 ? "task" : "tasks"}` : "Unavailable";
  document.querySelector("#task-nav-count").textContent = activeCount;

  const inProgressTasks = workerTasks.filter((task) => task.status === "In Progress");
  const selectedTaskId = evidenceTaskSelect.value;
  const options = [new Option("Select an in-progress task", "")];
  inProgressTasks.forEach((task) => {
    options.push(new Option(`${task.task_id} · ${task.hazard_category}`, task.task_id));
  });
  evidenceTaskSelect.replaceChildren(...options);
  evidenceTaskSelect.disabled = inProgressTasks.length === 0;
  if (inProgressTasks.some((task) => task.task_id === selectedTaskId)) {
    evidenceTaskSelect.value = selectedTaskId;
  }
  const enabled = inProgressTasks.length > 0;
  captureGpsButton.disabled = !enabled;
  beforePhotoInput.disabled = !enabled;
  afterPhotoInput.disabled = !enabled;
  submitEvidenceButton.disabled = !enabled;
}

function mapActivity(activity) {
  const statuses = {
    Joined: "Joined",
    Submitted: "Awaiting Admin Approval",
    Approved: "Approved",
    Rejected: "Rejected"
  };
  return {
    ...activity,
    display_status: statuses[activity.participant_status] || "Available"
  };
}

function createActivityCard(rawActivity) {
  const activity = mapActivity(rawActivity);
  const card = createElement("article", "worker-record-card worker-activity-card");
  const header = createElement("div", "worker-record-header");
  const title = createElement("div", "worker-record-title");
  title.append(createElement("p", "worker-record-id", `ACTIVITY ${activity.activity_id}`));
  title.append(createElement("h3", "", activity.name));
  header.append(
    title,
    createElement(
      "span",
      `worker-status worker-activity-status-${activity.display_status.toLowerCase().replace(/[^a-z]+/g, "-")}`,
      activity.display_status
    )
  );
  card.append(header, createElement("p", "worker-record-description", activity.description));

  const details = createElement("dl", "worker-detail-grid worker-activity-details");
  addDetailItem(details, "Location", activity.location_name);
  addDetailItem(details, "Date", activity.activity_date);
  addDetailItem(details, "Volunteers", `${activity.participant_count} / ${activity.required_volunteers}`);
  addDetailItem(details, "Volunteer points", String(activity.volunteer_points), "worker-points-value");
  card.append(details);

  if (activity.completion_details) {
    card.append(createElement("p", "worker-record-description", activity.completion_details));
  }
  if (activity.review_notes) {
    card.append(createElement("p", "worker-verification-note", activity.review_notes));
  }
  const actions = createElement("div", "worker-record-actions");
  if (activity.display_status === "Available") {
    const button = createElement("button", "button button-outline worker-action-button", "Join Activity");
    button.type = "button";
    button.dataset.activityAction = "join";
    button.dataset.activityId = activity.activity_id;
    actions.append(button);
  } else if (["Joined", "Rejected"].includes(activity.participant_status)) {
    const textarea = document.createElement("textarea");
    textarea.rows = 2;
    textarea.maxLength = 2000;
    textarea.minLength = 10;
    textarea.placeholder = "Describe what you completed (10–2000 characters).";
    textarea.dataset.completionDetails = activity.activity_id;
    textarea.setAttribute("aria-label", `Completion details for ${activity.name}`);
    const button = createElement("button", "button button-green worker-action-button", "Submit Completion");
    button.type = "button";
    button.dataset.activityAction = "complete";
    button.dataset.activityId = activity.activity_id;
    actions.append(textarea, button);
  } else {
    actions.append(createElement(
      "span",
      "worker-readonly-note",
      activity.display_status === "Approved"
        ? "Administrator approved this optional activity."
        : "Completion submitted · waiting for administrator review."
    ));
  }
  card.append(actions);
  return card;
}

function renderActivities() {
  activityList.replaceChildren();
  volunteerActivities.forEach((activity) => activityList.append(createActivityCard(activity)));
  const availableCount = volunteerActivities.filter(
    (activity) => !activity.participant_status && activity.status === "Open"
  ).length;
  document.querySelector("#activity-nav-count").textContent =
    activitiesAvailable ? availableCount : "—";
  const empty = document.createElement("p");
  empty.className = "worker-empty-state";
  empty.textContent = activitiesAvailable
    ? "No volunteer activities are currently available."
    : "Volunteer activities could not be loaded from Flask.";
  empty.hidden = volunteerActivities.length > 0;
  activityList.append(empty);
}

function renderProfile() {
  if (!workerProfile) {
    document.querySelector("#worker-profile-name").textContent = "Profile unavailable";
    document.querySelector("#worker-profile-role").textContent = "The signed-in profile could not be loaded.";
    document.querySelector("#worker-profile-id").textContent = "—";
    document.querySelector("#worker-profile-location").textContent = "—";
    document.querySelector("#worker-profile-email").textContent = "—";
    document.querySelector("#worker-profile-tag").lastChild.textContent = " API profile unavailable";
    return;
  }
  document.querySelector("#worker-profile-name").textContent = workerProfile.name;
  document.querySelector("#worker-profile-role").textContent = `Road Maintenance Worker · ${workerProfile.availability}`;
  document.querySelector("#worker-profile-id").textContent = workerProfile.worker_code;
  document.querySelector("#worker-profile-location").textContent = workerProfile.location_name;
  document.querySelector("#worker-profile-email").textContent = workerProfile.email;
  document.querySelector("#worker-profile-tag").lastChild.textContent = " Signed-in API profile";
}

function renderRewards() {
  const activeTaskCount = workerTasks.filter((task) => task.status !== "Declined").length;
  const pendingTaskCount = workerTasks.filter((task) =>
    ["Assigned", "Accepted", "In Progress"].includes(task.status)
  ).length;
  const countText = (value) => workerTasksAvailable ? String(value) : "—";
  document.querySelector("#assigned-task-count").textContent = countText(activeTaskCount);
  document.querySelector("#pending-task-count").textContent = countText(pendingTaskCount);
  if (!rewardTotals) {
    [
      "#completed-repair-count",
      "#overview-volunteer-points",
      "#reward-completed-repairs",
      "#verified-repair-count",
      "#completed-activity-count",
      "#total-earned-points",
      "#repair-reward-points",
      "#volunteer-reward-points"
    ].forEach((selector) => {
      document.querySelector(selector).textContent = "—";
    });
    const achievementList = document.querySelector("#achievement-list");
    achievementList.replaceChildren();
    achievementList.append(createElement(
      "p",
      "worker-no-badges",
      "Reward totals could not be loaded from Flask."
    ));
    return;
  }

  const completedCount = Number(rewardTotals.completed_tasks);
  document.querySelector("#completed-repair-count").textContent = completedCount;
  document.querySelector("#overview-volunteer-points").textContent = rewardTotals.volunteer_points;
  document.querySelector("#reward-completed-repairs").textContent = completedCount;
  document.querySelector("#verified-repair-count").textContent = rewardTotals.verified_repairs;
  document.querySelector("#completed-activity-count").textContent = rewardTotals.completed_activities;
  document.querySelector("#total-earned-points").textContent = rewardTotals.total_points;
  document.querySelector("#repair-reward-points").textContent = rewardTotals.repair_points;
  document.querySelector("#volunteer-reward-points").textContent = rewardTotals.volunteer_points;

  const achievements = [];
  if (Number(rewardTotals.verified_repairs) > 0) {
    achievements.push(`${rewardTotals.verified_repairs} verified repair${rewardTotals.verified_repairs === 1 ? "" : "s"}`);
  }
  if (Number(rewardTotals.completed_activities) > 0) {
    achievements.push(`${rewardTotals.completed_activities} approved volunteer activit${rewardTotals.completed_activities === 1 ? "y" : "ies"}`);
  }
  const achievementList = document.querySelector("#achievement-list");
  achievementList.replaceChildren();
  achievementList.append(createElement(
    "p",
    "worker-no-badges",
    achievements.length ? achievements.join(" · ") : "No verified contributions recorded yet."
  ));
}

function renderDashboard() {
  renderTasks();
  renderActivities();
  renderProfile();
  renderRewards();
}

async function refreshWorkerDashboard() {
  const results = await Promise.allSettled([
    workerApiRequest("/worker/tasks"),
    workerApiRequest("/worker/activities"),
    workerApiRequest("/worker/rewards")
  ]);
  const failures = [];
  workerTasksAvailable = false;
  activitiesAvailable = false;

  if (results[0].status === "fulfilled") {
    const result = results[0].value;
    if (!result.worker || !Array.isArray(result.tasks)) {
      failures.push("The worker task API returned an invalid response.");
      workerTasks = [];
      workerProfile = null;
      storageStatus.textContent = "Task API response invalid";
    } else {
      workerProfile = result.worker;
      workerTasks = result.tasks;
      workerTasksAvailable = true;
      storageStatus.textContent = "Loaded from Flask API";
    }
  } else {
    workerTasks = [];
    workerProfile = null;
    storageStatus.textContent = "Task API unavailable";
    failures.push(results[0].reason.message);
  }
  if (results[1].status === "fulfilled" && Array.isArray(results[1].value.activities)) {
    volunteerActivities = results[1].value.activities;
    activitiesAvailable = true;
  } else {
    volunteerActivities = [];
    failures.push(results[1].status === "rejected"
      ? results[1].reason.message
      : "The volunteer activity API returned an invalid response.");
  }
  const rewardFields = [
    "repair_points",
    "volunteer_points",
    "total_points",
    "completed_tasks",
    "verified_repairs",
    "completed_activities"
  ];
  if (
    results[2].status === "fulfilled"
    && results[2].value
    && rewardFields.every((field) => {
      const value = results[2].value[field];
      return (typeof value === "number" || (typeof value === "string" && value.trim() !== ""))
        && Number.isFinite(Number(value));
    })
  ) {
    rewardTotals = results[2].value;
  } else {
    rewardTotals = null;
    failures.push(results[2].status === "rejected"
      ? results[2].reason.message
      : "The worker reward API returned an invalid response.");
  }

  renderDashboard();
  if (failures.length) {
    showFeedback(workerFeedback, failures.join(" "), true);
  } else {
    workerFeedback.hidden = true;
  }
}

function resetPhotoPreview(input, previewId) {
  const oldUrl = previewUrls.get(input);
  if (oldUrl) {
    URL.revokeObjectURL(oldUrl);
    previewUrls.delete(input);
  }
  const preview = document.querySelector(previewId);
  preview.hidden = true;
  preview.querySelector("img").removeAttribute("src");
  preview.querySelector("span").textContent = "";
  input.value = "";
}

function updatePhotoPreview(input, previewId) {
  const file = input.files[0];
  const preview = document.querySelector(previewId);
  const previousUrl = previewUrls.get(input);
  if (previousUrl) {
    URL.revokeObjectURL(previousUrl);
    previewUrls.delete(input);
  }
  if (!file) {
    preview.hidden = true;
    return;
  }
  const extension = file.name.toLowerCase().split(".").pop();
  const typeMatches = ALLOWED_IMAGE_TYPES.includes(file.type)
    || (file.type === "" && ["jpg", "jpeg", "png"].includes(extension));
  if (!typeMatches || file.size > MAX_IMAGE_SIZE) {
    resetPhotoPreview(input, previewId);
    showFeedback(
      evidenceFeedback,
      file.size > MAX_IMAGE_SIZE
        ? "Each photo must be 5 MB or smaller."
        : "Choose a JPG, JPEG, or PNG photo.",
      true
    );
    return;
  }
  const url = URL.createObjectURL(file);
  previewUrls.set(input, url);
  preview.querySelector("img").src = url;
  preview.querySelector("span").textContent = file.name;
  preview.hidden = false;
  evidenceFeedback.hidden = true;
}

function captureCompletionGps() {
  completionGps = null;
  evidenceGpsMessage.textContent = "Requesting location permission…";
  captureGpsButton.disabled = true;
  if (!navigator.geolocation) {
    evidenceGpsMessage.textContent = "Location services are not available in this browser.";
    captureGpsButton.disabled = false;
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (position) => {
      completionGps = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude
      };
      evidenceGpsMessage.textContent =
        `Captured: ${completionGps.latitude.toFixed(6)}, ${completionGps.longitude.toFixed(6)}`;
      captureGpsButton.disabled = false;
    },
    (error) => {
      const messages = {
        1: "Location permission was denied. Allow access and try again.",
        2: "Your location is unavailable. Check device location services.",
        3: "GPS timed out. Please try again."
      };
      evidenceGpsMessage.textContent = messages[error.code] || "Unable to capture GPS.";
      captureGpsButton.disabled = false;
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
  );
}

function openEvidenceForm(taskId) {
  evidenceTaskSelect.value = taskId;
  document.querySelector("#task-evidence").scrollIntoView({ behavior: "smooth", block: "start" });
  evidenceTaskSelect.focus({ preventScroll: true });
  evidenceFeedback.hidden = true;
}

taskList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-task-action]");
  if (!button) {
    return;
  }
  const task = workerTasks.find((item) => item.task_id === button.dataset.taskId);
  if (!task) {
    showFeedback(workerFeedback, "That task is no longer available. Refresh and try again.", true);
    return;
  }
  if (button.dataset.taskAction === "evidence") {
    openEvidenceForm(task.task_id);
    return;
  }
  const nextStatus = {
    accept: "Accepted",
    decline: "Declined",
    start: "In Progress"
  }[button.dataset.taskAction];
  if (!nextStatus) {
    return;
  }
  button.disabled = true;
  try {
    await workerApiRequest("/worker/tasks/status", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: task.task_id, status: nextStatus })
    });
    await refreshWorkerDashboard();
    showFeedback(workerFeedback, `Task ${task.task_id} status updated to ${nextStatus}.`, false);
    window.loadRoadguardNotifications?.();
  } catch (error) {
    button.disabled = false;
    showFeedback(workerFeedback, error.message, true);
  }
});

activityList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-activity-action]");
  if (!button) {
    return;
  }
  const activityId = button.dataset.activityId;
  button.disabled = true;
  try {
    if (button.dataset.activityAction === "join") {
      await workerApiRequest("/worker/activities/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activity_id: activityId })
      });
    } else {
      const card = button.closest(".worker-activity-card");
      const details = card.querySelector("[data-completion-details]").value.trim();
      if (details.length < 10 || details.length > 2000) {
        button.disabled = false;
        showFeedback(workerFeedback, "Completion details must be between 10 and 2000 characters.", true);
        return;
      }
      await workerApiRequest("/worker/activities/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activity_id: activityId, completion_details: details })
      });
    }
    await refreshWorkerDashboard();
    window.loadRoadguardNotifications?.();
  } catch (error) {
    button.disabled = false;
    showFeedback(workerFeedback, error.message, true);
  }
});

beforePhotoInput.addEventListener("change", () => updatePhotoPreview(beforePhotoInput, "#before-photo-preview"));
afterPhotoInput.addEventListener("change", () => updatePhotoPreview(afterPhotoInput, "#after-photo-preview"));
evidenceTaskSelect.addEventListener("change", () => {
  completionGps = null;
  evidenceGpsMessage.textContent = "GPS location has not been captured.";
  evidenceFeedback.hidden = true;
});
captureGpsButton.addEventListener("click", captureCompletionGps);

evidenceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!evidenceTaskSelect.value || !beforePhotoInput.files[0] || !afterPhotoInput.files[0] || !completionGps) {
    showFeedback(evidenceFeedback, "Choose an in-progress task, both repair photos, and capture GPS.", true);
    return;
  }
  const description = evidenceForm.elements.repairDescription.value.trim();
  if (description.length < 10 || description.length > 2000) {
    showFeedback(evidenceFeedback, "Repair description must be between 10 and 2000 characters.", true);
    return;
  }

  const payload = new FormData();
  payload.set("task_id", evidenceTaskSelect.value);
  payload.set("repair_description", description);
  payload.set("latitude", String(completionGps.latitude));
  payload.set("longitude", String(completionGps.longitude));
  payload.set("before_photo", beforePhotoInput.files[0]);
  payload.set("after_photo", afterPhotoInput.files[0]);
  submitEvidenceButton.disabled = true;
  try {
    const result = await workerApiRequest("/worker/repair-evidence", {
      method: "POST",
      body: payload
    });
    evidenceForm.reset();
    resetPhotoPreview(beforePhotoInput, "#before-photo-preview");
    resetPhotoPreview(afterPhotoInput, "#after-photo-preview");
    completionGps = null;
    evidenceGpsMessage.textContent = "GPS location has not been captured.";
    await refreshWorkerDashboard();
    showFeedback(evidenceFeedback, result.message, false);
    window.loadRoadguardNotifications?.();
  } catch (error) {
    showFeedback(evidenceFeedback, error.message, true);
  } finally {
    submitEvidenceButton.disabled = workerTasks.every((task) => task.status !== "In Progress");
  }
});

const workerSidebar = document.querySelector("#worker-sidebar");
const sidebarToggle = document.querySelector(".sidebar-toggle");
const sidebarOverlay = document.querySelector(".sidebar-overlay");

function setSidebarOpen(isOpen) {
  workerSidebar.classList.toggle("is-open", isOpen);
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

refreshWorkerDashboard();
