import io
import logging
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from nodes import names_dqc
from src import paths
from src.communication.boto_handler import ObjectStorage
from src.paths import FOLDER_SUMMARY

if TYPE_CHECKING:
    from airflow.models import TaskInstance

logger = logging.getLogger(__name__)


def init_settings(
    path_s3_pdf: str,
    root_folder: str,
    dir_hash: str | None = None,
    front_kwargs: str | None = None,
):
    """
    Загрузить pdf файл из s3 хранилища в локальное и создать вспомогательные директории

    :param path_s3_pdf: путь к файлу на s3
    :param root_folder: путь к рабочей директории для airflow
    :param dir_hash: хеш директории (чтобы детерминировать запуски)
    :param front_kwargs: вспомогательные аргументы (используются в pre и post хуках)
    :return:
    """
    logger.info('Путь к переданному PDF файлу: "%s"', path_s3_pdf)

    root_folder = Path(root_folder)

    if dir_hash is None:
        dir_hash = uuid.uuid4().hex  # Уникальный путь для каждого запуска

    workdir = root_folder / dir_hash
    workdir.mkdir(parents=True, exist_ok=True)
    logger.info('Путь для хранения файлов текущего запуска: "%s"', str(workdir))

    # Скачаем pdf файл с s3 и поместим его в рабочую директорию
    object_storage = ObjectStorage()
    pdf_bytes = object_storage.open(path_s3_pdf)
    with open(workdir / paths.FILE_PDF_FILE_NAME, 'wb') as f:
        # Используем имя по умолчанию
        f.write(pdf_bytes)

    # На всякий случай в папку еще закинем исходный файл, чтобы хоть как-то сравнивать можно было
    shutil.copy(workdir / paths.FILE_PDF_FILE_NAME, workdir / path_s3_pdf.rsplit('/', 1)[-1])

    logger.info(
        'Скопированный PDF файл "%s" сохранен по пути "%s"', str(workdir), str(workdir / paths.FILE_PDF_FILE_NAME)
    )

    path_summary = workdir / FOLDER_SUMMARY
    path_summary.mkdir(parents=True, exist_ok=True)
    logger.info('Директория для логирования всех проверок находится по пути: "%s"', str(path_summary))

    return str(workdir)


def upload_data_to_s3(ti: 'TaskInstance', front_kwargs: str | None = None):
    work_dir_str: str = ti.xcom_pull(names_dqc.TASK_START_DAG)
    work_dir: Path = Path(work_dir_str)

    object_storage = ObjectStorage()

    s3_path_output = f'output/{work_dir.name}'

    with open(work_dir / paths.FILE_PDF_FILE_OUTPUT, 'rb') as f:
        file_io = io.BytesIO(f.read())
        object_storage.save(f'{s3_path_output}/{paths.FILE_PDF_FILE_OUTPUT}', file_io)

    with open(work_dir / paths.FILE_WARNINGS, 'rb') as f:
        file_io = io.BytesIO(f.read())
        object_storage.save(f'{s3_path_output}/{paths.FILE_WARNINGS}', file_io)

    with open(work_dir / paths.FILE_ERRORS, 'rb') as f:
        file_io = io.BytesIO(f.read())
        object_storage.save(f'{s3_path_output}/{paths.FILE_ERRORS}', file_io)

    print(s3_path_output)
    return s3_path_output  # send to xcom
