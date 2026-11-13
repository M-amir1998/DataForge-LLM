# Data report: `demo-pretraining-e5a12d56cebd`

- **Created:** 2026-11-13T21:50:00+00:00 (run `20261113T215000Z-1c849f`)
- **Pipeline config:** `configs/pipelines/demo.yaml` (hash `bee9ce37226a254d`)
- **Code:** DataForge 0.1.0, git `6e9c3f164625`, code fingerprint `2f7d2c1c586ac088`
- **Dataset fingerprint:** `e5a12d56cebd1c5ef038e3f0`
- **Output:** 469 documents, 475.68 KB text, 121,919 estimated tokens (chars/4), 4 shards
- **Input:** 1,501 documents, 1.50 MB; kept 31.2%
- **Processing time:** 5.6 s (0.27 MB/s of input), peak RSS 392 MB

## Sources

| Source | Type | License | Documents read | Record errors | Size |
| :--- | :--- | :--- | ---: | ---: | ---: |
| web | local | per record (`license`) | 651 | 3 | 816.15 KB |
| encyclopedia | local | per record (`license`) | 220 | 0 | 35.42 KB |
| books | local | LicenseRef-PublicDomain | 24 | 0 | 129.76 KB |
| forum | local | CC-BY-SA-4.0 | 250 | 0 | 181.03 KB |
| math | local | CC-BY-4.0 | 100 | 0 | 99.82 KB |
| code | code | MIT | 110 | 0 | 75.38 KB |
| code_gpl | code | GPL-3.0-only | 16 | 0 | 10.91 KB |
| docs | local | CC-BY-4.0 | 30 | 0 | 42.34 KB |
| misc | local | per record (`license`) | 100 | 0 | 105.37 KB |

Ingestion errors (structured, content-free): 3 - see `reports/ingest_errors.jsonl`.

## Before / after

| Stage | Documents | MB | Removed % | Top removal reason |
| :--- | ---: | ---: | ---: | :--- |
| ingest (input) | 1,501 | 1.50 | - | - |
| normalize | 1,501 | 1.50 | 0.0% | - |
| language | 1,435 | 1.45 | 4.4% | language_not_allowed |
| exact_dedup | 1,359 | 1.37 | 5.3% | exact_duplicate |
| near_dedup | 1,326 | 1.34 | 2.4% | near_duplicate |
| pii | 1,326 | 1.34 | 0.0% | - |
| secrets | 1,326 | 1.34 | 0.0% | - |
| license | 1,213 | 1.22 | 8.5% | license_not_allowed |
| quality | 1,191 | 1.21 | 1.8% | quality:length |
| contamination | 1,172 | 1.21 | 1.6% | contaminated |
| classify | 1,172 | 1.21 | 0.0% | - |
| mixture | 469 | 0.46 | 60.0% | mixture_not_sampled |
| split | 469 | 0.46 | 0.0% | - |
| analyze | 469 | 0.46 | 0.0% | - |

![Documents remaining after each stage](plots/stage_funnel.svg)

### Removal reasons

| Stage | Reason | Documents | Size |
| :--- | :--- | ---: | ---: |
| language | language_not_allowed | 66 | 48.54 KB |
| exact_dedup | exact_duplicate | 76 | 79.65 KB |
| near_dedup | near_duplicate | 33 | 35.49 KB |
| license | license_not_allowed | 58 | 57.53 KB |
| license | unknown_license | 55 | 59.47 KB |
| quality | quality:length | 13 | 5.74 KB |
| quality | quality:boilerplate | 9 | 2.68 KB |
| contamination | contaminated | 19 | 9.05 KB |
| mixture | mixture_not_sampled | 703 | 758.51 KB |

Every removal is auditable by document id in `reports/removals.parquet` (1,032 rows).

### Removals by source

| Source | Input | Removed | Removed bytes | Kept | Kept % | Top removing stage |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| books | 24 | 18 | 92.69 KB | 6 | 25.0% | mixture |
| code | 110 | 2 | 2.06 KB | 108 | 98.2% | contamination |
| code_gpl | 16 | 16 | 10.89 KB | 0 | 0.0% | license |
| docs | 30 | 12 | 13.44 KB | 18 | 60.0% | mixture |
| encyclopedia | 220 | 129 | 139.20 KB | 91 | 41.4% | mixture |
| forum | 250 | 214 | 144.94 KB | 36 | 14.4% | mixture |
| math | 100 | 69 | 65.77 KB | 31 | 31.0% | mixture |
| misc | 100 | 100 | 98.91 KB | 0 | 0.0% | exact_dedup |
| web | 651 | 472 | 488.76 KB | 179 | 27.5% | mixture |

## Deduplication

**Exact deduplication** (normalized-content xxh3-128, policy `keep_first`)

| Documents before | Documents after | Duplicates | Duplicate bytes | Groups | Largest group |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1,435 | 1,359 | 76 | 79.65 KB | 70 | 3 |

