"""Import an IB Questionbank mirror (the sixth-edition HTML dump) into the app.

    python -m scraper.questionbank_import \
        --source "/Volumes/SSD/IB QUESTIONBANKS/6. Sixth Edition - 2025 Sciences/questionbank" \
        --bank 58-dp-chemistry-first-assessment-2025 \
        --subject chemistry --syllabus 2025 \
        --out seed/chemistry.json --topics seed/topics_chemistry.json \
        --images-dir app/static/img/chemistry

The mirror stores one HTML file per question under
``en/teachers/<user>/questionbanks/<bank>/question_node_trees/``. Each page
carries a metadata table (reference code, level, paper, command term, marks), the
question body and the mark scheme, plus a breadcrumb of the syllabus sections the
question is filed under. Figures are inline ``data:`` URIs, so they are extracted
to files rather than fetched.

Only questions this app can present faithfully are kept: multiple choice with
four lettered options (auto-marked) and written answers shown with their mark
scheme for self-marking.
"""

from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import html
import json
import re
from pathlib import Path

_H1 = re.compile(r"<h1>(.*?)</h1>", re.S)
_CRUMB_LINK = re.compile(r'<a href="(\d+)\.html">\s*([^<]+)</a>')
_INFO = re.compile(r"class='info_label'>([^<]+)</td>\s*<td class='info_value'>([^<]*)</td>")
_MAXMARK = re.compile(r"Maximum&nbsp;mark:&nbsp;(\d+)")
_DATA_URI = re.compile(r'<img[^>]+src="data:(image/[a-z+]+);base64,([^"]+)"')
_OPTION = re.compile(r"(?m)^\s*([A-D])[\.\)]\s*(.+?)\s*$")


def _block_start(raw: str, marker: str, lookback: int = 120) -> int:
    """Index of the opening ``<div`` in front of ``marker`` (or the marker)."""
    index = raw.find(marker)
    if index < 0:
        return -1
    opening = raw.rfind("<div", max(0, index - lookback), index)
    return opening if opening >= 0 else index


def _text(fragment: str) -> str:
    fragment = re.sub(r"(?is)<script.*?</script>", " ", fragment)
    fragment = re.sub(r"(?is)<style.*?</style>", " ", fragment)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</p>", "\n", fragment, flags=re.I)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", "", fragment))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def _slug(text: str, limit: int = 48) -> str:
    text = text.lower().replace("&", " and ").replace("—", "-").replace("–", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:limit].rstrip("-")


def _section_slug(theme_code: str, code: str, title: str) -> str:
    """A stable, readable slug such as ``chem-s11-particulate-nature``."""
    prefix = {"s": "s", "r": "r", "t": "t"}.get(theme_code[:1].lower(), "x")
    number = re.sub(r"[^0-9.]", "", code).replace(".", "")
    return f"chem-{prefix}{number}-{_slug(title, 40)}"[:80]


def _theme_of(code: str) -> tuple[str, str]:
    """``"Structure 1.1"`` -> ``("Structure 1", "Structure")``."""
    match = re.match(r"([A-Za-z]+)\s*(\d+)", code)
    if not match:
        return code, code
    return f"{match.group(1)} {match.group(2)}", match.group(1)


def read_syllabus(sections_dir: Path) -> dict[str, dict]:
    """Every syllabus section page, keyed by its numeric id."""
    sections: dict[str, dict] = {}
    for path in sorted(sections_dir.glob("*.html")):
        if path.name.startswith("._"):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        h1 = _H1.search(raw)
        title = _text(h1.group(1)) if h1 else ""
        before = raw[: h1.start()] if h1 else raw
        names = [html.unescape(name).strip() for _id, name in _CRUMB_LINK.findall(before)]
        depth = len(names)
        code = re.split(r"[—–-]", title, maxsplit=1)[0].strip() if title else ""
        sections[path.stem] = {
            "id": path.stem,
            "title": title,
            "code": code,
            "depth": depth,
            "names": names,
            "name": names[-1] if names else title,
        }
    return sections


