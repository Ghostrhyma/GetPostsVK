"""Конфигурация из переменных окружения (только стандартная библиотека)."""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode


class ConfigError(Exception):
    """Конфигурация неполная или некорректная."""


# libpq-значения sslmode -> наши режимы
#   off     - без шифрования
#   require - шифрование без проверки сертификата
#   verify  - шифрование с проверкой сертификата и имени хоста
_SSLMODE_TO_DB_SSL = {
    "disable": "off",
    "allow": "off",
    "prefer": "off",
    "require": "require",
    "verify-ca": "verify",
    "verify-full": "verify",
}
_VALID_DB_SSL = {"off", "require", "verify"}

# Параметры строки подключения, которые понимает libpq, но не понимает asyncpg
_DROPPED_QUERY_PARAMS = {"channel_binding"}


def normalize_database_url(raw: str) -> tuple[str, str | None]:
    """Приводит DATABASE_URL к виду для SQLAlchemy + asyncpg.

    Возвращает (url, режим_ssl_из_url | None). Параметр ``sslmode`` из строки
    убирается (asyncpg его не принимает) и превращается в режим SSL.
    """
    base, _, query = raw.partition("?")
    base = re.sub(r"^postgres(?:ql)?://", "postgresql+asyncpg://", base)

    ssl_mode: str | None = None
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "sslmode":
            ssl_mode = _SSLMODE_TO_DB_SSL.get(value, "off")
        elif key in _DROPPED_QUERY_PARAMS:
            continue
        else:
            kept.append((key, value))

    url = base + (f"?{urlencode(kept)}" if kept else "")
    return url, ssl_mode


@dataclass(frozen=True)
class Settings:
    bot_token: str
    vk_token: str
    database_url: str
    db_ssl: str = "off"
    vk_api_version: str = "5.199"
    vk_api_host: str = "api.vk.ru"
    admin_id: int | None = None
    poll_interval: float = 60.0
    vk_request_delay: float = 0.4
    posts_per_check: int = 10
    heartbeat_file: str = "/tmp/bot-heartbeat"
    log_level: str = "INFO"
    telegram_proxy: str | None = None


_PROXY_SCHEMES = ("socks5://", "socks4://", "http://", "https://")


def _first(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _number(env, names, default, cast, minimum, maximum=None):
    raw = _first(env, *names)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{names[0]} должно быть числом, получено: {raw!r}") from None
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"не меньше {minimum}" if maximum is None else f"от {minimum} до {maximum}"
        raise ConfigError(f"{names[0]} должно быть {bounds}, получено: {raw}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    bot_token = _first(env, "BOT_TOKEN")
    vk_token = _first(env, "VK_TOKEN", "TOKEN")  # TOKEN - старое имя
    raw_db_url = _first(env, "DATABASE_URL")

    missing = [
        name
        for name, value in (
            ("BOT_TOKEN", bot_token),
            ("VK_TOKEN", vk_token),
            ("DATABASE_URL", raw_db_url),
        )
        if not value
    ]
    if missing:
        raise ConfigError("Не заданы переменные окружения: " + ", ".join(missing))

    database_url, ssl_from_url = normalize_database_url(raw_db_url)
    db_ssl = (_first(env, "DB_SSL") or ssl_from_url or "off").lower()
    if db_ssl not in _VALID_DB_SSL:
        raise ConfigError(f"DB_SSL должно быть одним из {sorted(_VALID_DB_SSL)}, получено: {db_ssl!r}")

    admin_raw = _first(env, "ADMIN_ID", "GOD_ID")  # GOD_ID - старое имя
    try:
        admin_id = int(admin_raw) if admin_raw else None
    except ValueError:
        raise ConfigError(f"ADMIN_ID должно быть числом, получено: {admin_raw!r}") from None

    proxy = _first(env, "PROXY_URL", "TELEGRAM_PROXY")
    if proxy and not proxy.lower().startswith(_PROXY_SCHEMES):
        raise ConfigError(
            "PROXY_URL должен начинаться с socks5://, socks4://, http:// или https:// "
            "(например, socks5://user:pass@host:1080)"
        )

    api_host = _first(env, "VK_API_HOST") or "api.vk.ru"
    api_host = re.sub(r"^https?://", "", api_host).rstrip("/")

    return Settings(
        bot_token=bot_token,
        vk_token=vk_token,
        database_url=database_url,
        db_ssl=db_ssl,
        vk_api_version=_first(env, "VK_API_VERSION", "VERSION") or "5.199",
        vk_api_host=api_host,
        admin_id=admin_id,
        poll_interval=_number(env, ("POLL_INTERVAL",), 60.0, float, 10.0),
        vk_request_delay=_number(env, ("VK_REQUEST_DELAY",), 0.4, float, 0.0),
        posts_per_check=_number(env, ("POSTS_PER_CHECK",), 10, int, 1, 100),
        heartbeat_file=_first(env, "HEARTBEAT_FILE") or "/tmp/bot-heartbeat",
        log_level=(_first(env, "LOG_LEVEL") or "INFO").upper(),
        telegram_proxy=proxy,
    )
