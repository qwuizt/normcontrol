from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from gigachat import GigaChat

from references_normcontrol.pdf_docx_reference_mapping import PdfIssueDocxLink
from references_normcontrol.docx_tracked_editing import TrackedReplacementRule

DEFAULT_ENV_PATH = Path(__file__).with_name('.env')


@dataclass(frozen=True)
class GeneratedReferenceFix:
    rule_id: str
    issue_message: str
    reference_number: int | None
    reference_ordinal_index: int | None
    target_paragraph_indexes: list[int]
    old_text: str
    new_text: str
    reason: str
    confidence: float
    applied_to_generation: bool
    error: str | None = None


class ReferenceTextEditor(Protocol):
    def generate_fix(self, link: PdfIssueDocxLink) -> GeneratedReferenceFix:
        """Сгенерировать исправленный текст библиографической записи."""


class StubReferenceTextEditor:
    """Детерминированная заглушка для тестов без внешнего API."""

    def generate_fix(self, link: PdfIssueDocxLink) -> GeneratedReferenceFix:
        old_text = link.docx_reference_text or link.pdf_reference_text or ''
        return GeneratedReferenceFix(
            rule_id=build_rule_id(link),
            issue_message=link.message,
            reference_number=link.reference_number,
            reference_ordinal_index=link.reference_ordinal_index,
            target_paragraph_indexes=link.docx_paragraph_indexes,
            old_text=old_text,
            new_text=old_text,
            reason='Заглушка: текст не изменялся',
            confidence=0.0,
            applied_to_generation=False,
        )


class GigaChatReferenceTextEditor:
    """
    Генератор исправлений библиографических записей через GigaChat.

    Credentials читаются только из env, чтобы не хранить секреты в коде:
    ``GIGACHAT_CREDENTIALS``, ``GIGACHAT_SCOPE``, ``GIGACHAT_MODEL``,
    ``GIGACHAT_VERIFY_SSL_CERTS``.
    """

    def __init__(
        self,
        *,
        credentials: str | None = None,
        scope: str | None = None,
        model: str | None = None,
        verify_ssl_certs: bool | None = None,
        timeout: float | None = 60.0,
    ) -> None:
        credentials = credentials or os.getenv('GIGACHAT_CREDENTIALS')
        if not credentials:
            raise ValueError('Для GigaChat нужен env GIGACHAT_CREDENTIALS')

        if verify_ssl_certs is None:
            verify_ssl_certs = parse_bool_env(os.getenv('GIGACHAT_VERIFY_SSL_CERTS'), default=True)

        self.client = GigaChat(
            credentials=credentials,
            scope=scope or os.getenv('GIGACHAT_SCOPE'),
            model=model or os.getenv('GIGACHAT_MODEL'),
            verify_ssl_certs=verify_ssl_certs,
            timeout=timeout,
        )

    def generate_fix(self, link: PdfIssueDocxLink) -> GeneratedReferenceFix:
        old_text = link.docx_reference_text or link.pdf_reference_text or ''
        if not old_text:
            return GeneratedReferenceFix(
                rule_id=build_rule_id(link),
                issue_message=link.message,
                reference_number=link.reference_number,
                reference_ordinal_index=link.reference_ordinal_index,
                target_paragraph_indexes=link.docx_paragraph_indexes,
                old_text='',
                new_text='',
                reason='Нет исходного текста источника',
                confidence=0.0,
                applied_to_generation=False,
                error='empty_reference_text',
            )

        prompt = build_reference_fix_prompt(link)
        try:
            response = self.client.chat(prompt)
            content = extract_response_content(response)
            payload = parse_json_object(content)
            new_text = str(payload.get('new_text', '')).strip()
            reason = str(payload.get('reason', '')).strip()
            confidence = clamp_float(payload.get('confidence', 0.0))
        except Exception as exc:
            return GeneratedReferenceFix(
                rule_id=build_rule_id(link),
                issue_message=link.message,
                reference_number=link.reference_number,
                reference_ordinal_index=link.reference_ordinal_index,
                target_paragraph_indexes=link.docx_paragraph_indexes,
                old_text=old_text,
                new_text=old_text,
                reason='GigaChat не вернул корректное исправление',
                confidence=0.0,
                applied_to_generation=False,
                error=str(exc),
            )

        if not new_text:
            return GeneratedReferenceFix(
                rule_id=build_rule_id(link),
                issue_message=link.message,
                reference_number=link.reference_number,
                reference_ordinal_index=link.reference_ordinal_index,
                target_paragraph_indexes=link.docx_paragraph_indexes,
                old_text=old_text,
                new_text=old_text,
                reason=reason or 'Пустой new_text в ответе GigaChat',
                confidence=confidence,
                applied_to_generation=False,
                error='empty_new_text',
            )

        return GeneratedReferenceFix(
            rule_id=build_rule_id(link),
            issue_message=link.message,
            reference_number=link.reference_number,
            reference_ordinal_index=link.reference_ordinal_index,
            target_paragraph_indexes=link.docx_paragraph_indexes,
            old_text=old_text,
            new_text=new_text,
            reason=reason,
            confidence=confidence,
            applied_to_generation=True,
        )


