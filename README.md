# IB Biology Revision

A self-hosted revision app for IB Biology (HL & SL). It serves syllabus topic
questions, marks your answers automatically against a mark scheme, records every
attempt, and shows you exactly which topics are still shaky.

Originally a small Flask experiment; rebuilt into a complete application with a
real question bank, a marking engine with partial credit, progress tracking and
a working UI.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Flask](https://img.shields.io/badge/flask-3.0-black)

---

## Quick start

```bash
git clone https://github.com/Dellis908/ibshit.git
cd ibshit

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

flask quiz seed                    # load the bundled question bank
flask run                          # http://127.0.0.1:5000
```

That is all that is needed — no database server, no configuration file. The app
creates `instance/quiz.db` (SQLite) on first run and loads 154 questions across
10 syllabus topics.

### Docker

```bash
docker compose up --build          # http://localhost:8000
```

---

## What's in it

| Area | What you get |
| --- | --- |
| **Question bank** | 154 built-in questions plus 2,239 imported from the rdojo bank, covering the complete 2025 syllabus (A1.1 → D4.3) across 40 subtopics |
| **Question types** | Multiple choice, true/false, calculation, and written answers with multi-point mark schemes |
| **Six study modes** | Practice, Timed, Exam simulation, Weak topics, Never attempted, Random mix |
| **Marking** | Instant marking, partial credit, per-marking-point feedback, and a self-marking panel for longer prose |
| **Accounts** | Email + password sign-in; progress, accuracy and history are stored per account and are invisible to other users |
| **Progress** | Per-topic accuracy and coverage, recent answers, resettable history |
| **API** | JSON endpoints for every page, plus import/export of the whole bank |
| **Optional importers** | Turn the rdojo question bank (JSON + Git LFS) or your own PDFs into importable questions |

### Study modes

| Mode | Behaviour |
| --- | --- |
| **Practice** | Instant marking with an explanation after every question. |
| **Timed** | One minute per mark against a countdown. |
| **Exam simulation** | Answers and explanations stay hidden until you finish. |
| **Weak topics** | Draws from the topics where your accuracy is lowest. |
| **Never attempted** | Only questions you have never seen, for full coverage. |
| **Random mix** | A shuffled, topic-balanced selection. |

---

## How marking works

Marking lives in `app/grading.py` and is deliberately conservative: it would
rather flag an answer as "close" than award a mark it is not sure about.

- **Multiple choice / true-false** — exact match after normalisation. You may
  also answer with a letter (`B`).
- **Calculations** — compared with a 1% relative tolerance, with SI prefixes
  converted, so `25 cm3` matches `0.000025 m3` and `0.0025 mol s-1` matches
  `0.0025 mol s⁻¹` despite spacing or Unicode differences. The right number with
  the wrong unit earns partial credit; a bare number with no unit earns nothing.
- **Written answers** — the model answer is split into marking points (on `;`),
  and each point is credited only when its distinctive vocabulary is present.
  Tokens are consumed as they are matched, so one phrase cannot earn marks for
  two different points, and partial keyword matches are not credited ("increases
  then drops" does not earn the mark that "increases then decreases" earns). You
  get partial credit per point covered, a list of the points you missed, and a
  self-marking panel.

Automated prose marking is a study aid, not an examiner. For long answers, check
your wording against the mark scheme with the self-marking panel.

---

## Commands

```bash
flask quiz init                  # create tables and syllabus topics
flask quiz seed                  # load seed/questions.json
flask quiz check [FILE]          # validate a question file (default: the seed)
flask quiz import FILE.json      # import questions (--dry-run to validate only)
flask quiz export out.json       # export the bank (--topic, --no-answers, --stats)
flask quiz stats                 # print bank and progress statistics
flask quiz reset [--yes]         # delete recorded attempts (--questions too)
```

Tests and verification:

```bash
python -m pytest tests -q        # unit and integration tests
python scripts/smoke_test.py     # boots the app and exercises every route
```

---

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/topics` | Topics with your progress |
| `GET` | `/api/questions` | Filter the bank (`topic`, `difficulty`, `type`, `limit`, `offset`, `include_answers`) |
| `GET` | `/api/stats` | Overall and per-topic statistics |
| `POST` | `/api/answer` | Submit one answer and get it marked |
| `POST` | `/api/attempts/<id>/override` | Apply a self-marking decision |
| `GET` | `/api/result` | Summary of the current quiz run |
| `POST` | `/api/reset` | Delete recorded attempts (`confirm: "RESET"`) |
| `GET` | `/api/export` | Export the bank as JSON |
| `POST` | `/api/import` | Import questions (`?dry_run=true` to validate) |
| `GET` | `/healthz` | Liveness probe |

State-changing endpoints require the CSRF token issued in the page's
`<meta name="csrf-token">` tag, sent as the `X-CSRF-Token` header. Set the
`API_KEY` environment variable to additionally require `X-API-Key` on
import/export.

```bash
curl -X POST http://localhost:5000/api/import \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: <token from the page>" \
  -d '{"questions": [{
        "topic": "genetics",
        "type": "short_answer",
        "question_text": "State the role of helicase in DNA replication.",
        "correct_answer": "Unwinds the DNA double helix",
        "marks": 1,
        "explanation": "Helicase breaks the hydrogen bonds between base pairs.",
        "difficulty": "easy",
        "tags": ["replication"]
      }]}'
