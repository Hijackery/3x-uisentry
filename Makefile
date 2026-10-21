# Удобные команды для разработки. Все проверки те же, что в CI.
PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
RUFF ?= .venv/bin/ruff
MYPY ?= $(PYTHON) -m mypy
PYTEST ?= $(PYTHON) -m pytest

.PHONY: help venv install-dev format lint format-check typecheck test shellcheck sudoers check clean

help:
	@printf '%s\n' \
	  'make venv         — создать .venv' \
	  'make install-dev  — установить зависимости (runtime + dev)' \
	  'make format       — отформатировать код' \
	  'make lint         — ruff check' \
	  'make format-check — проверить форматирование' \
	  'make typecheck    — mypy strict' \
	  'make test         — pytest' \
	  'make shellcheck   — bash -n + shellcheck скриптов' \
	  'make sudoers      — проверить install/stacksentry-sudoers' \
	  'make check        — всё вышеперечисленное'

venv:
	python3 -m venv .venv

install-dev: venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt -r requirements-dev.txt

format:
	$(RUFF) format .

lint:
	$(RUFF) check .

format-check:
	$(RUFF) format --check .

typecheck:
	$(MYPY) .

test:
	$(PYTEST) -q

shellcheck:
	bash -n install.sh install/stacksentry-ctl install/stacksentry-menu
	shellcheck install.sh install/stacksentry-ctl install/stacksentry-menu

sudoers:
	sudo visudo -cf install/stacksentry-sudoers

check: format-check lint typecheck test shellcheck sudoers

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
