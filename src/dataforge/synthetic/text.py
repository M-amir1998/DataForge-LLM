"""Deterministic synthetic text generation.

A small combinatorial grammar per domain produces readable prose with high lexical
diversity, so unrelated documents share few word 5-grams (no false near-duplicates) while
intentionally mutated copies stay above typical near-duplicate thresholds.
"""

from __future__ import annotations

import random
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

# fmt: off
PEOPLE: Final = (
    "Ada Moreno", "Ben Okafor", "Clara Lindqvist", "Dev Raman", "Elena Petrova", "Farid Haddad",
    "Grace Mbeki", "Hugo Laurent", "Iris Tanaka", "Jonas Weber", "Keira Walsh", "Liam Doherty",
    "Mira Castillo", "Noah Bergstrom", "Olga Ivanova", "Pablo Ruiz", "Quinn Harper", "Rosa Almeida",
    "Samir Qureshi", "Tessa Novak", "Umar Siddiqui", "Vera Kowalski", "Wen Zhao", "Ximena Duarte",
    "Yara Haddad", "Zoltan Farkas", "Anika Bose", "Bruno Santos", "Celine Dubois", "Dario Conti",
)
# fmt: on
# fmt: off
PLACES: Final = (
    "Lisbon", "Nairobi", "Montreal", "Kyoto", "Valparaiso", "Tromso", "Accra", "Hobart",
    "Ljubljana", "Cusco", "Tbilisi", "Windhoek", "Galway", "Porto Alegre", "Chiang Mai",
    "Reykjavik", "Marrakesh", "Tallinn", "Cebu", "Leipzig", "Zanzibar", "Halifax", "Bergen",
    "Oaxaca", "Kraków",
)
# fmt: on
# fmt: off
ORGS: Final = (
    "the Northfield Institute", "Harbor Analytics", "the Coastal Research Council",
    "Meridian Labs", "the Riverside Cooperative", "Atlas Systems", "the Open Data Alliance",
    "Granite Foundation", "the Lakeshore Observatory", "Pinecrest University",
    "Sable Robotics", "the Civic Futures Lab", "Brightwater Health", "Orion Logistics",
    "the Highland Water Board",
)
# fmt: on
# fmt: off
MONTHS: Final = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)
# fmt: on
WEEKDAYS: Final = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


@dataclass(frozen=True, slots=True)
class Lexicon:
    topics: tuple[str, ...]
    templates: tuple[str, ...]
    slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