```

---

## Keyboard shortcuts

While taking a quiz:

| Key | Action |
| --- | --- |
| `J` / `K` | next / previous question |
| `Alt`+`J` / `Alt`+`K` | the same, but still works while typing in an answer |
| `Ctrl`/`Cmd`+`Enter` | check the answer you are writing |

Plain `J` and `K` are deliberately ignored while a text field has focus, so
typing an answer that contains those letters cannot jump you to another question.
`Alt` is the escape hatch for moving between questions mid-sentence.

The shortcut handling is covered by `scripts/test_keyboard.mjs`, which drives the
real `quiz.js` in a DOM and fails if a typed letter navigates:

```bash
npm install --no-save jsdom
node scripts/test_keyboard.mjs
```

## Accounts and access

Everything is per account: two people using the same server cannot see each
other's answers, accuracy or history. The first account created becomes an
**administrator** and may import or export the question bank; other accounts can
only study.

| Property | How it is handled |
| --- | --- |
| Password storage | Werkzeug hashes (scrypt), salted per user. Plaintext is never stored or logged. |
| Sign-in failures | One generic message for both a wrong password and an unknown email, so the form cannot be used to discover who has an account. |
| Brute force | Rate limited per client address: 8 sign-ins / 5 min, 5 sign-ups / hour, 5 password changes / 15 min. |
| Session fixation | The session identifier is rotated on sign-in and cleared on sign-out. |
| CSRF | Applied globally to every state-changing request, so a cross-site form post cannot change a password or delete an account. |
| Progress isolation | Every progress query is scoped to the signed-in account; "unseen" means unseen *by you*. |
| Bank changes | `/api/import` and `/api/export` require an administrator. |

Controlling who can register:

```bash
# in /etc/ibshit.env
ALLOW_REGISTRATION=0          # closes the sign-up page, existing accounts still work
REGISTRATION_CODE=some-secret # keeps registration open but requires this code
```

Managing accounts from the command line:

```bash
flask auth list                              # accounts and how much each has answered
flask auth create you@example.com --admin    # create one non-interactively
flask auth passwd you@example.com            # reset a password
flask auth promote you@example.com           # grant administrator rights
flask auth adopt-attempts you@example.com    # take ownership of pre-accounts history
```

Upgrading an existing installation: a database created before accounts existed is
migrated in place on startup — the `user_id` column is added to `attempt` and old
rows are kept but left unowned, since attributing them to an account that did not
exist would be a guess. `flask auth adopt-attempts <email>` hands them to an
account if that is what you want. `app/db_migrate.py` only ever adds columns; it
never drops or rewrites data.

## Adding your own questions

`seed/questions.json` is the source of truth for the shipped bank, and imports
accept plain JSON or JSON Lines:

```json
{
  "meta": { "syllabus": "IB Biology", "version": 1 },
  "questions": [
    {
      "topic": "cell-biology",
      "type": "multiple_choice",
      "question_text": "Which organelle is the site of aerobic respiration?",
      "correct_answer": "Mitochondrion",
      "choices": ["Mitochondrion", "Ribosome", "Golgi apparatus", "Nucleus"],
      "marks": 1,
      "explanation": "The mitochondrion carries out oxidative phosphorylation.",
      "tags": ["organelles"],
      "difficulty": "easy"
    }
  ]
}
```

Rules the importer enforces (all covered by tests):

- `question_text` and `correct_answer` are required and must be non-empty.
- `type` is one of `multiple_choice`, `short_answer`, `true_false`, `calculation`.
- `difficulty` is one of `easy`, `medium`, `hard`.
- `marks` is an integer from 1 to 20.
- `multiple_choice` needs at least 2 distinct choices, one of which is the
  correct answer.
- `true_false` answers must be exactly `True` or `False`.
- HTML is sanitised on import: formatting (`<sub>`, `<sup>`, `<table>`, …)
  survives; scripts, event handlers, styles and `javascript:` URLs are removed.
- Questions whose text already exists are skipped, so re-importing is safe.

Unknown topic names are mapped onto the nearest syllabus topic where possible,
otherwise stored as their own topic rather than being discarded.

---

## Project layout

```
app/
  __init__.py      application factory, error handlers, template filters
  api.py           JSON API blueprint (marking, import/export, stats)
  routes.py        HTML pages (dashboard, topics, quiz, results, progress)
  models.py        Topic, Question, Attempt
  grading.py       the marking engine (pure, standard library only)
  content.py       validation, sanitising, import/export, topic mapping
  quiz.py          quiz assembly, session summaries, progress statistics
  sanitize.py      allow-list HTML sanitiser for question content
  topics.py        the syllabus topic catalogue
  cli.py           the `flask quiz` command group
  templates/       Jinja templates
  static/          stylesheet, quiz runner, shared JS
