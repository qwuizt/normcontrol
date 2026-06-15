import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.structures import ContentElement, Paths
from src.tools.summary_visualization import SummaryVisualization
from src.structures import PageClass

logger = logging.getLogger(__name__)


@dataclass
class PageOrder:
    min_pages: int
    after: tuple[PageClass, ...] | None
    order: int | None = None
    max_pages: int | None = None
    max_pages_warning_msg: str | None = None


PAGES_ORDER = {
    PageClass.titul: PageOrder(min_pages=1, after=(), order=0),
    PageClass.users: PageOrder(min_pages=1, after=(PageClass.titul, PageClass.users)),
    PageClass.abstract: PageOrder(min_pages=1, after=(PageClass.users, PageClass.abstract)),
    PageClass.content: PageOrder(min_pages=1, after=(PageClass.abstract, PageClass.content)),
    PageClass.abbreviations: PageOrder(min_pages=0, after=(PageClass.content, PageClass.abbreviations)),
    PageClass.introduction: PageOrder(
        min_pages=1,
        after=(PageClass.content, PageClass.abbreviations, PageClass.introduction),
        max_pages=5,
        max_pages_warning_msg='Алгоритм нашел слишком много страниц с классом "Введение". Возможно вы не '
        'правильно оформили основную часть. Обратите внимание что она должна быть '
        'пронумерована, например "3 Литературный обзор" (без точки после цифры)',
    ),
    PageClass.text: PageOrder(min_pages=1, after=(PageClass.introduction, PageClass.text)),
    PageClass.conclusion: PageOrder(min_pages=1, after=(PageClass.text, PageClass.conclusion)),
    PageClass.references: PageOrder(min_pages=0, after=(PageClass.conclusion, PageClass.references)),
    PageClass.app: PageOrder(
        min_pages=0,
        after=(
            PageClass.references,
            PageClass.conclusion,
            PageClass.app,
        ),
        order=-1,
    ),
}


def _validate_order(df_structure: pd.DataFrame) -> None:
    df_structure = df_structure.sort_values('page')

    # todo тут сделать список (см. документ от Аси, где содержание есть в приложении)
    structure: dict[str, int] = {}
    structure_prev: dict[str, str | None] = {}
    structure_next: dict[str, str | None] = {}

    class_: tuple[str, int] | None = None
    class_prev_: tuple[str, int] | None = None
    for i, row in df_structure.iterrows():
        page_type, page = row['page_type'], row['page']

        if class_ is None:
            class_ = (page_type, page)
            structure[page_type] = page
            structure_prev[page_type] = class_prev_[0] if class_prev_ is not None else None
            continue

        if class_prev_ is None or page_type != class_[0]:
            class_prev_ = class_
            class_ = (page_type, page)

            structure[class_[0]] = page
            structure_prev[class_[0]] = class_prev_[0] if class_prev_ is not None else None
            if class_prev_ is not None:
                structure_next[class_prev_[0]] = class_[0]
    if class_ is not None:
        structure_next[class_[0]] = None

    page_min: int = df_structure['page'].min()
    page_max: int = df_structure['page'].max()

    any_not_found: bool = False
    any_wrong_order: bool = False

    for page_class, order in PAGES_ORDER.items():
        is_present: bool = page_class.name in structure

        if not is_present and order.min_pages > 0:
            logger.error('Раздел "%s" должен присутствовать в документе, но не был найден', page_class.value)
            any_not_found = True

        if is_present:
            if order.order == 0 and structure[page_class.name] != page_min:
                name = df_structure.iloc[0]['page_type']
                logger.error(
                    'Раздел "%s" должен идти первым, но первый раздел: %s', page_class.value, PageClass[name].value
                )
            elif order.order == -1 and structure[page_class.name] != page_max:
                name = df_structure.iloc[-1]['page_type']
                logger.error(
                    'Раздел "%s" должен идти последним, но последний раздел: "%s"',
                    page_class.value,
                    PageClass[name].value,
                )

        class_prev: str = structure_prev.get(page_class.name, '')
        prev_sections: list[str] = list(map(lambda v: v.name, order.after))
        if is_present and prev_sections and class_prev not in prev_sections:
            classes = ', '.join(list(map(lambda v: v.value, order.after)))

            logger.error(
                'Раздел "%s" должен идти после разделов: "%s", но идет после раздела: %s',
                page_class.value,
                classes,
                PageClass[class_prev].value if class_prev else 'None',
            )
            any_wrong_order = True

        class_next: str = structure_next.get(page_class.name, '')
        if is_present and order.max_pages is not None and class_next:
            if order.max_pages < (pages_cnt := structure[class_next] - structure[page_class.name]):
                msg = order.max_pages_warning_msg or (
                    'Обычно максимальный размер для раздела "%s" - %d страниц, но в документе обнаружено %d страниц.',
                    page_class.value,
                    order.max_pages,
                    pages_cnt,
                )
                logger.warning(msg)

    if any_not_found:
        logger.warning(
            'Каждый структурный элемент и каждый раздел основной части должен начинаться '
            'с новой страницы, а также не содержать лишних символов. '
            'В ином случае он не будет найдет и появится это сообщение'
        )
    if any_wrong_order:
        logger.warning(
            'Порядок страниц должен быть следующий: титульный лист, список исполнителей, реферат, '
            'содержание, термины и определения, перечень сокращений и обозначений, введение, '
            'основная часть, заключение, список использованных источников, приложения'
        )


def _validate_pages_correct_by_content(
    path_pdf: Path, df_structure: pd.DataFrame, content_json: dict[str, ContentElement]
) -> None:
    pass


def validate_structure(paths_object: Paths, df_structure: pd.DataFrame, sv: SummaryVisualization | None = None) -> None:
    if df_structure.empty:
        raise ValueError('Структура документа не была обнаружена. Проверьте документ')

    if sv is None:
        sv = SummaryVisualization(paths_object.path_summary, validate_structure.__name__)
        sv.add_logger_handler(logger=logger)

    sv.set_meta(0, element=None)  # Выводим ошибки о структуре на титульной странице
    _validate_order(df_structure)

    sv.save()
