# Dataset mixtures

The mixture stage (barrier, off by default) shapes the domain composition of the output, for
example "45% general, 25% code, 15% mathematics, 15% science". Domains come from the
classify stage, which must run earlier.

```yaml
stages:
  mixture:
    enabled: true
    unit: tokens            # documents | bytes | tokens
    weights: {general: 0.45, code: 0.25, mathematics: 0.15, science: 0.15}
    target_total: null      # optional cap, in the chosen unit
    unlisted_domains: drop  # or keep
    seed: 0
```

Weights must be non-negative and sum to 1 (validated when the config loads).

## Units

| Unit | Each document counts as | Use when |
| :--- | :--- | :--- |
| `documents` | 1 | Composition by item count matters (e.g. instruction-like data) |
| `bytes` | UTF-8 bytes of its text | A tokenizer-independent proxy for training compute |
| `tokens` | `token_estimate` from the configured estimator | Weights should reflect training tokens |

The units disagree whenever document lengths differ by domain: sampling by documents
over-represents domains with short documents when measured in tokens, and vice versa.

Token estimates come from `tokenizer` in the pipeline config:

| `tokenizer.kind` | Estimate |
| :--- | :--- |
| `chars` (default) | `ceil(characters / chars_per_token)`, 4.0 characters per token by default |
| `words` | `ceil(whitespace tokens x tokens_per_word)`, 1.3 by default |
| `hf` | Exact counts from a local `tokenizer.json` via the `tokenizers` library (`tokenizers` extra, CPU only, no model weights) |

## Allocation

Let `A_d` be the available units in weighted domain `d` and `w_d` its weight. Without
upsampling, the largest total that honours every weight is

```text
T = min over d of A_d / w_d
```

or `target_total` when that is smaller. Each domain then receives `t_d = w_d * T` units. If a
`target_total` larger than `T` is requested, `T` is used and the report notes it. If a weighted
domain has no documents at all, the remaining weights are renormalized and the report notes
which domains were missing.

## Selection

Within each domain, documents are taken in increasing order of a seeded hash of their
document id (`seed` salts the hash) until the cumulative units would exceed `t_d`.
Consequences:

- **Deterministic and order-independent:** the same documents are selected regardless of
  input order (tested by shuffling).
- **Stable as data grows:** a document's priority depends only on its id, so adding unrelated
  documents does not reshuffle which existing documents are selected.
- Achieved weights match targets to within one document per domain.

Domains not listed in `weights` are dropped (`unlisted_domains: drop`, default) or kept in
full without counting toward the proportions (`keep`).

## Reporting

The report and `reports/summary.json` include, per domain: target and achieved weight,
available and selected units, selected documents, and sampling rate; plus a target-versus-
achieved chart. `dataforge mixture build CONFIG` runs the pipeline up to the mixture and prints
the allocation.

## Limitations

- **No upsampling in v0.1.** A scarce domain caps the total; there is no repetition of
  documents to reach a weight. Upsampling would need repeat indices to keep document ids
  unique in the output and is on the roadmap.
- Mixture quality is bounded by domain classification quality (rule-based in v0.1).
- `tokens` with the `chars` or `words` estimators is an approximation; use `hf` for exact
  counts with the target tokenizer.
