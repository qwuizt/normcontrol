from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pandas.errors import EmptyDataError

from src.models.model_text_extraction import PyMuPDFModel
from src.structures import BoundingBox, ExtractedText, PageClass, Paths
from src.tools.reading import read_pdf_structure
from src.tools.summary_visualization import SummaryVisualization

logger_ = logging.getLogger(__name__)


REFERENCE_TITLE_RE = re.compile(
    r'^\s*список\s+(?:'
    r'использованных\s+источников|'
    r'литературы|'
    r'использованной\s+литературы|'
    r'использованных\s+источников\s+и\s+литературы'
    r')\s*$',
    re.IGNORECASE,
)
DOT_NUMBER_RE = re.compile(r'^\s*(?P<number>\d{1,3})\.\s*(?P<body>.*)$')
BRACKET_NUMBER_RE = re.compile(r'^\s*\[(?P<number>\d{1,3})]\s*(?P<body>.*)$')
DATE_START_RE = re.compile(r'^\s*\d{1,2}\.\d{1,2}\.\d{2,4}\b')
YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')
URL_RE = re.compile(r'(?:https?://|www\.)\S+', re.IGNORECASE)
URL_MARKER_RE = re.compile(r'\bURL\s*:', re.IGNORECASE)
ACCESS_DATE_RE = re.compile(
    r'дата\s+обращения\s*:?\s*(?:\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})',
    re.IGNORECASE,
)
ACCESS_DATE_STRICT_RE = re.compile(r'\(дата\s+обращения:\s*\d{2}\.\d{2}\.\d{4}\)', re.IGNORECASE)
ACCESS_DATE_MARKER_RE = re.compile(r'дата\s+обращения', re.IGNORECASE)
ARTICLE_PAGES_RE = re.compile(r'(?:\bС\.|\bP\.|\bPp\.?)\s*\d+\s*[-–—]\s*\d+', re.IGNORECASE)
BOOK_PAGES_RE = re.compile(r'\b(?:[IVXLCDM]+,\s*)?\d+\s*(?:с\.|л\.|т\.)', re.IGNORECASE)
VOLUME_OR_NUMBER_RE = re.compile(r'(?:\bN\b|№|\bNo\.|\bVol\.|\bТ\.|\bВып\.)\s*\d+', re.IGNORECASE)
GOST_NUMBER_RE = re.compile(r'\bГОСТ\s+[\d.]+-?\d{0,4}\b', re.IGNORECASE)
LEGAL_ACT_RE = re.compile(
    r'\b(?:конституция|кодекс|законы|закон|приказ|федеральный\s+закон|постановление|распоряжение|нп-\d+)\b',
    re.IGNORECASE,
)
LEGAL_DATE_RE = re.compile(
    r'(?:от\s+)?\d{1,2}\s+[а-яё]+\s+\d{4}\s+г\.|\d{2}\.\d{2}\.\d{4}',
    re.IGNORECASE,
)
LEGAL_NUMBER_RE = re.compile(r'\b(?:N|№)\s*[\wА-Яа-яЁё/-]+')
PLACE_PUBLISHER_RE = re.compile(
    r'(?:'
    r'\bМ\.?\s*:|'
    r'\bСПб\.?\s*:|'
    r'\b(?:Москва|Санкт-Петербург|Ленинград)\s*[:,]\s*|'
    r'[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z.-]+\s*:\s*[^,]+,\s*(?:19|20)\d{2}|'
    r'\b(?:Москва|Санкт-Петербург|Ленинград|Самара|Вологда|Барнаул|Уфа|Томск|Новосибирск)\s*,\s*(?:19|20)\d{2}'
    r')'
)
BOOK_MARKER_RE = re.compile(
    r'\b(?:учебник|учебное\s+пособие|практикум|монография|словарь|энциклопедия|изд-?во|под\s+ред\.|'
    r'в\s+\d+\s+т\.|т\.\s*\d+|сборник|официальное\s+издание)\b',
    re.IGNORECASE,
)
PATENT_RE = re.compile(r'\bпатент\s*(?:N|№)?\s*\d+', re.IGNORECASE)
DISSERTATION_RE = re.compile(r'\b(?:диссертация|автореферат)\b', re.IGNORECASE)
CONFERENCE_RE = re.compile(r'\b(?:материалы|конференц|сборник\s+тезисов|сб\.\s*науч\.\s*тр\.)', re.IGNORECASE)
DOUBLED_PUNCT_RE = re.compile(r'(?<!\.)\.\.(?!\.)|,,|//\s*//')
URL_WITH_SPACE_RE = re.compile(r'https?://\S*\s+[A-Za-zА-Яа-яЁё0-9.-]+\.[A-Za-zА-Яа-яЁё]{2,}')


