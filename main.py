#newest version
import os
import time
import json
from threading import Lock
from html import escape
from typing import Dict, Iterable, List, Set

from dotenv import load_dotenv

from classes import Message
from filters import filters
from max import MaxClient as Client
from telegram import send_to_telegram, handle_telegram_commands

load_dotenv()

MAX_TOKEN = os.getenv("MAX_TOKEN")
MAX_CHAT_IDS_STR = os.getenv("MAX_CHAT_IDS", "")
MAX_CHAT_IDS = [int(x) for x in MAX_CHAT_IDS_STR.split(",")] if MAX_CHAT_IDS_STR else []

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# NEW — поддержка темы
TG_THREAD_ID = os.getenv("TG_THREAD_ID")
TG_THREAD_ID = int(TG_THREAD_ID) if TG_THREAD_ID and TG_THREAD_ID.isdigit() else None

# Проверка конфигурации
config_errors = []
if not MAX_TOKEN:
    config_errors.append("MAX_TOKEN не найден в .env")
if not MAX_CHAT_IDS:
    config_errors.append("MAX_CHAT_IDS пусты или некорректны в .env")
if not TG_BOT_TOKEN:
    config_errors.append("TG_BOT_TOKEN не найден в .env")
if not TG_CHAT_ID:
    config_errors.append("TG_CHAT_ID не найден в .env")

if config_errors:
    print("❌ Ошибки конфигурации:")
    for err in config_errors:
        print(f"   - {err}")
    print("\nПроверьте файл .env и убедитесь, что все переменные установлены.")
    exit(1)

print("✅ Конфигурация загружена успешно")
print(f"   MAX_TOKEN: {MAX_TOKEN[:20]}...") 
print(f"   MAX_CHAT_IDS: {MAX_CHAT_IDS}")
print(f"   TG_BOT_TOKEN: {TG_BOT_TOKEN[:20]}...")
print(f"   TG_CHAT_ID: {TG_CHAT_ID}")

MONITOR_ID = os.getenv("MONITOR_ID")
client = Client(MAX_TOKEN)
FORWARD_STATE_FILE = "forward_state.json"
CHAT_TITLES_FILE = "chat_titles.json"


# ===== ОПТИМИЗАЦИЯ: Простой кэш ========
_user_name_cache = {}  # {user_id: name}
_user_name_cache_lock = Lock()
_chat_titles_cache = {}  # {chat_id: title} — загружается при старте
_chat_titles_pending = {}  # {chat_id: title} — новые значения для сохранения
_chat_titles_lock = Lock()
_processed_message_ids = set()  # Кэш ID сообщений для дедупликации (последние 1000)
_processed_messages_lock = Lock()


def _safe_escape(text: str | None) -> str:
    """Escape text for HTML parse mode."""
    return escape(text, quote=False) if text else ""


def _get_contact_name(user) -> str:
    if not user or not getattr(user, "contact", None):
        return "Неизвестно"
    names = getattr(user.contact, "names", [])
    return names[0].name if names else "Неизвестно"


def _get_user_name_by_id(client: Client, user_id: int | None) -> str:
    """Get user name with caching to minimize API calls."""
    if not user_id:
        return "Неизвестно"
    
    # Быстрая проверка кэша БЕЗ блокировки (для скорости)
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    
    # API запрос только если нет в кэше
    try:
        user = client.get_user(id=user_id, _f=1)
        result = _get_contact_name(user)
        
        # Сохраняем в кэш с блокировкой
        with _user_name_cache_lock:
            _user_name_cache[user_id] = result
            # Ограничиваем размер кэша
            if len(_user_name_cache) > 1000:
                _user_name_cache.pop(next(iter(_user_name_cache)))
        
        return result
    except Exception:
        return "Неизвестно"


def _is_message_duplicate(message_id: str) -> bool:
    """Проверяет, не был ли этот ID сообщения уже обработан (для дедупликации)."""
    with _processed_messages_lock:
        if message_id in _processed_message_ids:
            return True
        
        _processed_message_ids.add(message_id)
        
        # Ограничиваем размер кэша последних 1000 сообщений
        if len(_processed_message_ids) > 1000:
            _processed_message_ids.pop()
        
        return False


