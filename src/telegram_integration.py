from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from fastapi import HTTPException

from src.assistant_models import (
    AssistantCommandRequest,
    AssistantCommandResponse,
    TelegramConnectionStatus,
    TelegramLinkStartResponse,
    TelegramTestMessageResponse,
)
from src.google_calendar import GoogleCalendarService
from src.llm_assistant import handle_command_with_llm
from src.repository import create_repository
from src.service import AssistantService


def _storage_backend() -> str:
    return (os.getenv("APP_STORAGE_BACKEND") or "json").strip().lower()


def _project_id() -> str | None:
    return os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")


def telegram_bot_token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def telegram_bot_username() -> str | None:
    raw = (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    return raw or None


def telegram_enabled() -> bool:
    return bool(telegram_bot_token() and telegram_bot_username())


def telegram_webhook_secret() -> str | None:
    raw = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    return raw or None


def _firestore_client():
    try:
        from google.cloud import firestore
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Firestore dependencies are not installed. Add google-cloud-firestore to requirements.",
        ) from exc
    try:
        project_id = _project_id()
        if project_id:
            return firestore.Client(project=project_id)
        return firestore.Client()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not initialize Firestore client for Telegram integration: {type(exc).__name__}: {exc}",
        ) from exc


def _registry_file_path() -> Path:
    path = Path("data/telegram/registry.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _registry_refs():
    client = _firestore_client()
    collection = os.getenv("FIRESTORE_TELEGRAM_COLLECTION", "personal_assistant_telegram")
    return (
        client.collection(collection).document("pending_links"),
        client.collection(collection).document("chat_links"),
        client.collection(collection).document("user_links"),
    )


def _json_registry() -> dict[str, Any]:
    path = _registry_file_path()
    if not path.exists():
        payload = {"pending_links": {}, "chat_links": {}, "user_links": {}}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    return json.loads(path.read_text(encoding="utf-8"))


def _registry_state() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if _storage_backend() == "firestore":
        pending_ref, chat_ref, user_ref = _registry_refs()
        pending_snapshot = pending_ref.get()
        chat_snapshot = chat_ref.get()
        user_snapshot = user_ref.get()
        pending = (pending_snapshot.to_dict() or {}).get("items", {}) if pending_snapshot.exists else {}
        chats = (chat_snapshot.to_dict() or {}).get("items", {}) if chat_snapshot.exists else {}
        users = (user_snapshot.to_dict() or {}).get("items", {}) if user_snapshot.exists else {}
        return pending, chats, users
    payload = _json_registry()
    return (
        payload.setdefault("pending_links", {}),
        payload.setdefault("chat_links", {}),
        payload.setdefault("user_links", {}),
    )


def _save_registry_state(pending: dict[str, Any], chats: dict[str, Any], users: dict[str, Any]) -> None:
    if _storage_backend() == "firestore":
        pending_ref, chat_ref, user_ref = _registry_refs()
        pending_ref.set({"items": pending}, merge=True)
        chat_ref.set({"items": chats}, merge=True)
        user_ref.set({"items": users}, merge=True)
        return
    _registry_file_path().write_text(
        json.dumps({"pending_links": pending, "chat_links": chats, "user_links": users}, indent=2),
        encoding="utf-8",
    )


def _utcnow() -> datetime:
    return datetime.utcnow()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _telegram_api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{telegram_bot_token()}/{method}"


def _telegram_api_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _telegram_api_url(method),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=502, detail=f"Telegram API error: {exc.code}: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Telegram API request failed: {type(exc).__name__}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Telegram API returned invalid JSON: {exc}") from exc
    if not payload.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram API returned an error: {payload}")
    return payload


def send_telegram_message(chat_id: str | int, text: str) -> dict[str, Any]:
    return _telegram_api_call(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
        },
    )


def telegram_connection_status(user_id: str) -> TelegramConnectionStatus:
    entry = _registry_state()[2].get(user_id)
    if not telegram_enabled():
        return TelegramConnectionStatus(
            configured=False,
            connected=False,
            bot_username=telegram_bot_username(),
            message="Telegram is not configured yet. Set TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME first.",
        )
    if not entry:
        return TelegramConnectionStatus(
            configured=True,
            connected=False,
            bot_username=telegram_bot_username(),
            message="Telegram is ready, but this account is not linked yet.",
        )
    return TelegramConnectionStatus(
        configured=True,
        connected=True,
        bot_username=telegram_bot_username(),
        telegram_chat_id=entry.get("chat_id"),
        telegram_username=entry.get("telegram_username"),
        telegram_first_name=entry.get("telegram_first_name"),
        linked_at=_parse_dt(entry.get("linked_at")),
        last_message_at=_parse_dt(entry.get("last_message_at")),
        message="Telegram is connected for this account.",
    )


