import logging

from src.structures import BoundingBox, ExtractedText

INCH_TO_CM = 2.54  # 1 дюйм это 2.54 см
INCH_TO_POINTS = 72  # 1 дюйм = 72 пункта
                     # x дюйм = N пунктов

CM_TO_POINTS = INCH_TO_POINTS / INCH_TO_CM  # 1 см это вот это число пунктов
CM_1_25_TO_POINTS = CM_TO_POINTS * 1.25


def text_is_centered(box: BoundingBox, margin_left: float, content_width: float, logger: logging.Logger) -> bool:
    delta_left = box.left - margin_left
    delta_right = content_width - box.right

    if abs(delta_right - delta_left) > 20:  # с полями
        if 0.9 < (delta_left - delta_right) / CM_1_25_TO_POINTS < 1.1:
            logger.warning('у вас слева расстояние больше на абзацный отступ (1.25 см), '
                           'возможно проблема в этом', extra={'bbox': box})
        else:
            logger.debug('margin слева %d, ширина контента %d', margin_left, content_width)
            logger.warning(
                'отступ слева "%d", отступ справа "%d"', delta_left, delta_right, extra={'bbox': box},
            )
        return False

    return True


def calc_left_offset(box: BoundingBox, margin_left: float, logger: logging.Logger) -> float:
    diff = box.left - margin_left
    # 1 дюйм = 2.54 см = 72 пункта
    #                x = diff пунктов
    return (diff * INCH_TO_CM) / INCH_TO_POINTS


def text_is_bold(text: ExtractedText, logger: logging.Logger) -> bool:
    return any(map(lambda v: 'bold' in v.lower(), text.meta_info.font_list))


def text_is_underline(text: ExtractedText, logger: logging.Logger) -> bool:
    return text.meta_info.underscored


def text_is_left_offset(
    line: ExtractedText,
    content_left: float,
    logger: logging.Logger,
    offset: float = 1.25,
    precision: float = 0.05,
) -> bool:
    left_offset = calc_left_offset(line.bbox, content_left, logger=logger)
    diff = abs(offset - left_offset)
    return diff < precision
