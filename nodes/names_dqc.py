# Инициализация запуска DAG (скопировать данных во временную директорию)
TASK_START_DAG: str = 'task_start_dag'

TASK_PDF_TO_NPY: str = 'task_pdf2npy'

TASK_TITUL_VALIDATION: str = 'task_titul_validation'

TASK_PDF_STRUCTURE_RECOGNITION: str = 'task_structure_recognition'

TASK_PDF_CONTENT_RECOGNITION: str = 'task_content_recognition'

# Детекция таблиц, рисунков, формул и других элементов
TASK_DETECTION_PAGE_ELEMENTS: str = 'task_page_elements_detection'

TASK_VALIDATION_PDF_STRUCTURE: str = 'task_validate_pdf_structure'

TASK_VALIDATION_SECTION_HEADERS: str = 'task_validate_section_headers'

TASK_VALIDATION_PERFORMERS: str = 'task_validate_performers'

TASK_VALIDATION_FIGURES: str = 'task_validate_figures'
TASK_VALIDATION_TABLES: str = 'task_validate_tables'
TASK_VALIDATION_INTERVALS: str = 'task_validate_intervals'

TASK_VISUALIZE: str = 'task_visualize'

# Загрузить все результаты на S3
TASK_FINISH_DAG: str = 'task_upload_results_to_s3'

# Error handling tasks
TASK_CHECK_TELEGRAM_RUN: str = 'check_telegram_run'
TASK_PREPARE_ADMIN_EMAIL: str = 'prepare_email_body'
TASK_SEND_ADMIN_EMAIL: str = 'send_admin_email'
TASK_SKIP_ADMIN_EMAIL: str = 'skip_admin_email'
