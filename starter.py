import subprocess
import time
import sys, os
import datetime
import threading
import json

import requests
from telegram import send_to_telegram, handle_telegram_commands
from dotenv import load_dotenv

load_dotenv()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MONITOR_ID = os.getenv("MONITOR_ID")
TG_CONTROL_ADMIN_ID = os.getenv("TG_CONTROL_ADMIN_ID")  # опционально ограничить по пользователю
MAX_CHAT_IDS_ENV = os.getenv("MAX_CHAT_IDS") or ""
MAX_CHAT_IDS = []
if MAX_CHAT_IDS_ENV:
    try:
        MAX_CHAT_IDS = [int(x) for x in MAX_CHAT_IDS_ENV.split(",") if x.strip()]
    except Exception:
        MAX_CHAT_IDS = []

def run_with_restart():
    restart_alarm = False
    while True:
        try:
            print(f"[{datetime.datetime.now()}] Запуск main.py...")
            
            process = subprocess.Popen(
                [sys.executable, "main.py"],
                stderr=subprocess.PIPE,
                text=True)
            if MONITOR_ID != "":
                send_to_telegram(
                    TG_BOT_TOKEN,
                    MONITOR_ID,
                    f"<b>Бот встал</b>",
                )
            restart_alarm = True
            process.wait()
            exit_code = process.returncode
            stderr = process.communicate()
            if MONITOR_ID != "" and restart_alarm:
                send_to_telegram(
                    TG_BOT_TOKEN,
                    MONITOR_ID,
                    f"[{datetime.datetime.now()}] Скрипт упал (код: {exit_code})\nstderr:{stderr}"
        
            
                )
                restart_alarm = False
            print(f"[{datetime.datetime.now()}] Скрипт упал (код: {exit_code}). Перезапуск через 3 секунды...")
            time.sleep(3)
                
        except KeyboardInterrupt:
            print(f"\n[{datetime.datetime.now()}] Остановлено пользователем")
            if process:
                process.terminate()
            break
        except Exception as e:
            print(f"[{datetime.datetime.now()}] Ошибка: {e}")
            time.sleep(3)

def _set_forward_enabled(enabled: bool):
    """
    Сохраняем флаг пересылки в файл, чтобы main.py мог его читать.
    True -> включена, False -> выключена.
    """
    state = {"forward_enabled": bool(enabled)}
    with open("forward_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f)


def _get_forward_enabled() -> bool:
    try:
        with open("forward_state.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return bool(data.get("forward_enabled", True))
    except Exception:
        return True


def telegram_control_loop():
    """
    Цикл опроса команд бота в Telegram.
    Работает в личных чатах и супергруппах, команды:
      /pause  – остановить пересылку
      /resume – возобновить пересылку
      /status – показать состояние
      /chats  – показать список отслеживаемых чатов
    Если задан TG_CONTROL_ADMIN_ID – принимает команды только от этого пользователя.
    Поддерживает темы в супергруппах.
    """
    if not TG_BOT_TOKEN:
        return

    api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"
    offset = None

    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(f"{api_url}/getUpdates", params=params, timeout=35)
            data = resp.json()
            if not data.get("ok"):
                time.sleep(3)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message")
                if not message:
                    continue

                chat = message.get("chat") or {}
                # Принимаем команды из личных чатов или групповых чатов
                chat_type = chat.get("type")
                if chat_type not in ("private", "group", "supergroup"):
                    continue

                from_user = message.get("from") or {}
                if TG_CONTROL_ADMIN_ID:
                    try:
                        admin_id = int(TG_CONTROL_ADMIN_ID)
                    except ValueError:
                        admin_id = None
                    if admin_id and from_user.get("id") != admin_id:
                        continue

                text = (message.get("text") or "").strip()
                if not text.startswith("/"):
                    continue

                chat_id = chat.get("id")
                if not chat_id:
                    continue

                # Извлекаем message_thread_id для ответа в тему супергруппы
                thread_id = message.get("message_thread_id")

                cmd = text.split()[0].lower()
                cmd = cmd.split("@")[0]
                if cmd == "/pause":
                    _set_forward_enabled(False)
                    payload = {
                        "chat_id": chat_id,
                        "text": "⏸ Пересылка сообщений остановлена. Используйте /resume для запуска.",
                    }
                    if thread_id:
                        payload["message_thread_id"] = thread_id
                    requests.post(
                        f"{api_url}/sendMessage",
                        data=payload,
                        timeout=10,
                    )
                elif cmd == "/resume":
                    _set_forward_enabled(True)
                    payload = {
                        "chat_id": chat_id,
                        "text": "▶️ Пересылка сообщений возобновлена.",
                    }
                    if thread_id:
                        payload["message_thread_id"] = thread_id
                    requests.post(
                        f"{api_url}/sendMessage",
                        data=payload,
                        timeout=10,
                    )
                else:
                    handled = handle_telegram_commands(
                        TG_BOT_TOKEN,
                        chat_id,
                        text,
                        thread_id=thread_id,
                        forward_enabled=_get_forward_enabled(),
                        fallback_chat_ids=MAX_CHAT_IDS,
                    )
                    if handled:
                        continue
        except Exception:
            time.sleep(5)


if __name__ == "__main__":
    # Запускаем управление через телеграм-бота в отдельном потоке
    threading.Thread(target=telegram_control_loop, name="TelegramControl", daemon=True).start()
    run_with_restart()
