##############################################################################
# CloudProbe — Developer command surface
#
# Every common developer action has a single, memorable target here. Targets
# are one-liners that shell out to tools; business logic never lives in this
# file (see docs/project-structure.md §5).
#
# Compatibility notes:
#   - Designed to work with POSIX-compatible `make` implementations.
#   - Windows contributors: run via WSL, Git Bash, or MSYS2's `make`. See
#     README.md "Windows setup".
##############################################################################

# ---- Configuration -------------------------------------------------------
PYTHON        ?= python3
VENV          ?= .venv
VENV_BIN      := $(VENV)/bin
PIP           := $(VENV_BIN)/pip
PY            := $(VENV_BIN)/python
PYTEST        := $(VENV_BIN)/pytest
COVERAGE      := $(VENV_BIN)/coverage
RUFF          := $(VENV_BIN)/ruff
BLACK         := $(VENV_BIN)/black
MYPY          := $(VENV_BIN)/mypy
PRECOMMIT     := $(VENV_BIN)/pre-commit

PKG           := cloudprobe
SRC_DIR       := src
TESTS_DIR     := tests

# Treat pytest exit code 5 (no tests collected) as success. Phase 1 of the
# ROADMAP lands the environment before any real tests exist; a fresh clone
# must still be able to run `make test` cleanly.
PYTEST_OK_EMPTY := ; code=$$?; if [ $$code -eq 5 ]; then exit 0; else exit $$code; fi

.DEFAULT_GOAL := help

# ---- Meta ----------------------------------------------------------------
.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / \
		{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---- Environment lifecycle ----------------------------------------------
.PHONY: bootstrap
bootstrap: $(VENV)/.stamp ## Create venv and install runtime + dev dependencies

$(VENV)/.stamp: requirements.txt requirements-dev.txt pyproject.toml
	@echo ">> Creating virtual environment in $(VENV)"
	@$(PYTHON) -m venv $(VENV)
	@$(PIP) install --upgrade pip setuptools wheel
	@echo ">> Installing runtime and development dependencies"
	@$(PIP) install -r requirements-dev.txt
	@echo ">> Installing pre-commit hooks"
	@$(PRECOMMIT) install --install-hooks
	@touch $(VENV)/.stamp
	@echo ">> Bootstrap complete. Activate with: source $(VENV_BIN)/activate"

.PHONY: install-editable
install-editable: bootstrap ## Install the cloudprobe package in editable mode
	@$(PIP) install -e .

# ---- Formatting and linting ---------------------------------------------
.PHONY: format
format: bootstrap ## Auto-format source with Black and fix Ruff-fixable issues
	@$(BLACK) $(SRC_DIR) $(TESTS_DIR)
	@$(RUFF) check --fix $(SRC_DIR) $(TESTS_DIR)

.PHONY: lint
lint: bootstrap ## Run Ruff (lint only), Black --check, and yamllint
	@$(RUFF) check $(SRC_DIR) $(TESTS_DIR)
	@$(BLACK) --check $(SRC_DIR) $(TESTS_DIR)
	@$(VENV_BIN)/yamllint -s configs docs .pre-commit-config.yaml || true

.PHONY: typecheck
typecheck: bootstrap ## Run mypy in strict mode
	@$(MYPY)

# ---- Testing -------------------------------------------------------------
.PHONY: test
test: bootstrap ## Run the full test suite (all tiers)
	@$(PYTEST) $(TESTS_DIR) $(PYTEST_OK_EMPTY)

.PHONY: test-unit
test-unit: bootstrap ## Run only unit tests
	@$(PYTEST) -m unit $(TESTS_DIR) $(PYTEST_OK_EMPTY)

.PHONY: test-integration
test-integration: bootstrap ## Run only integration tests (moto, fakes)
	@$(PYTEST) -m integration $(TESTS_DIR) $(PYTEST_OK_EMPTY)

.PHONY: test-regression
test-regression: bootstrap ## Run only regression / golden-file tests
	@$(PYTEST) -m regression $(TESTS_DIR) $(PYTEST_OK_EMPTY)

.PHONY: test-failure
test-failure: bootstrap ## Run only failure-scenario tests
	@$(PYTEST) -m failure_scenarios $(TESTS_DIR) $(PYTEST_OK_EMPTY)

.PHONY: coverage
coverage: bootstrap ## Run tests with coverage and enforce the 90% floor
	@$(COVERAGE) erase
	@$(PYTEST) --cov=$(PKG) --cov-report=term-missing --cov-report=xml --cov-report=html \
		$(TESTS_DIR) $(PYTEST_OK_EMPTY)

# ---- Pre-commit ---------------------------------------------------------
.PHONY: precommit
precommit: bootstrap ## Run all pre-commit hooks against every file
	@$(PRECOMMIT) run --all-files

# ---- Housekeeping -------------------------------------------------------
.PHONY: clean
clean: ## Remove caches, coverage output, and build artifacts (keeps .venv)
	@rm -rf .pytest_cache .mypy_cache .ruff_cache
	@rm -rf build dist *.egg-info
	@rm -rf coverage_html coverage.xml .coverage .coverage.*
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@find . -type f -name '*.pyc' -delete

.PHONY: distclean
distclean: clean ## Also remove the virtual environment
	@rm -rf $(VENV)
