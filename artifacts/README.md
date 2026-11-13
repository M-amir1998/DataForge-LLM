# artifacts/

Scratch space for locally exported artifacts that should not live in Git: ad-hoc exports of
reports, benchmark outputs you want to share by other means, or files handed to other tools.
Everything in this directory except this README is ignored by Git.

Datasets and work areas live under `.dataforge/` (configurable with `storage.output_dir` and
`storage.work_dir`); measured benchmark results live in `benchmarks/performance/results/`.
