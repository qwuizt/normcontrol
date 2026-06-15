import difflib
import logging
import re
from dataclasses import replace

from src.constants import LEFT_MARGIN_MM, RIGHT_MARGIN_MM, SHAPE_A4_MM
from src.models.model_text_extraction import PyMuPDFModel
from src.structures import FoundedCaption, ImageInfo, PageElementDetail
from src.tools.tools import scale_bbox_by_shapes
from src.tools.utils import check_captions_sequence, get_section_location, get_symbol_before_name
from src.tools.validation.helpers import (
    caption_words,
    is_latin_or_cyrillic_a,
    table_caption_start_kind,
    to_int,
    to_letter_index,
)
from src.tools.validation.schemas import (
    CaptionAlignmentRule,
    CaptionCapitalizationRule,
    CaptionEndPunctuationRule,
    CaptionExistenceRule,
    CaptionIntervalRule,
    CaptionNumberRule,
    CaptionOrderRule,
    CaptionRule,
    CaptionSectionRule,
    CaptionSeparatorRule,
    CaptionStartRule,
    CaptionTitleRequiredRule,
    CaptionValidationContext,
    CaptionValidationRules,
)
from src.tools.validation_text import text_is_centered


def validate_caption(
    context: CaptionValidationContext,
    rules: CaptionValidationRules,
) -> bool:
    return _validate_caption_context(context, rules)


def _validate_caption_context(context: CaptionValidationContext, rules: CaptionValidationRules) -> bool:
    existence_rule = next((r for r in rules.rules if isinstance(r, CaptionExistenceRule)), None)
    if existence_rule is not None and not _apply_existence_rule(context, existence_rule):
        return False

    if context.caption is None:
        return False

    is_valid = True
    for rule in rules.rules:
        if isinstance(rule, CaptionExistenceRule):
            continue
        is_valid &= _apply_rule(context, rule)

    return is_valid


def validate_table_caption_existence(context: CaptionValidationContext) -> bool:
    is_valid = True

    if context.caption is None or context.caption in context.captions:
        if context.logger:
            context.logger.error('подпись для таблицы не найдена', extra={'bbox': context.table_info.box})
        is_valid &= False

    return is_valid


def validate_figure_caption_existence(context: CaptionValidationContext) -> bool:
    if context.caption is not None:
        return True

    if context.logger:
        context.logger.error('Caption was not found', extra={'bbox': context.figure_info.box})
    return False



def validate_table_caption_format(
    context: CaptionValidationContext,
    toi_cutoff: float = 0.8,
) -> bool:
    is_valid = True
    caption = context.caption
    logger = context.logger

    caption_text = [word.strip() for word in caption.text.split() if word]
    if difflib.get_close_matches(word='Таблица', possibilities=[caption_text[0]], n=1, cutoff=toi_cutoff):
        if not caption.number:
            if logger:
                logger.error('ошибка в нумерации', extra={'bbox': caption.box})
            is_valid &= False

        if len(caption_text) == 1:
            if logger:
                logger.error('нет номера и названия таблицы', extra={'bbox': caption.box})
            is_valid &= False
        elif len(caption_text) == 2 and (caption.section_number or (caption.number and caption.number.isdigit())):
            if logger:
                logger.error(f'нет названия таблицы: {caption}')
            is_valid &= False
        elif re.search(r'[A-Za-zА-Яа-яЁё]', ' '.join(caption_text[2:])) is None:
            if logger:
                logger.error('нет названия таблицы', extra={'bbox': caption.box})
            is_valid &= False

        if len(caption_text) > 2 and get_symbol_before_name(caption.text) not in {'-', '–', '—'}:
            if logger:
                logger.error('неверный формат подписи (нет тире)', extra={'bbox': caption.box})
            is_valid &= False

    elif difflib.get_close_matches(
        word='Продолжение таблицы',
        possibilities=[' '.join(caption_text[:2])],
        n=1,
        cutoff=toi_cutoff,
    ):
        if not caption.number:
            if logger:
                logger.error('отсутствует номер таблицы', extra={'bbox': caption.box})
            is_valid &= False

        if len(caption_text) > 3:
            if logger:
                logger.error('содержит лишнее', extra={'bbox': caption.box})
            is_valid &= False
    else:
        if logger:
            logger.error(
                'подписи таблица должна начинаться со слов "Таблица..." или "Продолжение таблицы..."',
                extra={'bbox': caption.box},
            )
        is_valid &= False

    return is_valid


