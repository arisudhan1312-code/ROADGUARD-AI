/* ROADGUARD AI Service Worker — background push notifications */
const SW_VERSION = "roadguard-sw-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch { data = { title: "ROADGUARD AI", body: event.data ? event.data.text() : "New alert." }; }
  const title = data.title || "ROADGUARD AI Alert";
  const options = {
    body: data.body || data.message || "You have a new notification.",
    icon: "/frontend/css/icon-192.png",
    badge: "/frontend/css/icon-192.png",
    tag: data.tag || "roadguard-alert",
    data: { url: data.url || "/frontend/driver.html#notifications" },
    actions: [{ action: "open", title: "View" }, { action: "dismiss", title: "Dismiss" }]
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  if (event.action === "dismiss") return;
  const url = event.notification.data?.url || "/frontend/driver.html#notifications";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
      for (const w of ws) {
        if (w.url.includes("driver.html") && "focus" in w) {
          w.navigate(url); return w.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
