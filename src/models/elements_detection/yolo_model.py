from typing import Iterator

import numpy as np

from src.models.elements_detection.abstract_element_detection_model import AbstractDetectionModel
from src.structures import BoundingBox, PageElement, PageElementDetail

FIGURES = {PageElement.figure, PageElement.picture}


def _is_intersected(obj1: PageElementDetail, obj2: PageElementDetail) -> bool:
    bbox_prev, bbox_next = obj1.box, obj2.box
    iou = BoundingBox.get_iou(bbox_prev, bbox_next)
    return iou >= 0.2 or (bbox_prev.is_sub(bbox_next) or bbox_next.is_sub(bbox_prev))


def _can_be_merged(obj1: PageElementDetail, obj2: PageElementDetail) -> bool:
    can_be_merged = False

    if obj1.element_type == obj2.element_type:
        can_be_merged = True

    text = {PageElement.title, PageElement.section_header, PageElement.text, PageElement.list_item}
    if obj1.element_type in FIGURES and obj2.element_type in FIGURES:
        can_be_merged = True

    if obj1.element_type in FIGURES and obj2.element_type in ({PageElement.table} | text):
        can_be_merged = True

    if obj1.element_type in text and obj2.element_type in text:
        can_be_merged = True

    return can_be_merged and _is_intersected(obj1, obj2)


def _must_be_split(obj1: PageElementDetail, obj2: PageElementDetail) -> bool:
    if (
            obj1.element_type in FIGURES and
            obj2.element_type == PageElement.caption and
            obj1.box.is_sub(obj2.box, threshold=0.9)
    ):
        # Кейс когда рисунок перекрывает caption
        return True

    return False


def _is_follow_picture(obj1: PageElementDetail, obj2: PageElementDetail, objects: list[PageElementDetail]) -> bool:
    """
    Когда есть рисунок, но внутри 2 маленьких которые разделены белым фоном. Модель может
    определять их как 2 отдельных рисунка
    """
    if obj1.element_type in FIGURES and obj2.element_type in FIGURES:
        if obj2.box.top < obj1.box.top:
            # obj1 должен быть выше
            tmp = obj2
            obj2 = obj1
            obj1 = tmp

        top, bottom = obj1.box.bottom, obj2.box.top
        if bottom > top:
            # obj2 должен быть ниже obj1,
            # True если между этими 2-мя объектами нет никакого другого элемента
            return not any(filter(lambda v: top < v.box.top < bottom, objects))

    return False


def _merge(obj1: PageElementDetail, obj2: PageElementDetail) -> PageElementDetail:
    with_figures = {PageElement.list_item, PageElement.formula}

    element_type = obj2.element_type if obj1.confidence < obj2.confidence else obj1.element_type
    if (
            obj1.element_type in FIGURES and obj2.element_type in with_figures or
            obj2.element_type in FIGURES and obj1.element_type in with_figures
    ):
        # На некоторых рисунка изображены строки кода, которые детектятся как listitem
        element_type = PageElement.picture

    return PageElementDetail(
        element_type=element_type,
        box=obj1.box | obj2.box,
        confidence=max(obj1.confidence, obj2.confidence),
        page_index=obj1.page_index,
    )


def _merge_objects(objects: list[PageElementDetail]) -> None:
    is_merging: bool = True

    while is_merging:
        is_merging = False

        if len(objects) <= 1:
            break

        to_remove = set()
        for i in range(0, len(objects)):
            if i in to_remove:
                continue

            obj_prev = objects[i]
            for j in range(0, len(objects)):
                if i == j or j in to_remove:
                    continue

                obj = objects[j]

                if _must_be_split(obj_prev, obj):
                    obj_prev.box = BoundingBox(
                        top=obj_prev.box.top,
                        left=obj_prev.box.left,
                        bottom=obj.box.top - 5,
                        right=obj_prev.box.right,
                    )
                elif _can_be_merged(obj_prev, obj) or _is_follow_picture(obj_prev, obj, objects):

                    objects[i] = _merge(obj_prev, obj)
                    to_remove.add(j)
                    is_merging = True

        for i in sorted(to_remove, reverse=True):
            objects.pop(i)


class YOLOEverything(AbstractDetectionModel):
    TEXT_CATEGORIES = [
        PageElement.caption,
        PageElement.footnote,
        PageElement.list_item,
        PageElement.section_header,
        PageElement.text,
        PageElement.title,
        PageElement.page_header,
        PageElement.page_footer,
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._model = None

    def get_model(self):
        from ultralytics import YOLO

        if self._model is None:
            self._model = YOLO(self.path_to_weights)

        return self._model  # dla-model.pt

    def predict(self, img: np.ndarray):
        model = self.get_model()

        results = model.predict(source=img, conf=self.conf, iou=self.iou)
        boxes = results[0].boxes  # Get bounding boxes

        return boxes

    def detect_everything(self, img: np.ndarray, page_index: int) -> Iterator[PageElementDetail]:
        """Detect every element on the passed image"""

        boxes = self.predict(img)
        classes = [
            'Footnote',
            'Text',
            'Table',
            'Formula',
            'Caption',
            'Title',
            'Page-footer',
            'List-item',
            'Picture',
            'Section-header',
            'Page-header',
            'Listing'
        ]

        objects = []
        for box in boxes:
            conf = float(box.conf[0])
            bounding_box = BoundingBox(
                top=int(box.xyxy[0][1]),
                left=int(box.xyxy[0][0]),
                bottom=int(box.xyxy[0][3]),
                right=int(box.xyxy[0][2]),
            )
            el = PageElementDetail(
                element_type=PageElement(classes[int(box.cls[0])]),
                box=bounding_box,
                confidence=conf,
                page_index=page_index,
            )
            objects.append(el)

        objects = sorted(objects, key=lambda x: x.box.top, reverse=False)
        _merge_objects(objects)

        yield from objects

    def detect_text_boxes(self, img: np.ndarray) -> list[BoundingBox]:
        """Detect only text boxes on the passed image"""
        output_boxes_array: list[BoundingBox] = []

        for box in self.predict(img):
            if box['class'] in self.TEXT_CATEGORIES:
                output_box = BoundingBox(
                    top=int(box.xyxy[0][0]),
                    left=int(box.xyxy[0][1]),
                    bottom=int(box.xyxy[0][2]),
                    right=int(box.xyxy[0][3]),
                )
                output_boxes_array.append(output_box)

        return output_boxes_array
