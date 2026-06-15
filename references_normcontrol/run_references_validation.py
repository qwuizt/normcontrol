from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from references_normcontrol.pdf_pipeline import (  # noqa: E402
    prepare_pdf_workdir,
    run_pdf_references_validation,
)

logger = logging.getLogger(__name__)


def prepare_workdir(path_pdf: Path, output_dir: Path) -> Path:
    """Обратная совместимость со старым runner API."""
    return prepare_pdf_workdir(path_pdf, output_dir)


def run_validate_document(path_pdf: Path, output_dir: Path, verbose: int = 3) -> Path:
    """
    Запустить проверку списка источников для одного PDF-файла.

    Минимальный пайплайн: подготовка страниц PDF, распознавание структуры
    документа, проверка списка источников и сборка аннотированного PDF.
    """
    logger.info('Обработка файла %s', path_pdf.name)
    return run_pdf_references_validation(path_pdf, output_dir, verbose=verbose)


def run_folder(input_dir: Path, output_dir: Path, pattern: str = '*.pdf', verbose: int = 3) -> list[Path]:
    """
    Запустить проверку для всех PDF-файлов в папке.

    Поиск выполняется только на верхнем уровне папки, без рекурсивного обхода.
    Возвращается список рабочих директорий с результатами.
    """
    if not input_dir.exists():
        raise FileNotFoundError(f'Папка с PDF не найдена: {input_dir}')
    if not input_dir.is_dir():
        raise NotADirectoryError(f'Ожидалась папка с PDF: {input_dir}')

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_files = sorted(input_dir.glob(pattern))
    if not pdf_files:
        logger.warning('В папке %s не найдены файлы по шаблону %s', input_dir, pattern)
        return []

    workdirs: list[Path] = []
    for path_pdf in pdf_files:
        workdirs.append(run_validate_document(path_pdf, output_dir, verbose=verbose))
    return workdirs


def parse_args() -> argparse.Namespace:
    """Прочитать аргументы командной строки для локального запуска."""
    default_input_dir = Path(__file__).parent / 'input'
    default_output_dir = Path(__file__).parent / 'output'

    parser = argparse.ArgumentParser(description='Проверка списка использованных источников для PDF из папки.')
    parser.add_argument(
        'input_dir',
        nargs='?',
        type=Path,
        default=default_input_dir,
        help=f'Папка с PDF-файлами. По умолчанию: {default_input_dir}',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=default_output_dir,
        help=f'Папка для результатов. По умолчанию: {default_output_dir}',
    )
    parser.add_argument(
        '--pattern',
        default='*.pdf',
        help='Шаблон файлов внутри input_dir. По умолчанию: *.pdf',
    )
    parser.add_argument(
        '--verbose',
        type=int,
        default=3,
        choices=[1, 2, 3],
        help='Уровень детализации итогового PDF: 1, 2 или 3.',
    )
    return parser.parse_args()


def main() -> None:
    """Запустить проверку списка источников из командной строки."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    args = parse_args()
    workdirs = run_folder(args.input_dir, args.output_dir, pattern=args.pattern, verbose=args.verbose)
    for workdir in workdirs:
        logger.info('Результаты сохранены в %s', workdir)


if __name__ == '__main__':
    main()
