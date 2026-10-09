"""Разбор постов VK и выбор того, что нужно отправить (только стандартная библиотека)."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

VK_WEB_HOST = "vk.ru"

# Telegram скачивает фото по URL сам (лимит ~5 МБ), поэтому берём не самый
# большой размер, а самый большой из разумных.
PHOTO_MAX_SIDE = 1280
VIDEO_PREVIEW_MAX_SIDE = 800


# --------------------------------------------------------------------------- ссылки

# vk.ru - основной домен; vk.com и m./www. принимаем на вводе для удобства
_LINK_RE = re.compile(
    r"(?<![\w.@/-])(?:https?://)?(?:m\.|www\.)?vk\.(?:ru|com)/([A-Za-z0-9_.]+)(?![\w.\-@])",
    re.IGNORECASE,
)
_RESERVED = {
    "feed", "im", "video", "audio", "friends", "groups", "albums", "wall",
    "away.php", "login", "share.php", "al_feed.php", "app", "apps",
}


def extract_domain(text: str | None) -> str | None:
    """Достаёт короткое имя страницы из ссылки вида https://vk.ru/имя."""
    if not text:
        return None
    match = _LINK_RE.search(text)
    if not match:
        return None
    domain = match.group(1).strip(".").lower()
    if not domain or domain in _RESERVED:
        return None
    return domain


def video_url(owner_id: int, video_id: int, access_key: str | None = None) -> str:
    url = f"https://{VK_WEB_HOST}/video{owner_id}_{video_id}"
    return f"{url}?access_key={access_key}" if access_key else url


# --------------------------------------------------------------------------- посты


@dataclass(frozen=True)
class Media:
    kind: str  # "photo" | "doc" | "video"
    url: str
    title: str | None = None
    preview: str | None = None


@dataclass(frozen=True)
class VkPost:
    owner_id: int
    post_id: int
    date: int
    text: str
    attachments: tuple[Media, ...] = ()

    @property
    def key(self) -> str:
        """Ключ поста в БД. Формат совпадает со старой версией бота: ``owner-id``."""
        return f"{self.owner_id}-{self.post_id}"


def _pick_image(images, max_side: int) -> str | None:
    """Самая большая картинка, не превышающая max_side; иначе самая маленькая."""
    candidates = [i for i in images or [] if isinstance(i, dict) and i.get("url")]
    if not candidates:
        return None

    def side(i):
        return max(int(i.get("width") or 0), int(i.get("height") or 0))

    def area(i):
        return int(i.get("width") or 0) * int(i.get("height") or 0)

    fitting = [i for i in candidates if side(i) <= max_side]
    chosen = max(fitting, key=area) if fitting else min(candidates, key=area)
    return chosen["url"]


def _parse_attachment(raw) -> Media | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    data = raw.get(kind) if kind else None
    if not isinstance(data, dict):
        return None

    if kind == "photo":
        url = _pick_image(data.get("sizes"), PHOTO_MAX_SIDE)
        return Media("photo", url) if url else None

    if kind == "doc":
        url = data.get("url")
        return Media("doc", url, title=data.get("title") or "Файл") if url else None

    if kind == "video":
        owner_id, video_id = data.get("owner_id"), data.get("id")
        if owner_id is None or video_id is None:
            return None
        return Media(
            "video",
            video_url(owner_id, video_id, data.get("access_key")),
            title=data.get("title"),
            preview=_pick_image(data.get("image"), VIDEO_PREVIEW_MAX_SIDE),
        )

    return None


def parse_post(item) -> VkPost | None:
    """Превращает элемент ответа wall.get в VkPost.

    У репоста берётся и комментарий репостящего, и оригинал (текст и вложения).
    """
    if not isinstance(item, dict):
        return None
    try:
        owner_id = int(item["owner_id"])
        post_id = int(item["id"])
        date = int(item.get("date") or 0)
    except (KeyError, TypeError, ValueError):
        return None

    sources = [item]
    history = item.get("copy_history")
    if isinstance(history, list) and history and isinstance(history[0], dict):
        sources.append(history[0])

    texts = [(s.get("text") or "").strip() for s in sources]
    text = "\n\n".join(t for t in texts if t)

    attachments: list[Media] = []
    for source in sources:
        for raw in source.get("attachments") or []:
            media = _parse_attachment(raw)
            if media:
                attachments.append(media)

    return VkPost(owner_id, post_id, date, text, tuple(attachments))


def split_new_posts(
    posts: Sequence[VkPost], recent_keys: Sequence[str]
) -> tuple[list[VkPost], list[VkPost]]:
    """Решает, какие посты новые и какие из них отправлять.

    ``recent_keys`` - ключи последних постов этой группы, уже лежащие в БД.

    Возвращает ``(new, to_send)``:
      * ``new``     - всё, чего нет в БД (это нужно запомнить);
      * ``to_send`` - то, что реально уйдёт в чаты, по возрастанию даты.

    Правила:
      * если по владельцу стены в БД ещё ничего нет (группу только подключили),
        отправляется только самый свежий пост, а остальные просто запоминаются;
      * иначе отправляются только посты с id больше максимального известного.
        Благодаря этому после перехода со старой версии бота (которая помнила
        только последний пост) старые записи не вываливаются в чаты разом.
    """
    known = set(recent_keys)
    ordered = sorted(posts, key=lambda p: (p.date, p.post_id))
    new = [p for p in ordered if p.key not in known]

    baselines: dict[int, int] = {}
    for key in recent_keys:
        owner, sep, post_id = key.rpartition("-")
        if not sep:
            continue
        try:
            owner_i, post_i = int(owner), int(post_id)
        except ValueError:
            continue
        baselines[owner_i] = max(baselines.get(owner_i, post_i), post_i)

    to_send: list[VkPost] = []
    unbased: list[VkPost] = []
    for post in new:
        baseline = baselines.get(post.owner_id)
        if baseline is None:
            unbased.append(post)
        elif post.post_id > baseline:
            to_send.append(post)
    if unbased:
        to_send.append(unbased[-1])

    to_send.sort(key=lambda p: (p.date, p.post_id))
    return new, to_send


# --------------------------------------------------------------------------- текст


def split_text(text: str, limit: int = 4096) -> list[str]:
    """Режет длинный текст на куски не длиннее limit по абзацам/строкам/словам."""
    rest = text.strip()
    if len(rest) <= limit:
        return [rest]

    chunks: list[str] = []
    while len(rest) > limit:
        cut = limit
        for separator in ("\n\n", "\n", " "):
            found = rest.rfind(separator, 0, limit)
            if found >= limit // 2:
                cut = found
                break
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def chunk_album(items: Sequence, size: int = 10) -> list[list]:
    """Делит вложения на альбомы по ≤size; в альбоме Telegram должно быть минимум 2."""
    chunks = [list(items[i : i + size]) for i in range(0, len(items), size)]
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        chunks[-1].insert(0, chunks[-2].pop())
    return chunks