LEXICONS: Final[dict[str, Lexicon]] = {
    "science": Lexicon(
        topics=(
            "coral reef bleaching",
            "soil microbiome diversity",
            "protein folding dynamics",
            "glacial meltwater chemistry",
            "pollinator decline",
            "deep ocean hydrothermal vents",
            "bacterial antibiotic resistance",
            "exoplanet atmospheres",
            "neural plasticity in adults",
            "urban heat islands",
            "volcanic aerosol transport",
            "seed dormancy in alpine plants",
        ),
        slots={
            "method": (
                "mass spectrometry",
                "long-term field plots",
                "satellite imagery",
                "genome sequencing",
                "controlled greenhouse trials",
                "isotope tracing",
                "high-speed microscopy",
                "acoustic monitoring",
                "a randomized sampling design",
                "computational modelling",
            ),
            "verb": (
                "measured",
                "tracked",
                "quantified",
                "compared",
                "mapped",
                "modelled",
                "sampled",
            ),
            "object": (
                "nitrogen uptake",
                "cell membrane stiffness",
                "seasonal temperature swings",
                "dissolved oxygen levels",
                "enzyme activity",
                "the spread of invasive species",
                "carbon storage",
                "migration timing",
                "spectral signatures",
                "gene expression",
            ),
            "adj": ("unexpected", "consistent", "seasonal", "subtle", "measurable", "localized"),
            "finding": (
                "the effect weakens at higher altitudes",
                "warmer sites recovered more slowly",
                "the signal was strongest after heavy rainfall",
                "younger samples were more resilient",
                "the response depended on soil acidity",
                "competition mattered more than climate",
            ),
        },
        templates=(
            "Researchers at {org} {verb} {object} across {num} sites to better understand {topic}.",
            "Using {method}, the team {verb} {object} over {small} consecutive seasons.",
            "Earlier studies of {topic} suggested that {finding}, although the samples were small.",
            "The new measurements show a {adj} shift in {object} of roughly {pct}.",
            "{person}, who led the analysis, cautioned that {topic} varies strongly between regions.",
            "A follow-up experiment near {place} {verb} {object} under controlled conditions.",
            "When the data were pooled, {finding}, which surprised several reviewers.",
            "The authors argue that {method} offers a cheaper way to monitor {object}.",
            "Funding from {org} allowed the group to extend the survey until {year}.",
            "Independent replication will be needed before the results on {topic} are generalized.",
            "Each sample was {verb} twice to reduce instrument error in {object}.",
            "The dataset, covering {num} observations, will be released for other laboratories.",
        ),
    ),
    "technology": Lexicon(
        topics=(
            "edge caching",
            "vector databases",
            "container orchestration",
            "battery management firmware",
            "privacy-preserving analytics",
            "low-latency video streaming",
            "compiler optimization",
            "smart grid sensors",
            "mobile app accessibility",
            "distributed tracing",
            "open-source supply chains",
            "browser rendering engines",
        ),
        slots={
            "tech": (
                "the scheduler",
                "the storage engine",
                "the query planner",
                "the network stack",
                "the build system",
                "the inference server",
                "the message broker",
                "the API gateway",
            ),
            "feature": (
                "incremental snapshots",
                "adaptive batching",
                "zero-copy reads",
                "request hedging",
                "a pluggable cache layer",
                "lock-free queues",
                "tiered compression",
                "signed releases",
            ),
            "metric": (
                "tail latency",
                "memory usage",
                "cold-start time",
                "power draw",
                "error rates",
                "build times",
            ),
            "verb": ("reduces", "stabilizes", "halves", "improves", "smooths", "cuts"),
        },
        templates=(
            "The latest release of {tech} introduces {feature}, which {verb} {metric} in most workloads.",
            "Engineers at {org} measured a {pct} change in {metric} after enabling {feature}.",
            "{person} explained that {topic} used to require manual tuning for every deployment.",
            "With {feature}, operators can roll out changes to {tech} without downtime.",
            "Benchmarks on {num} production clusters show that {metric} depends heavily on configuration.",
            "Critics note that {feature} adds complexity, especially for small teams adopting {topic}.",
            "The project now publishes reproducible builds so that users can verify {tech} themselves.",
            "A design document from {year} first proposed {feature} as a solution for {topic}.",
            "Teams in {place} reported that {metric} dropped once {tech} was upgraded.",
            "Documentation for {topic} was rewritten to include migration notes and failure modes.",
            "Security reviewers asked for clearer defaults before {feature} becomes standard.",
            "The maintainers plan to deprecate the old interface of {tech} within {small} releases.",
        ),
    ),
    "mathematics": Lexicon(
        topics=(
            "prime gaps",
            "graph colouring",
            "convex optimization",
            "modular arithmetic",
            "random walks on lattices",
            "eigenvalues of symmetric matrices",
            "Fourier series",
            "combinatorial designs",
            "the pigeonhole principle",
            "Markov chains",
            "group actions",
            "linear recurrences",
        ),
        slots={
            "obj": (
                "integer",
                "matrix",
                "polynomial",
                "graph",
                "sequence",
                "function",
                "vector space",
            ),
            "technique": (
                "induction",
                "contradiction",
                "a counting argument",
                "the triangle inequality",
                "a generating function",
                "diagonalization",
                "a compactness argument",
                "symmetry",
            ),
            "claim": (
                "the bound is tight",
                "the sequence converges",
                "the sum is divisible by three",
                "every vertex has even degree",
                "the determinant is non-zero",
                "the map is injective",
            ),
        },
        templates=(
            "Let n be a positive {obj} parameter and consider {topic} for all values above {num}.",
            "We prove the statement by {technique}, starting from the base case n = {small}.",
            "A classical result on {topic} shows that {claim} whenever the hypotheses hold.",
            "Consider the expression x^2 + {small}x + {num}; its discriminant decides the number of real roots.",
            "By {technique}, it follows that {claim} for every {obj} we have constructed.",
            "{person} gave a shorter proof in {year} that relies only on {technique}.",
            "The key lemma states that {claim}, and the rest of the argument is routine.",
            "If we apply {technique} twice, the estimate improves by a factor of {small}.",
            "Students often confuse {topic} with related ideas, so a worked example helps.",
            "For example, when n = {num}, a direct computation confirms that {claim}.",
            "The theorem generalizes to any finite {obj} with the same symmetry.",
            "Exercises at the end of the chapter on {topic} ask readers to adapt {technique}.",
        ),
    ),
    "legal": Lexicon(
        topics=(
            "tenancy agreements",
            "data processing contracts",
            "intellectual property licensing",
            "employment termination",
            "consumer warranties",
            "public procurement",
            "zoning appeals",
            "maritime liability",
            "non-disclosure obligations",
            "insurance claims",
        ),
        slots={
            "party": (
                "the Licensee",
                "the Tenant",
                "the Supplier",
                "the Employer",
                "the Contractor",
                "the Insurer",
            ),
            "obligation": (
                "provide written notice",
                "maintain accurate records",
                "indemnify the other party",
                "remedy any material breach",
                "return all confidential materials",
                "pay the outstanding balance",
            ),
            "event": (
                "termination",
                "the effective date",
                "receipt of an invoice",
                "a court order",
                "completion of the works",
            ),
            "law": (
                "the Commercial Code",
                "the Consumer Protection Act",
                "the Data Protection Act",
                "the Tenancy Act",
            ),
        },
        templates=(
            "Pursuant to Section {num} of {law}, {party} shall {obligation} within {small} days of {event}.",
            "The court held that {party} had failed to {obligation} as required by the agreement.",
            "In disputes concerning {topic}, the burden of proof rests with the claimant.",
            "Nothing in this clause limits the right of {party} to seek injunctive relief.",
            "The appellate panel in {place} reversed the ruling on {topic} by a majority of {small} to one.",
            "Counsel for {party} argued that the notice period had not commenced before {event}.",
            "Any amendment to these terms must be signed by both parties and dated after {event}.",
            "Under {law}, liability for {topic} cannot be excluded for negligence causing personal injury.",
            "The tribunal awarded damages of {num} thousand euros together with reasonable costs.",
            "{person}, the presiding judge, noted that the contract language on {topic} was ambiguous.",
            "Where {party} is unable to {obligation}, the agreement may be suspended without penalty.",
            "Precedent from {year} suggests that courts interpret {topic} clauses narrowly.",
        ),
    ),
    "news": Lexicon(
        topics=(
            "a new rail link",
            "municipal budget cuts",
            "a drought emergency",
            "a hospital expansion",
            "a regional election",
            "port strikes",
            "a heritage restoration project",
            "wildfire recovery",
            "a public transport fare freeze",
            "a youth employment scheme",
        ),
        slots={
            "official": (
                "the mayor",
                "the transport minister",
                "a council spokesperson",
                "the regional governor",
            ),
            "reaction": (
                "welcomed the decision",
                "called for an independent review",
                "criticized the timeline",
                "urged residents to stay patient",
                "promised further consultation",
            ),
        },
        templates=(
            "{place} — Officials announced on {weekday} that {topic} will move ahead despite objections.",
            "{official} told reporters that funding for {topic} had been secured until {year}.",
            "Local business owners {reaction}, citing concerns about costs and disruption.",
            "According to figures released by {org}, about {num} households could be affected.",
            "{person}, who has lived in {place} for decades, said the community {reaction}.",
            "The plan, first proposed in {month}, has already been revised {small} times.",
            "Opposition councillors {reaction} and asked for the full contract to be published.",
            "Protesters gathered outside the council offices on {weekday} evening.",
            "{official} said a final vote on {topic} is expected before the end of {month}.",
            "Analysts at {org} estimate the project could create {num} jobs over the next decade.",
            "Residents can submit comments online until the {day} of {month}.",
            "The story of {topic} has dominated local headlines since early {year}.",
        ),
    ),
    "forums": Lexicon(
        topics=(
            "a flaky wifi adapter",
            "sourdough starter problems",
            "a squeaky bike brake",
            "a slow laptop after an update",
            "repotting a fiddle leaf fig",
            "a noisy dishwasher",
            "learning to play barre chords",
            "a leaking kitchen tap",
            "setting up a home NAS",
            "training a rescue dog",
        ),
        slots={
            "attempt": (
                "restarting everything twice",
                "reading the manual again",
                "watching three tutorials",
                "swapping the cable",
                "asking at the local shop",
                "resetting to factory settings",
            ),
            "feeling": ("frustrated", "a bit lost", "curious", "stuck", "hopeful"),
            "handle": (
                "night_owl",
                "tinker_tom",
                "plantmom22",
                "quietbyte",
                "gearhead_lu",
                "mossy_rock",
            ),
        },
        templates=(
            "Hi everyone, I have been dealing with {topic} for about {small} weeks and I am {feeling}.",
            "So far I have tried {attempt}, but nothing seems to make a real difference.",
            "Has anyone else run into {topic}? Any tips would be really appreciated.",
            "Reply from {handle}: I had the same issue last {month} and {attempt} fixed it for me.",
            "Reply from {handle}: honestly I think it depends on your setup, so post more details.",
            "Edit: thanks for the suggestions, I will try {attempt} this weekend and report back.",
            "For context, I live in {place} and the problem gets worse when it is humid.",
            "I asked a friend who works at {org} and they suggested {attempt} as a first step.",
            "Update: after {attempt} it worked for {small} days and then the problem came back.",
            "Thanks in advance, this community has helped me so much over the years.",
        ),
    ),
    "general": Lexicon(
        topics=(
            "the history of lighthouses",
            "traditional bread making",
            "mountain railway engineering",
            "the migration of monarch butterflies",
            "medieval trade routes",
            "public libraries",
            "the origins of chess",
            "urban rooftop gardens",
            "glassblowing techniques",
            "the culture of tea ceremonies",
            "early aviation",
            "salt production",
        ),
        slots={
            "feature": (
                "stone harbour walls",
                "a busy weekly market",
                "its colourful facades",
                "a long coastal promenade",
            ),
            "era": (
                "the seventeenth century",
                "the industrial period",
                "the early modern era",
                "the interwar years",
            ),
        },
        templates=(
            "{topic} has a long and varied history that scholars continue to debate.",
            "In {era}, {place} became known for {feature} and attracted visitors from afar.",
            "The first written account of {topic} dates from around {year}.",
            "{person} described the practice in detail after travelling through {place}.",
            "Over time, local communities adapted {topic} to the materials they had at hand.",
            "Today, a museum in {place} preserves more than {num} objects related to the subject.",
            "Many of the techniques used in {era} are still taught in workshops today.",
            "Historians point out that {topic} spread mainly along rivers and coastal roads.",
            "A restoration effort completed in {year} returned the old building to public use.",
            "Visitors to {place} can still see traces of {feature} near the old town.",
            "The subject gained new popularity after a documentary aired in {year}.",
            "Local guides estimate that {num} people visit the site during a typical summer.",
        ),
    ),
    "books": Lexicon(
        topics=(
            "the lighthouse keeper",
            "the winter crossing",
            "the last orchard",
            "the silent harbour",
            "the clockmaker",
        ),
        slots={
            "line": (
                "We should leave before the tide turns",
                "I never meant for any of this to happen",
                "You knew the letter would arrive today",
                "Nobody in this town remembers the old road",
                "Keep the lantern lit until I come back",
                "There is still time if we hurry",
            ),
            "action": (
                "looking out at the grey water",
                "folding the map with careful hands",
                "without lifting her eyes from the fire",
                "as the wind rattled the shutters",
                "and pulled the heavy door closed",
            ),
            "scene": (
                "The rain had not stopped for three days",
                "Snow covered the narrow path to the village",
                "The kitchen smelled of smoke and bread",
                "A bell rang somewhere beyond the hills",
                "The old house creaked as the evening grew colder",
            ),
        },
        templates=(
            "{scene}, and {person} sat by the window waiting for news.",
            '"{line}," {person} said, {action}.',
            "Nobody answered at first, and the silence settled over the room like dust.",
            '"{line}," came the quiet reply from the doorway.',
            "{scene}; even the dogs seemed to sense that something had changed.",
            "In the morning {person} walked down to {place}, where the market was already busy.",
            "It had been {small} years since the family last gathered under one roof.",
            "{person} remembered the summer of {year}, when everything had seemed so simple.",
            '"{line}," {person} whispered, {action}.',
            "The letter lay unopened on the table until long after midnight.",
        ),
    ),
}

