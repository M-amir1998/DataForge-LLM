# Jenkins CI

The `Jenkinsfile` at the repository root is the single source of truth for CI. The pipeline
needs no GPU, no external services, and no large data: every test uses generated fixtures.
GitHub Actions is intentionally not used ([ADR-005](../adr/ADR-005-jenkins-not-github-actions.md)).

## Pipeline at a glance

| Stage | What it runs |
| :--- | :--- |
| Checkout | `checkout scm`, prints the commit |
| Environment | Bootstraps `uv` (pinned `UV_VERSION`) if the agent lacks it; prints tool versions |
| Dependency Lock Validation | `uv lock --check` (fails if `pyproject.toml` and `uv.lock` disagree), `uv sync --locked`, `dataforge doctor` |
| Formatting Check | `ruff format --check` |
| Lint | `ruff check` with JUnit output |
| Static Type Check | `mypy` (strict) with JUnit output |
| Unit Tests | `pytest tests/unit` with coverage collection |
| Integration Tests | `pytest tests/integration`, coverage appended |
| Data Pipeline Smoke Test | Generates the tiny corpus, plans and runs `configs/pipelines/smoke.yaml`, validates the dataset, archives its manifest and report, runs the end-to-end CLI tests |
| Property Tests | `pytest tests/property` (derandomized Hypothesis profile), coverage report and 85% gate |
| Security Scan | DataForge's own credential scanner over the working tree and full Git history; gitleaks when installed; `pip-audit` (pinned) over the locked runtime dependencies |
| Package Build | `uv build` (wheel and sdist) |
| Artifact Validation | `scripts/check_dist.py`: wheel/sdist contents, license metadata, clean-install smoke |
| Docker Build | Only with `RUN_DOCKER_BUILD=true`: builds and runs the `runtime` image |
| Benchmarks | Only with `RUN_BENCHMARKS=true`: quick performance benchmarks |

Options: `timestamps()`, `disableConcurrentBuilds()`, a 45-minute timeout, build history
rotation, and `skipStagesAfterUnstable()`. The `post` section publishes JUnit results, archives
artifacts, and deletes generated data (`data/smoke`, `.dataforge`).

## 1. Install Jenkins

Any current Jenkins LTS works. Two convenient options:

- **Container (local laptop):** `docker compose --profile ci up -d jenkins` starts the pinned
  LTS image from `docker-compose.yml` (`jenkins/jenkins:2.580.2-lts-jdk21`), bound to
  `127.0.0.1:8080` with a 2 GB memory limit and a persistent `jenkins_home` volume. Set
  `JENKINS_PORT` in a local `.env` file if port 8080 is taken. Read the initial admin password
  with `docker compose exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword`.
- **Native package** on a Linux host with Java 21, following the official Jenkins installation
  guide for your distribution.

Use a current LTS release: recent plugin releases refuse to install on older Jenkins cores
(for example, Pipeline plugins that require 2.516.3 or newer). Keep plugins updated; the
plugin manager lists published security advisories for installed versions.

Required plugins (all part of the "suggested plugins" set): **Pipeline**, **Git**, **JUnit**,
and **Timestamper**. Optional: **GitHub** (webhook trigger) and **Docker Pipeline** (container
agents).

## 2. Prepare an agent

Agents need a POSIX shell, `git`, and `curl`; the pipeline installs `uv` into `~/.local/bin`
when missing, and `uv` installs the pinned Python version from `.python-version`.
Recommended on a 16 GB machine: one executor per agent, at least 4 GB of free RAM, and at
least 5 GB of free disk.

The `ci` target of the `Dockerfile` is a ready-made agent image (Python, git, curl, `uv`,
non-root `jenkins` user):

```bash
docker build --target ci -t dataforge-llm-ci .
```

Use it as a Docker agent or cloud template if your controller runs the Docker Pipeline
plugin. Windows agents are not supported as-is because the stages use `sh`; use a Linux
container or WSL.

## 3. Create the job

1. *New Item* -> *Pipeline*.
2. *Pipeline* -> *Definition*: **Pipeline script from SCM**.
3. *SCM*: Git; *Repository URL*: the repository's HTTPS clone URL; *Branch*: `*/main`.
4. *Script Path*: `Jenkinsfile`.
5. Save, then *Build Now*. Declarative parameters appear after the first run; later runs
   use *Build with Parameters*.

## 4. Triggers

- **Manual** (default): *Build with Parameters*.
  - `RUN_DOCKER_BUILD` (default off): build the runtime image; requires Docker on the agent.
  - `RUN_BENCHMARKS` (default off): run the quick benchmarks; meant for manual or nightly runs.
  - `UV_VERSION` (default `0.11.21`): `uv` version to bootstrap.
- **Polling** (optional): enable *Poll SCM* with a schedule such as `H/15 * * * *`.
- **GitHub webhook** (optional, not required): install the GitHub plugin, enable *GitHub hook
  trigger for GITScm polling* in the job, and in the GitHub repository add a webhook
  (*Settings* -> *Webhooks*) with payload URL `https://<your-jenkins-host>/github-webhook/`,
  content type `application/json`, and the *push* event. Jenkins must be reachable from
  GitHub; a laptop behind NAT needs a relay or should use polling instead.

## 5. Credentials

The pipeline itself uses no secrets, and none are stored in the repository or the
`Jenkinsfile`. A public repository needs no checkout credentials. For a private fork, create a
credential in the Jenkins credentials store (a GitHub App or a fine-grained, read-only token)
and select it in the job's SCM configuration by id. Future steps that need secrets (for
example publishing to object storage) must use `withCredentials` so values stay masked in
logs.

## 6. Workspace, caches, and cleanup

- The project virtual environment lives in the workspace (`uv sync`); the `uv` cache and the
  managed Python live under `~/.cache/dataforge-ci/` outside the workspace, so repeated
  builds are fast and nothing cache-related is ever committed.
- Generated data stays small: the smoke corpus is generated at scale 0.1, and `post.cleanup`
  removes `data/smoke` and `.dataforge`.
- Large pipeline jobs over real corpora are separate, manually triggered jobs with their own
  storage; they do not belong in this CI pipeline or in the workspace.

## 7. Resource safety

Stages run strictly one after another, so only one heavyweight step (a test suite or the
smoke pipeline) uses memory at a time; `disableConcurrentBuilds()` prevents overlapping builds
of the job. Tests run in a single process, the smoke configuration caps DuckDB at 256 MB and
one thread, and the derandomized Hypothesis profile (`HYPOTHESIS_PROFILE=ci`) keeps runs
deterministic.

## 8. Artifacts

Archived per build (lightweight only, never corpora or caches):

- `reports/junit-*.xml`: ruff, mypy, unit, integration, end-to-end, and property results
  (also published to the JUnit view);
- `reports/coverage.xml`;
- `reports/smoke-validation.json`, `smoke-manifest.json`, `smoke-report.md`, `smoke-summary.json`;
- `reports/secret-scan.json` (and `gitleaks.json` when gitleaks ran), `requirements.txt`;
- `reports/benchmarks.json` when benchmarks ran;
- `dist/*.whl`, `dist/*.tar.gz`.

## 9. Checking the Jenkinsfile

Validate syntax against your controller's declarative linter before pushing changes, for
example with the Jenkins CLI:

```bash
java -jar jenkins-cli.jar -s <JENKINS_URL> -auth <user>:<api-token> declarative-linter < Jenkinsfile
```

The repository has a test asserting that no `.github/workflows` directory exists.
