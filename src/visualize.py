import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from src import paths
from src.structures import Point, BoundingBox, FigureType, LabelPosition
from src.tools.summary_visualization import SummaryVerbose

logger = logging.getLogger(__name__)


def get_page_index(page_name: str) -> int:
    match = re.search(r'(\d+)$', page_name)
    if match is None:
        raise ValueError(f'Не удалось извлечь номер страницы из "{page_name}"')
    return int(match.group(1))


@dataclass
class PageInfo:
    width: float
    height: float
    coefs: tuple[float, float]
    angle: int


def _rotate_box_if_necessary(box: BoundingBox, page_info: PageInfo) -> BoundingBox:
    if page_info.angle == 90:
        return BoundingBox(
            top=int(page_info.width - box.right), left=box.top, bottom=int(page_info.width - box.left), right=box.right
        )
    if page_info.angle == 270:
        return BoundingBox(
            top=box.left,
            left=int(page_info.height - box.bottom),
            bottom=box.right,
            right=int(page_info.height - box.top),
        )

    return box


def _rotate_point_if_necessary(point: Point, page_info: PageInfo) -> Point:
    if page_info.angle in {90, 270}:
        return Point(row=int(page_info.width - point.row), col=point.col)

    return point


def _get_json_by_prefix(path_summary: Path, prefix: str):
    for json_path in path_summary.rglob(f'{prefix}.*.json'):
        with open(json_path, 'r', encoding='windows-1251') as f:
            json_ = json.load(f)

            if isinstance(json_, list):
                for item_ in json_:
                    yield item_
            else:
                yield json_


def _get_general_json(path_summary: Path, prefix: str) -> dict[str, list[dict]]:
    json_general_by_files: dict[str, list[dict]] = {}

    for json_ in _get_json_by_prefix(path_summary, prefix):
        if json_['file_id'] not in json_general_by_files:
            json_general_by_files[json_['file_id']] = []
        json_general_by_files[json_['file_id']].append(json_)

    return json_general_by_files


class PageNotes:
    class _Notes:
        def __init__(self) -> None:
            self.warnings: list[str] = []
            self.errors: list[str] = []

        def add_message(self, loglevel: int, message: str) -> None:
            if loglevel == logging.WARNING:
                self.warnings.append(message)
            elif loglevel == logging.ERROR:
                self.errors.append(message)
            else:
                raise ValueError(f'Unknown loglevel: {loglevel}')

    def __init__(self) -> None:
        self.notes: dict[BoundingBox, PageNotes._Notes] = {}

    def add_message(self, bbox: BoundingBox, loglevel: int, message: str) -> None:
        self.notes.setdefault(bbox, PageNotes._Notes()).add_message(loglevel, message)


def _load_notes(path_summary: Path, prefix: str) -> dict[str, PageNotes]:
    notes: dict[str, PageNotes] = {}

    for path_csv in path_summary.rglob(f'{prefix}.*.csv'):
        with open(path_csv, 'r', encoding='utf-8') as f:
            for line in f.readlines():
                line = line.strip().split('\t')
                bbox = BoundingBox(*list(map(int, line[2:6])))

                notes.setdefault(f'page-{line[0]}', PageNotes()).add_message(bbox, int(line[1]), line[-1])

    return notes


def _draw_point(
    draw: pymupdf.Shape,
    item_: dict[str, int | str | dict[str, int]],
    page_info: PageInfo,
    radius_default=3,
) -> None:
    radius: float = item_.get('radius', radius_default)

    p = Point(*item_['figure']['coords'])
    p = Point(row=int(p.row * page_info.coefs[1]), col=int(p.col * page_info.coefs[0]))
    p = _rotate_point_if_necessary(p, page_info)

    draw.draw_circle((p.col - radius, p.row - radius), radius)
    draw.finish(fill=_to_rgb(item_['color']), fill_opacity=0.3, color=_to_rgb(item_['color']), width=1)


def _to_rgb(color: str | tuple[float, float, float]) -> tuple[float, float, float]:
    if isinstance(color, tuple):
        return color

    match color:
        case 'red':
            return 0.55, 0.0, 0.0
        case 'darkred':
            return 1.0, 0.0, 0.0
        case 'green':
            return 0.0, 0.39, 0.0
        case 'darkgreen':
            return 0.0, 1.0, 0.0
        case 'blue':
            return 0.0, 0.0, 1.0
        case 'darkblue':
            return 0.0, 0.0, 0.55
        case 'royalblue':
            return 0.0, 0.14, 0.4
        case 'gray':
            return 0.5, 0.5, 0.5

    raise KeyError(f'Unknown color: {color}')


