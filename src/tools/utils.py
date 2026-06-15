import json
import logging
import re
import difflib
import numpy as np
import pandas as pd
from pathlib import Path
from natasha import Segmenter, Doc, NewsEmbedding, NewsMorphTagger

from src.protocols import AnnotatedPage
from src.structures import BoundingBox, FoundedCaption, FoundedReference, ImageInfo, PageExtractedTextList, \
    ExtractedText
from src.models.model_text_extraction import PyMuPDFModel
from src.detection_content import extract_section_name

from nodes import names_dqc

logger = logging.getLogger(__name__)


def get_page_index(img_name: str) -> int:
    return int(re.search(r'(\d+)$', img_name).group(1))


def read_img_np(path_img_np: Path, img_num: int) -> np.ndarray:
    if path_img_np.suffix != '.npy':
        raise TypeError('Должно быть *.npy расширение файла, а передано %s', str(path_img_np))

    if not path_img_np.exists():
        raise FileNotFoundError('Не существует *.npy объект для страницы %d', img_num)

    img = np.load(path_img_np)

    return img


def generate_exclude_bboxes(
    page_index: int,
    df_objects: pd.DataFrame,
    page_box: tuple[float, float],  # height, width
    page: AnnotatedPage | None = None,
    clip: BoundingBox | None = None,
) -> tuple[list[BoundingBox], list[BoundingBox]]:
    exclude_bounding_boxes = []

    if not df_objects.empty:
        df_objects_page = df_objects[df_objects['img_name'] == f"page-{page_index}"]

        if clip and not df_objects_page.empty:
            df_objects_page = df_objects_page[
                (df_objects_page['top'] > clip.top) & (df_objects_page['bottom'] < clip.bottom)
            ]

        if not df_objects_page.empty:
            for _, row in df_objects_page.iterrows():
                box = BoundingBox(top=row['top'], bottom=row['bottom'], left=0, right=page_box[1])
                exclude_bounding_boxes.append(box)

    if page is None:
        return exclude_bounding_boxes, []

    # todo пока закомментил это, работает хуже чем yolo
    # clip_ = page.rect if clip is None else clip.rectangle_for_pymupdf
    exclude_tables_bboxes = []
    # for table in page.find_tables(clip=clip_).tables:
    #     exclude_tables_bboxes.append(BoundingBox.from_pymupdf_box(table.bbox))

    return exclude_bounding_boxes, exclude_tables_bboxes


def generate_exclude_bboxes_all(
    page_index: int,
    df_objects: pd.DataFrame,
    page_box: tuple[float, float],  # height, width
    page: AnnotatedPage | None = None,
    clip: BoundingBox | None = None,
) -> list[BoundingBox]:
    exclude_objects, exclude_tables = generate_exclude_bboxes(page_index, df_objects, page_box, page, clip)
    return exclude_objects + exclude_tables


def track_progress(ti, front_kwargs):
    """Extract progress tracking parameters from front_kwargs and TaskInstance.
    """

    progress_params = {}
    if front_kwargs:
        try:
            front_data = json.loads(front_kwargs)
            progress_params.update(
                {
                    'channel_id': front_data.get('channel_id'),
                    'message_id': front_data.get('message_id'),
                    'run_id': ti.run_id,
                    'task_id': ti.task_id,
                }
            )
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f'Failed to parse front_kwargs for progress tracking: {e}')

    return progress_params


def get_error_handling_tasks():
    return [
        names_dqc.TASK_CHECK_TELEGRAM_RUN,
        names_dqc.TASK_PREPARE_ADMIN_EMAIL,
        names_dqc.TASK_SEND_ADMIN_EMAIL,
        names_dqc.TASK_SKIP_ADMIN_EMAIL,
    ]


def check_captions_sequence(caption_1: FoundedCaption, caption_2: FoundedCaption, logger: logging.Logger = None) -> bool:
    """
    Функция для проверки последовательности подписей.

    :param caption_1: Первая подпись для проверки.
    :type caption_1: FoundedCaption
    :param caption_2: Последующая подпись для проверки.
    :type caption_2: FoundedCaption
    :param logger: Логер для записи ошибок.
    :type logger: typing.Logger

    :return: Следуют ли подписи друг за другом.
    :rtype: bool
    """
    is_valid = True
    if caption_1.section_number == caption_2.section_number:
        pattern = 'Продолжение таблицы'
        is_table_continue = difflib.get_close_matches(word=pattern, possibilities=[caption_2.text[:len(pattern)]], n=1, cutoff=0.8)
        if is_table_continue and int(caption_2.number) - int(caption_1.number) != 0:
            if logger: logger.error(
                f'Неверный порядок: текущая подпись "{caption_2}" является продолжением '
                f'предыдущей подписи "{caption_1}"',
                extra={"bbox": caption_2.box}
            )
            is_valid &= False
        elif not is_table_continue and int(caption_2.number) - int(caption_1.number) != 1:
            if logger: logger.error(
                f'Неверный порядок: предыдущая подпись - "{caption_1.num_str}", '
                f'текущая - "{caption_2.num_str}"',
                extra={"bbox": caption_2.box}
            )
            is_valid &= False
    elif caption_2.number != "1":
        if logger: logger.error(
            f'Так как раздел предыдущей подписи "{caption_1.section_number}", '
            f'а текущей "{caption_2.section_number}", то номера должны идти с начала',
            extra={"bbox": caption_2.box}
        )
        is_valid &= False

    return is_valid


def connect_reference_w_caption(references: list[FoundedReference], captions: list[FoundedCaption]) -> list[FoundedReference]:
    """
    Функция присваивает ссылке соответствующую ей подпись.

    :param references: Ссылки документа.
    :type references: list[FoundedReference]
    :param captions: Подписи документа.
    :type captions: list[FoundedCaption]

    :return: Обновленный список ссылок с подписями.
    :rtype: list[FoundedReference]
    """
    for i, reference in enumerate(references):
        for caption in captions:
            if reference.num_str == caption.num_str:
                # Объект immutable, невозможно изменить, придется копировать (для cached_properties)
                references[i] = FoundedReference(
                    page_number=reference.page_number,
                    lines=reference.lines,
                    section_number=caption.section_number,
                    number=caption.number,
                    confidence=reference.confidence,
                    caption=caption,
                    text_extracted=reference.text,
                )

    return references


def get_section_location(img_info: ImageInfo, section: str, content: dict[str, int]) -> tuple[int, BoundingBox] | None:
    """
    Функция возвращает номер страницы с интересующим разделом и его ббокс.

    :param img_info: Информация об изображении.
    :type img_info: ImageInfo
    :param section: Номер раздела.
    :type section: str
    :param content: Информация о содержании документа.
    :type content: dict[str, int]

    :return: Номер страницы раздела в документе и его ббокс. Если нет нужного раздела, то None.
    :rtype: tuple[int, BoundingBox] | None
    """
    if section not in content:
        return None

    section_page = content[section] - 1  # Индексы на 1 отличаются из-за титула
    with PyMuPDFModel(img_info.path_pdf) as model:
        blocks: PageExtractedTextList = model.extract_paragraphs(section_page)
        for block in blocks:
            if section == extract_section_name(block.text):
                return block.page_num, block.bbox


def union_lines_to_blocks(lines: list[ExtractedText]) -> list[list[ExtractedText]]:
    if not lines:
        return []

    blocks = []
    block_number: int | None = None
    for line in lines:
        if block_number is None or line.block_num != block_number:
            blocks.append([line])
            block_number = line.block_num
        elif line.block_num == block_number:
            blocks[-1].append(line)

    return blocks


def get_symbol_before_name(text: str) -> str | None:
    """Найти символ в подписи до названия рисунка/таблицы"""
    segmenter: Segmenter = Segmenter()
    emb: NewsEmbedding = NewsEmbedding()
    morph_tagger: NewsMorphTagger = NewsMorphTagger(emb)

    doc: Doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_morph(morph_tagger)

    tokens = doc.tokens
    indices_noun: list[int] = [i for i in range(len(tokens)) if tokens[i].pos in {'NOUN', 'ADJ', 'VERB', 'ADV'} and tokens[i].feats]
    indices: list[int] = [i for i in range(len(tokens)) if tokens[i].pos == 'PUNCT' and tokens[i].text != '.']

    if indices:
        if len([i for i in indices_noun if i < indices[0]]) > 1:
            return None  # Найденные знаки за вторым словом

        return tokens[indices[0]].text

    return None
