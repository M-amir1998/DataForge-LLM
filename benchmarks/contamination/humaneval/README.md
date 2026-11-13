# HumanEval (optional, local copy)

Python function-completion problems from OpenAI's `human-eval` repository.

- **Upstream:** https://github.com/openai/human-eval
- **License:** MIT (verify the current license in the upstream repository before use).
- **Registry entry:** `humaneval` in `../registry.yaml`, protecting the `prompt` and
  `canonical_solution` fields; items are identified by `task_id`.

DataForge never downloads this file. The upstream file is gzip-compressed, and the benchmark
registry reads plain JSONL, so decompress it after reviewing the license:

```bash
curl -L -o /tmp/HumanEval.jsonl.gz https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz
gunzip -c /tmp/HumanEval.jsonl.gz > benchmarks/contamination/humaneval/HumanEval.jsonl
sha256sum benchmarks/contamination/humaneval/HumanEval.jsonl
```

The file is excluded from Git by `.gitignore`. Short canonical solutions with fewer words than
`ngram_size` are checked only by the exact and MinHash methods.