def load_env_file(path_env: Path = DEFAULT_ENV_PATH, *, override: bool = False) -> None:
    """
    Загрузить простой .env в os.environ без внешних зависимостей.

    Дальше параметры GigaChat читаются через ``os.getenv``. Значения из
    окружения не перезаписываются, если ``override=False``.
    """
    path_env = Path(path_env)
    if not path_env.exists():
        return

    for raw_line in path_env.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = strip_env_value(value.strip())
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def build_reference_fix_prompt(link: PdfIssueDocxLink) -> str:
    expected_number = link.expected_reference_number
    number_hint = ''
    if expected_number is not None and link.number_is_valid is False:
        number_hint = f'\nОжидаемый номер источника по порядку: {expected_number}.'

    return (
        'Ты исправляешь одну библиографическую запись в списке литературы научной работы.\n'
        'Исправь только проблему из замечания. Не меняй авторов, название, год, URL, DOI и страницы без необходимости.\n'
        'Не переводи текст источника на другой язык. Если запись на английском, оставь ее на английском; если на русском, оставь ее на русском.\n'
        'Не выдумывай отсутствующие фактические данные: дату обращения, количество страниц, том, выпуск, DOI, URL, издательство или место издания.\n'
        'Если в замечании требуется отсутствующая дата обращения, не подставляй случайную дату; добавь явный текст "[укажите дату обращения]".\n'
        'Если в замечании требуется отсутствующий объем книги, не подставляй пример или случайное число страниц; добавь "[укажите количество страниц]".\n'
        'Дату обращения оформляй только в формате "(дата обращения: ДД.ММ.ГГГГ)"; если точная дата неизвестна, используй "(дата обращения: [укажите дату обращения])".\n'
        'Сохрани язык и общий стиль записи. Не добавляй номер источника в начало записи: в DOCX он задается автонумерацией Word.\n'
        'Верни только JSON без Markdown.\n\n'
        f'Замечание нормоконтроля: {link.message}\n'
        f'Порядковый индекс записи с нуля: {link.reference_ordinal_index}\n'
        f'Текущий номер источника: {link.reference_number}{number_hint}\n\n'
        f'Исходная запись:\n{link.pdf_reference_text}\n\n'
        f'Текст соответствующей записи в DOCX:\n{link.docx_reference_text or link.pdf_reference_text}\n\n'
        'Формат ответа:\n'
        '{"new_text": "исправленная запись целиком", "reason": "краткое объяснение", "confidence": 0.0}'
    )


def build_rule_id(link: PdfIssueDocxLink) -> str:
    if link.reference_ordinal_index is not None:
        return f'gigachat-reference-{link.reference_ordinal_index}'
    if link.reference_number is not None:
        return f'gigachat-reference-number-{link.reference_number}'
    return 'gigachat-reference-unmatched'


def extract_response_content(response) -> str:
    choices = getattr(response, 'choices', None)
    if choices:
        message = getattr(choices[0], 'message', None)
        content = getattr(message, 'content', None)
        if content is not None:
            return str(content)
    return str(response)


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    match = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f'JSON object not found in response: {text[:200]}')
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError('GigaChat response JSON must be an object')
    return payload


def clamp_float(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def parse_bool_env(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def generate_reference_fixes(
    links: list[PdfIssueDocxLink],
    editor: ReferenceTextEditor,
    *,
    only_matched: bool = True,
) -> list[GeneratedReferenceFix]:
    fixes: list[GeneratedReferenceFix] = []
    for link in links:
        if only_matched and not link.docx_matched:
            continue
        fixes.append(editor.generate_fix(link))
    return fixes


def save_generated_reference_fixes(path_fixes: Path, fixes: list[GeneratedReferenceFix]) -> None:
    path_fixes.write_text(
        json.dumps([asdict(fix) for fix in fixes], ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def generated_fixes_to_replacement_rules(
    fixes: list[GeneratedReferenceFix],
    *,
    min_confidence: float = 0.5,
) -> list[TrackedReplacementRule]:
    rules: list[TrackedReplacementRule] = []
    for fix in fixes:
        if not fix.applied_to_generation:
            continue
        if fix.confidence < min_confidence:
            continue
        if not fix.old_text or not fix.new_text or fix.old_text == fix.new_text:
            continue
        new_text = strip_leading_reference_number(fix.new_text, fix.reference_number)
        if fix.old_text == new_text:
            continue
        rules.append(
            TrackedReplacementRule(
                old_text=fix.old_text,
                new_text=new_text,
                rule_id=fix.rule_id,
                comment=fix.reason,
                max_replacements=1,
                reference_number=fix.reference_number,
                query_text=fix.old_text,
                target_paragraph_indexes=fix.target_paragraph_indexes or None,
            )
        )
    return rules


def strip_leading_reference_number(text: str, reference_number: int | None) -> str:
    if reference_number is None:
        return text
    pattern = r'^\s*(?:\[\d{1,3}]|\d{1,3}\.)\s+'
    return re.sub(pattern, '', text, count=1)
