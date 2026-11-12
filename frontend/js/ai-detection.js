// Inference runs only when a trained road-damage checkpoint is configured.
const aiApiBase = window.ROADGUARD_API_BASE;
const aiMaximumFileSize = 5 * 1024 * 1024;
const aiDetectionsById = new Map();

async function aiApiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${aiApiBase}${path}`, {
      ...options,
      credentials: "include",
      headers: { Accept: "application/json", ...(options.headers || {}) }
    });
  } catch (error) {
    console.error("Could not reach the ROADGUARD AI API:", error);
    throw new Error("Cannot reach Flask. Start the backend and open the dashboard through VS Code Live Server.");
  }

  let result;
  try {
    result = await response.json();
  } catch (error) {
    console.error("The AI image API returned unreadable JSON:", error);
    throw new Error(`The API returned an unreadable response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(result.error || `The request failed (HTTP ${response.status}).`);
  }
  return result;
}

function aiImageUrl(path) {
  return path ? new URL(path, aiApiBase).href : "";
}

function aiDisplayValue(value, unavailable = "Not analyzed") {
  if (value === null || value === undefined || value === "") {
    return unavailable;
  }
  return String(value);
}

function createAiResultCard(detection, role) {
  const card = document.createElement("article");
  card.className = "ai-result-card";

  const heading = document.createElement("div");
  heading.className = "ai-result-heading";
  const title = document.createElement("h3");
  title.textContent = `Image ${detection.detection_id}`;
  const status = document.createElement("span");
  status.textContent = detection.detection_status || "Status unavailable";
  const statusClass = detection.detection_status === "Confirmed"
    ? "status-approved"
    : detection.detection_status === "Rejected"
      ? "status-rejected"
      : "status-pending";
  status.className = `report-status ${statusClass}`;
  heading.append(title, status);
  card.append(heading);

  if (detection.original_image_url) {
    const image = document.createElement("img");
    image.className = "ai-result-image";
    image.src = aiImageUrl(detection.original_image_url);
    image.alt = "Original uploaded road image.";
    image.loading = "lazy";
    card.append(image);
  }
  if (detection.annotated_image_url) {
    const annotatedLabel = document.createElement("p");
    annotatedLabel.className = "ai-result-note";
    annotatedLabel.textContent = "Model annotation (not administrator-confirmed)";
    const annotatedImage = document.createElement("img");
    annotatedImage.className = "ai-result-image";
    annotatedImage.src = aiImageUrl(detection.annotated_image_url);
    annotatedImage.alt = "Road image annotated with model predictions.";
    annotatedImage.loading = "lazy";
    card.append(annotatedLabel, annotatedImage);
  }

  const facts = document.createElement("div");
  facts.className = "ai-result-facts";
  [
    ["Submitter", detection.submitter_name],
    ["Linked report", detection.report_id],
    ["Hazard category", detection.hazard_category],
    ["Detection count", detection.detection_count],
    ["Counts by category", detection.category_counts
      ? JSON.stringify(detection.category_counts)
      : null],
    ["Pothole count", detection.pothole_count],
    ["Confidence", detection.confidence_score === null || detection.confidence_score === undefined
      ? null
      : `${(Number(detection.confidence_score) * 100).toFixed(1)}%`],
    ["Bounding boxes", Array.isArray(detection.bounding_boxes)
      ? JSON.stringify(detection.bounding_boxes)
      : null],
    ["Uploaded", detection.created_at]
  ].forEach(([label, value]) => {
    const fact = document.createElement("div");
    const factLabel = document.createElement("span");
    factLabel.textContent = label;
    const factValue = document.createElement("strong");
    factValue.textContent = aiDisplayValue(value);
    fact.append(factLabel, factValue);
    facts.append(fact);
  });
  card.append(facts);

  const note = document.createElement("p");
  note.className = "ai-result-note";
  const statusMessages = {
    "Pending Admin Review": "Detections are queued for human administrator review. They are not confirmed hazards.",
    "Low Confidence": "Low-confidence detections are queued for human review and are not confirmed hazards.",
    "No Detections": "The configured model found no supported hazards in this image.",
    "Model Unavailable": "The image was saved, but inference did not run because road-damage model weights or Ultralytics are unavailable.",
    Failed: "The image was saved, but inference failed. Check the backend log before retrying."
  };
  note.textContent = statusMessages[detection.detection_status]
    || "Model output is advisory and requires administrator verification.";
  card.append(note);

  if (role === "citizen" && detection.detection_status === "Model Unavailable") {
    const submitButton = document.createElement("button");
    submitButton.className = "button button-outline";
    submitButton.type = "button";
    submitButton.dataset.aiSubmitId = detection.detection_id;
    submitButton.textContent = "Submit for Admin Verification";
    card.append(submitButton);
  }
  if (
    role === "admin"
    && ["Pending Admin Review", "Low Confidence"].includes(detection.detection_status)
  ) {
    const actions = document.createElement("div");
    actions.className = "admin-action-group";
    [
      ["Confirm AI Result", "confirm", "button-green"],
      ["Reject AI Result", "reject", "button-outline admin-button-danger"]
    ].forEach(([label, decision, className]) => {
      const button = document.createElement("button");
      button.className = `button ${className}`;
      button.type = "button";
      button.dataset.aiReviewId = detection.detection_id;
      button.dataset.aiReviewDecision = decision;
      button.textContent = label;
      actions.append(button);
    });
    card.append(actions);
  }
  return card;
}