def _draw_rectangle(
    shape: pymupdf.Shape,
    item_: dict[str, int | str | dict[str, int]],
    page_info: PageInfo,
    width_default=2,
) -> None:
    width = item_.get('width', width_default)
    color = item_['color']

    box = BoundingBox(*item_['figure']['coords'])
    box = BoundingBox(
        left=int(box.left * page_info.coefs[0]),
        top=int(box.top * page_info.coefs[1]),
        right=int(box.right * page_info.coefs[0]),
        bottom=int(box.bottom * page_info.coefs[1]),
    )
    box = _rotate_box_if_necessary(box, page_info)

    shape.draw_rect(box.rectangle_for_pymupdf)
    shape.finish(fill_opacity=0.3, color=_to_rgb(color), width=width)

    if label := item_.get('label', None):
        box_label = BoundingBox(top=box.top - 16, left=0, bottom=box.top + 30, right=box.right)
        if page_info.angle == 270:
            box_label = BoundingBox(top=box.top, left=box.right - 30, bottom=box.bottom, right=box.right + 16)

        res = _draw_label(
            shape,
            {'label': label, 'color': color, 'font_size': 10},
            pos=box_label.rectangle_for_pymupdf,
            anchor='tr',
            page_info=page_info,
        )
        if res < 0:
            logger.warning('The label %s not drawn into box %s', label, str(box_label))


def _draw_label(
    shape: pymupdf.Shape,
    item_: dict[str, int | str],
    pos: tuple[int, int, int, int] | list[int],
    anchor: str | None = None,
    page_info: PageInfo | None = None,
) -> float:
    align = pymupdf.TEXT_ALIGN_RIGHT
    if anchor is not None:
        match LabelPosition[anchor]:
            case LabelPosition.tl | LabelPosition.cl | LabelPosition.bl:
                align = pymupdf.TEXT_ALIGN_LEFT
            case LabelPosition.tm | LabelPosition.cm | LabelPosition.bm:
                align = pymupdf.TEXT_ALIGN_CENTER
            case LabelPosition.tr | LabelPosition.cr | LabelPosition.br:
                align = pymupdf.TEXT_ALIGN_RIGHT
            case _:
                logger.warning('Unknown label_pos %s', anchor)

    font_size = item_.get('font_size', 12)  # (0, 750, 595, 800)

    return shape.insert_textbox(
        pos,
        buffer=unicodedata.normalize('NFC', item_['label']),
        align=align,
        fontsize=font_size,
        fontname='myhelv',  # Задается для страницы
        color=_to_rgb(item_['color']),
        lineheight=0,
        rotate=page_info.angle if page_info is not None else 0,
    )


def _draw_figure(
    page: pymupdf.Page,
    shape: pymupdf.Shape,
    item_: dict,
    page_info: PageInfo,
    verbose: SummaryVerbose.ADDITIONAL_MAIN,
) -> None:
    if SummaryVerbose(item_['verbose']).value <= verbose.value:
        mediabox = page.mediabox

        match FigureType[item_['figure']['type']]:
            case FigureType.point:
                _draw_point(shape, item_, page_info=page_info)
            case FigureType.rectangle:
                _draw_rectangle(shape, item_, page_info=page_info)
            case FigureType.label:
                _draw_label(shape, item_, pos=(0, 10, int(mediabox.width), 40), anchor='cm')
            case _:
                logger.warning('Unknown type: "%s"', str(item_['figure']['type']))


def _draw_note(page: pymupdf.Page, notes: PageNotes, page_info: PageInfo) -> None:
    if not notes:
        return

    coefs = page_info.coefs

    # Сортируем от больших к меньшим, чтобы не было перекрытия
    for bbox, notes_obj in sorted(list(notes.notes.items()), key=lambda kv: -kv[0].area):
        bbox_is_unknown = bbox.top == 0 and bbox.bottom == 0
        if bbox_is_unknown:
            bbox = BoundingBox(0, 0, 100, int(page.mediabox.width))
        else:
            bbox = BoundingBox(
                int(bbox.top * coefs[0]),
                int(bbox.left * coefs[1]),
                int(bbox.bottom * coefs[0]),
                int(bbox.right * coefs[1]),
            )
        bbox = _rotate_box_if_necessary(bbox, page_info)

        msg = 'Ошибки:\n' if notes_obj.errors else ''
        for error in notes_obj.errors:
            msg += f'- {error}\n'

        msg += '\nПредупреждения:\n' if notes_obj.warnings else ''
        for warning in notes_obj.warnings:
            msg += f'- {warning}\n'

        if not bbox_is_unknown:
            try:
                page.add_highlight_annot(bbox.rectangle_for_pymupdf)
            except ValueError:
                logger.warning('The bounding box does not fit inside the page')

        point = bbox.right - ((bbox.right - bbox.left) / 4), (bbox.top + bbox.bottom) / 2
        # point = (bbox.top + bbox.bottom) / 2, bbox.right - ((bbox.right - bbox.left) / 4)
        page.add_text_annot(point, msg)


