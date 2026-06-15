import logging
from pathlib import Path

from nodes import names_dqc
from src import paths
from src.structures import Paths
from src.tools.summary_visualization import SummaryVerbose
from src.visualize import visualize


def task_visualize_output_pdf_file(ti, path_pdf: Path | None = None, verbose: int = 2, **kwargs) -> None:
    if path_pdf is None:
        work_dir_str: str = ti.xcom_pull(names_dqc.TASK_START_DAG)
        path_pdf: Path = Path(work_dir_str) / paths.FILE_PDF_FILE_NAME

    logging.info('Starting visualization for pdf: %s', str(path_pdf))

    paths_object = Paths.create(path_pdf.parent)

    if isinstance(verbose, str):
        verbose = int(verbose)

    verbose_enum = SummaryVerbose(verbose)
    visualize(path_pdf, paths_object.path_summary, verbose=verbose_enum)