function showAiFeedback(container, message, isError) {
  container.textContent = message;
  container.hidden = false;
  container.classList.toggle("is-error", isError);
}

function initializeAiUploadForm(form) {
  const role = form.dataset.aiRole;
  const imageInput = form.querySelector("[data-ai-image]");
  const preview = form.querySelector("[data-ai-preview]");
  const progressWrap = form.querySelector("[data-ai-progress-wrap]");
  const progressText = form.querySelector("[data-ai-progress-text]");
  const feedback = form.querySelector("[data-ai-feedback]");
  const currentResult = form.querySelector("[data-ai-current-result]");
  const reportSelect = form.querySelector("[data-ai-report-id]");
  let previewUrl = "";

  imageInput.addEventListener("change", () => {
    feedback.hidden = true;
    preview.replaceChildren();
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = "";
    }

    const file = imageInput.files[0];
    if (!file) {
      preview.hidden = true;
      return;
    }
    const extension = file.name.toLowerCase().split(".").pop();
    const supported = file.type === "image/jpeg"
      || file.type === "image/png"
      || ["jpg", "jpeg", "png"].includes(extension);
    if (!supported || file.size > aiMaximumFileSize) {
      imageInput.value = "";
      preview.hidden = true;
      showAiFeedback(
        feedback,
        supported ? "The image must be 5 MB or smaller." : "Choose a JPG, JPEG, or PNG image.",
        true
      );
      return;
    }

    previewUrl = URL.createObjectURL(file);
    const image = document.createElement("img");
    image.src = previewUrl;
    image.alt = "Selected road image preview";
    preview.append(image);
    preview.hidden = false;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    feedback.hidden = true;
    currentResult.replaceChildren();
    if (!imageInput.files.length) {
      imageInput.focus();
      showAiFeedback(feedback, "Choose a road image before uploading.", true);
      return;
    }

    const button = form.querySelector('button[type="submit"]');
    const data = new FormData();
    data.append("image", imageInput.files[0]);
    if (reportSelect?.value) {
      data.append("report_id", reportSelect.value);
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    progressWrap.hidden = false;
    progressText.textContent = "Uploading image, validating it with OpenCV, and running the configured model…";
    try {
      const result = await aiApiRequest("/ai/detect", { method: "POST", body: data });
      if (!result.detection?.detection_id) {
        throw new Error("The API did not return an image record.");
      }
      aiDetectionsById.set(result.detection.detection_id, result.detection);
      currentResult.append(createAiResultCard(result.detection, role));
      showAiFeedback(feedback, result.message, false);
      progressText.textContent = "Image processing complete.";
      imageInput.value = "";
      preview.replaceChildren();
      preview.hidden = true;
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
        previewUrl = "";
      }
      await loadAiDetectionList(role);
    } catch (error) {
      showAiFeedback(feedback, error.message, true);
      progressText.textContent = "";
      console.error("Unable to upload the road image:", error);
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      progressText.textContent = "";
      progressWrap.hidden = true;
    }
  });
}

async function loadCitizenReportOptions(form) {
  const select = form.querySelector("[data-ai-report-id]");
  if (!select) {
    return;
  }
  try {
    const result = await aiApiRequest("/reports/my");
    if (!Array.isArray(result.reports)) {
      throw new Error("The API returned an invalid citizen report list.");
    }
    const options = [new Option("Do not link to an existing report", "")];
    result.reports.forEach((report) => {
      options.push(new Option(
        `${report.report_id} · ${report.hazard_category} · ${report.status}`,
        report.report_id
      ));
    });
    select.replaceChildren(...options);
  } catch (error) {
    select.replaceChildren(new Option("Reports could not be loaded", ""));
    select.disabled = true;
    console.error("Unable to load reports for AI image linking:", error);
  }
}

