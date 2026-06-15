class CaptionNotFoundError(Exception):
    def __init__(self, page_index: int, table_number: int):
        super().__init__(f'The caption was not found on page {page_index} for table {table_number}')


class ReferenceNotFoundError(Exception):
    def __init__(self, page_index: int, table_number: int):
        super().__init__(f'The reference was not found on page {page_index} for table {table_number}')


from src.structures import ErrorMessage


# ====================== Semantic Checks module errors ======================
class SemanticErrorMessages:
    """
    Error messages for the 'semantic checks' module (research/vershinin/semantic_checks).
    Mirror style of src.frontpage.checker.errors.ErrorMessages.
    """

    current_language: str = 'ru'

    @classmethod
    def set_language(cls, lang: str) -> None:
        if lang not in ('ru', 'en'):
            raise ValueError("Language must be 'ru' or 'en')")
        cls.current_language = lang

    @classmethod
    def get(cls, msg: ErrorMessage) -> str:
        return msg.en if cls.current_language == 'en' else msg.ru

    # LLM adapter errors
    LLM_EMPTY_CHOICES = ErrorMessage('LLM EMPTY CHOICES', 'LLM вернул пустые варианты')
    LLM_REQUEST_FAILED = ErrorMessage('LLM REQUEST FAILED', 'Ошибка запроса к LLM')
    LLM_INVALID_JSON = ErrorMessage('LLM INVALID JSON', 'LLM вернул невалидный JSON')


class InvalidLLMJsonError(Exception):
    """Raised when LLM returns invalid JSON that cannot be parsed."""

    def __init__(self, raw_content: str, required_keys: list[str] | None = None):
        self.raw_content = raw_content
        self.required_keys = required_keys
        super().__init__(f'Invalid JSON from LLM. Raw content: {raw_content[:200]}...')


__all__ = [
    'CaptionNotFoundError',
    'ReferenceNotFoundError',
    'SemanticErrorMessages',
]
