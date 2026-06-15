import logging
from pathlib import Path
from typing import TYPE_CHECKING

import hydra
from omegaconf import DictConfig

from nodes import names_dqc
from src import paths
from src.pdf2npy import pdf_to_npy

if TYPE_CHECKING:
    from airflow.models import TaskInstance

logger = logging.getLogger(__name__)


@hydra.main(config_path='configs', config_name='parsing_pdf.yaml', version_base=None)
def task_docker_parsing_pdf(cfg: DictConfig) -> None:
    logging.info('Start task parsing pdf file into *.npy by pages')

    path_pdf = Path(cfg.paths.pdf_file)

    pdf_to_npy(path_pdf)


def task_parsing_pdf(ti: 'TaskInstance', work_dir_str: str | None = None, front_kwargs: str | None = None) -> None:
    logging.info('Start task parsing pdf file into *.npy by pages')

    if work_dir_str is None:
        work_dir_str: str = ti.xcom_pull(names_dqc.TASK_START_DAG)

    work_dir: Path = Path(work_dir_str)

    pdf_to_npy(work_dir / paths.FILE_PDF_FILE_NAME)


if __name__ == '__main__':
    task_docker_parsing_pdf()
    exit(0)
