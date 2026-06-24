from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nodes.detection_document_structure.main import task_detection_document_structure
from nodes.parsing_pdf.main import task_parsing_pdf
from nodes.visualization_output_pdf import task_visualize_output_pdf_file
from references_normcontrol.references_validation import task_validate_references
from src import paths

logger = logging.getLogger(__name__)


def build_workdir_name(path_input: Path) -> str:
    """Сформировать имя рабочей папки для одного входного файла."""
    return path_input.stem.replace(' ', '_')


def prepare_pdf_workdir(path_pdf: Path, output_dir: Path, *, workdir_name: str | None = None) -> Path:
    """
    Подготовить рабочую директорию PDF-пайплайна.

    PDF копируется в рабочую директорию под стандартным именем ``input.pdf``,
    потому что legacy-задачи проекта ожидают именно это имя.
    """
    workdir = output_dir / (workdir_name or build_workdir_name(path_pdf))
    (workdir / paths.FOLDER_SUMMARY).mkdir(parents=True, exist_ok=True)
    shutil.copy(path_pdf, workdir / paths.FILE_PDF_FILE_NAME)
    return workdir


def run_pdf_references_validation(path_pdf: Path, output_dir: Path, *, verbose: int = 3) -> Path:
    """Подготовить workdir и запустить полный PDF-пайплайн проверки списка литературы."""
    logger.info('Обработка PDF %s', path_pdf.name)
    workdir = prepare_pdf_workdir(path_pdf, output_dir)
    run_pdf_references_validation_in_workdir(workdir, verbose=verbose)
    return workdir


def run_pdf_references_validation_in_workdir(workdir: Path, *, verbose: int = 3) -> Path:
    """Запустить parsing -> structure detection -> references validation -> PDF visualization."""
    workdir = Path(workdir)
    path_pdf = workdir / paths.FILE_PDF_FILE_NAME
    if not path_pdf.exists():
        raise FileNotFoundError(f'В рабочей папке нет {paths.FILE_PDF_FILE_NAME}: {workdir}')

    run_pdf_structure_extraction_in_workdir(workdir)
    task_validate_references(None, workdir)
    task_visualize_output_pdf_file(None, path_pdf, verbose=verbose)
    return workdir


def run_pdf_structure_extraction_in_workdir(workdir: Path) -> Path:
    """Запустить только PDF parsing -> structure detection без rule-based проверки."""
    workdir = Path(workdir)
    path_pdf = workdir / paths.FILE_PDF_FILE_NAME
    if not path_pdf.exists():
        raise FileNotFoundError(f'В рабочей папке нет {paths.FILE_PDF_FILE_NAME}: {workdir}')

    task_parsing_pdf(None, str(workdir))
    task_detection_document_structure(None, workdir)
    return workdir
