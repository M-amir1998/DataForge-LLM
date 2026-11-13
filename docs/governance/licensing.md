# Licensing

DataForge records where each document's license claim comes from and filters documents by a
configured license policy. This describes configuration, not legal conclusions; it is not
legal advice.

## License metadata

Every source in a registry can declare:

| Field | Meaning |
| :--- | :--- |
| `license` | SPDX identifier for the whole source (default `UNKNOWN`) |
| `license_source` | Evidence for the claim: "dataset card", "repository LICENSE file", "manual review", ... |
| `usage_policy` | Free-form usage label, e.g. `research-and-commercial`, `research-only` (default `unspecified`) |
| `license_field` | For structured records: a field holding a per-record license, which overrides the source license |

Each document stores `license`, `license_source`, and `usage_policy`. When the license comes
from a record field, `license_source` records that (`record field 'license'`); otherwise it is
the source's `license_source`, or `source registry` if none was given.

## Normalization never infers

`normalize_license` maps only well-known spellings to SPDX identifiers (for example
`apache 2.0` to `Apache-2.0`, `cc by-sa 4.0` to `CC-BY-SA-4.0`, `public domain` to
`LicenseRef-PublicDomain`). Empty values and markers such as `none`, `n/a`, `unknown`, or
`NOASSERTION` become `UNKNOWN`. Any other string is kept verbatim. DataForge never guesses a
license from file contents or from a LICENSE file; the code source type deliberately requires
the registry to declare the license.

## Policy (`stages.license`)

| Key | Default | Meaning |
| :--- | :--- | :--- |
| `allowed` | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0, Unlicense, LicenseRef-PublicDomain | Licenses accepted |
| `denied` | none | Licenses always rejected, even if allowed |
| `unknown_policy` | `reject` | `reject`, `allow`, or `fail` for `UNKNOWN` |
| `disallowed_policy` | `reject` | `reject` or `fail` for licenses not allowed or denied |
| `allowed_usage_policies` | not set | When set, documents whose `usage_policy` is not listed are rejected |

Rejection reasons: `unknown_license`, `license_not_allowed`, `denied_license`,
`usage_policy_not_allowed`. The `fail` policies stop the run with `UnsafeLicense` (exit code
7), which suits CI gates.

The defaults are a starting point, not a recommendation. Share-alike (CC-BY-SA) and
attribution licenses carry obligations that downstream users must meet; copyleft code
licenses are excluded by default.

## Reporting

The report's license table lists, for every license seen at the license stage, the number of
documents, bytes, and whether the policy includes it. The manifest lists each source's
declared license, license field, evidence, and usage policy, and the dataset card repeats
them with the allowlist.

## Limitations

- Correctness depends entirely on the declared metadata. A wrong `license` in a registry
  propagates to every document of that source.
- Per-record license fields are trusted as given; DataForge does not verify them.
- Licenses can attach obligations (attribution, share-alike) that a dataset's consumers must
  honour; DataForge records the information but does not enforce downstream obligations.