**Near-duplicate deduplication** (MinHash 128 permutations, word 5-gram shingles, Jaccard >= 0.85, LSH 13 bands x 8 rows, policy `keep_longest`)

| Documents before | Documents after | Duplicates | Duplicate bytes | Clusters | Largest cluster | Candidate pairs | Verified pairs |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,359 | 1,326 | 33 | 35.49 KB | 33 | 2 | 41 | 33 |

## Quality

Combination policy: `all_required`. A document fails a signal independently of the others; removals are attributed to the first failing signal in the documented order. Heuristic scores are not human quality judgements.

| Signal | Evaluated | Failed | Failure rate |
| :--- | ---: | ---: | ---: |
| length | 1,213 | 13 | 1.1% |
| alnum_ratio | 120 | 10 | 8.3% |
| boilerplate | 1,093 | 10 | 0.9% |
| char_entropy | 1,211 | 10 | 0.8% |
| terminal_punctuation | 1,093 | 9 | 0.8% |
| stopwords | 1,093 | 2 | 0.2% |
| alpha_words | 1,093 | 0 | 0.0% |
| code_line_length | 120 | 0 | 0.0% |
| language_confidence | 1,093 | 0 | 0.0% |
| line_repetition | 1,093 | 0 | 0.0% |
| mean_word_length | 1,093 | 0 | 0.0% |
| ngram_repetition | 1,093 | 0 | 0.0% |
| sentence_length | 1,093 | 0 | 0.0% |
| symbol_ratio | 1,093 | 0 | 0.0% |
| url_ratio | 1,093 | 0 | 0.0% |

![Quality score distribution](plots/quality_distribution.svg)

## Privacy and secrets

**PII** (policy `redact`): 90 documents with detections, 140 spans redacted, 0 documents dropped.

| Type | Detections | Documents | Action |
| :--- | ---: | ---: | :--- |
| credit_card | 7 | 7 | redacted |
| email | 57 | 57 | redacted |
| iban | 6 | 6 | redacted |
| ip_address | 7 | 7 | redacted |
| phone | 57 | 57 | redacted |
| us_ssn | 6 | 6 | redacted |

**Secrets** (policy `redact`): 14 documents with detections, 14 spans redacted, 0 documents dropped.

| Type | Detections | Documents | Action |
| :--- | ---: | ---: | :--- |
| aws_access_key_id | 3 | 3 | redacted |
| github_token | 3 | 3 | redacted |
| high_entropy_assignment | 3 | 3 | redacted |
| password_assignment | 3 | 3 | redacted |
| private_key | 2 | 2 | redacted |

> Regex and checksum detectors are a first line of defence, not a compliance guarantee: they miss PII without a fixed shape (names, addresses) and can flag look-alikes. Reports never contain the detected values.

## Licensing

Unknown-license policy: `reject`. Licenses are taken from source metadata and are never inferred; this table describes configuration, not legal advice.

| License | Documents | Bytes | Included? |
| :--- | ---: | ---: | :---: |
| CC-BY-4.0 | 609 | 643.80 KB | yes |
| CC-BY-SA-4.0 | 470 | 403.97 KB | yes |
| MIT | 110 | 74.15 KB | yes |
| UNKNOWN | 55 | 59.47 KB | no |
| proprietary | 42 | 46.64 KB | no |
| LicenseRef-PublicDomain | 24 | 129.74 KB | yes |
| GPL-3.0-only | 16 | 10.89 KB | no |

## Benchmark contamination

Methods: exact, substring, ngram, minhash; word 8-gram containment >= 0.5, MinHash Jaccard >= 0.8; policy `drop`. *Exact* counts exact and normalized substring matches; *Near* counts n-gram and MinHash matches. Evidence (`reports/contamination.jsonl`) is character offsets plus a short hash of the normalized benchmark field, never benchmark text.

| Benchmark | Exact | Near | Documents affected | Items matched | Available |
| :--- | ---: | ---: | ---: | ---: | :--- |
| demo_math | 13 | 5 | 17 | 18 | yes |
| demo_code | 6 | 2 | 2 | 4 | yes |
| gsm8k | 0 | 0 | 0 | 0 | not available locally |
| humaneval | 0 | 0 | 0 | 0 | not available locally |
| mbpp | 0 | 0 | 0 | 0 | not available locally |

## Domains

| Domain | Documents | Text bytes | Est. tokens | Share of documents |
| :--- | ---: | ---: | ---: | ---: |
| code | 108 | 72.09 KB | 18,496 | 23.0% |
| general | 96 | 95.51 KB | 24,482 | 20.5% |
| technology | 62 | 71.68 KB | 18,372 | 13.2% |
| science | 61 | 71.18 KB | 18,244 | 13.0% |
| mathematics | 47 | 47.33 KB | 12,134 | 10.0% |
| news | 45 | 47.44 KB | 12,144 | 9.6% |
| forums | 36 | 23.98 KB | 6,152 | 7.7% |
| legal | 8 | 9.42 KB | 2,416 | 1.7% |
| books | 6 | 37.04 KB | 9,479 | 1.3% |