scraper/           optional importers: rdojo bank + PDF pipeline (see below)
seed/              questions.json — the bundled question bank
scripts/           smoke_test.py — end-to-end route check
tests/             pytest suite
```

---

## Importing more questions

Two importers are included. Neither is needed to run the app; the built-in 154
questions work out of the box.

### The rdojo question bank (recommended)

`git.pirateib.sh/pirateIB/rdojo` publishes a complete IB Biology bank for the
2025 syllabus as JSON stored in Git LFS. `scraper/rdojo_import.py` resolves the
LFS pointers, downloads the banks and converts them into the import format:

```bash
python -m scraper.rdojo_import list                     # the 40 subtopics
python -m scraper.rdojo_import fetch --limit 3          # try a few topics
python -m scraper.rdojo_import fetch --check-images     # the full bank
flask quiz check seed/rdojo.json
flask quiz import seed/rdojo.json
```

A full run converts roughly **2,240 questions across 40 subtopics** and takes a
couple of minutes. Everything is cached under `data/rdojo/`, so re-running only
re-does what changed.

**The converted bank is committed** as `seed/rdojo.json`, so you can skip the
fetch entirely and go straight to importing it:

```bash
flask quiz import seed/rdojo.json
```

What the converter does:

| Upstream | Here |
| --- | --- |
| `questionType: MC` (4 options, one marked correct) | a `multiple_choice` question |
| `questionType: LA` (multi-part, `markscheme` with `<marks>n marks</marks>`) | one `short_answer` per part, one marking point per `;` so partial credit works |
| LaTeX (`$CO_2$`, `\frac`, `\rightarrow`) and Markdown | HTML the templates render |
| `difficulty` 1–8 | easy / medium / hard |
| `labeledSubSubTopics` (`A1.1.4`) | tags plus a real syllabus topic |

Four data-quality problems are handled automatically, because the upstream data
has them:

- **Cross-topic duplicates.** The same question is listed under several related
  subtopics. Duplicates are dropped so a mixed quiz never shows one twice.
- **Dead images.** Around 96% of the referenced image URLs now return 404. With
  `--check-images` every URL is probed first: broken tags are stripped, and
  questions that genuinely needed the diagram are dropped rather than shipped
  unanswerable. The result contains **no reference to a missing figure or table**.
- **Same stem, different question.** Two entries can share a stem with different
  options or marks. The deduplication key includes the answer, the options and
  the mark allocation, so a legitimate question is never dropped.
- **Images on retired hosts.** The bank's figures live on object storage that has
  been migrated (the current host, a legacy `s.revisiondojo.com` mirror, and a
  Supabase project that no longer resolves). `app/images.py` derives every
  candidate URL for an image and both the importer and the `/api/image` proxy try
  them in turn, so an image that 404s on its recorded URL is still served from a
  mirror when one exists. An image that is gone everywhere returns 404 with a
  reason, and the client shows a placeholder.

  This is a data problem, not a download problem: the LFS payloads verify against
  their content hashes, and only 19 of 475 image URLs are still served by any
  mirror — the objects were deleted at the source, not mis-fetched.

Two things to know about this source:

- This is third-party content, imported with the repository owner's permission.
  The converted output is committed, so check you are happy with the licensing
  position before publishing this repository publicly. The importer itself is
  independent of that: delete `seed/rdojo.json` and the app still runs on the
  154 built-in questions.
- The upstream bank is a snapshot. Re-running `fetch --refresh` picks up changes,
  and `--check-images` re-verifies the images each time.

### Your own PDFs

The `scraper/` package also turns topic-question PDFs into the same format. Its
dependencies are deliberately separate:

```bash
pip install -r scraper/requirements.txt

