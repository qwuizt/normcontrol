import logging
from pathlib import Path
from typing import TYPE_CHECKING

import hydra
from omegaconf import DictConfig

from nodes import names_dqc
from src import paths
from src.detection_document_structure import detect_document_structure
from src.models.model_text_extraction import PyMuPDFModel

if TYPE_CHECKING:
    from airflow.models import TaskInstance

logger = logging.getLogger(__name__)


@hydra.main(config_path='configs', config_name='main.yaml', version_base=None)
def task_docker_detection_document_structure(cfg: DictConfig) -> str:
    """Распознать и сохранить структуру документа, то есть на какой странице какой структурный элемент"""

    # Путь к pdf файлу, который где-то лежит
    path_pdf = Path(cfg.paths.pdf_file)

    model = PyMuPDFModel(path_pdf)
    path_file_doc_structure = detect_document_structure(path_pdf, model)

    # Для xcom, абсолютные пути
    return str(path_file_doc_structure)


def task_detection_document_structure(
    ti: 'TaskInstance', work_dir: Path | None = None, front_kwargs: str | None = None
) -> None:
    """Распознать и сохранить структуру документа, то есть на какой странице какой структурный элемент"""
    logging.info('Start task detection document structure')

    if work_dir is None:
        work_dir_str: str = ti.xcom_pull(names_dqc.TASK_START_DAG)
        work_dir: Path = Path(work_dir_str)

    logging.info('Work dir %s', str(work_dir))

    model = PyMuPDFModel(work_dir / paths.FILE_PDF_FILE_NAME)
    df_structure = detect_document_structure(work_dir / paths.FILE_PDF_FILE_NAME, model)

    # save DataFrame to work dir
    df_structure.to_csv(work_dir / paths.FILE_STRUCTURE, index=False)
    logging.info('File was saved by path %s', str(work_dir / paths.FILE_STRUCTURE))


if __name__ == '__main__':
    task_docker_detection_document_structure()
    exit(0)
