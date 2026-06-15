import json
import re
import math
import difflib

import numpy as np
import pandas as pd
from pathlib import Path
from pymupdf import Page, Rect

from src.protocols import AnnotatedPage
from src.structures import BoundingBox, FoundedReference, FoundedCaption, PageElementDetail
from src.constants import W_H_RATIO_A4, LEFT_MARGIN_MM, LEFT_OFFSET_MM, PT_TO_MM


def get_img_index(img_name: str) -> int:
    """get index from name, for example "page-1" -> 1"""
    match = re.match(r'page-(\d+)$', img_name)

    if match is None:
        raise ValueError(f'Error with img_name formalization, must be "page-0". Got {img_name}')

    return int(match.group(1))


def get_pdf_page_symbol_size(page: AnnotatedPage, sym: str = ' ') -> float:
    char_widths = []

    rawdict = page.get_text('rawdict')
    for block in rawdict['blocks']:
        for line in (block.get('lines') or []):
            for span in (line.get('spans') or []):
                for c in (span.get('chars') or []):
                    w = c['bbox'][2] - c['bbox'][0]  # ширина символа
                    char_widths.append((c['c'], w))

    selected_char_widths = list(map(lambda v: v[1], filter(lambda v: v[0] == sym, char_widths)))
    return float(np.percentile(selected_char_widths, 50))  # 3 must be in most cases


def read_content(file_path: Path, offset: int | None = None) -> dict[str, int]:
    """Открыть и подготовить содержание документа, которое было получено в отдельном узле"""

    if not file_path.exists():
        raise FileNotFoundError(f'Файл "{file_path}" с содержанием не был найден')

    with open(file_path, 'r') as f:
        content_source: dict[str, dict[str, int | str]] = json.load(f)
        content: dict[str, int] = dict(map(lambda kv: (kv[0], kv[1]['page_number']), content_source.items()))

    if offset is not None:
        # Обычно в содержании номер страницы на 1 больше, чем индекс в документе (тк начинается с 1 а не с 0)
        for key in content:
            content[key] = content[key] + offset

    return content


def read_elements(file_path: Path, page_elements: set[str] = None) -> pd.DataFrame:
    """
    Открыть и вернуть DataFrame с таблицами, рисунками или чем-то другим для страниц документа

    :param file_path: Путь к файлу FILE_DOC_ELEMENTS (*.csv).
    :type file_path: str
    :param page_elements: Имена элементов, которые нужно вернуть например {PageElement.figure.value, PageElement.table.value}. По умолчанию None.
    :type page_elements: set[str]

    :return: DataFrame с таблицами, рисунками или чем-то другим для страниц документа.
    """
    if not file_path.exists():
        raise FileNotFoundError(
            f'Файл "{file_path}" с найденными элементами (таблицами, рисунками и проч.) не был найден'
        )

    df_elements = pd.read_csv(file_path)

    if 'element_type' not in df_elements.columns:
        raise KeyError('Не найден обязательный столбец "element_type" с типами найденных элементов')

    if 'img_name' not in df_elements.columns:
        raise KeyError('Не найден обязательный столбец "img_name" с именем страницы (i.g. page-1)')

    if 'top' not in df_elements.columns:
        raise KeyError('Не найден обязательный столбец "top" с верхней координатой найденных элементов')

    df_elements = df_elements[df_elements['element_type'].isin(page_elements)] if page_elements else df_elements

    if df_elements.empty:
        return df_elements

    df_elements['img_num'] = df_elements['img_name'].apply(get_img_index)
    df_elements = df_elements.sort_values(['img_num', 'top'])

    return df_elements


def read_elements_to_dict(file_path: Path, page_elements: set[str] = None) -> dict[int, list[PageElementDetail]]:
    """Открыть и вернуть DataFrame с таблицами, рисунками или чем-то другим для страниц документа"""

    df_elements = read_elements(file_path, page_elements=page_elements)

    res = {}
    for index, row in df_elements.iterrows():
        el: PageElementDetail = PageElementDetail.from_series(row)

        res.setdefault(el.page_index, []).append(el)

    return res


def get_mode(array: list[int | float]) -> int | float:
    """
    Возвращает элемент списка, который соответствует максимальной моде.

    :param array: Список со значениями.
    :type array: list

    :return: Элемент списка, который соответствует максимальной моде.
    :rtype: Optional
    """
    return max(set(array), key=array.count)


def scale_bbox_by_shapes(source_shape: list[int], dest_shape: list[int], bbox: BoundingBox) -> BoundingBox:
    """
    Функция переводит ббокс из одного масштаба в другой относительно переданных форм.

    :param source_shape: Размеры из которых нужно масштабировать (ширина, высота).
    :type source_shape: list[int]
    :param dest_shape: Размеры в масштабы которых нужно переводить (ширина, высота).
    :type dest_shape: list[int]
    :param bbox: Ббокс, который нужно масштабировать.
    :type bbox: BoundingBox

    :return: Масштабируемый ббокс.
    :rtype: BoundingBox
    """
    w_coef = dest_shape[0] / source_shape[0]
    h_coef = dest_shape[1] / source_shape[1]
    return BoundingBox(
        int(bbox.top * h_coef), int(bbox.left * w_coef), math.ceil(bbox.bottom * h_coef), math.ceil(bbox.right * w_coef)
    )


