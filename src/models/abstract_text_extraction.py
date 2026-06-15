from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import ClassVar, cast

from pymupdf import pymupdf

from src.protocols import AnnotatedPage
from src.structures import ExtractedText, BoundingBox, PageExtractedTextList
from src.tools import tools

logger = logging.getLogger(__name__)


class AbstractTextExtraction:
    A4_HEIGHT: ClassVar[int] = 297
    PT: ClassVar[float] = 0.3528

    def __init__(self, path_pdf: Path) -> None:
        self.path_pdf = path_pdf

        self._doc: pymupdf.Document | None = None

    def __enter__(self):
        if not self.path_pdf.exists():
            raise FileNotFoundError(f'pdf файл по пути {self.path_pdf} не существует')

        self._doc = pymupdf.open(self.path_pdf)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            logger.error(f'Возникла ошибка: {str(exc_value)}')

        self._doc.close()
        self._doc = None

    @property
    def doc(self) -> pymupdf.Document:
        if self._doc is None:
            raise ValueError('Аттрибут doc доступен при открытии контекстного менеджера')

        return self._doc

    def get_page(self, page_index: int) -> AnnotatedPage:
        raw_page: pymupdf.Page = self.doc[page_index]
        page: AnnotatedPage = cast(AnnotatedPage, raw_page)
        return page

    def get_page_content_bbox(self, page_index: int) -> BoundingBox:
        bbox = None
        for line in self.extract_text(page_index):
            if bbox is None:
                bbox = line.bbox
            else:
                bbox |= line.bbox

        return bbox

    @property
    def n_pages(self) -> int:
        return len(self.doc)
    
    def get_page_margins(self, page_text: list[ExtractedText]) -> BoundingBox | None:
        if len(page_text):
            top_margin = page_text[0].bbox.top
            bottom_margin = page_text[-1].bbox.bottom
            left_margin = tools.get_mode([text.bbox.left for text in page_text])
            right_margin = tools.get_mode([text.bbox.right for text in page_text])

            return BoundingBox(top_margin, left_margin, bottom_margin, right_margin)

    @abstractmethod
    def extract_text(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
    ) -> list[ExtractedText]:
        """
        Extract text from page or its part (defined by clip).

        :param page_index: int, Index of page to extract text from
        :param clip: Bounding box for text extraction (by default is all page)
        :param exclude_clips: The regions of the page to exclude for extraction (tables, figures, ...)
        :param sort: bool, Sort the text blocks by their vertical position

        :return: A list of ExtractedText objects, each containing the text and its bounding box.
        :rtype: list[ExtractedText]
        """
        raise NotImplementedError()

    @abstractmethod
    def extract_paragraphs(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
    ) -> PageExtractedTextList:
        """
        Extract text from page or its part (defined by clip) into blocks, which are separated by tables.

        :param page_index: int, page index
        :param clip: The region of the page to extract text from
        :param exclude_clips: The regions of the page to exclude for extraction (tables, figures, ...)
        :param sort: Sort the text blocks by their vertical position

        :return: A list of ExtractedTextBlock objects, each containing the block`s number, text, its bounding box and style.
        :rtype: list[ExtractedTextBlock]
        """
        raise NotImplementedError()

    @abstractmethod
    def extract_lines(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
        lines_max: int | None = None,
    ) -> list[ExtractedText]:
        """
        Extract text from page or its part (defined by clip) into lines, which are separated by tables.

        :param page_index: The index of page from 0
        :param clip: The region of the page to extract text from
        :param exclude_clips: The regions of the page to exclude for extraction (tables, figures, ...)
        :param sort: bool, Sort the text blocks by their vertical position
        :param lines_max: The max number of lines to extract
        :return: A list of ExtractedText objects, each containing the block`s number, line`s number,
                 text, its bounding box, style and baseline y-coordinate.
        """
        raise NotImplementedError()
