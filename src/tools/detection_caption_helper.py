import logging
import re
from dataclasses import dataclass
from functools import reduce
from typing import Callable, Literal

import pandas as pd
import pymupdf

from src.models.model_text_extraction import PyMuPDFModel
from src.structures import BoundingBox, ExtractedText, FoundedCaption, ImageInfo, PageElement, PageElementDetail
from src.tools import tools, utils

CAPTION_SEARCH_GAP = 3
CAPTION_BOX_PADDING = 10
CAPTION_DETECTION_OVERLAP_THRESHOLD = 0.8
FIGURE_CAPTION_MIN_LENGTH = 10
FIGURE_MAX_CANDIDATES = 3
FIGURE_MAX_CAPTION_DEPTH_RATIO = 0.25
FIGURE_NEXT_PAGE_THRESHOLD = 0.75

SearchDirection = Literal['above', 'below']

TABLE_PATTERNS = ['Продолжение таблицы', 'Таблица']
FIGURE_WORD = 'Рисунок'
FIGURE_WORD_LOWER = FIGURE_WORD.lower()
FIGURE_SHORT_VARIANTS = {'рис', 'рис.'}
LETTER_LABEL_RE = re.compile(r'\b[а-яa-zА-ЯA-Z]\)')
NEXT_LABEL_RE = re.compile(r'^\s*[а-яa-zA-Z]\)')
FIGURE_WORD_IN_TEXT_RE = re.compile(rf'(.*?)({FIGURE_WORD_LOWER} )', re.IGNORECASE)
SECTION_NUMBER_RE = re.compile(r'\d+|[A-ZА-Я]')
FIGURE_NUMBER_RE = re.compile(r'\d+')

TABLE_NO_TEXT_MESSAGE = 'Над таблицей не найден текст, который мог бы быть ее подписью'
TABLE_DETECTED_MISSED_MESSAGE = 'YOLO-модель не нашла подпись, начинаем ручной поиск над таблицей'
TABLE_NOT_FOUND_MESSAGE = 'Подпись не удалось найти непосредственно над таблицей'
TABLE_NUMBER_NOT_FOUND_MESSAGE = 'Не удалось найти номер таблицы в предполагаемой подписи'

FIGURE_NO_TEXT_MESSAGE = 'Под рисунком не найдено текстовых блоков'
FIGURE_NEXT_PAGE_CHECK_MESSAGE = 'На странице рисунка подпись не найдена, проверяем следующую страницу'
FIGURE_ON_PICTURE_CHECK_MESSAGE = 'Подпись рисунка не найдена, проверяем текст на самом рисунке'
FIGURE_ON_NEXT_PAGE_MESSAGE = 'Подпись к рисунку обнаружена на следующей странице'
FIGURE_ON_PICTURE_MESSAGE = 'Подпись к рисунку обнаружена на самом рисунке'
FIGURE_NOT_FOUND_MESSAGE = 'Подпись для рисунка не была найдена'
FIGURE_LABELS_MESSAGE = 'Найден блок с буквенными обозначениями: %s'
FIGURE_LABELS_AFTER_CAPTION_MESSAGE = (
    'Буквенные обозначения (а), б), ...) обнаружены после подписи, '
    'но до подписи их не было'
)


@dataclass(frozen=True)
class SearchContext:
    region: BoundingBox
    exclude_clips: list[BoundingBox]
    page_height: float


def merge_lines(lines: list[ExtractedText]) -> ExtractedText:
    if not lines:
        raise ValueError('Ожидался непустой список строк')

    return reduce(lambda prev, curr: prev | curr, lines)


def build_caption(
    page_number: int,
    lines: list[ExtractedText],
    section_number: str | None,
    number: str | None,
) -> FoundedCaption:
    caption_text = merge_lines(lines)
    return FoundedCaption(
        page_number=page_number,
        lines=tuple(lines),
        section_number=section_number,
        number=number,
        confidence=caption_text.confidence,
    )


def get_last_block(lines: list[ExtractedText]) -> tuple[ExtractedText | None, list[ExtractedText]]:
    if not lines:
        raise ValueError('Ожидался непустой список строк')

    block_number = lines[-1].block_num
    previous_line = None

    if block_number > 0:
        try:
            previous_line = next(line for line in reversed(lines) if line.block_num == block_number - 1)
        except StopIteration:
            previous_line = None

    block_lines = [line for line in lines if line.block_num == block_number]
    return previous_line, block_lines