class IssueLevel(Enum):
    ERROR = 'error'
    WARNING = 'warning'


class NumberingStyle(Enum):
    DOT = 'dot'
    BRACKET = 'bracket_invalid'


class SourceType(Enum):
    ARTICLE = 'article'
    BOOK = 'book'
    WEB = 'web'
    STANDARD = 'standard'
    LEGAL_ACT = 'legal_act'
    PATENT = 'patent'
    DISSERTATION = 'dissertation'
    CONFERENCE = 'conference'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ReferenceStart:
    number: int
    number_raw: str
    numbering_style: NumberingStyle
    body: str


@dataclass(frozen=True)
class ReferenceIssue:
    level: IssueLevel
    message: str
    bbox: BoundingBox
    page_num: int


@dataclass(frozen=True)
class ReferenceValidationResult:
    reference_pages: list[int]
    entries: list[ReferenceEntry]
    issues: list[ReferenceIssue]
    orphan_lines: list[ExtractedText]
    section_found: bool


@dataclass
class ReferenceEntry:
    number: int
    number_raw: str
    numbering_style: NumberingStyle
    lines: list[ExtractedText]

    @property
    def page_num(self) -> int:
        return self.lines[0].page_num

    @property
    def text(self) -> str:
        return normalize_spaces(' '.join(line.text for line in self.lines))

    @property
    def body_text(self) -> str:
        return normalize_spaces(
            parse_reference_start(self.lines[0].text).body + ' ' + ' '.join(line.text for line in self.lines[1:])
        )

    @property
    def bbox(self) -> BoundingBox:
        return self.bbox_on_page(self.page_num)

    def bbox_on_page(self, page_num: int) -> BoundingBox:
        """
        Вернуть область записи только на одной странице.

        Библиографическая запись может переноситься через границу страницы.
        Нельзя объединять координаты строк с разных страниц: иначе верхние
        строки следующей страницы расширяют bbox на текущей странице почти до
        полного листа и визуализация подсвечивает несколько источников сразу.
        """
        page_lines = [line for line in self.lines if line.page_num == page_num]
        if not page_lines:
            page_lines = [self.lines[0]]

        bbox = page_lines[0].bbox
        for line in page_lines[1:]:
            bbox = bbox | line.bbox
        return bbox


def task_validate_references(
    ti,
    workdir: Path | str | None = None,
    front_kwargs: str | None = None,
) -> SummaryVisualization:
    """
    Исследовательская точка входа для проверки списка источников.

    Функция повторяет стиль задач из ``nodes``: принимает подготовленную
    рабочую директорию ``workdir`` и записывает предупреждения/ошибки через
    ``SummaryVisualization``.
    """
    if workdir is None:
        workdir = Path(ti.xcom_pull('task_start_dag'))
    return validate_references(Paths.create(Path(workdir)))


def validate_references(paths_object: Paths, sv: SummaryVisualization | None = None) -> SummaryVisualization:
    """
    Запустить полный детерминированный пайплайн проверки источников.

    Пайплайн читает распознанную структуру документа, извлекает строки со
    страниц списка источников, группирует их в библиографические записи,
    проверяет правила уровня списка и отдельных записей, затем сохраняет
    артефакты визуализации для итогового PDF.
    """
    if sv is None:
        clear_previous_summary_artifacts(paths_object.path_summary / validate_references.__name__)
        sv = SummaryVisualization(paths_object.path_summary, validate_references.__name__)

    logger = sv.add_logger_handler(logger_)
    result = collect_reference_validation_result(paths_object)

    if not result.section_found:
        sv.set_meta(0, element=None)
        logger.warning('Раздел "Список использованных источников" не найден')
        sv.save()
        return sv

    emit_issues(result.issues, sv, logger)
    sv.save()
    return sv


