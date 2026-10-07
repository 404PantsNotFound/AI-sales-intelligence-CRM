import { getAuthenticatedUser, getAuthToken, logoutUser } from "./api.js";

export function element(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

export function displayValue(value, fallback = "Not provided") {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string" && value.trim() === "") return fallback;
  return String(value);
}

export function formatDate(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not available";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function initials(value) {
  const words = displayValue(value, "C").trim().split(/\s+/);
  return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

export function humanize(value) {
  return displayValue(value, "Not provided")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function mountAuthControls() {
  const header = document.querySelector(".site-header");
  if (!header || header.querySelector(".header-auth")) return;

  const container = element("div", "header-auth");
  const token = getAuthToken();
  const user = getAuthenticatedUser();

  if (token) {
    if (user?.email) {
      container.append(element("span", "header-user-badge", user.email));
    }
    const logoutButton = element("button", "header-logout", "Sign out");
    logoutButton.type = "button";
    logoutButton.addEventListener("click", () => {
      logoutUser();
    });
    container.append(logoutButton);
  } else if (window.location.pathname !== "/login") {
    const loginLink = element("a", "header-logout", "Sign in");
    loginLink.href = "/login";
    container.append(loginLink);
  }

  header.append(container);
}

