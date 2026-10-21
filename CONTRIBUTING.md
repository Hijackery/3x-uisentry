# Как помочь проекту / Contributing

**Русский** · [English](#english)

Спасибо, что хотите помочь. Проект небольшой, поэтому правила простые.

## Окружение

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Обязательные проверки перед PR

```bash
make check   # format + ruff + mypy + pytest + shellcheck + visudo
```

Или по отдельности:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/python -m mypy .          # strict, 3.10+
.venv/bin/python -m pytest          # все тесты должны быть зелёными
bash -n install.sh && shellcheck install.sh install/stacksentry-ctl install/stacksentry-menu
sudo visudo -cf install/stacksentry-sudoers
```

Те же проверки гоняются в CI на Python 3.10 и 3.13 (плюс отдельная job для
скриптов установки).

## Стиль

- Type hints везде, `mypy --strict` без исключений; строка ≤ 88 символов.
- Комментарии и docstring — по-русски, идентификаторы — по-английски. Комментарий
  пишем там, где объясняется **почему**, а не что делает код.
- Новый функционал и любое исправление — с тестом-регрессией: в проекте принято
  фиксировать тестом именно найденный баг (см. `tests/test_review_fixes.py`).
- Скрипты установки: `set -euo pipefail`, никаких `eval`/`bash -c` с
  пользовательскими строками, все аргументы валидируются.

## Коммиты и PR

- Сообщение коммита: императив, до 72 символов в первой строке, дальше —
  подробности построчно.
- Релизы — семантические теги `vX.Y.Z`; порядок описан в
  [docs/RELEASING.md](docs/RELEASING.md). Не переписывайте опубликованные теги.
- В описании PR: что изменилось и **как проверено** (команды + результат).
- Не коммитьте `.env`, токены, `state.db`, логи. Проверьте `git diff --cached`.

## Что особенно приветствуется

- Поддержка новых дистрибутивов (init-системы, пакетные менеджеры).
- Улучшения UX в Telegram: понятнее текст, меньше тапов, безопаснее кнопки.
- Наблюдаемость: новые метрики 3X-UI/Xray, если они есть в API панели.
- Документация: `docs/`, README (ru/en), help бота и `stacksentry-menu --help`
  должны совпадать с фактическим поведением.
- Новые панели/протоколы: адаптер, тесты, строка в
  [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) и CHANGELOG.

---

## English

Thanks for helping. The project is small, so the rules are short.

**Environment:** `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt`.

**Required checks before a PR:** `ruff format --check .`, `ruff check .`,
`mypy .` (strict, 3.10+), `pytest`, `bash -n` + `shellcheck` for the shell
scripts, and `visudo -cf install/stacksentry-sudoers`. The same checks run in CI
on Python 3.10 and 3.13, plus a separate job for the installer scripts.

**Style:** type hints everywhere, `mypy --strict`, 88-column limit; comments and
docstrings in Russian, identifiers in English; comments explain *why*, not *what*.
Every fix or feature comes with a regression test — the project convention is to
test the exact bug that was found (see `tests/test_review_fixes.py`). Installer
scripts: `set -euo pipefail`, no `eval`/`bash -c` with user input, validate every
argument.

**Commits and PRs:** imperative subject up to 72 characters, details below;
releases are semantic `vX.Y.Z` tags (see [docs/RELEASING.md](docs/RELEASING.md)),
never rewrite published tags. Describe what changed and how it was verified. Never commit `.env`,
tokens, `state.db` or logs — check `git diff --cached`.

**Especially welcome:** support for more distributions (init systems, package
managers), Telegram UX improvements, extra 3X-UI/Xray metrics available in the
panel API, and keeping `docs/` + bot help + `stacksentry-menu --help` in sync.
