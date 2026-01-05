#commamnds update
import json
import os
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import time

import requests

CHAT_TITLES_FILE = "chat_titles.json"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎥 КЭШИРОВАНИЕ ВИДЕО (для пересылки с direct URL)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_video_url_cache = {}  # {video_id: {"url": str, "timestamp": float}}
_VIDEO_CACHE_TTL = 3600  # 1 час


def _cache_video_url(video_id: str, url: str) -> None:
    """Кэширует ссылку на видео с меткой времени"""
    _video_url_cache[video_id] = {
        "url": url,
        "timestamp": time.time()
    }
    print(f"   💾 Ссылка на видео кэширована: {video_id[:16]}...")


def _get_cached_video_url(video_id: str) -> str | None:
    """Получает ссылку из кэша если она ещё свежая"""
    if video_id in _video_url_cache:
        cached = _video_url_cache[video_id]
        if time.time() - cached["timestamp"] < _VIDEO_CACHE_TTL:
            return cached["url"]
        else:
            del _video_url_cache[video_id]
    return None


def _get_authenticated_video_url(attach: Dict, max_token: str | None) -> str | None:
    """
    Получает прямую ссылку на видео из MAX с аутентификацией.
    
    Стратегия:
    1. Сначала ищем готовую HTTP(S) ссылку
    2. Если нужно, добавляем MAX_TOKEN как параметр
    3. Если ссылки нет, пробуем построить из baseUrl + id
    
    Args:
        attach: Словарь вложения из MAX
        max_token: Токен MAX для аутентификации
    
    Returns:
        Прямая ссылка на видео или None если не найдена
    """
    if not attach:
        return None
    
    # Логируем структуру видео для отладки
    print(f"   🔍 Анализирую структуру видео...")
    attach_keys = list(attach.keys())
    print(f"       Ключи: {attach_keys[:5]}{'...' if len(attach_keys) > 5 else ''}")
    
    # Сначала пробуем найти готовую ссылку
    direct_url = _find_first_url(attach)
    if direct_url and isinstance(direct_url, str) and direct_url.startswith(("http://", "https://")):
        print(f"       ✅ Найдена готовая ссылка: {direct_url[:50]}...")
        # Проверяем, может быть нужен токен
        if "token=" not in direct_url.lower() and max_token:
            # Добавляем токен если его нет
            separator = "&" if "?" in direct_url else "?"
            auth_url = f"{direct_url}{separator}token={max_token}"
            print(f"       🔐 Добавлен MAX_TOKEN к URL")
            return auth_url
        return direct_url
    
    # Если нет готовой ссылки, пробуем построить из компонентов
    print(f"       📦 Пробую построить URL из компонентов...")
    
    # Проверяем структуру file/preview
    file_data = attach.get("file") or attach.get("preview") or attach.get("data")
    if isinstance(file_data, dict):
        file_keys = list(file_data.keys())
        print(f"           file/preview ключи: {file_keys[:5]}")
        
        base_url = file_data.get("baseUrl") or file_data.get("base_url") or file_data.get("url")
        file_id = file_data.get("id") or attach.get("id") or attach.get("fileId")
        
        if base_url:
            print(f"           📍 Найден baseUrl: {base_url[:50]}")
        if file_id:
            print(f"           🏷️  Найден id/fileId: {file_id}")
        
        if base_url and file_id:
            url = f"{base_url}/{file_id}"
            if not url.startswith(("http://", "https://")):
                # Попробуем добавить протокол
                url = f"https://{url}"
            if max_token and "token=" not in url:
                url = f"{url}?token={max_token}"
            print(f"       🔨 Построена ссылка: {url[:50]}...")
            return url
    
    # Последний вариант - проверяем есть ли простой id поле
    if "id" in attach:
        file_id = attach["id"]
        # Проверим, может это уже полная ссылка?
        if isinstance(file_id, str) and file_id.startswith(("http://", "https://")):
            print(f"       ✅ Поле id содержит готовую ссылку")
            if max_token and "token=" not in file_id:
                return f"{file_id}?token={max_token}"
            return file_id
    
    print(f"       ❌ Не удалось получить прямую ссылку на видео")
    return None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _load_monitored_chats() -> List[Dict]:
    """Загружает список мониторимых чатов из кэша chat_titles.json"""
    try:
        if os.path.exists(CHAT_TITLES_FILE):
            with open(CHAT_TITLES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [
                    {"id": chat_id, "name": title}
                    for chat_id, title in data.items()
                ]
    except Exception:
        pass
    return []


def handle_attach(attach: Dict) -> str:
    attach_type = attach.get("_type") or attach.get("type") or "UNKNOWN"
    name = attach.get("name") or attach.get("fileName")
    if name:
        return f"{attach_type}: {name}"
    return str(attach_type)


def _find_first_url(value) -> Optional[str]:
    """
    Walk over dict/lists to find the first string that looks like a URL.
    Helps when MAX кладёт ссылку глубоко в `file`/`preview`.
    """
    if isinstance(value, str):
        if value.startswith(("http://", "https://", "file://")):
            return value
        return None
    if isinstance(value, dict):
        # Первый приоритет - известные поля с URL
        for k in [
            "baseUrl",
            "base_url",
            "url",
            "link",
            "fileUrl",
            "downloadUrl",
            "contentUrl",
            "originUrl",
            "rawUrl",
            "baseRawUrl",
            "cdnUrl",
            "previewUrl",
            "sourceUrl",
            "downloadLink",
            "viewUrl",
        ]:
            if k in value and isinstance(value[k], str) and value[k].startswith(("http://", "https://")):
                return value[k]
        
        # Второй приоритет - рекурсивный поиск в известных блоках
        for block_key in ["file", "preview", "image", "data"]:
            if block_key in value and isinstance(value[block_key], dict):
                found = _find_first_url(value[block_key])
                if found:
                    return found
        
        # Третий приоритет - рекурсивный поиск в остальных значениях
        for v in value.values():
            if isinstance(v, (dict, list)):
                found = _find_first_url(v)
                if found:
                    return found
    if isinstance(value, list):
        for v in value:
            found = _find_first_url(v)
            if found:
                return found
    return None


def _get_media_url(attach: Dict) -> str | None:
    """Try to extract a downloadable URL from different attachment shapes."""
    direct = _find_first_url(attach)
    if direct and isinstance(direct, str) and direct.startswith(("http://", "https://")):
        return direct
    file_block = attach.get("file") or attach.get("preview") or attach.get("image")
    if isinstance(file_block, dict):
        return _find_first_url(file_block)
    return None


def _guess_attach_kind(attach: Dict) -> str:
    """
    Return category: photo, video, audio, voice, document, sticker, unknown.
    Uses type + mime/contentType + filename.
    """
    attach_type = str(attach.get("_type") or attach.get("type") or "").upper()
    mime = str(attach.get("mimeType") or attach.get("contentType") or "").lower()
    name = (attach.get("name") or attach.get("fileName") or "").lower()

    if attach_type in ("PHOTO", "IMAGE"):
        return "photo"
    if attach_type == "VIDEO":
        return "video"
    if attach_type == "AUDIO":
        return "audio"
    if attach_type == "VOICE":
        return "voice"
    if attach_type == "STICKER":
        return "sticker"

    # Infer from mime
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"

    # Infer from extension
    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}:
        return "photo"
    if suffix in {".mp4", ".mov", ".mkv", ".avi"}:
        return "video"
    if suffix in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}:
        return "audio"

    if attach_type in ("FILE", "DOCUMENT") or name or mime:
        return "document"

    return "unknown"