def detect_message_types(
    text: str,
    attachments: Iterable[Dict],
    link_type: str | None,
    message_type: str | None,
) -> Set[str]:
    """Collect a set of message types we see in MAX."""
    detected: Set[str] = set()
    if text:
        detected.add("TEXT")
    if link_type:
        detected.add(link_type.upper())
    if message_type:
        detected.add(message_type.upper())

    for attach in attachments or []:
        attach_type = (
            attach.get("_type")
            or attach.get("type")
            or attach.get("kind")
            or "UNKNOWN"
        )
        detected.add(str(attach_type).upper())
        print(f"   └─ Обнаружено вложение: {attach_type}")

    return detected


def describe_control_attach(attach: Dict, resolve_user_name) -> str:
    """Render control/system attachment into readable text."""
    # Prefer server-provided human text if есть
    if attach.get("shortMessage"):
        return f"ℹ️ Системное сообщение: {attach['shortMessage']}"

    raw_event = attach.get("event")
    
    # === 🔴 НОВОЕ: Обработка ЗВОНКОВ ===
    if attach.get("callType"):
        call_type = attach.get("callType", "").upper()
        
        # Определяем эмодзи в зависимости от типа звонка
        if call_type == "VIDEO":
            call_icon = "📹"
        elif call_type == "VOICE":
            call_icon = "☎️"
        else:
            call_icon = "📞"
        
        # Получаем имя звонящего
        user_id = attach.get("initiatorId") or attach.get("userId")
        user_name = resolve_user_name(user_id) if user_id else "Неизвестно"
        
        # ТВОЙ ФОРМАТ:
        return f"{call_icon} {user_name} начал звонок"
    # === 🔴 КОНЕЦ ===
    
    # === Обработка специальных событий с userId ===
    # Попытаемся найти userId несколькими способами
    user_id = None
    user_name = None
    
    # ВАЖНО: для "add" приходит userIds (массив), для "remove" - userId (число)
    if attach.get("userId"):
        user_id = attach.get("userId")
    elif attach.get("userIds") and isinstance(attach.get("userIds"), list) and attach["userIds"]:
        user_id = attach["userIds"][0]  # Берём первого пользователя из массива
    elif attach.get("memberId"):
        user_id = attach.get("memberId")
    elif attach.get("contactId"):
        user_id = attach.get("contactId")
    elif isinstance(attach.get("member"), dict):
        user_id = attach.get("member", {}).get("id")
    elif isinstance(attach.get("user"), dict):
        user_id = attach.get("user", {}).get("id")
    
    # Если нашли ID, попробуем разрешить имя
    if user_id:
        user_name = resolve_user_name(user_id)
    
    # Если нет, попробуем найти имя прямо в структуре
    if not user_name:
        if isinstance(attach.get("member"), dict):
            user_name = attach.get("member", {}).get("name")
        elif isinstance(attach.get("user"), dict):
            user_name = attach.get("user", {}).get("name")
        elif isinstance(attach.get("author"), dict):
            user_name = attach.get("author", {}).get("name")
    
    # === Специальная обработка для "add" (добавление в группу) ===
    if raw_event == "add":
        if user_name:
            return f"✅ {user_name} добавлен(а) в группу"
        else:
            return "✅ Участник добавлен(а) в группу"
    
    # === Специальная обработка для "joinByLink" ===
    if raw_event == "joinByLink":
        if user_name:
            return f"🔗 {user_name} вошёл(а) по ссылке"
        else:
            return "🔗 Участник вошёл по ссылке"
    
    # === Специальная обработка для "remove" (удаление из группы) ===
    if raw_event == "remove":
        if user_name:
            return f"❌ {user_name} удалён(а) из группы"
        else:
            return "❌ Участник удалён из группы"
    
    # === Специальная обработка для "leave" (выход из группы) ===
    if raw_event == "leave":
        if user_name:
            return f"👋 {user_name} вышел(ла) из группы"
        else:
            return "👋 Участник вышел из группы"

    # === Стандартная обработка для остальных событий ===
    candidates = [
        attach.get("title"),
        attach.get("text"),
        attach.get("message"),
        attach.get("controlType"),
        attach.get("type"),
        attach.get("event"),
        attach.get("status"),
        attach.get("action"),
    ]
    first = next((c for c in candidates if c), raw_event or "CONTROL")

    extra_parts = []
    if isinstance(attach.get("members"), list) and attach["members"]:
        members = attach["members"]
        names = []
        for m in members:
            if isinstance(m, dict):
                names.append(m.get("name") or m.get("phone") or str(m.get("id")))
            else:
                names.append(str(m))
        extra_parts.append("участники: " + ", ".join(n for n in names if n))

    if attach.get("callType"):
        extra_parts.append(f"тип звонка: {attach['callType']}")

    if attach.get("action"):
        extra_parts.append(f"действие: {attach['action']}")
    if attach.get("eventType"):
        extra_parts.append(f"событие: {attach['eventType']}")
    if attach.get("reason"):
        extra_parts.append(f"причина: {attach['reason']}")

    # Single member field
    member = attach.get("member") or attach.get("user") or attach.get("author")
    if member:
        if isinstance(member, dict):
            extra_parts.append(
                "участник: "
                + (member.get("name") or member.get("phone") or str(member.get("id")))
            )
        else:
            extra_parts.append(f"участник: {member}")

    tail = f" ({'; '.join(extra_parts)})" if extra_parts else ""
    return f"ℹ️ Системное сообщение: {first}{tail}"


