import {
  connectGoogleCalendar,
  disconnectGoogleCalendar,
  getAuthenticatedUser,
  getCalendarStatus,
  getCurrentUser,
  logoutUser,
  requireAuth,
  retryCalendarSync,
  updateCalendarPreferences,
} from "./api.js";
import { element, getPreference, humanize, mountAuthControls, setPreference } from "./common.js";

const statusText = document.querySelector("#calendar-status");
const message = document.querySelector("#calendar-message");
const connectButton = document.querySelector("#calendar-connect");
const disconnectButton = document.querySelector("#calendar-disconnect");
const preferencesForm = document.querySelector("#calendar-preferences");
const reminderInput = document.querySelector("#reminder-minutes");
const failures = document.querySelector("#calendar-failures");
const failureCount = document.querySelector("#calendar-failure-count");
const retryButton = document.querySelector("#calendar-retry");

const ERROR_MESSAGES = {
  access_denied: "Google Calendar access was not granted.",
  calendar_oauth_state_invalid: "The connection request expired or could not be verified. Please try again.",
  calendar_scope_not_granted: "Calendar access was not granted. Please allow calendar access to connect.",
  calendar_reauthorization_required: "Google rejected the authorization. Please connect again.",
  calendar_not_configured: "Google Calendar is not configured on this server.",
};

function showMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("is-error", isError);
  message.hidden = !text;
}

function render(status) {
  const connected = status.connected;
  if (!status.configured) {
    statusText.textContent = "Google Calendar is not configured on this server.";
  } else if (!connected) {
    statusText.textContent = "Not connected.";
  } else if (status.status === "reauthorization_required") {
    statusText.textContent = "Connected, but Google needs you to reconnect before events can sync.";
  } else {
    statusText.textContent = "Connected. New scheduled activities are added to your Google Calendar.";
  }
  connectButton.hidden = !status.configured || (connected && status.status === "connected");
  connectButton.textContent = connected ? "Reconnect Google Calendar" : "Connect Google Calendar";
  disconnectButton.hidden = !connected;
  preferencesForm.hidden = !connected;
  reminderInput.value = String(status.reminder_minutes);
  failures.hidden = !connected || status.failed_syncs === 0;
  failureCount.textContent = `${status.failed_syncs} ${status.failed_syncs === 1 ? "activity has" : "activities have"} not synced.`;
}

async function run(action, successText) {
  for (const button of [connectButton, disconnectButton, retryButton]) button.disabled = true;
  try {
    const status = await action();
    if (status) render(status);
    if (successText) showMessage(successText);
  } catch (error) {
    showMessage(ERROR_MESSAGES[error.code] || error.message || "The request could not be completed.", true);
  } finally {
    for (const button of [connectButton, disconnectButton, retryButton]) button.disabled = false;
  }
}

connectButton.addEventListener("click", () => {
  void run(async () => {
    const { authorization_url: url } = await connectGoogleCalendar();
    window.location.assign(url);
    return null;
  });
});
disconnectButton.addEventListener("click", () => {
  void run(disconnectGoogleCalendar, "Google Calendar disconnected. Existing events were left in your calendar.");
});
retryButton.addEventListener("click", () => {
  void run(retryCalendarSync, "Retry finished.");
});
preferencesForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void run(
    () => updateCalendarPreferences(Number(reminderInput.value)),
    "Reminder preference saved for future events.",
  );
});

// ---------- Appearance and workspace preferences (this browser) ----------
const preferencesStatus = document.querySelector("#preferences-status");
let savedTimer = 0;

function showSaved() {
  preferencesStatus.textContent = "Preference saved for this browser.";
  window.clearTimeout(savedTimer);
  savedTimer = window.setTimeout(() => {
    preferencesStatus.textContent = "";
  }, 2500);
}

const PREFERENCE_GROUPS = ["theme", "density", "motion", "sidebar"];

function syncPreferenceInputs() {
  for (const name of PREFERENCE_GROUPS) {
    const current = getPreference(name);
    for (const input of document.querySelectorAll(`input[name="${name}"]`)) {
      input.checked = input.value === current;
    }
  }
}

for (const name of PREFERENCE_GROUPS) {
  for (const input of document.querySelectorAll(`input[name="${name}"]`)) {
    input.addEventListener("change", () => {
      setPreference(name, input.value);
      showSaved();
    });
  }
}
// The theme toggle and sidebar button change the same preferences.
document.addEventListener("preferencechange", syncPreferenceInputs);
syncPreferenceInputs();
const startPage = document.querySelector("#start-page");
startPage.value = getPreference("startPage");
startPage.addEventListener("change", () => {
  setPreference("startPage", startPage.value);
  showSaved();
});

// ---------- Account ----------
function renderAccount(user) {
  const details = document.querySelector("#account-details");
  details.replaceChildren();
  for (const [label, value] of [["Name", user?.full_name], ["Email", user?.email], ["Role", user?.role && humanize(user.role)]]) {
    details.append(element("dt", "", label), element("dd", "", value || "Not available"));
  }
  // Display only; the server enforces who may change action policies.
  document.querySelector("#admin-note").hidden = user?.role !== "admin";
}

document.querySelector("#settings-sign-out").addEventListener("click", () => logoutUser());

mountAuthControls();
if (requireAuth()) {
  renderAccount(getAuthenticatedUser());
  getCurrentUser().then(renderAccount).catch(() => {});
  const params = new URLSearchParams(window.location.search);
  if (params.get("calendar") === "connected") {
    showMessage("Google Calendar connected.");
  } else if (params.get("calendar") === "error") {
    showMessage(ERROR_MESSAGES[params.get("reason")] || "Google Calendar could not be connected.", true);
  }
  if (params.has("calendar")) window.history.replaceState(null, "", "/settings");
  void run(getCalendarStatus);
}