def group_lines_to_paragraphs(
    lines: list[ExtractedText],
    distance_threshold: float = 10,
    left_delta_threshold: float = 30,
) -> list[list[ExtractedText]]:
    if not lines:
        return []

    paragraphs: list[list[ExtractedText]] = []
    current_paragraph = [lines[0]]

    for prev, curr in zip(lines, lines[1:]):
        vertical_gap = curr.bbox.top - prev.bbox.bottom
        paragraph_top = min(line.bbox.top for line in current_paragraph)
        paragraph_bottom = max(line.bbox.bottom for line in current_paragraph)
        paragraph_height = paragraph_bottom - paragraph_top

        is_same_paragraph = (
            (vertical_gap < distance_threshold or curr.bbox.top - prev.bbox.top < distance_threshold)
            and prev.bbox.left - curr.bbox.left < left_delta_threshold
            and curr.bbox.bottom - curr.bbox.top < paragraph_height * 2
        )

        if is_same_paragraph:
            current_paragraph.append(curr)
            continue

        paragraphs.append(current_paragraph)
        current_paragraph = [curr]

    paragraphs.append(current_paragraph)
    return paragraphs


def get_caption_candidates(
    lines: list[ExtractedText],
    logger: logging.Logger,
    detected_captions: list[PageElementDetail] | None = None,
    distance_threshold: float = 10,
) -> list[list[ExtractedText]]:
    if not detected_captions:
        return group_lines_to_paragraphs(lines, distance_threshold=distance_threshold)

    if not lines:
        return []

    detected_candidate = _get_detected_caption_candidate(lines, detected_captions)
    if detected_candidate is not None:
        return [detected_candidate]

    logger.info('Для извлеченного текста не нашлось подходящей детекции подписи')
    return group_lines_to_paragraphs(lines, distance_threshold=distance_threshold)


def extract_caption_from_regions(
    page_index: int,
    model: PyMuPDFModel,
    regions: list[BoundingBox],
    exclude_clips: list[BoundingBox],
    build_caption_from_lines: Callable[[list[ExtractedText]], FoundedCaption | None],
    logger: logging.Logger,
    empty_region_message: str,
) -> FoundedCaption | None:
    for region in regions:
        lines = model.extract_line_intervals(page_index, region, exclude_clips=exclude_clips)
        if not lines:
            logger.info(empty_region_message)
            continue

        if (caption := build_caption_from_lines(lines)) is not None:
            return caption

    return None


def _get_detected_caption_candidate(
    lines: list[ExtractedText],
    detected_captions: list[PageElementDetail],
) -> list[ExtractedText] | None:
    first_line = lines[0]
    for caption in detected_captions:
        caption_box = caption.box.extend(offset=CAPTION_BOX_PADDING)
        overlap_ratio = (first_line.bbox & caption_box).area / first_line.bbox.area
        if overlap_ratio <= CAPTION_DETECTION_OVERLAP_THRESHOLD:
            continue

        caption_lines = [first_line]
        for line in lines[1:]:
            next_overlap = (line.bbox & caption.box).area / line.bbox.area
            if next_overlap > CAPTION_DETECTION_OVERLAP_THRESHOLD:
                caption_lines.append(line)
            else:
                break

        return caption_lines

    return None


def _page_clip(page: pymupdf.Page) -> BoundingBox:
    return BoundingBox(top=0, left=0, bottom=page.rect.height, right=page.rect.width)


def _get_page_objects(
    df_objects: pd.DataFrame,
    page_index: int,
    search_region: BoundingBox,
) -> pd.DataFrame:
    page_elements = {PageElement.table.value, PageElement.picture.value, PageElement.figure.value}

    if df_objects.empty:
        return pd.DataFrame(columns=['img_num', 'element_type', 'img_name', 'top', 'left', 'bottom', 'right'])

    page_objects = df_objects[
        (df_objects['img_num'] == page_index) & (df_objects['element_type'].isin(page_elements))
    ]
    if page_objects.empty:
        return page_objects

    return page_objects[
        (page_objects['top'] > search_region.top) & (page_objects['bottom'] < search_region.bottom)
    ]


def _build_search_region(
    page: pymupdf.Page,
    element_info: PageElementDetail,
    direction: SearchDirection,
) -> BoundingBox:
    search_region = _page_clip(page)

    if direction == 'above':
        search_region.bottom = element_info.box.top
    else:
        search_region.top = element_info.box.bottom + CAPTION_SEARCH_GAP

    return search_region


def _prepare_search_context(
    img_info: ImageInfo,
    element_info: PageElementDetail,
    df_objects: pd.DataFrame,
    direction: SearchDirection,
) -> SearchContext:
    with pymupdf.Document(img_info.path_pdf) as doc:
        page = doc[element_info.page_index]
        search_region = _build_search_region(page, element_info, direction)
        page_objects = _get_page_objects(df_objects, element_info.page_index, search_region)
        page_box = (page.rect.height, page.rect.width)
        exclude_bboxes, exclude_tables_bboxes = utils.generate_exclude_bboxes(
            element_info.page_index,
            page_objects,
            page_box,
            page=page,
            clip=search_region,
        )

        return SearchContext(
            region=search_region,
            exclude_clips=exclude_bboxes + exclude_tables_bboxes,
            page_height=page.rect.height,
        )


