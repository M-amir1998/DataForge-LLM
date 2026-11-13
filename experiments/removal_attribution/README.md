# Experiment: removal attribution

**Question.** On a representative small corpus, how much data is removed by exact
deduplication, near deduplication, quality filtering, PII handling, and contamination
filtering, and how accurately does each filter act on the phenomena it targets?

**Why it matters.** Removal statistics in a single pipeline order conflate filters: a page
removed by deduplication never reaches the quality filter. Reporting both the sequential
(marginal) and the independent (standalone) view, plus the overlap between filters, shows
how much each filter actually contributes and where they compete.

## Method

1. Generate the synthetic sample corpus (`dataforge sample generate`, seed 7, scale 2.0).
   It plants exact and near duplicates, PII, fake credentials, low-quality pages, benchmark
   leaks (exact, substring, paraphrased), non-English pages, and unknown/disallowed licenses,
   and writes `ground_truth.jsonl` with the planted labels.
2. **Run A (sequential):** the demo pipeline in filter mode (mixture disabled).
3. **Run B (independent):** the same pipeline with every filtering stage in `annotate` mode,
   so each filter sees the full corpus and records a flag instead of removing.
4. Join the removal audit log, output flags, and detection artifacts with the ground truth
   in Polars, and compute rates, overlaps, recall, and precision. Metric definitions are in
   the docstring of [`run.py`](run.py); duplicates are scored per planted group.

## Reproduce

```bash
uv run python experiments/removal_attribution/run.py --scale 2.0 --seed 7
```

The run takes well under a minute on a laptop CPU and writes
[`results/RESULTS.md`](results/RESULTS.md) and `results/results.json`.

## Results

See [`results/RESULTS.md`](results/RESULTS.md) for the measured tables. Highlights are
summarized in the project README.

## Threats to validity

- The corpus is synthetic and template-generated; precision and recall here measure the
  filters on planted phenomena, not on real web text.
- Ground-truth "low quality" means the generator's junk-page templates; real low-quality
  text is more varied.
- The language filter and quality filter overlap heavily on junk pages; attribution in the
  sequential run depends on stage order (documented in `docs/architecture/pipeline.md`).
