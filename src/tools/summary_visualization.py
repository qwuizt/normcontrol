from __future__ import annotations

import json
import logging
import uuid
from enum import Enum
from pathlib import Path

from src.structures import Point, BoundingBox, Offset, FigureType, LabelPosition, PageElementDetail

logger_ = logging.getLogger(__name__)


def _clear_file_id(file_id: str) -> str:
    return file_id.rsplit('.', 1)[0]


class SummaryVerbose(Enum):
    MAIN = 1  # Подпись рисунка или таблицы, ссылка и тд. Ключевые элементы нормоконтроля
    ADDITIONAL_MAIN = 2  # Типы страниц, рисунки, таблицы
    ADDITIONAL = 3  # Абзацы текста и тд, прям вспомогательная информация


class SummaryVisualization:
    class SummaryHandler(logging.Handler):
        """Хендлер для вычленения варнингов и ошибок и их записи в файл"""

        def __init__(self, summary_object: SummaryVisualization, **kwargs):
            super().__init__(**kwargs)

            self.summary_object: SummaryVisualization = summary_object

        def emit(self, record: logging.LogRecord):
            if record.levelno == logging.WARNING:
                self.summary_object.handle_warning(record.getMessage(), bbox=getattr(record, 'bbox', None))
            elif record.levelno == logging.ERROR:
                self.summary_object.handle_error(record.getMessage(), bbox=getattr(record, 'bbox', None))

    def __init__(self, summary_path: Path | str, node_id: str):
        if isinstance(summary_path, str):
            summary_path = Path(summary_path)

        self.path = summary_path

        self.path_function = self.path / node_id if self.path else None
        if self.path_function is not None:
            self.path_function.mkdir(parents=True, exist_ok=True)

        self.objects = []

        self.errors: list[str] = []
        self.errors_in_comment: dict[tuple[int, BoundingBox], list[str]] = {}

        self.warnings: list[str] = []
        self.warnings_in_comment: dict[tuple[int, BoundingBox], list[str]] = {}

        self._offset: Offset = Offset(0, 0)

        self.page_index: int | None = None  # Нужен для логирования, чтобы везде не таскать текущую страницу
        # Нужен для логирования, чтобы везде не таскать текущий элемент (таблицу например)
        self.element: PageElementDetail | None = None

        self.logger: logging.Logger = logger_

    def update_offset(self, offset: Offset | None) -> None:
        if offset is None:
            return

        self._offset = offset

    def set_meta(self, page_index: int, element: PageElementDetail | None) -> None:
        self.page_index = page_index
        self.element = element

    def _form_msg_full(self, kind: str, msg: str) -> str:
        msg_prefix: str = ''
        if self.page_index is not None:
            msg_prefix = f'{kind} на странице {self.page_index}'
        if self.element is not None:
            msg_prefix = f'{msg_prefix} для элемента {self.element}'
        if msg_prefix:
            msg_prefix = f'{msg_prefix}: '

        return f'{msg_prefix}{msg}'

    def handle_error(self, msg: str, bbox: BoundingBox | None = None) -> str:
        # Тут не нужно говорить "где" произошла ошибка, ибо комментарий будет в нужном месте
        page_index = self.page_index or 0
        bbox_ = bbox or (self.element and self.element.box) or BoundingBox(0, 0, 0, 0)
        bbox_ = bbox_ + self._offset
        self.errors_in_comment.setdefault((page_index, bbox_), []).append(msg)

        msg_full = self._form_msg_full('Ошибка', msg)
        self.errors.append(msg_full)

        return msg_full

    def handle_warning(self, msg: str, bbox: BoundingBox | None = None) -> str:
        page_index = self.page_index or 0
        bbox_ = bbox or (self.element and self.element.box) or BoundingBox(0, 0, 0, 0)
        bbox_ = bbox_ + self._offset
        self.warnings_in_comment.setdefault((page_index, bbox_), []).append(msg)

        msg_full = self._form_msg_full('Предупреждение', msg)
        self.warnings.append(msg_full)

        return msg_full

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._save_file('all', self.objects)

    def _save_file(self, file_id: str, data: list[dict]) -> None:
        if self.path_function is None:
            logger_.info('[SummaryVisualization] The path_function is None')
            return

        if not self.path_function.exists():
            self.path_function.mkdir(parents=True, exist_ok=True)

        hash_ = uuid.uuid4().hex[-10:]
        if data:
            filepath = self.path_function / f'{file_id}.figures.{hash_}.json'
            with open(filepath, 'w', encoding='utf-8') as f:
                text = json.dumps(data)  # ensure_ascii=False
                f.write(text)
        else:
            logger_.info('[SummaryVisualization] The data is None')

        if self.errors or self.warnings:
            with open(self.path_function / f'{file_id}.messages.{hash_}.csv', 'w') as f:
                for message in self.warnings:
                    f.write(f'{logging.WARNING}\t{message}\n')
                for message in self.errors:
                    f.write(f'{logging.ERROR}\t{message}\n')

            with open(self.path_function / f'{file_id}.notes.{hash_}.csv', 'w') as f:
                for (page_index, bbox), messages in self.warnings_in_comment.items():
                    coords_str = '\t'.join(list(map(str, bbox.summary)))
                    for message in messages:
                        f.write(f'{page_index}\t{logging.WARNING}\t{coords_str}\t{message}\n')

                for (page_index, bbox), messages in self.errors_in_comment.items():
                    coords_str = '\t'.join(list(map(str, bbox.summary)))
                    for message in messages:
                        f.write(f'{page_index}\t{logging.ERROR}\t{coords_str}\t{message}\n')
        else:
            logger_.info('[SummaryVisualization] The errors and warnings is None')

    def save(self):
        self._save_file('all', self.objects)

    def _get_color(self, color: str | None, verbose: SummaryVerbose) -> str:
        if color is None:
            match verbose:
                case SummaryVerbose.MAIN:
                    color = 'darkgreen'
                case SummaryVerbose.ADDITIONAL_MAIN:
                    color = 'royalblue'
                case SummaryVerbose.ADDITIONAL:
                    color = 'red'
                case _:
                    color = 'black'

        return color

    def add_label(
        self,
        file_id: str,
        label: str,
        *,
        color: str | None,
        label_pos: str | int,
        verbose: SummaryVerbose,
    ):
        color = self._get_color(color, verbose)

        self.objects.append(
            {
                'file_id': _clear_file_id(file_id),
                'figure': {'type': FigureType.label.name, 'coors': None},
                'color': color,
                'label': label,
                'label_pos': label_pos,
                'verbose': verbose.value,
            }
        )

    def add_points(
        self,
        file_id: str,
        points: list[Point],
        color: str | None,
        verbose: SummaryVerbose,
    ) -> None:
        color = self._get_color(color, verbose)

        for point in points:
            self.objects.append(
                {
                    'file_id': _clear_file_id(file_id),
                    'figure': {
                        'type': FigureType.point.name,
                        'coords': (point + self._offset).summary,
                    },
                    'color': color,
                    'label': '',
                    'label_pos': None,
                    'verbose': verbose.value,
                }
            )

    def add_rectangle(
        self,
        file_id: str,
        rectangle: BoundingBox,
        verbose: SummaryVerbose = SummaryVerbose.ADDITIONAL_MAIN,
        color: str | None = None,
        label: str = '',
        label_pos: LabelPosition | None = None,
    ) -> None:
        color = self._get_color(color, verbose)

        if label_pos is None:
            label_pos = LabelPosition.tm

        json_ = {
            'file_id': _clear_file_id(file_id),
            'figure': {
                'type': FigureType.rectangle.name,
                'coords': (rectangle + self._offset).summary,
            },
            'color': color,
            'label': label,
            'label_pos': label_pos.value,
            'verbose': verbose.value,
        }

        self.objects.append(json_)

    def add_logger_handler(self, logger: logging.Logger) -> logging.Logger:
        logger.setLevel(level=logging.INFO)

        handler_summary = None
        stream_handler_added = False
        for handler in logger.handlers:
            if isinstance(handler, self.SummaryHandler):
                handler_summary = handler
            if isinstance(handler, logging.StreamHandler):
                stream_handler_added = True

        if handler_summary is not None:
            logger.removeHandler(handler_summary)

        handler = self.SummaryHandler(self)
        handler.setLevel(logger.level)
        logger.addHandler(handler)

        if not stream_handler_added:
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)

        self.logger = logger
        return logger

    def update_rectangle(
        self,
        file_id: str,
        rectangle: BoundingBox,
        verbose: SummaryVerbose = SummaryVerbose.ADDITIONAL_MAIN,
        color: str | None = None,
        label: str = '',
        label_pos: LabelPosition | None = None,
    ) -> None:
        if color is None:
            match verbose:
                case SummaryVerbose.MAIN:
                    color = 'darkgreen'
                case SummaryVerbose.ADDITIONAL_MAIN:
                    color = 'orange'
                case SummaryVerbose.ADDITIONAL:
                    color = 'red'
                case _:
                    color = 'black'

        if label_pos is None:
            label_pos = LabelPosition.tm

        json_ = {
            'file_id': _clear_file_id(file_id),
            'figure': {
                'type': FigureType.rectangle.name,
                'coords': (rectangle + self._offset).summary,
            },
            'color': color,
            'label': label,
            'label_pos': label_pos.value,
            'verbose': verbose.value,
        }

        keys_to_compare = ['file_id', 'figure', 'label', 'label_pos', 'verbose']
        for obj in self.objects:
            if all(obj[k] == json_[k] for k in keys_to_compare):
                obj.update(json_)

    def warning(self, message: str) -> None:
        pass

    def error(self):
        pass
