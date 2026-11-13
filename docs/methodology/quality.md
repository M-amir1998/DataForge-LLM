# Quality scoring

The quality stage computes interpretable heuristic signals for every document and combines
them into a keep/reject decision. These heuristics approximate what is undesirable in web
text (spam, boilerplate, garbage, extreme repetition); they do not measure human-judged
quality, usefulness, or truthfulness.

## Signals

Each signal measures one property, compares it with a threshold from
`stages.quality.signals.<name>`, and declares which content kinds it applies to (prose
heuristics are meaningless for source code and vice versa). Words below are `\w+` tokens;
tokens are whitespace-separated strings; lines are non-empty, stripped lines unless noted.

| Signal | Value | Default pass condition | Applies to | Inspired by |
| :--- | :--- | :--- | :--- | :--- |
| `length` | characters | 200 to 1,000,000 characters and at least 30 words | all | Gopher |
| `mean_word_length` | mean word length | between 3 and 10 | text, mixed; skipped for zh, ja, ko, th | Gopher |
| `alpha_words` | share of tokens containing a letter | at least 0.8 | text, mixed | Gopher |
| `symbol_ratio` | (`#` count + `...` count + ellipsis count) / tokens | at most 0.1 | text, mixed | Gopher |
| `line_repetition` | share of lines that repeat an earlier line | at most 0.3 (and repeated-line characters at most 0.2) | text, mixed | Gopher |
| `ngram_repetition` | share of word 3-gram occurrences that repeat an earlier 3-gram | at most 0.35 | text, mixed | Gopher (simplified) |
| `char_entropy` | Shannon entropy of the character distribution, bits | at least 3.0 (not applied below 50 characters) | all | generic |
| `url_ratio` | share of characters inside URLs | at most 0.2 | text, mixed | generic |
| `boilerplate` | share of short lines (under 120 characters) matching boilerplate patterns (cookies, privacy policy, terms of use, sign in, subscribe, click here, share on, all rights reserved, ...); any "lorem ipsum" fails outright | at most 0.3 | text, mixed | C4 |
| `terminal_punctuation` | share of lines with at least 3 words that end in terminal punctuation or a closing quote/bracket | at least 0.3 (fails when no such line exists) | text | C4 |
| `stopwords` | number of distinct words from {the, be, to, of, and, that, have, with} | at least 2; only for documents whose language is in `languages` (default `en`) | text, mixed | Gopher |
| `language_confidence` | language identification confidence | at least 0.5; only when the language stage annotated a confidence | text, mixed | CCNet-style LID filtering |
| `sentence_length` | words per sentence (sentences split after `.`, `!`, `?`) | between 3 and 80 | text | generic |
| `code_line_length` | mean line length (maximum line length reported as detail) | maximum at most 1000 and mean at most 100 | code | The Stack |
| `alnum_ratio` | share of alphanumeric characters | at least 0.25 | code | The Stack |

Every signal can be disabled (`enabled: false`) or re-weighted (`weight`, default 1.0).
Extra boilerplate patterns can be added with `boilerplate.extra_patterns`.

Signal values for applicable signals are stored per document in the `quality_signals` map
column, so thresholds can be revisited later with `dataforge quality analyze DATASET
--config CONFIG` without re-running the pipeline.

## Combination

- **`quality_score`** is the weighted fraction of applicable signals that pass, in `[0, 1]`.
  It is a transparent summary for ranking and distribution plots, not a calibrated
  probability.
- **`combine: all_required`** (default): a document is kept only if every applicable signal
  passes.
- **`combine: min_score`**: a document is kept if `quality_score >= min_score`.

The rejection reason is recorded as `quality:<signal>`, naming the first failing signal in
the fixed order of the table above. Because signals overlap (spam often fails several), the
attribution is to that first signal; the per-signal failure counts in the report count every
failure independently. `mode: annotate` keeps all documents and records flags instead.

`configs/quality/default.yaml` spells out the defaults; `configs/quality/permissive.yaml` is
a score-based preset that relaxes prose-specific rules.

## Calibration note

During development the `ngram_repetition` threshold was checked against real English prose:
on the 34 topic pages of more than 300 words in Python's bundled `pydoc_data` documentation,
the 3-gram repeat ratio had a median of 0.106 and a maximum of 0.226, below the 0.35
threshold. Python's own license file, which repeats clauses across several licenses, scored
0.419 and would be rejected, which is the intended behaviour for license boilerplate. Treat
this as a sanity check on one text type, not as a validation of the threshold for every
corpus.

## Learned quality models

`stages.quality.learned_model` plugs in any model object with
`predict(texts) -> probabilities` through a `module:factory` entrypoint. Its probability
becomes an extra signal, `learned_model`, with its own `threshold` and `weight`, combined
like the heuristics. v0.1 ships no model. CPU-friendly choices are fastText or linear
models over hashed n-grams; GPU models can run as a separate scoring job whose scores are
joined back by document id. The entrypoint executes code, so only reference modules you
control (see [SECURITY](../../SECURITY.md)).

## Limitations

- The heuristics favour well-formed prose. Lists, tables, poetry, transcripts, and
  dialectal text can be removed; review the report's per-signal failure counts before
  adopting thresholds for a new corpus.
- Language-dependent signals cover English by default.
- Code receives only four signals (length, character entropy, line length, alphanumeric
  ratio); there is no syntax validation or code-quality model.

Measured quality removals on the sample corpus are in the
[README measured results](../../README.md#measured-results).
