import asyncio
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pymupdf

from src.structures import PageClass, LabelPosition, ImageInfo, Paths, BoundingBox, PageElement
from src.tools import utils
from src.tools.summary_visualization import SummaryVisualization, SummaryVerbose
from src.tools.progress_tracker import create_progress_tracker

from src.services.detection_client import DetectionClient, get_detection_client

logger = logging.getLogger(__name__)


async def detect_elements_on_page(
    img_info: ImageInfo,
    coefs: tuple[float, float],
    sv: SummaryVisualization,
    detection_client: DetectionClient,
):
    try:
        page_elements = await detection_client.detect_page_elements(image=img_info.img, page_index=img_info.img_index)
        objs = sorted(page_elements, key=lambda obj: obj.box.top)
    except Exception as e:
        logger.error(f'Detection failed for page {img_info.img_index}: {e}')
        return []

    prev_label = None
    prev_box = None
    prev_confidence = None
    out_list = []

    for obj in objs:
        if (
            prev_box
            and (
                BoundingBox.get_iou(prev_box, obj.box) >= 0.2 or (prev_box.is_sub(obj.box) or obj.box.is_sub(prev_box))
            )
            and not obj.is_important
        ):
            out_list[-1][0] = obj.element_type.value if prev_confidence < obj.confidence else prev_label  # label
            out_list[-1][3:7] = (prev_box | obj.box).summary  # box
            out_list[-1][-1] = (prev_confidence + obj.confidence) / 2  # confidence
        else:
            out_list.append(
                [obj.element_type.value, img_info.img_index, img_info.img_name, *obj.box.summary, obj.confidence]
            )

        prev_label = out_list[-1][0]
        prev_box = BoundingBox(*out_list[-1][3:7])
        prev_confidence = out_list[-1][-1]

    for out_item in out_list:
        box = BoundingBox(
            top=out_item[3] * coefs[0],
            left=out_item[4] * coefs[1],
            bottom=out_item[5] * coefs[0],
            right=out_item[6] * coefs[1],
        )
        out_item[3:7] = box.summary
        is_important = out_item[0] in {
            PageElement.table.value,
            PageElement.picture.value,
            PageElement.figure.value,
            PageElement.caption.value,
        }
        verbose = SummaryVerbose.ADDITIONAL_MAIN if is_important else SummaryVerbose.ADDITIONAL
        sv.add_rectangle(
            out_item[2],
            rectangle=box,
            color='red',
            label=f'{out_item[0]} ({round(out_item[-1], 2)})',
            label_pos=LabelPosition.tr,
            verbose=verbose,
        )

    return out_list


async def process_page_batch(
    batch_df: pd.DataFrame,
    paths_object: Paths,
    page_dimensions: dict[int, tuple[float, float]],
    sv: SummaryVisualization,
    path_150: Path,
    detection_client: DetectionClient,
    semaphore: asyncio.Semaphore,
    progress_tracker,
):
    async with semaphore:
        batch_results = []
        
        for i, row in batch_df.iterrows():
            img_index = row['page']
            img_name = f'page-{img_index}'

            logger.info(f'Processing page: {img_name}')

            img: np.ndarray = utils.read_img_np(path_150 / f'{img_name}.npy', img_index)
            img_info = ImageInfo(paths_object.path_pdf, img_index, img_name, img)

            page_pdf_width, page_pdf_height = page_dimensions[img_index]

            coef_y: float = page_pdf_height / img.shape[0]
            coef_x: float = page_pdf_width / img.shape[1]

            page_objects = await detect_elements_on_page(
                img_info, coefs=(coef_y, coef_x), sv=sv, detection_client=detection_client
            )
            
            batch_results.extend(page_objects)
            
            if progress_tracker:
                progress_tracker.increment()

        return batch_results


async def detect_elements(
    paths_object: Paths,
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
    channel_id: Optional[int] = None,
    message_id: Optional[int] = None,
    **kwargs,
) -> pd.DataFrame:
    columns = [
        'element_type',
        'img_num',
        'img_name',
        'top',
        'left',
        'bottom',
        'right',
        'confidence',
    ]

    path_150: Path = paths_object.path_150

    df_classes: pd.DataFrame = pd.read_csv(paths_object.path_file_structure)
    df_classes: pd.DataFrame = df_classes[
        (df_classes['page_type'].isin([PageClass.introduction.name, PageClass.text.name]))
    ]

    sv = SummaryVisualization(paths_object.path_summary, detect_elements.__name__)
    sv.add_logger_handler(logger=logger)

    total_pages = len(df_classes)

    page_dimensions: dict[int, tuple[float, float]] = {}
    with pymupdf.open(paths_object.path_pdf) as doc:
        for img_index in df_classes['page']:
            page = doc[img_index]
            page_dimensions[img_index] = (page.rect.width, page.rect.height)
    
    progress_tracker = create_progress_tracker(
        run_id=run_id,
        task_id=task_id,
        channel_id=channel_id,
        message_id=message_id,
        total_items=total_pages,
        task_display_name='Обнаружение элементов страницы',
        min_percent_diff=10,
    )

    BATCH_SIZE = 10
    MAX_CONCURRENT_BATCHES = 4
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

    async with get_detection_client() as detection_client:
        batches = [
            df_classes.iloc[i:i + BATCH_SIZE] 
            for i in range(0, total_pages, BATCH_SIZE)
        ]
        
        tasks = [
            process_page_batch(
                batch, paths_object, page_dimensions, sv, path_150, 
                detection_client, semaphore, progress_tracker
            )
            for batch in batches
        ]
        
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_page_objects = []
        for result in batch_results:
            if isinstance(result, Exception):
                logger.error(f'Batch processing failed: {result}')
                continue
            all_page_objects.extend(result)

    if progress_tracker:
        progress_tracker.set_completed()

    sv.save()

    if not all_page_objects:
        return pd.DataFrame(None, columns=columns)

    return pd.DataFrame(all_page_objects, columns=columns)
