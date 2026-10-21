# Релизы

StackSentry использует [Semantic Versioning](https://semver.org/lang/ru/):
`MAJOR.MINOR.PATCH`. Версия хранится в `stacksentry/__init__.py` и видна в боте
командой `/version`.

## Что считается релизом

- Тег `vX.Y.Z` в `main` и GitHub Release с заметками.
- `CHANGELOG.md` содержит раздел с этой версией и датой.
- CI на коммите тега зелёный.
- Установщик умеет ставить конкретный тег: `--ref vX.Y.Z`.

Релизы не переписываются: если после публикации нужна правка — выпускается
следующая версия. `main` может опережать последний релиз; для продакшена
рекомендуется тег.

## Процесс

1. Обновите `CHANGELOG.md`: перенесите нужные пункты из `Unreleased` в новый
   раздел `## [X.Y.Z] — YYYY-MM-DD`.
2. Поднимите `__version__` в `stacksentry/__init__.py`.
3. Прогоните полный набор проверок:

   ```bash
   make check
   ```

4. Закоммитьте изменения и поставьте тег:

   ```bash
   git commit -am "release: X.Y.Z"
   git tag -a vX.Y.Z -m "StackSentry X.Y.Z"
   git push origin main --tags
   ```

5. Создайте GitHub Release:

   ```bash
   gh release create vX.Y.Z --title "StackSentry X.Y.Z" \
     --notes-file <(sed -n '/## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md | sed '$d')
   ```

6. Проверьте установку с тегом на чистой машине:

   ```bash
   sudo bash install.sh --ref vX.Y.Z
   ```

## Откат

- Код: `sudo bash install.sh --ref vX.Y.Z-1` (установщик сохранит `.env` и
  состояние, если не передан `--force-config`).
- Бот: `sudo stacksentry-menu` → обновление/переустановка с нужным тегом.
- Перед крупными обновлениями делайте резервную копию `.env` и каталога
  состояния (`STATE_PATH`, по умолчанию `/var/lib/stacksentry`).

## Поддержка версий

Поддерживается последний выпущенный минорный релиз и `main`. Критические
исправления безопасности выпускаются отдельным patch-релизом; подробности — в
[SECURITY.md](../SECURITY.md).
