from __future__ import annotations

import datetime as dt
import json
import logging
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from docx import Document
from lxml import etree

logger = logging.getLogger(__name__)

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML_NS = 'http://www.w3.org/XML/1998/namespace'
NS = {'w': W_NS}

REFERENCE_TITLE_RE = (
    r'^\s*список\s+('
    r'использованных\s+источников|'
    r'литературы|'
    r'использованной\s+литературы|'
    r'использованных\s+источников\s+и\s+литературы'
    r')\s*$'
)
REFERENCE_END_RE = r'^\s*приложени[ея]\b'


@dataclass(frozen=True)
class TrackedReplacementRule:
    old_text: str
    new_text: str
    rule_id: str = ''
    comment: str = ''
    max_replacements: int = 1
    reference_number: int | None = None
    query_text: str | None = None
    target_paragraph_indexes: list[int] | None = None


@dataclass(frozen=True)
class ParagraphSnapshot:
    index: int
    text: str
    in_references: bool


@dataclass
class TrackedReplacementResult:
    rule_id: str
    old_text: str
    new_text: str
    applied: bool
    replacements: int = 0
    paragraph_indexes: list[int] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RevisionInspection:
    track_revisions_enabled: bool
    insertions: int
    deletions: int


def qn(tag: str) -> str:
    return f'{{{W_NS}}}{tag}'