async function loadAiDetectionList(role) {
  const citizenList = document.querySelector("[data-ai-citizen-results]");
  const adminList = document.querySelector("[data-ai-admin-queue]");
  const list = role === "admin" ? adminList : citizenList;
  if (!list) {
    return;
  }
  const emptyMessage = role === "admin"
    ? document.querySelector("[data-ai-admin-empty]")
    : null;
  list.replaceChildren();
  const loading = document.createElement("p");
  loading.className = role === "admin" ? "admin-empty-state" : "ai-result-note";
  loading.textContent = role === "admin"
    ? "Loading images for review…"
    : "Loading your uploaded images…";
  list.append(loading);

  try {
    const path = role === "admin" ? "/admin/ai/detections" : "/ai/detections/mine";
    const result = await aiApiRequest(path);
    if (!Array.isArray(result.detections)) {
      throw new Error("The API returned an invalid image list.");
    }
    result.detections.forEach((detection) => {
      aiDetectionsById.set(detection.detection_id, detection);
    });
    list.replaceChildren(...result.detections.map((detection) => createAiResultCard(detection, role)));
    if (emptyMessage) {
      emptyMessage.hidden = result.detections.length > 0;
      emptyMessage.textContent = result.detections.length
        ? ""
        : "No images are awaiting manual review.";
    } else if (!result.detections.length) {
      const empty = document.createElement("p");
      empty.className = "ai-result-note";
      empty.textContent = "No road images have been uploaded yet.";
      list.append(empty);
    }
    const navCount = document.querySelector("[data-ai-admin-count]");
    if (navCount) {
      navCount.textContent = result.detections.length;
    }
  } catch (error) {
    list.replaceChildren();
    const message = document.createElement("p");
    message.className = role === "admin" ? "admin-empty-state" : "ai-result-note";
    message.textContent = error.message;
    list.append(message);
    if (emptyMessage) {
      emptyMessage.hidden = true;
    }
    console.error("Unable to load road image records:", error);
  }
}

document.querySelectorAll("[data-ai-detection-form]").forEach((form) => {
  initializeAiUploadForm(form);
  if (form.dataset.aiRole === "citizen") {
    void loadCitizenReportOptions(form);
  }
});
if (document.querySelector("[data-ai-citizen-results]")) {
  loadAiDetectionList("citizen");
}
if (document.querySelector("[data-ai-admin-queue]")) {
  loadAiDetectionList("admin");
}

document.addEventListener("click", async (event) => {
  const reviewButton = event.target.closest("[data-ai-review-id]");
  if (reviewButton) {
    const detectionId = reviewButton.dataset.aiReviewId;
    const detection = aiDetectionsById.get(detectionId);
    const card = reviewButton.closest(".ai-result-card");
    const decision = reviewButton.dataset.aiReviewDecision;
    if (
      !window.confirm(
        `${decision === "confirm" ? "Confirm" : "Reject"} this AI result? `
        + "This will not approve or reject the linked citizen report."
      )
    ) {
      return;
    }
    reviewButton.disabled = true;
    try {
      const result = await aiApiRequest(
        `/admin/ai/detections/${encodeURIComponent(detectionId)}/review`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision })
        }
      );
      if (detection) {
        detection.detection_status = result.detection_status;
      }
      if (card) {
        card.replaceWith(createAiResultCard(detection || {
          detection_id: detectionId,
          detection_status: result.detection_status
        }, "admin"));
      }
      await loadAiDetectionList("admin");
      if (typeof window.refreshDashboard === "function") {
        await window.refreshDashboard();
      }
      window.loadRoadguardNotifications?.();
    } catch (error) {
      reviewButton.disabled = false;
      const note = card?.querySelector(".ai-result-note");
      if (note) {
        note.textContent = error.message;
      }
      console.error("Unable to save AI detection review:", error);
    }
    return;
  }

  const button = event.target.closest("[data-ai-submit-id]");
  if (!button) {
    return;
  }
  const detection = aiDetectionsById.get(button.dataset.aiSubmitId);
  const card = button.closest(".ai-result-card");
  const feedback = card?.querySelector(".ai-result-note");
  button.disabled = true;
  try {
    const result = await aiApiRequest(
      `/ai/detections/${encodeURIComponent(button.dataset.aiSubmitId)}/submit`,
      { method: "POST" }
    );
    if (detection) {
      detection.detection_status = result.detection_status;
      card.replaceWith(createAiResultCard(detection, "citizen"));
      aiDetectionsById.set(detection.detection_id, detection);
    }
    if (feedback) {
      feedback.textContent = result.message;
    }
    await loadAiDetectionList("citizen");
  } catch (error) {
    button.disabled = false;
    if (feedback) {
      feedback.textContent = error.message;
    }
    console.error("Unable to submit image for administrator review:", error);
  }
});