def _get_detected_caption_regions(
    search_region: BoundingBox,
    element_info: PageElementDetail,
    captions_on_page: list[PageElementDetail],
    direction: SearchDirection,
) -> list[BoundingBox]:
    if direction == 'above':
        sorted_captions = sorted(
            filter(lambda caption: caption.box.top < element_info.box.top, captions_on_page),
            key=lambda caption: -caption.box.top,
        )
    else:
        sorted_captions = sorted(
            filter(lambda caption: caption.box.bottom > element_info.box.bottom, captions_on_page),
            key=lambda caption: caption.box.top,
        )

    return [
        BoundingBox(caption.box.top, search_region.left, caption.box.bottom, search_region.right)
        for caption in sorted_captions
    ]


def _parse_table_caption(
    page_index: int,
    lines: list[ExtractedText],
    pattern_match_cutoff: float,
    logger: logging.Logger,
) -> FoundedCaption | None:
    previous_line, block_lines = get_last_block(lines)
    caption_text = merge_lines(block_lines)
    section_number, number = tools.extract_pattern_id(caption_text.text, TABLE_PATTERNS, pattern_match_cutoff)

    if not (section_number or number):
        logger.warning(TABLE_NUMBER_NOT_FOUND_MESSAGE, extra={'bbox': caption_text.bbox})
        return None

    if previous_line is not None and caption_text.bbox.top < previous_line.bbox.bottom:
        first_line = block_lines[0]
        block_lines[0] = ExtractedText(
            page_num=first_line.page_num,
            block_num=first_line.block_num,
            bbox=BoundingBox(
                top=previous_line.bbox.bottom,
                left=first_line.bbox.left,
                bottom=first_line.bbox.bottom,
                right=first_line.bbox.right,
            ),
            text=first_line.text,
            confidence=first_line.confidence,
            meta_info=first_line.meta_info,
            line_interval=first_line.line_interval,
        )

    return build_caption(page_index, block_lines, section_number, number)


def _contains_letter_labels(text: str) -> bool:
    return bool(LETTER_LABEL_RE.search(text))


def _normalize_figure_caption_text(
    caption_text: ExtractedText,
    logger: logging.Logger,
) -> tuple[ExtractedText, bool]:
    has_letter_labels = False
    if not _contains_letter_labels(caption_text.text):
        return caption_text, has_letter_labels

    logger.info(FIGURE_LABELS_MESSAGE, caption_text.text)
    has_letter_labels = True
    if FIGURE_WORD_LOWER not in caption_text.text.lower():
        return caption_text, has_letter_labels

    start = caption_text.text.lower().find(FIGURE_WORD_LOWER)
    caption_text.text = caption_text.text[start:]
    return caption_text, has_letter_labels


def _warn_if_labels_continue_after_caption(
    paragraphs: list[list[ExtractedText]],
    index: int,
    has_letter_labels: bool,
    founded_caption: FoundedCaption,
    logger: logging.Logger,
) -> None:
    if has_letter_labels or index + 1 >= len(paragraphs):
        return

    next_paragraph_text = merge_lines(paragraphs[index + 1])
    if NEXT_LABEL_RE.match(next_paragraph_text.text.strip()):
        logger.warning(FIGURE_LABELS_AFTER_CAPTION_MESSAGE, extra={'bbox': founded_caption.box})


