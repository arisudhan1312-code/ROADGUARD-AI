// Load and mark role-scoped notifications through the authenticated Flask API.
const notificationApiBase = window.ROADGUARD_API_BASE;
const notificationList = document.querySelector("[data-notifications-list]");
const notificationStatus = document.querySelector("[data-notifications-status]");
const notificationCountNodes = document.querySelectorAll("[data-notification-count]");

function formatNotificationDate(value) {
  if (!value) {
    return "Date not available";
  }
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function renderNotifications(items) {
  notificationList.replaceChildren();
  const unreadCount = items.filter((item) => !item.read_at).length;
  notificationCountNodes.forEach((node) => {
    node.textContent = String(unreadCount);
    node.hidden = unreadCount === 0;
  });

  items.forEach((item) => {
    const row = document.createElement("article");
    row.className = `notification-item${item.read_at ? "" : " notification-item-unread"}`;
    const icon = document.createElement("span");
    icon.className = `notification-icon ${item.read_at ? "update-notification" : "approved-notification"}`;
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = item.read_at ? "i" : "•";

    const content = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = item.title;
    const message = document.createElement("p");
    message.textContent = item.message;
    const time = document.createElement("time");
    time.dateTime = item.created_at.replace(" ", "T");
    time.textContent = formatNotificationDate(item.created_at);
    content.append(title, message, time);
    row.append(icon, content);

    if (!item.read_at) {
      const readButton = document.createElement("button");
      readButton.className = "notification-read-button";
      readButton.type = "button";
      readButton.dataset.notificationRead = String(item.notification_id);
      readButton.textContent = "Mark read";
      row.append(readButton);
    }
    notificationList.append(row);
  });

  const empty = document.querySelector("[data-notifications-empty]");
  if (empty) {
    empty.hidden = items.length > 0;
  }
}

async function loadRoadguardNotifications() {
  if (!notificationList) {
    return;
  }
  notificationStatus.textContent = "Loading";
  try {
    const response = await fetch(`${notificationApiBase}/notifications`, {
      credentials: "include",
      headers: { Accept: "application/json" }
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Notifications failed (HTTP ${response.status}).`);
    }
    if (!Array.isArray(result.notifications)) {
      throw new Error("The notifications API returned an invalid list.");
    }
    renderNotifications(result.notifications);
    notificationStatus.textContent = "Live";
  } catch (error) {
    notificationList.replaceChildren();
    notificationCountNodes.forEach((node) => {
      node.textContent = "!";
      node.hidden = false;
    });
    notificationStatus.textContent = "Unavailable";
    const empty = document.querySelector("[data-notifications-empty]");
    if (empty) {
      empty.textContent = error instanceof Error ? error.message : "Notifications are unavailable.";
      empty.hidden = false;
    }
    console.error("Could not load notifications:", error);
  }
}

async function sendRoadguardHazardNotification(hazard, position, radius) {
  const response = await fetch(`${notificationApiBase}/notifications/hazard-warning`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      hazard_id: hazard.hazard_id,
      latitude: position.latitude,
      longitude: position.longitude,
      radius
    })
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || `Hazard notification failed (HTTP ${response.status}).`);
  }
  await loadRoadguardNotifications();
  return result;
}

notificationList?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-notification-read]");
  if (!button) {
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`${notificationApiBase}/notifications/read`, {
      method: "PUT",
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ notification_id: Number(button.dataset.notificationRead) })
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Notification update failed (HTTP ${response.status}).`);
    }
    await loadRoadguardNotifications();
  } catch (error) {
    button.disabled = false;
    notificationStatus.textContent = "Update failed";
    console.error("Could not mark notification as read:", error);
  }
});

window.loadRoadguardNotifications = loadRoadguardNotifications;
window.sendRoadguardHazardNotification = sendRoadguardHazardNotification;
loadRoadguardNotifications();
window.setInterval(loadRoadguardNotifications, 15_000);
