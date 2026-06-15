from pathlib import Path
import logging

import numpy as np
from pymupdf import pymupdf

from src import paths
from src.constants import DPI_LIST


def get_page(doc: pymupdf.Document, page_index: int, dpi: int) -> np.ndarray:
    pix = doc[page_index].get_pixmap(dpi=dpi, colorspace='RGB', alpha=False)

    return np.ndarray([pix.h, pix.w, 3], dtype=np.uint8, buffer=pix.samples_mv).copy()


def pdf_to_npy(path_pdf: Path, dpi_list: list[int] | None = None) -> tuple[Path, ...]:
    """
    Преобразовать pdf с N страниц в N *.npy объектов согласно переданному DPI
    :param path_pdf: Путь к pdf в рабочей директории
    :param dpi_list: Список DPI, в рамках которого нужно извлечь страницы pdf как изображений
    :return: Абсолютные пути к папкам с *.npy файлам для разных DPI
    """
    if dpi_list is None:
        dpi_list = DPI_LIST

    workdir = path_pdf.parent

    doc = pymupdf.open(path_pdf)
    n_pages = len(doc)
    list_path_output_dpi = []

    logging.info('Start processing pdf file "%s". It has len %d. dpi list is %s', str(path_pdf), n_pages, str(dpi_list))

    for dpi in dpi_list:
        path_output_dpi = workdir / f'{paths.FOLDER_DPI_PREFIX}{dpi}'
        path_output_dpi.mkdir(exist_ok=True, parents=True)
        list_path_output_dpi.append(path_output_dpi)

    for iter_, page_index in enumerate(range(n_pages)):
        for dpi in dpi_list:
            image = get_page(doc, page_index=page_index, dpi=dpi)
            np.save(workdir / f'{paths.FOLDER_DPI_PREFIX}{dpi}' / f'page-{iter_}.npy', image)

        iter_ += 1
    logging.info('NPY files were saved in folders: %s', str(list_path_output_dpi))

    return tuple(list_path_output_dpi)
