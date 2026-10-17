"""The canonical IB Biology topic catalogue.

Topics live in code rather than only in the database so that the syllabus
structure is version controlled, seed imports can create topics on demand, and
the scraper can map scraped material onto topics consistently.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TopicSpec:
    """A single syllabus topic."""

    slug: str
    title: str
    short_title: str
    description: str
    #: IB syllabus reference (e.g. "A1", "B2") — shown as a badge in the UI.
    syllabus_ref: str = ""
    #: Lowercase keywords used to map loose text (file names, scraped pages,
    #: topic headings) onto this topic.
    keywords: tuple[str, ...] = field(default_factory=tuple)


# Ordered exactly as the IB Biology guide presents the themes.
TOPIC_CATALOGUE: tuple[TopicSpec, ...] = (
    TopicSpec(
        slug="cell-biology",
        title="Cell Biology",
        short_title="Cells",
        description="Cell structure, membranes, transport, division and the origin of cells.",
        keywords=(
            "cell", "organelle", "mitosis", "membrane", "osmosis", "diffusion",
            "microscope", "eukaryote", "prokaryote", "vesicle", "endosymbiosis",
            "surface area", "magnification", "transport", "active transport",
            "meiosis", "cell cycle", "stem cell", "differentiation",
        ),
    ),
    TopicSpec(
        slug="molecular-biology",
        title="Molecular Biology",
        short_title="Molecules",
        description="Water, carbohydrates, lipids, proteins, enzymes and DNA structure.",
        keywords=(
            "water", "carbohydrate", "lipid", "protein", "enzyme", "amino acid",
            "condensation", "hydrolysis", "dna", "rna", "nucleotide", "monomer",
            "polymer", "glucose", "triglyceride", "peptide", "denatur", "collagen",
            "insulin", "antibod", "immobilis", "chromatograph", "biuret",
        ),
    ),
    TopicSpec(
        slug="genetics",
        title="Genetics",
        short_title="Genetics",
        description="Mendelian inheritance, chromosomes, mutations, gene technology and pedigrees.",
        keywords=(
            "gene", "allele", "genotype", "phenotype", "mendel", "pedigree",
            "punnett", "heterozyg", "homozyg", "dominant", "recessive", "mutation",
            "karyotype", "cross", "dihybrid", "monohybrid", "sex linkage",
            "codominan", "multiple allele", "gel electrophoresis", "cloning",
            "crispr", "recombinant", "genetic modification", "pcr", "inheritance",
        ),
    ),
    TopicSpec(
        slug="ecology",
        title="Ecology",
        short_title="Ecology",
        description="Species and communities, energy flow, nutrient cycles, climate change and conservation.",
        keywords=(
            "ecosystem", "habitat", "population", "community", "food chain",
            "food web", "energy", "pyramid", "biomass", "nutrient cycle",
            "carbon cycle", "nitrogen", "succession", "biome", "climate",
            "greenhouse", "conservation", "biodiversity index", "quadrat",
            "carrying capacity", "trophic", "decompos", "eutrophication",
        ),
    ),
    TopicSpec(
        slug="evolution-biodiversity",
        title="Evolution and Biodiversity",
        short_title="Evolution",
        description="Evidence for evolution, natural selection, speciation and classification.",
        keywords=(
            "evolution", "natural selection", "speciation", "darwin", "fossil",
            "homologous", "analogous", "clade", "phylogen", "taxonom",
            "classification", "binomial", "variation", "adaptation", "antibiotic",
            "selective pressure", "extinction", "reproductive isolation",
        ),
    ),
    TopicSpec(
        slug="human-physiology",
        title="Human Physiology",
        short_title="Physiology",
        description="Digestion, circulation, gas exchange, defence, nervous and endocrine systems, homeostasis.",
        keywords=(
            "digest", "absorption", "blood", "heart", "circulation", "ventilation",
            "lung", "alveol", "haemoglobin", "immun", "antibody", "pathogen",
            "neuron", "synapse", "hormone", "osmoregulation", "kidney", "nephron",
            "homeostasis", "thermoregulation", "muscle", "reproduction", "gamete",
            "fertilis", "menstrual", "insulin", "diabetes", "gas exchange",
        ),
    ),
    TopicSpec(
        slug="nucleic-acids",
        title="Nucleic Acids and Protein Synthesis",
        short_title="Nucleic Acids",
        description="DNA replication, transcription, translation, the genetic code and biotechnology.",
        keywords=(
            "replication", "transcription", "translation", "protein synthesis",
            "genetic code", "codon", "anticodon", "ribosome", "polymerase",
            "helicase", "semiconservative", "mrna", "trna", "pcr",
            "electrophoresis", "nucleic acid", "base pairing", "exon", "intron",
        ),
    ),
    TopicSpec(
        slug="metabolism-cell-respiration-photosynthesis",
        title="Metabolism, Cell Respiration and Photosynthesis",
        short_title="Metabolism",
        description="Enzymes and metabolism, glycolysis, the Krebs cycle, the electron transport chain and photosynthesis.",
        keywords=(
            "metabolism", "respiration", "glycolysis", "krebs", "oxidative",
            "electron transport", "atp", "photosynthesis", "light",
            "chlorophyll", "calvin", "thylakoid", "chemiosmosis", "anaerobic",
            "lactate", "fermentation", "limiting factor", "chromatograph",
            "spectrophotomet", "rate of respiration",
        ),
    ),
    TopicSpec(
        slug="plant-biology",
        title="Plant Biology",
        short_title="Plants",
        description="Plant transport, reproduction, growth, hormones and responses.",
        keywords=(
            "plant", "xylem", "phloem", "transpiration", "stomata", "root",
            "leaf", "meristem", "auxin", "hormone", "tropism", "flower",
            "pollination", "seed", "germination", "photoperiod", "translocation",
            "capillary", "cohesion", "tension",
        ),
    ),
    TopicSpec(
        slug="data-analysis",
        title="Data Analysis and Experimental Skills",
        short_title="Data Skills",
        description="Graphing, statistics, experimental design, error and uncertainty.",
        keywords=(
            "data", "graph", "statistic", "mean", "median", "standard deviation",
            "t-test", "chi-squared", "correlation", "uncertainty", "error",
            "control", "variable", "significance", "rate", "percentage change",
        ),
    ),
)

TOPICS_BY_SLUG: dict[str, TopicSpec] = {t.slug: t for t in TOPIC_CATALOGUE}

TOPIC_ORDER: tuple[str, ...] = tuple(t.slug for t in TOPIC_CATALOGUE)


def normalize_topic(value: str | None) -> str | None:
    """Best-effort map of an arbitrary topic string onto a catalogue slug.

    Accepts slugs, titles, short titles, syllabus references or loose
    free text containing a topic keyword. Returns ``None`` when nothing
    matches confidently.
    """
    if not value:
        return None

    text = str(value).strip().lower()
    if not text:
        return None

    # Direct slug / title / short-title / syllabus-ref hit.
    for spec in TOPIC_CATALOGUE:
        if text in {spec.slug, spec.title.lower(), spec.short_title.lower()}:
            return spec.slug
    for spec in TOPIC_CATALOGUE:
        if spec.syllabus_ref and text == spec.syllabus_ref.lower():
            return spec.slug

    # Slug-ish match: "Cell Biology" -> "cell-biology".
    dashed = text.replace("_", "-").replace(" ", "-")
    for spec in TOPIC_CATALOGUE:
        if dashed == spec.slug:
            return spec.slug

    # A 2025 subtopic slug is already canonical: return it unchanged instead of
    # letting keyword scoring pull it into a 2024 topic ("bio-b32-transport"
    # contains "transport", which is a cell-biology keyword).
    if dashed in NEW_SYLLABUS_BY_SLUG:
        return dashed
    for spec in NEW_SYLLABUS_TOPICS:
        if text in {spec.ref.lower(), spec.title.lower(), spec.full_title.lower()}:
            return spec.slug

    # Keyword scoring over the free text.
    best_slug: str | None = None
    best_score = 0
    for spec in TOPIC_CATALOGUE:
        score = sum(1 for kw in spec.keywords if kw in text)
        # A named topic inside free text is a strong signal.
        if spec.title.lower() in text or spec.short_title.lower() in text:
            score += 3
        if score > best_score:
            best_slug, best_score = spec.slug, score
    return best_slug if best_score > 0 else None


# ---------------------------------------------------------------------------
# IB Biology 2025 syllabus (first assessment 2025)
#
# The catalogue above follows the legacy "topic" naming used by the original
# app. The 2025 guide restructures the same biology into four themes (A-D);
# these entries map the revisiondojo-style question banks onto that structure
# so imported questions get real titles instead of raw slugs.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SyllabusTopic:
    """One 2025-syllabus subtopic."""

    slug: str
    ref: str
    title: str
    theme: str
    description: str = ""

    @property
    def full_title(self) -> str:
        return f"{self.ref} {self.title}"


#: Every subtopic in the 2025 guide, in syllabus order.
NEW_SYLLABUS_TOPICS: tuple[SyllabusTopic, ...] = (
    # A — Unity and Diversity
    SyllabusTopic("bio-a11-water", "A1.1", "Water", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a12-nucleic-acids", "A1.2", "Nucleic Acids", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a21-origins-of-cells-hl", "A2.1", "Origins of Cells", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a22-cell-structure", "A2.2", "Cell Structure", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a23-viruses-hl", "A2.3", "Viruses", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a31-diversity-of-organisms", "A3.1", "Diversity of Organisms", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a32-classification-and-cladistics-hl", "A3.2", "Classification and Cladistics", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a41-evolution-and-speciation", "A4.1", "Evolution and Speciation", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    SyllabusTopic("bio-a42-conservation-of-biodiversity", "A4.2", "Conservation of Biodiversity", 'A — Unity and Diversity', 'Molecules, cells, organisms and the diversity of life.'),
    # B — Form and Function
    SyllabusTopic("bio-b11-carbohydrates-and-lipids", "B1.1", "Carbohydrates and Lipids", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b12-proteins", "B1.2", "Proteins", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b21-membranes-and-membrane-transport", "B2.1", "Membranes and Membrane Transport", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b22-organelles-and-compartmentalization", "B2.2", "Organelles and Compartmentalization", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b23-cell-specialization", "B2.3", "Cell Specialization", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b31-gas-exchange", "B3.1", "Gas Exchange", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b32-transport", "B3.2", "Transport", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b33-muscle-and-motility-hl", "B3.3", "Muscle and Motility", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b41-adaptation-to-environment", "B4.1", "Adaptation to Environment", 'B — Form and Function', 'How biological structures carry out their functions.'),
    SyllabusTopic("bio-b42-ecological-niches", "B4.2", "Ecological Niches", 'B — Form and Function', 'How biological structures carry out their functions.'),
    # C — Interaction and Interdependence
    SyllabusTopic("bio-c11-enzymes-and-metabolism", "C1.1", "Enzymes and Metabolism", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c12-cell-respiration", "C1.2", "Cell Respiration", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c13-photosynthesis", "C1.3", "Photosynthesis", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c21-chemical-signalling-hl", "C2.1", "Chemical Signalling", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c22-neural-signalling", "C2.2", "Neural Signalling", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c31-integration-of-body-systems", "C3.1", "Integration of Body Systems", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c32-defense-against-disease", "C3.2", "Defence against Disease", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c41-populations-and-communities", "C4.1", "Populations and Communities", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    SyllabusTopic("bio-c42-transfer-of-energy-and-matter", "C4.2", "Transfer of Energy and Matter", 'C — Interaction and Interdependence', 'How organisms interact with each other and their environment.'),
    # D — Continuity and Change
    SyllabusTopic("bio-d11-dna-replication", "D1.1", "DNA Replication", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d12-protein-synthesis", "D1.2", "Protein Synthesis", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d13-mutations-and-gene-editing", "D1.3", "Mutations and Gene Editing", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d21-cell-and-nuclear-division", "D2.1", "Cell and Nuclear Division", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d22-gene-expression-hl", "D2.2", "Gene Expression", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d23-water-potential", "D2.3", "Water Potential", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d31-reproduction", "D3.1", "Reproduction", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d32-inheritance", "D3.2", "Inheritance", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d33-homeostasis", "D3.3", "Homeostasis", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d41-natural-selection", "D4.1", "Natural Selection", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d42-stability-and-change", "D4.2", "Stability and Change", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
    SyllabusTopic("bio-d43-climate-change", "D4.3", "Climate Change", 'D — Continuity and Change', 'Reproduction, inheritance, evolution and change over time.'),
)


NEW_SYLLABUS_BY_SLUG: dict[str, SyllabusTopic] = {t.slug: t for t in NEW_SYLLABUS_TOPICS}



# ---------------------------------------------------------------------------
# Presentation order
#
# The bank mixes two programmes: the 2025 guide (40 subtopics, A1.1 → D4.3) and
# the 2024 topics that arrived with the older import, which carry no real
# syllabus reference. Both are worth reading, but a flat list of them is
# unreadable — so the 2025 subtopics are grouped under their theme and the 2024
# topics are kept together, after them, labelled for what they actually are.
# ---------------------------------------------------------------------------

#: Heading shown for the pre-2025 topics, which have no A–D theme of their own.
LEGACY_GROUP = "IB Biology (2024 programme)"


def syllabus_sort_key(ref: str) -> tuple:
    """Sort ``"B3.2"`` style references by theme, then numerically.

    A plain string sort puts ``A1.10`` before ``A1.2``; splitting on the dot
    keeps the numeric order the syllabus uses.
    """
    theme = ""
    section = 0
    point = 0
    match = re.match(r"([A-Za-z]+)(\d+)(?:\.(\d+))?", str(ref or ""))
    if match:
        theme = match.group(1).upper()
        section = int(match.group(2))
        point = int(match.group(3) or 0)
    return (theme, section, point)


def topic_groups() -> tuple[tuple[str, tuple], ...]:
    """The catalogue as display groups: ``(heading, (spec, ...))``.

    Whitespace is squeezed out of the theme name so the heading reads
    ``A — Unity and Diversity`` rather than inheriting the guide's line breaks.
    """
    groups: list[tuple[str, list]] = []
    for spec in NEW_SYLLABUS_TOPICS:
        heading = re.sub(r"\s+", " ", spec.theme).strip()
        if not groups or groups[-1][0] != heading:
            groups.append((heading, []))
        groups[-1][1].append(spec)

    ordered = [
        (heading, tuple(sorted(items, key=lambda item: syllabus_sort_key(item.ref))))
        for heading, items in groups
    ]
    if TOPIC_CATALOGUE:
        ordered.append((LEGACY_GROUP, tuple(TOPIC_CATALOGUE)))
    return tuple(ordered)


def ordered_specs() -> tuple:
    """Every catalogue entry in display order: 2025 subtopics, then 2024 topics.

    Used to assign ``Topic.position`` so a database query and the grouped page
    agree on the order.
    """
    specs = []
    for _heading, items in topic_groups():
        specs.extend(items)
    return tuple(specs)


# ---------------------------------------------------------------------------
# Subjects
#
# The bank started as biology and now carries chemistry. Each subject keeps its
# own catalogue; topics are still one flat table, distinguished by ``subject``.
# ---------------------------------------------------------------------------

SUBJECTS: dict[str, str] = {"biology": "Biology", "chemistry": "Chemistry"}

_SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


def _display_theme(theme: str) -> str:
    """``"Structure 1. Models of ..."`` -> ``"Structure 1 — Models of ..."``."""
    match = re.match(r"^([A-Za-z]+\s*\d*)\.\s*(.+)$", theme)
    if match and match.group(1).strip():
        return f"{match.group(1).strip()} — {match.group(2).strip()}"
    return theme


def load_subject_topics(subject: str) -> tuple[dict, ...]:
    """Topic rows for a subject whose catalogue lives in ``seed/topics_<subject>.json``.

    Biology's catalogue is in code (it predates the file format); chemistry's is
    generated from the source's own syllabus tree, because forty hand-written
    entries would drift the moment a section is renamed.
    """
    path = _SEED_DIR / f"topics_{subject}.json"
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    topics = payload.get("topics") if isinstance(payload, dict) else payload
    if not isinstance(topics, list):
        return ()
    return tuple(topic for topic in topics if isinstance(topic, dict) and topic.get("slug"))


def chemistry_topics() -> tuple[dict, ...]:
    return load_subject_topics("chemistry")


def subject_specs(subject: str) -> tuple:
    """Every catalogue entry for a subject, in display order."""
    if subject == "chemistry":
        return chemistry_topics()
    return ordered_specs()


def subject_groups(subject: str) -> tuple[tuple[str, tuple], ...]:
    """Display groups for one subject: ``(heading, (spec, ...))``."""
    if subject != "chemistry":
        return topic_groups()

    # Order themes as the guide does: Structure, then Reactivity, then the skills
    # material. The catalogue file follows the source's section ids, which are not
    # hierarchical.
    order = {"structure": 0, "reactivity": 1, "tools": 2, "inquiry": 3}

    def _key(topic: dict) -> tuple:
        theme = str(topic.get("theme") or "")
        word = (theme.split(" ")[0] or "").lower()
        number = re.match(r"[A-Za-z]+\s*(\d+)", theme)
        return (order.get(word, 9), int(number.group(1)) if number else 0, str(topic.get("ref") or ""))

    groups: list[tuple[str, list]] = []
    for topic in sorted(chemistry_topics(), key=_key):
        heading = _display_theme(str(topic.get("theme") or "Chemistry"))
        if not groups or groups[-1][0] != heading:
            groups.append((heading, []))
        groups[-1][1].append(topic)
    return tuple((heading, tuple(items)) for heading, items in groups)