def _validate_start(context: CaptionValidationContext, rule: CaptionStartRule) -> bool:
    caption = context.caption
    logger = context.logger
    caption_text = caption_words(caption)

    for allowed_start in rule.allowed:
        allowed_words = [word.strip() for word in allowed_start.split() if word]
        actual_start = ' '.join(caption_text[:len(allowed_words)])
        if difflib.get_close_matches(
            word=allowed_start.lower(),
            possibilities=[actual_start.lower()],
            n=1,
            cutoff=rule.similarity_cutoff,
        ):
            return True

    if logger:
        logger.error(
            'подпись должна начинаться с одного из допустимых значений: %s',
            ', '.join(rule.allowed),
            extra={'bbox': caption.box},
        )
    return False


def _validate_title_required(context: CaptionValidationContext, rule: CaptionTitleRequiredRule) -> bool:
    caption = context.caption
    logger = context.logger
    caption_text = caption_words(caption)
    start_kind = table_caption_start_kind(caption_text, rule.similarity_cutoff)
    is_valid = True

    if start_kind == 'table':
        if not caption.number:
            if logger:
                logger.error('ошибка в нумерации', extra={'bbox': caption.box})
            is_valid &= False

        if len(caption_text) == 1:
            if logger:
                logger.error('нет номера и названия таблицы', extra={'bbox': caption.box})
            is_valid &= False
        elif len(caption_text) == 2 and (caption.section_number or (caption.number and caption.number.isdigit())):
            if logger:
                logger.error(f'нет названия таблицы: {caption}')
            is_valid &= False
        elif re.search(r'[A-Za-zА-Яа-яЁё]', ' '.join(caption_text[2:])) is None:
            if logger:
                logger.error('нет названия таблицы', extra={'bbox': caption.box})
            is_valid &= False

    elif start_kind == 'table_continuation':
        if not caption.number:
            if logger:
                logger.error('отсутствует номер таблицы', extra={'bbox': caption.box})
            is_valid &= False

        if len(caption_text) > 3:
            if logger:
                logger.error('содержит лишнее', extra={'bbox': caption.box})
            is_valid &= False

    return is_valid


def _validate_separator(context: CaptionValidationContext, rule: CaptionSeparatorRule) -> bool:
    caption = context.caption
    logger = context.logger
    allowed = set(rule.allowed)
    caption_text = caption_words(caption)
    start_kind = table_caption_start_kind(caption_text, rule.similarity_cutoff)

    if start_kind == 'table':
        if len(caption_text) > 2 and get_symbol_before_name(caption.text) not in allowed:
            if logger:
                logger.error('неверный формат подписи (нет тире)', extra={'bbox': caption.box})
            return False
        return True

    if start_kind is None and len(caption_text) > 2 and caption_text[2] not in allowed:
        if logger:
            logger.error(
                'После слова "Рисунок" в подписи рисунка должен идти дефис, например "Рисунок 1 — "',
                extra={'bbox': caption.box},
            )
        return False

    return True


