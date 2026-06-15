import difflib
import logging
import re
from logging import Logger

from src.structures import FoundedCaption, FoundedReference, ImageInfo, PageElementDetail
from src.tools.utils import get_section_location


def validate_table_reference(
    img_info: ImageInfo,
    reference: FoundedReference | None,
    content: dict[str, int],
    logger: logging.Logger | None = None,
) -> bool:
    if reference is None:
        if logger:
            logger.error('Нет ссылки на таблицу')
        return False

    is_valid = validate_table_reference_format(reference, logger)
    is_valid &= validate_table_reference_numbers(reference, logger)
    is_valid &= validate_table_reference_order(img_info, reference, content, logger)

    return is_valid


def validate_table_reference_format(
    reference: FoundedReference,
    logger: Logger | None = None,
    toi_cutoff: float = 0.8,
) -> bool:
    is_valid = True

    reference_text = [word.lower().strip() for word in reference.text.split() if word]
    is_full = difflib.get_close_matches(word='таблица', possibilities=reference_text, n=1, cutoff=toi_cutoff)
    is_short = difflib.get_close_matches(word='табл.', possibilities=reference_text, n=1, cutoff=toi_cutoff)
    if not is_full and not is_short:
        if logger:
            logger.error(
                'Ссылка на таблицу должна начинаться со слова "таблица 1" или "в таблице 1". '
                'Допускаются сокращения вида "(табл. 1)"',
                extra={'bbox': reference.box},
            )
        is_valid &= False

    return is_valid


def validate_table_reference_numbers(
    reference: FoundedReference,
    logger: Logger | None = None,
) -> bool:
    is_valid = True

    if reference.section_number and re.fullmatch(r'[\dA-ZА-ЯЁ]', reference.section_number) is None:
        if logger:
            logger.error(f'Неправильное обозначение раздела: {reference}')
        is_valid &= False

    if reference.number:
        for n in reference.number.split('.'):
            if not n.isdigit():
                if logger:
                    logger.error('Номер таблицы не число: {reference}', extra={'bbox': reference.box})
                is_valid &= False
                break
    else:
        if logger:
            logger.error('Нет номера таблицы у ссылки', extra={'bbox': reference.box})
        is_valid &= False

    return is_valid


def validate_table_reference_order(
    img_info: ImageInfo,
    reference: FoundedReference,
    content: dict[str, int],
    logger: Logger | None = None,
) -> bool:
    is_valid = True

    if reference.section_number:
        if section_location := get_section_location(img_info, reference.section_number, content):
            section_page, section_bbox = section_location
            if reference.page_number < section_page:
                if logger:
                    logger.error(
                        f'Ссылка содержит раздел, который начинается ниже, на странице {section_page}',
                        extra={'bbox': reference.box},
                    )
                is_valid &= False
            elif reference.page_number == section_page and reference.box.top < section_bbox.top:
                if logger:
                    logger.error(
                        'Ссылка содержит раздел, который начинается ниже',
                        extra={'bbox': reference.box},
                    )
                is_valid &= False
        else:
            if logger:
                logger.error(
                    f'Ссылка содержит раздел, которого нет в документе "{reference.section_number}"',
                    extra={'bbox': reference.box},
                )
            is_valid &= False

    return is_valid


def validate_figure_reference(
    reference: FoundedReference,
    figure_info: PageElementDetail,
    new_references: list[FoundedReference],
    references: list[FoundedReference],
    caption: FoundedCaption,
    logger: logging.Logger,
    sv=None,
) -> bool:
    is_valid = True

    if re.search(r'\bсм\.?\s+рис(?!\w)', reference.text.lower()) or re.search(
        r'\bрис(?!\w)',
        reference.text.lower(),
    ):
        if 'рис' in reference.text and 'рис.' not in reference.text:
            logger.warning(
                'После "рис" в ссылке на рисунок должна быть точка: "рис."',
                extra={'bbox': reference.box},
            )

    if reference not in references:
        references.append(reference)
        new_references.append(reference)

    return is_valid
