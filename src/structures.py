from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from enum import Enum
from functools import reduce, cached_property
from pathlib import Path
from typing import TypedDict, Optional, Any

import numpy as np
import pandas as pd


class SectionHeader(Enum):
    terms = "термины и определения"
    abbreviations = "перечень сокращений и обозначений"
    intro = "введение"
    conclusion = "заключение"
    resources = "список использованных источников"
    app = "приложение "


@dataclass
class ContentElement:
    section_number: str  # "1.1" например, или главный размер как "1."
    text: str  # название раздела без номера и страницы
    content_page_number: int  #  номер странице, на который находится этот элемент содержания
    lines: list[ExtractedText]
    page_number: int | None = None  # на какой странице находится, None если номер не найден

    @property
    def bbox(self) -> BoundingBox:
        # bbox элемента содержания, чтобы прикреплять ошибки
        block = reduce(lambda prev, curr: prev | curr, self.lines)
        return block.bbox

    @property
    def text_full(self) -> str:
        block = reduce(lambda prev, curr: prev | curr, self.lines)
        return block.text

    def __repr__(self):
        return f'<{self.section_number} "{self.text}" ... {self.page_number}>'

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        return {
            'section_number': self.section_number,
            'page_number': self.page_number,
            'text': self.text,
            'content_page_number': self.content_page_number,
            'lines': [line.to_dict(full=full) for line in self.lines],
        }


@dataclass
class Point:
    row: int
    col: int

    def __add__(self, other: Offset) -> Point:
        return Point(row=other.top, col=other.left)

    @property
    def summary(self) -> list[int]:
        return [int(self.row), int(self.col)]


@dataclass
class Offset:
    top: int | float
    left: int | float


