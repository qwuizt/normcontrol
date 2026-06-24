from __future__ import annotations

import csv
import datetime as dt
import difflib
import json
import posixpath
import re
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree

from gigachat import GigaChat

from references_normcontrol.docx_references import DocxReferenceIndex
from references_normcontrol.docx_tracked_editing import TrackedReplacementRule
from references_normcontrol.llm_utils import (
    clamp_float,
    extract_response_content,
    parse_bool_env,
    parse_json_object,
)
from references_normcontrol.references_validation import (
    CONFERENCE_RE,
    DISSERTATION_RE,
    GOST_NUMBER_RE,
    LEGAL_ACT_RE,
    PATENT_RE,
    URL_MARKER_RE,
    URL_RE,
    SourceType,
    is_book_like_reference,
)

REFERENCE_REPORT_FILENAME = 'reference_report.json'
REFERENCE_REPORT_SCHEMA_VERSION = 'reference-report/v1'
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE_EXAMPLES_PATH = PROJECT_ROOT / 'reference_examples.json'
LEGACY_REFERENCE_EXAMPLES_PATH = Path.home() / 'Desktop' / 'примеры оформления источников.xlsx'
DEFAULT_REFERENCE_EXAMPLES_PATHS = (DEFAULT_REFERENCE_EXAMPLES_PATH, LEGACY_REFERENCE_EXAMPLES_PATH)
EXAMPLE_MATCHING_AGENT_MODE = 'example_matching_without_llm'
GIGACHAT_AGENT_MODE = 'gigachat_reference_agent'
GIGACHAT_GOST2017_BASELINE_MODE = 'gigachat_gost2017_baseline'

