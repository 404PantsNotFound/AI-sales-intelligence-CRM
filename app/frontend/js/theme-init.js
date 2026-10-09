(() => {
  let theme = "";
  try {
    theme = window.localStorage.getItem("salesdesk_theme") || "";
  } catch {
    theme = "";
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.dataset.theme = theme;
})();