python -m scraper.scrape links       # list PDFs found on the source page
python -m scraper.scrape download    # download them (cached)
python -m scraper.scrape parse       # extract text (pdfplumber or PyMuPDF)
python -m scraper.scrape build       # text -> seed/scraped.json + review file
python -m scraper.scrape all         # the whole pipeline
```

Already have text? Skip the network entirely:

```bash
python -m scraper.scrape build --from-text data/extracted/cell_biology.txt --topic cell-biology
flask quiz check seed/scraped.importable.json
flask quiz import seed/scraped.importable.json
```

Each stage caches its output and is resumable. Every generated question carries a
`_confidence` score and a `_warnings` list, and the pipeline writes a
`*.review.md` file listing the low-confidence ones — review before trusting
them, because PDF question extraction is inherently messy.

> **Use these responsibly.** Automated access may breach a site's terms of
> service, and question banks are usually copyrighted. The importers only read
> public sources the way a browser would; what you point them at and what you do
> with the output is your responsibility.

## Configuration

Copy `.env.example` to `.env` and adjust as needed; every value is optional.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | dev value | Session signing. **Set a real value in production.** |
| `DATABASE_URL` | `sqlite:///instance/quiz.db` | Any SQLAlchemy URL (Postgres, MySQL, …) |
| `QUIZ_DEFAULT_COUNT` | `10` | Questions per quiz |
| `QUIZ_MAX_COUNT` | `60` | Upper bound on a quiz request |
| `QUIZ_FUZZY_THRESHOLD` | `72` | Similarity above which a written answer is flagged "close" |
| `QUIZ_NUMERIC_TOLERANCE` | `0.01` | Relative tolerance for calculations |
| `API_KEY` | unset | When set, required on import/export |

---

## Deployment

```bash
gunicorn --workers 4 --bind 0.0.0.0:8000 wsgi:app
```

`wsgi.py` creates missing tables and syllabus topics on boot, so a fresh
deployment needs no migration step. Behind a reverse proxy, terminate TLS there
and set `SECRET_KEY` plus (ideally) a Postgres `DATABASE_URL`.

**Full walkthrough:** see [DEPLOY.md](DEPLOY.md) for a step-by-step Debian 13
setup (systemd, nginx, HTTPS, PostgreSQL, backups, troubleshooting). Ready-made
config files live in [`deploy/`](deploy), and
`sudo bash deploy/bootstrap-debian.sh your.domain` automates the whole thing.

---

## Credits and licensing

Question content is original study material written for this project, not
official IB exam questions, and is not endorsed by the IB. Built with Flask,
Flask-SQLAlchemy and SQLAlchemy. See `LICENSE` for the project licence.

The original scraper targeted a third-party archive. That behaviour is retained
only as the optional, off-by-default pipeline described above, pointed at
whatever source you configure.