def _add_notes_to_summary(notes_summary: list[str], page_name: str, notes: PageNotes | None):
    if notes is None:
        return []

    n_errors, n_warnings = 0, 0
    for notes_ in notes.notes.values():
        n_warnings += len(notes_.warnings)
        n_errors += len(notes_.errors)

    page_index = get_page_index(page_name)
    str_ = f'На странице "{page_index}" найдено {n_errors} ошибок и {n_warnings} предупреждений'
    notes_summary.append(str_)
    return notes_summary


def _draw_note_summary(page: pymupdf.Page, notes_summary: list[str]) -> None:
    point = 10, 10

    point_highlight = BoundingBox(top=point[1], left=point[0], bottom=point[1] + 20, right=point[0] + 20)
    page.add_highlight_annot(point_highlight.rectangle_for_pymupdf)

    str_ = '\n'.join(notes_summary)
    page.add_text_annot(point, str_)


def visualize(path_pdf: Path, path_summary: Path, verbose: SummaryVerbose.ADDITIONAL_MAIN) -> Path:
    json_general_by_files: dict[str, list[dict]] = _get_general_json(path_summary, 'all.figures')
    json_notes: dict[str, PageNotes] = _load_notes(path_summary, 'all.notes')

    notes_summary = []

    with pymupdf.open(path_pdf) as file:
        for page_number in range(file.page_count):
            page_name = f'page-{page_number}'
            logger.info('Process page number %d', page_number)

            page: pymupdf.Page = file[page_number]
            font = pymupdf.Font('helv')  # looks exactly equal to its Base-14 brother
            page.insert_font(fontname='myhelv', fontbuffer=font.buffer)

            shape: pymupdf.Shape = page.new_shape()  # start a Shape (canvas)

            width, height = page.mediabox.width, page.mediabox.height
            if page.rotation in {90, 270}:
                width, height = page.mediabox.height, page.mediabox.width

            page_info = PageInfo(width=width, height=height, coefs=(1, 1), angle=page.rotation)

            # personal file json's
            for item_ in _get_json_by_prefix(path_summary, page_name):
                try:
                    _draw_figure(page, shape, item_, page_info=page_info, verbose=verbose)
                except Exception as e:
                    logger.error('Ошибка визуализации для элемента "%s": %s', str(item_), str(e))

            # from general json of task
            for item_ in json_general_by_files.get(page_name, []):
                try:
                    _draw_figure(page, shape, item_, page_info=page_info, verbose=verbose)
                except Exception as e:
                    logger.error('Ошибка визуализации для элемента "%s": %s', str(item_), str(e))

            # from general notes of task
            try:
                _draw_note(page, json_notes.get(page_name, None), page_info=page_info)
                _add_notes_to_summary(notes_summary, page_name, json_notes.get(page_name, None))
            except Exception as e:
                logger.error('Ошибка добавления комментария для страницы "%s": %s', page_name, str(e))

            shape.commit(overlay=True)

        # Вставить summary ошибок на титульную (первую) страницу
        page_0: pymupdf.Page = file[0]
        _draw_note_summary(page_0, notes_summary)

        file.save(path_pdf.parent / paths.FILE_PDF_FILE_OUTPUT)

    messages_error = []
    messages_warning = []
    for messages_file_path in path_summary.rglob('all.messages.*.csv'):
        with open(messages_file_path, 'r') as f:
            lines = f.readlines()

        for line in lines:
            type_, message = line.split('\t')
            if int(type_) == logging.WARNING:
                messages_warning.append(message)
            elif int(type_) == logging.ERROR:
                messages_error.append(message)

    with open(path_summary.parent / paths.FILE_ERRORS, 'w') as f:
        f.writelines(messages_error)

    with open(path_summary.parent / paths.FILE_WARNINGS, 'w') as f:
        f.writelines(messages_warning)

    return path_pdf.parent / paths.FILE_PDF_FILE_OUTPUT
