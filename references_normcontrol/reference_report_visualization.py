from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf

from references_normcontrol.reference_agent import ReferenceAgentEntryReport, ReferenceAgentReport
from references_normcontrol.references_validation import ReferenceEntry, collect_reference_validation_result
from src import paths
from src.structures import BoundingBox, Paths


def visualize_reference_report_on_pdf(
    pdf_workdir: Path,
    report: ReferenceAgentReport,
    *,
    output_pdf: Path | None = None,
) -> Path:
    """
    Отрисовать LLM-замечания из ``reference_report.json`` на PDF.

    PDF здесь не является валидатором: он используется только как поверхность
    просмотра уже готовых замечаний агента.
    """
    pdf_workdir = Path(pdf_workdir)
    path_pdf = pdf_workdir / paths.FILE_PDF_FILE_NAME
    if output_pdf is None:
        output_pdf = pdf_workdir / paths.FILE_PDF_FILE_OUTPUT

    if not path_pdf.exists():
        raise FileNotFoundError(f'PDF для визуализации не найден: {path_pdf}')

    pdf_result = collect_reference_validation_result(Paths.create(pdf_workdir), include_rule_issues=False)
    entries_by_ordinal = {
        ordinal_index: entry for ordinal_index, entry in enumerate(pdf_result.entries)
    }
    reports_with_issues = [entry for entry in report.entries if entry.issues]

    if not reports_with_issues:
        shutil.copy(path_pdf, output_pdf)
        write_issue_text_files(output_pdf.parent, report)
        return output_pdf

    with pymupdf.open(path_pdf) as document:
        summary_lines: list[str] = []
        for entry_report in reports_with_issues:
            pdf_entry = get_pdf_entry(entry_report, entries_by_ordinal)
            if pdf_entry is None:
                summary_lines.extend(format_summary_lines(entry_report, prefix='[без координат] '))
                continue

            issue_text = format_issue_comment(entry_report)
            summary_lines.extend(format_summary_lines(entry_report))
            for page_num in sorted({line.page_num for line in pdf_entry.lines}):
                if page_num >= document.page_count:
                    continue
                page = document[page_num]
                bbox = clamp_bbox(pdf_entry.bbox_on_page(page_num).extend(4), page)
                page.add_highlight_annot(bbox.rectangle_for_pymupdf)
                page.add_text_annot(comment_point(bbox, page), issue_text)

        if summary_lines and document.page_count:
            document[0].add_text_annot((10, 10), '\n'.join(summary_lines))

        document.save(output_pdf)

    write_issue_text_files(output_pdf.parent, report)
    return output_pdf


def get_pdf_entry(
    entry_report: ReferenceAgentEntryReport,
    entries_by_ordinal: dict[int, ReferenceEntry],
) -> ReferenceEntry | None:
    if entry_report.reference_ordinal_index is None:
        return None
    return entries_by_ordinal.get(entry_report.reference_ordinal_index)


def format_issue_comment(entry_report: ReferenceAgentEntryReport) -> str:
    header = f'Источник {entry_report.reference_number or entry_report.reference_ordinal_index}: {entry_report.source_subtype}'
    lines = [header]
    for issue in entry_report.issues:
        lines.append(f'- {issue.message}')
        if issue.evidence:
            lines.append(f'  Обоснование: {issue.evidence}')
    if entry_report.suggested_text:
        lines.append(f'Предложение: {entry_report.suggested_text}')
    return '\n'.join(lines)


def format_summary_lines(entry_report: ReferenceAgentEntryReport, *, prefix: str = '') -> list[str]:
    number = entry_report.reference_number or entry_report.reference_ordinal_index
    return [f'{prefix}Источник {number}: {issue.message}' for issue in entry_report.issues]


def clamp_bbox(bbox: BoundingBox, page: pymupdf.Page) -> BoundingBox:
    return BoundingBox(
        top=max(0, min(bbox.top, page.rect.height - 1)),
        left=max(0, min(bbox.left, page.rect.width - 1)),
        bottom=max(1, min(bbox.bottom, page.rect.height)),
        right=max(1, min(bbox.right, page.rect.width)),
    )


def comment_point(bbox: BoundingBox, page: pymupdf.Page) -> tuple[float, float]:
    x = min(max(bbox.right + 8, 10), page.rect.width - 20)
    y = min(max(bbox.top, 10), page.rect.height - 20)
    return x, y


def write_issue_text_files(pdf_workdir: Path, report: ReferenceAgentReport) -> None:
    errors: list[str] = []
    warnings: list[str] = []
    for entry in report.entries:
        for issue in entry.issues:
            message = f'Источник {entry.reference_number}: {issue.message}\n'
            if issue.level == 'error':
                errors.append(message)
            else:
                warnings.append(message)

    (pdf_workdir / paths.FILE_ERRORS).write_text(''.join(errors), encoding='utf-8')
    (pdf_workdir / paths.FILE_WARNINGS).write_text(''.join(warnings), encoding='utf-8')