def split_control_attachments(attachments: Iterable[Dict], resolve_user_name) -> tuple[list[Dict], list[str]]:
    media: list[Dict] = []
    service_notes: list[str] = []
    for attach in attachments or []:
        attach_type = str(attach.get("_type") or attach.get("type") or "").upper()
        if attach_type == "CONTROL":
            service_notes.append(describe_control_attach(attach, resolve_user_name))
        else:
            media.append(attach)
    return media, service_notes


def build_outgoing_payload(client: Client, message: Message, chat_title: str = "") -> tuple[str, List[Dict], Set[str]]:
    """
    Prepare caption, attachments and detected types for a message coming from MAX.
    Handles forwards and replies so the context is visible in Telegram.
    """
    link = message.kwargs.get("link") if isinstance(message.kwargs, dict) else {}
    link_type = link.get("type") if isinstance(link, dict) else None
    linked_message = link.get("message") if isinstance(link, dict) else {}

    text = message.text or ""
    attachments = list(message.attaches or [])
    print(f"   📎 Всего вложений в сообщении: {len(attachments)}")
    for idx, att in enumerate(attachments, 1):
        att_type = att.get("_type") or att.get("type") or "UNKNOWN"
        print(f"      [{idx}] {att_type}")
    context_lines: List[str] = []

    # Handle forwarded messages: replace content with original and mark author.
    if link_type == "FORWARD" and isinstance(linked_message, dict):
        text = linked_message.get("text") or ""
        attachments = list(linked_message.get("attaches") or [])
        original_author = _get_user_name_by_id(client, linked_message.get("sender"))
        context_lines.append(f"<blockquote>↩️ Переслано от: <b>{_safe_escape(original_author)}</b></blockquote>")

    # Handle replies: prepend quoted context.
    if link_type == "REPLY" and isinstance(linked_message, dict):
        reply_author = _get_user_name_by_id(client, linked_message.get("sender"))
        reply_text = linked_message.get("text") or ""
        reply_attaches = linked_message.get("attaches") or []
        if not reply_text and reply_attaches:
            reply_text = f"[{reply_attaches[0].get('_type', 'Вложение')}]"
        # Always add reply context, even without text
        context_lines.append(
            f"<blockquote>↪️ Ответ на сообщение от <b>{_safe_escape(reply_author)}</b>{': ' + _safe_escape(reply_text) if reply_text else ''}</blockquote>"
        )

    # Separate service/control attachments from media
    attachments, control_notes = split_control_attachments(
        attachments, lambda uid: _get_user_name_by_id(client, uid)
    )
    if message.type and str(message.type).upper() == "CONTROL" and not control_notes:
        control_notes.append("Системное сообщение: CONTROL")
    context_lines.extend(_safe_escape(n) for n in control_notes if n)

    sender_name = _get_contact_name(message.user)

    # Кастомная подпись для отдельных людей
    if sender_name == "Татьяна Петровна":
        display_name = f"👩‍🏫 {sender_name}"
    else:
        display_name = f"👤 {sender_name}"

    # Добавляем название чата в скобочках
    if chat_title and chat_title != sender_name:
        display_name = f"{display_name} ({_safe_escape(chat_title)})"

    caption_parts = [f"<b>{_safe_escape(display_name)}</b>"]
    caption_parts.extend(context_lines)
    if text:
        caption_parts.append(_safe_escape(text))
    caption = "\n".join(part for part in caption_parts if part)

    detected_types = detect_message_types(text, attachments, link_type, message.type)
    return caption, attachments, detected_types