def build_topics(sections: dict[str, dict], subject: str, syllabus: str) -> tuple[list[dict], dict[str, str]]:
    """Topics are the depth-2 sections; the theme is their depth-1 parent."""
    topics: dict[str, dict] = {}
    by_id: dict[str, str] = {}
    for section in sections.values():
        if section["depth"] != 2:
            continue
        # Empty placeholder sections ("[N/A]") carry no content or questions.
        if not section["title"] or "[N/A]" in section["title"]:
            continue
        theme_name = section["names"][0] if section["names"] else section["code"]
        theme_code, theme_letter = _theme_of(theme_name)
        # "Structure 1.1—Introduction..." -> code "Structure 1.1", title "Introduction..."
        code = section["code"]
        rest = section["title"][len(code):].lstrip("—–-: ").strip() or code
        slug = _section_slug(theme_letter, code, rest)
        topics[section["id"]] = {
            "slug": slug,
            "ref": code,
            "title": rest,
            "theme": theme_name,
            "subject": subject,
            "syllabus": syllabus,
            "position": len(topics),
        }
        by_id[section["id"]] = slug
    return list(topics.values()), by_id


def _pick_topic(linked_ids: list[str], sections: dict[str, dict], by_id: dict[str, str]) -> str | None:
    """The depth-2 section a question belongs to, preferring the deepest link."""
    best = None
    for section_id in linked_ids:
        section = sections.get(section_id)
        if section is None:
            continue
        if section["depth"] == 2 and section_id in by_id:
            return by_id[section_id]
        if best is None or section["depth"] > sections[best]["depth"]:
            best = section_id
    # A question filed only under a depth-3 section: walk up to its parent.
    if best is not None:
        for section_id in linked_ids:
            section = sections.get(section_id)
            if section and section["depth"] == 2 and section_id in by_id:
                return by_id[section_id]
    return None


def _save_figure(data: str, mimetype: str, images_dir: Path) -> str:
    """Write an inline figure to disk and return the path relative to static/."""
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/svg+xml": "svg"}.get(
        mimetype, "png"
    )
    raw = base64.b64decode(data)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    images_dir.mkdir(parents=True, exist_ok=True)
    target = images_dir / f"{digest}.{extension}"
    if not target.exists():
        target.write_bytes(raw)
    return f"img/{images_dir.name}/{target.name}"


