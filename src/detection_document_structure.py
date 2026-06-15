import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.models.abstract_text_extraction import AbstractTextExtraction
from src.paths import FOLDER_SUMMARY
from src.structures import StructurePage, PageClass, BoundingBox
from src.tools.summary_visualization import SummaryVisualization, SummaryVerbose

logger = logging.getLogger(__name__)


@dataclass
class SectionRule:
    enum: PageClass
    keywords: list[str]
    starts_with: str | None
    line_number: int | None


# Определение структуры документа
SECTION_RULES = {
    'Титульная страница': SectionRule(
        enum=PageClass.titul,
        keywords=['о научно-исследовательской работе'],
        starts_with=None,
        line_number=None,
    ),
    'Список исполнителей': SectionRule(
        enum=PageClass.users,
        keywords=['СПИСОК ИСПОЛНИТЕЛЕЙ'],
        starts_with=None,
        line_number=0,
    ),
    'Реферат': SectionRule(
        enum=PageClass.abstract,
        keywords=['РЕФЕРАТ'],
        starts_with=None,
        line_number=0,
    ),
    'Содержание': SectionRule(
        enum=PageClass.content,
        keywords=['СОДЕРЖАНИЕ'],
        starts_with=None,
        line_number=0,
    ),
    'Перечень сокращений и обозначений': SectionRule(
        enum=PageClass.abbreviations,
        keywords=['ПЕРЕЧЕНЬ СОКРАЩЕНИЙ И ОБОЗНАЧЕНИЙ', 'ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ'],
        starts_with=None,
        line_number=0,
    ),
    'Введение': SectionRule(
        enum=PageClass.introduction,
        keywords=['ВВЕДЕНИЕ'],
        starts_with=None,
        line_number=0,
    ),
    'Текстовая часть': SectionRule(
        enum=PageClass.text,
        keywords=[],
        starts_with=r'1\s?\.?\s*[A-ZА-Яa-zа-я]',  # с новой странице и должно начинаться "1 Название раздела" или "1. Название"
        line_number=0,
    ),
    'Заключение': SectionRule(
        enum=PageClass.conclusion,
        keywords=['ЗАКЛЮЧЕНИЕ'],
        starts_with=None,
        line_number=0,
    ),
    'Список литературы': SectionRule(
        enum=PageClass.references,
        keywords=[
            'СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ',
            'СПИСОК ЛИТЕРАТУРЫ',
            'СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ',
            'СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ',
        ],
        starts_with=None,
        line_number=2,
    ),
    'Приложение': SectionRule(
        enum=PageClass.app,
        keywords=['ПРИЛОЖЕНИЯ'],
        starts_with=r'(?i)приложени[ея]',
        line_number=2,
    ),
}


def detect_document_structure(
    path_pdf: Path, text_extractor: AbstractTextExtraction, sv: SummaryVisualization | None = None
) -> pd.DataFrame:
    """
    Распознать и сохранить структуру документа. Определить, на какой страница находится какой
    структурный элемент (титульный, список пользователей, текстовая часть, приложение и тд)

    :param path_pdf: путь к *.pdf файлу
    :param text_extractor: модель для извлечения текста
    :param sv: инстанс, для логирования ошибок и предупреждений для последующей визуализации
    :return: путь к *.csv документу со структурой документа
    """
    if sv is None:
        sv = SummaryVisualization(path_pdf.parent / FOLDER_SUMMARY, detect_document_structure.__name__)

    structure: list[StructurePage] = []

    with text_extractor as extractor:
        n_pages = extractor.n_pages
        for page_num in range(extractor.n_pages):
            logger.info('Start analyze page %d from %d', page_num, n_pages)

            section: PageClass | None = None
            bbox: BoundingBox | None = None

            lines: list[tuple[str, BoundingBox]] = []
            for line in extractor.extract_lines(page_num):
                lines.append((line.text, line.bbox))

            is_found: bool = False
            for section_name, rule in SECTION_RULES.items():
                lines_for_rule = lines if rule.line_number is None else lines[: rule.line_number + 1]

                for line, line_bbox in lines_for_rule:
                    is_found = False
                    if rule.keywords:
                        is_found = any(keyword.lower() == line.lower() for keyword in rule.keywords)
                    if not is_found and rule.starts_with:
                        is_found = re.match(rule.starts_with, line) is not None

                    if not rule.keywords and not rule.starts_with:
                        raise ValueError(
                            'Не указано, как искать раздел. Должно быть указаны ключевые слова или '
                            'с чего начинается правило'
                        )

                    if is_found:
                        logger.info('Found class %s on page %d', rule.enum.name, page_num)
                        section = rule.enum
                        bbox = line_bbox
                        break

                if is_found:
                    break  # Если правило нашли на странице - нет смысла искать дальше

            if section is not None:
                logger.info('Adding section %s for page %d', section.name, page_num)
                sv.add_rectangle(f'page-{page_num}', bbox, verbose=SummaryVerbose.ADDITIONAL_MAIN)
                structure.append(StructurePage(page=page_num, page_type=section.name))
            else:
                if structure:
                    structure.append(StructurePage(page=page_num, page_type=structure[-1]['page_type']))
                else:
                    logger.warning(
                        'Не обнаружен класс страницы, при этом до нет какого-то класса. По этому '
                        'страницы не будет в итоговом файле со структурой'
                    )
                logger.info('On page %d was not found any class page', page_num)

    sv.save()
    return pd.DataFrame.from_records(structure)
