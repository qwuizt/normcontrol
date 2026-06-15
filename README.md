# References Normcontrol

Python-прототип для поиска замечаний в списке литературы и внесения исправлений в DOCX в режиме Track Changes.

## Сначала прочитайте ограничения

- Проект проверяет и редактирует только раздел списка литературы/источников, а не весь документ.
- DOCX проверяется через промежуточный PDF: `DOCX -> PDF -> анализ PDF`. Для конвертации используется [`docx2pdf`](https://github.com/AlJohri/docx2pdf), поэтому на macOS/Windows нужен установленный Microsoft Word.
- GigaChat используется только как генератор варианта исправленной библиографической записи. Модель может ошибаться, поэтому итоговый `edited_tracked.docx` нужно просматривать вручную.
- Модель не должна выдумывать даты обращения, объем книги, DOI, URL, издательство и другие фактические данные. Если данных нет, prompt требует вставлять placeholders: `[укажите дату обращения]`, `[укажите количество страниц]`.
- Часть PDF-пайплайна перенесена из `auto-normcontrol` как legacy-код в `src/` и `nodes/`. Это сделано для воспроизводимости НИР, а не как финальная архитектура библиотеки.

## Контекст

Прототип выделен из проекта `auto-normcontrol` и решает одну практическую задачу: автоматизировать нормоконтроль списка литературы в учебных и научных работах.

Основная цепочка работы:

```text
PDF -> поиск раздела списка литературы -> группировка источников -> rule-based замечания
DOCX -> PDF -> замечания PDF -> сопоставление с DOCX -> GigaChat -> DOCX Track Changes
```

Фоновая информация:

- [Python packaging with pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [`uv` package manager](https://docs.astral.sh/uv/)
- [`python-docx`](https://python-docx.readthedocs.io/) для чтения структуры DOCX
- [Office Open XML](https://learn.microsoft.com/en-us/office/open-xml/open-xml-sdk) как формат DOCX/PPTX/XLSX
- [`docx2pdf`](https://github.com/AlJohri/docx2pdf) для конвертации DOCX в PDF через Microsoft Word
- [GigaChat Python SDK](https://github.com/ai-forever/gigachat)

## Термины

- **Список литературы / список источников**: раздел документа с библиографическими записями.
- **PDF-пайплайн**: последовательность `parsing_pdf -> detection_document_structure -> references_validation -> visualization`.
- **Track Changes**: режим исправлений Microsoft Word. В DOCX хранится как элементы [`w:ins` и `w:del`](https://learn.microsoft.com/en-us/office/open-xml/word/working-with-wordprocessingml-documents).
- **OOXML**: XML-формат документов Microsoft Office. DOCX фактически является zip-архивом с XML-файлами.
- **Mapping PDF/DOCX**: сопоставление замечания, найденного на PDF, с конкретной библиографической записью в DOCX.
- **Placeholder**: явный текст вместо неизвестного факта, например `[укажите дату обращения]`.

## Установка

Требования:

- Python `>=3.12,<3.14`
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Microsoft Word для сценариев `check-docx` и `edit-docx`
- ключ GigaChat только для режима `--use-gigachat`

Команды:

```bash
cd <path_to_project> 
uv sync
cp .env.example .env
```

Заполните `.env`:

```env
GIGACHAT_CREDENTIALS=...
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=
GIGACHAT_VERIFY_SSL_CERTS=false
```


## Быстрый старт

Положите входной DOCX сюда:

```text
input_docx/
```

Запустите проверку и редактирование:

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<your_docx_name>.docx --output-dir fixed_docx --use-gigachat
```

Итоговый DOCX:

```text
fixed_docx/<your_docx_name>/edited_tracked.docx
```

## Сценарии CLI

### 1. Проверка PDF

```bash
uv run python references_normcontrol/run_normcontrol.py check-pdf <your_pdf_name>.pdf --output-dir output
```

Результат:

```text
output/<your_pdf_name>/
  <your_pdf_name>.pdf
  structure.csv
  summary/
  output.pdf
```

### 2. Проверка DOCX без редактирования

```bash
uv run python references_normcontrol/run_normcontrol.py check-docx input_docx/<your_docx_name>.docx --output-dir output
```

Что происходит:

```text
input.docx -> work_docx/check_docx/<your_docx_name>.pdf -> output/<your_docx_name>/
```

### 3. Проверка и редактирование DOCX

Без GigaChat, если передаются ручные правила:

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<your_docx_name>.docx --output-dir fixed_docx --rules rules.json
```

С GigaChat:

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<your_docx_name>.docx --output-dir fixed_docx --use-gigachat
```

Что происходит:

```text
DOCX -> PDF -> PDF-проверка -> PDF/DOCX mapping -> GigaChat -> edited_tracked.docx
```

## Структура проекта

```text
references_normcontrol/      # исследовательский код проверки и DOCX-редактирования
src/                         # минимальный legacy-код PDF-пайплайна
nodes/                       # минимальные legacy task-функции
input_docx/                  # входные DOCX
output/                      # результаты проверки PDF/DOCX
fixed_docx/                  # итоговые DOCX с Track Changes
work_docx/                   # промежуточные файлы DOCX->PDF
```

## API

### `references_normcontrol.run_normcontrol`

Единая CLI-точка входа.

Основные команды:

- `check-pdf`: проверить входной PDF.
- `check-docx`: сконвертировать DOCX в PDF и проверить PDF.
- `edit-docx`: выполнить полную цепочку проверки и редактирования DOCX.

### `references_normcontrol.pdf_pipeline`

Переиспользуемый PDF-пайплайн.

```python
from pathlib import Path
from references_normcontrol.pdf_pipeline import run_pdf_references_validation

workdir = run_pdf_references_validation(
    Path('input.pdf'),
    Path('output'),
    verbose=3,
)
```

Ключевые функции:

- `prepare_pdf_workdir(path_pdf, output_dir) -> Path`
- `run_pdf_references_validation(path_pdf, output_dir, verbose=3) -> Path`
- `run_pdf_references_validation_in_workdir(workdir, verbose=3) -> Path`

### `references_normcontrol.references_validation`

Rule-based проверка списка литературы в PDF.

```python
from src.structures import Paths
from references_normcontrol.references_validation import collect_reference_validation_result

result = collect_reference_validation_result(Paths.create(Path('output/input')))
print(len(result.entries), len(result.issues))
```

Основные типы:

- `ReferenceValidationResult`
- `ReferenceEntry`
- `ReferenceIssue`
- `SourceType`

Проверяются:

- нумерация;
- дубли и пропуски;
- тип источника;
- год;
- точка в конце записи;
- URL и дата обращения;
- строгий формат даты обращения: `(дата обращения: ДД.ММ.ГГГГ)`;
- объем книги или тома;
- признаки статьи, конференции, патента, диссертации, стандарта и нормативного акта.

### `references_normcontrol.docx_context`

Строит индекс DOCX-документа.

```python
from pathlib import Path
from references_normcontrol.docx_context import build_docx_context

context = build_docx_context(Path('input_docx/input.docx'))
print(context.paragraph_count)
```

Использует `python-docx` для чтения абзацев, таблиц, стилей и runs.

### `references_normcontrol.docx_references`

Выделяет библиографические записи из DOCX.

```python
from references_normcontrol.docx_context import build_docx_context
from references_normcontrol.docx_references import build_reference_index

context = build_docx_context(Path('input_docx/input.docx'))
reference_index = build_reference_index(context)
print(len(reference_index.entries))
```

Особенность: поддерживается автонумерация Word, потому что номер источника может не входить в `paragraph.text`.

### `references_normcontrol.pdf_docx_reference_mapping`

Связывает PDF-замечания с DOCX-источниками.

```python
from src.structures import Paths
from references_normcontrol.pdf_docx_reference_mapping import build_pdf_docx_reference_mapping

mapping = build_pdf_docx_reference_mapping(Paths.create(Path('output/input')), reference_index)
print(len(mapping.links))
```

Основная стратегия сопоставления: порядковый индекс источника.

### `references_normcontrol.gigachat_reference_editor`

Генерирует исправленный текст библиографической записи.

```python
from references_normcontrol.gigachat_reference_editor import (
    GigaChatReferenceTextEditor,
    generate_reference_fixes,
)

editor = GigaChatReferenceTextEditor()
fixes = generate_reference_fixes(mapping.links, editor)
```

Prompt запрещает:

- переводить источник на другой язык;
- выдумывать дату обращения;
- выдумывать количество страниц;
- подставлять случайные DOI, URL, издательства и места издания.

### `references_normcontrol.docx_tracked_editing`

Применяет замены в DOCX как Track Changes.

```python
from pathlib import Path
from references_normcontrol.docx_tracked_editing import apply_tracked_replacements

results = apply_tracked_replacements(
    Path('input_docx/input.docx'),
    Path('fixed_docx/input/edited_tracked.docx'),
    rules,
    references_only=True,
)
```

Важно: Track Changes создаются не публичным API `python-docx`, а прямым редактированием OOXML: `word/document.xml`, `w:ins`, `w:del`, `word/settings.xml`.

### `references_normcontrol.docx_to_pdf`

Конвертация DOCX в PDF через `docx2pdf`.

```python
from references_normcontrol.docx_to_pdf import convert_docx_to_pdf_with_word

path_pdf = convert_docx_to_pdf_with_word(Path('input_docx/input.docx'), Path('work_docx/check_docx'))
```

Это временная зависимость от Microsoft Word. 

## Варианты использования

1. Если есть только PDF, используйте `check-pdf`.
2. Если есть DOCX и нужно только найти замечания, используйте `check-docx`.
3. Если есть DOCX и нужно получить файл с исправлениями, используйте `edit-docx`.
4. Если нужны содержательные исправления текста, добавьте `--use-gigachat`.
5. После получения `edited_tracked.docx` откройте файл в Word и вручную проверьте все исправления.

