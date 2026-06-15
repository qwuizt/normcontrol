from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src.structures import BoundingBox, Paths

from references_normcontrol.docx_references import DocxReferenceIndex, find_reference, find_reference_by_ordinal_index
from references_normcontrol.references_validation import (
    ReferenceEntry,
    ReferenceIssue,
    ReferenceValidationResult,
    collect_reference_validation_result,
)


@dataclass(frozen=True)
class PdfReferenceSnapshot:
    ordinal_index: int
    number: int
    expected_number: int
    number_is_valid: bool
    text: str
    page_nums: list[int]
    first_page_num: int
    paragraph_key: str


@dataclass(frozen=True)
class PdfIssueDocxLink:
    level: str
    message: str
    page_num: int
    bbox: dict[str, int | float]
    reference_number: int | None
    reference_ordinal_index: int | None
    expected_reference_number: int | None
    number_is_valid: bool | None
    pdf_reference_text: str | None
    docx_reference_text: str | None
    docx_matched: bool
    docx_paragraph_indexes: list[int]
    docx_match_score: float
    mapping_strategy: str
    reason: str


@dataclass(frozen=True)
class PdfDocxReferenceMapping:
    section_found: bool
    pdf_reference_count: int
    pdf_issue_count: int
    links: list[PdfIssueDocxLink]
    pdf_references: list[PdfReferenceSnapshot]


def build_pdf_docx_reference_mapping(
    paths_object: Paths,
    docx_reference_index: DocxReferenceIndex,
) -> PdfDocxReferenceMapping:
    """
    Связать PDF-замечания по списку литературы с DOCX-источниками.

    Основной стабильный ключ для раздела литературы - номер источника. PDF
    замечание привязывается к PDF ``ReferenceEntry`` по странице и пересечению
    bbox. Затем тот же номер ищется в DOCX index.
    """
    pdf_result = collect_reference_validation_result(paths_object)
    return link_reference_validation_result(pdf_result, docx_reference_index)


def link_reference_validation_result(
    pdf_result: ReferenceValidationResult,
    docx_reference_index: DocxReferenceIndex,
) -> PdfDocxReferenceMapping:
    pdf_references = [
        snapshot_pdf_reference(entry, ordinal_index=ordinal_index)
        for ordinal_index, entry in enumerate(pdf_result.entries)
    ]
    links = [link_issue(issue, pdf_result.entries, docx_reference_index) for issue in pdf_result.issues]
    return PdfDocxReferenceMapping(
        section_found=pdf_result.section_found,
        pdf_reference_count=len(pdf_result.entries),
        pdf_issue_count=len(pdf_result.issues),
        links=links,
        pdf_references=pdf_references,
    )


def link_issue(
    issue: ReferenceIssue,
    pdf_entries: list[ReferenceEntry],
    docx_reference_index: DocxReferenceIndex,
) -> PdfIssueDocxLink:
    pdf_entry = find_pdf_entry_for_issue(issue, pdf_entries)
    if pdf_entry is None:
        return PdfIssueDocxLink(
            level=issue.level.value,
            message=issue.message,
            page_num=issue.page_num,
            bbox=bbox_to_dict(issue.bbox),
            reference_number=None,
            reference_ordinal_index=None,
            expected_reference_number=None,
            number_is_valid=None,
            pdf_reference_text=None,
            docx_reference_text=None,
            docx_matched=False,
            docx_paragraph_indexes=[],
            docx_match_score=0.0,
            mapping_strategy='unmatched',
            reason='PDF-замечание не удалось привязать к источнику',
        )

    pdf_ordinal_index = get_pdf_entry_ordinal_index(pdf_entry, pdf_entries)
    expected_number = pdf_ordinal_index + 1
    number_is_valid = pdf_entry.number == expected_number and pdf_entry.number_raw == f'{expected_number}.'
    docx_match = find_reference_by_ordinal_index(docx_reference_index, pdf_ordinal_index)
    mapping_strategy = 'ordinal_index'
    if docx_match.entry is None:
        docx_match = find_reference(docx_reference_index, reference_number=pdf_entry.number)
        mapping_strategy = 'number_fallback'

    if docx_match.entry is None:
        return PdfIssueDocxLink(
            level=issue.level.value,
            message=issue.message,
            page_num=issue.page_num,
            bbox=bbox_to_dict(issue.bbox),
            reference_number=pdf_entry.number,
            reference_ordinal_index=pdf_ordinal_index,
            expected_reference_number=expected_number,
            number_is_valid=number_is_valid,
            pdf_reference_text=pdf_entry.text,
            docx_reference_text=None,
            docx_matched=False,
            docx_paragraph_indexes=[],
            docx_match_score=docx_match.score,
            mapping_strategy=mapping_strategy,
            reason=docx_match.reason,
        )

    return PdfIssueDocxLink(
        level=issue.level.value,
        message=issue.message,
        page_num=issue.page_num,
        bbox=bbox_to_dict(issue.bbox),
        reference_number=pdf_entry.number,
        reference_ordinal_index=pdf_ordinal_index,
        expected_reference_number=expected_number,
        number_is_valid=number_is_valid,
        pdf_reference_text=pdf_entry.text,
        docx_reference_text=docx_match.entry.text,
        docx_matched=True,
        docx_paragraph_indexes=docx_match.entry.paragraph_indexes,
        docx_match_score=docx_match.score,
        mapping_strategy=mapping_strategy,
        reason='PDF-замечание связано с DOCX-источником по порядковому индексу',
    )


