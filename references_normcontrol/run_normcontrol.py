from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from references_normcontrol.docx_to_pdf import convert_docx_to_pdf_with_word  # noqa: E402
from references_normcontrol.pdf_pipeline import run_pdf_references_validation  # noqa: E402
from references_normcontrol.run_docx_editing import run_docx_editing  # noqa: E402

logger = logging.getLogger(__name__)


def check_pdf(args: argparse.Namespace) -> Path:
    return run_pdf_references_validation(args.pdf, args.output_dir, verbose=args.verbose)


def check_docx(args: argparse.Namespace) -> Path:
    path_pdf = convert_docx_to_pdf_with_word(args.docx, args.work_dir)
    return run_pdf_references_validation(path_pdf, args.output_dir, verbose=args.verbose)


def edit_docx(args: argparse.Namespace) -> Path:
    return run_docx_editing(
        args.docx,
        args.rules,
        args.output_dir,
        references_only=not args.all_document,
        author=args.author,
        pdf_workdir=args.pdf_workdir,
        use_gigachat=args.use_gigachat,
        gigachat_min_confidence=args.gigachat_min_confidence,
        convert_docx_to_pdf=True,
        validate_converted_pdf=True,
        pdf_verbose=args.verbose,
        env_file=args.env_file,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Проверка и исправление списка литературы в PDF/DOCX.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    check_pdf_parser = subparsers.add_parser('check-pdf', help='Проверить список литературы во входном PDF.')
    check_pdf_parser.add_argument('pdf', type=Path, help='Входной PDF.')
    check_pdf_parser.add_argument('--output-dir', type=Path, default=Path('output'), help='Папка результатов.')
    check_pdf_parser.add_argument('--verbose', type=int, default=3, choices=[1, 2, 3])
    check_pdf_parser.set_defaults(func=check_pdf)

    check_docx_parser = subparsers.add_parser(
        'check-docx',
        help='Сконвертировать DOCX в PDF и проверить список литературы.',
    )
    check_docx_parser.add_argument('docx', type=Path, help='Входной DOCX.')
    check_docx_parser.add_argument('--work-dir', type=Path, default=Path('work_docx/check_docx'))
    check_docx_parser.add_argument('--output-dir', type=Path, default=Path('output'), help='Папка результатов.')
    check_docx_parser.add_argument('--verbose', type=int, default=3, choices=[1, 2, 3])
    check_docx_parser.set_defaults(func=check_docx)

    edit_docx_parser = subparsers.add_parser(
        'edit-docx',
        help='DOCX -> PDF -> проверка замечаний -> исправленный DOCX с Track Changes.',
    )
    edit_docx_parser.add_argument('docx', type=Path, help='Входной DOCX.')
    edit_docx_parser.add_argument('--rules', type=Path, default=None, help='JSON-файл с ручными заменами.')
    edit_docx_parser.add_argument('--output-dir', type=Path, default=Path('fixed_docx'), help='Папка результатов.')
    edit_docx_parser.add_argument(
        '--pdf-workdir',
        type=Path,
        default=None,
        help='Готовая рабочая папка PDF-пайплайна. Обычно не нужна: edit-docx запускает pipeline сам.',
    )
    edit_docx_parser.add_argument('--all-document', action='store_true')
    edit_docx_parser.add_argument('--author', default='AutoNormControl')
    edit_docx_parser.add_argument('--use-gigachat', action='store_true')
    edit_docx_parser.add_argument('--gigachat-min-confidence', type=float, default=0.5)
    edit_docx_parser.add_argument('--env-file', type=Path, default=Path('.env'))
    edit_docx_parser.add_argument('--verbose', type=int, default=3, choices=[1, 2, 3])
    edit_docx_parser.set_defaults(func=edit_docx)

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    args = parse_args()
    workdir = args.func(args)
    logger.info('Результаты сохранены в %s', workdir)


if __name__ == '__main__':
    main()
