from __future__ import annotations

import logging

import math
from typing import Any, Optional

from src.models.abstract_text_extraction import AbstractTextExtraction
from src.structures import ExtractedText, BoundingBox, PageExtractedTextList, TextLineInterval
from src.constants import SHAPE_A4_MM, PT, PT_TO_MM, LEFT_OFFSET_MM
from src.tools.tools import cut_by_tables, is_bbox_start_from_section_offset

logger = logging.getLogger(__name__)


def is_same_line(info: ExtractedText, info_next: ExtractedText) -> bool:
    # Если расстояние между базовыми линиями строк меньше 2px, то считаем, что это одна строка
    if info.meta_info is None or info_next.meta_info is None:
        raise AssertionError('Impossible to find the same line because meta is None')

    if info_next.meta_info.size < info.meta_info.size:
        # Символ формулы находится внутри строки (ниже top, но выше baseline)
        is_inside = info_next.bbox.top < info.bbox.top and info_next.meta_info.baseline_y < info.meta_info.baseline_y
        return is_inside or abs(info.meta_info.baseline_y - info_next.meta_info.baseline_y) < 5.5

    if abs(info_next.bbox.top - info.bbox.top) < 1 and info_next.meta_info.baseline_y < info.meta_info.baseline_y:
        return True

    return abs(info.meta_info.baseline_y - info_next.meta_info.baseline_y) < 2


def is_center_location(margins: BoundingBox, bbox: BoundingBox, offset: bool = False) -> bool:
    """

    :param margins:
    :param bbox:
    :param offset: Учитывать ли абзацный отступ, ибо это наиболее частая ошибка
    :return:
    """
    left_dist = bbox.left - margins.left
    right_dist = margins.right - bbox.right
    if abs(left_dist) < 0.5 or abs(right_dist) < 0.5:
        return False  # Выравнены по ширине, у левого может быть отступ, по этому правого хватает

    is_center = abs(left_dist - right_dist) <= 12
    if not is_center and offset:
        left_dist_offset = (bbox.left - LEFT_OFFSET_MM * PT_TO_MM) - margins.left
        is_center = abs(left_dist_offset - right_dist) <= 12

    return is_center


def px_to_line_spacing(dist_y_px: float, page_h_px: float, size_pt: float):
    # Коэфицент для перевода пикселей в мм
    q = SHAPE_A4_MM[1] / page_h_px

    # Перевеодим пиксели в мм
    dist_y_mm = dist_y_px * q

    # Возвращаем интервал (отношение расстояния между базовыми линиями и размера шрифта)
    return round(dist_y_mm / (size_pt * PT), 3)


def get_spacing(line: ExtractedText, next_line: ExtractedText, height: int) -> tuple[float, float]:
    dist = next_line.meta_info.baseline_y_list[0] - line.meta_info.baseline_y
    font_size = line.meta_info.size
    line_size = font_size * line.meta_info.ascender + abs(font_size*line.meta_info.descender)

    spacing: float = px_to_line_spacing(dist, height, font_size)
    add_spacing = px_to_line_spacing(dist, height, line_size)
    return spacing, add_spacing


def check_spacing(s_spacings: tuple[float], d_spacing: float):
    return (d_spacing - 0.05 < s_spacings[0] < d_spacing + 0.05) or (d_spacing - 0.05 < s_spacings[1] < d_spacing + 0.05)


# TODO: Переписать функцию, чтобы учитывать формат формул
def is_same_block(
    page_index: int,
    margins: BoundingBox,
    info: ExtractedText,
    info_next: ExtractedText
) -> bool:
    # Если следующий блок - подпись таблицы - то это новый блок
    is_new_figure = info_next.text.lower().startswith('рисунок ')  # для рисунка новый абзац по центру
    is_new_block = is_new_figure | info_next.text.lower().startswith(('продолжение таблицы', 'таблица '))
    if is_new_block:
        return False

    # Если отступ по горизонтали больше 5 миллиметров - то точно новый блок
    if (info_next.bbox.top - info.bbox.bottom) / PT_TO_MM > 5:
        # Для межстрочного интервала 12-м шрифтом это 2-3 миллиметра
        return False

    # Блок с проверкой если предыдущий выровнен по центру (для рисунка допустимо смещение на абзацный отступ)
    prev_is_center = is_center_location(margins, info.bbox, offset=True if is_new_figure else False)
    if prev_is_center:
        return is_center_location(margins, info_next.bbox)  # если следующий тоже по центру

    # Следующий с абзацного отступа
    if is_bbox_start_from_section_offset(info_next.bbox):
        return False

    is_same = abs((info.bbox.left - info_next.bbox.left) / PT_TO_MM) < 0.1  # новый на одной линии со старым
    is_same |= abs((info_next.bbox.left - margins.left) / PT_TO_MM) < 0.1  # новый начинается без отступа слева
    if is_same:
        return True

    # Справа info не на всю ширину (конец абзаца скорее всего)
    right_mm = margins.right * PT_TO_MM
    prev_right_mm = info.bbox.right * PT_TO_MM
    if right_mm - prev_right_mm > 5:  # 5 - на вскидку :(
        # Старый абзац закончился до конца страницы. Либо окончание абзаца, либо не выравненно по ширине страницы
        return info_next.text[0].islower()  # Если нижний регистр - то точно продолжение абзаца

    dist_left_mm = (info.bbox.left - info_next.bbox.left) / PT_TO_MM
    if dist_left_mm > 5:  # новый левее старого (абзацного):
        return True  # После абзаца

    return False