def collect_reference_validation_result(
    paths_object: Paths,
    *,
    include_rule_issues: bool = True,
) -> ReferenceValidationResult:
    """
    Выполнить анализ списка источников и вернуть данные для интеграции.

    В отличие от ``validate_references`` эта функция не пишет summary и не
    создает визуализацию. Ее можно использовать в DOCX-пайплайне, чтобы
    связать PDF-замечания с номерами библиографических записей.
    """
    issues: list[ReferenceIssue] = []
    reference_pages = get_reference_pages(paths_object)
    if not reference_pages:
        return ReferenceValidationResult(
            reference_pages=[],
            entries=[],
            issues=[],
            orphan_lines=[],
            section_found=False,
        )

    lines = extract_reference_lines(paths_object, reference_pages)
    body_lines, orphan_lines = filter_reference_body_lines(lines)
    entries, grouping_orphans = group_reference_entries(body_lines)
    orphan_lines.extend(grouping_orphans)

    if include_rule_issues:
        issues.extend(validate_reference_list(entries, orphan_lines))
        for entry in entries:
            issues.extend(validate_reference_entry(entry))
        issues.extend(find_near_duplicate_entries(entries))
        issues.extend(validate_reference_alignment(entries))

    return ReferenceValidationResult(
        reference_pages=reference_pages,
        entries=entries,
        issues=issues,
        orphan_lines=orphan_lines,
        section_found=True,
    )


def clear_previous_summary_artifacts(path_function: Path) -> None:
    """
    Удалить старые артефакты этой проверки перед новым запуском.

    ``SummaryVisualization`` сохраняет файлы с hash в имени, поэтому без
    очистки визуализатор читает старые и новые замечания одновременно. Функция
    удаляет только файлы текущего исследовательского валидатора и не трогает
    результаты других проверок.
    """
    if not path_function.exists():
        return

    for pattern in ('*.notes.*.csv', '*.messages.*.csv', '*.figures.*.json'):
        for path_artifact in path_function.glob(pattern):
            path_artifact.unlink()


def get_reference_pages(paths_object: Paths) -> list[int]:
    """
    Вернуть индексы страниц раздела со списком источников.

    Классы страниц берутся из ``structure.csv``, который создается задачей
    ``task_detection_document_structure``. Если файл отсутствует, будет
    выброшена стандартная ошибка ``Paths.check_exists``.
    """
    paths_object.check_exists(['path_file_structure'])
    try:
        df_structure = read_pdf_structure(paths_object.path_file_structure)
    except EmptyDataError:
        logger_.warning('Файл структуры документа пуст: %s', paths_object.path_file_structure)
        return []

    if df_structure.empty or 'page_type' not in df_structure.columns:
        return []

    return PageClass.get_page_numbers(df_structure, PageClass.references)


def extract_reference_lines(paths_object: Paths, reference_pages: list[int]) -> list[ExtractedText]:
    """
    Извлечь текстовые строки со всех страниц списка источников.

    Каждый объект ``ExtractedText`` содержит текст строки и координаты в PDF.
    Эти координаты затем используются, чтобы привязать сообщение проверки к
    конкретной библиографической записи в аннотированном PDF.
    """
    paths_object.check_exists(['path_pdf'])

    lines: list[ExtractedText] = []
    with PyMuPDFModel(paths_object.path_pdf) as extractor:
        for page_num in reference_pages:
            lines.extend(extractor.extract_lines(page_num, sort=True))
    return lines


def filter_reference_body_lines(lines: list[ExtractedText]) -> tuple[list[ExtractedText], list[ExtractedText]]:
    """
    Разделить извлеченные строки на тело списка источников и лишние строки.

    Функция убирает заголовок раздела и строки, похожие на номера страниц.
    Строки до заголовка или строки, не похожие на часть нумерованной записи,
    возвращаются как ``orphan_lines`` для последующего предупреждения.
    """
    body_lines: list[ExtractedText] = []
    orphan_lines: list[ExtractedText] = []
    title_seen = False

    for line in lines:
        text = normalize_spaces(line.text)
        if not text:
            continue
        if REFERENCE_TITLE_RE.fullmatch(text):
            title_seen = True
            continue
        if text.isdigit():
            continue
        if title_seen or parse_reference_start(text):
            body_lines.append(line)
        else:
            orphan_lines.append(line)

    return body_lines, orphan_lines