def extract_pattern_id(
        text: str,
        patterns: list[str],
        toi_cutoff: float = 0.7
) -> tuple[str | None, str | None]:
    # Разбиваем на токены переданную строку
    tokens = text.split()

    # Ищем в ней соответствие с паттернами
    section_number, number = None, None
    for pattern in patterns:
        tokens_n = len(pattern.split())
        # Если паттерн начинается с прописной буквы, то берем только начало строки
        tokens_combinations = [" ".join(tokens[0:tokens_n])] if pattern[0].isupper() else [" ".join(tokens[i:i + tokens_n]) for i in range(len(tokens) - tokens_n + 1)]
        # Если есть совпадение, то обновляем паттерн согласно тому, как написано в тексте и извлекаем id
        if pattern := difflib.get_close_matches(word=pattern, possibilities=tokens_combinations, n=1, cutoff=toi_cutoff):
            # Обрезаем текст
            if cropped_text := text.strip(pattern[0]).strip():
                # Ищем id
                if pattern_id := re.search(r"[\.\dA-ZА-ЯЁ][\s\.\d]*", cropped_text):
                    pattern_id = re.sub(r"\s", "", pattern_id[0])
                    pattern_id_nums = [number for number in pattern_id.split(".") if number]

                    if len(pattern_id_nums) == 1:
                        section_number, number = (pattern_id_nums[0], None) if pattern_id[-1] == "." else (None, pattern_id_nums[0])
                        break
                    elif len(pattern_id_nums) > 1:
                        section_number, number = (None, ".".join(pattern_id_nums)) if pattern_id[0] == "." else (pattern_id_nums[0], ".".join(pattern_id_nums[1:]))
                        break

    return section_number, number


def is_a4(checked_shape: list[int]) -> bool:
    """
    Функция для проверки соответствия размерам страницы формату А4.

    :param checked_shape: Размеры страницы (ширина, высота).
    :return: True/False в зависимости от соответствия формату листа A4.
    """
    w_h_ratio = checked_shape[0] / checked_shape[1]
    return abs(w_h_ratio - W_H_RATIO_A4) < 0.02


def is_bbox_start_from_start_page(bbox: BoundingBox) -> bool:
    left_mm = bbox.left / PT_TO_MM  # Переводим в мм
    return abs(left_mm - LEFT_MARGIN_MM) < 1  # 30 мм слева + 1 погрешность


def is_bbox_start_from_section_offset(bbox: BoundingBox):
    left_mm = bbox.left / PT_TO_MM  # Переводим в мм
    return abs(left_mm - (LEFT_MARGIN_MM + LEFT_OFFSET_MM)) < 1  # 30 + 12.5 мм слева + 2 погрешность


def get_reference_by_caption(references: list[FoundedReference] | None,
                             caption: FoundedCaption | None) -> list[FoundedReference]:
        founded_references: list[FoundedReference] = []
        pattern = 'Продолжение таблицы'
        if references and caption and not difflib.get_close_matches(word=pattern, possibilities=[caption.text[:len(pattern)]], n=1, cutoff=0.8):
            for reference in references:
                if reference.section_number == caption.section_number and reference.number == caption.number:
                    founded_references.append(reference)
        
        return founded_references
                

def cut_by_tables(page, clip: BoundingBox, exclude_clips: list[BoundingBox] | None = None) -> list[BoundingBox]:
    """
    Функция, которая разбивает страницу на части, исключая области с таблицами.

    :param page: Страница документа.
    :type page: Page
    :param clip: Область, в которой нужно разбить страницу. Default is None.
    :type clip: Rect

    :return: Список с областями, где нет таблиц.
    :rtype: list[Rect]
    """
    top, left, bottom, right = clip.summary

    # Если есть рисунок или таблица, нам нужно не забыть абзац после нее и до конца страницы
    exclude_clips += [BoundingBox(top=bottom, left=left, right=right, bottom=bottom + 1)]

    output = []
    for exclude_clip in exclude_clips:
        if exclude_clip.bottom < top or exclude_clip.top > bottom:
            continue  # объект находится вне зоне поиска

        bbox = BoundingBox(top=top, left=left, right=right, bottom=exclude_clip.top)

        if bbox.height > 1:
            output.append(bbox)

        top = exclude_clip.bottom

    if bottom - top > 1:
        # вырезали последний объект, и возможно еще область ниже объекта осталась в допустимой области
        output.append(BoundingBox(top=top, left=left, right=right, bottom=bottom))

    return output