def start_telegram_link(user_id: str, email: str | None) -> TelegramLinkStartResponse:
    if not telegram_enabled():
        return TelegramLinkStartResponse(
            configured=False,
            bot_username=telegram_bot_username(),
            message="Telegram is not configured yet. Set TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME first.",
        )
    pending, chats, users = _registry_state()
    now = _utcnow()
    expires_at = now + timedelta(minutes=30)
    code = secrets.token_urlsafe(12).replace("-", "").replace("_", "")
    pending[code] = {
        "user_id": user_id,
        "email": email,
        "created_at": _iso(now),
        "expires_at": _iso(expires_at),
    }
    _save_registry_state(pending, chats, users)
    deep_link_url = f"https://t.me/{telegram_bot_username()}?start={parse.quote(code)}"
    return TelegramLinkStartResponse(
        configured=True,
        bot_username=telegram_bot_username(),
        deep_link_url=deep_link_url,
        link_code=code,
        expires_at=expires_at,
        message="Open this Telegram bot link and press Start to connect your account.",
    )


def disconnect_telegram(user_id: str) -> TelegramConnectionStatus:
    pending, chats, users = _registry_state()
    user_entry = users.pop(user_id, None)
    if user_entry and user_entry.get("chat_id"):
        chats.pop(str(user_entry["chat_id"]), None)
    _save_registry_state(pending, chats, users)
    return telegram_connection_status(user_id)


def send_telegram_test_message(user_id: str) -> TelegramTestMessageResponse:
    status = telegram_connection_status(user_id)
    if not status.configured:
        return TelegramTestMessageResponse(
            sent=False,
            telegram_chat_id=status.telegram_chat_id,
            message="Telegram is not configured yet.",
        )
    if not status.connected or not status.telegram_chat_id:
        return TelegramTestMessageResponse(
            sent=False,
            telegram_chat_id=status.telegram_chat_id,
            message="Telegram is not linked for this account yet.",
        )
    send_telegram_message(
        status.telegram_chat_id,
        "Telegram is connected to your Personal Pilot account. You can message me here anytime.",
    )
    return TelegramTestMessageResponse(
        sent=True,
        telegram_chat_id=status.telegram_chat_id,
        message="Sent a test message to your Telegram chat.",
    )


def _link_telegram_chat(chat: dict[str, Any], user: dict[str, Any], code: str) -> None:
    pending, chats, users = _registry_state()
    pending.pop(code, None)

    user_id = user["user_id"]
    chat_id = str(chat["id"])
    now = _utcnow()

    existing_user = users.get(user_id)
    if existing_user and existing_user.get("chat_id") and str(existing_user["chat_id"]) != chat_id:
        chats.pop(str(existing_user["chat_id"]), None)

    existing_chat = chats.get(chat_id)
    if existing_chat and existing_chat.get("user_id") and existing_chat.get("user_id") != user_id:
        users.pop(existing_chat["user_id"], None)

    entry = {
        "user_id": user_id,
        "email": user.get("email"),
        "chat_id": chat_id,
        "telegram_username": chat.get("username"),
        "telegram_first_name": chat.get("first_name"),
        "linked_at": _iso(now),
        "last_message_at": _iso(now),
    }
    chats[chat_id] = entry
    users[user_id] = entry
    _save_registry_state(pending, chats, users)


def _assistant_service_for_user(user_id: str) -> AssistantService:
    google_service = GoogleCalendarService(user_id=user_id)
    return AssistantService(create_repository(user_id=user_id), google_calendar_service=google_service)


def _format_agenda(data: dict[str, Any]) -> str:
    lines = [f"Agenda for {data.get('date')}:"] if data.get("date") else ["Your agenda:"]
    events = data.get("events") or []
    reminders = data.get("reminders") or []
    tasks = data.get("tasks") or []
    bills = data.get("bills") or []
    if events:
        lines.append("Events:")
        for event in events[:5]:
            title = event.get("title") or "Event"
            starts_at = event.get("starts_at") or ""
            lines.append(f"- {title} at {starts_at}")
    if reminders:
        lines.append("Reminders:")
        for reminder in reminders[:5]:
            title = reminder.get("title") or "Reminder"
            remind_at = reminder.get("remind_at") or ""
            lines.append(f"- {title} at {remind_at}")
    if tasks:
        lines.append("Tasks:")
        for task in tasks[:5]:
            title = task.get("title") or "Task"
            lines.append(f"- {title}")
    if bills:
        lines.append("Bills:")
        for bill in bills[:5]:
            lines.append(f"- {bill.get('name')} due {bill.get('due_at')}")
    if not any([events, reminders, tasks, bills]):
        lines.append("- No scheduled events, reminders, tasks, or bills for that day.")
    if data.get("best_next_action"):
        lines.append(f"Next: {data['best_next_action']}")
    return "\n".join(lines[:20])


