from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from references_normcontrol.docx_context import DocxDocumentContext, DocxParagraphContext, normalize_docx_text
from references_normcontrol.docx_tracked_editing import TrackedReplacementRule

DOT_NUMBER_RE = re.compile(r'^\s*(?P<number>\d{1,3})\.\s*(?P<body>.*)$')
BRACKET_NUMBER_RE = re.compile(r'^\s*\[(?P<number>\d{1,3})]\s*(?P<body>.*)$')


@dataclass(frozen=True)
class DocxReferenceEntry:
    ordinal_index: int
    number: int
    number_raw: str
    text: str
    normalized_text: str
    text_hash: str
    paragraph_indexes: list[int]
    first_paragraph_index: int
    last_paragraph_index: int


@dataclass(frozen=True)
class DocxReferenceIndex:
    entries: list[DocxReferenceEntry]
    orphan_paragraph_indexes: list[int]


@dataclass(frozen=True)
class DocxLocation:
    ordinal_index: int | None
    paragraph_indexes: list[int]
    first_paragraph_index: int
    last_paragraph_index: int
    source: str = 'references'
    confidence: float = 1.0


@dataclass(frozen=True)
class ReferenceMatch:
    entry: DocxReferenceEntry | None
    score: float
    reason: str


@dataclass(frozen=True)
class EditProposal:
    rule_id: str
    old_text: str
    new_text: str
    reference_text: str | None
    location: DocxLocation | None
    reference_number: int | None
    reference_ordinal_index: int | None
    expected_reference_number: int | None
    matched: bool
    match_score: float
    reason: str
    comment: str = ''


def build_number_fix_rules(reference_index: DocxReferenceIndex) -> list[TrackedReplacementRule]:
    """
    Подготовить правила исправления неверной нумерации по порядку записей.

    Если запись стоит на позиции ``ordinal_index``, ожидаемый номер равен
    ``ordinal_index + 1``. Правка заменяет только числовой маркер записи
    (`2.` или ошибочный `[2]`), а не весь текст источника.
    """
    rules: list[TrackedReplacementRule] = []
    for entry in reference_index.entries:
        expected_number = entry.ordinal_index + 1
        if entry.number == expected_number and entry.number_raw == f'{expected_number}.':
            continue
        old_number = entry.number_raw
        new_number = f'{expected_number}.'
        rules.append(
            TrackedReplacementRule(
                old_text=old_number,
                new_text=new_number,
                rule_id=f'fix-reference-number-{entry.ordinal_index}',
                comment=f'Исправить номер источника {entry.number} на {expected_number}',
                max_replacements=1,
                reference_number=entry.number,
                query_text=entry.text,
                target_paragraph_indexes=[entry.first_paragraph_index],
            )
        )
    return rules


def build_reference_index(context: DocxDocumentContext) -> DocxReferenceIndex:
    """
    Сгруппировать абзацы раздела литературы в логические записи.

    Новая запись начинается с корректного ``1.`` или ошибочного ``[1]``.
    Ошибочный bracket-номер распознается, чтобы запись можно было исправить
    через Track Changes. Абзацы без нового номера считаются продолжением
    текущей записи. Если явных номеров нет, но раздел оформлен автонумерацией
    Word, каждый непустой абзац списка считается отдельной записью.
    """
    reference_paragraphs = [
        paragraph for paragraph in context.paragraphs if paragraph.in_references and paragraph.text.strip()
    ]
    entries = build_explicit_number_reference_entries(reference_paragraphs)
    if entries:
        orphan_indexes = find_orphan_reference_paragraph_indexes(reference_paragraphs)
        return DocxReferenceIndex(entries=entries, orphan_paragraph_indexes=orphan_indexes)

    return build_auto_numbered_reference_index(reference_paragraphs)