def parse_reference_start(text: str) -> ReferenceStart | None:
    """
    Распознать маркер начала библиографической записи.

    Основной корректный формат нумерации - ``1.``. Формат ``[1]`` распознается
    только как ошибочный маркер, чтобы не потерять саму запись. Функция возвращает
    номер, исходный текст маркера, стиль нумерации и текст после маркера.
    Если строка не является началом записи, возвращается ``None``.
    """
    if DATE_START_RE.match(text):
        return None

    for pattern, style in ((DOT_NUMBER_RE, NumberingStyle.DOT), (BRACKET_NUMBER_RE, NumberingStyle.BRACKET)):
        match = pattern.match(text)
        if match is None:
            continue

        number_raw = match.group(0)[: match.start('body')].strip()
        return ReferenceStart(
            number=int(match.group('number')),
            number_raw=number_raw,
            numbering_style=style,
            body=match.group('body').strip(),
        )

    return None


def group_reference_entries(lines: list[ExtractedText]) -> tuple[list[ReferenceEntry], list[ExtractedText]]:
    """
    Объединить физические строки PDF в логические записи источников.

    Новая запись начинается со строки с поддерживаемым маркером нумерации.
    Следующие строки без маркера считаются продолжением текущей записи.
    Строки до первой нумерованной записи возвращаются как лишние строки.
    """
    entries: list[ReferenceEntry] = []
    orphans: list[ExtractedText] = []
    current: ReferenceEntry | None = None
    seen_numbers: set[int] = set()

    for line in lines:
        start = parse_reference_start(line.text)
        if start is not None and is_new_reference_start(start, current, seen_numbers):
            if current is not None:
                entries.append(current)
                seen_numbers.add(current.number)
            current = ReferenceEntry(
                number=start.number,
                number_raw=start.number_raw,
                numbering_style=start.numbering_style,
                lines=[line],
            )
            continue

        if current is None:
            orphans.append(line)
        else:
            current.lines.append(line)

    if current is not None:
        entries.append(current)

    return entries, orphans


def is_new_reference_start(
    start: ReferenceStart,
    current: ReferenceEntry | None,
    seen_numbers: set[int],
) -> bool:
    """
    Проверить, является ли найденный номер началом новой записи.

    Внутри описаний часто встречаются DOI и диапазоны страниц, которые после
    переноса строки выглядят как ``10.1234...`` или ``937. - DOI ...``. Такие
    строки нельзя отделять от текущего источника. Основной критерий нового
    источника - ожидаемый следующий номер; повторный номер разрешается только
    для явных дублей с нормальным началом библиографического текста.
    """
    if current is None:
        return True
    if start.number == current.number + 1:
        return True
    if start.number == current.number or start.number in seen_numbers:
        return body_looks_like_reference_start(start.body)
    return False


def body_looks_like_reference_start(body: str) -> bool:
    """
    Проверить текст после номера на похожесть на начало библиографической записи.

    DOI, диапазоны страниц и обрывки URL обычно начинаются с цифры, дефиса,
    косой черты или точки. Фамилия автора, название организации или заглавие
    источника обычно начинаются с буквы или кавычки.
    """
    body = body.strip()
    if not body:
        return False
    return bool(re.match(r'[A-ZА-ЯЁ"«]', body))


def validate_reference_list(entries: list[ReferenceEntry], orphan_lines: list[ExtractedText]) -> list[ReferenceIssue]:
    """
    Проверить правила, относящиеся ко всему списку источников.

    Функция проверяет, что список не пустой, нумерация начинается с единицы,
    номера идут последовательно, не повторяются, а маркер номера оформлен
    через точку: ``1.``.
    """
    issues: list[ReferenceIssue] = []

    if not entries:
        bbox = orphan_lines[0].bbox if orphan_lines else BoundingBox(0, 0, 0, 0)
        page_num = orphan_lines[0].page_num if orphan_lines else 0
        return [ReferenceIssue(IssueLevel.ERROR, 'Не найдены пронумерованные записи списка источников', bbox, page_num)]

    for orphan in orphan_lines:
        issues.append(
            ReferenceIssue(
                IssueLevel.WARNING,
                'Строка списка источников не относится ни к одной пронумерованной записи',
                orphan.bbox,
                orphan.page_num,
            )
        )

    first = entries[0]
    if first.number != 1:
        issues.append(
            ReferenceIssue(
                IssueLevel.ERROR,
                f'Нумерация списка источников должна начинаться с 1, найдено: {first.number}',
                first.bbox,
                first.page_num,
            )
        )

    for entry in entries:
        if entry.numbering_style == NumberingStyle.BRACKET:
            issues.append(
                ReferenceIssue(
                    IssueLevel.ERROR,
                    f'Неверный формат номера источника {entry.number}: используйте "{entry.number}." вместо "{entry.number_raw}"',
                    entry.bbox,
                    entry.page_num,
                )
            )

    seen: dict[int, ReferenceEntry] = {}
    for entry in entries:
        if entry.number in seen:
            issues.append(
                ReferenceIssue(
                    IssueLevel.ERROR,
                    f'Номер источника {entry.number} встречается повторно',
                    entry.bbox,
                    entry.page_num,
                )
            )
        seen[entry.number] = entry

    numbers = [entry.number for entry in entries]
    expected_numbers = list(range(1, max(numbers) + 1))
    missing = sorted(set(expected_numbers) - set(numbers))
    for number in missing:
        anchor = find_next_entry(entries, number)
        issues.append(
            ReferenceIssue(
                IssueLevel.ERROR,
                f'В нумерации списка источников пропущен номер {number}',
                anchor.bbox,
                anchor.page_num,
            )
        )

    return issues


