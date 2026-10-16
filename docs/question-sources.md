# Question sources: state of play

Notes for whoever works on this next, so the findings are not rediscovered.

## The app today

Two banks ship in `seed/`:

| File | Questions | Images |
| --- | --- | --- |
| `questions.json` | 154 core, classic topic structure | none |
| `additional.json` | 2,857 IB-style: 2,244 on the 2025 syllabus (A1.1 → D4.3) plus 613 pirateIB Village Paper 1 items | 153 in `app/static/img/questions/` |

Everything kept is self-contained: nothing in `seed/` points at a missing
diagram. Of the 2,667 questions in the first conversion, **423 were dropped**
because they referred to a figure that could not be resolved; the Village import
is stricter still and only takes questions whose stem is complete without art,
or whose art was downloaded.

## pirateIB Village: the one source that works

`village.pirateib.su` serves a single-page app whose question data is
**AES-encrypted and decrypted in the browser**, so the plaintext never exists as
a static file — and the repository copy (`pirateIB/village`) is Git LFS on a
Cloudflare-challenged host, so neither the repo nor a mirror yields content.

What does work is running the real page in a headless browser and recording the
payloads the app hands to `JSON.parse`. See `scraper/village_import.py` for the
converter; the capture itself is a dozen lines of Puppeteer
(`page.evaluateOnNewDocument` patches `JSON.parse`, then navigate to
`#app/ib-biology/<level>/questionbank/<subtopic>/all`).

Useful facts about that source:

* Question payloads are `{questions: [...], topics: {subtopics: [...]}}`; each
  question carries `reference` (e.g. `BI0009`), `content`, `markScheme`,
  `maximumMark`, `difficulty`, `paper`, `images[]` and `solutions[]`.
* `markScheme` for Paper 1 questions starts with `:answer[**B**]`, which is the
  answer letter; the rest is the explanation.
* Images are described by a `path` such as
  `public/question/<uuid>/images/<uuid>/<name>.png`, and the CDN object is
  fetchable **without a signature** — the `url` field carries an expired
  CloudFront signature, but stripping the query string still returns the bytes.
* The 2024 programme is organised as SL/HL × topics; the 2025 programme as
  SL/HL × four themes. Village also carries maths, chemistry, physics,
  psychology, economics, business and ESS — the same shape this app will need
  when it grows past biology.

### What the import keeps

Only Paper 1 (one-mark, four-option) questions, because those are what this app
can mark faithfully. From 1,062 unique biology questions:

| Outcome | Count |
| --- | --- |
| kept | 613 |
| dropped: image lives in the *options* (needs a per-choice image schema) | 2 |
| dropped: options indistinguishable once art is removed | 4 |
| dropped: no four options in the text (table/figure-only) | 169 |
| dropped: stem needs a figure we could not fetch | — |
| dropped: not a one-mark Paper 1 item | 274 |

Images that belong to the stem are downloaded, deduplicated by content hash and
committed under `app/static/img/questions/<hash>.png`; the question carries the
relative path in `image_path`, which the quiz template already renders.

## Why the older bank's images are missing

The image URLs in the 2,244-question bank point at one content-addressed CDN
(`pub-images.revisiondojo.com`). Measured on the full set of 475 distinct URLs:

| Pattern | Alive | Dead |
| --- | --- | --- |
| `question-images/<id>.<ext>` | 4 | 303 |
| `question-images/<id>` | 15 | 132 |
| `plots/<id>` | 0 | 17 |
| `notes/<id>` | 0 | 4 |
| **total** | **19 (4.0%)** | **456 (96.0%)** |

All surviving URLs are content-addressed object ids — the ones whose bytes are
still on the CDN. There is no pattern to exploit: the objects were deleted, not
moved. `app/images.py` still derives every candidate URL (current host, legacy
mirror, extensionless variant) and both the importer and `/api/image` try them,
so the surviving 4% are served. Nothing else can be recovered from the URL alone.

## Access to the upstream repositories

`git.pirateib.sh` answers every web request with a Cloudflare challenge:

```
https://git.pirateib.sh/pirateIB/rdojo                 403  "Just a moment..."
https://git.pirateib.sh/pirateIB/village.git/info/lfs/objects/<oid>  403
https://dojo.pirateib.su/                              403
https://village.pirateib.su/                           200  (the SPA works)
```

* `git clone` still works (the git protocol is not challenged), but the trees
  only contain pointers: **neither repository has a directory of question
  images.** `village` is 245 files (177 `*.questionData.js` LFS pointers, fonts,
  one favicon, one `index.html`); the only images in `rdojo` are 330 revision
  cheatsheet JPGs under `public/cheatsheets/`.
* `git-lfs` cannot fetch the objects: the LFS batch API is an HTTP `POST` and
  gets the same `403`.
* `scraper/rdojo_import.py` can no longer download banks: it uses ordinary HTTP,
  which is now challenged. The data already committed is unaffected.
* The live SPA at `village.pirateib.su` is *not* challenged, which is why the
  Village import works while the repo import does not.

## Options, if more images matter

1. **Capture more of Village.** It is the only source that answers reliably. The
   remaining wins there are a per-choice image schema (2 questions here, more in
   other subjects) and the other seven subjects.
2. **Add images to questions we own.** The schema already supports `image_path`,
   served from `app/static/img/` — this is what the Village import uses.
3. **Ask the source** for the older bank's images, or a stable unauthenticated
   location for them. Anything else is reverse-engineering a moving target.
4. **Build the source selector.** Independent of images, and it is the piece that
   makes adding subjects (Chemistry, Physics, …) practical. Design it so a source
   can declare `has_images` and a `base_url`, and so the image proxy can be
   pointed at whatever store eventually exists.

## Verifying a new source

```bash
# How many questions, and how many depend on a figure?
flask quiz check candidate.json

# Does the figure actually resolve from a server (not just a browser)?
curl -sI "https://example.org/path/to/figure.png" | head -1
```

A `403` with `Server: cloudflare` means the host challenges automated requests;
treat the static source as unusable and look for a live app instead.
