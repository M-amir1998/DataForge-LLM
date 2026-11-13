# GSM8K (optional, local copy)

Grade-school math word problems from OpenAI's `grade-school-math` repository.

- **Upstream:** https://github.com/openai/grade-school-math
- **License:** MIT (verify the current license in the upstream repository before use).
- **Registry entry:** `gsm8k` in `../registry.yaml`, protecting the `question` field; items are
  identified by line number.

DataForge never downloads this file. To enable the check, fetch the test split yourself,
after reviewing the license, and place it here as `test.jsonl`:

```bash
curl -L -o benchmarks/contamination/gsm8k/test.jsonl \
  https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl
sha256sum benchmarks/contamination/gsm8k/test.jsonl   # record the digest for your audit trail
```

The file is excluded from Git by `.gitignore`. The next pipeline run records its SHA-256 in
the manifest, and because benchmark files are part of the pipeline fingerprint, the run
produces a new dataset version.
