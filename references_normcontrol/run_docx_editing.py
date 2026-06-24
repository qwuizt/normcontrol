from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import replace
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
from references_normcontrol.llm_utils import load_env_file  # noqa: E402
from references_normcontrol.pdf_pipeline import (  # noqa: E402
    run_pdf_references_validation_in_workdir,
    run_pdf_structure_extraction_in_workdir,
)
from references_normcontrol.reference_agent import (  # noqa: E402
    GigaChatGost2017BaselineValidator,
    GigaChatReferenceAgentValidator,
    REFERENCE_REPORT_FILENAME,
    build_reference_report,
    load_examples_or_warn,
    reference_report_to_replacement_rules,
    save_reference_report,
)
from references_normcontrol.reference_report_visualization import visualize_reference_report_on_pdf  # noqa: E402
from src import paths  # noqa: E402

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
    reference_examples_path: Path | None = None,
    llm_baseline: bool = False,
) -> Path:
    if not path_docx.exists():
        raise FileNotFoundError(f'DOCX не найден: {path_docx}')
    if path_rules is not None and not path_rules.exists():
        raise FileNotFoundError(f'Файл правил не найден: {path_rules}')
    workdir = prepare_workdir(path_docx, output_dir)
    input_docx = workdir / 'input.docx'
    output_docx = workdir / 'edited_tracked.docx'
    path_report = workdir / 'docx_edits_report.json'
    path_context = workdir / 'docx_context.json'
    path_references = workdir / 'docx_references.json'
    path_proposals = workdir / 'docx_edit_proposals.json'
    path_number_fix_rules = workdir / 'docx_number_fix_rules.json'
    path_reference_agent_rules = workdir / 'reference_agent_replacement_rules.json'
    path_reference_report = workdir / REFERENCE_REPORT_FILENAME

    if convert_docx_to_pdf:
        convert_docx_to_pdf_with_word(input_docx, workdir)
        pdf_workdir = workdir
        if validate_converted_pdf:
            run_pdf_references_validation_in_workdir(workdir, verbose=pdf_verbose)
        else:
            run_pdf_structure_extraction_in_workdir(workdir)
    elif pdf_workdir is not None:
        pdf_workdir = Path(pdf_workdir)
        if not (pdf_workdir / paths.FILE_STRUCTURE).exists() and (pdf_workdir / paths.FILE_PDF_FILE_NAME).exists():
            run_pdf_structure_extraction_in_workdir(pdf_workdir)

    rules = load_replacement_rules(path_rules) if path_rules is not None else []
    context = build_docx_context(input_docx)
    save_docx_context(path_context, context)
    reference_index = build_reference_index(context)
    save_reference_index(path_references, reference_index)
    proposals = build_edit_proposals(reference_index, rules)
    save_edit_proposals(path_proposals, proposals)
    number_fix_rules = build_number_fix_rules(reference_index)
    save_replacement_rules(path_number_fix_rules, number_fix_rules)
    if llm_baseline:
        examples, resolved_examples_path, report_warnings = [], None, []
    else:
        examples, resolved_examples_path, report_warnings = load_examples_or_warn(reference_examples_path)
    validator = None
    if llm_baseline:
        load_env_file(env_file or Path(__file__).with_name('.env'))
        validator = GigaChatGost2017BaselineValidator()
    elif use_gigachat:
        load_env_file(env_file or Path(__file__).with_name('.env'))
        validator = GigaChatReferenceAgentValidator()
    reference_report = build_reference_report(
        reference_index,
        examples,
        examples_source=resolved_examples_path,
        validator=validator,
    )
    if report_warnings:
        reference_report = replace(reference_report, warnings=[*reference_report.warnings, *report_warnings])
    save_reference_report(path_reference_report, reference_report)
    reference_agent_rules = reference_report_to_replacement_rules(
        reference_report,
        reference_index,
        min_confidence=gigachat_min_confidence,
    )
    save_replacement_rules(path_reference_agent_rules, reference_agent_rules)
    path_reference_output_pdf = workdir / paths.FILE_PDF_FILE_OUTPUT
    if pdf_workdir is not None and (Path(pdf_workdir) / paths.FILE_STRUCTURE).exists():
        visualize_reference_report_on_pdf(Path(pdf_workdir), reference_report, output_pdf=path_reference_output_pdf)
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
        [*rules, *reference_agent_rules],
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
    logger.info('Агентный отчет по источникам сохранен: %s', path_reference_report)
    logger.info('Правила Track Changes из агентного отчета сохранены: %s', path_reference_agent_rules)
    if pdf_workdir is not None:
        logger.info('PDF-визуализация агентных замечаний сохранена: %s', path_reference_output_pdf)
    if use_gigachat:
        logger.info('GigaChat использован как основной агент проверки источников')
    if llm_baseline:
        logger.info('GigaChat использован в baseline-режиме переписывания по ГОСТ 7.32-2017')
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
        help='Legacy-режим: после --convert-docx-to-pdf запустить старый rule-based PDF validator.',
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
        help='Использовать GigaChat как основной LLM-агент проверки источников.',
    )
    parser.add_argument(
        '--llm-baseline',
        action='store_true',
        help='Baseline: попросить GigaChat переписать каждый источник по ГОСТ 7.32-2017 без базы примеров.',
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
        help='Файл с переменными окружения GigaChat.',
    )
    parser.add_argument(
        '--reference-examples',
        type=Path,
        default=None,
        help='XLSX/CSV/JSON-база эталонных примеров оформления источников. По умолчанию ищется reference_examples.json, затем XLSX на Desktop.',
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
        reference_examples_path=args.reference_examples,
        llm_baseline=args.llm_baseline,
    )


if __name__ == '__main__':
    main()