def _add_thread(payload: Dict, TG_THREAD_ID: int | None) -> Dict:
    if TG_THREAD_ID:
        payload["message_thread_id"] = TG_THREAD_ID
    return payload


def _send_text(TG_BOT_TOKEN: str, TG_CHAT_ID: int, text: str, TG_THREAD_ID: int | None):
    if not text:
        return
    api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = _add_thread(
        {
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        },
        TG_THREAD_ID,
    )
    resp = requests.post(api_url, data=payload)
    print(resp.json())


def _send_media_group(
    TG_BOT_TOKEN: str,
    TG_CHAT_ID: int,
    media: List[Dict],
    TG_THREAD_ID: int | None,
):
    api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMediaGroup"
    payload = _add_thread({"chat_id": TG_CHAT_ID, "media": json.dumps(media)}, TG_THREAD_ID)
    resp = requests.post(api_url, data=payload)
    print(resp.json())


def send_telegram_message(bot_token: str, chat_id: str, text: str, thread_id: int | None = None):
    """Отправляет сообщение в Telegram"""
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    requests.post(api_url, data=payload)


def handle_telegram_commands(
    bot_token: str,
    chat_id: str,
    message_text: str,
    thread_id: int | None = None,
    forward_enabled: bool = True,
    fallback_chat_ids: List[int] | None = None,
) -> bool:
    """Обработчик команд Telegram бота. Возвращает True если команда обработана."""
    message_text = message_text.strip()

    if message_text == "/status":
        status_text = (
            "<b>✅ Статус работы:</b>\n\n"
            "🤖 Бот включен\n"
            f"⏸️ Пересылка: {'🟢 включена' if forward_enabled else '🔴 ВЫКЛЮЧЕНА'}"
        )
        send_telegram_message(bot_token, chat_id, status_text, thread_id)
        return True

    elif message_text == "/chats":
        monitored_chats = _load_monitored_chats()
        if not monitored_chats:
            chats_text = "<b>📋 Отслеживаемые чаты:</b>\n\nНет активных чатов"
        else:
            chats_list = "\n".join(
                f"• <b>{chat.get('name', 'Без имени')}</b>\n   ID: <code>{chat.get('id', 'N/A')}</code>"
                for chat in monitored_chats
            )
            chats_text = f"<b>📋 Отслеживаемые чаты ({len(monitored_chats)}):</b>\n\n{chats_list}"

        send_telegram_message(bot_token, chat_id, chats_text, thread_id)
        return True

    return False


