# ADR-003: Exact and near-duplicate strategy

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Duplicates waste training compute, encourage memorization, and leak evaluation data across
splits. Exact copies are cheap to find; edited copies (changed words, added datelines or
footers) require similarity search, which is quadratic if done naively.

## Decision

1. **Exact dedup first**, on xxh3-128 hashes of normalized text, with deterministic
   representative policies (`keep_first` by default).
2. **Optional paragraph dedup** with a fixed-memory Bloom filter for repeated blocks inside
   otherwise distinct documents.
3. **Near dedup with MinHash and LSH** over word 5-gram shingles: 128 permutations,
   Jaccard threshold 0.85 by default, representatives chosen by `keep_longest` by default.
4. **Recall-constrained LSH tuning:** pick the band layout with the smallest false-positive
   area among layouts whose candidate probability at the threshold is at least 0.98, then
   **verify every candidate** against full signatures before merging with union-find.
5. Duplicate groups and clusters determine the **split key**, so a group never spans two
   splits.

Details: [deduplication methodology](../methodology/deduplication.md).

## Consequences

- Exact dedup is collision-safe and costs tens of bytes per document.
- MinHash scales linearly in the number of documents and shingles; LSH keeps comparisons near
  linear; verification removes false positives so the threshold means what it says.
- The equal-weight tuning objective (selected 8 bands x 16 rows at 0.85 / 128, about 0.46
  candidate probability at the threshold) was replaced because it silently missed true
  duplicates; the recall-constrained rule (13 x 8 for the same setting) trades a few more
  verified candidates for recall.
- Transitive clustering can join documents whose direct similarity is below the threshold;
  reported similarity is to the representative.
- Word shingles do not catch paraphrases or translations.

## Alternatives considered

- **SimHash:** compact, but tuned for Hamming-distance near-identity on web pages and less
  interpretable as a Jaccard threshold.
- **Suffix arrays / exact substring dedup:** finds repeated spans precisely and is valuable
  for removing boilerplate spans; much heavier to build and keep on a laptop. Paragraph dedup
  covers the common boilerplate case.
- **Embedding similarity:** catches paraphrases but needs a model, a vector index, and usually
  a GPU; it also over-merges topical neighbours. Out of scope for a CPU-only v0.1.
- **datasketch:** a solid reference implementation that hashes every shingle string. The
  in-house version hashes each word once, combines shingles and applies all permutations with
  vectorized NumPy arithmetic across a whole batch, and keeps signatures in a memory-mappable
  layout. Its estimates are tested against exact Jaccard similarity.
