# Deduplication

DataForge removes duplicates at three granularities. All three are deterministic: the same
input in the same order always keeps the same documents.

## Exact deduplication (`exact_dedup`, barrier)

- **Key.** The xxh3-128 hash of the document text after normalization (`content_hash`),
  stored as two unsigned 64-bit halves. 128 bits make accidental collisions negligible at any
  corpus size this tool targets.
- **Memory.** About 60 bytes per document (hash halves, byte length, quality score, document
  id), independent of document size. Grouping is one vectorized `lexsort`.
- **Representative policy** (`stages.exact_dedup.policy`):

| Policy | Survivor | Tie-break |
| :--- | :--- | :--- |
| `keep_first` (default) | Earliest in canonical ingestion order (source order in the registry, then record order) | - |
| `keep_longest` | Most bytes | Ingestion order |
| `keep_highest_quality` | Highest `quality_score` | Most bytes, then ingestion order |

  Exact duplicates are byte-identical after normalization, so `keep_first` loses no content;
  only provenance differs. `keep_highest_quality` requires the quality stage earlier in
  `stage_order`.
- **Annotations.** Every member of a duplicate group, including the survivor, gets
  `exact_dup_group = content_hash`, which also keeps the group in one split.
- **Report.** `reports/exact_duplicates.parquet` lists each removed document with the
  document it duplicates; the report shows documents before and after, duplicates, duplicate
  bytes, group count, and the largest group.

## Paragraph deduplication (`paragraph_dedup`, streaming, off by default)

Removes paragraphs (blocks separated by blank lines) that already occurred earlier in the
stream, such as boilerplate footers or syndicated blocks, while keeping each document's
first occurrences.

- Paragraphs shorter than `min_chars` (40) are never removed.
- The key is the lower-cased, whitespace-collapsed paragraph.
- Membership uses a fixed-memory Bloom filter sized from `capacity` (10 million) and
  `error_rate` (0.001), with double hashing over xxh3-128. A false positive removes a unique
  paragraph with probability about `error_rate`; that is the price of a hard memory bound.
- `apply_to` selects content kinds (text only by default; code license headers and
  repeated blocks are left alone).
- The stage is order-dependent, so its filter is snapshotted at every checkpoint and restored
  on resume. A document whose paragraphs are all duplicates is rejected
  (`all_paragraphs_duplicate`).

## Near-duplicate deduplication (`near_dedup`, barrier)

### Signatures

1. Text is lower-cased and split into word tokens (`\w+`); punctuation and whitespace
   differences are ignored.
2. Shingles are consecutive word `shingle_size`-grams (5 by default). Each word is hashed
   once with xxh3-64 and windows are combined with a polynomial rolling hash, so shingle
   strings are never materialized. Documents shorter than one shingle form a single shingle;
   documents with no word tokens are excluded from clustering.
3. MinHash uses `num_perm` (128) universal hash permutations `(a * x + b) mod p` with
   `p = 2^32 - 5`, chosen so every intermediate product fits in uint64 without overflow.
   Signatures are computed for a whole batch at once in bounded chunks and appended to a
   spill file that is memory-mapped at finalization.

### Candidate generation (LSH)

Signatures are split into `bands` of `rows` values; documents that share any band hash become
candidates. The probability that a pair with Jaccard similarity `s` becomes a candidate is
`1 - (1 - s^rows)^bands`.

When `bands`/`rows` are not set, DataForge picks the layout with `bands * rows <= num_perm`
that has the smallest false-positive area below the threshold **among layouts whose candidate
probability at the threshold is at least 0.98**. Candidates are always re-verified against
the full signatures, so a false positive costs one vectorized comparison while a false
negative silently keeps a duplicate. The classic objective that weights false-positive and
false-negative areas equally was measured during development and rejected: for threshold
0.85 and 128 permutations it selects 8 bands x 16 rows, whose candidate probability at the
threshold is about 0.46. The recall-constrained rule selects 13 bands x 8 rows (about 0.98 at
the threshold) for the same setting.

Within each band bucket, every member is paired with the bucket's first member (the anchor,
lowest ordinal). Buckets are capped at `max_bucket_size` (500) members to bound the cost of
templated pages that collide massively.

### Verification and clustering

A candidate pair is accepted when the fraction of equal signature positions (the MinHash
estimate of Jaccard similarity) is at least `threshold` (0.85). Accepted pairs are merged
with union-find; each connected component is a cluster.

### Representatives and reporting

The representative is chosen with the same policies as exact dedup; the default is
`keep_longest`, which keeps the most complete version of an edited copy. Every cluster member
is annotated with `near_dup_cluster` (`nd-` followed by the first 16 hex characters of the
representative's document id) and `near_dup_similarity`, the estimated Jaccard similarity to
the representative. Because clusters are transitive, a member's similarity to the
representative can be below the threshold even though each linking pair was above it.
`reports/near_duplicate_clusters.parquet` lists every member with its cluster, representative,
role, similarity, and size.

## Interaction with splits

Duplicate groups never straddle splits: the split key of a document is its near-duplicate
cluster, else its exact-duplicate group, else its content hash. Dataset validation checks
this property on every published dataset.

## Limitations

- MinHash estimates Jaccard similarity with standard error about
  `sqrt(J(1 - J) / num_perm)`; pairs near the threshold can land on either side.
- Word shingles detect copy-edits, not paraphrases or translations.
- Near-duplicate detection compares whole documents. A short document embedded in a long one
  has low Jaccard similarity to it; contamination detection uses containment for that case.
- Exact and near dedup keep per-document keys in memory (tens of bytes per document plus a
  memory-mapped signature file); see [ADR-002](../adr/ADR-002-streaming-bounded-memory.md).

Measured removal counts on the sample corpus are in the
[README measured results](../../README.md#measured-results).
