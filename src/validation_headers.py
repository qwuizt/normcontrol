import logging

from src.constants import LEFT_MARGIN_MM
from src.models.model_text_extraction import PyMuPDFModel
from src.structures import ContentElement, ExtractedText, Paths, BoundingBox, SectionHeader
from src.tools.summary_visualization import SummaryVisualization
from src.tools import validation_text, reading
from src.tools.tools import get_pdf_page_symbol_size

logger = logging.getLogger(__name__)

def _find_line(content_item: ContentElement, lines: list[ExtractedText]) -> ExtractedText | None:
    num: str = content_item.section_number.lower()  # Введение или 1 или 1.1
    text: str = content_item.text.lower()
    text_full: str = f'{content_item.section_number} {content_item.text}'.lower()

    line_index_on_page = 0
    line_matched = None
    line_next = None
    for i, line in enumerate(lines):
        line_text = line.text.lower()
        if len(line.text) < 5:
            # Номер страницы например
            continue

        is_the_same_num = len(num) > 0 and line_text.startswith(num)
        is_header = not text  # В введении не должно быть больше текста

        is_included = line_text.startswith(text_full) or text_full.startswith(line_text)
        if (is_header and is_the_same_num) or (text and line_text.endswith(text)) or is_included:
            line_index_on_page = i
            line_matched = line
            line_next = lines[i + 1] if i + 1 < len(lines) else None
            break

    # line_next нужен, чтобы проверять следующую строку, если заголовок состоит из нескольких строк
    return line_matched, line_next, line_index_on_page


def _is_heading(el: ContentElement):
    """Функция проверяющая если элемент это заголовок (например, ВВЕДЕНИЕ)"""
    options = [
        el.section_number.lower(), # num
        el.text.lower(), # text
        f'{el.section_number} {el.text}'.lower(), # full text
    ]
    options = [el for el in options if el]

    return (
        any([SectionHeader.terms.value.startswith(option) for option in options])
        or any([SectionHeader.abbreviations.value.startswith(option) for option in options])
        or any([SectionHeader.intro.value.startswith(option) for option in options])
        or any([SectionHeader.conclusion.value.startswith(option) for option in options])
        or any([SectionHeader.resources.value.startswith(option) for option in options])
    )


def _is_application(el: ContentElement) -> bool:
    """Функция проверяющая, если элемент – это приложение (например, Приложение А)"""
    return el.section_number.lower().startswith(SectionHeader.app.value)


def _is_section(el: ContentElement) -> bool:
    text = el.section_number.strip() or ' '
    return text.isnumeric()


def _is_section_and_subsections(el: ContentElement) -> bool:
    # Только для раздела "1." и подраздела "1.1"
    text = el.section_number.strip() or ' '
    return _is_section(el) or text[0].isnumeric() and text.count('.') <= 1


def calculate_required_left_offset(el: ContentElement) -> int:
    if _is_heading(el) or _is_application(el) or _is_section(el):
        return 0

    text = el.section_number.strip() or ' '
    return text.count('.') * 2  # Подразделы, пункты и тд должны быть смещены на 2*i символа


def _is_start_from_begin_page(el: ContentElement, tolerance: int = 2) -> bool:
    assert len(el.lines) > 0, 'Функция работает только найденными строками содержания'
    bbox: BoundingBox = el.lines[0].bbox

    required_left = LEFT_MARGIN_MM * 72 / 25.4  # ≈ 85.039 pt
    return abs(bbox.left - required_left) < tolerance


def _is_start_from_offset(el: ContentElement, offset: int, sym_size: float, tolerance: int = 2) -> bool:
    assert len(el.lines) > 0, 'Функция работает только найденными строками содержания'
    bbox: BoundingBox = el.lines[0].bbox

    required_left = LEFT_MARGIN_MM * 72 / 25.4 + offset * sym_size  # ≈ 85.039 pt
    return abs(bbox.left - required_left) < tolerance


def _is_must_be_centered(el: ContentElement) -> bool:
    """Введение, заключение и тд должны быть по центру"""
    return _is_heading(el) or _is_application(el)


def _is_must_be_bold(el: ContentElement) -> bool:
    """Введение, заключение и тд + заголовки и подзаголовки должны быть жирные"""
    return _is_heading(el) or _is_application(el) or _is_section_and_subsections(el)


def _is_must_be_left_offset(el: ContentElement) -> bool:
    """Заголовки, разделы и подразделы должны начинаться с абзацного отступа"""
    text = el.section_number.strip() or ' '
    return el.section_number.isnumeric() or text[0].isnumeric() and text.count('.') >= 1