@dataclass
class BoundingBox:
    top: int | float
    left: int | float
    bottom: int | float
    right: int | float

    def __hash__(self) -> int:
        return hash((self.top, self.left, self.bottom, self.right))

    def __str__(self) -> str:
        return self.to_csv()

    @property
    def center(self) -> tuple[float, float]:
        """
        Возвращает координаты центра прямоугольника.

        :return: Кортеж (cx, cy) – координаты центра.
        """
        return (self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0

    @property
    def height(self) -> int | float:
        return self.bottom - self.top

    @property
    def rectangle_for_pymupdf(self) -> list[int]:
        return [int(self.left), int(self.top), int(self.right), int(self.bottom)]

    def to_csv(self) -> str:
        return f'({self.top},{self.left},{self.bottom},{self.right})'

    def copy(self):
        return BoundingBox(self.top, self.left, self.bottom, self.right)

    def to_dict(self, *, full: bool = False) -> dict[str, int]:
        return {
            'top': self.top,
            'left': self.left,
            'bottom': self.bottom,
            'right': self.right,
        }

    @property
    def area(self) -> float:
        return max(0, self.right - self.left + 1) * max(0, self.bottom - self.top + 1)

    @property
    def summary(self) -> list[int]:
        return [int(self.top), int(self.left), int(self.bottom), int(self.right)]

    @property
    def rectangle_for_pillow(self) -> list[tuple[int, int]]:
        # [(100, 10), (200, 20)] -> 10-20 - это строки (сверху-вниз), 100-200 - это столбцы (слева-направо)
        return [(self.left, self.top), (self.right, self.bottom)]

    @property
    def rectangle_for_cv(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return (int(self.left), int(self.top)), (int(self.right), int(self.bottom))

    def __add__(self, other: Point | Offset) -> BoundingBox:
        if not isinstance(other, (Point, Offset)):
            raise ValueError('you can add only Point')

        if isinstance(other, Offset):
            return BoundingBox(
                top=self.top + other.top,
                left=self.left + other.left,
                bottom=self.bottom + other.top,
                right=self.right + other.left,
            )

        return BoundingBox(
            top=self.top + other.row,
            left=self.left + other.col,
            bottom=self.bottom + other.row,
            right=self.right + other.col,
        )

    def __eq__(self, other) -> bool:
        if isinstance(other, BoundingBox):
            bboxes_is_equal = self.top == other.top
            bboxes_is_equal &= self.left == other.left
            bboxes_is_equal &= self.bottom == other.bottom
            bboxes_is_equal &= self.right == other.right
            return bboxes_is_equal

        raise TypeError(f'Impossible to compare with {type(other)}')

    def __and__(self, other: BoundingBox) -> BoundingBox:
        if other is None:
            return self

        assert isinstance(other, BoundingBox), 'Support only BoundingBox'

        x_1 = max(self.left, other.left)
        y_1 = max(self.top, other.top)
        x_2 = min(self.right, other.right)
        y_2 = min(self.bottom, other.bottom)

        return BoundingBox(top=y_1, left=x_1, bottom=y_2, right=x_2)

    def __or__(self, other: BoundingBox | None) -> BoundingBox:
        if other is None:
            return self

        assert isinstance(other, BoundingBox), 'Support only BoundingBox'

        new_left = min(self.left, other.left)
        new_top = min(self.top, other.top)
        new_right = max(self.right, other.right)
        new_bottom = max(self.bottom, other.bottom)

        return BoundingBox(top=new_top, left=new_left, bottom=new_bottom, right=new_right)

    @classmethod
    def from_pymupdf_box(cls, box) -> BoundingBox:
        left, top, right, bottom = box
        return cls(top, left, bottom, right)

    @staticmethod
    def get_intersection(bbox_1: BoundingBox, bbox_2: BoundingBox):
        return (bbox_1 & bbox_2).area

    @classmethod
    def get_iou(cls, bbox_1: BoundingBox, bbox_2: BoundingBox):
        """
        Функция ищет отношение пересечения площадей двух ббоксов к общей площади.

        :param bbox_1: Первый ббокс.
        :type bbox_1: BoundingBox
        :param bbox_2: Второй ббокс.
        :type bbox_2: BoundingBox

        :return: Отношение пересечения площадей двух ббоксов к их общей площади.
        :rtype: float
        """
        intersection_area = cls.get_intersection(bbox_1, bbox_2)
        union_area = (bbox_1 | bbox_2).area

        return intersection_area / max(union_area - intersection_area, 1)

    def is_sub(self, bbox: BoundingBox, threshold: float = 0.8):
        intersection_area = (self & bbox).area
        bbox_area = bbox.area
        return (intersection_area / bbox_area) > threshold

    def extend(self, offset: int | Offset) -> BoundingBox:
        if isinstance(offset, (int, float)):
            offset = Offset(offset, offset)

        return BoundingBox(
            top=max(self.top - offset.top, 0),
            left=max(self.left - offset.left, 0),
            bottom=self.bottom + offset.top,
            right=self.right + offset.left,
        )


@dataclass
class Paths:
    path_pdf: Path
    path_150: Path | None = None
    path_file_structure: Path | None = None  # Путь к структуре документа, где содержание, где основная часть
    path_file_content: Path | None = None  # Путь к содержанию
    path_doc_elements: Path | None = None  # Путь к распознанным элементам на странице документа
    path_summary: Path | None = None  # None - для тестов, когда мы не хотим сохранять в файл
    multiple_path: Path | None = None  # Путь к нескольких файлам, если они есть

    @staticmethod
    def create(workdir: Path) -> Paths:
        from src import paths

        return Paths(
            path_pdf=workdir / paths.FILE_PDF_FILE_NAME,
            path_150=workdir / paths.FOLDER_NPY_DPI_150,
            path_file_structure=workdir / paths.FILE_STRUCTURE,
            path_file_content=workdir / paths.FILE_CONTENT,
            path_doc_elements=workdir / paths.FILE_DOC_ELEMENTS,
            path_summary=workdir / paths.FOLDER_SUMMARY,
        )
    
    def check_exists(self, names: list[str]):
        for name in names:
            path = getattr(self, name, None)
            if path is None:
                raise AttributeError(f'Unknown or empty name "{name}"')

            if not path.exists():
                raise FileNotFoundError(f'File or folder not found: {path}')


@dataclass
class ImageInfo:
    path_pdf: Path
    img_index: int  # 0 for titul and etc
    img_name: str  # for example, "page-1"
    img: np.ndarray

    captions: list[PageElementDetail] | None = None


class PageClass(Enum):
    titul = 'Титульный лист'
    users = 'Список исполнителей'
    abstract = 'Реферат'
    content = 'Содержание документа'
    introduction = 'Введение'
    abbreviations = 'Перечень используемых сокращений и обозначений'
    text = 'Текстовая часть'
    conclusion = 'Заключение'
    app = 'Приложение'
    references = 'Список использованной литературы'

    @staticmethod
    def get_page_numbers(df: pd.DataFrame, page_class: PageClass) -> list[int]:
        df_page_class = df[df['page_type'] == page_class.name]
        return df_page_class['page'].tolist()


class StructurePage(TypedDict):
    page: int
    page_type: str


class PageElement(Enum):
    caption = 'Caption'
    footnote = 'Footnote'
    list_item = 'List-item'
    section_header = 'Section-header'
    text = 'Text'
    title = 'Title'
    page_header = 'Page-header'
    page_footer = 'Page-footer'
    figure = 'Figure'
    picture = 'Picture'
    table = 'Table'
    formula = 'Formula'
    listing = 'Listing'


@dataclass
class PageElementDetail:
    element_type: PageElement
    box: BoundingBox
    confidence: float
    page_index: int = -1

    def __str__(self) -> str:
        return f'{self.element_type}: {self.box} ({round(self.confidence, 2)})'

    def __eq__(self, other: PageElementDetail):
        if isinstance(other, PageElementDetail):
            return self.element_type == other.element_type and self.box == other.box

        raise TypeError('Comparing is accepted for PageElementDetail instances')

    def __add__(self, other: Offset):
        if isinstance(other, Offset):
            return PageElementDetail(
                element_type=self.element_type,
                box=self.box + other,
                confidence=self.confidence,
                page_index=self.page_index,
            )

        raise TypeError(f'Unknown type: {other}')

    @property
    def is_important(self) -> bool:
        return self.element_type in {PageElement.table, PageElement.picture, PageElement.figure}

    @property
    def img_name(self):
        return f'page-{self.page_index}'

    @classmethod
    def from_series(cls, s: pd.Series) -> 'PageElementDetail':
        match = re.search(r'(\d+)$', s['img_name'])
        if match is None:
            raise ValueError(f'Не удалось извлечь номер страницы из "{s["img_name"]}"')
        page_index = int(match.group(1))
        box = BoundingBox(**s[['top', 'left', 'bottom', 'right']].to_dict())
        return PageElementDetail(
            element_type=s['element_type'],
            box=box,
            confidence=s['confidence'],
            page_index=page_index,
        )

    @classmethod
    def from_records(cls, records: list[dict[str, int | str]]) -> list['PageElementDetail']:
        from src.tools import tools  # recurrent import

        res = []
        for record in records:
            page_index: int = tools.get_img_index(record['img_name'])
            box = BoundingBox(
                top=record['top'],
                left=record['left'],
                bottom=record['bottom'],
                right=record['right'],
            )
            res.append(
                PageElementDetail(
                    element_type=PageElement(record['element_type']),
                    box=box,
                    confidence=record['confidence'],
                    page_index=page_index,
                )
            )

        return res


class PageExtractedTextList(list):
    pass


class PageLineMeta:
    def __init__(
        self,
        font: str | None,
        size: float | None,
        baseline_y: int | None,
        ascender: float | None,
        descender: float | None,
        underscored: bool | None,
    ) -> None:
        self.font_list: set[str] = {font, } if font else set()
        self.size_list: set[float] = {size, } if size else set()
        self.baseline_y_list: list[int] = [baseline_y] if baseline_y else []
        self.ascender_list: list[float] = [ascender] if ascender else []
        self.descender_list: list[float] = [descender] if descender else []

        self.underscore_list: list[bool] = [underscored] if underscored is not None else []

    @property
    def font(self) -> str | None:
        return next(iter(self.font_list)) if self.font_list else None

    @property
    def size(self) -> float | None:
        # Если формула или есть индексы - нам нужен максимальный размер
        return max(self.size_list) if self.size_list else None

    @property
    def baseline_y(self) -> int | None:
        return max(self.baseline_y_list) if self.baseline_y_list else None

    @property
    def ascender(self) -> int | None:
        return self.ascender_list[0] if self.ascender_list else None

    @property
    def descender(self) -> int | None:
        return self.descender_list[0] if self.descender_list else None

    @property
    def underscored(self) -> bool | None:
        return any(self.underscore_list) if self.underscore_list else None

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        if full:
            return {
                'font_list': self.font_list,
                'size_list': self.baseline_y_list,
                'baseline_y_list': self.ascender_list,
                'ascender_list': self.ascender_list,
                'descender_list': self.descender_list,
                'underscore_list': self.underscore_list,
            }
        return {
            'font': self.font,
            'size': self.size,
            'baseline_y': self.baseline_y,
            'ascender': self.ascender,
            'descender': self.descender,
            'underscored': self.underscored,
        }

    @staticmethod
    def from_dict(data: dict) -> Optional['PageLineMeta']:
        if 'font_list' in data or 'size_list' in data:
            meta = PageLineMeta(None, None, None, None, None, None)
            meta.font_list = set(data.get('font_list', []))
            meta.size_list = set(data.get('size_list', []))
            meta.baseline_y_list = data.get('baseline_y_list', [])
            meta.ascender_list = data.get('ascender_list', [])
            meta.descender_list = data.get('descender_list', [])
            meta.underscore_list = data.get('underscore_list', [])

        elif 'font' in data or 'size' in data:
            meta = PageLineMeta(
                font=data.get('font', None),
                size=data.get('size', None),
                baseline_y=data.get('baseline_y', None),
                ascender=data.get('ascender', None),
                descender=data.get('descender', None),
                underscored=data.get('underscored', None)
            )
        else:
            meta = None

        return meta

    def __copy__(self):
        return PageLineMeta.from_dict(self.__dict__.copy())

    def __or__(self, other: 'PageLineMeta') -> 'PageLineMeta':
        if not isinstance(other, PageLineMeta):
            raise TypeError('Union is accepted for PageLineMeta instances')

        out = self.__copy__()
        out.font_list = self.font_list | other.font_list
        out.size_list = self.size_list | other.size_list
        out.baseline_y_list = self.baseline_y_list + other.baseline_y_list
        out.ascender_list = self.ascender_list + other.ascender_list
        out.descender_list = self.descender_list + other.descender_list
        out.underscore_list = self.underscore_list + other.underscore_list

        return out


@dataclass
class ExtractedText:
    page_num: int
    block_num: int
    bbox: BoundingBox
    text: str
    confidence: float = 1.0
    meta_info: Optional[PageLineMeta] = None
    line_interval: Optional[TextLineInterval] = None

    def __bool__(self) -> bool:
        return len(self.text) > 0

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return (f'{self.__class__.__name__}(block_num={self.block_num}, page_num={self.page_num}, '
                f'text={self.text!r}, bbox=...)')

    def to_dict(self, *, full: bool = False) -> dict[str, int | str | Any]:
        return {
            'page_num': self.page_num,
            'block_num': self.block_num,
            'bbox': self.bbox,
            'text': self.text,
            'confidence': self.confidence,
            'meta_info': self.meta_info.to_dict(full=full) if self.meta_info else {},
            'line_interval': self.line_interval if self.line_interval is not None else {},
        }

    @staticmethod
    def from_dict(data: dict) -> 'ExtractedText':
        return ExtractedText(
            page_num=data['page_num'],
            block_num=data['block_num'],
            bbox=BoundingBox(**data['bbox']),
            text=data['text'],
            confidence=data['confidence'],
            meta_info=PageLineMeta.from_dict(data.get('meta_info', {})),
            line_interval=TextLineInterval.from_dict(data.get('line_interval', {})),
        )

    def __or__(self, other: ExtractedText) -> ExtractedText:
        if not isinstance(other, ExtractedText):
            raise TypeError('Union is accepted for ExtractedText instances')

        if self.text == 'Т' and other.text.startswith('абл'):
            # Нашел такой баг, когда буква Т отдельным словом находится
            text = f'{self.text}{other.text}'
        else:
            text = f'{self.text} {other.text}'

        return ExtractedText(
            page_num=self.page_num,
            block_num=self.block_num,  # Если мы джоиним кусок текста, то это значит что блок должен быть предыдущий
            bbox=self.bbox | other.bbox,
            text=text,
            confidence=(self.confidence + other.confidence) / 2,
            meta_info=(self.meta_info | other.meta_info) if self.meta_info else other.meta_info,
            line_interval=(self.line_interval | other.line_interval) if self.line_interval else other.line_interval
        )

    @classmethod
    def from_block(cls, page_num: int, block_num: int, block) -> ExtractedText | None:
        if 'lines' not in block:
            return None

        info = None
        for line in block['lines']:
            next_info = cls.from_line(page_num, block_num, line)
            info = info | next_info if info else next_info

        return info

    @staticmethod
    def from_line(page_num: int, block_num: int, line) -> ExtractedText | None:
        spans = line['spans']
        if not spans:
            return None

        info = None
        for span in spans:
            next_info = ExtractedText.from_span(page_num, block_num, span)
            info = info | next_info if info else next_info

        return info

    @staticmethod
    def from_span(page_num: int, block_num: int, span, drawings: list[dict] | None = None) -> ExtractedText:
        bbox = BoundingBox.from_pymupdf_box(span['bbox'])

        # Если drawings не передали, то мы говорим что не знаем
        underscored: bool | None = None if drawings is None else False
        for draw in (drawings or []):
            bbox_draw = BoundingBox.from_pymupdf_box(draw['rect'])

            is_same_bottom = abs(bbox.bottom - bbox_draw.top) < 2.
            is_left_ok = abs(bbox_draw.left - bbox.left) < 1. or bbox_draw.left > bbox.left
            is_right_ok = abs(bbox_draw.right - bbox.right) < 1. or bbox_draw.right < bbox.right
            if is_same_bottom and is_left_ok and is_right_ok:
                underscored = True  # Нашли подчеркивание для span

        meta_ = PageLineMeta(
            span['font'],
            size=span['size'],
            baseline_y=span['origin'][1],
            ascender=span['ascender'],
            descender=span['descender'],
            underscored=underscored
        )

        text = span['text'].strip()

        return ExtractedText(page_num, block_num, bbox, text, confidence=1.0, meta_info=meta_)


class TextLineInterval:
    def __init__(self,
                 block_n: int, next_block_n: int,
                 line_n: int, next_line_n: int,
                 bbox: BoundingBox, next_bbox: BoundingBox,
                 spacing: float, add_spacing: float):
        self.block_n = block_n
        self.next_block_n = next_block_n

        self.line_n = line_n
        self.next_line_n = next_line_n

        self.bbox = bbox
        self.next_bbox = next_bbox

        self.spacing = spacing
        self.add_spacing = add_spacing

    def __str__(self) -> str:
        if self.block_n == self.next_block_n:
            return f'Paragraph {self.block_n}, rows {self.line_n}-{self.next_line_n}, interval {self.spacing} (add {self.add_spacing})'
        else:
            return f'Paragraphs {self.block_n}-{self.next_block_n}, interval {self.spacing} (add {self.add_spacing})'

    def copy(self) -> TextLineInterval:
        return TextLineInterval(self.block_n, self.next_block_n,
                                self.line_n, self.next_line_n,
                                self.bbox, self.next_bbox,
                                self.spacing, self.add_spacing)
    
    def are_lines_in_one_block(self):
        return self.block_n == self.next_block_n
    
    def to_dict(self):
        return {
            'block_n': self.block_n,
            'next_block_n': self.next_block_n,
            
            'line_n': self.line_n,
            'next_line_n': self.next_line_n,
            
            'bbox': self.bbox,
            'next_bbox': self.next_bbox,

            'spacing': self.spacing,
            'add_spacing': self.add_spacing
        }

    @staticmethod
    def from_dict(data: dict) -> Optional[TextLineInterval]:
        if 'block_n' not in data:
            return None

        return TextLineInterval(
            block_n=data['block_n'],
            next_block_n=data['next_block_n'],
            line_n=data['line_n'],
            next_line_n=data['next_line_n'],
            bbox=data['bbox'],
            next_bbox=data['next_bbox'],
            spacing=data['spacing'],
            add_spacing=data['add_spacing']
        )

    def __or__(self, other: TextLineInterval) -> TextLineInterval:
        if other is None:
            return self

        if not isinstance(other, TextLineInterval):
            raise TypeError('Union is accepted for TextLineInterval instances')

        return TextLineInterval(
            self.block_n,
            self.next_block_n,
            self.line_n,
            self.next_line_n,
            bbox=self.bbox | self.next_bbox,
            next_bbox=other.bbox | other.next_bbox,
            spacing=(self.spacing + other.spacing) / 2,
            add_spacing=(self.add_spacing + other.add_spacing) / 2
        )


@dataclass(frozen=True)
class FoundedCaption:
    page_number: int
    lines: tuple[ExtractedText, ...]  # immutable
    section_number: str | None
    number: str
    confidence: float | None = None

    def __str__(self) -> str:
        text = self.text[:20]
        return f'{self.page_number}: {self.num_str} "{text}"'

    @cached_property
    def box(self) -> BoundingBox:
        caption: ExtractedText = reduce(lambda prev, curr: prev | curr, self.lines)
        return caption.bbox

    @cached_property
    def text(self) -> str:
        caption: ExtractedText = reduce(lambda prev, curr: prev | curr, self.lines)
        return caption.text

    @property
    def num_str(self) -> str:
        number = f'{self.section_number}.{self.number}' if self.section_number is not None else str(self.number)
        return number

    @property
    def is_continue(self):
        return self.text.lower().strip().startswith('продолжение таблицы')  # Пока так

    def __eq__(self, other):
        if isinstance(other, FoundedCaption):
            sections_is_equal = self.page_number == other.page_number
            sections_is_equal &= self.box == other.box
            sections_is_equal &= self.text == other.text
            return sections_is_equal

        raise TypeError(f'Impossible to compare with {type(other)}')


@dataclass(frozen=True)
class FoundedReference(FoundedCaption):
    caption: FoundedCaption = None
    text_extracted: str | None = None  # Из абзаца нужный кусочек с текстом ссылки

    @property
    def text(self) -> str:
        return self.text_extracted

    def __eq__(self, other):
        if isinstance(other, FoundedReference) or isinstance(other, FoundedCaption):
            sections_is_equal = self.page_number == other.page_number
            boxes_diff = max(
                abs(self.box.top - other.box.top),
                abs(self.box.left - other.box.left),
                abs(self.box.bottom - other.box.bottom),
                abs(self.box.right - other.box.right),
            )
            sections_is_equal &= boxes_diff < 10  # если меньше 10 - то это один объект
            sections_is_equal &= self.text == other.text
            return sections_is_equal

        raise TypeError(f'Impossible to compare with {type(other)}')


class FigureType(Enum):
    """Для отрисовки в финальном pdf"""

    point = 1
    rectangle = 2
    label = 3


class LabelPosition(Enum):
    tl = 1  # top left
    tm = 2  # top middle
    tr = 3  # top right
    cl = 4  # center left
    cm = 5  # center middle
    cr = 6  # center right
    bl = 6  # bottom left
    bm = 7  # bottom middle
    br = 8  # bottom right


@dataclass
class FontStyle:
    """
    Описание стиля текста.

    Атрибуты:
        font (str): Название шрифта.
        size (float): Размер шрифта.
        bold (bool): Флаг, что шрифт жирный.
        italic (bool): Флаг, что шрифт курсивный.
    """

    font: str
    size: float
    bold: bool
    italic: bool

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FontStyle):
            return False
        return (
            self.font == other.font
            and self.size == other.size
            and self.bold == other.bold
            and self.italic == other.italic
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Span:
    """
    Описание одного текстового фрагмента (span).

    Атрибуты:
        text (str): Текст фрагмента.
        bbox (BoundingBox): Область, занимаемая фрагментом.
        style (FontStyle): Стиль текста фрагмента.
    """

    text: str
    bbox: BoundingBox
    style: FontStyle

    def to_dict(self) -> dict:
        return {
            'text': self.text,
            'bbox': self.bbox.to_dict(),
            'style': self.style.to_dict(),
        }


@dataclass
class Block:
    """
    Описание блока текста, сформированного после кластеризации спанов.

    Атрибуты:
        bbox (BoundingBox): Область блока.
        text (str): Объединённый текст блока.
        style (FontStyle): Стиль, использованный в блоке (берётся из первого спана).
        items (List[Span]): Список исходных спанов, вошедших в блок.
    """

    bbox: BoundingBox
    text: str
    style: FontStyle
    items: list[Span]

    def to_dict(self) -> dict:
        return {
            'bbox': self.bbox.to_dict(),
            'text': self.text,
            'style': self.style.to_dict(),
            'items': [span.to_dict() for span in self.items],
        }


@dataclass
class TextBlock:
    """
    Описание текстового блока, сформированного после кластеризации спанов.

    Атрибуты:
        bbox (BoundingBox): Область блока.
        text (str): Объединённый текст блока.
        fonts (List[FontStyle]): Список объектов FontStyle, описывающих стили отдельных спанов.
    """

    bbox: BoundingBox
    text: str
    fonts: list[FontStyle]
    items: list[Span]

    def to_dict(self) -> dict:
        return {
            'bbox': self.bbox.to_dict(),
            'text': self.text,
            'fonts': [font.to_dict() for font in self.fonts],
            'items': [span.to_dict() for span in self.items],
        }


@dataclass
class ValidationError:
    """
    Описание ошибки, обнаруженной при валидации.

    Атрибуты:
        bbox (BoundingBox): Область, к которой привязана ошибка.
        messages (List[str]): Список сообщений об ошибке.
    """

    bbox: BoundingBox
    messages: list[str]

    def to_dict(self) -> dict:
        return {
            'bbox': self.bbox.to_dict(),
            'messages': self.messages,
        }


@dataclass
class ErrorMessage:
    """
    Описание ошибки с поддержкой нескольких языков.

    Атрибуты:
        en (str): Ошибка на английском языке.
        ru (str): Ошибка на русском языке.
    """

    en: str
    ru: str

    def __str__(self) -> str:
        return self.ru

    def full(self) -> str:
        return f'{self.en} / {self.ru}'

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractedData:
    """
    Результат обработки PDF-файла.

    Атрибуты:
      title_page_index (int): Индекс титульной страницы.
      text_blocks (List[TextBlock]): Список блоков текста.
      errors (List[ValidationError]): Список ошибок валидации.
      annotated_pdf (str): Путь к аннотированному PDF.
    """

    title_page_index: int
    text_blocks: list[TextBlock]
    errors: list[ValidationError]
    annotated_pdf: str | None

    def to_dict(self) -> dict:
        return {
            'title_page_index': self.title_page_index,
            'text_blocks': [tb.to_dict() for tb in self.text_blocks],
            'errors': [err.to_dict() for err in self.errors],
            'annotated_pdf': self.annotated_pdf,
        }


# Abstract checking context
@dataclass(slots=True)
class DataContext:
    """
    Everything each check may need
    """

    pdf_path: Path
    pages: list[int]
    texts: list[str]
    ref_pages: list[int]
    ref_texts: list[str]


# Abstract checking result
@dataclass(slots=True)
class CheckResult:
    """
    Unified output produced by every check
    """

    name: str
    passed: bool | None
    score: float | None = None
    details: dict[str, object] | None = None


@dataclass(slots=True)
class PipelineResult:
    """
    Public result of semantic checks pipeline.

    Exposes only structured check results. Pages/texts are optional and intended
    for internal/debug use, not for the public interface.
    """

    checks: list[CheckResult]
    pages: list[int] | None = None
    texts: list[str] | None = None


# WatermarkCandidate - для поиска водяных знаков в тексте
@dataclass(slots=True, frozen=True)
class WatermarkCandidate:
    """
    Кандидат на водяной знак: bbox спана, текст, список сработавших сигналов и итоговый score.
    """

    bbox: tuple[float, float, float, float]
    text: str
    signals: list[str]  # [SIGNAL_URL_TEXT, SIGNAL_EMAIL_TEXT, SIGNAL_LINK_HIT, SIGNAL_NEAR_WHITE]
    score: int  # strong*SCORE_STRONG_WEIGHT + weak