def validate_reference_entry(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить одну библиографическую запись.

    Сначала выполняются общие проверки. Затем по текстовым маркерам
    определяется тип источника и применяются проверки для конкретного типа:
    электронный источник, статья, книга, стандарт, нормативный акт,
    материал конференции или неизвестный источник.
    """
    source_type = detect_source_type(entry)
    issues = validate_common_entry_rules(entry, source_type=source_type)

    match source_type:
        case SourceType.WEB:
            issues.extend(validate_web_reference(entry))
        case SourceType.ARTICLE:
            issues.extend(validate_article_reference(entry))
        case SourceType.BOOK:
            issues.extend(validate_book_reference(entry))
        case SourceType.STANDARD:
            issues.extend(validate_standard_reference(entry))
        case SourceType.LEGAL_ACT:
            issues.extend(validate_legal_act_reference(entry))
        case SourceType.PATENT:
            issues.extend(validate_patent_reference(entry))
        case SourceType.DISSERTATION:
            issues.extend(validate_dissertation_reference(entry))
        case SourceType.CONFERENCE:
            issues.extend(validate_conference_reference(entry))
        case SourceType.UNKNOWN:
            issues.append(warn(entry, 'Тип библиографического источника не распознан'))

    return issues


def validate_common_entry_rules(
    entry: ReferenceEntry,
    *,
    source_type: SourceType | None = None,
) -> list[ReferenceIssue]:
    """
    Проверить общие правила оформления для всех типов источников.

    Эти проверки находят отсутствие года, отсутствие точки в конце,
    подозрительно короткую запись, повторяющуюся пунктуацию, несбалансированные
    скобки, URL с пробелами и несогласованные пары URL/дата обращения.
    """
    text = entry.text
    body = entry.body_text
    issues: list[ReferenceIssue] = []

    if not body:
        issues.append(error(entry, 'После номера источника отсутствует текст библиографической записи'))
    # if len(body) < 25:
    #     issues.append(warn(entry, 'Библиографическая запись выглядит слишком короткой'))
    if not YEAR_RE.search(text):
        issues.append(error(entry, 'В библиографической записи не найден год'))
    if not text.rstrip().endswith('.'):
        issues.append(warn(entry, 'Библиографическая запись должна заканчиваться точкой'))
    if '  ' in ' '.join(line.text for line in entry.lines):
        issues.append(warn(entry, 'В библиографической записи найден двойной пробел'))
    if DOUBLED_PUNCT_RE.search(text):
        issues.append(warn(entry, 'В библиографической записи найдена подозрительная повторяющаяся пунктуация'))
    if not brackets_are_balanced(text):
        issues.append(warn(entry, 'В библиографической записи есть незакрытые скобки'))
    if URL_WITH_SPACE_RE.search(text):
        issues.append(error(entry, 'URL содержит пробел и может быть некорректным'))
    if source_type != SourceType.WEB:
        if URL_RE.search(text) and not ACCESS_DATE_MARKER_RE.search(text):
            issues.append(error(entry, 'Для электронного источника не найдена дата обращения'))
        if ACCESS_DATE_MARKER_RE.search(text) and not ACCESS_DATE_STRICT_RE.search(text):
            issues.append(error(entry, 'Дата обращения должна быть оформлена так: "(дата обращения: ДД.ММ.ГГГГ)"'))
        if ACCESS_DATE_MARKER_RE.search(text) and not (URL_RE.search(text) or URL_MARKER_RE.search(text)):
            issues.append(warn(entry, 'Найдена дата обращения, но не найден URL источника'))

    return issues


def validate_web_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить электронный или интернет-источник.

    Минимально ожидаются маркер URL или сам URL, а также дата обращения.
    Дата обращения должна быть оформлена строго: ``(дата обращения: ДД.ММ.ГГГГ)``.
    """
    text = entry.text
    issues: list[ReferenceIssue] = []

    if not URL_MARKER_RE.search(text):
        issues.append(warn(entry, 'Для электронного источника рекомендуется указывать маркер "URL:"'))
    if not URL_RE.search(text):
        issues.append(error(entry, 'Для электронного источника не найден URL'))
    if not ACCESS_DATE_MARKER_RE.search(text):
        issues.append(error(entry, 'Для электронного источника не найдена дата обращения'))
    elif not ACCESS_DATE_STRICT_RE.search(text):
        issues.append(error(entry, 'Дата обращения должна быть оформлена так: "(дата обращения: ДД.ММ.ГГГГ)"'))

    return issues


def validate_article_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить источник, похожий на журнальную статью.

    Правила ожидают разделитель ``//`` между названием статьи и названием
    издания, маркеры тома/номера выпуска и диапазон страниц в русской или
    английской записи.
    """
    text = entry.text
    issues: list[ReferenceIssue] = []

    if '//' not in text:
        issues.append(error(entry, 'Для статьи не найден разделитель "//" между названием и изданием'))
    if not ARTICLE_PAGES_RE.search(text):
        issues.append(warn(entry, 'Для статьи не найден диапазон страниц вида "С. 10 - 20" или "P. 10 - 20"'))
    if not VOLUME_OR_NUMBER_RE.search(text):
        issues.append(warn(entry, 'Для статьи не найден номер, том или выпуск издания'))

    return issues


def validate_book_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить книгу, учебник или источник, похожий на монографию.

    Функция проверяет наличие общего количества страниц и признака места
    издания или издательства. Год проверяется в общих правилах.
    """
    text = entry.text
    issues: list[ReferenceIssue] = []

    if not BOOK_PAGES_RE.search(text):
        issues.append(warn(entry, 'Для книги не найден общий объем: укажите количество страниц или томов'))
    if not PLACE_PUBLISHER_RE.search(text):
        issues.append(warn(entry, 'Для книги не найдено место издания или издательство'))

    return issues


def validate_standard_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить запись стандарта, в первую очередь источники типа ГОСТ.

    Функция проверяет наличие номера ГОСТ и видимого названия после
    идентификатора стандарта. Общие проверки года и точки в конце выполняются
    отдельно.
    """
    text = entry.text
    issues: list[ReferenceIssue] = []

    if not GOST_NUMBER_RE.search(text):
        issues.append(error(entry, 'Для стандарта не найден номер ГОСТ'))
    if len(entry.body_text.split('.')) < 2:
        issues.append(warn(entry, 'Для стандарта не найдено название после номера'))

    return issues


def validate_patent_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить патентное описание.

    По примерам ГОСТ ожидаются номер патента, страна, регистрационные даты
    заявки/публикации и объем описания в страницах.
    """
    text = entry.text
    issues: list[ReferenceIssue] = []

    if not PATENT_RE.search(text):
        issues.append(error(entry, 'Для патента не найден номер патента'))
    if 'российская федерация' not in text.lower() and 'russian federation' not in text.lower():
        issues.append(warn(entry, 'Для патента не найдена страна выдачи'))
    if 'заявлено' not in text.lower():
        issues.append(warn(entry, 'Для патента не найдена дата заявки'))
    if 'опубл' not in text.lower():
        issues.append(warn(entry, 'Для патента не найдена дата публикации'))
    if not BOOK_PAGES_RE.search(text):
        issues.append(warn(entry, 'Для патента не найден объем описания в страницах'))

    return issues


def validate_dissertation_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить диссертацию или автореферат.

    Ожидаются маркер типа работы, специальность и место/объем. Год проверяется
    общими правилами.
    """
    text = entry.text
    issues: list[ReferenceIssue] = []

    if not DISSERTATION_RE.search(text):
        issues.append(error(entry, 'Для диссертации или автореферата не найден тип работы'))
    if 'специальность' not in text.lower():
        issues.append(warn(entry, 'Для диссертации не найдена специальность'))
    if not (BOOK_PAGES_RE.search(text) or PLACE_PUBLISHER_RE.search(text)):
        issues.append(warn(entry, 'Для диссертации не найдено место издания или объем'))

    return issues


def validate_legal_act_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить правовой или нормативный документ.

    Функция ищет дату и номер документа. Если нормативный акт одновременно
    является электронным источником, для него также применяются общие правила
    проверки URL и даты обращения.
    """
    text = entry.text
    lower = text.lower()
    issues: list[ReferenceIssue] = []
    number_expected = any(marker in lower for marker in ('закон', 'приказ', 'постановление', 'распоряжение', 'нп-'))

    if not (LEGAL_DATE_RE.search(text) or YEAR_RE.search(text)):
        issues.append(error(entry, 'Для нормативного документа не найдена дата принятия'))
    if number_expected and not LEGAL_NUMBER_RE.search(text):
        issues.append(warn(entry, 'Для нормативного документа не найден номер документа'))
    if URL_RE.search(text) and not ACCESS_DATE_MARKER_RE.search(text):
        issues.append(error(entry, 'Для нормативного электронного источника не найдена дата обращения'))
    elif ACCESS_DATE_MARKER_RE.search(text) and not ACCESS_DATE_STRICT_RE.search(text):
        issues.append(error(entry, 'Дата обращения должна быть оформлена так: "(дата обращения: ДД.ММ.ГГГГ)"'))

    return issues


def validate_conference_reference(entry: ReferenceEntry) -> list[ReferenceIssue]:
    """
    Проверить материал конференции или сборника трудов.

    Ожидаемые маркеры похожи на статью, но дополнительно проверяется наличие
    места издания или издательства, характерных для материалов конференций.
    """
    text = entry.text
    issues: list[ReferenceIssue] = []
    is_article_in_collection = '//' in text and ARTICLE_PAGES_RE.search(text)
    is_whole_collection = BOOK_PAGES_RE.search(text) and PLACE_PUBLISHER_RE.search(text)

    if not (is_article_in_collection or is_whole_collection):
        issues.append(warn(entry, 'Для материала конференции не найден диапазон страниц или общий объем сборника'))
    if not PLACE_PUBLISHER_RE.search(text):
        issues.append(warn(entry, 'Для материала конференции не найдено место издания или издательство'))

    return issues


def detect_source_type(entry: ReferenceEntry) -> SourceType:
    """
    Определить тип источника по детерминированным текстовым маркерам.

    Определение типа намеренно использует простые и объяснимые признаки:
    ``ГОСТ``, ``URL:``, ``//``, маркеры количества страниц и ключевые слова
    нормативных актов. Результат определяет, какой типовой валидатор будет
    запущен.
    """
    text = entry.text
    lower = text.lower()

    if 'гост' in lower:
        return SourceType.STANDARD
    if URL_MARKER_RE.search(text) or URL_RE.search(text) or 'электронный ресурс' in lower:
        return SourceType.WEB
    if PATENT_RE.search(text):
        return SourceType.PATENT
    if DISSERTATION_RE.search(text):
        return SourceType.DISSERTATION
    if CONFERENCE_RE.search(text) and ('конф' in lower or 'науч' in lower or 'тезис' in lower):
        return SourceType.CONFERENCE
    if LEGAL_ACT_RE.search(text):
        return SourceType.LEGAL_ACT
    if is_book_like_reference(text):
        return SourceType.BOOK
    if '//' in text:
        return SourceType.ARTICLE
    return SourceType.UNKNOWN


def is_book_like_reference(text: str) -> bool:
    """
    Проверить, похожа ли запись на книгу или отдельное издание.

    В ГОСТ-примерах книги, учебники, монографии, словари, сборники и тома
    обычно имеют место издания/издательство, общий объем ``с.``/``т.`` или
    явный маркер типа издания. Эти признаки важнее наличия ``//``: в реальных
    работах авторы иногда ошибочно ставят ``//`` перед местом издания книги.
    """
    if DISSERTATION_RE.search(text) or PATENT_RE.search(text) or GOST_NUMBER_RE.search(text):
        return False
    if BOOK_MARKER_RE.search(text):
        return True
    if BOOK_PAGES_RE.search(text) and PLACE_PUBLISHER_RE.search(text):
        return True
    if BOOK_PAGES_RE.search(text) and '//' in text and not ARTICLE_PAGES_RE.search(text):
        return True
    if (
        PLACE_PUBLISHER_RE.search(text)
        and '//' in text
        and not (ARTICLE_PAGES_RE.search(text) or VOLUME_OR_NUMBER_RE.search(text))
    ):
        return True
    return False


def find_near_duplicate_entries(
    entries: list[ReferenceEntry],
    *,
    similarity_threshold: float = 0.9,
) -> list[ReferenceIssue]:
    """
    Найти библиографические записи, похожие на дубли.

    Перед сравнением нормализуются URL, годы, пунктуация и регистр. Это
    намеренно предупреждение, потому что похожие источники могут быть
    разными изданиями или связанными страницами.
    """
    issues: list[ReferenceIssue] = []
    normalized_entries = [(entry, normalize_for_duplicate_check(entry.body_text)) for entry in entries]

    for index, (entry, text) in enumerate(normalized_entries):
        if len(text) < 40:
            continue
        for other, other_text in normalized_entries[index + 1 :]:
            if len(other_text) < 40:
                continue
            similarity = difflib.SequenceMatcher(None, text, other_text).ratio()
            if similarity >= similarity_threshold:
                issues.append(
                    ReferenceIssue(
                        IssueLevel.WARNING,
                        f'Источник похож на дублирующую запись N {other.number} (сходство {similarity:.0%})',
                        entry.bbox,
                        entry.page_num,
                    )
                )

    return issues


def validate_reference_alignment(
    entries: list[ReferenceEntry],
    *,
    tolerance: float = 8.0,
) -> list[ReferenceIssue]:
    """
    Проверить визуальное выравнивание номеров источников.

    Правило сравнивает левую координату первой строки каждой записи с
    медианной левой координатой. Большие отклонения считаются предупреждениями,
    потому что извлечение PDF и выравнивание текста могут давать небольшие
    смещения.
    """
    if len(entries) < 3:
        return []

    left_values = [entry.lines[0].bbox.left for entry in entries]
    expected_left = median(left_values)
    issues: list[ReferenceIssue] = []

    for entry in entries:
        actual_left = entry.lines[0].bbox.left
        if abs(actual_left - expected_left) > tolerance:
            issues.append(warn(entry, 'Номер источника визуально смещен относительно остальных записей списка'))

    return issues


def emit_issues(issues: list[ReferenceIssue], sv: SummaryVisualization, logger: logging.Logger) -> None:
    """
    Записать найденные проблемы через логгер визуализации проекта.

    ``SummaryVisualization`` перехватывает записи уровня warning/error и
    сохраняет их вместе с переданным ``bbox``. Позже
    ``task_visualize_output_pdf_file`` отрисует эти сообщения в
    аннотированном PDF.
    """
    for issue in issues:
        sv.set_meta(issue.page_num, element=None)
        if issue.level == IssueLevel.ERROR:
            logger.error(issue.message, extra={'bbox': issue.bbox})
        else:
            logger.warning(issue.message, extra={'bbox': issue.bbox})


def error(entry: ReferenceEntry, message: str) -> ReferenceIssue:
    """Создать ошибку, привязанную ко всей библиографической записи."""
    return ReferenceIssue(IssueLevel.ERROR, message, entry.bbox, entry.page_num)


def warn(entry: ReferenceEntry, message: str) -> ReferenceIssue:
    """Создать предупреждение, привязанное ко всей библиографической записи."""
    return ReferenceIssue(IssueLevel.WARNING, message, entry.bbox, entry.page_num)


def find_next_entry(entries: list[ReferenceEntry], missing_number: int) -> ReferenceEntry:
    """Вернуть ближайшую следующую запись для привязки ошибки о пропущенном номере."""
    for entry in entries:
        if entry.number > missing_number:
            return entry
    return entries[-1]


def normalize_spaces(text: str) -> str:
    """Сжать все последовательности пробельных символов до одного пробела."""
    return re.sub(r'\s+', ' ', text).strip()


def normalize_for_duplicate_check(text: str) -> str:
    """Нормализовать текст записи перед поиском похожих дублей."""
    text = text.lower()
    text = re.sub(r'https?://\S+|www\.\S+', '', text)
    text = re.sub(r'\b(?:19|20)\d{2}\b', '', text)
    text = re.sub(r'[\W_]+', ' ', text, flags=re.UNICODE)
    return normalize_spaces(text)


def brackets_are_balanced(text: str) -> bool:
    """Проверить, что круглые и квадратные скобки сбалансированы по количеству."""
    return text.count('(') == text.count(')') and text.count('[') == text.count(']')


def median(values: list[float]) -> float:
    """Вычислить медиану для непустого списка чисел."""
    sorted_values = sorted(values)
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[middle]
    return (sorted_values[middle - 1] + sorted_values[middle]) / 2
