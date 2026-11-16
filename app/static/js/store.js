const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  init = init || {};
  const request = input instanceof Request ? input : null;
  const method = (init.method || request?.method || "GET").toUpperCase();
  const url = new URL(request ? request.url : input, window.location.href);
  if (
    csrfToken
    && url.origin === window.location.origin
    && ["POST", "PUT", "PATCH", "DELETE"].includes(method)
  ) {
    const headers = new Headers(init.headers || request?.headers);
    headers.set("X-CSRFToken", csrfToken);
    init = { ...init, headers };
  }
  return nativeFetch(input, init);
};

document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector("[data-menu-toggle]");
  const menu = document.querySelector("[data-menu]");
  if (toggle && menu) {
    toggle.addEventListener("click", () => {
      const opened = menu.classList.toggle("menu-open");
      toggle.setAttribute("aria-expanded", String(opened));
      toggle.setAttribute("aria-label", opened ? "Close navigation" : "Open navigation");
    });
  }
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelector("[data-print]")?.addEventListener("click", () => window.print());
});
