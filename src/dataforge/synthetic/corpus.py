"""Deterministic sample corpus with ground-truth labels.

The corpus deliberately mixes formats (JSONL, Parquet, CSV, JSON, text, HTML, source code)
and planted phenomena: exact and near duplicates, PII (reserved/test values only), fake
secrets, low-quality pages, benchmark contamination, multiple domains and languages,
unknown and disallowed licenses, and a few corrupted records. ``ground_truth.jsonl``
records what was planted so filters can be scored for precision and recall.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from dataforge.dedup.shingles import shingle_hashes
from dataforge.synthetic.benchmarks import demo_code_items, demo_math_items, write_demo_benchmarks
from dataforge.synthetic.code import EXTENSIONS, CodeGenerator
from dataforge.synthetic.text import LEXICONS, TextGenerator

_WEB_DOMAINS: Final = (("news", 3), ("science", 3), ("technology", 3), ("legal", 1), ("general", 2))
_URL_HOSTS: Final = {
    "news": "news.example.com",
    "science": "research.example.org",
    "technology": "tech.example.com",
    "legal": "law.example.org",
    "general": "www.example.net",
}
_LOW_QUALITY_KINDS: Final = ("spam", "boilerplate", "short", "symbols", "urls", "lorem")
_CODE_MIX: Final = (("python", 8), ("javascript", 4), ("go", 3), ("rust", 3), ("java", 2))


@dataclass(frozen=True, slots=True)
class CorpusSpec:
    web_articles: int = 520
    web_low_quality: int = 60
    web_pii: int = 50
    web_exact_dups: int = 30
    web_near_dups: int = 40
    web_corrupted: int = 3
    encyclopedia: int = 220
    books: int = 24
    forum_posts: int = 240
    forum_pii: int = 40
    forum_contaminated: int = 10
    math_docs: int = 90
    math_exact_contaminated: int = 4
    math_paraphrase_contaminated: int = 6
    code_files: int = 110
    code_with_secrets: int = 14
    code_contaminated: int = 2
    code_gpl: int = 16
    html_docs: int = 30
    misc_syndicated: int = 40
    misc_multilingual: int = 30
    misc_unknown_license: int = 30

    def scaled(self, scale: float) -> CorpusSpec:
        """Scale every count, keeping at least two of each phenomenon."""
        values = {f.name: max(2, round(getattr(self, f.name) * scale)) for f in fields(self)}
        return replace(self, **values)


@dataclass(slots=True)
class GroundTruth:
    source_id: str
    record_id: str
    domain: str
    language: str = "en"
    license: str = "UNKNOWN"
    low_quality: str | None = None
    exact_duplicate_of: str | None = None
    near_duplicate_group: str | None = None
    near_duplicate_jaccard: float | None = None
    pii: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    contamination: list[dict[str, str]] = field(default_factory=list)
    corrupted: bool = False

    def as_json(self) -> dict[str, Any]:
        return {name.name: getattr(self, name.name) for name in fields(self)}


@dataclass(frozen=True, slots=True)
class CorpusSummary:
    root: Path
    files: int
    records: int
    bytes: int
    phenomena: dict[str, int]


class _Builder:
    def __init__(self, seed: int, spec: CorpusSpec) -> None:
        self.rng = random.Random(seed)
        self.text = TextGenerator(self.rng)
        self.code = CodeGenerator(self.text)
        self.spec = spec
        self.truth: list[GroundTruth] = []
        self.math_items = demo_math_items()
        self.code_items = demo_code_items()
        self._math_cursor = 0
        self.clean_web_articles: list[dict[str, Any]] = []

    def weighted(self, options: tuple[tuple[str, int], ...]) -> str:
        names = [name for name, _ in options]
        weights = [weight for _, weight in options]
        return self.rng.choices(names, weights=weights, k=1)[0]

    def timestamp(self) -> str:
        start = dt.datetime(2019, 1, 1, tzinfo=dt.UTC)
        offset = dt.timedelta(seconds=self.rng.randint(0, 6 * 365 * 24 * 3600))
        return (start + offset).replace(microsecond=0).isoformat()

    def next_math_item(self) -> dict[str, str]:
        item = self.math_items[self._math_cursor % len(self.math_items)]
        self._math_cursor += 1
        return item

    # ------------------------------------------------------------------ web.jsonl

    def web(self, path: Path) -> int:
        spec = self.spec
        rows: list[dict[str, Any]] = []
        articles: list[dict[str, Any]] = []
        for index in range(spec.web_articles):
            domain = self.weighted(_WEB_DOMAINS)
            title, text = self.text.document(domain)
            license_roll = self.rng.random()
            row: dict[str, Any] = {
                "id": f"web-{index:05d}",
                "url": f"https://{_URL_HOSTS[domain]}/articles/{index:05d}",
                "title": title,
                "text": text,
                "timestamp": self.timestamp(),
            }
            if license_roll < 0.88:
                row["license"] = "CC-BY-4.0"
            elif license_roll < 0.95:
                row["license"] = "proprietary"
            truth = GroundTruth("web", row["id"], domain, license=row.get("license", "UNKNOWN"))
            if index < spec.web_pii:
                email, phone = self.text.email(), self.text.phone()
                row["text"] += f"\n\nFor more information contact {email} or call {phone}."
                truth.pii = ["email", "phone"]
            rows.append(row)
            articles.append(row)
            self.truth.append(truth)

        for index in range(spec.web_low_quality):
            kind = _LOW_QUALITY_KINDS[index % len(_LOW_QUALITY_KINDS)]
            row = {
                "id": f"web-lq-{index:04d}",
                "url": f"https://spam.example.net/page/{index}",
                "text": self.text.low_quality(kind),
                "timestamp": self.timestamp(),
                "license": "CC-BY-4.0",
            }
            rows.append(row)
            self.truth.append(GroundTruth("web", row["id"], "general", license="CC-BY-4.0", low_quality=kind))

        licensed = [a for a in articles if a.get("license") == "CC-BY-4.0" and "contact" not in a["text"]]
        self.clean_web_articles = licensed
        for index in range(spec.web_exact_dups):
            original = licensed[index % len(licensed)]
            row = {**original, "id": f"web-dup-{index:04d}", "url": original["url"] + "?ref=feed"}
            rows.append(row)
            self.truth.append(
                GroundTruth(
                    "web",
                    row["id"],
                    self._domain_of(original),
                    license="CC-BY-4.0",
                    exact_duplicate_of=f"web:{original['id']}",
                )
            )

        for index in range(spec.web_near_dups):
            original = licensed[(index * 7 + 3) % len(licensed)]
            group = f"near-web-{index:04d}"
            row = {
                **original,
                "id": f"web-near-{index:04d}",
                "url": original["url"] + "/amp",
                "text": self.text.mutate(original["text"], edits=self.rng.randint(1, 2)),
            }
            rows.append(row)
            self._mark_near(f"web:{original['id']}", group)
            left, right = shingle_hashes(original["text"], 5), shingle_hashes(row["text"], 5)
            jaccard = len(np.intersect1d(left, right)) / max(1, len(np.union1d(left, right)))
            self.truth.append(
                GroundTruth(
                    "web",
                    row["id"],
                    self._domain_of(original),
                    license="CC-BY-4.0",
                    near_duplicate_group=group,
                    near_duplicate_jaccard=round(jaccard, 4),
                )
            )

        self.rng.shuffle(rows)
        lines = [json.dumps(row, ensure_ascii=False).encode("utf-8") for row in rows]
        for index in range(spec.web_corrupted):
            position = self.rng.randrange(len(lines))
            broken = b'{"id": "web-broken-%d", "text": "truncated record' % index
            lines.insert(position, broken)
            self.truth.append(GroundTruth("web", f"web-broken-{index}", "general", corrupted=True))
        # One legacy-encoded record: a Latin-1 byte inside otherwise valid UTF-8 JSON.
        _, legacy_text = self.text.document("general")
        legacy = json.dumps(
            {"id": "web-latin1-0", "text": legacy_text + " Café notes.", "license": "CC-BY-4.0"},
            ensure_ascii=False,
        )
        lines.insert(self.rng.randrange(len(lines)), legacy.encode("utf-8").replace(b"\xc3\xa9", b"\xe9"))
        self.truth.append(GroundTruth("web", "web-latin1-0", "general", license="CC-BY-4.0", corrupted=True))
        with path.open("wb") as handle:
            for line in lines:
                handle.write(line + b"\n")
        return len(lines)

    def _domain_of(self, row: dict[str, Any]) -> str:
        for truth in self.truth:
            if truth.source_id == "web" and truth.record_id == row["id"]:
                return truth.domain
        return "general"

    def _mark_near(self, key: str, group: str) -> None:
        source_id, record_id = key.split(":", 1)
        for truth in self.truth:
            if truth.source_id == source_id and truth.record_id == record_id:
                truth.near_duplicate_group = truth.near_duplicate_group or group
                return

    # ------------------------------------------------------------------ encyclopedia.parquet

    def encyclopedia(self, path: Path) -> int:
        ids, titles, texts, urls = [], [], [], []
        for index in range(self.spec.encyclopedia):
            domain = self.weighted((("general", 5), ("science", 3), ("mathematics", 2)))
            title, text = self.text.document(domain, min_sentences=14, max_sentences=26)
            record_id = f"enc-{index:05d}"
            ids.append(record_id)
            titles.append(title)
            texts.append(text)
            urls.append(f"https://wiki.example.org/wiki/{record_id}")
            self.truth.append(GroundTruth("encyclopedia", record_id, domain, license="CC-BY-SA-4.0"))
        table = pa.table(
            {
                "id": ids,
                "title": titles,
                "text": texts,
                "url": urls,
                "license": ["CC-BY-SA-4.0"] * len(ids),
            }
        )
        pq.write_table(table, path, compression="zstd")
        return len(ids)

    # ------------------------------------------------------------------ books/*.txt

    def books(self, directory: Path) -> int:
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(self.spec.books):
            name = f"book-{index:03d}.txt"
            body = self.text.long_document("books", paragraphs=self.rng.randint(10, 22))
            (directory / name).write_text(body + "\n", encoding="utf-8", newline="\n")
            self.truth.append(GroundTruth("books", f"books/{name}", "books", license="LicenseRef-PublicDomain"))
        return self.spec.books

    # ------------------------------------------------------------------ forum.csv

    def forum(self, path: Path) -> int:
        spec = self.spec
        rows: list[dict[str, str]] = []
        pii_makers: tuple[tuple[str, Callable[[], str]], ...] = (
            ("email", lambda: f"You can email me at {self.text.email()} if that is easier."),
            ("phone", lambda: f"Or text me on {self.text.phone()} after six."),
            (
                "ip_address",
                lambda: f"My router shows the public address {self.text.ip_address()} right now.",
            ),
            ("credit_card", lambda: f"The shop charged my card {self.text.credit_card()} twice!"),
            ("us_ssn", lambda: f"They even asked for my SSN {self.text.ssn_like()} on the form."),
            ("iban", lambda: f"Please send the refund to IBAN {self.text.iban()} instead."),
        )
        for index in range(spec.forum_posts):
            _, text = self.text.document("forums", min_sentences=5, max_sentences=10)
            record_id = f"forum-{index:05d}"
            truth = GroundTruth("forum", record_id, "forums", license="CC-BY-SA-4.0")
            if index < spec.forum_pii:
                pii_type, maker = pii_makers[index % len(pii_makers)]
                text += " " + maker()
                truth.pii = [pii_type]
            rows.append(
                {
                    "id": record_id,
                    "thread": f"t{index // 4:04d}",
                    "text": text,
                    "timestamp": self.timestamp(),
                }
            )
            self.truth.append(truth)
        for index in range(spec.forum_contaminated):
            item = self.next_math_item()
            text = (
                "Can someone help me with this homework question? "
                + item["question"]
                + " I keep getting a different answer from the one in the back of the book, "
                "and I would really like to understand where my reasoning goes wrong."
            )
            record_id = f"forum-hw-{index:04d}"
            rows.append(
                {
                    "id": record_id,
                    "thread": f"hw{index:03d}",
                    "text": text,
                    "timestamp": self.timestamp(),
                }
            )
            self.truth.append(
                GroundTruth(
                    "forum",
                    record_id,
                    "forums",
                    license="CC-BY-SA-4.0",
                    contamination=[{"benchmark": "demo_math", "item_id": item["id"], "kind": "substring"}],
                )
            )
        self.rng.shuffle(rows)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "thread", "text", "timestamp"])
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)

    # ------------------------------------------------------------------ math.jsonl

    def math(self, path: Path) -> int:
        spec = self.spec
        rows: list[dict[str, str]] = []
        for index in range(spec.math_docs):
            _, text = self.text.document("mathematics", min_sentences=10, max_sentences=18)
            record_id = f"math-{index:04d}"
            rows.append({"id": record_id, "text": text})
            self.truth.append(GroundTruth("math", record_id, "mathematics", license="CC-BY-4.0"))
        for index in range(spec.math_exact_contaminated):
            item = self.next_math_item()
            record_id = f"math-leak-{index:03d}"
            rows.append({"id": record_id, "text": item["question"]})
            self.truth.append(
                GroundTruth(
                    "math",
                    record_id,
                    "mathematics",
                    license="CC-BY-4.0",
                    contamination=[{"benchmark": "demo_math", "item_id": item["id"], "kind": "exact"}],
                )
            )
        for index in range(spec.math_paraphrase_contaminated):
            item = self.next_math_item()
            record_id = f"math-para-{index:03d}"
            intro = self.text.document("mathematics", min_sentences=3, max_sentences=4)[1]
            text = f"{intro}\n\nWorked example. {self.text.mutate(item['question'], edits=3)}"
            rows.append({"id": record_id, "text": text})
            self.truth.append(
                GroundTruth(
                    "math",
                    record_id,
                    "mathematics",
                    license="CC-BY-4.0",
                    contamination=[{"benchmark": "demo_math", "item_id": item["id"], "kind": "paraphrase"}],
                )
            )
        self.rng.shuffle(rows)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return len(rows)

    # ------------------------------------------------------------------ code trees

    def code_tree(self, directory: Path, source_id: str, count: int, license_id: str, *, plant: bool) -> int:
        secret_kinds = (
            "aws_access_key_id",
            "github_token",
            "slack_token",
            "password",
            "private_key",
        )
        truth_types = {
            "aws_access_key_id": "aws_access_key_id",
            "github_token": "github_token",
            "slack_token": "slack_token",
            "password": "password_assignment",
            "private_key": "private_key",
        }
        for index in range(count):
            language = self.weighted(_CODE_MIX)
            secret = None
            if plant and index < self.spec.code_with_secrets:
                secret = secret_kinds[index % len(secret_kinds)]
                if secret == "private_key" and language not in {"python", "javascript"}:
                    language = "python"
            embed = None
            contamination: list[dict[str, str]] = []
            if plant and secret is None and index < self.spec.code_with_secrets + self.spec.code_contaminated:
                language = "python"
                item = self.code_items[index % len(self.code_items)]
                embed = item["prompt"] + item["canonical_solution"]
                contamination = [{"benchmark": "demo_code", "item_id": item["id"], "kind": "substring"}]
            relative = f"{language}/module_{index:04d}{EXTENSIONS[language]}"
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                self.code.source_file(language, secret=secret, embed=embed),
                encoding="utf-8",
                newline="\n",
            )
            self.truth.append(
                GroundTruth(
                    source_id,
                    f"{directory.name}/{relative}",
                    "code",
                    language="code",
                    license=license_id,
                    secrets=[truth_types[secret]] if secret else [],
                    contamination=contamination,
                )
            )
        return count

    # ------------------------------------------------------------------ docs/*.html

    def html_docs(self, directory: Path) -> int:
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(self.spec.html_docs):
            title, text = self.text.document("technology", min_sentences=8, max_sentences=14)
            paragraphs = "".join(f"<p>{p}</p>\n" for p in text.split("\n\n"))
            code_block = ""
            if index % 3 == 0:
                code_block = "<pre><code>pip install dataforge-llm\ndataforge doctor\n</code></pre>\n"
            html = (
                f"<!DOCTYPE html>\n<html><head><title>{title}</title>"
                "<style>body { font-family: sans-serif; }</style>"
                "<script>window.analytics = [];</script></head>\n<body>\n"
                f"<nav><a href='/'>Home</a> <a href='/docs'>Docs</a></nav>\n<main><h1>{title}</h1>\n"
                f"{paragraphs}{code_block}</main>\n</body></html>\n"
            )
            name = f"guide-{index:03d}.html"
            (directory / name).write_text(html, encoding="utf-8", newline="\n")
            self.truth.append(GroundTruth("docs", f"docs/{name}", "technology", license="CC-BY-4.0"))
        return self.spec.html_docs

    # ------------------------------------------------------------------ misc.json

    def misc(self, path: Path) -> int:
        spec = self.spec
        web_rows = self.clean_web_articles
        rows: list[dict[str, Any]] = []
        for index in range(spec.misc_syndicated):
            original = web_rows[(index * 11 + 5) % len(web_rows)]
            record_id = f"misc-syn-{index:04d}"
            rows.append({"id": record_id, "text": original["text"], "license": "CC-BY-4.0"})
            self.truth.append(
                GroundTruth(
                    "misc",
                    record_id,
                    self._domain_of(original),
                    license="CC-BY-4.0",
                    exact_duplicate_of=f"web:{original['id']}",
                )
            )
        languages = ("es", "fr", "de")
        for index in range(spec.misc_multilingual):
            language = languages[index % len(languages)]
            record_id = f"misc-{language}-{index:04d}"
            rows.append(
                {
                    "id": record_id,
                    "text": self.text.multilingual(language, sentences=self.rng.randint(7, 12)),
                    "license": "CC-BY-4.0",
                }
            )
            self.truth.append(GroundTruth("misc", record_id, "news", language=language, license="CC-BY-4.0"))
        for index in range(spec.misc_unknown_license):
            domain = self.rng.choice(sorted(LEXICONS))
            record_id = f"misc-unk-{index:04d}"
            rows.append({"id": record_id, "text": self.text.document(domain)[1]})
            self.truth.append(GroundTruth("misc", record_id, domain))
        self.rng.shuffle(rows)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return len(rows)


def generate_sample_corpus(root: Path, *, seed: int = 7, scale: float = 1.0) -> CorpusSummary:
    """Write the sample corpus under ``root`` and return a summary.

    Layout: ``raw/`` (sources), ``benchmarks/`` (demo evaluation sets), and
    ``ground_truth.jsonl`` (planted labels, never read by the pipeline).
    """
    if scale <= 0:
        raise ValueError("scale must be positive")
    spec = CorpusSpec().scaled(scale)
    builder = _Builder(seed, spec)
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    records = 0
    records += builder.web(raw / "web.jsonl")
    records += builder.encyclopedia(raw / "encyclopedia.parquet")
    records += builder.books(raw / "books")
    records += builder.forum(raw / "forum.csv")
    records += builder.math(raw / "math.jsonl")
    records += builder.code_tree(raw / "code", "code", spec.code_files, "MIT", plant=True)
    records += builder.code_tree(raw / "code-gpl", "code_gpl", spec.code_gpl, "GPL-3.0-only", plant=False)
    records += builder.html_docs(raw / "docs")
    records += builder.misc(raw / "misc.json")
    write_demo_benchmarks(root / "benchmarks")

    with (root / "ground_truth.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for truth in builder.truth:
            handle.write(json.dumps(truth.as_json(), ensure_ascii=False, sort_keys=True) + "\n")

    phenomena: Counter[str] = Counter()
    for truth in builder.truth:
        phenomena["records"] += 1
        phenomena["low_quality"] += truth.low_quality is not None
        phenomena["exact_duplicates"] += truth.exact_duplicate_of is not None
        phenomena["near_duplicates"] += truth.near_duplicate_group is not None and truth.record_id.startswith(
            "web-near"
        )
        phenomena["pii_records"] += bool(truth.pii)
        phenomena["secret_records"] += bool(truth.secrets)
        phenomena["contaminated_records"] += bool(truth.contamination)
        phenomena["non_english"] += truth.language not in {"en", "code"}
        phenomena["corrupted"] += truth.corrupted
    files = [p for p in raw.rglob("*") if p.is_file()]
    summary = CorpusSummary(
        root=root,
        files=len(files),
        records=records,
        bytes=sum(p.stat().st_size for p in files),
        phenomena=dict(sorted(phenomena.items())),
    )
    (root / "SUMMARY.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "scale": scale,
                "files": summary.files,
                "records": summary.records,
                "bytes": summary.bytes,
                "phenomena": summary.phenomena,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary
