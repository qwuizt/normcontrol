import logging
from dataclasses import dataclass
from typing import Literal

from src.structures import FoundedCaption, ImageInfo, PageElementDetail


@dataclass(frozen=True)
class CaptionValidationContext:
    img_info: ImageInfo
    element_info: PageElementDetail
    caption: FoundedCaption | None
    captions: list[FoundedCaption]
    content: dict[str, int]
    shape_page: list[int]
    logger: logging.Logger | None

    @property
    def table_info(self) -> PageElementDetail:
        return self.element_info

    @property
    def figure_info(self) -> PageElementDetail:
        return self.element_info


@dataclass(frozen=True)
class CaptionExistenceRule:
    kind: Literal['table', 'figure']


@dataclass(frozen=True)
class CaptionStartRule:
    allowed: list[str]
    similarity_cutoff: float = 0.8


@dataclass(frozen=True)
class CaptionTitleRequiredRule:
    similarity_cutoff: float = 0.8


@dataclass(frozen=True)
class CaptionAlignmentRule:
    alignment: Literal['center', 'left', 'width']


@dataclass(frozen=True)
class CaptionSeparatorRule:
    allowed: list[str]
    similarity_cutoff: float = 0.7


@dataclass(frozen=True)
class CaptionCapitalizationRule:
    similarity_cutoff: float = 0.7
    validate_title_after_separator: bool = False


@dataclass(frozen=True)
class CaptionEndPunctuationRule:
    forbidden: bool = True
    required: list[str] | None = None


@dataclass(frozen=True)
class CaptionNumberRule:
    has_section: bool = True
    suffix_forbidden: bool = False
    max_level: int | None = None


@dataclass(frozen=True)
class CaptionOrderRule:
    kind: Literal['table', 'figure']


@dataclass(frozen=True)
class CaptionIntervalRule:
    spacing_tolerance: float


@dataclass(frozen=True)
class CaptionSectionRule:
    pass


CaptionRule = (
    CaptionExistenceRule
    | CaptionStartRule
    | CaptionTitleRequiredRule
    | CaptionAlignmentRule
    | CaptionSeparatorRule
    | CaptionCapitalizationRule
    | CaptionEndPunctuationRule
    | CaptionNumberRule
    | CaptionOrderRule
    | CaptionIntervalRule
    | CaptionSectionRule
)


@dataclass(frozen=True)
class CaptionValidationRules:
    rules: tuple[CaptionRule, ...]