def _save_chat_title(chat_id: int, title: str) -> None:
    """
    Кэшируем человекочитаемое имя чата, чтобы потом показать его по команде в телеграм-боте.
    """
    if not title:
        return
    try:
        data: Dict[str, str] = {}
        if os.path.exists(CHAT_TITLES_FILE):
            with open(CHAT_TITLES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        key = str(chat_id)
        if data.get(key) == title:
            return
        data[key] = title
        with open(CHAT_TITLES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # Не ломаем пересылку, если не смогли сохранить
        pass


def _get_chat_title(chat_id: int) -> str | None:
    """
    Получает сохранённое название чата из кэша.
    """
    try:
        if os.path.exists(CHAT_TITLES_FILE):
            with open(CHAT_TITLES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(str(chat_id))
    except Exception:
        pass
    return None


def _is_forward_enabled() -> bool:
    """
    Читает состояние пересылки из файла, который обновляет starter.py (telegram_control_loop).
    По умолчанию пересылка включена.
    """
    try:
        with open(FORWARD_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return bool(data.get("forward_enabled", True))
    except Exception:
        return True


def _load_monitored_chats() -> List[Dict]:
    """
    Загружает список мониторимых чатов из кэша chat_titles.json
    """
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


@client.on_connect
def onconnect():
    if client.me != None:
        print(f"Имя: {client.me.contact.names[0].name}, Номер: {client.me.contact.phone} | ID: {client.me.contact.id}")


@client.on_message(filters.any())
def onmessage(client: Client, message: Message):
    # перед пересылкой проверяем флаг, который меняется командами в телеге
    if not _is_forward_enabled():
        return

    print(f"📬 Сообщение из чата: {message.chat.id} | ID: {message.id}")
    
    # Проверяем на дубликаты
    if _is_message_duplicate(message.id):
        print(f"⚠️ Дубликат сообщения {message.id} - пропускаем")
        return
    
    if message.chat.id in MAX_CHAT_IDS and message.status != "REMOVED":
        # Получаем название чата — сначала из кэша, потом используем имя отправителя
        cached_title = _get_chat_title(message.chat.id)
        if cached_title:
            chat_title_text = cached_title
            print(f"DEBUG: Название из кэша: '{chat_title_text}'")
        else:
            chat_title_text = _get_contact_name(message.user)
            print(f"DEBUG: Новое имя отправителя: '{chat_title_text}'")
            _save_chat_title(message.chat.id, chat_title_text)

        caption, msg_attaches, detected_types = build_outgoing_payload(client, message, chat_title_text)

        print(f"📨 Сообщение {message.id} | Вложений: {len(msg_attaches) if msg_attaches else 0}")
        if msg_attaches:
            print(f"   Вложения: {[a.get('_type', a.get('type', 'UNKNOWN')) for a in msg_attaches]}")
        if caption or msg_attaches:
            print(f"✉️ Типы сообщения в MAX: {', '.join(sorted(detected_types)) or 'UNKNOWN'}")
            send_to_telegram(
                TG_BOT_TOKEN,
                TG_CHAT_ID,
                caption,
                msg_attaches,
                TG_THREAD_ID,  # ← ДОБАВЛЕНО!
                MAX_TOKEN,
                message.user.contact.id,
            )


client.run()

