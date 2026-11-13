# ADR-005: Jenkins instead of GitHub Actions

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

The project owner requires that the repository consume no GitHub Actions minutes. CI must
still run formatting, linting, strict type checking, every test suite, an end-to-end pipeline
smoke test, security scans, and package validation, on hardware the owner controls (a 16 GB
laptop or a self-hosted agent), without GPUs.

## Decision

- The declarative `Jenkinsfile` is the single source of truth for CI.
- The repository contains no `.github/workflows/` directory; a test asserts its absence.
- Stages run sequentially, `disableConcurrentBuilds()` is set, and tests use generated
  fixtures only, so a build fits a laptop.
- Caches live outside the workspace; only lightweight artifacts (test reports, coverage,
  smoke-run reports, scan results, wheels) are archived.
- Docker is optional: the `Dockerfile` provides a `ci` agent image, and the `Docker Build`
  stage runs only when requested.

Setup: [Jenkins CI](../ci/jenkins.md).

## Consequences

- No hosted CI minutes are used, and resource limits are explicit and local.
- The owner operates Jenkins (updates, plugins, agents, backups).
- There is no hosted CI status on pull requests unless a webhook and status reporting are
  configured; contributors run the same checks locally (`ruff`, `mypy`, `pytest`, pre-commit).
- Pipeline syntax is validated with the Jenkins declarative linter rather than a hosted
  service.

## Alternatives considered

- **GitHub Actions:** ruled out by the owner's constraint.
- **Self-hosted GitHub Actions runner:** avoids hosted minutes but still couples CI to GitHub's
  control plane; Jenkins was preferred for portability across Git hosts.
- **GitLab CI, Buildkite, Drone:** viable, but add another service or account; Jenkins is
  widely deployed and runs entirely on the owner's machine.
