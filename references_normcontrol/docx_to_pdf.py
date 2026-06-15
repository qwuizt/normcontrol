from __future__ import annotations

import logging
from pathlib import Path

from docx2pdf import convert

logger = logging.getLogger(__name__)


def convert_docx_to_pdf_with_word(path_docx: Path, output_dir: Path, *, output_name: str = 'input.pdf') -> Path:
    """
    Конвертировать DOCX в PDF через docx2pdf / Microsoft Word.

    Это исследовательская заглушка для НИР: она требует установленный
    Microsoft Word и не подходит как переносимое production-решение. Плюс
    такого подхода - PDF ближе к отображению Word, чем при рендеринге через
    LibreOffice.
    """
    path_docx = Path(path_docx)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path_pdf = output_dir / output_name

    if not path_docx.exists():
        raise FileNotFoundError(f'DOCX не найден: {path_docx}')
    if path_docx.suffix.lower() != '.docx':
        raise ValueError(f'Ожидался .docx файл: {path_docx}')

    logger.info('Конвертация DOCX в PDF через docx2pdf: %s -> %s', path_docx, path_pdf)
    convert(str(path_docx), str(path_pdf))

    if not path_pdf.exists():
        raise FileNotFoundError(f'docx2pdf завершился без создания PDF: {path_pdf}')
    return path_pdf
