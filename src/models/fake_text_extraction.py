from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

from src.models.abstract_text_extraction import AbstractTextExtraction
from src.structures import ExtractedText, BoundingBox, PageExtractedTextList

logger = logging.getLogger(__name__)


class FakeTextExtraction(AbstractTextExtraction):
    A4_HEIGHT: ClassVar[int] = 297
    PT: ClassVar[float] = 0.3528

    def __init__(self, path_pdf: Path | None, fake_text_lines: list[list[list[str]]]) -> None:
        super().__init__(path_pdf)

        # page_index -> block -> line -> text
        self.fake_text_lines: list[list[list[str]]] = fake_text_lines

    @property
    def n_pages(self) -> int:
        return len(self.fake_text_lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass  # nothing to do, this is fake

    def extract_text(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
    ) -> list[ExtractedText]:
        assert page_index < len(self.fake_text_lines), (
            f'Индекс страницы больше чем кол-во фейковых страниц {len(self.fake_text_lines)}'
        )

        fake_box: BoundingBox = BoundingBox(0, 0, 0, 0)

        text: str = ''
        for block in self.fake_text_lines[page_index]:
            text += ' '.join(block)

        return [ExtractedText(page_index, 0, bbox=fake_box, text=text)]

    def extract_paragraphs(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
    ) -> PageExtractedTextList:
        assert page_index < len(self.fake_text_lines), (
            f'Индекс страницы больше чем кол-во фейковых страниц {len(self.fake_text_lines)}'
        )

        blocks: PageExtractedTextList = PageExtractedTextList()
        return blocks

    def extract_lines(
        self,
        page_index: int,
        clip: BoundingBox | None = None,
        exclude_clips: list[BoundingBox] | None = None,
        *,
        sort: bool = True,
        lines_max: int | None = None,
    ) -> list[ExtractedText]:
        assert page_index < len(self.fake_text_lines), (
            f'Индекс страницы больше чем кол-во фейковых страниц {len(self.fake_text_lines)}'
        )

        fake_box: BoundingBox = BoundingBox(0, 0, 0, 0)

        max_lines_reached: bool = False
        lines: list[ExtractedText] = []
        for block in self.fake_text_lines[page_index]:
            if max_lines_reached:
                break

            for block_num, text in enumerate(block):
                if max_lines_reached:
                    break

                line_info = ExtractedText(page_num=page_index, block_num=block_num, bbox=fake_box, text=text)
                lines.append(line_info)

                if lines_max is not None and len(lines) >= lines_max:
                    max_lines_reached = True

        return lines