def validate_table_caption_number_format(
    context: CaptionValidationContext,
) -> bool:
    is_valid = True
    caption = context.caption
    logger = context.logger

    if caption.section_number and re.fullmatch(r'[\dA-ZА-ЯЁ]', caption.section_number) is None:
        if logger:
            logger.error('неправильное обозначение раздела', extra={'bbox': caption.box})
        is_valid &= False

    if caption.number:
        nums = caption.number.split('.')
        if len(nums) > 1:
            if logger:
                logger.error('номер может быть максимум 2-х уровневым', extra={'bbox': caption.box})
            is_valid &= False

        for n in nums:
            if not n.isdigit():
                if logger:
                    logger.error('номер таблицы не число', extra={'bbox': caption.box})
                is_valid &= False
                break
    else:
        if logger:
            logger.error('нет номера таблицы', extra={'bbox': caption.box})
        is_valid &= False

    return is_valid


def validate_table_caption_letters_format(
    context: CaptionValidationContext,
    toi_cutoff: float = 0.7,
) -> bool:
    is_valid = True
    caption = context.caption
    logger = context.logger

    caption_text = [word for word in caption.text.split() if word]
    if not caption_text:
        return False

    if caption_text[0].islower():
        if logger:
            logger.error('подпись начинается не с заглавной буквы', extra={'bbox': caption.box})
        is_valid &= False

    if (
        difflib.get_close_matches(word='Таблица', possibilities=[caption_text[0]], n=1, cutoff=toi_cutoff)
        and len(caption_text) > 3
    ):
        caption_name = ' '.join(caption_text[2:])
        first_symbol = re.search(r'[^_\s\W]', caption_name)
        if first_symbol and first_symbol[0].islower():
            if logger:
                logger.error(
                    'название элемента (после тире) начинается не с заглавной буквы',
                    extra={'bbox': caption.box},
                )
            is_valid &= False

    if re.search(r'[_\s\W^)]', caption_text[-1][-1]) and re.search(r'[^)"\']', caption_text[-1][-1]):
        if logger:
            logger.error('на конце не должно быть знаков препинания', extra={'bbox': caption.box})
        is_valid &= False

    return is_valid


def _validate_capitalization(context: CaptionValidationContext, rule: CaptionCapitalizationRule) -> bool:
    caption = context.caption
    logger = context.logger
    caption_text = caption_words(caption)
    if not caption_text:
        return False

    is_valid = True
    if caption_text[0].islower():
        if logger:
            logger.error('подпись начинается не с заглавной буквы', extra={'bbox': caption.box})
        is_valid &= False

    if (
        rule.validate_title_after_separator
        and len(caption_text) > 3
    ):
        separator_index = next(
            (index for index, word in enumerate(caption_text) if word in {'-', '–', '—', '―'}),
            1,
        )
        caption_name = ' '.join(caption_text[separator_index + 1:])
        first_symbol = re.search(r'[^_\s\W]', caption_name)
        if first_symbol and first_symbol[0].islower():
            if logger:
                logger.error(
                    'название элемента (после тире) начинается не с заглавной буквы',
                    extra={'bbox': caption.box},
                )
            is_valid &= False

    return is_valid


def _validate_end_punctuation(context: CaptionValidationContext, rule: CaptionEndPunctuationRule) -> bool:
    caption = context.caption
    logger = context.logger
    caption_text = caption_words(caption)
    if not caption_text:
        return False

    last_symbol = caption_text[-1][-1]

    if rule.required is not None:
        if last_symbol in rule.required:
            return True
        if logger:
            logger.error(
                'подпись должна заканчиваться одним из допустимых символов: %s',
                ', '.join(rule.required),
                extra={'bbox': caption.box},
            )
        return False

    if not rule.forbidden:
        return True

    start_kind = table_caption_start_kind(caption_text, 0.7)

    if start_kind is None:
        if last_symbol == '.':
            if logger:
                logger.error('В конце подписи рисунка не должно быть точки', extra={'bbox': caption.box})
            return False
        return True

    if re.search(r'[_\s\W^)]', last_symbol) and re.search(r'[^)"\']', last_symbol):
        if logger:
            logger.error('на конце не должно быть знаков препинания', extra={'bbox': caption.box})
        return False

    return True


