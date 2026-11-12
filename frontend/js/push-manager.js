/* ROADGUARD AI — Web Push subscription manager */
(function () {
  const API_BASE = window.ROADGUARD_API_BASE || "http://127.0.0.1:5000/api";
  let _vapidKey = "";

  function urlBase64ToUint8Array(b64) {
    const padding = "=".repeat((4 - b64.length % 4) % 4);
    const base64 = (b64 + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
  }

  async function getVapidKey() {
    if (_vapidKey) return _vapidKey;
    const r = await fetch(`${API_BASE}/push/vapid-public-key`, { credentials: "include" });
    if (!r.ok) throw new Error("VAPID key not available. Add VAPID keys to backend/.env");
    const d = await r.json();
    _vapidKey = d.public_key;
    return _vapidKey;
  }

  async function enablePushNotifications(statusEl) {
    function set(msg, err) {
      if (statusEl) { statusEl.textContent = msg; statusEl.style.color = err ? "#f87171" : "#34d399"; }
    }
    if (!("Notification" in window)) { set("Notifications not supported.", true); return; }
    if (Notification.permission === "denied") { set("Permission denied — enable in browser settings.", true); return; }
    set("Requesting permission…", false);
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { set("Permission not granted.", true); return; }
    try {
      set("Registering service worker…", false);
      if (!("serviceWorker" in navigator)) throw new Error("Service workers not supported.");
      const reg = await navigator.serviceWorker.register("/frontend/service-worker.js", { scope: "/frontend/" });
      await navigator.serviceWorker.ready;
      set("Subscribing to push…", false);
      const key = await getVapidKey();
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) });
      set("Saving subscription…", false);
      const r2 = await fetch(`${API_BASE}/push/subscribe`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sub)
      });
      const d2 = await r2.json();
      if (!r2.ok) throw new Error(d2.error || "Could not save subscription.");
      set("Background notifications enabled!", false);
    } catch (err) { set("Error: " + err.message, true); console.error("Push error:", err); }
  }

  window.RoadguardPush = { enablePushNotifications };
})();
