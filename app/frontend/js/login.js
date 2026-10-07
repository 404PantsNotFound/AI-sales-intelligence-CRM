import { getAuthToken, loginUser, registerUser } from "./api.js";

const form = document.querySelector("#login-form");
const tabLogin = document.querySelector("#tab-login");
const tabRegister = document.querySelector("#tab-register");
const nameGroup = document.querySelector("#register-name-group");
const fullNameInput = document.querySelector("#auth-full-name");
const emailInput = document.querySelector("#auth-email");
const passwordInput = document.querySelector("#auth-password");
const errorBox = document.querySelector("#auth-error");
const noticeBox = document.querySelector("#auth-notice");
const submitButton = document.querySelector("#auth-submit");
const submitLabel = submitButton.querySelector("[data-auth-label]");
const heading = document.querySelector("#auth-heading");

let mode = "login";

function safeNextDestination() {
  const params = new URLSearchParams(window.location.search);
  const candidate = params.get("next") || "";
  if (candidate.startsWith("/") && !candidate.startsWith("//") && !candidate.startsWith("/login")) {
    return candidate;
  }
  return "/customer";
}

if (getAuthToken()) {
  window.location.assign(safeNextDestination());
}

function setMessages({ error = "", notice = "" } = {}) {
  errorBox.textContent = error;
  errorBox.hidden = !error;
  noticeBox.textContent = notice;
  noticeBox.hidden = !notice;
}

function setMode(nextMode) {
  mode = nextMode === "register" ? "register" : "login";
  const isRegister = mode === "register";
  tabLogin.classList.toggle("active", !isRegister);
  tabLogin.setAttribute("aria-selected", String(!isRegister));
  tabRegister.classList.toggle("active", isRegister);
  tabRegister.setAttribute("aria-selected", String(isRegister));
  nameGroup.hidden = !isRegister;
  fullNameInput.required = isRegister;
  passwordInput.autocomplete = isRegister ? "new-password" : "current-password";
  heading.textContent = isRegister ? "Create a workspace account" : "Sign in to SalesDesk";
  submitLabel.textContent = isRegister ? "Create account" : "Sign in";
  setMessages();
}

tabLogin.addEventListener("click", () => setMode("login"));
tabRegister.addEventListener("click", () => setMode("register"));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessages();

  if (!form.reportValidity()) return;

  const email = emailInput.value.trim();
  const password = passwordInput.value;
  const fullName = fullNameInput.value.trim();

  if (!email || !password.trim()) {
    setMessages({ error: "Please enter both your email and password." });
    return;
  }
  if (mode === "register" && !fullName) {
    setMessages({ error: "Please enter your full name." });
    fullNameInput.focus();
    return;
  }

  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  submitLabel.textContent = mode === "register" ? "Creating…" : "Signing in…";

  try {
    if (mode === "register") {
      await registerUser({
        email,
        password,
        full_name: fullName,
      });
    }
    await loginUser({ email, password });
    window.location.assign(safeNextDestination());
  } catch (error) {
    setMessages({
      error: error.message || "Authentication failed. Please check your credentials and try again.",
    });
  } finally {
    submitButton.disabled = false;
    submitButton.removeAttribute("aria-busy");
    submitLabel.textContent = mode === "register" ? "Create account" : "Sign in";
  }
});