def validate_table_caption_padding(
    context: CaptionValidationContext,
) -> bool:
    is_valid = True
    caption = context.caption
    logger = context.logger

    with PyMuPDFModel(context.img_info.path_pdf):
        lines = caption.lines

        for i, line in enumerate(lines):
            bbox_mm = scale_bbox_by_shapes(context.shape_page, SHAPE_A4_MM, line.bbox)

            if abs(LEFT_MARGIN_MM - bbox_mm.left) > 3:
                if logger:
                    logger.warning(
                        'По ГОСТ подпись таблицы должна идти без отступа от поля документа. '
                        'У вас наблюдается отступ %dмм',
                        LEFT_MARGIN_MM - bbox_mm.left,
                        extra={'bbox': caption.box},
                    )
                is_valid &= False

            if len(lines) > 1 and i < len(lines) - 1:
                diff_right_margin = (SHAPE_A4_MM[0] - bbox_mm.right) - RIGHT_MARGIN_MM

                if diff_right_margin < 0 and abs(diff_right_margin) > 3 or diff_right_margin > 3:
                    if logger:
                        logger.warning(
                            'По ГОСТ расстояние от правого края должно быть 15мм, у вас %dмм, '
                            'что является ошибкой',
                            SHAPE_A4_MM[0] - bbox_mm.right,
                            extra={'bbox': caption.box},
                        )
                    is_valid &= False

    return is_valid


def validate_table_caption_order(
    context: CaptionValidationContext,
) -> bool:
    caption = context.caption
    captions = context.captions
    logger = context.logger

    if not validate_table_caption_number_format(context):
        if logger:
            logger.error('порядок не определить, тк подпись в неверном формате', extra={'bbox': caption.box})
        return False

    is_valid = True

    if caption.section_number:
        if section_location := get_section_location(context.img_info, caption.section_number, context.content):
            section_page, section_bbox = section_location
            if caption.page_number < section_page:
                if logger:
                    logger.error(
                        f'Подпись содержит раздел, который начинается ниже, на странице {section_page}',
                        extra={'bbox': caption.box},
                    )
                is_valid &= False
            elif caption.page_number == section_page and caption.box.top < section_bbox.top:
                if logger:
                    logger.error('Подпись содержит раздел, который начинается ниже', extra={'bbox': caption.box})
                is_valid &= False
        else:
            if logger:
                logger.error('Подпись содержит раздел, которого нет в документе', extra={'bbox': caption.box})
            is_valid &= False

    if len(captions) == 0:
        if caption.number != '1':
            if logger:
                logger.warning(
                    'Эта таблица первая, которую нормоконтроль нашел, но номер таблицы не "1". '
                    'Обратите внимание на это. Если это ошибка и алгоритм пропустил ошибку - пожалуйста, '
                    'игнорируйте данное сообщение',
                    extra={'bbox': caption.box},
                )
            is_valid &= False

        return is_valid

    prev_caption = captions[-1]
    if not validate_table_caption_number_format(replace(context, caption=prev_caption)):
        if logger:
            logger.warning(
                f'порядок не определить, тк предыдущая подпись ({prev_caption}) в неверном формате',
                extra={'bbox': caption.box},
            )

        return is_valid

    is_valid &= check_captions_sequence(prev_caption, caption, logger)

    return is_valid


def find_figure_section_number(
    img_page_number: int,
    start_section_pages: list[str | int],
    sections_numbers: list[str],
) -> str | None:
    section = None

    for i in range(len(start_section_pages) - 1, -1, -1):
        is_valid_number = type(start_section_pages[i]) is int or start_section_pages[i].isnumeric()
        if is_valid_number and img_page_number >= int(start_section_pages[i]):
            section = sections_numbers[i]

            if section.strip().isnumeric() or re.match(r'^\s*приложение\b', section, re.IGNORECASE):
                match = re.search(r'([А-ЯA-Z])$', section)
                if match:
                    return match.group(1)
                break

            section = None

    return section