def xml_bytes(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def load_replacement_rules(path_rules: Path) -> list[TrackedReplacementRule]:
    """
    Прочитать JSON со списком ручных замен для исследовательского runner.

    Ожидаемый формат:
    [
      {"old_text": "...", "new_text": "...", "rule_id": "optional"}
    ]
    """
    data = json.loads(path_rules.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError('Файл правил должен содержать JSON-массив замен')

    rules: list[TrackedReplacementRule] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError('Каждое правило замены должно быть JSON-объектом')
        rules.append(TrackedReplacementRule(**item))
    return rules


def extract_docx_paragraphs(path_docx: Path, references_only: bool = True) -> list[ParagraphSnapshot]:
    """
    Извлечь абзацы через python-docx для диагностики и будущего поиска контекста.

    На этом этапе Track Changes записываются низкоуровнево в OOXML, но
    python-docx остается удобным слоем для чтения структуры документа.
    """
    document = Document(str(path_docx))
    texts = [paragraph.text for paragraph in document.paragraphs]
    reference_indexes = find_reference_paragraph_indexes(texts) if references_only else set(range(len(texts)))

    return [
        ParagraphSnapshot(index=index, text=text, in_references=index in reference_indexes)
        for index, text in enumerate(texts)
    ]


def apply_tracked_replacements(
    input_docx: Path,
    output_docx: Path,
    rules: list[TrackedReplacementRule],
    *,
    references_only: bool = True,
    author: str = 'AutoNormControl',
) -> list[TrackedReplacementResult]:
    """
    Применить текстовые замены в DOCX как настоящие Track Changes.

    Ограничение первого этапа: замена выполняется в ``word/document.xml``.
    Если ``old_text`` совпадает со всем текстом абзаца, заменяется весь абзац.
    Иначе поддерживается замена только внутри одного ``w:t`` text node.
    """
    input_docx = Path(input_docx)
    output_docx = Path(output_docx)
    output_docx.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(input_docx, 'r') as zin:
        document_root = etree.fromstring(zin.read('word/document.xml'))
        settings_root = read_or_create_settings(zin)

        enable_track_revisions(settings_root)
        next_change_id = get_next_change_id(document_root)
        timestamp = utc_now()
        paragraphs = document_root.xpath('.//w:p', namespaces=NS)
        paragraph_texts = [paragraph_text(paragraph) for paragraph in paragraphs]
        reference_indexes = (
            find_reference_paragraph_indexes(paragraph_texts) if references_only else set(range(len(paragraphs)))
        )

        results: list[TrackedReplacementResult] = []
        for rule in rules:
            result = TrackedReplacementResult(
                rule_id=rule.rule_id,
                old_text=rule.old_text,
                new_text=rule.new_text,
                applied=False,
                paragraph_indexes=[],
            )
            if references_only and not reference_indexes:
                result.reason = 'Раздел списка литературы не найден'
                results.append(result)
                continue

            if rule.target_paragraph_indexes is not None:
                candidates = [idx for idx in rule.target_paragraph_indexes if idx in reference_indexes]
            else:
                candidates = [idx for idx in range(len(paragraphs)) if idx in reference_indexes]
            next_change_id = apply_rule_to_candidates(
                rule,
                paragraphs,
                paragraph_texts,
                candidates,
                result,
                next_change_id=next_change_id,
                author=author,
                timestamp=timestamp,
            )
            if rule.target_paragraph_indexes is not None and result.replacements == 0:
                fallback_candidates = [idx for idx in range(len(paragraphs)) if idx in reference_indexes]
                next_change_id = apply_rule_to_candidates(
                    rule,
                    paragraphs,
                    paragraph_texts,
                    fallback_candidates,
                    result,
                    next_change_id=next_change_id,
                    author=author,
                    timestamp=timestamp,
                )

            result.applied = result.replacements > 0
            if not result.applied and result.reason is None:
                result.reason = 'Фрагмент не найден в разрешенной области документа'
            results.append(result)

        overrides = {
            'word/document.xml': xml_bytes(document_root),
            'word/settings.xml': xml_bytes(settings_root),
        }
        write_docx_with_overrides(zin, output_docx, overrides)

    return results


def apply_rule_to_candidates(
    rule: TrackedReplacementRule,
    paragraphs: list[etree._Element],
    paragraph_texts: list[str],
    candidates: list[int],
    result: TrackedReplacementResult,
    *,
    next_change_id: int,
    author: str,
    timestamp: str,
) -> int:
    for paragraph_index in candidates:
        if result.replacements >= rule.max_replacements:
            break

        paragraph = paragraphs[paragraph_index]
        text = paragraph_texts[paragraph_index]
        if normalize_match_text(text) == normalize_match_text(rule.old_text):
            next_change_id = replace_whole_paragraph(
                paragraph,
                old_text=text,
                new_text=rule.new_text,
                change_id=next_change_id,
                author=author,
                timestamp=timestamp,
            )
            result.replacements += 1
            result.paragraph_indexes.append(paragraph_index)
            continue

        if rule.old_text not in text:
            continue

        if text == rule.old_text:
            next_change_id = replace_whole_paragraph(
                paragraph,
                old_text=rule.old_text,
                new_text=rule.new_text,
                change_id=next_change_id,
                author=author,
                timestamp=timestamp,
            )
            result.replacements += 1
            result.paragraph_indexes.append(paragraph_index)
            continue

        next_change_id, changed = replace_in_single_text_node(
            paragraph,
            old_text=rule.old_text,
            new_text=rule.new_text,
            change_id=next_change_id,
            author=author,
            timestamp=timestamp,
        )
        if changed:
            result.replacements += 1
            result.paragraph_indexes.append(paragraph_index)
        else:
            result.reason = 'Фрагмент найден, но он разбит между несколькими run/w:t; нужна замена абзаца целиком'
    return next_change_id


def normalize_match_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()


def inspect_revision_markup(path_docx: Path) -> RevisionInspection:
    """Проверить наличие основных элементов Track Changes в DOCX."""
    with zipfile.ZipFile(path_docx, 'r') as zin:
        document_root = etree.fromstring(zin.read('word/document.xml'))
        settings_root = read_or_create_settings(zin)

    track_revisions_enabled = settings_root.find('w:trackRevisions', namespaces=NS) is not None
    insertions = len(document_root.xpath('.//w:ins', namespaces=NS))
    deletions = len(document_root.xpath('.//w:del', namespaces=NS))
    return RevisionInspection(track_revisions_enabled, insertions, deletions)


def read_or_create_settings(zin: zipfile.ZipFile) -> etree._Element:
    if 'word/settings.xml' in zin.namelist():
        return etree.fromstring(zin.read('word/settings.xml'))
    return etree.Element(qn('settings'), nsmap={'w': W_NS})


def enable_track_revisions(settings_root: etree._Element) -> None:
    if settings_root.find('w:trackRevisions', namespaces=NS) is None:
        settings_root.insert(0, etree.Element(qn('trackRevisions')))


def get_next_change_id(document_root: etree._Element) -> int:
    ids: list[int] = []
    for element in document_root.xpath('.//*[@w:id]', namespaces=NS):
        value = element.get(qn('id'))
        if value is None:
            continue
        try:
            ids.append(int(value))
        except ValueError:
            continue
    return max(ids, default=0) + 1


def paragraph_text(paragraph: etree._Element) -> str:
    return ''.join(node.text or '' for node in paragraph.xpath('.//w:t', namespaces=NS))


def find_reference_paragraph_indexes(texts: list[str]) -> set[int]:
    import re

    start_index: int | None = None
    end_index = len(texts)

    for index, text in enumerate(texts):
        if re.fullmatch(REFERENCE_TITLE_RE, text.strip(), flags=re.IGNORECASE):
            start_index = index + 1
            break

    if start_index is None:
        return set()

    for index in range(start_index, len(texts)):
        if re.match(REFERENCE_END_RE, texts[index].strip(), flags=re.IGNORECASE):
            end_index = index
            break

    return set(range(start_index, end_index))


def replace_whole_paragraph(
    paragraph: etree._Element,
    *,
    old_text: str,
    new_text: str,
    change_id: int,
    author: str,
    timestamp: str,
) -> int:
    ppr = paragraph.find('w:pPr', namespaces=NS)
    for child in list(paragraph):
        if child is ppr:
            continue
        paragraph.remove(child)

    paragraph.append(make_deleted_run(old_text, change_id, author, timestamp))
    paragraph.append(make_inserted_run(new_text, change_id + 1, author, timestamp))
    return change_id + 2


def replace_in_single_text_node(
    paragraph: etree._Element,
    *,
    old_text: str,
    new_text: str,
    change_id: int,
    author: str,
    timestamp: str,
) -> tuple[int, bool]:
    for text_node in paragraph.xpath('.//w:t', namespaces=NS):
        text = text_node.text or ''
        if old_text not in text:
            continue

        parent_run = text_node.getparent()
        run_parent = parent_run.getparent() if parent_run is not None else None
        if parent_run is None or run_parent is None:
            return change_id, False

        before, after = text.split(old_text, 1)
        rpr = parent_run.find('w:rPr', namespaces=NS)
        index = run_parent.index(parent_run)
        replacement_nodes = []

        if before:
            replacement_nodes.append(make_plain_run(before, rpr))
        replacement_nodes.append(make_deleted_run(old_text, change_id, author, timestamp, rpr))
        replacement_nodes.append(make_inserted_run(new_text, change_id + 1, author, timestamp, rpr))
        if after:
            replacement_nodes.append(make_plain_run(after, rpr))

        for node in reversed(replacement_nodes):
            run_parent.insert(index, node)
        run_parent.remove(parent_run)
        return change_id + 2, True

    return change_id, False


def make_plain_run(text: str, rpr: etree._Element | None = None) -> etree._Element:
    run = etree.Element(qn('r'))
    append_run_properties(run, rpr)
    text_node = etree.SubElement(run, qn('t'))
    text_node.set(f'{{{XML_NS}}}space', 'preserve')
    text_node.text = text
    return run


def make_deleted_run(
    text: str,
    change_id: int,
    author: str,
    timestamp: str,
    rpr: etree._Element | None = None,
) -> etree._Element:
    deleted = etree.Element(qn('del'))
    deleted.set(qn('id'), str(change_id))
    deleted.set(qn('author'), author)
    deleted.set(qn('date'), timestamp)

    run = etree.SubElement(deleted, qn('r'))
    append_run_properties(run, rpr)
    text_node = etree.SubElement(run, qn('delText'))
    text_node.set(f'{{{XML_NS}}}space', 'preserve')
    text_node.text = text
    return deleted


def make_inserted_run(
    text: str,
    change_id: int,
    author: str,
    timestamp: str,
    rpr: etree._Element | None = None,
) -> etree._Element:
    inserted = etree.Element(qn('ins'))
    inserted.set(qn('id'), str(change_id))
    inserted.set(qn('author'), author)
    inserted.set(qn('date'), timestamp)

    run = etree.SubElement(inserted, qn('r'))
    append_run_properties(run, rpr)
    text_node = etree.SubElement(run, qn('t'))
    text_node.set(f'{{{XML_NS}}}space', 'preserve')
    text_node.text = text
    return inserted


def append_run_properties(run: etree._Element, rpr: etree._Element | None) -> None:
    if rpr is not None:
        run.append(etree.fromstring(etree.tostring(rpr)))


def write_docx_with_overrides(
    zin: zipfile.ZipFile,
    output_docx: Path,
    overrides: dict[str, bytes],
) -> None:
    with zipfile.ZipFile(output_docx, 'w', zipfile.ZIP_DEFLATED) as zout:
        written = set()
        for item in zin.infolist():
            name = item.filename
            if name in overrides:
                zout.writestr(name, overrides[name])
                written.add(name)
            else:
                zout.writestr(item, zin.read(name))

        for name, content in overrides.items():
            if name not in written:
                zout.writestr(name, content)


def save_results(path_report: Path, results: list[TrackedReplacementResult], inspection: RevisionInspection) -> None:
    payload = {
        'inspection': asdict(inspection),
        'results': [asdict(result) for result in results],
    }
    path_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
