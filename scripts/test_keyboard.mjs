#!/usr/bin/env node
/*
 * Regression test for the quiz keyboard shortcuts.
 *
 * The bug this guards against: pressing "k" while writing an answer jumped to
 * the previous question, because the j/k handler listened on document and did
 * not check whether the event came from a text field. A textarea does not stop
 * keydown from reaching document, so every letter typed into an answer was also
 * a navigation shortcut.
 *
 * Run with:  node scripts/test_keyboard.mjs
 * Needs jsdom (`npm install --no-save jsdom`).
 */

import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const QUIZ_JS = readFileSync(new URL("../app/static/js/quiz.js", import.meta.url), "utf8");

let failures = 0;
let checks = 0;

function check(label, condition, detail = "") {
  checks += 1;
  if (condition) {
    console.log(`[PASS] ${label}`);
  } else {
    failures += 1;
    console.log(`[FAIL] ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

/** Build a quiz page in jsdom and run quiz.js against it. */
function buildQuiz() {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="quiz-progress"></div>
      <div id="quiz-progress-label"></div>
      <span id="score-correct"></span>
      <span id="score-total"></span>
      <a id="finish-btn" href="/results"></a>
      <input type="checkbox" id="self-mark-toggle">
      <script id="quiz-config" type="application/json">{"mode":"practice","count":3}</script>
      <ol id="question-list">
        ${[1, 2, 3]
          .map(
            (n) => `<li class="question-card" data-question-id="${n}" data-type="short_answer" data-marks="1">
              <textarea data-answer id="a${n}"></textarea>
              <button data-submit></button>
              <div data-feedback hidden></div>
            </li>`,
          )
          .join("")}
      </ol>
    </body></html>`,
    { runScripts: "outside-only", pretendToBeVisual: true },
  );

  const { window } = dom;

  // Minimal stubs: this test is about the keyboard handler, not the network.
  window.fetch = async () => ({ ok: true, json: async () => ({}) });
  window.confirm = () => true;

  // Record scrolling so navigation can be observed.
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {
    window.__scrolledTo = this;
  };

  // jsdom has no layout engine: every getBoundingClientRect() is all zeros, which
  // would make "the card nearest the top of the viewport" always the first one.
  // Lay the cards out vertically so navigation can be tested properly.
  window.__scrollY = 0;
  Object.defineProperty(window, "scrollY", { get: () => window.__scrollY, configurable: true });
  window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
    const index = this.classList && this.classList.contains("question-card")
      ? [...window.document.querySelectorAll(".question-card")].indexOf(this)
      : 0;
    const top = index * 400 - window.__scrollY;
    return { top, bottom: top + 380, left: 0, right: 0, width: 800, height: 380, x: 0, y: top };
  };

  // cssText is not implemented in jsdom; the progress bar writes to it.
  Object.defineProperty(window.HTMLElement.prototype, "style", {
    get() {
      if (!this.__style) this.__style = new Proxy({}, { set: () => true, get: () => "" });
      return this.__style;
    },
    configurable: true,
  });

  window.eval(QUIZ_JS);
  return window;
}

function press(window, key, target, options = {}) {
  const event = new window.KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...options,
  });
  // jsdom sets target itself for dispatched events, so dispatch on the element.
  (target || window.document.body).dispatchEvent(event);
  return event;
}

// ---------------------------------------------------------------- the bug

{
  console.log("\n--- typing must not navigate ---");
  const window = buildQuiz();
  const textarea = window.document.getElementById("a1");
  textarea.focus();

  for (const letter of "kayak jokingly work".split("")) {
    const event = press(window, letter, textarea);
    if (letter === "k" || letter === "j") {
      check(
        `"${letter}" while typing is not consumed`,
        event.defaultPrevented === false,
        "the handler called preventDefault, so navigation was triggered",
      );
    }
  }

  // Write a real answer containing both shortcut letters and confirm it stays.
  const before = textarea.value;
  for (const letter of "kj".split("")) {
    textarea.value += letter;
    press(window, letter, textarea);
  }
  check(
    "an answer containing k and j is kept intact",
    textarea.value === `${before}kj`,
    `value is ${JSON.stringify(textarea.value)}`,
  );

  check(
    "no scrolling happened while typing",
    window.__scrolledTo === undefined,
    "the page scrolled on a typed letter",
  );
}

// ------------------------------------------------- uppercase and modifiers

{
  console.log("\n--- uppercase and modifier keys ---");
  const window = buildQuiz();
  const textarea = window.document.getElementById("a1");
  textarea.focus();

  const upper = press(window, "K", textarea);
  check("uppercase K while typing is not consumed", upper.defaultPrevented === false);

  const withCtrl = press(window, "k", textarea, { ctrlKey: true });
  check("Ctrl+K is left to the browser", withCtrl.defaultPrevented === false);

  const withMeta = press(window, "k", textarea, { metaKey: true });
  check("Cmd+K is left to the browser", withMeta.defaultPrevented === false);
}

// ------------------------------------------------------- navigation works

{
  console.log("\n--- navigation still works outside a text field ---");
  const window = buildQuiz();
  const cards = [...window.document.querySelectorAll(".question-card")];

  // Focus nothing: navigation should follow the card nearest the viewport.
  const first = press(window, "j", window.document.body);
  check("j from the page navigates", first.defaultPrevented === true);
  check("j scrolls to the second card", window.__scrolledTo === cards[1]);

  // Emulate having scrolled to the second card, then press k.
  window.__scrollY = 400;
  window.__scrolledTo = undefined;
  const second = press(window, "k", window.document.body);
  check("k from the page navigates", second.defaultPrevented === true);
  check("k scrolls back to the previous card", window.__scrolledTo === cards[0]);

  // After that scroll, the viewport is back at the first card: k must do nothing.
  window.__scrollY = 0;
  window.__scrolledTo = undefined;
  press(window, "k", window.document.body);
  check("k at the first card does nothing", window.__scrolledTo === undefined);
}

// --------------------------------------------------- typing escape hatch

{
  console.log("\n--- Alt+J / Alt+K while typing ---");
  const window = buildQuiz();
  const textarea = window.document.getElementById("a1");
  textarea.focus();

  const event = press(window, "j", textarea, { altKey: true });
  check("Alt+J navigates even from a text area", event.defaultPrevented === true);
}

// --------------------------------------------------- other focus targets

{
  console.log("\n--- other element types ---");
  const window = buildQuiz();

  const select = window.document.createElement("select");
  window.document.body.appendChild(select);
  select.focus();
  check("a select does not trigger navigation", press(window, "k", select).defaultPrevented === false);

  const checkbox = window.document.getElementById("self-mark-toggle");
  checkbox.focus();
  check(
    "a checkbox still allows navigation",
    press(window, "j", checkbox).defaultPrevented === true,
  );

  // A button consumes no letter input, so the shortcut should still work.
  const button = window.document.createElement("button");
  window.document.body.appendChild(button);
  button.focus();
  check("a focused button still allows navigation", press(window, "j", button).defaultPrevented === true);
}

console.log(`\n${"=".repeat(56)}`);
if (failures) {
  console.log(`${failures} of ${checks} checks FAILED`);
  process.exit(1);
}
console.log(`all ${checks} keyboard checks passed`);
