// DataForge-LLM continuous integration (the single source of truth for CI).
//
// Runs on any Linux agent (or the `ci` target of the Dockerfile) with git and curl.
// No GPU, no external services, no large data: tests use generated fixtures only.
// Stages run sequentially on purpose: one heavyweight step at a time fits a 16 GB machine.
// See docs/ci/jenkins.md for setup, credentials handling, and the optional webhook.

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        skipStagesAfterUnstable()
    }

    parameters {
        booleanParam(name: 'RUN_DOCKER_BUILD', defaultValue: false,
                     description: 'Build the runtime container image (requires Docker on the agent).')
        booleanParam(name: 'RUN_BENCHMARKS', defaultValue: false,
                     description: 'Run the quick performance benchmarks (manual or nightly only).')
        string(name: 'UV_VERSION', defaultValue: '0.11.21',
               description: 'uv version to bootstrap when the agent does not provide uv.')
    }

    environment {
        // Persistent caches live outside the workspace and are never committed.
        UV_CACHE_DIR = "${env.HOME}/.cache/dataforge-ci/uv"
        UV_PYTHON_INSTALL_DIR = "${env.HOME}/.cache/dataforge-ci/python"
        UV_LINK_MODE = 'copy'
        PATH = "${env.HOME}/.local/bin:${env.PATH}"
        PYTHONUTF8 = '1'
        PYTHONDONTWRITEBYTECODE = '1'
        HYPOTHESIS_PROFILE = 'ci'
        REPORTS = 'reports'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                sh 'git log -1 --format="commit %H%nauthor %an%ndate   %cI%n%n    %s"'
            }
        }

        stage('Environment') {
            steps {
                sh '''
                    set -eu
                    if ! command -v uv >/dev/null 2>&1; then
                      curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh
                    fi
                    uv --version
                    git --version
                    mkdir -p "${REPORTS}"
                    rm -f "${REPORTS}"/*.xml
                '''
            }
        }

        stage('Dependency Lock Validation') {
            steps {
                // Fails if pyproject.toml and uv.lock disagree; then installs exactly the lock.
                sh 'uv lock --check && uv sync --locked'
                sh 'uv run dataforge doctor'
            }
        }

        stage('Formatting Check') {
            steps {
                sh 'uv run ruff format --check src tests scripts benchmarks experiments'
            }
        }

        stage('Lint') {
            steps {
                sh 'uv run ruff check src tests scripts benchmarks experiments --output-format junit --output-file "${REPORTS}/junit-ruff.xml"'
            }
        }

        stage('Static Type Check') {
            steps {
                sh 'uv run mypy --junit-xml "${REPORTS}/junit-mypy.xml"'
            }
        }

        stage('Unit Tests') {
            steps {
                sh 'uv run pytest tests/unit -q --junitxml="${REPORTS}/junit-unit.xml" --cov --cov-report= --cov-fail-under=0'
            }
        }

        stage('Integration Tests') {
            steps {
                sh 'uv run pytest tests/integration -q --junitxml="${REPORTS}/junit-integration.xml" --cov --cov-append --cov-report= --cov-fail-under=0'
            }
        }

        stage('Data Pipeline Smoke Test') {
            steps {
                sh '''
                    set -eu
                    rm -rf data/smoke .dataforge/smoke
                    uv run dataforge sample generate data/smoke --scale 0.1
                    uv run dataforge pipeline plan configs/pipelines/smoke.yaml
                    uv run dataforge pipeline run configs/pipelines/smoke.yaml --quiet
                    DATASET="$(ls -d .dataforge/smoke/datasets/smoke/*/ | head -n 1)"
                    uv run dataforge dataset validate "${DATASET}" --json > "${REPORTS}/smoke-validation.json"
                    cp "${DATASET}/manifest.json" "${REPORTS}/smoke-manifest.json"
                    cp "${DATASET}/reports/report.md" "${REPORTS}/smoke-report.md"
                    cp "${DATASET}/reports/summary.json" "${REPORTS}/smoke-summary.json"
                    uv run pytest tests/e2e -q --junitxml="${REPORTS}/junit-e2e.xml" --no-cov
                '''
            }
        }

        stage('Property Tests') {
            steps {
                sh 'uv run pytest tests/property -q --junitxml="${REPORTS}/junit-property.xml" --cov --cov-append --cov-report=xml:"${REPORTS}/coverage.xml" --cov-fail-under=85'
            }
        }

        stage('Security Scan') {
            steps {
                sh '''
                    set -eu
                    # DataForge's own credential detectors over the working tree and full history.
                    uv run python scripts/scan_secrets.py --history --report "${REPORTS}/secret-scan.json"
                    # Independent second opinion when gitleaks is available on the agent.
                    if command -v gitleaks >/dev/null 2>&1; then
                      gitleaks git --redact --report-format json --report-path "${REPORTS}/gitleaks.json" .
                    else
                      echo "gitleaks not installed on this agent; in-repo scanner result is authoritative"
                    fi
                    # Known vulnerabilities in the locked runtime dependencies.
                    uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt > "${REPORTS}/requirements.txt"
                    uvx --from pip-audit==2.9.0 pip-audit --strict --progress-spinner off -r "${REPORTS}/requirements.txt"
                '''
            }
        }

        stage('Package Build') {
            steps {
                sh 'rm -rf dist && uv build'
            }
        }

        stage('Artifact Validation') {
            steps {
                sh 'uv run python scripts/check_dist.py dist'
            }
        }

        stage('Docker Build') {
            when { expression { return params.RUN_DOCKER_BUILD } }
            steps {
                sh '''
                    set -eu
                    TAG="$(git rev-parse --short=12 HEAD)"
                    docker build --target runtime -t "dataforge-llm:${TAG}" .
                    docker run --rm "dataforge-llm:${TAG}" --version
                '''
            }
        }

        stage('Benchmarks') {
            when { expression { return params.RUN_BENCHMARKS } }
            steps {
                sh 'uv run python benchmarks/performance/run.py all --quick --output "${REPORTS}/benchmarks.json"'
            }
        }
    }

    post {
        always {
            junit allowEmptyResults: true, testResults: 'reports/junit-*.xml'
            archiveArtifacts allowEmptyArchive: true, fingerprint: true,
                             artifacts: 'reports/**, dist/*.whl, dist/*.tar.gz'
        }
        cleanup {
            // Generated corpora and work areas never persist in the workspace.
            sh 'rm -rf data/smoke .dataforge'
        }
    }
}
