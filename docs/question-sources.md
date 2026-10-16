# Question sources: state of play

Notes for whoever works on this next, so the findings are not rediscovered.

## The app today

Two banks ship in `seed/`:

| File | Questions | Images |
| --- | --- | --- |
| `questions.json` | 154 core, classic topic structure | none |
| `additional.json` | 2,244 IB-style, 2025 syllabus (A1.1 → D4.3), 40 subtopics | 16 with a working figure, 467 with inline tables |

Of the 2,667 questions originally converted, **423 were dropped** because they
referred to a figure that could not be resolved. Everything kept is
self-contained: nothing in `seed/` points at a missing diagram.

## Why the images are missing

The image URLs in the bank point at one content-addressed CDN
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

## Why mirroring the upstream repositories does not help

Both `pirateIB/rdojo` and `pirateIB/village` were inspected directly.

**`village`** — 245 files total:

```
178  *.questionData.js   (Git LFS pointers, question content)
 61  fonts (woff, woff2, ttf, otf)
  1  src/favicon.png     (the site's own icon)
  1  index.html
```

`.gitattributes` confirms the tracking rule: `*.js`, `*.woff`, `*.woff2`, `*.ttf`
go through LFS. **There is no directory of question images.** The question data
only *references* CDN URLs.

**`rdojo`** — the only images in the tree are 330 JPGs under
`public/cheatsheets/`, which are revision cheatsheets, not question diagrams.
`public/questionbank/` and `public/quizzes/` contain JSON and nothing else.

So running a mirror of either site reproduces exactly the same page, pointing at
exactly the same dead URLs. A mirror is not a source of images; the images were
never in the repositories. To serve them, the *originals* would have to be put
somewhere — a separate asset store.

## Access to the upstream has changed

While this was being investigated, `git.pirateib.sh` began answering every web
request with a Cloudflare challenge:

```
https://git.pirateib.sh/pirateIB/rdojo                 403  "Just a moment..."
https://git.pirateib.sh/pirateIB/village.git/info/lfs/objects/<oid>  403
https://dojo.pirateib.su/                              403
https://pub-images.revisiondojo.com/                   400  (alive)
```

The same API calls succeeded earlier in the session, so this is a change on their
side, not a mistake in the requests. Consequences:

* `git clone` still works (the git protocol is not challenged), but it only
  yields pointers — and `git-lfs` is not installed here, so blob contents cannot
  be fetched.
* `scraper/rdojo_import.py` can no longer download banks: it uses ordinary HTTP,
  which is now challenged.
* A headless Firefox is also stopped by the challenge (`"Cierpliwości..."`), so
  the images cannot be verified in a browser from a server either.

The data already committed is unaffected: `seed/additional.json` is complete and
self-contained, because it was fetched before this changed.

## Options, if images matter

1. **Ask the source.** The simplest and most reliable: request either the image
   files or a stable, unauthenticated location for them. Anything else is
   reverse-engineering a moving target.
2. **Add images to questions we own.** The schema already supports `image_path`,
   served from `app/static/img/`. Nothing needs to change in the app to use it —
   it is simply unused because no images ship today.
3. **Accept the coverage.** 84% of the converted bank was retained, and 82% of
   those questions never needed a figure. The table questions still work.
4. **Build the source selector.** Independent of images, and it is the piece that
   makes adding subjects (Chemistry, Physics, …) practical. Design it so a source
   can declare `has_images` and a `base_url`, and so the image proxy can be
   pointed at whatever store eventually exists.

For a future subject, the same rule applies: a source is only useful if its
diagrams resolve. Check that before converting anything.

## Verifying a new source

```bash
# How many questions, and how many depend on a figure?
flask quiz check candidate.json

# Does the figure actually resolve from a server (not just a browser)?
curl -sI "https://example.org/path/to/figure.png" | head -1
```

A `403` with `Server: cloudflare` means the host challenges automated requests;
treat the source as unusable for import unless access is arranged.