def validate_figure_section_number(
    page_number: int,
    section_number: str,
    content: dict[str, int],
    logger: logging.Logger,
) -> bool:
    int_section_number = to_int(section_number)

    if int_section_number:
        section_page_number: int | None = content.get(str(section_number), None)
    else:
        section_page_number: int | None = content.get(f'ПРИЛОЖЕНИЕ {section_number}', None)

    if section_page_number is not None:
        section_page_number = section_page_number - 1  # fixme

    if section_page_number is None:
        logger.warning(
            f'На странице "{page_number}" не удалось найти номер раздела "{section_number}" в содержании документа'
        )
        return False

    if page_number < section_page_number:
        logger.error(
            f'раздел начинается на странице {section_page_number}, '
            f'а элемент на странице {page_number}. Подпись элемента ссылается на раздел '
            f'"{section_number}", который расположен ниже элемента'
        )
        return False

    if page_number == section_page_number:
        logger.warning(
            f'На странице "{page_number}" раздел {section_number} начинается на той же странице, '
            f'что и подпись элемента. Требуется дополнительная проверка'
        )
        return False

    if isinstance(section_number, int):
        # fixme - В Раздел_ПД_No1_ПЗ.pdf тут строка приходит
        section_next_number = str(section_number + 1)
        if section_next_number in content:
            section_next_page_number: int = content[section_next_number]

            if page_number > section_next_page_number:
                logger.error(
                    f'На странице "{page_number}" находится уже следующий раздел {section_next_number} '
                    f'хотя в подписи элемента находится ссылка на предыдущий'
                )
                return False

            if page_number == section_next_page_number:
                logger.warning(
                    f'На странице "{page_number}", следующий раздел {section_next_number} начинается '
                    f'на той же странице, что и подпись элемента. Требуется дополнительная проверка '
                    f'что подпись к элементу идет до начала следующего раздела'
                )
                return False

    return True


def validate_figure_caption_text(caption: FoundedCaption, logger: logging.Logger) -> None:
    text_caption = caption.text.split()
    if text_caption[1].endswith('.'):
        logger.error('После нумерации не должно идти точки, например "Рисунок 1 - "', extra={'bbox': caption.box})

    if text_caption[2] not in {'-', '—', '–', '―', '—'}:
        logger.error(
            'После слова "Рисунок" в подписи рисунка должен идти дефис, например "Рисунок 1 — "',
            extra={'bbox': caption.box},
        )

    if text_caption[-1][-1] == '.':
        logger.error('В конце подписи рисунка не должно быть точки', extra={'bbox': caption.box})



def validate_figure_number_order(
    caption: FoundedCaption,
    captions: list[FoundedCaption],
    logger: logging.Logger,
) -> bool:
    if not captions:
        return to_int(caption.number) == 1

    prev_caption = captions[-1]
    current_number = to_int(caption.number)
    previous_number = to_int(prev_caption.number)
    if current_number is None or previous_number is None:
        return False

    if not _figure_caption_numbering_style_is_consistent(caption, captions):
        logger.error(
            'Нумерация рисунков должна быть в одном стиле (либо сплошная, либо с номерами разделов)',
            extra={'bbox': caption.box},
        )
        return False

    if caption.section_number is None:
        return current_number == previous_number + 1

    return _figure_caption_section_order_is_valid(caption, prev_caption, current_number, previous_number)


def _figure_caption_numbering_style_is_consistent(
    caption: FoundedCaption,
    captions: list[FoundedCaption],
) -> bool:
    current_has_section = caption.section_number is not None
    first_has_section = captions[0].section_number is not None

    if to_letter_index(caption.section_number) is not None:
        return True

    return current_has_section == first_has_section