def find_last_title_line(
    lines: list[ExtractedText],
    line: ExtractedText,
    content: ContentElement,
    *,
    is_multiple_title: bool
) -> ExtractedText | None:
    if not is_multiple_title:
        return line

    index = 0
    for line_current in lines:
        if line.text == line_current.text:
            break
        index += 1

    if index >= len(lines) - 1:
        # ну нет дальше ничего, хз пока что с этим делать
        return None

    text_content = f'{content.section_number} {content.text}'.strip().lower()
    text_content = text_content.replace(line.text.strip().lower(), '').strip()  # убрали текст первой строки заголовка

    index_next = index + 1
    line_next = lines[index_next]
    text = line_next.text.strip().rstrip('.').lower()
    while not text_content.endswith(text):
        if text not in text_content:
            line_next = None  # почему-то вторая строка заголовка отличается
            break

        if index_next - index >= 5 or index >= len(lines) - 1:
            line_next = None  # когда всё пошло наперекосяк и ничего не ищется
            break

        text_content = text_content.replace(text, '').strip()

        index_next += 1
        line_next = lines[index_next]
        text = line_next.text.strip().rstrip('.').lower()

    return line_next


def _validate_order(content: list[ContentElement], sv: SummaryVisualization, logger: logging.Logger) -> None:
    current_number: str | None = None
    for el in sorted(content, key=lambda el: (el.content_page_number, el.bbox.top)):
        sv.set_meta(el.content_page_number, element=None)
        if _is_heading(el) or _is_application(el):
            continue

        section_number: str = el.section_number
        if not section_number:
            logger.warning(
                'Невозможно проверить строку содержания. Не был найден номер '
                'раздела/подраздела. Такое иногда бывает, что номер хранится как изображение.',
                extra={'bbox': el.bbox},
            )
            continue

        if current_number is None:  # Просто для первой записи
            current_number = section_number
            continue

        try:
            section_number_int = int(section_number.replace('.', ''))
            current_number_int = int(current_number.replace('.', ''))
        except ValueError:  # Для приложений
            current_number = section_number
            continue

        diff = section_number_int - current_number_int
        if -9 < diff < 0:  # Например, 1.1 - 1.3
            logger.warning('Номер "%s" идет после номера "%s", но является меньшим',
                           section_number, current_number, extra={'bbox': el.bbox})
        elif 1 < diff < 10:  # Например, 1.3 - 1.1
            logger.warning('Номер "%s" идет после номера "%s", но больше не на один',
                           section_number, current_number, extra={'bbox': el.bbox})

        current_number = section_number


