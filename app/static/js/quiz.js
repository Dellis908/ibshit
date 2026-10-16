/* Quiz runner: submission, marking feedback, scoring, timer and restore. */
(function () {
  "use strict";

  const configNode = document.getElementById("quiz-config");
  const config = configNode ? JSON.parse(configNode.textContent) : {};
  const apiFetch = (window.QuizApp && window.QuizApp.apiFetch) || fallbackFetch;

  const list = document.getElementById("question-list");
  if (!list) return;

  const cards = Array.from(list.querySelectorAll(".question-card"));
  const progressFill = document.getElementById("quiz-progress");
  const progressLabel = document.getElementById("quiz-progress-label");
  const scoreCorrect = document.getElementById("score-correct");
  const scoreTotal = document.getElementById("score-total");
  const finishButton = document.getElementById("finish-btn");
  const hiddenMode = config.mode === "exam" || config.mode === "timed";

  const state = { answered: 0, correct: 0, timerId: null, expired: false };
  const STORAGE_KEY = `quiz:${config.mode}:${window.location.pathname}${window.location.search}`;

  /* ------------------------------------------------------------- utilities */

  function fallbackFetch(url, options) {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign(
      { "Content-Type": "application/json" },
      opts.headers,
      meta ? { "X-CSRF-Token": meta.content } : {}
    );
    return fetch(url, opts).then((response) =>
      response.json().then((body) => {
        if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
        return body;
      })
    );
  }

  const selfMarkToggle = document.getElementById("self-mark-toggle");

  /**
   * Whether the user wants to self-mark right now.
   *
   * Read on demand rather than captured at load: this script is deferred and
   * the box starts unchecked, so a captured value would pin it to false for the
   * whole session and the toggle would silently do nothing.
   */
  function selfMarkChosen() {
    return Boolean(selfMarkToggle && selfMarkToggle.checked);
  }

  function getAnswer(card) {
    const type = card.dataset.type;
    if (type === "multiple_choice" || type === "true_false") {
      const checked = card.querySelector("[data-answer]:checked");
      return checked ? checked.value : "";
    }
    const field = card.querySelector("[data-answer]");
    return field ? field.value.trim() : "";
  }

  function clearAnswer(card) {
    card.querySelectorAll("[data-answer]").forEach((node) => {
      if (node.type === "radio" || node.type === "checkbox") node.checked = false;
      else node.value = "";
    });
  }

  function setProgress() {
    const total = cards.length;
    const pct = total ? Math.round((state.answered / total) * 100) : 0;
    if (progressFill) progressFill.style.width = `${pct}%`;
    if (progressLabel) {
      progressLabel.textContent = `${state.answered} of ${total} answered`;
    }
    if (scoreCorrect) scoreCorrect.textContent = String(state.correct);
    if (scoreTotal) scoreTotal.textContent = String(total);
  }

  function saveState() {
    try {
      const payload = {
        answered: state.answered,
        correct: state.correct,
        responses: cards
          .filter((card) => card.classList.contains("is-answered"))
          .map((card) => ({ id: card.dataset.questionId, answer: getAnswer(card) })),
      };
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (err) {
      /* storage may be unavailable (private mode) — progress simply is not restored */
    }
  }

  function restoreState() {
    let saved = null;
    try {
      saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
    } catch (err) {
      saved = null;
    }
    if (!saved || !Array.isArray(saved.responses)) return;

    saved.responses.forEach((entry) => {
      const card = list.querySelector(`.question-card[data-question-id="${entry.id}"]`);
      if (!card || !entry.answer) return;
      if (card.dataset.type === "multiple_choice" || card.dataset.type === "true_false") {
        const radio = Array.from(card.querySelectorAll("[data-answer]")).find(
          (node) => node.value === entry.answer
        );
        if (radio) radio.checked = true;
      } else {
        const field = card.querySelector("[data-answer]");
        if (field) field.value = entry.answer;
      }
    });
  }

  /* ------------------------------------------------------------- feedback */

  function showFeedback(card, data, answer) {
    const box = card.querySelector("[data-feedback]");
    if (!box) return;

    const verdict = data.verdict || (data.correct ? "correct" : "wrong");
    const verdictClass =
      verdict === "correct" ? "is-correct" : verdict === "wrong" ? "is-wrong" : "is-partial";

    card.classList.add("is-answered", `is-${verdict === "correct" ? "correct" : verdict === "wrong" ? "wrong" : "partial"}`);
    box.hidden = false;
    box.className = `feedback ${verdictClass}`;
    box.textContent = "";

    const head = document.createElement("div");
    head.className = "feedback-head";
    const icons = { correct: "✓", partial: "◐", close: "≈", wrong: "✗" };
    head.textContent = `${icons[verdict] || "•"} ${data.feedback || ""}`.trim();
    box.appendChild(head);

    const marks = document.createElement("div");
    marks.className = "small muted";
    marks.textContent = `${data.marks_awarded} of ${data.marks_available} mark${
      data.marks_available === 1 ? "" : "s"
    } awarded`;
    box.appendChild(marks);

    // Multiple choice: colour the chosen and correct options.
    if (card.dataset.type === "multiple_choice" || card.dataset.type === "true_false") {
      card.querySelectorAll(".choice").forEach((label) => {
        const input = label.querySelector("input");
        if (!input) return;
        input.disabled = true;
        if (input.value === data.correct_answer) label.classList.add("is-correct");
        else if (input.checked) label.classList.add("is-wrong");
      });
    }

    // Mark-scheme keywords for written answers.
    const matched = data.keywords_matched || [];
    const missed = data.keywords_missed || [];
    if (matched.length || missed.length) {
      const listNode = document.createElement("ul");
      listNode.className = "keyword-list";
      matched.forEach((point) => {
        const item = document.createElement("li");
        item.className = "hit";
        item.textContent = `✓ ${point}`;
        listNode.appendChild(item);
      });
      missed.forEach((point) => {
        const item = document.createElement("li");
        item.className = "miss";
        item.textContent = `✗ ${point}`;
        listNode.appendChild(item);
      });
      box.appendChild(listNode);
    }

    // The model answer, when the mode permits revealing it.
    if (data.correct_answer) {
      const dl = document.createElement("dl");
      const dt = document.createElement("dt");
      dt.textContent = "Mark scheme";
      const dd = document.createElement("dd");
      dd.className = "good";
      dd.textContent = data.correct_answer;
      dl.append(dt, dd);

      if (data.explanation) {
        const dt2 = document.createElement("dt");
        dt2.textContent = "Why";
        const dd2 = document.createElement("dd");
        dd2.textContent = data.explanation;
        dl.append(dt2, dd2);
      }
      box.appendChild(dl);
    } else if (hiddenMode) {
      const reveal = document.createElement("button");
      reveal.type = "button";
      reveal.className = "btn btn-ghost btn-small";
      reveal.textContent = "Show answer";
      reveal.addEventListener("click", () => {
        reveal.disabled = true;
        apiFetch("/api/answer", {
          method: "POST",
          body: JSON.stringify({
            question_id: Number(card.dataset.questionId),
            answer: answer,
            mode: config.mode,
            reveal: true,
          }),
        })
          .then((revealed) => showFeedback(card, revealed, answer))
          .catch(() => {
            reveal.disabled = false;
          });
      });
      box.appendChild(reveal);
    }

    // Self-marking panel for multi-mark written answers. Also offered when the
    // toggle is switched on after answering, which is when most people reach
    // for it.
    if (data.attempt_id && data.marks_available > 1 && selfMarkChosen()) {
      buildSelfMark(card, data);
    }
  }

  function buildSelfMark(card, data) {
    const details = card.querySelector("[data-self-mark]");
    const body = card.querySelector("[data-self-mark-body]");
    if (!details || !body) return;

    body.textContent = "";
    const available = data.marks_available;

    const select = document.createElement("select");
    for (let mark = 0; mark <= available; mark += 1) {
      const option = document.createElement("option");
      option.value = String(mark);
      option.textContent = `${mark} / ${available} marks`;
      if (mark === data.marks_awarded) option.selected = true;
      select.appendChild(option);
    }

    const row = document.createElement("div");
    row.className = "self-mark-row";
    const label = document.createElement("label");
    label.className = "points";
    label.textContent = "Marks I deserve: ";
    label.appendChild(select);

    const save = document.createElement("button");
    save.type = "button";
    save.className = "btn btn-small";
    save.textContent = "Save";
    save.addEventListener("click", () => {
      save.disabled = true;
      apiFetch(`/api/attempts/${data.attempt_id}/override`, {
        method: "POST",
        body: JSON.stringify({ marks_awarded: Number(select.value) }),
      })
        .then(() => {
          save.textContent = "Saved";
        })
        .catch(() => {
          save.disabled = false;
          save.textContent = "Retry";
        });
    });

    row.append(label, save);
    body.appendChild(row);
    details.hidden = false;

    if (data.correct_answer) {
      const scheme = document.createElement("p");
      scheme.className = "small muted";
      scheme.textContent = `Mark scheme: ${data.correct_answer}`;
      body.appendChild(scheme);
    }
  }

  /* ------------------------------------------------------------ submission */

  function submitCard(card) {
    const answer = getAnswer(card);
    const hint = card.querySelector("[data-hint]");
    const submit = card.querySelector("[data-submit]");

    if (!answer) {
      if (hint) hint.textContent = "Write or choose an answer first.";
      return;
    }
    if (hint) hint.textContent = "";
    if (submit) submit.disabled = true;

    apiFetch("/api/answer", {
      method: "POST",
      body: JSON.stringify({
        question_id: Number(card.dataset.questionId),
        answer: answer,
        mode: config.mode,
        self_mark: selfMarkChosen() && card.dataset.type === "short_answer",
      }),
    })
      .then((data) => {
        if (submit) submit.disabled = false;
        const firstAnswer = !card.classList.contains("is-answered");
        showFeedback(card, data, answer);
        if (firstAnswer) {
          state.answered += 1;
          if (data.correct) state.correct += 1;
        }
        setProgress();
        saveState();
      })
      .catch((error) => {
        if (submit) submit.disabled = false;
        const box = card.querySelector("[data-feedback]");
        if (box) {
          box.hidden = false;
          box.className = "feedback is-error";
          box.textContent = `Could not check that answer: ${error.message}`;
        }
      });
  }

  cards.forEach((card) => {
    const submit = card.querySelector("[data-submit]");
    if (submit) submit.addEventListener("click", () => submitCard(card));

    const skip = card.querySelector("[data-skip]");
    if (skip) {
      skip.addEventListener("click", () => {
        const next = card.nextElementSibling;
        clearAnswer(card);
        if (next) next.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }

    // Option buttons submit immediately — fewer clicks per question.
    card.querySelectorAll(".choice input").forEach((input) => {
      input.addEventListener("change", () => {
        if (!card.classList.contains("is-answered")) submitCard(card);
      });
    });

    // Ctrl/Cmd+Enter submits a written answer.
    const field = card.querySelector("textarea[data-answer]");
    if (field) {
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          submitCard(card);
        }
      });
    }
  });

  /* ----------------------------------------------------------------- timer */

  function formatTime(totalSeconds) {
    const seconds = Math.max(0, totalSeconds);
    const minutes = Math.floor(seconds / 60);
    return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function startTimer() {
    const node = document.getElementById("quiz-timer");
    const value = document.getElementById("timer-value");
    if (!node || !value || !config.timeLimit) return;

    let deadline = Date.now() + config.timeLimit * 1000;
    let warned = false;

    function render() {
      const remaining = Math.max(0, Math.round((deadline - Date.now()) / 1000));
      value.textContent = formatTime(remaining);
      node.classList.toggle("is-low", remaining <= 60);
      return remaining;
    }

    function tick() {
      const remaining = render();
      if (remaining > 0) return;

      window.clearInterval(state.timerId);
      state.timerId = null;
      node.classList.add("is-low");

      if (!warned) {
        warned = true;
        const finish = window.confirm("Time is up. Finish the quiz and see your results?");
        if (finish) {
          window.location.href = finishButton ? finishButton.href : "/results";
          return;
        }
        // Declined: allow a short grace period, then stop the clock for good.
        deadline = Date.now() + 120 * 1000;
        state.timerId = window.setInterval(tick, 1000);
        render();
        return;
      }
      state.expired = true;
    }

    state.timerId = window.setInterval(tick, 1000);
    render();
  }

  if (finishButton) {
    finishButton.addEventListener("click", (event) => {
      const unanswered = cards.length - state.answered;
      if (unanswered > 0 && !state.expired) {
        const proceed = window.confirm(
          `${unanswered} question${unanswered === 1 ? " is" : "s are"} still unanswered. Finish anyway?`
        );
        if (!proceed) event.preventDefault();
        else try { window.localStorage.removeItem(STORAGE_KEY); } catch (err) { /* ignore */ }
      }
    });
  }

  /* ------------------------------------------------------------- keyboard */

  /* Letters are only shortcuts when they are not being typed.
   *
   * Without this, pressing "k" while writing an answer jumped to the previous
   * question, because the handler below listened globally and a textarea does
   * not stop keydown from reaching document.
   */
  function isTyping(target) {
    if (!target || !target.tagName) return false;
    if (target.isContentEditable) return true;
    const tag = target.tagName.toLowerCase();
    if (tag === "textarea" || tag === "select") return true;
    if (tag === "input") {
      // Checkboxes and radio buttons do not consume letters, so shortcuts may
      // still work while one of those has focus.
      const type = (target.getAttribute("type") || "text").toLowerCase();
      return type !== "checkbox" && type !== "radio" && type !== "button";
    }
    return false;
  }

  /* The question nearest the top of the viewport, which is the one being read.
   * Using focus instead would break as soon as the user clicked a blank area. */
  function currentCardIndex() {
    let best = 0;
    let bestDistance = Infinity;
    cards.forEach((card, index) => {
      const distance = Math.abs(card.getBoundingClientRect().top);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = index;
      }
    });
    return best;
  }

  function goToCard(index) {
    if (index < 0 || index >= cards.length) return;
    cards[index].scrollIntoView({ behavior: "smooth", block: "center" });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") return;
    if (event.ctrlKey || event.metaKey) return;

    const key = event.key.toLowerCase();
    if (key !== "j" && key !== "k") return;

    // Alt+J / Alt+K always navigate, which is the escape hatch for someone who
    // is typing and still wants to move between questions.
    if (isTyping(event.target) && !event.altKey) return;

    event.preventDefault();
    const index = currentCardIndex();
    goToCard(key === "j" ? index + 1 : index - 1);
  });

  /* ----------------------------------------------------------------- init */

  restoreState();
  setProgress();
  startTimer();
})();
