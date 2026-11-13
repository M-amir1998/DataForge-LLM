# DataForge-LLM documentation

Start with the [project README](../README.md) for the quickstart and measured results.

## Architecture

| Document | What it covers |
| :--- | :--- |
| [Overview](architecture/overview.md) | Layers, package map, end-to-end data flow |
| [Pipeline](architecture/pipeline.md) | Stage DAG, streaming vs barrier stages, segments, checkpoints, resume, caching, immutable versions |
| [Storage](architecture/storage.md) | Canonical Arrow schema and its versioning, parts, shards, storage backends, disk budget |
| [Scale-out](architecture/scale-out.md) | How the design maps to Ray / Spark / batch workers (not implemented in v0.1) |

## Methodology

| Document | What it covers |
| :--- | :--- |
| [Deduplication](methodology/deduplication.md) | Exact, paragraph-level, and MinHash/LSH near-duplicate detection |
| [Quality](methodology/quality.md) | The fifteen heuristic signals, combination policies, learned-model hook |
| [Contamination](methodology/contamination.md) | Benchmark registry and the four detection methods |
| [Mixtures](methodology/mixtures.md) | Domain mixtures by documents, bytes, or tokens |

## Governance

| Document | What it covers |
| :--- | :--- |
| [Privacy](governance/privacy.md) | PII and credential detection, redaction, logging rules, limitations |
| [Licensing](governance/licensing.md) | License metadata, normalization, and policy (not legal advice) |
| [Provenance](governance/provenance.md) | Document identity, per-document provenance, manifests, lineage, reproduction |

## Operations

| Document | What it covers |
| :--- | :--- |
| [Jenkins CI](ci/jenkins.md) | Installing Jenkins, agents, the pipeline job, webhook, credentials, resources |
| [Architecture decision records](adr/README.md) | Why the main technical choices were made |

Repository-level documents: [CONTRIBUTING](../CONTRIBUTING.md), [SECURITY](../SECURITY.md),
[CHANGELOG](../CHANGELOG.md), [benchmarks](../benchmarks/README.md).