XLSX_MAIN_NS = {'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
XLSX_RELS_NS = {'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}


@dataclass(frozen=True)
class ReferenceExample:
    row_number: int
    source_subtype: str
    example_text: str
    explanation: str | None = None


@dataclass(frozen=True)
class MatchedReferenceExample:
    row_number: int
    source_subtype: str
    score: float
    explanation: str | None = None


@dataclass(frozen=True)
class ReferenceAgentIssue:
    level: str
    message: str
    evidence: str
    old_text: str
    new_text: str
    confidence: float


@dataclass(frozen=True)
class ReferenceAgentEntryReport:
    reference_number: int | None
    reference_ordinal_index: int | None
    reference_text: str
    source_family: str
    source_subtype: str | None
    matched_examples: list[int]
    suggested_text: str | None
    issues: list[ReferenceAgentIssue]


@dataclass(frozen=True)
class ReferenceAgentReport:
    schema_version: str
    agent_mode: str
    examples_source: str | None
    generated_at: str
    entries: list[ReferenceAgentEntryReport]
    warnings: list[str]


@dataclass(frozen=True)
class ReferenceAgentRequest:
    reference_number: int | None
    reference_ordinal_index: int | None
    reference_text: str
    matched_examples: list[MatchedReferenceExample]
    examples: list[ReferenceExample]
    fallback_source_family: str
    fallback_source_subtype: str | None


@dataclass(frozen=True)
class ReferenceAgentValidation:
    source_family: str
    source_subtype: str | None
    suggested_text: str | None
    issues: list[ReferenceAgentIssue]
    warnings: list[str]
    matched_examples: list[MatchedReferenceExample] | None = None


@dataclass(frozen=True)
class ReferenceAgentClassification:
    source_family: str
    source_subtype: str | None
    confidence: float
    reason: str | None = None


class ReferenceAgentValidator(Protocol):
    agent_mode: str

    def validate_reference(self, request: ReferenceAgentRequest) -> ReferenceAgentValidation:
        """Определить тип источника и замечания для одной библиографической записи."""


class ExampleMatchingReferenceAgentValidator:
    """
    Заглушка агентного валидатора без LLM.

    Используется для локальных прогонов без ключа модели: тип источника и
    ближайшие примеры уже подобраны, а замечания не генерируются.
    """

    agent_mode = EXAMPLE_MATCHING_AGENT_MODE

    def validate_reference(self, request: ReferenceAgentRequest) -> ReferenceAgentValidation:
        return ReferenceAgentValidation(
            source_family=request.fallback_source_family,
            source_subtype=request.fallback_source_subtype,
            suggested_text=None,
            issues=[],
            warnings=[],
            matched_examples=request.matched_examples,
        )


class GigaChatReferenceAgentValidator:
    """LLM-валидатор библиографических записей по базе эталонных примеров."""

    agent_mode = GIGACHAT_AGENT_MODE

    def __init__(
        self,
        *,
        credentials: str | None = None,
        scope: str | None = None,
        model: str | None = None,
        verify_ssl_certs: bool | None = None,
        timeout: float | None = 60.0,
    ) -> None:
        import os

        credentials = credentials or os.getenv('GIGACHAT_CREDENTIALS')
        if not credentials:
            raise ValueError('Для GigaChat нужен env GIGACHAT_CREDENTIALS')

        if verify_ssl_certs is None:
            verify_ssl_certs = parse_bool_env(os.getenv('GIGACHAT_VERIFY_SSL_CERTS'), default=True)

        self.client = GigaChat(
            credentials=credentials,
            scope=scope or os.getenv('GIGACHAT_SCOPE'),
            model=model or os.getenv('GIGACHAT_MODEL'),
            verify_ssl_certs=verify_ssl_certs,
            timeout=timeout,
        )

    def validate_reference(self, request: ReferenceAgentRequest) -> ReferenceAgentValidation:
        classification_prompt = build_reference_classification_prompt(request)
        classification_response = self.client.chat(classification_prompt)
        classification_payload = parse_json_object(extract_response_content(classification_response))
        classification = parse_reference_classification_payload(classification_payload, request)
        matched_examples = find_examples_for_classified_source(
            request.reference_text,
            classification.source_family,
            classification.source_subtype,
            request.examples,
        )
        comparison_request = replace(
            request,
            matched_examples=matched_examples,
            fallback_source_family=classification.source_family,
            fallback_source_subtype=classification.source_subtype,
        )
        prompt = build_reference_agent_prompt(comparison_request)
        response = self.client.chat(prompt)
        payload = parse_json_object(extract_response_content(response))
        validation = parse_reference_agent_payload(payload, comparison_request)
        return replace(validation, matched_examples=matched_examples)


class GigaChatGost2017BaselineValidator:
    """Baseline: попросить LLM переписать запись по ГОСТ 7.32-2017 без retrieval-слоя."""

    agent_mode = GIGACHAT_GOST2017_BASELINE_MODE

    def __init__(
        self,
        *,
        credentials: str | None = None,
        scope: str | None = None,
        model: str | None = None,
        verify_ssl_certs: bool | None = None,
        timeout: float | None = 60.0,
    ) -> None:
        import os

        credentials = credentials or os.getenv('GIGACHAT_CREDENTIALS')
        if not credentials:
            raise ValueError('Для GigaChat нужен env GIGACHAT_CREDENTIALS')

        if verify_ssl_certs is None:
            verify_ssl_certs = parse_bool_env(os.getenv('GIGACHAT_VERIFY_SSL_CERTS'), default=True)

        self.client = GigaChat(
            credentials=credentials,
            scope=scope or os.getenv('GIGACHAT_SCOPE'),
            model=model or os.getenv('GIGACHAT_MODEL'),
            verify_ssl_certs=verify_ssl_certs,
            timeout=timeout,
        )

    def validate_reference(self, request: ReferenceAgentRequest) -> ReferenceAgentValidation:
        prompt = build_gost2017_baseline_prompt(request)
        response = self.client.chat(prompt)
        payload = parse_json_object(extract_response_content(response))
        return parse_gost2017_baseline_payload(payload, request)


def resolve_reference_examples_path(path_examples: Path | None) -> Path | None:
    if path_examples is not None:
        path_examples = Path(path_examples)
        if not path_examples.exists():
            raise FileNotFoundError(f'База примеров оформления источников не найдена: {path_examples}')
        return path_examples

    for default_path in DEFAULT_REFERENCE_EXAMPLES_PATHS:
        if default_path.exists():
            return default_path
    return None


def load_reference_examples(path_examples: Path) -> list[ReferenceExample]:
    suffix = path_examples.suffix.casefold()
    if suffix == '.json':
        return load_reference_examples_from_json(path_examples)
    if suffix == '.csv':
        return load_reference_examples_from_csv(path_examples)

    rows = read_first_sheet_rows(path_examples)
    return load_reference_examples_from_rows(rows)


def load_reference_examples_from_csv(path_examples: Path) -> list[ReferenceExample]:
    text = path_examples.read_text(encoding='utf-8-sig')
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    return load_reference_examples_from_rows(rows)


def load_reference_examples_from_json(path_examples: Path) -> list[ReferenceExample]:
    payload = json.loads(path_examples.read_text(encoding='utf-8'))
    raw_examples = payload.get('examples') if isinstance(payload, dict) else payload
    if not isinstance(raw_examples, list):
        raise ValueError('JSON-база примеров должна содержать массив examples или быть массивом объектов')

    examples: list[ReferenceExample] = []
    for index, item in enumerate(raw_examples, start=2):
        if not isinstance(item, dict):
            continue
        source_subtype = normalize_optional_text(item.get('source_subtype') or item.get('Тип источника'))
        example_text = normalize_optional_text(item.get('example_text') or item.get('Примеры') or item.get('пример'))
        if not source_subtype or not example_text:
            continue
        row_number = parse_int(item.get('row_number'), default=index)
        examples.append(
            ReferenceExample(
                row_number=row_number,
                source_subtype=source_subtype,
                example_text=example_text,
                explanation=normalize_optional_text(item.get('explanation') or item.get('Пояснение')),
            )
        )
    return examples


def load_reference_examples_from_rows(rows: list[list[str]]) -> list[ReferenceExample]:
    if not rows:
        return []

    header = [normalize_header(cell) for cell in rows[0]]
    type_idx = find_header_index(header, {'тип источника', 'тип'})
    example_idx = find_header_index(header, {'примеры', 'пример'})
    explanation_idx = find_header_index(header, {'пояснение', 'комментарий'})

    examples: list[ReferenceExample] = []
    for zero_based_index, row in enumerate(rows[1:], start=2):
        source_subtype = get_row_value(row, type_idx)
        example_text = get_row_value(row, example_idx)
        if not source_subtype or not example_text:
            continue

        explanation = get_row_value(row, explanation_idx) if explanation_idx is not None else ''
        examples.append(
            ReferenceExample(
                row_number=zero_based_index,
                source_subtype=source_subtype,
                example_text=example_text,
                explanation=explanation or None,
            )
        )
    return examples


def find_header_index(header: list[str], candidates: set[str]) -> int | None:
    for index, value in enumerate(header):
        if value in candidates:
            return index
    return None


def get_row_value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ''
    return normalize_spaces(row[index])


def read_first_sheet_rows(path_xlsx: Path) -> list[list[str]]:
    with zipfile.ZipFile(path_xlsx) as workbook_zip:
        shared_strings = read_shared_strings(workbook_zip)
        sheet_path = find_first_sheet_path(workbook_zip)
        sheet_root = ElementTree.fromstring(workbook_zip.read(sheet_path))

    rows: list[list[str]] = []
    for row_element in sheet_root.findall('.//main:sheetData/main:row', XLSX_MAIN_NS):
        cells: dict[int, str] = {}
        max_column = -1
        for cell_element in row_element.findall('main:c', XLSX_MAIN_NS):
            column_index = cell_column_index(cell_element.get('r', ''))
            if column_index is None:
                column_index = max_column + 1
            cells[column_index] = read_cell_value(cell_element, shared_strings)
            max_column = max(max_column, column_index)

        if max_column < 0:
            rows.append([])
            continue
        rows.append([cells.get(index, '') for index in range(max_column + 1)])
    return rows


def read_shared_strings(workbook_zip: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(workbook_zip.read('xl/sharedStrings.xml'))
    except KeyError:
        return []

    shared_strings: list[str] = []
    for item in root.findall('main:si', XLSX_MAIN_NS):
        shared_strings.append(normalize_spaces(''.join(text.text or '' for text in item.findall('.//main:t', XLSX_MAIN_NS))))
    return shared_strings


def find_first_sheet_path(workbook_zip: zipfile.ZipFile) -> str:
    workbook_root = ElementTree.fromstring(workbook_zip.read('xl/workbook.xml'))
    first_sheet = workbook_root.find('.//main:sheets/main:sheet', XLSX_MAIN_NS)
    if first_sheet is None:
        raise ValueError('В XLSX не найден ни один лист')

    relation_id = first_sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
    if not relation_id:
        raise ValueError('У первого листа XLSX нет relationship id')

    rels_root = ElementTree.fromstring(workbook_zip.read('xl/_rels/workbook.xml.rels'))
    for rel in rels_root.findall('rel:Relationship', XLSX_RELS_NS):
        if rel.attrib.get('Id') != relation_id:
            continue
        target = rel.attrib.get('Target')
        if not target:
            break
        target = target.lstrip('/')
        if target.startswith('xl/'):
            return posixpath.normpath(target)
        return posixpath.normpath(posixpath.join('xl', target))

    raise ValueError('Не удалось найти XML первого листа XLSX')


def read_cell_value(cell_element: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell_element.attrib.get('t')
    if cell_type == 'inlineStr':
        return normalize_spaces(''.join(text.text or '' for text in cell_element.findall('.//main:t', XLSX_MAIN_NS)))

    value_element = cell_element.find('main:v', XLSX_MAIN_NS)
    if value_element is None or value_element.text is None:
        return ''

    raw_value = value_element.text
    if cell_type == 's':
        try:
            return shared_strings[int(raw_value)]
        except (IndexError, ValueError):
            return ''
    return normalize_spaces(raw_value)


def cell_column_index(cell_reference: str) -> int | None:
    match = re.match(r'([A-Z]+)', cell_reference)
    if match is None:
        return None

    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord('A') + 1)
    return index - 1


def build_reference_report(
    reference_index: DocxReferenceIndex,
    examples: list[ReferenceExample],
    *,
    examples_source: Path | None = None,
    validator: ReferenceAgentValidator | None = None,
) -> ReferenceAgentReport:
    warnings: list[str] = []
    validator = validator or ExampleMatchingReferenceAgentValidator()
    if not examples and validator.agent_mode != GIGACHAT_GOST2017_BASELINE_MODE:
        warnings.append('База примеров оформления источников пуста или не подключена')
    entries_by_ordinal = build_entry_reports_from_docx(reference_index, examples, validator, warnings)

    return ReferenceAgentReport(
        schema_version=REFERENCE_REPORT_SCHEMA_VERSION,
        agent_mode=validator.agent_mode,
        examples_source=str(examples_source) if examples_source is not None else None,
        generated_at=utc_now(),
        entries=[entries_by_ordinal[key] for key in sorted(entries_by_ordinal)],
        warnings=warnings,
    )


def build_entry_reports_from_docx(
    reference_index: DocxReferenceIndex,
    examples: list[ReferenceExample],
    validator: ReferenceAgentValidator,
    warnings: list[str],
) -> dict[int, ReferenceAgentEntryReport]:
    entries_by_ordinal: dict[int, ReferenceAgentEntryReport] = {}
    for entry in reference_index.entries:
        entries_by_ordinal[entry.ordinal_index] = make_entry_report(
            reference_number=entry.number,
            reference_ordinal_index=entry.ordinal_index,
            reference_text=entry.text,
            examples=examples,
            validator=validator,
            warnings=warnings,
        )
    return entries_by_ordinal


def make_entry_report(
    *,
    reference_number: int | None,
    reference_ordinal_index: int | None,
    reference_text: str,
    examples: list[ReferenceExample],
    validator: ReferenceAgentValidator,
    warnings: list[str],
) -> ReferenceAgentEntryReport:
    source_family = detect_source_family(reference_text)
    matched_examples = find_matching_examples(reference_text, source_family, examples)
    source_subtype = matched_examples[0].source_subtype if matched_examples else None
    request = ReferenceAgentRequest(
        reference_number=reference_number,
        reference_ordinal_index=reference_ordinal_index,
        reference_text=reference_text,
        matched_examples=matched_examples,
        examples=examples,
        fallback_source_family=source_family,
        fallback_source_subtype=source_subtype,
    )
    try:
        validation = validator.validate_reference(request)
        warnings.extend(validation.warnings)
    except Exception as exc:
        warnings.append(
            f'LLM-валидация источника {reference_number or reference_ordinal_index} не выполнена: {exc}'
        )
        validation = ExampleMatchingReferenceAgentValidator().validate_reference(request)
    report_matched_examples = validation.matched_examples
    if report_matched_examples is None:
        report_matched_examples = matched_examples

    return ReferenceAgentEntryReport(
        reference_number=reference_number,
        reference_ordinal_index=reference_ordinal_index,
        reference_text=reference_text,
        source_family=validation.source_family,
        source_subtype=validation.source_subtype,
        matched_examples=[example.row_number for example in report_matched_examples],
        suggested_text=validation.suggested_text,
        issues=validation.issues,
    )


def find_matching_examples(
    reference_text: str,
    source_family: str,
    examples: list[ReferenceExample],
    *,
    limit: int = 3,
) -> list[MatchedReferenceExample]:
    return score_reference_examples(
        reference_text,
        examples,
        source_family=source_family,
        source_subtype=None,
        limit=limit,
    )


def find_examples_for_classified_source(
    reference_text: str,
    source_family: str,
    source_subtype: str | None,
    examples: list[ReferenceExample],
    *,
    limit: int = 3,
) -> list[MatchedReferenceExample]:
    """
    Выбрать примеры после LLM-классификации.

    Сначала берутся примеры точного подтипа, затем примеры того же общего
    типа. Это сохраняет требуемый порядок pipeline: LLM классифицирует
    источник, а retrieval-слой уже после этого выбирает примеры из базы.
    """
    if not examples:
        return []

    selected: list[MatchedReferenceExample] = []
    used_rows: set[int] = set()
    known_subtype = find_known_source_subtype(source_subtype, examples)
    if known_subtype is not None:
        exact_examples = [
            example
            for example in examples
            if normalize_for_similarity(example.source_subtype) == normalize_for_similarity(known_subtype)
        ]
        selected.extend(
            score_reference_examples(
                reference_text,
                exact_examples,
                source_family=source_family,
                source_subtype=known_subtype,
                limit=limit,
            )
        )
        used_rows.update(example.row_number for example in selected)

    if len(selected) < limit:
        family_examples = [
            example
            for example in examples
            if example.row_number not in used_rows
            and detect_source_family(f'{example.source_subtype} {example.example_text}') == source_family
        ]
        family_matches = score_reference_examples(
            reference_text,
            family_examples,
            source_family=source_family,
            source_subtype=known_subtype,
            limit=limit - len(selected),
        )
        selected.extend(family_matches)
        used_rows.update(example.row_number for example in family_matches)

    if len(selected) < limit:
        fallback_examples = [example for example in examples if example.row_number not in used_rows]
        selected.extend(
            score_reference_examples(
                reference_text,
                fallback_examples,
                source_family=source_family,
                source_subtype=known_subtype,
                limit=limit - len(selected),
            )
        )

    return selected[:limit]


def score_reference_examples(
    reference_text: str,
    examples: list[ReferenceExample],
    *,
    source_family: str,
    source_subtype: str | None,
    limit: int,
) -> list[MatchedReferenceExample]:
    reference_norm = normalize_for_similarity(reference_text)
    if not reference_norm:
        return []

    scored: list[MatchedReferenceExample] = []
    normalized_source_subtype = normalize_for_similarity(source_subtype or '')
    for example in examples:
        example_family = detect_source_family(f'{example.source_subtype} {example.example_text}')
        score = difflib.SequenceMatcher(None, reference_norm, normalize_for_similarity(example.example_text)).ratio()
        if normalized_source_subtype and normalize_for_similarity(example.source_subtype) == normalized_source_subtype:
            score += 0.3
        if example_family == source_family:
            score += 0.15
        scored.append(
            MatchedReferenceExample(
                row_number=example.row_number,
                source_subtype=example.source_subtype,
                score=round(min(score, 1.0), 4),
                explanation=example.explanation,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]


def build_reference_classification_prompt(request: ReferenceAgentRequest) -> str:
    return (
        'Ты классификатор библиографической записи для системы нормоконтроля.\n'
        'Определи тип источника до сравнения с эталонными примерами.\n'
        'Используй только текст записи и список типов из базы. Не ищи и не выдумывай фактические данные.\n'
        'Верни только JSON без Markdown.\n\n'
        f'Номер источника: {request.reference_number}\n'
        f'Исходная запись:\n{request.reference_text}\n\n'
        f'Типы из базы примеров:\n{format_reference_type_catalog(request.examples)}\n\n'
        'Формат ответа:\n'
        '{\n'
        '  "source_family": "book|article|web|legal_act|standard|patent|dissertation|conference|unknown",\n'
        '  "source_subtype": "один из типов базы или null",\n'
        '  "confidence": 0.0,\n'
        '  "reason": "краткое основание классификации"\n'
        '}'
    )


def build_reference_agent_prompt(request: ReferenceAgentRequest) -> str:
    examples_text = '\n\n'.join(
        format_reference_example_for_prompt(example, match)
        for match in request.matched_examples
        for example in request.examples
        if example.row_number == match.row_number
    )
    if not examples_text:
        examples_text = 'Эталонные примеры не найдены. Используй только текст записи и не выдумывай факты.'

    return (
        'Ты агент нормоконтроля библиографической записи.\n'
        'Тип источника уже классифицирован на предыдущем шаге.\n'
        'Твоя задача: сравнить запись с выбранными эталонными примерами этого типа и вернуть замечания.\n'
        'Не используй внешние знания о фактических данных источника. Не выдумывай авторов, даты, страницы, DOI, URL, '
        'издательство, место издания или дату обращения.\n'
        'Если фактических данных не хватает, используй явные placeholders: '
        '[укажите дату обращения], [укажите количество страниц], [укажите место издания].\n'
        'Если ошибок нет, верни пустой массив issues и null в suggested_text.\n'
        'Если ошибки есть, suggested_text должен быть полной исправленной библиографической записью целиком, '
        'без номера источника в начале.\n'
        'Верни только JSON без Markdown.\n\n'
        f'Номер источника: {request.reference_number}\n'
        f'Порядковый индекс с нуля: {request.reference_ordinal_index}\n'
        f'Классифицированный общий тип: {request.fallback_source_family}\n'
        f'Классифицированный подтип: {request.fallback_source_subtype}\n\n'
        f'Исходная запись:\n{request.reference_text}\n\n'
        f'Эталонные примеры выбранного типа:\n{examples_text}\n\n'
        'Формат ответа:\n'
        '{\n'
        '  "source_family": "book|article|web|legal_act|standard|patent|dissertation|conference|unknown",\n'
        '  "source_subtype": "подтип из эталонных примеров или null",\n'
        '  "suggested_text": "полная исправленная запись без номера источника или null",\n'
        '  "issues": [\n'
        '    {\n'
        '      "level": "error|warning",\n'
        '      "message": "краткое замечание",\n'
        '      "evidence": "почему это замечание следует из эталона",\n'
        '      "old_text": "исходная запись целиком или проблемный фрагмент",\n'
        '      "new_text": "исправленная запись целиком или пустая строка",\n'
        '      "confidence": 0.0\n'
        '    }\n'
        '  ]\n'
        '}'
    )


def build_gost2017_baseline_prompt(request: ReferenceAgentRequest) -> str:
    return (
        'Ты выполняешь baseline-нормоконтроль одной библиографической записи.\n'
        'Перепиши запись согласно ГОСТ 7.32-2017 для списка использованных источников.\n'
        'Не сравнивай с базой примеров и не объясняй ход рассуждений.\n'
        'Не выдумывай фактические данные: авторов, название, год, страницы, DOI, URL, издательство, место издания '
        'или дату обращения.\n'
        'Если обязательных фактических данных нет, используй placeholders: '
        '[укажите дату обращения], [укажите количество страниц], [укажите место издания].\n'
        'Не добавляй номер источника в начало: номер задается Word.\n'
        'Верни только JSON без Markdown.\n\n'
        f'Номер источника: {request.reference_number}\n'
        f'Исходная запись:\n{request.reference_text}\n\n'
        'Формат ответа:\n'
        '{\n'
        '  "new_text": "переписанная библиографическая запись целиком без номера источника",\n'
        '  "reason": "кратко, что изменено или почему изменений нет",\n'
        '  "confidence": 0.0\n'
        '}'
    )


def format_reference_example_for_prompt(example: ReferenceExample, match: MatchedReferenceExample) -> str:
    explanation = f'\nПояснение: {example.explanation}' if example.explanation else ''
    return (
        f'Строка базы: {example.row_number}\n'
        f'Тип источника: {example.source_subtype}\n'
        f'Похожесть: {match.score:.2f}\n'
        f'Пример: {example.example_text}'
        f'{explanation}'
    )


def format_reference_type_catalog(examples: list[ReferenceExample]) -> str:
    if not examples:
        return (
            '- book\n'
            '- article\n'
            '- web\n'
            '- legal_act\n'
            '- standard\n'
            '- patent\n'
            '- dissertation\n'
            '- conference\n'
            '- unknown'
        )

    rows: dict[tuple[str, str], int] = {}
    for example in examples:
        family = detect_source_family(f'{example.source_subtype} {example.example_text}')
        key = (family, example.source_subtype)
        rows[key] = rows.get(key, 0) + 1

    return '\n'.join(
        f'- source_family={family}; source_subtype={subtype}; examples={count}'
        for (family, subtype), count in sorted(rows.items(), key=lambda item: (item[0][0], item[0][1]))
    )


def parse_reference_classification_payload(
    payload: dict,
    request: ReferenceAgentRequest,
) -> ReferenceAgentClassification:
    source_family = normalize_source_family(payload.get('source_family'), request.fallback_source_family)
    source_subtype = normalize_optional_text(
        payload.get('source_subtype') or payload.get('source_type') or payload.get('type')
    )
    source_subtype = find_known_source_subtype(source_subtype, request.examples) or source_subtype

    return ReferenceAgentClassification(
        source_family=source_family,
        source_subtype=source_subtype or request.fallback_source_subtype,
        confidence=round(clamp_float(payload.get('confidence', 0.0)), 4),
        reason=normalize_optional_text(payload.get('reason')),
    )


def parse_gost2017_baseline_payload(payload: dict, request: ReferenceAgentRequest) -> ReferenceAgentValidation:
    new_text = normalize_optional_text(payload.get('new_text') or payload.get('suggested_text'))
    reason = normalize_optional_text(payload.get('reason')) or 'LLM baseline: переписывание записи по ГОСТ 7.32-2017.'
    confidence = round(clamp_float(payload.get('confidence', 0.0)), 4)
    issues: list[ReferenceAgentIssue] = []

    if new_text and normalize_spaces(new_text) != normalize_spaces(request.reference_text):
        issues.append(
            ReferenceAgentIssue(
                level='warning',
                message='LLM предложила переписать источник по ГОСТ 7.32-2017',
                evidence=reason,
                old_text=request.reference_text,
                new_text=new_text,
                confidence=confidence,
            )
        )

    return ReferenceAgentValidation(
        source_family=request.fallback_source_family,
        source_subtype=request.fallback_source_subtype,
        suggested_text=new_text,
        issues=issues,
        warnings=[],
    )


def parse_reference_agent_payload(payload: dict, request: ReferenceAgentRequest) -> ReferenceAgentValidation:
    source_family = normalize_source_family(payload.get('source_family'), request.fallback_source_family)
    source_subtype = normalize_optional_text(payload.get('source_subtype')) or request.fallback_source_subtype
    suggested_text = normalize_optional_text(payload.get('suggested_text') or payload.get('corrected_text'))
    issues = parse_reference_agent_issues(payload.get('issues'), request.reference_text)

    if suggested_text is None:
        suggested_text = choose_suggested_text_from_issues(issues, request.reference_text)
    if suggested_text is not None and normalize_spaces(suggested_text) == normalize_spaces(request.reference_text):
        suggested_text = None

    return ReferenceAgentValidation(
        source_family=source_family,
        source_subtype=source_subtype,
        suggested_text=suggested_text,
        issues=issues,
        warnings=[],
    )


def parse_reference_agent_issues(value, reference_text: str) -> list[ReferenceAgentIssue]:
    if not isinstance(value, list):
        return []

    issues: list[ReferenceAgentIssue] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        message = normalize_optional_text(item.get('message'))
        if not message:
            continue
        level = normalize_issue_level(item.get('level'))
        old_text = normalize_optional_text(item.get('old_text')) or reference_text
        new_text = normalize_optional_text(item.get('new_text')) or ''
        issues.append(
            ReferenceAgentIssue(
                level=level,
                message=message,
                evidence=normalize_optional_text(item.get('evidence')) or 'Замечание сформировано LLM-агентом.',
                old_text=old_text,
                new_text=new_text,
                confidence=round(clamp_float(item.get('confidence', 0.0)), 4),
            )
        )
    return issues


def reference_report_to_replacement_rules(
    report: ReferenceAgentReport,
    reference_index: DocxReferenceIndex,
    *,
    min_confidence: float = 0.5,
) -> list[TrackedReplacementRule]:
    entries_by_ordinal = {entry.ordinal_index: entry for entry in reference_index.entries}
    rules: list[TrackedReplacementRule] = []

    for entry_report in report.entries:
        if entry_report.reference_ordinal_index is None:
            continue
        docx_entry = entries_by_ordinal.get(entry_report.reference_ordinal_index)
        if docx_entry is None:
            continue

        replacement_text = choose_replacement_text(entry_report, min_confidence=min_confidence)
        if not replacement_text:
            continue
        replacement_text = strip_leading_reference_number(replacement_text, entry_report.reference_number)
        if normalize_spaces(replacement_text) == normalize_spaces(docx_entry.text):
            continue

        rules.append(
            TrackedReplacementRule(
                old_text=docx_entry.text,
                new_text=replacement_text,
                rule_id=f'reference-agent-{entry_report.reference_ordinal_index}',
                comment='; '.join(issue.message for issue in entry_report.issues)[:500],
                max_replacements=1,
                reference_number=entry_report.reference_number,
                query_text=docx_entry.text,
                target_paragraph_indexes=docx_entry.paragraph_indexes,
            )
        )

    return rules


def choose_replacement_text(entry_report: ReferenceAgentEntryReport, *, min_confidence: float) -> str | None:
    if entry_report.suggested_text:
        confident_issues = [issue for issue in entry_report.issues if issue.confidence >= min_confidence]
        if confident_issues or not entry_report.issues:
            return entry_report.suggested_text

    candidates = [
        issue
        for issue in entry_report.issues
        if issue.confidence >= min_confidence and issue.new_text and issue.new_text != issue.old_text
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda issue: issue.confidence, reverse=True)
    return candidates[0].new_text


def choose_suggested_text_from_issues(issues: list[ReferenceAgentIssue], reference_text: str) -> str | None:
    for issue in sorted(issues, key=lambda item: item.confidence, reverse=True):
        if not issue.new_text:
            continue
        if normalize_spaces(issue.new_text) != normalize_spaces(reference_text):
            return issue.new_text
    return None


def detect_source_family(text: str) -> str:
    source_type = detect_source_type_from_text(text)
    return source_type.value


def detect_source_type_from_text(text: str) -> SourceType:
    lower = text.lower()
    if 'гост' in lower or GOST_NUMBER_RE.search(text):
        return SourceType.STANDARD
    if URL_MARKER_RE.search(text) or URL_RE.search(text) or 'электронный ресурс' in lower:
        return SourceType.WEB
    if PATENT_RE.search(text):
        return SourceType.PATENT
    if DISSERTATION_RE.search(text):
        return SourceType.DISSERTATION
    if CONFERENCE_RE.search(text) and ('конф' in lower or 'науч' in lower or 'тезис' in lower):
        return SourceType.CONFERENCE
    if LEGAL_ACT_RE.search(text):
        return SourceType.LEGAL_ACT
    if is_book_like_reference(text):
        return SourceType.BOOK
    if '//' in text:
        return SourceType.ARTICLE
    return SourceType.UNKNOWN


def save_reference_report(path_report: Path, report: ReferenceAgentReport) -> None:
    path_report.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding='utf-8')


def load_examples_or_warn(path_examples: Path | None) -> tuple[list[ReferenceExample], Path | None, list[str]]:
    resolved_path = resolve_reference_examples_path(path_examples)
    if resolved_path is None:
        defaults = ', '.join(str(path) for path in DEFAULT_REFERENCE_EXAMPLES_PATHS)
        return [], None, [f'База примеров не найдена по умолчанию: {defaults}']
    return load_reference_examples(resolved_path), resolved_path, []


def normalize_spaces(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text).replace('\xa0', ' ')).strip()


def normalize_header(text: str) -> str:
    return normalize_spaces(text).casefold()


def normalize_for_similarity(text: str) -> str:
    text = normalize_spaces(text).casefold()
    return re.sub(r'[^\wа-яё]+', ' ', text, flags=re.IGNORECASE).strip()


def normalize_source_family(value, fallback: str) -> str:
    text = normalize_optional_text(value)
    allowed = {source_type.value for source_type in SourceType}
    if text in allowed:
        return text
    return fallback


def find_known_source_subtype(value: str | None, examples: list[ReferenceExample]) -> str | None:
    normalized = normalize_for_similarity(value or '')
    if not normalized:
        return None
    for example in examples:
        if normalize_for_similarity(example.source_subtype) == normalized:
            return example.source_subtype
    return None


def normalize_issue_level(value) -> str:
    text = normalize_optional_text(value)
    if text in {'error', 'warning'}:
        return text
    return 'warning'


def normalize_optional_text(value) -> str | None:
    if value is None:
        return None
    text = normalize_spaces(str(value))
    if not text or text.casefold() == 'null':
        return None
    return text


def parse_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def strip_leading_reference_number(text: str, reference_number: int | None) -> str:
    if reference_number is None:
        return text
    return re.sub(r'^\s*(?:\[\d{1,3}]|\d{1,3}\.)\s+', '', text, count=1)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
