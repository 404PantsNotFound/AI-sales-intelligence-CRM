// Applies saved display preferences before first paint, so pages never flash.
// Keys and values are owned by preferences in common.js (Settings > Appearance).
(() => {
  const read = (key) => {
    try {
      return window.localStorage.getItem(key) || "";
    } catch {
      return "";
    }
  };
  const root = document.documentElement;
  let theme = read("salesdesk_theme");
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  root.dataset.theme = theme;
  if (read("salesdesk_density") === "compact") root.dataset.density = "compact";
  if (read("salesdesk_motion") === "reduce") root.dataset.motion = "reduce";
  if (read("salesdesk_sidebar") === "collapsed") root.dataset.sidebar = "collapsed";
  // Signed-out pages (home, sign-in) show no workspace navigation; only presence is checked.
  let signedIn = false;
  try {
    signedIn = Boolean(window.sessionStorage.getItem("salesdesk_access_token"));
  } catch {
    signedIn = false;
  }
  if (!signedIn) root.dataset.auth = "signed-out";
})();
