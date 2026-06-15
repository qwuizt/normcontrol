from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from references_normcontrol.docx_context import build_docx_context, save_docx_context  # noqa: E402
from references_normcontrol.docx_references import (  # noqa: E402
    build_edit_proposals,
    build_number_fix_rules,
    build_reference_index,
    save_edit_proposals,
    save_reference_index,
    save_replacement_rules,
)
from references_normcontrol.docx_tracked_editing import (  # noqa: E402
    apply_tracked_replacements,
    inspect_revision_markup,
    load_replacement_rules,
    save_results,
)
from references_normcontrol.docx_to_pdf import convert_docx_to_pdf_with_word  # noqa: E402
from references_normcontrol.gigachat_reference_editor import (  # noqa: E402
    GigaChatReferenceTextEditor,
    generate_reference_fixes,
    generated_fixes_to_replacement_rules,
    load_env_file,
    save_generated_reference_fixes,
)
from references_normcontrol.pdf_docx_reference_mapping import (  # noqa: E402
    build_pdf_docx_reference_mapping,
    save_pdf_docx_reference_mapping,
)
from references_normcontrol.pdf_pipeline import run_pdf_references_validation_in_workdir  # noqa: E402
from src import paths  # noqa: E402
from src.structures import Paths  # noqa: E402

logger = logging.getLogger(__name__)


def build_workdir_name(path_docx: Path) -> str:
    return path_docx.stem.replace(' ', '_')


def prepare_workdir(path_docx: Path, output_dir: Path) -> Path:
    workdir = output_dir / build_workdir_name(path_docx)
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(path_docx, workdir / 'input.docx')
    return workdir