def _extract_figure_numbers(
    caption_text: ExtractedText,
    logger: logging.Logger,
) -> tuple[str | None, str | None]:
    text = caption_text.text.strip()

    if (match := FIGURE_WORD_IN_TEXT_RE.search(text)) is not None:
        figure_start = match.start(2)
        if figure_start > 0:
            logger.error(
                'Слово "Рисунок" должно быть первым словом в подписи. '
                'Перед ним найден текст "%s"',
                match.group(1),
                extra={'bbox': caption_text.bbox},
            )
            text = text[figure_start:]

    tokens = text.split()
    if not tokens:
        return None, None

    first_token = tokens[0]
    first_token_lower = first_token.lower()

    if first_token != FIGURE_WORD and first_token_lower == FIGURE_WORD_LOWER:
        logger.error(
            'Слово "Рисунок" в подписи к изображению начинается с маленькой буквы',
            extra={'bbox': caption_text.bbox},
        )

    if first_token_lower in FIGURE_SHORT_VARIANTS:
        logger.error(
            'Слово "Рисунок" в подписи к изображению не должно быть сокращено',
            extra={'bbox': caption_text.bbox},
        )

    if first_token_lower not in {FIGURE_WORD_LOWER, *FIGURE_SHORT_VARIANTS}:
        return None, None

    if len(tokens) <= 1:
        logger.warning(
            'После слова "Рисунок" в подписи к изображению не найдена нумерация, '
            'например "Рисунок 1 - ..." или "Рисунок 1.1 - ..."',
            extra={'bbox': caption_text.bbox},
        )
        return None, None

    number_parts = [part.strip() for part in tokens[1].strip().split('.')]
    if len(number_parts) == 2 and number_parts[1] == '':
        logger.warning(
            'В подписи обнаружен лишний пробел после разделяющей точки '
            '(например, "Рисунок А. 1", ожидается "Рисунок А.1").'
        )
        if len(tokens) >= 3 and tokens[2].isdigit():
            number_parts[1] = tokens[2]

    number_parts = [part for part in number_parts if part]
    if not number_parts:
        return None, None

    if len(number_parts) > 1:
        section_number = number_parts[0]
        figure_number = number_parts[-1]
    else:
        section_number = None
        figure_number = number_parts[0]

    if section_number and SECTION_NUMBER_RE.match(section_number) is None:
        logger.warning(
            'Номер раздела в подписи рисунка некорректен: допускается номер раздела '
            '(1.1) или название приложения (A.1). Найдено: "%s"',
            section_number,
        )
        section_number = None

    if FIGURE_NUMBER_RE.match(figure_number) is None:
        logger.warning(
            'Номер рисунка некорректен: допускаются только числа, найдено: "%s"',
            figure_number,
        )
        return section_number, None

    return section_number, figure_number


def _parse_figure_caption_candidates(
    page_index: int,
    lines: list[ExtractedText],
    top_boundary: float,
    page_height: float,
    logger: logging.Logger,
    detected_captions: list[PageElementDetail] | None = None,
) -> FoundedCaption | None:
    paragraphs = get_caption_candidates(lines, logger=logger, detected_captions=detected_captions)

    max_caption_depth = page_height * FIGURE_MAX_CAPTION_DEPTH_RATIO
    checked_candidates = 0

    for index, caption_lines in enumerate(paragraphs):
        if checked_candidates >= FIGURE_MAX_CANDIDATES:
            break

        caption_text = merge_lines(caption_lines)
        if len(caption_text.text.strip()) <= FIGURE_CAPTION_MIN_LENGTH:
            continue

        if caption_text.bbox.top - top_boundary > max_caption_depth:
            break

        caption_text, has_letter_labels = _normalize_figure_caption_text(caption_text, logger)

        section_number, figure_number = _extract_figure_numbers(caption_text, logger=logger)
        if figure_number is None:
            checked_candidates += 1
            continue

        founded_caption = build_caption(page_index, caption_lines, section_number, figure_number)
        _warn_if_labels_continue_after_caption(paragraphs, index, has_letter_labels, founded_caption, logger)
        return founded_caption

    return None


def _check_figure_caption_outside_search_region(
    img_info: ImageInfo,
    element_info: PageElementDetail,
    page_height: float,
    logger: logging.Logger,
    check_next_page: bool,
) -> FoundedCaption | None:
    with PyMuPDFModel(img_info.path_pdf) as model:
        page = model.doc[img_info.img_index]
        picture_clip = BoundingBox(
            top=element_info.box.top + CAPTION_BOX_PADDING,
            left=0,
            bottom=page.rect.height,
            right=page.rect.width,
        )
        lines_on_picture = model.extract_line_intervals(img_info.img_index, clip=picture_clip)

        next_page_index = img_info.img_index + 1
        can_check_next_page = check_next_page and next_page_index < len(model.doc)
        page_lines = []
        if can_check_next_page:
            next_page = model.doc[next_page_index]
            page_lines = model.extract_line_intervals(next_page_index, clip=_page_clip(next_page))

    logger.info(FIGURE_ON_PICTURE_CHECK_MESSAGE)
    checks = [
        (
            lines_on_picture,
            img_info.img_index,
            element_info.box.top + CAPTION_BOX_PADDING,
            FIGURE_ON_PICTURE_MESSAGE,
            logger.warning,
        )
    ]

    if can_check_next_page:
        logger.info(FIGURE_NEXT_PAGE_CHECK_MESSAGE)
        checks.append((page_lines, next_page_index, 0, FIGURE_ON_NEXT_PAGE_MESSAGE, logger.error))

    for lines, page_index, top_boundary, found_message, log_message in checks:
        if not lines:
            continue

        if (
            caption := _parse_figure_caption_candidates(
                page_index=page_index,
                lines=lines,
                top_boundary=top_boundary,
                page_height=page_height,
                logger=logger,
            )
        ) is not None:
            log_message(found_message, extra={'bbox': element_info.box})
            return caption

    return None
