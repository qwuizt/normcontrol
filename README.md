# References Normcontrol

Python-прототип агентной проверки списка литературы и внесения исправлений в DOCX в режиме Track Changes.

## Текущая концепция

Система ориентирована на LLM-проверку библиографических записей:

```text
DOCX -> извлечение списка литературы -> LLM-классификация типа источника
     -> подбор примеров этого типа -> LLM-сравнение и генерация замечаний -> reference_report.json
reference_report.json -> DOCX Track Changes
reference_report.json -> опциональная PDF-визуализация замечаний
```

## Ограничения

- Проверяется и редактируется только раздел списка литературы/источников.
- PDF создается для визуализации замечаний в `output.pdf`; Для конвертации DOCX -> PDF используется библеотека [`docx2pdf`](https://github.com/AlJohri/docx2pdf), для которой необходим установленный Microsoft Word.
- В сценарии `run_normcontrol.py edit-docx` можно работать без PDF: флаг `--skip-pdf` отключает `docx2pdf` и оставляет только `DOCX -> LLM/reference_report.json -> Track Changes`.
- GigaChat используется в режимах `--use-gigachat` и `--llm-baseline`.
- Prompt запрещает выдумывать DOI, URL, издательство, место издания, объем книги, дату обращения и другие фактические данные. Если данных нет, модель должна использовать placeholders вроде `[укажите дату обращения]`.

## Термины

- **Reference agent**: компонент, который формирует `reference_report.json` по каждой библиографической записи.
- **`reference_report.json`**: основной машинный отчет агента с типом источника, примером, замечаниями и предложенным исправленным текстом.
- **PDF-визуализация**: `output.pdf` с подсветкой источников, для которых агент нашел замечания.
- **Track Changes**: исправления Microsoft Word, записанные напрямую в OOXML как `w:ins` и `w:del`.
- **Legacy PDF validation**: старые проверки PDF на основе правил; не являются основным агентным сценарием.

## Установка

Требования:

- Python `>=3.12,<3.14`
- [`uv`](https://docs.astral.sh/uv/)
- Microsoft Word только для PDF-визуализации через DOCX -> PDF
- ключ GigaChat для LLM-режимов

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

Baseline без `docx2pdf`: прямой LLM-запрос "перепиши источник согласно ГОСТ 7.32-2017" без базы примеров, результат сразу в DOCX Track Changes.

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<file>.docx --output-dir fixed_docx --llm-baseline --skip-pdf
```

Основной агентный режим с базой примеров:

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<file>.docx --output-dir fixed_docx --use-gigachat --skip-pdf
```

Если нужна PDF-визуализация замечаний, не передавайте `--skip-pdf`:

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<file>.docx --output-dir fixed_docx --use-gigachat
```

Если файл примеров лежит не в репозитории:

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<file>.docx --output-dir fixed_docx --use-gigachat --skip-pdf --reference-examples "/path/to/reference_examples.json"
```

Основные результаты без PDF:

```text
fixed_docx/<file>/
  input.docx
  reference_report.json
  reference_agent_replacement_rules.json
  edited_tracked.docx
  docx_edits_report.json
```

Дополнительно при запуске без `--skip-pdf`:

```text
fixed_docx/<file>/
  input.pdf
  structure.csv
  output.pdf
```

## Режимы

### `edit-docx` без LLM

```bash
uv run python references_normcontrol/run_normcontrol.py edit-docx input_docx/<file>.docx --output-dir fixed_docx --skip-pdf
```

Система анализирует структуру DOCX-документа, выделяет раздел списка литературы и формирует `reference_report.json`. Без `--use-gigachat`, `--llm-baseline` или ручного `--rules` содержательные исправления не генерируются.

### `edit-docx --skip-pdf`

Отключает DOCX -> PDF через `docx2pdf`. В этом режиме не создаются `input.pdf`, `structure.csv` и `output.pdf`, но сохраняются `reference_report.json`, `reference_agent_replacement_rules.json`, `edited_tracked.docx` и `docx_edits_report.json`.

### `edit-docx --use-gigachat`

LLM сначала классифицирует тип источника. Затем система выбирает из базы примеры этого типа, после чего LLM сравнивает запись с выбранными примерами и возвращает структурированные замечания. Если агент предлагает `suggested_text`, оно преобразуется в правила замены и применяется к DOCX через Track Changes.

### `edit-docx --llm-baseline`

Минимальный baseline без базы примеров: каждая запись отправляется в LLM с просьбой переписать источник по ГОСТ 7.32-2017. Ответ сохраняется в `suggested_text` и применяется к DOCX, если отличается от исходного текста.

### `edit-docx --rules rules.json`

Применяет ручные правила замен. Можно использовать вместе с агентными режимами: ручные правила и правила из `reference_report.json` применяются последовательно.

### `edit-docx --legacy-pdf-validation`

Дополнительно запускает старый rule-based PDF validator. Этот режим нужен только для сравнения с прежним pipeline и несовместим с `--skip-pdf`.

### `check-pdf` и `check-docx`

Legacy-команды для старой rule-based проверки PDF:

```bash
uv run python references_normcontrol/run_normcontrol.py check-pdf input.pdf --output-dir output
uv run python references_normcontrol/run_normcontrol.py check-docx input_docx/<file>.docx --output-dir output
```

## Структура проекта

```text
references_normcontrol/
  run_normcontrol.py                 # единая CLI-точка входа
  run_docx_editing.py                # orchestration DOCX -> report -> PDF -> Track Changes
  reference_agent.py                 # LLM/reference agent и JSON-схема отчета
  reference_report_visualization.py  # PDF-визуализация замечаний из reference_report.json
  pdf_pipeline.py                    # PDF parsing/structure и legacy validation
  references_validation.py           # извлечение PDF-источников и legacy rule-based checks
  docx_context.py                    # индекс DOCX
  docx_references.py                 # выделение библиографических записей
  docx_tracked_editing.py            # OOXML Track Changes
  docx_to_pdf.py                     # DOCX -> PDF через Microsoft Word
  llm_utils.py                       # общие helpers для env/JSON/LLM-ответов

src/
  ...                                # минимальный legacy-код PDF-структуры и визуализации

nodes/
  ...                                # минимальные legacy task-функции PDF-пайплайна

```

## Отчет в агентном режиме

`reference_report.json` содержит:

- `agent_mode`: режим генерации отчета;
- `examples_source`: путь к базе примеров, если она использовалась;
- `entries`: записи списка литературы;
- `source_family`: общий тип источника, например `book`, `article`, `web`;
- `source_subtype`: ближайший подтип из базы примеров или LLM-классификации;
- `matched_examples`: примеры, выбранные после LLM-классификации типа источника;
- `suggested_text`: полная исправленная запись, если агент предлагает замену;
- `issues`: замечания с `message`, `old_text`, `new_text`, `evidence`, `confidence`.

Имя файла фиксированное:

```text
reference_report.json
```

## Как строится PDF визуализация

1. Если `--skip-pdf` не передан, `edit-docx` конвертирует входной DOCX в `input.pdf`.
2. `pdf_pipeline.run_pdf_structure_extraction_in_workdir` строит `structure.csv`.
3. `reference_report_visualization.visualize_reference_report_on_pdf` берет замечания из `reference_report.json`.
4. Для каждой записи используется порядковый индекс источника из DOCX и соответствующая PDF-запись из `structure.csv`.
5. На `output.pdf` подсвечивается bounding box найденной библиографической записи, рядом добавляется PDF-комментарий с текстом замечания.

## API

### `references_normcontrol.reference_agent`

Основные классы:

- `ExampleMatchingReferenceAgentValidator`: локальный подбор похожих примеров без LLM.
- `GigaChatReferenceAgentValidator`: основной двухэтапный LLM-агент с базой примеров.
- `GigaChatGost2017BaselineValidator`: baseline direct rewrite по ГОСТ 7.32-2017.
- `ReferenceAgentReport`: JSON-схема отчета.
- `reference_report_to_replacement_rules`: преобразование `suggested_text` в правила Track Changes.

### `references_normcontrol.run_docx_editing`

```python
from pathlib import Path
from references_normcontrol.run_docx_editing import run_docx_editing

workdir = run_docx_editing(
    Path("input_docx/input.docx"),
    path_rules=None,
    output_dir=Path("fixed_docx"),
    use_gigachat=True,
)
```

### `references_normcontrol.docx_tracked_editing`

```python
from pathlib import Path
from references_normcontrol.docx_tracked_editing import apply_tracked_replacements

results = apply_tracked_replacements(
    Path("input.docx"),
    Path("edited_tracked.docx"),
    rules,
    references_only=True,
)
```

Track Changes создаются прямым редактированием OOXML: `word/document.xml`, `w:ins`, `w:del`, `word/settings.xml`.