def find_pdf_entry_for_issue(issue: ReferenceIssue, entries: list[ReferenceEntry]) -> ReferenceEntry | None:
    candidates = [entry for entry in entries if any(line.page_num == issue.page_num for line in entry.lines)]
    if not candidates:
        return None

    best_entry: ReferenceEntry | None = None
    best_score = -1.0
    for entry in candidates:
        bbox = entry.bbox_on_page(issue.page_num)
        score = bbox_overlap_score(issue.bbox, bbox)
        if score > best_score:
            best_entry = entry
            best_score = score

    if best_score <= 0 and best_entry is not None:
        return find_nearest_entry_by_vertical_center(issue, candidates)
    return best_entry


def find_nearest_entry_by_vertical_center(
    issue: ReferenceIssue,
    entries: list[ReferenceEntry],
) -> ReferenceEntry | None:
    issue_center_y = (issue.bbox.top + issue.bbox.bottom) / 2
    return min(
        entries,
        key=lambda entry: abs(issue_center_y - entry.bbox_on_page(issue.page_num).center[1]),
        default=None,
    )


def bbox_overlap_score(issue_bbox: BoundingBox, entry_bbox: BoundingBox) -> float:
    intersection = issue_bbox & entry_bbox
    intersection_area = intersection.area
    if intersection_area <= 0:
        return 0.0
    return intersection_area / max(issue_bbox.area, 1)


def get_pdf_entry_ordinal_index(entry: ReferenceEntry, entries: list[ReferenceEntry]) -> int:
    for ordinal_index, candidate in enumerate(entries):
        if candidate is entry:
            return ordinal_index
    return entries.index(entry)


def snapshot_pdf_reference(entry: ReferenceEntry, ordinal_index: int) -> PdfReferenceSnapshot:
    page_nums = sorted({line.page_num for line in entry.lines})
    expected_number = ordinal_index + 1
    return PdfReferenceSnapshot(
        ordinal_index=ordinal_index,
        number=entry.number,
        expected_number=expected_number,
        number_is_valid=entry.number == expected_number and entry.number_raw == f'{expected_number}.',
        text=entry.text,
        page_nums=page_nums,
        first_page_num=entry.page_num,
        paragraph_key=f'pdf-reference-{entry.number}',
    )


def bbox_to_dict(bbox: BoundingBox) -> dict[str, int | float]:
    return {
        'top': bbox.top,
        'left': bbox.left,
        'bottom': bbox.bottom,
        'right': bbox.right,
    }


def save_pdf_docx_reference_mapping(path_mapping: Path, mapping: PdfDocxReferenceMapping) -> None:
    path_mapping.write_text(json.dumps(asdict(mapping), ensure_ascii=False, indent=2), encoding='utf-8')