_MULTILINGUAL: Final[dict[str, tuple[str, ...]]] = {
    "es": (
        "El ayuntamiento de {place} anunció que el nuevo parque estará abierto para todos los vecinos.",
        "Según los expertos, la mayoría de los cambios serán visibles en los próximos {small} años.",
        "Los vecinos del barrio pidieron más información sobre el proyecto y sus costes.",
        "La universidad publicó un estudio con datos de más de {num} familias de la región.",
        "Muchos comerciantes creen que la medida es positiva, pero también tienen dudas.",
        "El informe también señala que el transporte público necesita una inversión urgente.",
    ),
    "fr": (
        "La mairie de {place} a annoncé que la nouvelle bibliothèque ouvrira ses portes au printemps.",
        "Selon les chercheurs, les résultats sont encourageants mais il faut encore des données.",
        "Les habitants du quartier ont demandé plus de transparence sur le budget du projet.",
        "Une enquête menée auprès de {num} personnes montre que la plupart sont satisfaites.",
        "Le directeur a expliqué que les travaux dureront environ {small} mois.",
        "Les associations locales veulent participer aux décisions qui concernent leur ville.",
    ),
    "de": (
        "Die Stadtverwaltung von {place} hat angekündigt, dass die neue Brücke im Herbst eröffnet wird.",
        "Nach Angaben der Forscher sind die Ergebnisse vielversprechend, aber noch nicht endgültig.",
        "Die Anwohner fordern mehr Informationen über die Kosten und den Zeitplan des Projekts.",
        "Eine Umfrage unter {num} Personen zeigt, dass die meisten mit dem Angebot zufrieden sind.",
        "Der Bürgermeister erklärte, dass die Arbeiten etwa {small} Monate dauern werden.",
        "Viele Vereine wollen sich an den Entscheidungen für ihre Stadt beteiligen.",
    ),
}