def _format_summary(data: dict[str, Any]) -> str:
    lines = ["Current summary:"]
    if data.get("best_next_action"):
        lines.append(f"Next: {data['best_next_action']}")
    priority_tasks = data.get("priority_tasks") or []
    if priority_tasks:
        lines.append("Priority tasks:")
        for task in priority_tasks[:5]:
            lines.append(f"- {task.get('title')}")
    due_reminders = data.get("due_reminders") or []
    if due_reminders:
        lines.append("Due reminders:")
        for reminder in due_reminders[:5]:
            lines.append(f"- {reminder.get('title')}")
    upcoming_events = data.get("upcoming_events") or []
    if upcoming_events:
        lines.append("Upcoming events:")
        for event in upcoming_events[:5]:
            lines.append(f"- {event.get('title')} at {event.get('starts_at')}")
    return "\n".join(lines[:20])


def _format_list_reply(response: AssistantCommandResponse, data: dict[str, Any]) -> str | None:
    mapping = [
        ("tasks", "Tasks", "title"),
        ("reminders", "Reminders", "title"),
        ("events", "Events", "title"),
        ("bills", "Bills", "name"),
        ("items", "Shopping items", "name"),
        ("notes", "Notes", "title"),
    ]
    for key, label, field in mapping:
        items = data.get(key) or []
        if not items:
            continue
        lines = [f"{label}:"]
        for item in items[:10]:
            value = item.get(field) or "Untitled"
            extra = ""
            if key == "tasks" and item.get("due_at"):
                extra = f" (due {item['due_at']})"
            elif key == "reminders" and item.get("remind_at"):
                extra = f" ({item['remind_at']})"
            elif key == "events" and item.get("starts_at"):
                extra = f" ({item['starts_at']})"
            elif key == "bills" and item.get("due_at"):
                extra = f" (due {item['due_at']})"
            lines.append(f"- {value}{extra}")
        return "\n".join(lines)
    if response.action in {"list_tasks", "list_reminders", "list_events", "list_bills", "list_shopping_items", "list_notes"}:
        return response.message
    return None


def _format_created_reply(response: AssistantCommandResponse, data: dict[str, Any]) -> str | None:
    if response.action == "create_task":
        due_at = data.get("due_at")
        if due_at:
            return f"{response.message}\nDue: {due_at}"
        return response.message
    if response.action == "create_reminder":
        remind_at = data.get("remind_at")
        if remind_at:
            return f"{response.message}\nWhen: {remind_at}"
        return response.message
    if response.action in {"create_event", "create_and_sync_event_google"}:
        event_data = data.get("event") if response.action == "create_and_sync_event_google" else data
        starts_at = (event_data or {}).get("starts_at")
        if starts_at:
            return f"{response.message}\nWhen: {starts_at}"
        return response.message
    return None


def format_telegram_reply(response: AssistantCommandResponse) -> str:
    data = response.data or {}
    if response.action == "agenda":
        return _format_agenda(data)
    if response.action == "summary":
        return _format_summary(data)
    created_reply = _format_created_reply(response, data)
    if created_reply is not None:
        return created_reply
    list_reply = _format_list_reply(response, data)
    if list_reply is not None:
        return list_reply
    if response.message:
        return response.message
    return "Done."


def _help_text() -> str:
    bot_name = f"@{telegram_bot_username()}" if telegram_bot_username() else "the bot"
    return (
        f"This Telegram bot is linked to your Personal Assistant account.\n"
        f"You can send messages like:\n"
        f"- what do I have today?\n"
        f"- add task take out the trash\n"
        f"- clear my shopping list\n\n"
        f"If this chat is not linked yet, start from the app and open the Telegram link for {bot_name}."
    )


def handle_telegram_update(update: dict[str, Any]) -> dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True, "status": "ignored"}

    pending, chats, users = _registry_state()
    chat_key = str(chat_id)

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        pending_entry = pending.get(code)
        if not code or not pending_entry:
            send_telegram_message(chat_id, "That link code is invalid or expired. Start the Telegram connection again from the app.")
            return {"ok": True, "status": "invalid_link"}
        expires_at = _parse_dt(pending_entry.get("expires_at"))
        if expires_at and expires_at < _utcnow():
            pending.pop(code, None)
            _save_registry_state(pending, chats, users)
            send_telegram_message(chat_id, "That link code has expired. Start the Telegram connection again from the app.")
            return {"ok": True, "status": "expired_link"}
        _link_telegram_chat(chat, pending_entry, code)
        send_telegram_message(chat_id, "Telegram is now linked to your Personal Assistant account. Send me a message whenever you want help.")
        return {"ok": True, "status": "linked"}

    if text.startswith("/help"):
        send_telegram_message(chat_id, _help_text())
        return {"ok": True, "status": "help_sent"}

    linked = chats.get(chat_key)
    if not linked:
        send_telegram_message(chat_id, "This Telegram chat is not linked yet. Open the Telegram connection link from the app first.")
        return {"ok": True, "status": "not_linked"}

    linked["last_message_at"] = _iso(_utcnow())
    chats[chat_key] = linked
    users[linked["user_id"]] = linked
    _save_registry_state(pending, chats, users)

    service = _assistant_service_for_user(linked["user_id"])
    response = handle_command_with_llm(AssistantCommandRequest(text=text), service)
    send_telegram_message(chat_id, format_telegram_reply(response))
    return {"ok": True, "status": "processed", "action": response.action}