def _figure_caption_section_order_is_valid(
    caption: FoundedCaption,
    prev_caption: FoundedCaption,
    current_number: int,
    previous_number: int,
) -> bool:
    if caption.section_number == prev_caption.section_number:
        return current_number == previous_number + 1

    if current_number != 1:
        return False

    current_section_is_letter = to_letter_index(caption.section_number) is not None
    previous_section_is_letter = to_letter_index(prev_caption.section_number) is not None
    previous_section_is_number = to_int(prev_caption.section_number) is not None
    starts_appendix_numbering = (
        current_section_is_letter
        and not previous_section_is_letter
        and not previous_section_is_number
    )

    return (
        _is_next_numeric_section(caption.section_number, prev_caption.section_number)
        or _is_next_letter_section(caption.section_number, prev_caption.section_number)
        or starts_appendix_numbering
    )


def _is_next_numeric_section(current_section: str | None, previous_section: str | None) -> bool:
    current_section_num = to_int(current_section)
    previous_section_num = to_int(previous_section)
    return (
        current_section_num is not None
        and previous_section_num is not None
        and current_section_num == previous_section_num + 1
    )


def _is_next_letter_section(current_section: str | None, previous_section: str | None) -> bool:
    current_letter_index = to_letter_index(current_section)
    previous_letter_index = to_letter_index(previous_section)
    return (
        current_letter_index is not None
        and previous_letter_index is not None
        and current_letter_index == previous_letter_index + 1
    )


def validate_caption_centering(
    img_info: ImageInfo,
    caption: FoundedCaption,
    logger: logging.Logger,
) -> bool:
    is_valid = True
    with PyMuPDFModel(img_info.path_pdf) as pdf:
        box_content = pdf.get_page_content_bbox(caption.page_number)

    for caption_line in caption.lines:
        if text_is_centered(caption_line.bbox, box_content.left, box_content.right, logger):
            continue

        logger.error('Строка в подписи к рисунку не отцентрована', extra={'bbox': caption_line.bbox})
        is_valid = False
        break

    return is_valid


def validate_figure_caption_section(
    img_info: ImageInfo,
    caption: FoundedCaption,
    content: dict[str, int],
    logger: logging.Logger,
) -> bool:
    if not content:
        return True

    sections_numbers, start_section_pages = zip(*sorted(content.items(), key=lambda x: x[1]))
    current_section_number = find_figure_section_number(
        img_info.img_index,
        list(start_section_pages),
        list(sections_numbers),
    )

    if caption.section_number is None:
        return True

    is_valid = True
    try:
        is_valid &= validate_figure_section_number(
            caption.page_number,
            caption.section_number,
            content,
            logger,
        )
    except ValueError:
        logger.warning(
            f'Номер раздела в подписи "{caption.section_number}" не является числом',
            extra={'bbox': caption.box},
        )

    if str(caption.section_number) == str(current_section_number):
        return is_valid

    if is_latin_or_cyrillic_a(caption.section_number) and is_latin_or_cyrillic_a(current_section_number):
        logger.warning(
            'для раздела в подписи рисунка и содержании использованы буквы А разных языков',
            extra={'bbox': caption.box},
        )
        return False

    logger.warning(
        f'для подписи рисунка указанный в подписи раздел — {caption.section_number}, '
        f'но согласно содержанию рисунок находится в разделе {current_section_number}',
        extra={'bbox': caption.box},
    )
    return False


def validate_figure_caption_order(
    caption: FoundedCaption,
    captions: list[FoundedCaption],
    logger: logging.Logger,
) -> bool:
    if not caption.number.isdigit():
        logger.warning(
            f'Невозвожно проверить порядок нумерации рисунков, из-за некорректного номера: "{caption.number}"',
            extra={'bbox': caption.box},
        )
        return False

    if validate_figure_number_order(caption, captions, logger):
        return True

    num_prev = captions[-1].num_str if captions else 'Нет'
    logger.error(
        'Найдена ошибка в порядке нумерации рисунков, '
        'текущая подпись: "%s", предыдущая: "%s". Возможно '
        'алгоритмы пропустили рисунок до текущего',
        caption.num_str,
        num_prev,
        extra={'bbox': caption.box},
    )
    return False