def build_explicit_number_reference_entries(
    reference_paragraphs: list[DocxParagraphContext],
) -> list[DocxReferenceEntry]:
    """Собрать записи, когда номер источника находится прямо в тексте абзаца."""
    entries: list[DocxReferenceEntry] = []
    current_number: int | None = None
    current_number_raw = ''
    current_paragraphs: list[DocxParagraphContext] = []

    for paragraph in reference_paragraphs:
        start = parse_reference_start(paragraph.text)
        if start is not None:
            if current_number is not None:
                entries.append(
                    make_reference_entry(
                        current_number,
                        current_number_raw,
                        current_paragraphs,
                        ordinal_index=len(entries),
                    )
                )
            current_number, current_number_raw = start
            current_paragraphs = [paragraph]
            continue

        if current_number is not None:
            current_paragraphs.append(paragraph)

    if current_number is not None:
        entries.append(
            make_reference_entry(
                current_number,
                current_number_raw,
                current_paragraphs,
                ordinal_index=len(entries),
            )
        )

    return entries


def find_orphan_reference_paragraph_indexes(
    reference_paragraphs: list[DocxParagraphContext],
) -> list[int]:
    """Найти непустые абзацы до первой явно пронумерованной записи."""
    orphan_paragraph_indexes: list[int] = []
    found_first_entry = False
    for paragraph in reference_paragraphs:
        if parse_reference_start(paragraph.text) is not None:
            found_first_entry = True
            continue
        if not found_first_entry:
            orphan_paragraph_indexes.append(paragraph.paragraph_index)
    return orphan_paragraph_indexes


def build_auto_numbered_reference_index(
    reference_paragraphs: list[DocxParagraphContext],
) -> DocxReferenceIndex:
    """Собрать записи из автонумерованного Word-списка, где номер не входит в paragraph.text."""
    list_paragraphs = [
        paragraph for paragraph in reference_paragraphs if is_probable_auto_numbered_reference_paragraph(paragraph)
    ]
    paragraphs = list_paragraphs or reference_paragraphs
    entries = [
        make_reference_entry(
            ordinal_index + 1,
            f'{ordinal_index + 1}.',
            [paragraph],
            ordinal_index=ordinal_index,
        )
        for ordinal_index, paragraph in enumerate(paragraphs)
    ]
    entry_paragraph_indexes = {entry.first_paragraph_index for entry in entries}
    orphan_paragraph_indexes = [
        paragraph.paragraph_index
        for paragraph in reference_paragraphs
        if paragraph.paragraph_index not in entry_paragraph_indexes
    ]
    return DocxReferenceIndex(entries=entries, orphan_paragraph_indexes=orphan_paragraph_indexes)


def is_probable_auto_numbered_reference_paragraph(paragraph: DocxParagraphContext) -> bool:
    style_name = (paragraph.style_name or '').casefold()
    return 'list' in style_name or 'спис' in style_name


def parse_reference_start(text: str) -> tuple[int, str] | None:
    for pattern in (DOT_NUMBER_RE, BRACKET_NUMBER_RE):
        match = pattern.match(text)
        if match is None:
            continue
        number_raw = match.group(0)[: match.start('body')].strip()
        return int(match.group('number')), number_raw
    return None


def make_reference_entry(
    number: int,
    number_raw: str,
    paragraphs: list[DocxParagraphContext],
    ordinal_index: int,
) -> DocxReferenceEntry:
    text = normalize_reference_text(' '.join(paragraph.text for paragraph in paragraphs))
    normalized_text = normalize_docx_text(text)
    paragraph_indexes = [paragraph.paragraph_index for paragraph in paragraphs]
    return DocxReferenceEntry(
        ordinal_index=ordinal_index,
        number=number,
        number_raw=number_raw,
        text=text,
        normalized_text=normalized_text,
        text_hash=paragraphs[0].text_hash if len(paragraphs) == 1 else hash_text(normalized_text),
        paragraph_indexes=paragraph_indexes,
        first_paragraph_index=paragraph_indexes[0],
        last_paragraph_index=paragraph_indexes[-1],
    )


def normalize_reference_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()


def hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def find_reference(
    reference_index: DocxReferenceIndex,
    *,
    reference_number: int | None = None,
    query_text: str | None = None,
    min_score: float = 0.55,
) -> ReferenceMatch:
    if reference_number is not None:
        for entry in reference_index.entries:
            if entry.number == reference_number:
                return ReferenceMatch(entry=entry, score=1.0, reason='Найдено по номеру источника')
        return ReferenceMatch(entry=None, score=0.0, reason=f'Источник с номером {reference_number} не найден')

    if not query_text:
        return ReferenceMatch(entry=None, score=0.0, reason='Не задан номер источника или текстовый запрос')

    normalized_query = normalize_docx_text(query_text)
    if not normalized_query:
        return ReferenceMatch(entry=None, score=0.0, reason='Текстовый запрос пуст после нормализации')

    best_entry: DocxReferenceEntry | None = None
    best_score = 0.0
    best_reason = 'Совпадение не найдено'

    for entry in reference_index.entries:
        if normalized_query in entry.normalized_text:
            score = len(normalized_query) / max(len(entry.normalized_text), 1)
            score = max(score, 0.8)
            if score > best_score:
                best_entry = entry
                best_score = score
                best_reason = 'Найдено по подстроке'
            continue

        score = difflib.SequenceMatcher(None, normalized_query, entry.normalized_text).ratio()
        if score > best_score:
            best_entry = entry
            best_score = score
            best_reason = 'Найдено по fuzzy-сходству'

    if best_entry is None or best_score < min_score:
        return ReferenceMatch(entry=None, score=best_score, reason='Нет совпадения выше порога')

    return ReferenceMatch(entry=best_entry, score=best_score, reason=best_reason)


def find_reference_by_ordinal_index(
    reference_index: DocxReferenceIndex,
    ordinal_index: int,
) -> ReferenceMatch:
    for entry in reference_index.entries:
        if entry.ordinal_index == ordinal_index:
            return ReferenceMatch(entry=entry, score=1.0, reason='Найдено по порядковому индексу источника')
    return ReferenceMatch(entry=None, score=0.0, reason=f'Источник с порядковым индексом {ordinal_index} не найден')


def build_edit_proposals(
    reference_index: DocxReferenceIndex,
    rules: list[TrackedReplacementRule],
) -> list[EditProposal]:
    proposals: list[EditProposal] = []

    for rule in rules:
        reference_number = getattr(rule, 'reference_number', None)
        query_text = getattr(rule, 'query_text', None) or rule.old_text
        match = find_reference(
            reference_index,
            reference_number=reference_number,
            query_text=query_text,
        )

        if match.entry is None:
            proposals.append(
                EditProposal(
                    rule_id=rule.rule_id,
                    old_text=rule.old_text,
                    new_text=rule.new_text,
                    reference_text=None,
                    location=None,
                    reference_number=reference_number,
                    reference_ordinal_index=None,
                    expected_reference_number=None,
                    matched=False,
                    match_score=match.score,
                    reason=match.reason,
                    comment=rule.comment,
                )
            )
            continue

        proposals.append(
            EditProposal(
                rule_id=rule.rule_id,
                old_text=rule.old_text,
                new_text=rule.new_text,
                reference_text=match.entry.text,
                location=DocxLocation(
                    ordinal_index=match.entry.ordinal_index,
                    paragraph_indexes=match.entry.paragraph_indexes,
                    first_paragraph_index=match.entry.first_paragraph_index,
                    last_paragraph_index=match.entry.last_paragraph_index,
                    confidence=match.score,
                ),
                reference_number=match.entry.number,
                reference_ordinal_index=match.entry.ordinal_index,
                expected_reference_number=match.entry.ordinal_index + 1,
                matched=True,
                match_score=match.score,
                reason=match.reason,
                comment=rule.comment,
            )
        )

    return proposals


def save_reference_index(path_index: Path, reference_index: DocxReferenceIndex) -> None:
    path_index.write_text(json.dumps(asdict(reference_index), ensure_ascii=False, indent=2), encoding='utf-8')


def save_edit_proposals(path_proposals: Path, proposals: list[EditProposal]) -> None:
    path_proposals.write_text(
        json.dumps([asdict(proposal) for proposal in proposals], ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def save_replacement_rules(path_rules: Path, rules: list[TrackedReplacementRule]) -> None:
    path_rules.write_text(
        json.dumps([asdict(rule) for rule in rules], ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
