# MBPP (optional, local copy)

Mostly Basic Python Problems from the `google-research` repository.

- **Upstream:** https://github.com/google-research/google-research/tree/master/mbpp
- **License:** CC-BY-4.0 (verify the current license in the upstream repository before use;
  attribution is required).
- **Registry entry:** `mbpp` in `../registry.yaml`, protecting the `text` (problem statement)
  and `code` (reference solution) fields; items are identified by `task_id`.

DataForge never downloads this file. To enable the check, fetch it yourself after reviewing
the license:

```bash
curl -L -o benchmarks/contamination/mbpp/mbpp.jsonl \
  https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl
sha256sum benchmarks/contamination/mbpp/mbpp.jsonl
```

The file is excluded from Git by `.gitignore`.
