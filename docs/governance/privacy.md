# Privacy and credentials

Two stages protect personal data and credentials: `pii` and `secrets`. Both run on every
document, record what they found as counts per type, and apply a configurable policy.

**Limitation first:** pattern-based detection is a first line of defence, not a compliance
guarantee. It finds data with a recognizable shape and misses personal data without one
(names, postal addresses, health or financial details written in prose, identifiers in
unusual formats). DataForge makes no claim of compliance with GDPR, CCPA, HIPAA, or any other
regime.

## PII detectors (`stages.pii.detectors`)

| Detector | Pattern | Validation that limits false positives |
| :--- | :--- | :--- |
| `email` | local part, `@`, dotted domain with a 2 to 24 letter top-level domain | boundary checks |
| `phone` | North American numbers with separators; international numbers starting with `+` and 2 to 5 groups | 7 to 15 digits in total |
| `ip_address` | dotted IPv4 quads; IPv6 in full or compressed form | parsed with Python's `ipaddress`; unspecified, loopback, and broadcast addresses skipped; IPv4 quads preceded by "v", "version", or "release" are treated as software versions |
| `credit_card` | 13 to 19 digits with optional spaces or dashes | known issuer prefix and Luhn checksum |
| `us_ssn` | `AAA-GG-SSSS` | Social Security Administration rules: area not 000, 666, or 900-999; group not 00; serial not 0000 |
| `iban` | country code, check digits, groups of four | length 15 to 34 and ISO 13616 mod-97 checksum |

Private, link-local, and documentation IP ranges are reported like any other address; the
detector does not judge whether an address identifies a person.

## Credential detectors (`stages.secrets.detectors`)

Three complementary techniques:

- **Known token formats** with fixed prefixes: AWS access key ids, GitHub tokens (classic and
  fine-grained), GitLab personal access tokens, Slack tokens, Stripe live keys, Google API
  keys, Hugging Face tokens, `sk-` style API keys, JSON Web Tokens, and PEM blocks whose
  header names a private key.
- **Credential assignments**: an identifier containing `password`, `passwd`, `pwd`, or
  `secret` assigned a quoted value (`password_assignment`); an identifier containing `key`,
  `token`, `secret`, `credential`, `auth`, or `bearer` assigned a long quoted value
  (`high_entropy_assignment`); a 40-character value after an `aws_secret_access_key`
  keyword.
- **Entropy gating**: assignment values must reach a Shannon entropy threshold
  (`min_entropy`, 3.5 bits per character; 3.0 for password assignments) and are ignored when
  they look like placeholders (`${...}`, `{{...}}`, `<...>`, `changeme`, `example`, `your_...`,
  environment lookups, or values with three or fewer distinct characters).

## Policies

| `policy` | PII | Credentials |
| :--- | :--- | :--- |
| `detect` | counts recorded, text unchanged | same |
| `redact` (default) | each match replaced by a typed placeholder: `<EMAIL>`, `<PHONE>`, `<IP_ADDRESS>`, `<CREDIT_CARD>`, `<US_SSN>`, `<IBAN>` | each match replaced by `<SECRET>` |
| `drop` | the document is removed when it has at least `drop_threshold` (1) detections | the document is removed on any detection |

Overlapping matches are resolved before redaction (earliest start wins; at the same start
the longest match wins), and redacted text no longer matches the detectors, so re-running
the stage is a no-op. Redaction is recorded in the document's transformation history (for
example `pii:redact[2]`) and the per-type counts in `pii_counts` / `secret_counts`.

## Logging and reporting rules

- Logs, artifacts, and reports contain **counts, types, and document ids only**. Detection
  spans carry offsets and a type, never the matched value.
- `reports/privacy.json` and the report's privacy section give detections and affected
  documents per type, redactions, and drops.
- `dataforge privacy scan DATASET` counts detections in an existing dataset by type; it never
  prints values.
- `dataforge sources inspect SOURCE` previews raw records with PII and credentials redacted.
- Planted test data uses reserved values (example.com addresses, fictional 555-01xx phone
  numbers, documentation IP ranges, payment-processor test card numbers, voided SSNs,
  documentation IBANs); fake credentials are assembled from fragments at runtime so that no
  credential-shaped literal exists in the repository.

## Recommendations

- Treat redaction as risk reduction, and combine it with source selection, license and
  usage-policy review, and manual audits of samples.
- For higher recall on unstructured PII, add a model-based detector (for example a named
  entity recognizer) as an additional stage; the stage interface supports it.
- Review the per-type counts after each run; a sudden change usually indicates a source
  problem.