_FORMATTER: Final = string.Formatter()
_SHARED_SLOTS: Final[dict[str, Sequence[str]]] = {
    "person": PEOPLE,
    "place": PLACES,
    "org": ORGS,
    "month": MONTHS,
    "weekday": WEEKDAYS,
}


@lru_cache(maxsize=1024)
def _fields(template: str) -> tuple[str, ...]:
    return tuple(name for _, name, _, _ in _FORMATTER.parse(template) if name)


class TextGenerator:
    """Seeded generator for prose, code, low-quality pages, and multilingual snippets."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    # ------------------------------------------------------------------ primitives

    def pick(self, values: Sequence[str]) -> str:
        return values[self.rng.randrange(len(values))]

    def _value(self, name: str, lexicon: Lexicon | None, topic: str) -> str:
        rng = self.rng
        if name == "topic":
            return topic
        if lexicon is not None and name in lexicon.slots:
            return self.pick(lexicon.slots[name])
        if name in _SHARED_SLOTS:
            return self.pick(_SHARED_SLOTS[name])
        if name == "num":
            return str(rng.randint(12, 980))
        if name == "small":
            return str(rng.randint(2, 12))
        if name == "pct":
            return f"{rng.randint(3, 97)} percent"
        if name == "year":
            return str(rng.randint(1890, 2024))
        if name == "day":
            return f"{rng.randint(2, 28)}th"
        raise KeyError(f"unknown template slot: {name}")

    def sentence(self, template: str, lexicon: Lexicon | None = None, topic: str = "") -> str:
        values = {name: self._value(name, lexicon, topic) for name in _fields(template)}
        text = template.format(**values)
        return text[0].upper() + text[1:]

    # ------------------------------------------------------------------ documents

    def _paragraphs(self, sentences: list[str]) -> str:
        paragraphs: list[str] = []
        index = 0
        while index < len(sentences):
            size = self.rng.randint(3, 5)
            paragraphs.append(" ".join(sentences[index : index + size]))
            index += size
        return "\n\n".join(paragraphs)

    def document(self, domain: str, *, min_sentences: int = 10, max_sentences: int = 22) -> tuple[str, str]:
        """Return ``(title, text)`` for a prose document in ``domain``.

        Templates are drawn without replacement, as a human writer rarely repeats the same
        sentence frame within one short document; this keeps n-gram repetition in the range
        measured on real prose (median 3-gram repeat ratio ~0.1).
        """
        lexicon = LEXICONS[domain]
        topic = self.pick(lexicon.topics)
        total = min(self.rng.randint(min_sentences, max_sentences), len(lexicon.templates))
        templates = self.rng.sample(lexicon.templates, k=total)
        sentences = [self.sentence(template, lexicon, topic) for template in templates]
        title = f"{topic[0].upper()}{topic[1:]}: notes from {self.pick(PLACES)}"
        return title, self._paragraphs(sentences)

    def long_document(self, domain: str, *, paragraphs: int) -> str:
        """Long-form text drawing frames from the domain plus related lexicons.

        Frames are consumed from a shuffled pool and only recycled once it is exhausted,
        so repetition grows slowly with length, as it does in real long-form writing.
        """
        related = [domain] + [name for name in ("general", "news", "science") if name != domain]
        pool: list[tuple[str, Lexicon, str]] = []
        sentences: list[str] = []
        target = sum(self.rng.randint(3, 5) for _ in range(paragraphs))
        while len(sentences) < target:
            if not pool:
                pool = [
                    (template, LEXICONS[name], self.pick(LEXICONS[name].topics))
                    for name in related
                    for template in LEXICONS[name].templates
                ]
                self.rng.shuffle(pool)
            template, lexicon, topic = pool.pop()
            sentences.append(self.sentence(template, lexicon, topic))
        return self._paragraphs(sentences)

    def multilingual(self, language: str, sentences: int = 8) -> str:
        templates = _MULTILINGUAL[language]
        return " ".join(self.sentence(self.pick(templates)) for _ in range(sentences))

    # ------------------------------------------------------------------ low quality

    def low_quality(self, kind: str) -> str:
        rng = self.rng
        if kind == "spam":
            product = self.pick(("watches", "sneakers", "headphones", "vitamins", "phone cases"))
            lines = [f"Buy cheap {product} online best price buy {product} now" for _ in range(rng.randint(12, 25))]
            return "\n".join(lines)
        if kind == "boilerplate":
            nav = [
                "Home | About | Contact | Privacy Policy | Terms of Use",
                "Accept all cookies",
                "Subscribe to our newsletter",
                "Sign in | Register",
                "All rights reserved.",
                "Share on Facebook | Share on Twitter",
                "Click here to read more",
                "Cookie settings",
            ]
            return "\n".join(
                [
                    *nav,
                    self.sentence(self.pick(LEXICONS["general"].templates), LEXICONS["general"], "this page"),
                ]
            )
        if kind == "short":
            return self.pick(("Click here to continue.", "Page not found.", "Loading...", "Thanks!"))
        if kind == "symbols":
            rows = [
                "### $$$ ### !!! >>> <<< ### ***",
                "#### ---- #### ++++ ####",
                "... ... ... ### ###",
            ]
            return "\n".join(self.pick(rows) for _ in range(rng.randint(15, 30)))
        if kind == "urls":
            return "\n".join(
                f"https://links.example.net/{rng.randint(1000, 9999)}/{self.pick(('a', 'b', 'c'))}" for _ in range(30)
            )
        if kind == "lorem":
            return (
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
                "incididunt ut labore et dolore magna aliqua. "
            ) * rng.randint(3, 6)
        raise KeyError(kind)

    # ------------------------------------------------------------------ mutations

    def mutate(self, text: str, edits: int = 2) -> str:
        """A light edit: swap a few words and occasionally add a dateline."""
        words = text.split(" ")
        fillers = ("notably", "reportedly", "recently", "locally", "officially", "partly")
        for _ in range(edits):
            position = self.rng.randrange(len(words))
            if words[position].isalpha():
                words[position] = self.pick(fillers)
        mutated = " ".join(words)
        if self.rng.random() < 0.5:
            mutated += f"\n\nUpdated on {self.pick(MONTHS)} {self.rng.randint(2, 28)}, {self.rng.randint(2019, 2024)}."
        return mutated

    # ------------------------------------------------------------------ PII

    def email(self) -> str:
        first, last = self.pick(PEOPLE).lower().split(" ", 1)
        domain = self.pick(("example.com", "example.org", "example.net"))
        return f"{first}.{last.replace(' ', '')}@{domain}"

    def phone(self) -> str:
        # 555-0100..0199 is reserved for fictional use in the North American numbering plan.
        area = self.pick(("202", "312", "415", "617"))
        line = f"01{self.rng.randint(0, 99):02d}"
        return self.pick((f"+1 {area}-555-{line}", f"({area}) 555-{line}"))

    def ip_address(self) -> str:
        # Documentation ranges (RFC 5737).
        prefix = self.pick(("192.0.2", "198.51.100", "203.0.113"))
        return f"{prefix}.{self.rng.randint(1, 254)}"

    def credit_card(self) -> str:
        # Publicly documented payment-processor test numbers (Luhn valid, never issued).
        return self.pick(("4111 1111 1111 1111", "5555 5555 5555 4444", "4012 8888 8888 1881"))

    def ssn_like(self) -> str:
        # Numbers voided by the US Social Security Administration after public misuse.
        return self.pick(("078-05-1120", "219-09-9999"))

    def iban(self) -> str:
        # Standard documentation example IBANs.
        return self.pick(("GB82 WEST 1234 5698 7654 32", "DE89 3704 0044 0532 0130 00"))

    # ------------------------------------------------------------------ secrets (fragments)

    def _token(self, alphabet: str, length: int) -> str:
        return "".join(alphabet[self.rng.randrange(len(alphabet))] for _ in range(length))

    def fake_secret(self, kind: str) -> str:
        """A syntactically realistic but random credential, assembled from fragments.

        Prefixes are split so that no contiguous secret-shaped literal exists in the
        repository; values only exist inside generated sample data.
        """
        upper_digits = string.ascii_uppercase + "234567"
        alnum = string.ascii_letters + string.digits
        if kind == "aws_access_key_id":
            return "AK" + "IA" + self._token(upper_digits, 16)
        if kind == "aws_secret_access_key":
            return self._token(alnum + "/+", 40)
        if kind == "github_token":
            return "gh" + "p_" + self._token(alnum, 36)
        if kind == "slack_token":
            return "xo" + "xb-" + self._token(string.digits, 12) + "-" + self._token(alnum, 24)
        if kind == "password":
            return self._token(alnum + "!#%&", 18)
        if kind == "private_key":
            body = "\n".join(self._token(alnum + "+/", 64) for _ in range(6))
            kind_line = "RSA " + "PRIVATE KEY-----"
            return f"-----BEGIN {kind_line}\n{body}\n-----END {kind_line}"
        raise KeyError(kind)