def run_docx_editing(
    path_docx: Path,
    path_rules: Path | None,
    output_dir: Path,
    *,
    references_only: bool = True,
    author: str = 'AutoNormControl',
    pdf_workdir: Path | None = None,
    use_gigachat: bool = False,
    gigachat_min_confidence: float = 0.5,
    convert_docx_to_pdf: bool = False,
    validate_converted_pdf: bool = False,
    pdf_verbose: int = 3,
    env_file: Path | None = None,
) -> Path:
    if not path_docx.exists():
        raise FileNotFoundError(f'DOCX не найден: {path_docx}')
    if path_rules is not None and not path_rules.exists():
        raise FileNotFoundError(f'Файл правил не найден: {path_rules}')
    if use_gigachat and pdf_workdir is None and not (convert_docx_to_pdf and validate_converted_pdf):
        raise ValueError(
            'Для --use-gigachat нужно передать --pdf-workdir с результатами PDF-пайплайна '
            'или включить --convert-docx-to-pdf вместе с --validate-converted-pdf'
        )

    workdir = prepare_workdir(path_docx, output_dir)
    input_docx = workdir / 'input.docx'
    output_docx = workdir / 'edited_tracked.docx'
    path_report = workdir / 'docx_edits_report.json'
    path_context = workdir / 'docx_context.json'
    path_references = workdir / 'docx_references.json'
    path_proposals = workdir / 'docx_edit_proposals.json'
    path_number_fix_rules = workdir / 'docx_number_fix_rules.json'
    path_pdf_mapping = workdir / 'pdf_docx_reference_mapping.json'
    path_gigachat_fixes = workdir / 'gigachat_reference_fixes.json'
    path_gigachat_rules = workdir / 'gigachat_replacement_rules.json'

    if convert_docx_to_pdf:
        convert_docx_to_pdf_with_word(input_docx, workdir)
        pdf_workdir = workdir
        if validate_converted_pdf:
            run_pdf_references_validation_in_workdir(workdir, verbose=pdf_verbose)

    rules = load_replacement_rules(path_rules) if path_rules is not None else []
    context = build_docx_context(input_docx)
    save_docx_context(path_context, context)
    reference_index = build_reference_index(context)
    save_reference_index(path_references, reference_index)
    proposals = build_edit_proposals(reference_index, rules)
    save_edit_proposals(path_proposals, proposals)
    number_fix_rules = build_number_fix_rules(reference_index)
    save_replacement_rules(path_number_fix_rules, number_fix_rules)
    generated_rules = []
    if pdf_workdir is not None:
        if not (Path(pdf_workdir) / paths.FILE_STRUCTURE).exists():
            raise FileNotFoundError(
                f'Для связки PDF-замечаний нужен {paths.FILE_STRUCTURE} в pdf_workdir. '
                'Сначала запустите PDF parsing/detection/validation pipeline.'
            )
        pdf_mapping = build_pdf_docx_reference_mapping(Paths.create(Path(pdf_workdir)), reference_index)
        save_pdf_docx_reference_mapping(path_pdf_mapping, pdf_mapping)
        if use_gigachat:
            load_env_file(env_file or Path(__file__).with_name('.env'))
            editor = GigaChatReferenceTextEditor()
            generated_fixes = generate_reference_fixes(pdf_mapping.links, editor)
            save_generated_reference_fixes(path_gigachat_fixes, generated_fixes)
            generated_rules = generated_fixes_to_replacement_rules(
                generated_fixes,
                min_confidence=gigachat_min_confidence,
            )
            save_replacement_rules(path_gigachat_rules, generated_rules)
    logger.info(
        'DOCX проиндексирован: абзацев=%d, таблиц=%d, абзацев в списке литературы=%d, источников=%d',
        context.paragraph_count,
        context.table_count,
        sum(paragraph.in_references for paragraph in context.paragraphs),
        len(reference_index.entries),
    )

    results = apply_tracked_replacements(
        input_docx,
        output_docx,
        [*rules, *generated_rules],
        references_only=references_only,
        author=author,
    )
    inspection = inspect_revision_markup(output_docx)
    save_results(path_report, results, inspection)

    logger.info('DOCX с Track Changes сохранен: %s', output_docx)
    logger.info('Отчет сохранен: %s', path_report)
    logger.info('Контекст DOCX сохранен: %s', path_context)
    logger.info('Индекс источников сохранен: %s', path_references)
    logger.info('Предложения правок сохранены: %s', path_proposals)
    logger.info('Правила исправления номеров сохранены: %s', path_number_fix_rules)
    if pdf_workdir is not None:
        logger.info('Связка PDF-замечаний с DOCX сохранена: %s', path_pdf_mapping)
    if use_gigachat:
        logger.info('GigaChat-исправления сохранены: %s', path_gigachat_fixes)
        logger.info('GigaChat-правила Track Changes сохранены: %s', path_gigachat_rules)
    return workdir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Исследовательское редактирование DOCX в режиме Track Changes.')
    parser.add_argument('docx', type=Path, help='Входной DOCX-файл.')
    parser.add_argument('--rules', type=Path, default=None, help='JSON-файл с ручными заменами.')
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path(__file__).parent / 'output_docx',
        help='Папка для результатов.',
    )
    parser.add_argument(
        '--all-document',
        action='store_true',
        help='Разрешить замены по всему документу. По умолчанию правится только список литературы.',
    )
    parser.add_argument('--author', default='AutoNormControl', help='Автор правок Track Changes.')
    parser.add_argument(
        '--pdf-workdir',
        type=Path,
        default=None,
        help='Рабочая папка PDF-пайплайна с input.pdf и structure.csv для связки замечаний с DOCX.',
    )
    parser.add_argument(
        '--convert-docx-to-pdf',
        action='store_true',
        help='Сконвертировать входной DOCX в input.pdf через docx2pdf / Microsoft Word внутри workdir.',
    )
    parser.add_argument(
        '--validate-converted-pdf',
        action='store_true',
        help='После --convert-docx-to-pdf сразу запустить PDF parsing/detection/validation pipeline.',
    )
    parser.add_argument(
        '--pdf-verbose',
        type=int,
        default=3,
        choices=[1, 2, 3],
        help='Уровень детализации PDF-визуализации при --validate-converted-pdf.',
    )
    parser.add_argument(
        '--use-gigachat',
        action='store_true',
        help='Сгенерировать исправления библиографических записей через GigaChat по PDF-замечаниям.',
    )
    parser.add_argument(
        '--gigachat-min-confidence',
        type=float,
        default=0.5,
        help='Минимальная confidence из ответа GigaChat для применения Track Changes.',
    )
    parser.add_argument(
        '--env-file',
        type=Path,
        default=Path(__file__).with_name('.env'),
        help='Файл с переменными окружения GigaChat. По умолчанию: research/pimenov/.env',
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
    args = parse_args()
    run_docx_editing(
        args.docx,
        args.rules,
        args.output_dir,
        references_only=not args.all_document,
        author=args.author,
        pdf_workdir=args.pdf_workdir,
        use_gigachat=args.use_gigachat,
        gigachat_min_confidence=args.gigachat_min_confidence,
        convert_docx_to_pdf=args.convert_docx_to_pdf,
        validate_converted_pdf=args.validate_converted_pdf,
        pdf_verbose=args.pdf_verbose,
        env_file=args.env_file,
    )


if __name__ == '__main__':
    main()
