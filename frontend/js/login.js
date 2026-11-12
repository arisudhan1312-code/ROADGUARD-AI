// Validate the account forms and send them to the Flask API.
const authForm = document.querySelector("[data-auth-form]");

if (authForm) {
  const formType = authForm.dataset.authForm;
  const feedback = authForm.querySelector(".form-feedback");
  const submitButton = authForm.querySelector('button[type="submit"]');
  const apiBaseUrl = window.ROADGUARD_API_BASE;
  const dashboardByRole = {
    citizen: "citizen.html",
    driver: "driver.html",
    worker: "worker.html",
    admin: "admin.html"
  };

  function getFieldLabel(field) {
    const label = authForm.querySelector(`label[for="${field.id}"]`);
    return label ? label.textContent.trim() : "This field";
  }

  function validateForm() {
    const fields = Array.from(authForm.querySelectorAll("[required]"));

    for (const field of fields) {
      if (!field.value.trim()) {
        return { field, message: `Please enter your ${getFieldLabel(field).toLowerCase()}.` };
      }
    }

    const email = authForm.querySelector('input[type="email"]');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email.value.trim())) {
      return { field: email, message: "Please enter a valid email address." };
    }

    if (formType === "register") {
      const mobile = authForm.querySelector('[name="mobile"]');
      const digitsOnly = mobile.value.replace(/\D/g, "");
      if (!/^\+?[0-9\s().-]+$/.test(mobile.value.trim()) || digitsOnly.length < 7 || digitsOnly.length > 15) {
        return { field: mobile, message: "Enter a valid mobile number with 7 to 15 digits." };
      }

      const password = authForm.querySelector('[name="password"]');
      const confirmPassword = authForm.querySelector('[name="confirmPassword"]');
      if (password.value.length < 8 || password.value.length > 128) {
        return { field: password, message: "Your password must be between 8 and 128 characters long." };
      }
      if (password.value !== confirmPassword.value) {
        return { field: confirmPassword, message: "The passwords do not match." };
      }

      const role = authForm.querySelector('[name="role"]');
      if (!["citizen", "driver", "worker"].includes(role.value)) {
        return { field: role, message: "Choose Citizen, Driver, or Worker. Admin accounts are provisioned separately." };
      }
    }

    return null;
  }

  function showFeedback(message, isError) {
    feedback.textContent = message;
    feedback.hidden = false;
    feedback.classList.toggle("is-error", isError);
  }

  // Toggle visibility without changing the entered password.
  document.querySelectorAll("[data-toggle-password]").forEach((button) => {
    button.addEventListener("click", () => {
      const password = document.getElementById(button.dataset.togglePassword);
      const willShow = password.type === "password";
      password.type = willShow ? "text" : "password";
      button.textContent = willShow ? "Hide" : "Show";
      button.setAttribute("aria-pressed", String(willShow));
      button.setAttribute("aria-label", `${willShow ? "Hide" : "Show"} ${getFieldLabel(password).toLowerCase()}`);
    });
  });

  authForm.addEventListener("input", (event) => {
    event.target.removeAttribute("aria-invalid");
    feedback.hidden = true;
  });
  authForm.addEventListener("change", (event) => {
    event.target.removeAttribute("aria-invalid");
    feedback.hidden = true;
  });

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    authForm.querySelectorAll("[aria-invalid='true']").forEach((field) => {
      field.removeAttribute("aria-invalid");
    });

    const error = validateForm();
    if (error) {
      error.field.setAttribute("aria-invalid", "true");
      showFeedback(error.message, true);
      error.field.focus();
      return;
    }

    const formData = new FormData(authForm);
    const payload = {
      email: String(formData.get("email")).trim(),
      password: String(formData.get("password"))
    };

    if (formType === "register") {
      payload.name = String(formData.get("name")).trim();
      payload.mobile = String(formData.get("mobile")).trim();
      payload.role = String(formData.get("role"));
    }

    submitButton.disabled = true;
    submitButton.setAttribute("aria-busy", "true");

    try {
      let response;
      try {
        response = await fetch(`${apiBaseUrl}/${formType === "register" ? "register" : "login"}`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
      } catch (requestError) {
        console.error("Authentication request failed:", requestError);
        showFeedback(
          "Cannot reach the Flask API. Start the backend and open this page through VS Code Live Server.",
          true
        );
        return;
      }

      let result;
      try {
        result = await response.json();
      } catch (responseError) {
        console.error("The Flask API returned invalid JSON:", responseError);
        showFeedback(`The API returned an unreadable response (HTTP ${response.status}).`, true);
        return;
      }

      if (!response.ok) {
        showFeedback(result.error || `The request failed (HTTP ${response.status}).`, true);
        return;
      }

      if (formType === "register") {
        authForm.reset();
        showFeedback("Your account was created. You can now log in.", false);
        return;
      }

      const destination = dashboardByRole[result.user?.role];
      if (!destination) {
        showFeedback("Login succeeded, but this account has no supported dashboard role.", true);
        return;
      }

      showFeedback("Login successful. Opening your dashboard…", false);
      window.location.assign(destination);
    } finally {
      submitButton.disabled = false;
      submitButton.removeAttribute("aria-busy");
    }
  });

  const forgotPasswordLink = document.querySelector("[data-forgot-password]");
  if (forgotPasswordLink) {
    forgotPasswordLink.addEventListener("click", (event) => {
      event.preventDefault();
      showFeedback("Password recovery is not available in this stage.", false);
      feedback.focus();
    });
  }
}
