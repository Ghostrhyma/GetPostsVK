"""Минимальный клиент VK API (api.vk.ru)."""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger(__name__)

# 6 - слишком много запросов в секунду, 9 - flood control, 10 - внутренняя ошибка VK
_RETRY_CODES = {6, 9, 10}


class VkException(Exception):
    """Базовая ошибка работы с VK."""


class VkError(VkException):
    """VK ответил ошибкой API."""

    # 15 - доступ запрещён (закрытая стена/группа), 18 - страница удалена или
    # заблокирована, 30 - закрытый профиль, 100/113 - неверное имя/идентификатор
    INACCESSIBLE_CODES = {15, 18, 30, 100, 113}

    def __init__(self, code: int, message: str):
        super().__init__(f"VK API error {code}: {message}")
        self.code = code
        self.message = message

    @property
    def inaccessible(self) -> bool:
        return self.code in self.INACCESSIBLE_CODES


class VkUnavailable(VkException):
    """VK недоступен: сеть, таймаут, не-JSON в ответе."""


class VkClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        token: str,
        version: str,
        host: str = "api.vk.ru",
        request_delay: float = 0.4,
        max_attempts: int = 3,
    ):
        self._session = session
        self._token = token
        self._version = version
        self._base_url = f"https://{host}/method"
        self._delay = request_delay
        self._max_attempts = max_attempts
        self._pace_lock = asyncio.Lock()
        self._last_call = 0.0

    async def _pace(self) -> None:
        """Не чаще одного запроса за request_delay секунд (лимит VK - 3 в секунду)."""
        async with self._pace_lock:
            wait = self._delay - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def call(self, method: str, **params):
        """Вызывает метод API и возвращает поле ``response``."""
        payload = {**params, "access_token": self._token, "v": self._version}
        url = f"{self._base_url}/{method}"

        for attempt in range(1, self._max_attempts + 1):
            await self._pace()
            try:
                # POST, чтобы токен не попадал в URL и логи
                async with self._session.post(url, data=payload) as response:
                    data = await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                if attempt < self._max_attempts:
                    log.warning("VK %s: сетевая ошибка (%s), попытка %d", method, exc, attempt)
                    await asyncio.sleep(attempt * 2)
                    continue
                raise VkUnavailable(f"{method}: {exc}") from exc

            if not isinstance(data, dict):
                raise VkUnavailable(f"{method}: неожиданный ответ VK")

            error = data.get("error")
            if error:
                code = int(error.get("error_code") or 0)
                message = str(error.get("error_msg") or "")
                if code in _RETRY_CODES and attempt < self._max_attempts:
                    log.warning("VK %s: ошибка %d, повтор через %d с", method, code, attempt)
                    await asyncio.sleep(attempt)
                    continue
                raise VkError(code, message)

            return data.get("response")

        raise VkUnavailable(f"{method}: попытки исчерпаны")

    async def get_wall(self, domain: str, count: int) -> list[dict]:
        """Последние записи со стены (закреплённая запись тоже входит в выдачу)."""
        response = await self.call("wall.get", domain=domain, count=count)
        items = response.get("items") if isinstance(response, dict) else None
        return items or []

    async def get_name(self, domain: str) -> str | None:
        """Название сообщества или имя пользователя; None, если определить не удалось."""
        try:
            response = await self.call("groups.getById", group_id=domain)
            groups = response.get("groups") if isinstance(response, dict) else response
            if groups and groups[0].get("name"):
                return groups[0]["name"]
        except VkError:
            pass

        try:
            response = await self.call("users.get", user_ids=domain)
            if response:
                user = response[0]
                name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                if name:
                    return name
        except VkError:
            pass

        return None
