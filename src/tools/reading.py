import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.structures import ImageInfo, Paths, PageClass, ContentElement, ExtractedText
from src.tools.tools import get_img_index

logger = logging.getLogger(__name__)


def read_content(file_path: Path, offset: int | None = None) -> dict[str, int]:
    """Открыть и подготовить содержание документа, которое было получено в отдельном узле"""

    if not file_path.exists():
        raise FileNotFoundError(f'Файл "{file_path}" с содержанием не был найден')

    with open(file_path, 'r') as f:
        content_source: list[dict[str, int | str]] = json.load(f)

    iter_filtered_none = filter(lambda v: v['page_number'], content_source)
    content: dict[str, int] = dict(map(lambda v: (v['section_number'], v['page_number']), iter_filtered_none))
    if offset is not None:
        # Обычно в содержании номер страницы на 1 больше, чем индекс в документе (тк начинается с 1 а не с 0)
        for key in content:
            content[key] = content[key] + offset

    return content


def read_content_file(path_content: Path) -> list[ContentElement]:
    """Прочитать файл с распознанным содержанием pdf документа"""
    logger.info('Read file content %s', str(path_content))

    if not path_content.exists():
        raise ValueError(f'Файл с распознанным содержанием не существует по пути {path_content}')

    with open(path_content, 'r') as f:
        data = json.load(f)

    result: list[ContentElement] = []
    for value in data:
        value['lines'] = [ExtractedText.from_dict(v) for v in value['lines']]
        result.append(ContentElement(**value))

    return result


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


def read_pdf_structure(path_structure: Path) -> pd.DataFrame:
    """Прочитать файл со структурой документа"""
    logger.info('Read file structure %s', str(path_structure))

    if not path_structure.exists():
        raise ValueError(f'Файл с распознанной структурой pdf документа не существует по пути {path_structure}')

    return pd.read_csv(path_structure)


def read_pdf_structure_dict(path: Path, include: set[PageClass] | None = None) -> dict[int, PageClass]:
    df_page_classes: pd.DataFrame = read_pdf_structure(path)

    res: dict[int, PageClass] = {}
    for name, class_str in zip(df_page_classes['page'], df_page_classes['page_type']):
        cls_ = PageClass[class_str]
        if include is None or cls_ in include:
            res[name] = PageClass[class_str]

    return res


def load_npy(paths_object: Paths, img_index: int) -> ImageInfo:
    img_name = f'page-{img_index}'
    img_np = np.load(str(paths_object.path_150 / f'{img_name}.npy'))
    return ImageInfo(paths_object.path_pdf, img_index, img_name, img_np)
