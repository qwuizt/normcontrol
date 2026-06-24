from __future__ import annotations

import json
import os
import re
from pathlib import Path


def load_env_file(path_env: Path, *, override: bool = False) -> None:
    """
    Загрузить простой .env в os.environ без внешних зависимостей.

    Значения из окружения не перезаписываются, если ``override=False``.
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
        raise ValueError('LLM response JSON must be an object')
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
