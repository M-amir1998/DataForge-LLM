# data/

Local input data. Everything in this directory except this README is ignored by Git: corpora,
generated samples, and downloaded datasets must never be committed.

Typical contents:

| Path | Created by | Used by |
| :--- | :--- | :--- |
| `data/sample/` | `dataforge sample generate data/sample` | `configs/pipelines/demo.yaml` |
| `data/smoke/` | `dataforge sample generate data/smoke --scale 0.1` | `configs/pipelines/smoke.yaml` and CI |

Each generated corpus contains `raw/` (the source files), `benchmarks/` (copies of the demo
evaluation sets), `ground_truth.jsonl` (what was planted, for scoring filters; never read by the
pipeline), and `SUMMARY.json`. Generation is deterministic for a given seed and scale.

Real corpora can live anywhere; point a source registry at them (paths resolve against the
registry's `base_dir`).
