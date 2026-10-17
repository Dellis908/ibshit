/* Shared helpers: CSRF-aware fetch, small DOM utilities, reset action. */
(function () {
  "use strict";

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');

  async function apiFetch(url, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign(
      { "Content-Type": "application/json" },
      opts.headers,
      csrfMeta ? { "X-CSRF-Token": csrfMeta.content } : {}
    );
    const response = await fetch(url, opts);
    let body = null;
    try {
      body = await response.json();
    } catch (err) {
      body = null;
    }
    if (!response.ok) {
      const message = (body && (body.error || body.message)) || `HTTP ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  /* ---------------------------------------------------------- reset button */
  const resetButton = document.getElementById("reset-progress");
  if (resetButton) {
    resetButton.addEventListener("click", async () => {
      const status = document.getElementById("reset-status");
      const confirmed = window.confirm(
        "Delete every recorded answer? This cannot be undone."
      );
      if (!confirmed) return;

      resetButton.disabled = true;
      if (status) status.textContent = "Deleting…";
      try {
        const result = await apiFetch("/api/reset", {
          method: "POST",
          body: JSON.stringify({ confirm: resetButton.dataset.confirm || "RESET" }),
        });
        if (status) status.textContent = `Deleted ${result.deleted} answers. Reloading…`;
        window.setTimeout(() => window.location.reload(), 700);
      } catch (error) {
        resetButton.disabled = false;
        if (status) status.textContent = `Could not reset: ${error.message}`;
      }
    });
  }

  /* ------------------------------------------------- broken question images */
  // Question images live on a third-party CDN and can disappear. When one fails
  // to load, replace it with a note so the question is not silently unanswerable.
  document.addEventListener(
    "error",
    (event) => {
      const img = event.target;
      if (!img || img.tagName !== "IMG") return;
      if (!img.closest(".question-text, .choice-text, .question-figure")) return;
      if (img.dataset.fallbackApplied) return;
      img.dataset.fallbackApplied = "1";

      const note = el("span", "img-missing", "Diagram unavailable");
      note.title = "This image could not be loaded from the question source.";
      img.replaceWith(note);
    },
    true // capture: image load errors do not bubble
  );

  window.QuizApp = { apiFetch, el };
})();

/* Theme toggle: remembers the choice, otherwise follows the system setting. */
(function () {
  "use strict";
  var toggles = Array.prototype.slice.call(document.querySelectorAll(".theme-toggle"));
  if (!toggles.length) return;

  function current() {
    var explicit = document.documentElement.dataset.theme;
    if (explicit === "light" || explicit === "dark") return explicit;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    toggles.forEach(function (button) {
      button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    });
  }

  apply(current());

  toggles.forEach(function (button) {
    button.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      apply(next);
      try {
        localStorage.setItem("theme", next);
      } catch (e) { /* ignore: the theme still applies for this page */ }
    });
  });
})();
