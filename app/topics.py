"""The canonical IB Biology topic catalogue.

Topics live in code rather than only in the database so that the syllabus
structure is version controlled, seed imports can create topics on demand, and
the scraper can map scraped material onto topics consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
        syllabus_ref="A2",
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
        syllabus_ref="B1",
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
        syllabus_ref="D1",
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
        syllabus_ref="C1",
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
        syllabus_ref="C2",
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
        syllabus_ref="D2",
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
        syllabus_ref="D1.1",
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
        syllabus_ref="C3",
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
        syllabus_ref="D3",
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
        syllabus_ref="Skills",
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