![Domain distribution](plots/domain_distribution.svg)

## Mixture

Unit: `tokens`; unlisted domains: `drop` (0 documents). Selection takes documents in seeded-hash order within each domain, so it is deterministic and stable when unrelated documents are added.

| Domain | Target | Achieved | Available (tokens) | Selected (tokens) | Selected docs | Sampling rate |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| general | 20.0% | 20.1% | 49,902 | 24,482 | 96 | 49.1% |
| science | 15.0% | 15.0% | 55,653 | 18,244 | 61 | 32.8% |
| technology | 15.0% | 15.1% | 40,003 | 18,372 | 62 | 45.9% |
| code | 15.0% | 15.2% | 18,496 | 18,496 | 108 | 100.0% |
| mathematics | 10.0% | 10.0% | 33,565 | 12,134 | 47 | 36.2% |
| news | 10.0% | 10.0% | 32,546 | 12,144 | 45 | 37.3% |
| books | 8.0% | 7.8% | 33,203 | 9,479 | 6 | 28.5% |
| forums | 5.0% | 5.0% | 42,308 | 6,152 | 36 | 14.5% |
| legal | 2.0% | 2.0% | 10,629 | 2,416 | 8 | 22.7% |

![Mixture weights](plots/mixture_weights.svg)

## Sources in the output

| Source | Documents | Text bytes | Est. tokens | Share of documents |
| :--- | ---: | ---: | ---: | ---: |
| web | 179 | 196.10 KB | 50,246 | 38.2% |
| code | 108 | 72.09 KB | 18,496 | 23.0% |
| encyclopedia | 91 | 95.85 KB | 24,569 | 19.4% |
| forum | 36 | 23.98 KB | 6,152 | 7.7% |
| math | 31 | 30.46 KB | 7,809 | 6.6% |
| docs | 18 | 20.16 KB | 5,168 | 3.8% |
| books | 6 | 37.04 KB | 9,479 | 1.3% |

![Source mixture](plots/source_mixture.svg)

## Languages in the output

| Language | Documents | Text bytes | Est. tokens | Share of documents |
| :--- | ---: | ---: | ---: | ---: |
| en | 361 | 403.59 KB | 103,423 | 77.0% |
| n/a (code) | 108 | 72.09 KB | 18,496 | 23.0% |

## Token and length analysis

| Documents | Mean chars | Median chars | P90 chars | Max chars | Est. tokens |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 469 | 1,038 | 1,048 | 1,228 | 8,092 | 121,919 |

![Document length distribution](plots/length_distribution.svg)

## Splits

| Split | Documents | Est. tokens | Shards | Size |
| :--- | ---: | ---: | ---: | ---: |
| test | 24 | 6,172 | 1 | 29.26 KB |
| train | 420 | 107,823 | 2 | 155.91 KB |
| validation | 25 | 7,924 | 1 | 30.93 KB |

Splits are assigned per duplicate group (near-duplicate cluster, exact group, or content hash), so no group spans two splits; `reports/validation.json` records the leakage check.

## Output shards

| Shard | Split | Documents | Size | SHA-256 |
| :--- | :--- | ---: | ---: | :--- |
| data/test-00000-of-00001.parquet | test | 24 | 29.26 KB | 398bea8e282e |
| data/train-00000-of-00002.parquet | train | 400 | 132.80 KB | 19956f603753 |
| data/train-00001-of-00002.parquet | train | 20 | 23.11 KB | afad9c76007a |
| data/validation-00000-of-00001.parquet | validation | 25 | 30.93 KB | e75c6949cf0d |

## Processing time by stage

| Stage | Kind | Seconds | Documents processed |
| :--- | :--- | ---: | ---: |
| ingest | source | 0.83 | 1,501 |
| normalize | streaming | 0.15 | 1,501 |
| language | streaming | 0.14 | 1,501 |
| exact_dedup | barrier | 0.00 | 1,435 |
| near_dedup | barrier | 0.49 | 1,359 |
| pii | streaming | 0.41 | 1,326 |
| secrets | streaming | 0.53 | 1,326 |
| license | streaming | 0.00 | 1,326 |
| quality | streaming | 0.47 | 1,213 |
| contamination | streaming | 1.51 | 1,191 |
| classify | streaming | 0.05 | 1,172 |
| mixture | barrier | 0.01 | 1,172 |
| split | streaming | 0.00 | 469 |
| analyze | streaming | 0.00 | 469 |

## Reproducibility

- Re-run: `dataforge pipeline run configs/pipelines/demo.yaml`
- Verify against this manifest: `dataforge pipeline reproduce .dataforge/datasets/demo-pretraining/e5a12d56cebd/manifest.json`
