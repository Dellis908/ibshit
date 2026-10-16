#!/usr/bin/env node
/*
 * Regression test for the self-mark toggle.
 *
 * The bug this guards against: quiz.js read the "Self-mark" checkbox once at
 * load time, while the script is deferred and the box is unchecked by default.
 * Ticking it afterwards changed nothing, because the captured value stayed
 * false — the answer was submitted without `self_mark` and the mark-scheme
 * panel never appeared.
 *
 * Run with:  node scripts/test_self_mark.mjs
 * Needs jsdom (`npm install --no-save jsdom`).
 */

import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const QUIZ_JS = readFileSync(new URL("../app/static/js/quiz.js", import.meta.url), "utf8");
const TEMPLATE_HTML = readFileSync(new URL("../tests/fixtures/quiz_page.html", import.meta.url), "utf8");

let failures = 0;

function check(label, condition, detail = "") {
  const status = condition ? "PASS" : "FAIL";
  if (!condition) failures += 1;
  console.log(`[${status}] ${label}${condition || !detail ? "" : ` — ${detail}`}`);
}

/** Boot the quiz script against the fixture and capture every API call. */
async function run({ toggleChecked }) {
  const dom = new JSDOM(TEMPLATE_HTML, { runScripts: "outside-only" });
  const { window } = dom;
  const calls = [];

  window.fetch = (url, options) => {
    const body = options && options.body ? JSON.parse(options.body) : null;
    calls.push({ url, body });
    // Mirror the server's contract closely enough to exercise the client.
    const payload = url === "/api/answer"
      ? {
          is_correct: false,
          correct: false,
          marks_awarded: 0,
          marks_available: 3,
          feedback: "Partly there.",
          correct_answer: "glycolysis, link reaction, Krebs cycle",
          explanation: "Three stages.",
          attempt_id: 4242,
          verdict: "partial",
          score: 33.3,
          keywords_matched: ["glycolysis"],
          keywords_missed: ["Krebs cycle"],
          self_mark_available: body && body.self_mark === true,
        }
      : { id: 4242, marks_awarded: 3, marks_available: 3, is_correct: true };
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(payload),
    });
  };

  window.eval(QUIZ_JS);
  await new Promise((resolve) => setTimeout(resolve, 0));

  if (toggleChecked) window.document.getElementById("self-mark-toggle").checked = true;

  const card = window.document.querySelector(".question-card");
  const field = card.querySelector("[data-answer]");
  if (field.tagName === "TEXTAREA" || field.tagName === "INPUT") {
    field.value = "glycolysis and stuff";
  }
  const submit = card.querySelector("[data-submit]") || card.querySelector("button");
  submit.click();

  // Let the promise chain settle (submit -> feedback -> panel).
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setTimeout(resolve, 5));

  return { window, calls, card };
}

const on = await run({ toggleChecked: true });
const answerCall = on.calls.find((call) => call.url === "/api/answer");

check("the answer request is sent", Boolean(answerCall), JSON.stringify(on.calls.map((c) => c.url)));
check(
  "ticking the box submits self_mark: true",
  Boolean(answerCall && answerCall.body && answerCall.body.self_mark === true),
  answerCall ? JSON.stringify(answerCall.body) : "no request"
);
const onPanel = on.card.querySelector("[data-self-mark]");
check(
  "the mark-scheme panel appears once the box is ticked",
  Boolean(onPanel && !onPanel.hasAttribute("hidden")),
  onPanel ? `hidden=${onPanel.hasAttribute("hidden")}` : "no panel element"
);

const off = await run({ toggleChecked: false });
const offAnswer = off.calls.find((call) => call.url === "/api/answer");
check(
  "leaving the box unticked submits self_mark: false",
  Boolean(offAnswer && offAnswer.body && offAnswer.body.self_mark === false),
  offAnswer ? JSON.stringify(offAnswer.body) : "no request"
);
check(
  "no self-mark panel when the box is unticked",
  !off.card.querySelector("[data-self-mark-body]")?.children.length,
);

console.log("");
if (failures) {
  console.log(`${failures} CHECK(S) FAILED`);
  process.exit(1);
}
console.log("ALL SELF-MARK CHECKS PASSED");