def send_to_telegram(
    TG_BOT_TOKEN: str = "",
    TG_CHAT_ID: int = 0,
    caption: str = "",
    attachments: List[Dict] | None = None,
    TG_THREAD_ID: int | None = None,  # ← поддержка темы
    max_token: str | None = None,
    sender_id: int | None = None,
):
    attachments = attachments or []

    # ------------------------
    # 1) ОТПРАВКА ТЕКСТА
    # ------------------------
    if not attachments:
        _send_text(TG_BOT_TOKEN, TG_CHAT_ID, caption, TG_THREAD_ID)
        return

    # ------------------------
    # 2) КЛАССИФИКАЦИЯ ВЛОЖЕНИЙ
    # ------------------------
    categorized = {
        "photos": [],
        "videos": [],
        "audios": [],
        "voices": [],
        "documents": [],
        "stickers": [],
        "unknown": [],
    }

    for attach in attachments:
        attach_type = str(attach.get("_type") or attach.get("type") or "UNKNOWN").upper()
        if attach_type == "CONTROL":
            # service message already обработан на стороне MAX → текстом
            continue

        kind = _guess_attach_kind(attach)
        url = _get_media_url(attach)
        
        # ✨ НОВОЕ: Для видео пробуем получить authenticated URL из MAX если обычный не найден
        if kind == "video" and (not url or not str(url).startswith(("http://", "https://"))):
            url = _get_authenticated_video_url(attach, max_token)
            if url:
                print(f"   🔓 Видео: получена authenticated ссылка из MAX")

        if not url or not str(url).startswith(("http://", "https://")):
            categorized["unknown"].append(attach)
            print(f"   ⚠️ Видео без ссылки: {attach_type}")
            continue

        if kind == "photo":
            categorized["photos"].append({"url": url, "raw": attach})
        elif kind == "video":
            categorized["videos"].append({"url": url, "raw": attach})
        elif kind == "audio":
            categorized["audios"].append({"url": url, "raw": attach})
        elif kind == "voice":
            categorized["voices"].append({"url": url, "raw": attach})
        elif kind == "sticker":
            print(f"   📌 Классифицировано как стикер: {kind}")
            categorized["stickers"].append({"url": url, "raw": attach})
        elif kind == "document":
            categorized["documents"].append({"url": url, "raw": attach})
        else:
            categorized["unknown"].append(attach)

    caption_sent = False
    caption_left = caption

    # ------------------------
    # 3) ФОТО (альбомами по 10)
    # ------------------------
    photos = categorized["photos"]
    for i in range(0, len(photos), 10):
        media: List[Dict] = []
        chunk = photos[i : i + 10]
        for idx, item in enumerate(chunk):
            m = {"type": "photo", "media": item["url"]}
            if not caption_sent and caption_left and idx == 0:
                m["caption"] = caption_left
                m["parse_mode"] = "HTML"
                caption_sent = True
                caption_left = ""
            media.append(m)
        if media:
            _send_media_group(TG_BOT_TOKEN, TG_CHAT_ID, media, TG_THREAD_ID)

    # ------------------------
    # 4) ВИДЕО / АУДИО / ГОЛОС / ДОКУМЕНТЫ
    # ------------------------
    def _send_single(endpoint: str, field: str, items: List[Dict], supports_caption: bool = True):
        nonlocal caption_sent, caption_left
        for idx, item in enumerate(items):
            payload = _add_thread({"chat_id": TG_CHAT_ID}, TG_THREAD_ID)
            
            # ← НОВОЕ: Проверяем и оптимизируем URL для видео
            media_url = item.get("url")
            if field == "video" and media_url:
                video_id = item["raw"].get("id") or hashlib.md5(media_url.encode()).hexdigest()
                
                # Пробуем кэш
                cached_url = _get_cached_video_url(video_id)
                if cached_url:
                    print(f"   ♻️ Видео из кэша: {video_id}")
                    media_url = cached_url
                else:
                    # Получаем authenticated URL если нужно
                    auth_url = _get_authenticated_video_url(item["raw"], max_token)
                    if auth_url:
                        print(f"   🔐 Используем authenticated URL для видео")
                        media_url = auth_url
                        _cache_video_url(video_id, auth_url)
            
            payload[field] = media_url

            if supports_caption and not caption_sent and caption_left:
                payload["caption"] = caption_left
                payload["parse_mode"] = "HTML"
                caption_sent = True
                caption_left = ""
            
            resp = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/{endpoint}",
                data=payload,
            )
            result = resp.json()
            if not result.get("ok"):
                print(f"   ❌ Ошибка Telegram: {result.get('description', 'Unknown error')}")
            else:
                print(f"   ✅ Видео успешно отправлено")

    def _send_sticker_from_url(sticker_data: Dict):
        """
        Отправляет стикер из URL в Telegram.
        Поддерживает загрузку файла и отправку через sendSticker API.
        """
        nonlocal caption_sent, caption_left
        try:
            url = sticker_data.get("url")
            if not url:
                print(f"⚠️ Нет URL для стикера: {sticker_data}")
                return False
            
            print(f"📥 Загружаю стикер: {url}")
            
            # Загружаем файл с поддержкой редиректов
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            img_response = requests.get(url, timeout=10, headers=headers, allow_redirects=True)
            img_response.raise_for_status()
            
            # Проверяем Content-Type
            content_type = img_response.headers.get('Content-Type', '')
            content_len = len(img_response.content)
            print(f"📊 Content-Type: {content_type}, Size: {content_len} bytes")
            
            if not img_response.content or content_len == 0:
                print(f"⚠️ Пустой файл стикера")
                return False
            
            # Отправляем как стикер через API
            api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendSticker"
            
            # Определяем расширение файла
            if 'webp' in content_type.lower():
                filename = "sticker.webp"
                mime_type = "image/webp"
            elif 'png' in content_type.lower():
                filename = "sticker.png"
                mime_type = "image/png"
            else:
                # По умолчанию пробуем PNG
                filename = "sticker.png"
                mime_type = "image/png"
            
            files = {"sticker": (filename, img_response.content, mime_type)}
            payload = _add_thread({"chat_id": TG_CHAT_ID}, TG_THREAD_ID)
            
            print(f"📤 Отправляю стикер в Telegram...")
            resp = requests.post(api_url, data=payload, files=files)
            result = resp.json()
            
            if result.get("ok"):
                print(f"✅ Стикер успешно отправлен!")
            else:
                print(f"❌ Ошибка Telegram: {result}")
            
            return result.get("ok", False)
        except Exception as e:
            print(f"❌ Ошибка при отправке стикера: {type(e).__name__}: {e}")
            return False

    _send_single("sendVideo", "video", categorized["videos"])
    _send_single("sendAudio", "audio", categorized["audios"])
    _send_single("sendVoice", "voice", categorized["voices"])
    _send_single("sendDocument", "document", categorized["documents"])

    # ------------------------
    # 5) СТИКЕРЫ (загружаем и отправляем как стикер Telegram)
    # ------------------------
    if categorized["stickers"]:
        print(f"🎨 Стикеров для отправки: {len(categorized['stickers'])}")
        for idx, sticker_item in enumerate(categorized["stickers"], 1):
            print(f"   [{idx}/{len(categorized['stickers'])}] Стикер: {sticker_item.get('url')}")
        
        if caption_left and not caption_sent:
            _send_text(TG_BOT_TOKEN, TG_CHAT_ID, caption_left, TG_THREAD_ID)
            caption_sent = True
            caption_left = ""
        for sticker_item in categorized["stickers"]:
            _send_sticker_from_url(sticker_item)

    # ------------------------
    # 6) НЕИЗВЕСТНЫЕ ПРИЛОЖЕНИЯ
    # ------------------------
    if categorized["unknown"]:
        suffix_lines = [
            "Не могу отправить вложение без прямой ссылки: "
            + ", ".join(handle_attach(a) for a in categorized["unknown"])
        ]
        extra_text = caption_left
        if extra_text:
            extra_text += "\n\n"
        extra_text += "\n".join(suffix_lines)
        _send_text(TG_BOT_TOKEN, TG_CHAT_ID, extra_text, TG_THREAD_ID)
        caption_sent = True
        caption_left = ""

    # ------------------------
    # 7) ЕСЛИ ПОДПИСЬ ЕЩЕ НЕ УШЛА
    # ------------------------
    if caption_left and not caption_sent:
        _send_text(TG_BOT_TOKEN, TG_CHAT_ID, caption_left, TG_THREAD_ID)
