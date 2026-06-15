import logging
from dataclasses import dataclass
from functools import partial
from typing import Callable

import pandas as pd

from src.models.model_text_extraction import PyMuPDFModel
from src.structures import ExtractedText, FoundedCaption, ImageInfo, PageElement, PageElementDetail
from src.tools import detection_caption_helper as helper


@dataclass(frozen=True)
class CaptionStrategy:
    direction: helper.SearchDirection
    empty_region_message: str
    detected_missed_message: str | None
    build_caption_from_lines_factory: Callable[[PageElementDetail, logging.Logger, float, float], Callable]
    post_search_fallback: Callable | None = None


def _detect_in_search_region(
    img_info: ImageInfo,
    element_info: PageElementDetail,
    captions_dict: dict[int, list[PageElementDetail]],
    search_context: helper.SearchContext,
    strategy: CaptionStrategy,
    logger: logging.Logger,
    pattern_match_cutoff: float,
) -> FoundedCaption | None:
    detected_regions = helper._get_detected_caption_regions(
        search_context.region,
        element_info,
        captions_dict.get(element_info.page_index, []),
        direction=strategy.direction,
    )
    build_caption_from_lines = strategy.build_caption_from_lines_factory(
        element_info,
        logger,
        search_context.page_height,
        pattern_match_cutoff,
    )

    with PyMuPDFModel(img_info.path_pdf) as model:
        if (
            caption := helper.extract_caption_from_regions(
                page_index=element_info.page_index,
                model=model,
                regions=detected_regions,
                exclude_clips=search_context.exclude_clips,
                build_caption_from_lines=build_caption_from_lines,
                logger=logger,
                empty_region_message=strategy.empty_region_message,
            )
        ) is not None:
            return caption

        if strategy.detected_missed_message is not None:
            logger.warning(strategy.detected_missed_message, extra={'bbox': element_info.box})
            return helper.extract_caption_from_regions(
                page_index=element_info.page_index,
                model=model,
                regions=[search_context.region],
                exclude_clips=search_context.exclude_clips,
                build_caption_from_lines=build_caption_from_lines,
                logger=logger,
                empty_region_message=strategy.empty_region_message,
            )

    return None


def _figure_post_search_fallback(
    img_info: ImageInfo,
    element_info: PageElementDetail,
    search_context: helper.SearchContext,
    captions_dict: dict[int, list[PageElementDetail]],
    logger: logging.Logger,
) -> FoundedCaption | None:
    detected_captions = captions_dict.get(element_info.page_index, [])
    with PyMuPDFModel(img_info.path_pdf) as model:
        lines_below = model.extract_line_intervals(
            element_info.page_index,
            clip=search_context.region,
            exclude_clips=search_context.exclude_clips,
        )

    check_next_page = (
        element_info.box.bottom > search_context.page_height * helper.FIGURE_NEXT_PAGE_THRESHOLD
    )
    if not lines_below:
        logger.error(helper.FIGURE_NO_TEXT_MESSAGE)
        return helper._check_figure_caption_outside_search_region(
            # Implemented in helper; strategy file only orchestrates calls.
            img_info,
            element_info,
            search_context.page_height,
            logger,
            check_next_page=True,
        )

    if (
        caption := helper._parse_figure_caption_candidates(
            page_index=element_info.page_index,
            lines=lines_below,
            top_boundary=element_info.box.bottom + helper.CAPTION_SEARCH_GAP,
            page_height=search_context.page_height,
            logger=logger,
            detected_captions=detected_captions,
        )
    ) is not None:
        return caption

    return helper._check_figure_caption_outside_search_region(
        img_info,
        element_info,
        search_context.page_height,
        logger,
        check_next_page=check_next_page,
    )


def _build_table_parser(
    element_info: PageElementDetail,
    logger: logging.Logger,
    _page_height: float,
    pattern_match_cutoff: float,
) -> Callable[[list[ExtractedText]], FoundedCaption | None]:
    return partial(
        helper._parse_table_caption,
        element_info.page_index,
        pattern_match_cutoff=pattern_match_cutoff,
        logger=logger,
    )


def _build_figure_parser(
    element_info: PageElementDetail,
    logger: logging.Logger,
    page_height: float,
    _pattern_match_cutoff: float,
) -> Callable[[list[ExtractedText]], FoundedCaption | None]:
    return partial(
        helper._parse_figure_caption_candidates,
        page_index=element_info.page_index,
        top_boundary=element_info.box.bottom + helper.CAPTION_SEARCH_GAP,
        page_height=page_height,
        logger=logger,
    )


TABLE_STRATEGY = CaptionStrategy(
    direction='above',
    empty_region_message=helper.TABLE_NO_TEXT_MESSAGE,
    detected_missed_message=helper.TABLE_DETECTED_MISSED_MESSAGE,
    build_caption_from_lines_factory=_build_table_parser,
)

FIGURE_STRATEGY = CaptionStrategy(
    direction='below',
    empty_region_message=helper.FIGURE_NO_TEXT_MESSAGE,
    detected_missed_message=None,
    build_caption_from_lines_factory=_build_figure_parser,
    post_search_fallback=_figure_post_search_fallback,
)


def _get_strategy(element_type: str) -> CaptionStrategy:
    if element_type == PageElement.table.value:
        return TABLE_STRATEGY
    if element_type in {PageElement.figure.value, PageElement.picture.value}:
        return FIGURE_STRATEGY
    raise ValueError(f'Поиск подписи не поддерживается для типа {element_type}')


def detect_caption(
    *,
    img_info: ImageInfo,
    element_info: PageElementDetail,
    df_objects: pd.DataFrame,
    captions_dict: dict[int, list[PageElementDetail]] | None = None,
    logger: logging.Logger | None = None,
    toi_cutoff: float = 0.7,
) -> FoundedCaption | None:
    logger = logger or logging.getLogger(__name__)
    captions_dict = captions_dict or {}
    strategy = _get_strategy(element_info.element_type)
    search_context = helper._prepare_search_context(img_info, element_info, df_objects, strategy.direction)

    if (
        caption := _detect_in_search_region(
            img_info,
            element_info,
            captions_dict,
            search_context,
            strategy,
            logger,
            toi_cutoff,
        )
    ) is not None:
        return caption

    if strategy.post_search_fallback is not None:
        if (
            caption := strategy.post_search_fallback(
                img_info,
                element_info,
                search_context,
                captions_dict,
                logger,
            )
        ) is not None:
            return caption

    if element_info.element_type == PageElement.table.value:
        logger.warning(helper.TABLE_NOT_FOUND_MESSAGE, extra={'bbox': element_info.box})
    else:
        logger.error(helper.FIGURE_NOT_FOUND_MESSAGE, extra={'bbox': element_info.box})

    return None