class PyMuPDFModel(AbstractTextExtraction):
    def extract_text(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
    ) -> list[ExtractedText]:
        page = self.doc[page_index]
        page_shape = BoundingBox(0, 0, page.rect[3], page.rect[2])

        if clip is None:
            clip = page_shape

        clips = [page_shape]
        if exclude_clips:
            # Исключаем области, где есть таблицы
            clips = cut_by_tables(page, clip, exclude_clips)

        # Извлекаем текст из областей
        blocks: list[ExtractedText] = []
        for clip in clips:
            page_text = page.get_text(option='dict', clip=clip.rectangle_for_pymupdf, sort=sort)

            info = None

            # Извлекаем текст и ббоксы из абзацев
            for block in page_text.get('blocks', []):
                if info_next := ExtractedText.from_block(page_index, 0, block):
                    info = info | info_next if info else info_next

            if info:
                blocks.append(info)

        return blocks

    def extract_paragraphs(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
    ) -> PageExtractedTextList:
        lines: list[ExtractedText] = self.extract_lines(page_index, clip, sort=sort, exclude_clips=exclude_clips)

        if not lines:
            return PageExtractedTextList()

        blocks: PageExtractedTextList = PageExtractedTextList()

        page = self.doc[page_index]

        block: ExtractedText = ExtractedText(
            page_num=page.number, block_num=lines[0].block_num, bbox=lines[0].bbox, text=lines[0].text
        )
        for line in lines[1:]:
            block_next: ExtractedText = ExtractedText(
                page_num=page.number, block_num=line.block_num, bbox=line.bbox, text=line.text
            )

            if block.block_num == block_next.block_num:
                block = block | block_next
            else:
                blocks.append(block)
                block = block_next

        blocks.append(block)  # Последний в цикле точно не добавится, по этому отдельно добавляем

        return blocks

    def determine_blocks(
        self,
        page_index: int,
        margins: BoundingBox,
        lines: list[ExtractedText]
    ) -> list[ExtractedText]:
        if not lines:
            return []

        block_number: int = 0
        line: ExtractedText = lines[0]
        for line_next in lines[1:]:
            if is_same_block(page_index, margins, line, line_next):
                line_next.block_num = block_number

            else:
                block_number += 1  # Начался новый блок
                line_next.block_num = block_number

            line = line_next

        return lines

    def extract_lines(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
        lines_max: int | None = None,
    ) -> list[ExtractedText]:
        page = self.doc[page_index]

        clip = clip if clip else BoundingBox.from_pymupdf_box(page.rect)

        bbox_media: Optional[BoundingBox] = self.get_page_content_bbox(page_index)
        if not bbox_media:
            logger.warning('Не найден текст на странице')
            return []

        clips = [clip]
        if exclude_clips:
            clips = cut_by_tables(page, clip, exclude_clips)

        lines: list[ExtractedText] = []

        block_number: int = 0
        line_number: int = 0

        # Для извлечения подчеркиваний (2 - наугад, надо будет потестить)
        drawings: list[dict] = page.get_drawings()
        drawings = list(filter(lambda v: BoundingBox.from_pymupdf_box(v['rect']).height < 2.0, drawings))

        info: Optional[ExtractedText] = None

        max_lines_reached: bool = False
        line_number: int = 0

        for clip in clips:
            page_text = page.get_text(option='dict', clip=clip.rectangle_for_pymupdf, sort=sort)

            if max_lines_reached:
                logger.info(f'На странице {page.number} достигли необходимое кол-во строк {lines_max}')
                break  # Если мы получили нужное кол-во строк - выходим

            for block in page_text['blocks']:
                if 'lines' not in block:
                    logger.info(f'На странице {page.number} не найдены "lines" для блока')
                    continue

                if max_lines_reached:
                    logger.info(f'На странице {page.number} достигли необходимое кол-во строк {lines_max}')
                    break  # Если мы получили нужное кол-во строк - выходим

                for line in block['lines']:
                    spans = line['spans']
                    if not spans:
                        continue

                    if max_lines_reached:
                        logger.info(f'На странице {page.number} достигли необходимое кол-во строк {lines_max}')
                        break  # Если мы получили нужное кол-во строк - выходим

                    for span in spans:
                        info_next: ExtractedText = ExtractedText.from_span(
                            page_index,
                            block_number,
                            span,
                            drawings=drawings
                        )
                        if not info_next:
                            continue

                        if info is None:  # Первый вообще элемент с текстом на странице
                            info = info_next
                        elif is_same_line(info, info_next):
                            info = info | info_next
                        else:
                            lines.append(info)

                            if lines_max is not None and len(lines) >= lines_max:
                                max_lines_reached = True

                            line_number += 1  # Началась новая строка
                            info = info_next

            # После всех циклов, последний info не добавился. По этому его нужно отдельно добавить
            if info is not None:
                info.block_num = block_number
                lines.append(info)

                block_number += 1
                line_number = 0

            info: Optional[ExtractedText] = None

        if sort:
            lines = sorted(lines, key=lambda v: v.bbox.top)

        return self.determine_blocks(page_index, bbox_media, lines)

    def extract_line_intervals(
        self,
        page_index: int,
        clip: Optional[BoundingBox] = None,
        exclude_clips: list[BoundingBox] | None = None,
        sort: bool = True,
    ) -> list[ExtractedText]:
        """
        Extract intervals between lines of text in a page.

        The intervals are calculated as the vertical distance between the
        baselines of two consecutive lines of text, divided by the height of
        a single line of text. The intervals are rounded to one decimal place.

        :param page_index: Page index from 0
        :param clip: Rect or None, If given, only consider text within this rectangle
        :param exclude_clips: The regions of the page to exclude for extraction (tables, figures, ...)
        :param sort: bool, If True (default), sort the extracted lines by their y-coordinate.

        :return: List of intervals between lines of text.
        """
        # Берём страницу и находим её высоту
        page = self.get_page(page_index)
        height = math.ceil(page.rect[-1])

        # Извлекаем строки текста
        lines: list[ExtractedText] = self.extract_lines(page_index, clip, exclude_clips, sort=sort)

        # Если на странице 1 строка, то считать интервал нет смысла
        if len(lines) <= 1:
            return lines

        # Считаем интервалы между строк
        line_n = 0
        line = lines[0]
        for next_line in lines[1:]:
            # Если нет метаданных, то посчитать интервал не получится
            if line.meta_info is None or next_line.meta_info is None:
                raise ValueError('У линии не найдена мета информация')

            # Считаем интервалы по базовым линиям и размеру шрифта
            spacing, add_spacing = get_spacing(line, next_line, height)

            # Находим номер следующей строки в абзаце
            next_line_n = line_n + 1 if line.block_num == next_line.block_num else 0

            # Если абзацы не идут следом, то находить интервал между ними не нужно
            if spacing > 3:
                # Обновляем исследуемые строки
                line_n = next_line_n
                line = next_line
                continue

            # Записываем интервал
            interval = TextLineInterval(
                line.block_num,
                next_line.block_num,
                line_n, next_line_n,
                line.bbox, next_line.bbox,
                spacing * 0.86701,  # Нормализующий коэффициент для 12-го шрифта
                add_spacing
            )
            line.line_interval = interval

            # Обновляем исследуемые строки
            line_n = next_line_n
            line = next_line

        return lines

    @classmethod
    def _px_to_line_spacing(cls, dist_y_px: float, page_h_px: float, font_size_pt: float):
        # Коэфицент для перевода пикселей в мм
        q = cls.A4_HEIGHT / page_h_px

        # Перевеодим пиксели в мм
        dist_y_mm = dist_y_px * q

        # Возвращаем интервал (отношение расстояния между базовыми линиями и размера шрифта)
        return round(dist_y_mm / (font_size_pt * cls.PT), 3)

    @staticmethod
    def _get_rect_of_h_fontsize(line: Any) -> BoundingBox:
        box = line.box
        box.bottom = line.baseline_y - line.size * line.descender / (line.ascender - line.descender)
        box.top = box.bottom - line.size  # box now is a rectangle of height 'fontsize'
        return box