def _apply_existence_rule(context: CaptionValidationContext, rule: CaptionExistenceRule) -> bool:
    if rule.kind == 'table':
        return validate_table_caption_existence(context)
    return validate_figure_caption_existence(context)


def _apply_rule(context: CaptionValidationContext, rule: CaptionRule) -> bool:
    match rule:
        case CaptionExistenceRule():
            return True
        case CaptionStartRule():
            return _validate_start(context, rule)
        case CaptionTitleRequiredRule():
            return _validate_title_required(context, rule)
        case CaptionAlignmentRule():
            return _validate_alignment(context, rule)
        case CaptionSeparatorRule():
            return _validate_separator(context, rule)
        case CaptionCapitalizationRule():
            return _validate_capitalization(context, rule)
        case CaptionEndPunctuationRule():
            return _validate_end_punctuation(context, rule)
        case CaptionNumberRule():
            return _validate_number(context, rule)
        case CaptionOrderRule():
            return _validate_order(context, rule)
        case CaptionIntervalRule():
            return _validate_interval(context, rule)
        case CaptionSectionRule():
            return _validate_section(context, rule)

    raise ValueError(f'Неизвестное правило валидации подписи: {rule}')


def _validate_alignment(context: CaptionValidationContext, rule: CaptionAlignmentRule) -> bool:
    if rule.alignment == 'center':
        if context.logger is None:
            return True
        return validate_caption_centering(context.img_info, context.caption, context.logger)
    if rule.alignment == 'left':
        return validate_table_caption_padding(context)
    return True


def _validate_number(context: CaptionValidationContext, rule: CaptionNumberRule) -> bool:
    is_valid = True
    if rule.has_section:
        is_valid &= validate_table_caption_number_format(context)
    if rule.max_level is not None:
        number_parts = context.caption.number.split('.') if context.caption.number else []
        level = len(number_parts) + int(context.caption.section_number is not None)
        if level > rule.max_level:
            if context.logger:
                context.logger.error(
                    'в подписи слишком глубокая нумерация: максимум %d уровней',
                    rule.max_level,
                    extra={'bbox': context.caption.box},
                )
            is_valid &= False
    if rule.suffix_forbidden:
        caption_text = caption_words(context.caption)
        if len(caption_text) > 1 and caption_text[1].endswith('.'):
            if context.logger:
                context.logger.error(
                    'После нумерации не должно идти точки, например "Рисунок 1 - "',
                    extra={'bbox': context.caption.box},
                )
            is_valid &= False
    return is_valid


def _validate_order(context: CaptionValidationContext, rule: CaptionOrderRule) -> bool:
    if rule.kind == 'table':
        return validate_table_caption_order(context)
    if context.logger is None:
        return True
    return validate_figure_caption_order(context.caption, context.captions, context.logger)


def _validate_interval(context: CaptionValidationContext, rule: CaptionIntervalRule) -> bool:
    return validate_caption_interval(context.caption, context.logger, rule.spacing_tolerance)


def _validate_section(context: CaptionValidationContext, rule: CaptionSectionRule) -> bool:
    if context.logger is None:
        return True
    return validate_figure_caption_section(context.img_info, context.caption, context.content, context.logger)


def validate_caption_interval(
    caption: FoundedCaption,
    logger: logging.Logger | None = None,
    spacing_tolerance: float = 0.1,
) -> bool:
    if len(caption.lines) <= 1:
        return True

    for caption_line in caption.lines[:-1]:
        assert caption_line.line_interval, 'Должен присутствовать для проверки интервал строк'
        if abs(caption_line.line_interval.spacing - 1) <= spacing_tolerance:
            continue

        if logger is not None:
            logger.error(
                'Межстрочный интервал "%.1f" не соответствует единичному (для '
                'подписей рисунков и таблиц должен быть единичный межстрочный интервал)',
                caption_line.line_interval.spacing,
                extra={'bbox': caption.box},
            )
        return False

    return True


