import difflib
import string

from src.structures import FoundedCaption


def to_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else None


def to_letter_index(value: str | None) -> int | None:
    if value is None:
        return None

    value = value.strip().upper()
    ru_letters = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЫЭЮЯ'
    if value in ru_letters:
        return ru_letters.index(value) + 1
    if value in string.ascii_uppercase:
        return string.ascii_uppercase.index(value) + 1
    return None


def is_latin_or_cyrillic_a(value: object) -> bool:
    return str(value) in {'A', 'А'}


def caption_words(caption: FoundedCaption) -> list[str]:
    return [word.strip() for word in caption.text.split() if word]


def table_caption_start_kind(caption_text: list[str], cutoff: float) -> str | None:
    if caption_text and difflib.get_close_matches(
        word='Таблица',
        possibilities=[caption_text[0]],
        n=1,
        cutoff=cutoff,
    ):
        return 'table'

    if difflib.get_close_matches(
        word='Продолжение таблицы',
        possibilities=[' '.join(caption_text[:2])],
        n=1,
        cutoff=cutoff,
    ):
        return 'table_continuation'

    return None
