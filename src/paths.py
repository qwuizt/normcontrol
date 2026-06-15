from pathlib import Path

from src.structures import Paths

NAME_PDF = 'input.pdf'

FOLDER_SUMMARY = 'summary'

FOLDER_DPI_PREFIX = 'dpi'
FOLDER_NPY_DPI_50 = 'dpi50'
FOLDER_NPY_DPI_150 = 'dpi150'

FILE_PDF_FILE_NAME: str = 'input.pdf'  # Для каждого запуска dag он будет называться всегда одинаково
FILE_PDF_FILE_OUTPUT: str = 'output.pdf'  # Для каждого запуска dag он будет называться всегда одинаково
FILE_CONTENT = 'content.json'  # json файл с содержанием документа
FILE_STRUCTURE = 'structure.csv'  # csv файл со структурой документ (0 -> титульная, 1 -> исполнители и тд)
FILE_DOC_ELEMENTS = 'page_elements.csv'

FILE_WARNINGS = 'warnings.txt'
FILE_ERRORS = 'errors.txt'


def create_paths_object(path_pdf: Path) -> Paths:
    return Paths(
        path_pdf=path_pdf,
        path_150=path_pdf.parent / FOLDER_NPY_DPI_150,
        path_file_structure=path_pdf.parent / FILE_STRUCTURE,
        path_file_content=path_pdf.parent / FILE_CONTENT,
        path_doc_elements=path_pdf.parent / FILE_DOC_ELEMENTS,
        path_summary=path_pdf.parent / FOLDER_SUMMARY,
    )