def validate_headers(
    paths_object: Paths, content: list[ContentElement], sv: SummaryVisualization | None = None
) -> None:
    # Проверить страницу содержания (заголовок на странице содержания)
    if sv is None:
        sv = SummaryVisualization(paths_object.path_summary, validate_headers.__name__)
    sv.add_logger_handler(logger=logger)

    if not content:
        logger.warning('Не найдены пункты содержания страницы для проверки')
        return

    _validate_order(content, sv, logger)  # И тут может быть ошибка

    with PyMuPDFModel(paths_object.path_pdf) as pdf:
        page = pdf.get_page(content[0].content_page_number)
        space_size: float = get_pdf_page_symbol_size(page, sym=' ')

        for content_item in content:
            logger.info("Process item %s", content_item.section_number)

            if content_item.page_number is None:
                logger.info("Ignore item %s, because not found page_number for element.", str(content_item))
                continue

            # Проверка отступа элемента содержания (на странице содержания)
            sv.set_meta(content_item.content_page_number, element=None)
            required_offset = calculate_required_left_offset(content_item)
            if required_offset == 0:
                if not _is_start_from_begin_page(content_item):
                    logger.warning('Заголовки структурных элементов и разделы должны начинаться с начала '
                                   'страницы (30 мм. от левого края)', extra={'bbox': content_item.bbox})
            else:
                text_error = ('Наименования подразделов в содержании записывают с абз. отступа, '
                              'равного двум печатным знакам. Наименования пунктов - четырем печатным знакам.')
                if not _is_start_from_offset(content_item, required_offset, sym_size=space_size, tolerance=2):
                    logger.warning(text_error, extra={'bbox': content_item.bbox})

            page_index_display: int = content_item.page_number
            page_index: int = page_index_display - 1

            try:
                page_content_box: BoundingBox = pdf.get_page_content_bbox(page_index)
            except IndexError:
                logger.error('Страница %d отсутствует в документе', page_index,
                             extra={'bbox': content_item.bbox})
                continue

            if _is_heading(content_item) and not content_item.section_number:
                continue

            lines = pdf.extract_lines(page_index, sort=True)
            line, line_next, line_index_on_page = _find_line(content_item, lines)

            if line is None:
                logger.error('На странице %d не был найден заголовок. Возможно имеется '
                             'опечатка. А может быть алгоритм допустил ошибку. В любом случае '
                             'настоятельно рекомендуется сверить данный пункт с заголовком', page_index_display,
                             extra={'bbox': content_item.bbox})
                continue

            logger.info('Found line "%s" on page %d', line.text, page_index)
            sv.set_meta(page_index, element=line)

            must_be_centered: bool = _is_must_be_centered(content_item)
            must_be_left_offset: bool = _is_must_be_left_offset(content_item)

            must_be_bold: bool = _is_must_be_bold(content_item)
            is_multiple_title: bool = False

            is_determined = True
            if must_be_centered:
                is_centered = validation_text.text_is_centered(line.bbox, page_content_box.left,
                                                               page_content_box.right, logger=logger)
                if not is_centered:
                    logger.error('Заголовки структурных элементов должны быть отцентрованы',
                                 extra={'bbox': line.bbox})
            elif must_be_left_offset:
                left_offset_is_correct = validation_text.text_is_left_offset(line, page_content_box.left,
                                                                             logger=logger, offset=1.25)
                if not left_offset_is_correct:
                    logger.error('Разделы, подразделы и пункты должны иметь абзацный отступ = 1.25 см',
                                 extra={'bbox': line.bbox})

                is_multiple_title = len(f'{content_item.section_number} {content_item.text}') - len(line.text) > 5
                if is_multiple_title:
                    left_offset_is_correct = validation_text.text_is_left_offset(
                        line_next, page_content_box.left, logger=logger, offset=0
                    )
                    if not left_offset_is_correct:
                        logger.error(
                            'Для многострочных разделов, подразделов и пунктов вторая строка должны идти '
                            'без абзацного отступа',
                            extra={'bbox': line_next.bbox},
                        )
            else:
                is_determined = False
                logger.warning('Элемент может быть либо структурным элементом, либо разделом и тд. В данной '
                               'ситуации элемент не был определен. Возможно проблема с номером.',
                               extra={'bbox': line.bbox})

            if is_determined:
                text_is_bold_ = validation_text.text_is_bold(line, logger=logger)
                if must_be_bold and not text_is_bold_:
                    logger.error('заголовки структурных элементов (ВВЕДЕНИЕ, ЗАКЛЮЧЕНИЕ и тд), '
                                 'разделы и подразделы должны быть выделены жирным',
                                 extra={'bbox': line.bbox})
                elif not must_be_bold and text_is_bold_:
                    logger.error('пункты и подпункты не должны выделяться жирным. Выделяются жирным только '
                                 'заголовки структурных элементов (ВВЕДЕНИЕ, ЗАКЛЮЧЕНИЕ и тд), разделы и подразделы',
                                 extra={'bbox': line.bbox})

            if validation_text.text_is_underline(line, logger=logger):
                logger.error('заголовки структурных элементов (ВВЕДЕНИЕ, ЗАКЛЮЧЕНИЕ и тд), '
                             'разделы, подразделы и пункты НЕ должны быть подчеркнуты', extra={'bbox': line.bbox})

            last_line = find_last_title_line(lines, line, content_item, is_multiple_title=is_multiple_title)
            if last_line is None:
                logger.warning('не удалось определить окончания заголовка, чтобы проверить, '
                               'оканчивается ли он на точку.', extra={'bbox': line.bbox})
            elif last_line.text.endswith('.'):
                logger.error(
                    'заголовки структурных элементов (ВВЕДЕНИЕ, ЗАКЛЮЧЕНИЕ и тд), '
                    'разделы, подразделы и пункты не должны заканчиваться точкой',
                    extra={'bbox': last_line.bbox},
                )

            if (_is_heading(content_item) or _is_section(content_item)) and line_index_on_page > 0:
                logger.error(
                    'заголовки структурных элементов (ВВЕДЕНИЕ, ЗАКЛЮЧЕНИЕ и тд) и '
                    'разделы должны начинаться с нового листа',
                    extra={'bbox': line.bbox},
                )

    sv.save()

def validate_headers_by_files(paths_object: Paths) -> None:
    content: list[ContentElement] = reading.read_content_file(paths_object.path_file_content)

    validate_headers(paths_object, content)
