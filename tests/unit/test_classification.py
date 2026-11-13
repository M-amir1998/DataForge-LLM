from __future__ import annotations

import pytest

from dataforge.classification.code import assess, is_code_line
from dataforge.classification.domain import DomainClassifier
from dataforge.classification.language import BuiltinLanguageDetector, build_detector
from dataforge.config.models import ClassifyConfig
from dataforge.domain.document import ContentKind
from dataforge.errors import OptionalDependencyMissing

DETECTOR = BuiltinLanguageDetector()


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("The committee said that the plan would be reviewed by the end of the year because it was late.", "en"),
        ("El gobierno dijo que los resultados son muy buenos y que la ciudad va a crecer este año.", "es"),
        ("Les habitants du quartier ont demandé plus de transparence sur le budget et sont satisfaits.", "fr"),
        ("Die Stadtverwaltung hat angekündigt, dass die neue Brücke im Herbst eröffnet wird und sicher ist.", "de"),
        ("La città è molto bella e questo progetto della regione non è ancora finito per gli abitanti.", "it"),
        ("Het project van de stad is niet klaar maar de bewoners zijn tevreden met deze plannen.", "nl"),
        ("这是一个关于数据工程的中文句子，用于测试语言识别。", "zh"),
        ("これは日本語の文章です。言語識別のテストに使います。", "ja"),
        ("데이터 엔지니어링에 관한 한국어 문장입니다.", "ko"),
        ("Это предложение на русском языке для проверки.", "ru"),
        ("Це речення українською мовою для перевірки їжі.", "uk"),
        ("هذه جملة باللغة العربية لاختبار التعرف على اللغة.", "ar"),
    ],
)
def test_builtin_detector_languages(text: str, language: str) -> None:
    prediction = DETECTOR.detect(text)
    assert prediction.language == language
    assert 0.0 < prediction.confidence <= 1.0


def test_detector_undetermined_for_non_language() -> None:
    for text in ("", "12345 67890", "### $$$ ###", "https://a.example/1 https://b.example/2"):
        assert DETECTOR.detect(text).language == "und"


def test_short_ambiguous_text_has_lower_confidence() -> None:
    long = DETECTOR.detect("The results of the study were published in the journal and they are now available.")
    short = DETECTOR.detect("the plan")
    assert short.confidence < long.confidence


def test_optional_py3langid_adapter_reports_missing_extra() -> None:
    try:
        import py3langid  # noqa: F401
    except ImportError:
        with pytest.raises(OptionalDependencyMissing, match="langid"):
            build_detector("py3langid", 1000)
    else:
        assert build_detector("py3langid", 1000).detect("This is plainly an English sentence.").language == "en"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("def compute(values):", True),
        ("    return x + y;", True),
        ("const total = items.reduce((a, b) => a + b, 0);", True),
        ("#include <stdio.h>", True),
        ("The quick brown fox jumps over the lazy dog.", False),
        ("https://links.example.net/1234/a", False),
        ("See https://docs.example.com/api for details.", False),
        ("", False),
    ],
)
def test_code_line_heuristics(line: str, expected: bool) -> None:
    assert is_code_line(line) is expected


def test_assess_text_code_mixed() -> None:
    code = (
        "import os\n\ndef main():\n    path = os.getcwd()\n    return path\n\nif __name__ == '__main__':\n    main()\n"
    )
    prose = "This guide explains the deployment process.\nIt covers configuration and rollback.\nEach step is short.\n"
    mixed = prose * 2 + "```\npip install dataforge-llm\ndataforge doctor\n```\n"
    assert assess(code).kind is ContentKind.CODE
    assert assess(prose).kind is ContentKind.TEXT
    assert assess(mixed).kind is ContentKind.MIXED
    assert assess("").kind is ContentKind.TEXT


def test_url_lists_are_not_code() -> None:
    urls = "\n".join(f"https://links.example.net/{i}/a" for i in range(30))
    assert assess(urls).kind is ContentKind.TEXT


def test_domain_classifier_precedence() -> None:
    classifier = DomainClassifier(ClassifyConfig())
    science = "Researchers measured protein samples in the laboratory to test the hypothesis about cell growth."
    assert classifier.classify(science, kind=ContentKind.TEXT, domain_hint="books").domain == "books"
    assert classifier.classify("x = 1", kind=ContentKind.CODE).domain == "code"
    prediction = classifier.classify(science, kind=ContentKind.TEXT)
    assert (prediction.domain, prediction.method) == ("science", "keywords")
    assert classifier.classify("Hello there friend.", kind=ContentKind.TEXT).domain == "general"
    via_url = classifier.classify("Hello there friend.", kind=ContentKind.TEXT, url="https://news.example.com/a")
    assert (via_url.domain, via_url.method) == ("news", "url_rule")


def test_domain_classifier_hints_can_be_disabled_and_extended() -> None:
    config = ClassifyConfig(use_source_hints=False, extra_keywords={"legal": ("gdpr", "controller")})
    classifier = DomainClassifier(config)
    text = "The controller must report a gdpr breach to the court and the tribunal under the statute."
    assert classifier.classify(text, kind=ContentKind.TEXT, domain_hint="books").domain == "legal"