def convert_question(
    raw: str, sections: dict[str, dict], by_id: dict[str, str], images_dir: Path
) -> tuple[dict | None, str]:
    """One question page -> (importable dict, reason-when-skipped)."""
    meta = {key.strip(): html.unescape(value).strip() for key, value in _INFO.findall(raw)}
    reference = meta.get("Reference code", "")
    if not reference:
        return None, "brak kodu referencyjnego"

    body_start = _block_start(raw, "qc_body")
    ms_start = _block_start(raw, "qc_markscheme")
    if body_start < 0 or ms_start < 0:
        return None, "brak treści lub markscheme"
    body_html = raw[body_start:ms_start]
    scheme_match = re.search(
        r"<div class='qc_markscheme.*?<div class='card-body'>(.*?)</div>\s*</div>", raw, re.S
    )
    ms_html = scheme_match.group(1) if scheme_match else raw[ms_start:]

    marks_match = _MAXMARK.search(raw)
    marks = int(marks_match.group(1)) if marks_match else 1
    body_text = _text(body_html)
    mark_scheme = _text(ms_html).replace("Markscheme", "", 1).strip()

    # A figure, when there is one, belongs to the stem.
    image_path = None
    figure = _DATA_URI.search(body_html)
    if figure:
        image_path = _save_figure(figure.group(2), figure.group(1), images_dir)
        body_text = body_text.replace("[diagram]", " ")

    options = _OPTION.findall(body_text)
    qtype = "short_answer"
    choices: list[str] = []
    answer = mark_scheme
    stem = body_text
    if len(options) >= 4 and marks == 1:
        cut = _OPTION.search(body_text)
        stem = body_text[: cut.start()].strip()
        choices = [text.strip() for _label, text in options[:4]]
        letter = (mark_scheme.strip()[:1] or "").upper()
        index = "ABCD".find(letter)
        if index < 0 or index >= len(choices):
            return None, "markscheme nie wskazuje opcji"
        answer = choices[index]
        qtype = "multiple_choice"

    if len(stem) < 15 or not answer:
        return None, "pusty rdzeń lub odpowiedź"

    links = re.findall(r'href="[^"]*syllabus_sections/(\d+)\.html"', raw)
    topic = _pick_topic(links, sections, by_id)
    if topic is None:
        return None, "brak mapowania na temat"

    level = (meta.get("Level") or "").strip().upper()
    difficulty = "medium"
    tags = [f"ib-{meta.get('Paper','').strip().lower() or 'paper'}x", "questionbank"]
    if level:
        tags.append("hl" if level in {"HL", "AHL"} else "sl")

    return (
        {
            "topic": topic,
            "type": qtype,
            "question_text": stem,
            "correct_answer": answer[:2000],
            "choices": choices,
            "marks": max(1, min(marks, 20)),
            "explanation": mark_scheme[:2000] if qtype != "multiple_choice" else "",
            "tags": tags,
            "difficulty": difficulty,
            "source": f"questionbank:{reference}",
            "image_path": image_path,
        },
        "",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="questionbank mirror root")
    parser.add_argument("--bank", required=True, help="e.g. 58-dp-chemistry-first-assessment-2025")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--syllabus", default="2025")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--topics", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    bank_dir = args.source / "en" / "teachers" / "pirateIB" / "questionbanks" / args.bank
    if not bank_dir.is_dir():
        parser.error(f"not found: {bank_dir}")

    sections = read_syllabus(bank_dir / "syllabus_sections")
    topics, by_id = build_topics(sections, args.subject, args.syllabus)
    print(f"sekcji: {len(sections)} | tematów (depth 2): {len(topics)}")

    # The same question appears under several syllabus sections. Collect every
    # copy by reference code first and keep the best one: a copy that maps onto a
    # topic beats one that does not, and a multiple-choice copy beats a written
    # one, so a partial mapping elsewhere cannot hide a good copy.
    def _score(item: dict) -> int:
        return (2 if item["type"] == "multiple_choice" else 0) + (1 if item.get("topic") else 0)

    questions: dict[str, dict] = {}
    skipped: collections.Counter = collections.Counter()
    files = 0
    for path in sorted((bank_dir / "question_node_trees").glob("*.html")):
        if path.name.startswith("._"):
            continue
        files += 1
        raw = path.read_text(encoding="utf-8", errors="replace")
        question, reason = convert_question(raw, sections, by_id, args.images_dir)
        if question is None:
            skipped[reason] += 1
            continue
        current = questions.get(question["source"])
        if current is None or _score(question) > _score(current):
            questions[question["source"]] = question

    items = list(questions.values())
    print(f"plików: {files} | unikalnych pytań: {len(items)}")
    for reason, count in skipped.most_common():
        print(f"  pominięte ({reason}): {count}")

    by_type = collections.Counter(item["type"] for item in items)
    print(f"  typy: {dict(by_type)} | z figurą: {sum(1 for i in items if i.get('image_path'))}")
    print(f"  tematów z pytaniami: {len({i['topic'] for i in items})}/{len(topics)}")

    args.topics.parent.mkdir(parents=True, exist_ok=True)
    args.topics.write_text(
        json.dumps({"subject": args.subject, "syllabus": args.syllabus, "topics": topics}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "meta": {
                    "subject": args.subject,
                    "syllabus": args.syllabus,
                    "source": f"IB Questionbank — {args.bank}",
                    "count": len(items),
                },
                "questions": items,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"zapisano {args.out} i {args.topics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
