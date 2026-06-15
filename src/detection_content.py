import logging
import re

import pandas as pd

from src.models.model_text_extraction import PyMuPDFModel
from src.structures import ContentElement, Paths, PageClass, BoundingBox, ExtractedText
from src.tools.summary_visualization import SummaryVisualization

logger = logging.getLogger(__name__)


def is_section():
    pass


def extract_section_name(text: str) -> str:
    """
    Определить наименование или номер раздела. Номер раздела - 1. или 1.1
    Если номер нет, то записывается наименование, например "ВВЕДЕНИЕ", или "СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ"
    :param text: str
    :return: str, name of section
    """
    section_number: str = ''
    if match_section := re.match(r'[\d.]+', text):
        section_number = match_section.group(0).strip('.')

    if not section_number and (match_section := re.match(r'(приложение (\w+)) ', text, re.IGNORECASE)):
        section_number = match_section.group(1)

    if not section_number and (match_section := re.match(r'[А-ЯЁ ]{3,}', text)):
        section_number = match_section.group(0).strip()

    if not section_number and re.match(r'\s*(\w+\s+){2}\w+ *\w+', text):
        # Забыли указать номер раздела
        return ''

    elif not section_number and (match_section := re.match(r'\s*(\w+\s+){2}\w+', text)):
        # Если исполнитель написал "Введение" не upper case
        section_number = match_section.group(0).strip()

    if not section_number:
        logger.warning('Наименования раздела не было найдено для пункта содержания "%s"', text)

    return section_number


def extract_text(content_text: str, section_name: str) -> str:
    index_start: int = len(section_name)

    # Когда разраб не знает гост и пишет номер раздела как "1. Бла бла бла"
    content_text_without_section = content_text[index_start:]
    if (match := re.match(r'(\. *)\w+', content_text_without_section)) is not None:
        index_end: int = match.span(1)[1]
        content_text_without_section = content_text_without_section[index_end:]

    # Когда остались только точки
    content_text_without_section = content_text_without_section.strip()
    if content_text_without_section.startswith('.'):
        return ''

    match = re.match(r'[^.]*', content_text_without_section)
    if match is not None:
        return match.group(0).strip()

    return ''


def _create_content_element(line: ExtractedText, text: str) -> ContentElement:
    section_number = extract_section_name(text)
    content = extract_text(text, section_number)
    # Кейс когда надо для контента убрать номер страницы
    content = re.sub(r'\d+ *$', '', content).strip()
    return ContentElement(
        section_number,
        page_number=None,
        text=content,
        content_page_number=line.page_num,
        lines=[line]
    )


def try_to_extract_page_number(item: ContentElement) -> None:
    if item.page_number is not None:
        return

    assert len(item.lines) > 0, 'Метод должен вызываться если есть хотя бы одна строка'
    last_row = item.lines[-1]

    text = last_row.text
    if (match := re.search(r'\.+ *(\d+) *$', text)) is not None:
        item.text = item.text[:match.start() + 1]  # Вырезаем
        item.page_number = int(match.group(1))
    elif (match := re.search(r' *(\d+) *$', text)) is not None:
        item.text = item.text[: match.start() + 1]  # Вырезаем
        item.page_number = int(match.group(1))
    else:
        logger.info('Не найден номер страницы для элемента содержания: %s', item.section_number)


def extract_lines(lines: list[ExtractedText]) -> list[ContentElement]:
    res = []

    lines = [l for l in lines if len(l.text) >= 5 and l.text.lower() != 'содержание']

    for index, line in enumerate(lines):
        text = line.text.replace('…', '.')

        if not res:
            res.append(_create_content_element(line, text))
            try_to_extract_page_number(res[-1])
            continue

        prev_is_finished: bool = res[-1].page_number is not None
        if prev_is_finished:
            res.append(_create_content_element(line, text))
        else:
            res[-1].lines.append(line)

        try_to_extract_page_number(res[-1])


    return res


def detection_content(paths_object: Paths, sv: SummaryVisualization | None = None) -> dict[str, ContentElement]:
    """
    Распознать содержание документа и сохранить его в файл *.json

    :param paths_object: путь к pdf и другим вспомогательным файлам
    :param sv: инстанс, для логирования ошибок и предупреждений для последующей визуализации
    :return: путь к созданному json файлу с содержанием
    """
    if not paths_object.path_pdf.exists():
        raise FileNotFoundError(f'Файл pdf для детекции содержания документ не найден по пути "{paths_object}"')

    if sv is None:
        sv = SummaryVisualization(paths_object.path_summary, detection_content.__name__)

    # Структура документа
    df_structure = pd.read_csv(paths_object.path_file_structure)
    content_page_numbers: list[int] = PageClass.get_page_numbers(df_structure, PageClass.content)

    # номер раздела, подраздела или приложения -> информация и элементе содержания
    content: dict[str, ContentElement] = {}

    if content_page_numbers:
        with PyMuPDFModel(paths_object.path_pdf) as extractor:
            lines: list[ExtractedText] = []

            page_num_prev = None
            for page_num in content_page_numbers:
                if page_num_prev is not None and (page_num - page_num_prev) > 10:
                    break  # Тупняк с приложением, надо разбираться

                lines.extend(extractor.extract_lines(page_num, sort=True))
                page_num_prev = page_num

            content: list[ContentElement] = extract_lines(lines)
    else:
        logger.warning(
            'Не найдены страницы с содержанием документа, по этому информация о содержании '
            'останется пустой. Все проверки связанные с информацией о содержании не будут выполненны'
        )

    return content
