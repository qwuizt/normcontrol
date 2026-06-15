from pathlib import Path

import cv2
import numpy as np
from pymupdf import pymupdf


def img_save_tmp(img: np.ndarray, *elements, img_name: str = 'tmp.png'):
    img_draw = img.copy()
    for el in elements:
        if hasattr(el, 'bbox'):
            el = el.bbox
        if hasattr(el, 'box'):
            el = el.box

        color = (0, 0, 255)
        cv2.rectangle(img_draw, *el.rectangle_for_cv, color, 2)

    cv2.imwrite(img_name, img_draw)


def pdf_page_tmp(path_pdf: Path, index: int, *elements, img_name: str = 'tmp.png'):
    with pymupdf.open(path_pdf) as pdf:
        page = pdf[index]
        pix = page.get_pixmap(colorspace='RGB', alpha=False)
        img_draw = np.ndarray([pix.h, pix.w, 3], dtype=np.uint8, buffer=pix.samples_mv)

    img_save_tmp(img_draw, *elements, img_name=img_name)
