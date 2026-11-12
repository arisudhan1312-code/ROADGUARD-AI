// End the server-side signed session before returning to the public home page.
const logoutApiUrl = `${window.ROADGUARD_API_BASE}/logout`;

document.querySelectorAll("[data-logout]").forEach((link) => {
  link.addEventListener("click", async (event) => {
    event.preventDefault();
    link.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(logoutApiUrl, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" }
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || `Logout failed (HTTP ${response.status}).`);
      }
      window.location.assign("index.html");
    } catch (error) {
      link.removeAttribute("aria-busy");
      const existing = link.parentElement.querySelector("[data-logout-error]");
      const feedback = existing || document.createElement("p");
      feedback.dataset.logoutError = "true";
      feedback.className = "logout-error";
      feedback.setAttribute("role", "alert");
      feedback.textContent = error instanceof Error
        ? `${error.message} Your session may still be active.`
        : "Logout failed. Your session may still be active.";
      if (!existing) {
        link.parentElement.append(feedback);
      }
    }
  });
});
