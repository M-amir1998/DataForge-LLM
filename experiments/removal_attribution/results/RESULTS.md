# Removal attribution - measured results

Corpus: synthetic sample (seed 7, scale 2.0) - 3,001 ingested documents, 2.991 MB. Produced by `experiments/removal_attribution/run.py`; every number is measured.

## Sequential removal (pipeline order, filter mode)

| Stage | Removed | % of input | MB removed |
| :--- | ---: | ---: | ---: |
| normalize | 0 | 0.0% | 0.0 |
| language | 129 | 4.3% | 0.096 |
| exact_dedup | 153 | 5.1% | 0.153 |
| near_dedup | 82 | 2.7% | 0.082 |
| pii | 0 | 0.0% | 0.0 |
| secrets | 0 | 0.0% | 0.0 |
| license | 222 | 7.4% | 0.219 |
| quality | 35 | 1.2% | 0.014 |
| contamination | 41 | 1.4% | 0.019 |
| classify | 0 | 0.0% | 0.0 |
| split | 0 | 0.0% | 0.0 |
| analyze | 0 | 0.0% | 0.0 |

Output: 2,339 documents.

## Independent flag rates (each filter sees the full corpus, annotate mode)

| Filter | Flagged | % of input |
| :--- | ---: | ---: |
| language | 129 | 4.3% |
| exact_dedup | 177 | 5.9% |
| near_dedup | 276 | 9.2% |
| license | 222 | 7.4% |
| quality | 155 | 5.2% |
| contamination | 42 | 1.4% |

## Overlap between filters (documents flagged by both, independent run)

| | language | exact_dedup | near_dedup | license | quality | contamination |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| language | 129 | 24 | 41 | 0 | 103 | 0 |
| exact_dedup | 24 | 177 | 177 | 0 | 34 | 0 |
| near_dedup | 41 | 177 | 276 | 0 | 58 | 0 |
| license | 0 | 0 | 0 | 222 | 0 | 0 |
| quality | 103 | 34 | 58 | 0 | 155 | 1 |
| contamination | 0 | 0 | 0 | 0 | 1 | 42 |

## Privacy handling (sequential run, `policy: redact`)

| Stage | Documents in | With detections | Spans redacted | Bytes in | Bytes out | Removed |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| pii | 2,637 | 180 | 280 | 2,789,545 | 2,786,365 | 0 |
| secrets | 2,637 | 28 | 28 | 2,786,365 | 2,783,632 | 0 |

Detections by type (one document can contain several):

- pii: credit_card 13, email 114, iban 13, ip_address 13, phone 114, us_ssn 13
- secrets: aws_access_key_id 6, github_token 6, high_entropy_assignment 5, password_assignment 5, private_key 5, slack_token 1

Redaction keeps the document and replaces each span with a typed placeholder such as `<EMAIL>`;
under `policy: drop` the documents with detections would be removed instead.

## Detection quality against planted ground truth (sequential run)

| Phenomenon | Planted | Acted on | Recall | Precision |
| :--- | ---: | ---: | ---: | ---: |
| non-English page -> language filter | 60 | 129 | 100.0% | 100.0% |
| bad or unknown license -> license gate | 222 | 222 | 100.0% | 100.0% |
| planted exact-duplicate group | 130 | 130 | 100.0% | - |
| planted near-duplicate group (J >= 0.85) | 80 | 78 | 97.5% | - |
| exact/near dedup removals in a planted group or templated junk page | 535 | 235 | - | 100.0% |
| low-quality page removed by any stage | 120 | 120 | 100.0% | - |
| quality-stage removals that are planted low-quality | 120 | 35 | - | 97.1% |
| PII document -> PII detector | 180 | 180 | 100.0% | 100.0% |
| secret in code -> secret detector | 28 | 28 | 100.0% | 100.0% |
| benchmark contamination -> contamination stage | 44 | 41 | 93.2% | 100.0% |

- *Planted* counts documents (or duplicate groups) carrying the label; *Acted on* counts what the
  filter removed or detected. Recall is over planted items, precision over acted-on items.
- The language filter also removes planted junk pages without English prose (keyword spam,
  lorem ipsum, URL lists); its precision counts those as correct, so *Acted on* exceeds *Planted*.
- Duplicate groups count as deduplicated when at most one member survives. Dedup precision also
  counts removals of junk pages as correct: the generator builds them from a few templates, so they
  can legitimately be near-duplicates of each other.
- Quality-stage recall is not reported because earlier stages remove most junk pages first; the
  *removed by any stage* row gives the end-to-end recall.
- PII and secret recall only counts planted documents that reached those stages.

Planted near-duplicate groups below the 0.85 threshold (correctly kept by definition): 0.

## Contamination recall by leak type

| Leak type | Planted | Removed by contamination stage | Removed earlier by |
| :--- | ---: | ---: | :--- |
| exact | 8 | 7 | {'quality': 1} |
| substring | 24 | 24 | - |
| paraphrase | 12 | 10 | - |

Definitions are in the docstring of `run.py`. Ground truth comes from the generator, so these figures
measure the filters on synthetic phenomena; they are not estimates of performance on real web data.
