from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.assistant_models import (
    AssistantCommandRequest,
    AssistantCommandResponse,
    Bill,
    BillUpdate,
    EventCreate,
    EventUpdate,
    ItemStatus,
    NoteCreate,
    ReminderCreate,
    ReminderStatus,
    ReminderUpdate,
    ShoppingItemCreate,
    TaskCreate,
    TaskUpdate,
)
from src.nlp import (
    _clean_title_after_date,
    _extract_agenda_target,
    _normalize_entity_title,
    _match_text,
    _extract_recurrence,
    _extract_task_priority,
    _parse_datetime_phrase,
    _split_shopping_items,
    handle_command,
)
from src.service import AssistantService


YES_TOKENS = {
    "yes", "yeah", "yep", "sure", "do it", "go ahead", "confirm", "yes please",
    "si", "claro", "dale", "vale", "hazlo",
    "sim", "claro que sim", "pode", "pode sim", "faz isso",
}
NO_TOKENS = {
    "no", "nope", "cancel", "never mind", "stop", "don't", "do not",
    "cancela", "para", "no hagas eso",
    "nao", "não", "cancela", "pare", "deixa pra la",
}


class LlmAssistantPlan(BaseModel):
    mode: Literal["tool", "clarification", "canonical_command", "unsupported"]
    action: Literal[
        "create_task",
        "create_reminder",
        "create_event",
        "create_and_sync_event_google",
        "create_note",
        "add_shopping_items",
        "clear_shopping_list",
        "clear_google_calendar_day",
        "get_summary",
        "get_agenda",
        "get_briefing",
        "list_items",
        "complete_task",
        "cancel_task",
        "move_task",
        "rename_task",
        "cancel_reminder",
        "move_reminder",
        "snooze_reminder",
        "cancel_event",
        "move_event",
        "pay_bill",
        "cancel_bill",
        "update_bill",
        "sync_task_google",
        "sync_reminder_google",
        "sync_event_google",
    ] | None = None
    canonical_command: str | None = None
    clarification_question: str | None = None
    suggested_confirmation_command: str | None = None
    title: str | None = None
    target_title: str | None = None
    new_title: str | None = None
    details: str | None = None
    when_text: str | None = None
    date_text: str | None = None
    priority: str | None = None
    recurrence: str | None = None
    amount: float | None = None
    items: list[str] = Field(default_factory=list)
    list_type: Literal["tasks", "reminders", "bills", "shopping", "notes", "events"] | None = None
    briefing_kind: Literal["morning", "evening", "tomorrow"] | None = None


SYSTEM_PROMPT = """You are the planning layer for a personal assistant backend.

Return a structured plan for the user's request.

Rules:
- Prefer mode="tool" with a concrete action whenever possible.
- Use mode="clarification" if the request is ambiguous in a way that could change or delete data.
- Use mode="canonical_command" only when a short safe command string is the best fallback.
- Use mode="unsupported" if you truly cannot map the request safely.
- For normal personal-organizer requests, avoid mode="unsupported" unless the request is clearly outside tasks, reminders, events, notes, shopping, bills, summaries, briefings, or Google Calendar actions.
- Never invent existing user data.
- Keep clarification questions short and practical.
- Do not ask numbered multiple-choice questions.
- Do not ask the user to choose from options the backend cannot execute yet.
- Prefer a single direct clarification question that can be answered with a short phrase or yes/no.
- The user may write in English, Spanish, or Portuguese. Understand all three.
- Keep extracted titles in the user's original language when possible.

Available tool actions:
- create_task
- create_reminder
- create_event
- create_and_sync_event_google
- create_note
- add_shopping_items
- clear_shopping_list
- clear_google_calendar_day
- get_summary
- get_agenda
- get_briefing
- list_items
- complete_task
- cancel_task
- move_task
- rename_task
- cancel_reminder
- move_reminder
- snooze_reminder
- cancel_event
- move_event
- pay_bill
- cancel_bill
- update_bill
- sync_task_google
- sync_reminder_google
- sync_event_google

Guidance:
- Informal phrasing is still valid. Requests like "show me my shopping", "what do I need to pay", "what do I have tomorrow", or "put milk and eggs on shopping" should be mapped to the closest safe supported action.
- "calendar" can be ambiguous. If the user says "clear my calendar", ask whether they mean Google Calendar or local assistant events.
- If local assistant events are not directly supported for that action, ask only about Google Calendar or ask the user to say the exact source they want.
- For shopping additions, put each item in items.
- For shopping list viewing requests, use list_items with list_type="shopping".
- For shopping additions like "add milk and eggs to shopping", "put eggs on my list", or similar, use add_shopping_items.
- For agenda/day questions, put the target day phrase in date_text like "today", "tomorrow", or "Friday".
- For reminder/task/event creation, put the subject in title and the time phrase in when_text.
- For move/cancel/rename/pay actions, put the existing item name in target_title.
- For rename actions, put the replacement in new_title.
- For bill amount changes, put the numeric value in amount.
- For briefings, set briefing_kind to morning, evening, or tomorrow.
- For list_items, set list_type to tasks, reminders, bills, shopping, notes, or events.
- For bill-list questions like "what do I need to pay" or "which bills are due", use list_items with list_type="bills".
- For task, reminder, note, or event list questions, prefer list_items over unsupported.
- For commands like "mark X done", use complete_task.
- For commands like "move X to Friday", use move_task, move_reminder, or move_event depending on the object.
- For commands like "put X on Google Calendar", use sync_task_google, sync_reminder_google, or sync_event_google.
- For commands like "add dinner with Alex to my Google Calendar tonight at 7pm", use create_and_sync_event_google.
- For commands like "pay the internet bill", use pay_bill.
- For commands like "change the internet bill to 89 due Friday", use update_bill.
"""


def _openai_enabled() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def _router_model() -> str:
    return (os.getenv("OPENAI_ASSISTANT_MODEL") or "gpt-5-nano").strip()


def _trace_enabled() -> bool:
    return (os.getenv("APP_ASSISTANT_TRACE") or "").strip().lower() in {"1", "true", "yes", "on"}


def _with_trace(response: AssistantCommandResponse, **trace: object) -> AssistantCommandResponse:
    if not _trace_enabled():
        return response
    data = dict(response.data or {})
    data["trace"] = trace
    response.data = data
    return response


def _route_with_openai(text: str, history: list[dict] | None = None) -> LlmAssistantPlan:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    system_prompt = SYSTEM_PROMPT
    if history:
        context_lines = []
        for msg in history[-6:]:
            role = "User" if msg.get("role") == "user" else "Assistant"
            content = (msg.get("text") or "").strip()
            if content:
                context_lines.append(f"{role}: {content}")
        if context_lines:
            system_prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"Recent conversation context (use this to understand follow-up references):\n"
                + "\n".join(context_lines)
            )

    response = client.responses.parse(
        model=_router_model(),
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        ],
        text_format=LlmAssistantPlan,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("OpenAI router returned no parsed output.")
    return parsed


def _list_message(count: int, noun: str) -> str:
    return f"You have {count} {noun}."


CLASSIFICATION_PREFIX = "classify::"
CLASSIFICATION_TYPES = {"task", "reminder", "event", "shopping", "shopping item", "note"}


def _format_bill_name(bill: Bill) -> str:
    return f"{bill.name} ({bill.currency} {bill.amount:,.2f})"


def _llm_fallback_clarification_message() -> str:
    return (
        "I’m not fully sure yet. Are you trying to view, add, update, complete, pay, "
        "or remove something in tasks, reminders, shopping, bills, notes, events, or calendar?"
    )


def _resolve_single_match(matches: list, entity_name: str, label_field: str) -> AssistantCommandResponse | object:
    if not matches:
        return AssistantCommandResponse(
            action=f"missing_{entity_name}",
            message=f"I could not find that {entity_name.replace('_', ' ')}.",
        )
    if len(matches) > 1:
        candidates = [getattr(item, label_field) for item in matches[:5]]
        return AssistantCommandResponse(
            action=f"ambiguous_{entity_name}",
            message=(
                f"I found multiple {entity_name.replace('_', ' ')}s that match. "
                f"Which one do you mean: {', '.join(candidates)}?"
            ),
            data={"candidates": candidates},
        )
    return matches[0]


def _is_google_calendar_creation_request(text: str) -> bool:
    lowered = _match_text(text)
    has_google_calendar = any(token in lowered for token in ["google calendar", "google calendario"])
    has_create_intent = any(token in lowered for token in ["add", "put", "schedule", "create", "agrega", "agregar", "programa", "crear", "adiciona", "adicionar", "agenda", "cria"])
    return has_google_calendar and has_create_intent


def _follow_up_command_from_classification(original_text: str, classification: str) -> str:
    normalized = classification.strip().lower()
    if normalized == "shopping item":
        normalized = "shopping"
    if normalized == "shopping":
        return f"add shopping item {original_text}"
    return f"create {normalized} {original_text}"


def _normalize_google_calendar_event_title(text: str) -> str:
    title = _normalize_entity_title(
        text,
        prefixes=[
        "can you add to my google calendar",
        "could you add to my google calendar",
        "please add to my google calendar",
        "add to my google calendar",
        "put on my google calendar",
        "put into my google calendar",
        "add on my google calendar",
        "schedule on my google calendar",
        "schedule in my google calendar",
        "agrega a mi google calendar",
        "agregar a mi google calendar",
        "pon en mi google calendar",
        "programa en mi google calendar",
        "adiciona no meu google calendar",
        "adicionar no meu google calendar",
        "coloca no meu google calendar",
        "agenda no meu google calendar",
        "cria no meu google calendar",
        ],
    )
    title = re.sub(r"^(add|put|schedule|create|agrega|agregar|anade|poner|programa|crear|adiciona|adicionar|coloca|agenda|cria)\s+", "", title, flags=re.IGNORECASE)
    return title or "Event"


def _execute_plan(
    plan: LlmAssistantPlan,
    request: AssistantCommandRequest,
    service: AssistantService,
) -> AssistantCommandResponse:
    now = request.now or service.current_time(service.get_settings())

    if plan.action == "get_summary":
        summary = service.get_summary(now=now)
        return AssistantCommandResponse(
            action="summary",
            message="Here is your current summary.",
            data=summary.model_dump(mode="json"),
        )

    if plan.action == "get_agenda":
        target = _extract_agenda_target(plan.date_text or request.text, now)
        if target is None:
            return AssistantCommandResponse(
                action="clarification_required",
                message="Which day do you want me to look at?",
            )
        agenda = service.get_agenda_for_date(target)
        return AssistantCommandResponse(
            action="agenda",
            message=f"Here is your agenda for {agenda.date}.",
            data=agenda.model_dump(mode="json"),
        )

    if plan.action == "get_briefing":
        if plan.briefing_kind == "morning":
            briefing = service.get_morning_briefing(now=now)
            action = "morning_briefing"
        elif plan.briefing_kind == "evening":
            briefing = service.get_evening_briefing(now=now)
            action = "evening_briefing"
        else:
            briefing = service.get_tomorrow_briefing(now=now)
            action = "tomorrow_briefing"
        return AssistantCommandResponse(
            action=action,
            message="Here is your briefing.",
            data=briefing.model_dump(mode="json"),
        )

    if plan.action == "list_items":
        if plan.list_type == "tasks":
            tasks = service.list_tasks(status=ItemStatus.active)
            return AssistantCommandResponse(
                action="list_tasks",
                message=_list_message(len(tasks), "active task(s)"),
                data={"tasks": [task.model_dump(mode="json") for task in tasks]},
            )
        if plan.list_type == "reminders":
            reminders = service.list_reminders(status=ReminderStatus.pending)
            return AssistantCommandResponse(
                action="list_reminders",
                message=_list_message(len(reminders), "pending reminder(s)"),
                data={"reminders": [reminder.model_dump(mode="json") for reminder in reminders]},
            )
        if plan.list_type == "bills":
            bills = service.list_bills(status=ItemStatus.active)
            return AssistantCommandResponse(
                action="list_bills",
                message=_list_message(len(bills), "active bill(s)"),
                data={"bills": [bill.model_dump(mode="json") for bill in bills]},
            )
        if plan.list_type == "shopping":
            items = service.list_shopping_items(status=ItemStatus.active)
            return AssistantCommandResponse(
                action="list_shopping_items",
                message=_list_message(len(items), "shopping item(s)"),
                data={"items": [item.model_dump(mode="json") for item in items]},
            )
        if plan.list_type == "notes":
            notes = service.list_notes()
            return AssistantCommandResponse(
                action="list_notes",
                message=_list_message(len(notes), "note(s)"),
                data={"notes": [note.model_dump(mode="json") for note in notes]},
            )
        if plan.list_type == "events":
            events = [event for event in service.list_events() if event.status == ItemStatus.active]
            return AssistantCommandResponse(
                action="list_events",
                message=_list_message(len(events), "event(s)"),
                data={"events": [event.model_dump(mode="json") for event in events]},
            )

    if plan.action == "create_note":
        title = (plan.title or normalized_title_from_request(request.text)).strip() or "Quick note"
        note = service.create_note(NoteCreate(title=title[:60], content=plan.details or request.text))
        return AssistantCommandResponse(
            action="create_note",
            message=f"Saved note '{note.title}'.",
            created_type="note",
            created_id=note.id,
            data=note.model_dump(mode="json"),
        )

    if plan.action == "create_task":
        settings = service.get_settings()
        due_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        recurrence, interval = _extract_recurrence(plan.recurrence or request.text)
        priority = plan.priority or _extract_task_priority(request.text, settings.default_task_priority)
        title = (
            plan.title
            or _normalize_entity_title(request.text, drop_leading_to=True)
            or "Task"
        )
        task = service.create_task(
            TaskCreate(
                title=title,
                details=plan.details or "",
                due_at=due_at,
                priority=priority,
                recurrence=recurrence,
                recurrence_interval=interval,
            )
        )
        return AssistantCommandResponse(
            action="create_task",
            message=f"Created task '{task.title}'.",
            created_type="task",
            created_id=task.id,
            data=task.model_dump(mode="json"),
        )

    if plan.action == "create_reminder":
        remind_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        if remind_at is None:
            return AssistantCommandResponse(
                action="clarification_required",
                message="What time should I set that reminder for?",
            )
        recurrence, interval = _extract_recurrence(plan.recurrence or request.text)
        title = (
            plan.title
            or _normalize_entity_title(request.text, drop_leading_to=True)
            or "Reminder"
        )
        reminder = service.create_reminder(
            ReminderCreate(
                title=title.capitalize(),
                remind_at=remind_at,
                notes=plan.details or "",
                recurrence=recurrence,
                recurrence_interval=interval,
            )
        )
        return AssistantCommandResponse(
            action="create_reminder",
            message=f"Created a reminder for {reminder.title}.",
            created_type="reminder",
            created_id=reminder.id,
            data=reminder.model_dump(mode="json"),
        )

    if plan.action == "create_event":
        starts_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        if starts_at is None:
            return AssistantCommandResponse(
                action="clarification_required",
                message="When should I schedule that event?",
            )
        title = plan.title or _normalize_entity_title(request.text) or "Event"
        event = service.create_event(
            EventCreate(
                title=title,
                starts_at=starts_at,
                notes=plan.details or "",
            )
        )
        return AssistantCommandResponse(
            action="create_event",
            message=f"Scheduled '{event.title}'.",
            created_type="event",
            created_id=event.id,
            data=event.model_dump(mode="json"),
        )

    if plan.action == "create_and_sync_event_google":
        starts_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        if starts_at is None:
            return AssistantCommandResponse(
                action="clarification_required",
                message="When should I put that on your Google Calendar?",
            )
        title = plan.title or _normalize_google_calendar_event_title(request.text) or "Event"
        event = service.create_event(
            EventCreate(
                title=title,
                starts_at=starts_at,
                notes=plan.details or "",
            )
        )
        if service.google_calendar_service is None:
            return AssistantCommandResponse(
                action="google_calendar_unavailable",
                message="I created the event, but Google Calendar is not configured right now.",
                created_type="event",
                created_id=event.id,
                data=event.model_dump(mode="json"),
            )
        result = service.google_calendar_service.sync_local_event(event, settings=service.get_settings())
        service.set_event_google_id(event.id, result.google_event_id)
        synced_event = service.get_event(event.id)
        return AssistantCommandResponse(
            action="create_and_sync_event_google",
            message=f"Added '{synced_event.title}' to your Google Calendar.",
            created_type="event",
            created_id=synced_event.id,
            data={
                "event": synced_event.model_dump(mode="json"),
                "calendar": result.model_dump(mode="json"),
            },
        )

    if plan.action == "add_shopping_items":
        raw_items = plan.items or _split_shopping_items(request.text)
        names = [item.strip(" .") for item in raw_items if item.strip(" .")]
        created = []
        updated_existing = []
        for raw in names:
            shopping_item, existed = service.add_or_increment_shopping_item(ShoppingItemCreate(name=raw, quantity="1"))
            if existed:
                updated_existing.append(shopping_item.model_dump(mode="json"))
            else:
                created.append(shopping_item)
        return AssistantCommandResponse(
            action="create_shopping_items",
            message=f"Updated {len(updated_existing)} and added {len(created)} shopping item(s).",
            created_type="shopping_item" if names else None,
            created_id=created[0].id if created else None,
            data={
                "items": [item.model_dump(mode="json") for item in created],
                "updated_existing": updated_existing,
            },
        )

    if plan.action == "clear_shopping_list":
        cleared = service.clear_active_shopping_items()
        if not cleared:
            return AssistantCommandResponse(action="clear_shopping_list", message="Your shopping list is already empty.", data={"cleared": 0})
        return AssistantCommandResponse(
            action="clear_shopping_list",
            message=f"Cleared {len(cleared)} shopping item(s) from your shopping list.",
            data={"cleared": len(cleared), "items": [item.model_dump(mode="json") for item in cleared]},
        )

    if plan.action == "clear_google_calendar_day":
        target = _extract_agenda_target(plan.date_text or plan.when_text or request.text, now)
        if target is None:
            return AssistantCommandResponse(
                action="clarification_required",
                message="Which day should I clear from Google Calendar?",
            )
        result = service.clear_google_calendar_for_date(target)
        date_label = target.strftime("%A, %B %d")
        if result.deleted == 0:
            return AssistantCommandResponse(
                action="clear_google_calendar_day",
                message=f"Your Google Calendar was already clear for {date_label}.",
                data=result.model_dump(mode="json"),
            )
        return AssistantCommandResponse(
            action="clear_google_calendar_day",
            message=f"Cleared {result.deleted} Google Calendar event(s) for {date_label}.",
            data=result.model_dump(mode="json"),
        )

    if plan.action == "complete_task":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_task_matches_by_title(target_title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        task = service.complete_task(resolved.id)
        return AssistantCommandResponse(
            action="complete_task",
            message=f"Marked task '{task.title}' as completed.",
            created_type="task",
            created_id=task.id,
            data=task.model_dump(mode="json"),
        )

    if plan.action == "cancel_task":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_task_matches_by_title(target_title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        task = service.update_task(resolved.id, TaskUpdate(status=ItemStatus.cancelled))
        return AssistantCommandResponse(
            action="delete_task",
            message=f"Cancelled task '{task.title}'.",
            created_type="task",
            created_id=task.id,
            data=task.model_dump(mode="json"),
        )

    if plan.action == "move_task":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_task_matches_by_title(target_title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        due_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        if due_at is None:
            return AssistantCommandResponse(action="clarification_required", message="When should I move that task to?")
        task = service.update_task(resolved.id, TaskUpdate(due_at=due_at))
        return AssistantCommandResponse(
            action="move_task",
            message=f"Moved task '{task.title}'.",
            created_type="task",
            created_id=task.id,
            data=task.model_dump(mode="json"),
        )

    if plan.action == "rename_task":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_task_matches_by_title(target_title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        new_title = (plan.new_title or "").strip()
        if not new_title:
            return AssistantCommandResponse(action="clarification_required", message="What should I rename that task to?")
        task = service.update_task(resolved.id, TaskUpdate(title=new_title))
        return AssistantCommandResponse(
            action="rename_task",
            message=f"Renamed task to '{task.title}'.",
            created_type="task",
            created_id=task.id,
            data=task.model_dump(mode="json"),
        )

    if plan.action == "cancel_reminder":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_reminder_matches_by_title(target_title), "reminder", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        reminder = service.update_reminder(resolved.id, ReminderUpdate(status=ReminderStatus.dismissed))
        return AssistantCommandResponse(
            action="delete_reminder",
            message=f"Dismissed reminder '{reminder.title}'.",
            created_type="reminder",
            created_id=reminder.id,
            data=reminder.model_dump(mode="json"),
        )

    if plan.action == "move_reminder":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_reminder_matches_by_title(target_title), "reminder", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        remind_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        if remind_at is None:
            return AssistantCommandResponse(action="clarification_required", message="When should I move that reminder to?")
        reminder = service.update_reminder(resolved.id, ReminderUpdate(remind_at=remind_at))
        return AssistantCommandResponse(
            action="move_reminder",
            message=f"Moved reminder '{reminder.title}'.",
            created_type="reminder",
            created_id=reminder.id,
            data=reminder.model_dump(mode="json"),
        )

    if plan.action == "snooze_reminder":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_reminder_matches_by_title(target_title), "reminder", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        remind_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        if remind_at is None:
            return AssistantCommandResponse(action="clarification_required", message="How long should I snooze that reminder for?")
        reminder = service.update_reminder(resolved.id, ReminderUpdate(remind_at=remind_at))
        return AssistantCommandResponse(
            action="snooze_reminder",
            message=f"Snoozed reminder '{reminder.title}'.",
            created_type="reminder",
            created_id=reminder.id,
            data=reminder.model_dump(mode="json"),
        )

    if plan.action == "cancel_event":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_event_matches_by_title(target_title), "event", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        event = service.update_event(resolved.id, EventUpdate(status=ItemStatus.cancelled))
        return AssistantCommandResponse(
            action="delete_event",
            message=f"Cancelled event '{event.title}'.",
            created_type="event",
            created_id=event.id,
            data=event.model_dump(mode="json"),
        )

    if plan.action == "move_event":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_event_matches_by_title(target_title), "event", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        starts_at = _parse_datetime_phrase(plan.when_text or request.text, now)
        if starts_at is None:
            return AssistantCommandResponse(action="clarification_required", message="When should I move that event to?")
        event = service.update_event(resolved.id, EventUpdate(starts_at=starts_at))
        return AssistantCommandResponse(
            action="move_event",
            message=f"Moved event '{event.title}'.",
            created_type="event",
            created_id=event.id,
            data=event.model_dump(mode="json"),
        )

    if plan.action == "pay_bill":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_bill_matches_by_name(target_title), "bill", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        bill = service.mark_bill_paid(resolved.id)
        return AssistantCommandResponse(
            action="pay_bill",
            message=f"Marked bill '{bill.name}' as paid.",
            created_type="bill",
            created_id=bill.id,
            data=bill.model_dump(mode="json"),
        )

    if plan.action == "cancel_bill":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_bill_matches_by_name(target_title), "bill", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        bill = service.update_bill(resolved.id, BillUpdate(status=ItemStatus.cancelled))
        return AssistantCommandResponse(
            action="delete_bill",
            message=f"Cancelled bill '{bill.name}'.",
            created_type="bill",
            created_id=bill.id,
            data=bill.model_dump(mode="json"),
        )

    if plan.action == "update_bill":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_bill_matches_by_name(target_title), "bill", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        updates: dict = {}
        if plan.amount is not None:
            updates["amount"] = plan.amount
        due_at = _parse_datetime_phrase(plan.when_text or "", now) if plan.when_text else None
        if due_at is not None:
            updates["due_at"] = due_at
        if not updates:
            return AssistantCommandResponse(action="clarification_required", message="What should I change about that bill?")
        bill = service.update_bill(resolved.id, BillUpdate(**updates))
        return AssistantCommandResponse(
            action="update_bill",
            message=f"Updated bill '{_format_bill_name(bill)}'.",
            created_type="bill",
            created_id=bill.id,
            data=bill.model_dump(mode="json"),
        )

    if plan.action == "sync_task_google":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_task_matches_by_title(target_title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        if service.google_calendar_service is None:
            return AssistantCommandResponse(action="google_calendar_unavailable", message="Google Calendar is not configured.")
        result = service.google_calendar_service.sync_local_task(resolved, settings=service.get_settings())
        service.set_task_google_id(resolved.id, result.google_event_id)
        return AssistantCommandResponse(
            action="sync_task_google",
            message=f"Synced task '{resolved.title}' to Google Calendar.",
            created_type="task",
            created_id=resolved.id,
            data=result.model_dump(mode="json"),
        )

    if plan.action == "sync_reminder_google":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_reminder_matches_by_title(target_title), "reminder", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        if service.google_calendar_service is None:
            return AssistantCommandResponse(action="google_calendar_unavailable", message="Google Calendar is not configured.")
        result = service.google_calendar_service.sync_local_reminder(resolved, settings=service.get_settings())
        service.set_reminder_google_id(resolved.id, result.google_event_id)
        return AssistantCommandResponse(
            action="sync_reminder_google",
            message=f"Synced reminder '{resolved.title}' to Google Calendar.",
            created_type="reminder",
            created_id=resolved.id,
            data=result.model_dump(mode="json"),
        )

    if plan.action == "sync_event_google":
        target_title = plan.target_title or plan.title or request.text
        resolved = _resolve_single_match(service.find_event_matches_by_title(target_title), "event", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        if service.google_calendar_service is None:
            return AssistantCommandResponse(action="google_calendar_unavailable", message="Google Calendar is not configured.")
        result = service.google_calendar_service.sync_local_event(resolved, settings=service.get_settings())
        service.set_event_google_id(resolved.id, result.google_event_id)
        return AssistantCommandResponse(
            action="sync_event_google",
            message=f"Synced event '{resolved.title}' to Google Calendar.",
            created_type="event",
            created_id=resolved.id,
            data=result.model_dump(mode="json"),
        )

    return AssistantCommandResponse(action="unknown", message="I could not confidently parse that command yet.", data={"text": request.text})


def normalized_title_from_request(text: str) -> str:
    return " ".join(text.strip().split())


def handle_command_with_llm(
    request: AssistantCommandRequest,
    service: AssistantService,
    *,
    record_diagnostic: bool = True,
) -> AssistantCommandResponse:
    def finish(response: AssistantCommandResponse, **trace: object) -> AssistantCommandResponse:
        enriched = _with_trace(response, **trace)
        if record_diagnostic:
            service.record_assistant_diagnostic(request.text, enriched)
        return enriched

    normalized = " ".join(request.text.strip().split())
    lowered = _match_text(normalized)
    pending = service.get_pending_confirmation()
    direct = handle_command(request, service)

    if "clear" in lowered and "calendar" in lowered and "google" not in lowered:
        proposed = "clear my google calendar for today"
        target = _extract_agenda_target(normalized, request.now or service.current_time(service.get_settings()))
        if target is not None:
            proposed = f"clear my google calendar for {target.strftime('%Y-%m-%d')}"
        question = "Do you want me to clear your Google Calendar for that day?"
        service.set_pending_confirmation(question=question, proposed_command=proposed)
        return finish(
            AssistantCommandResponse(
                action="clarification_required",
                message=question,
                data={"proposed_command": proposed},
            ),
            source="calendar_binary_clarification",
            proposed_command=proposed,
        )

    if pending is not None:
        if pending.proposed_command.startswith(CLASSIFICATION_PREFIX):
            classification = lowered.strip()
            if classification in CLASSIFICATION_TYPES:
                original_text = pending.proposed_command[len(CLASSIFICATION_PREFIX):]
                service.clear_pending_confirmation()
                return finish(
                    handle_command_with_llm(
                        AssistantCommandRequest(
                            text=_follow_up_command_from_classification(original_text, classification),
                            now=request.now,
                        ),
                        service,
                        record_diagnostic=False,
                    ),
                    source="pending_classification_choice",
                    classification=classification,
                    original_text=original_text,
                )
        if lowered in YES_TOKENS:
            service.clear_pending_confirmation()
            return finish(
                handle_command_with_llm(
                    AssistantCommandRequest(text=pending.proposed_command, now=request.now),
                    service,
                    record_diagnostic=False,
                ),
                source="pending_confirmation_yes",
                proposed_command=pending.proposed_command,
            )
        if lowered in NO_TOKENS:
            service.clear_pending_confirmation()
            return finish(AssistantCommandResponse(
                action="confirmation_declined",
                message="Okay, I won't do that.",
            ), source="pending_confirmation_no")

    if _is_google_calendar_creation_request(normalized):
        starts_at = _parse_datetime_phrase(normalized, request.now or service.current_time(service.get_settings()))
        if starts_at is None:
            return finish(
                AssistantCommandResponse(
                    action="clarification_required",
                    message="What time should I add that to your Google Calendar?",
                ),
                source="google_calendar_create_heuristic",
            )
        title = _normalize_google_calendar_event_title(normalized)
        event = service.create_event(EventCreate(title=title, starts_at=starts_at))
        if service.google_calendar_service is None:
            return finish(
                AssistantCommandResponse(
                    action="google_calendar_unavailable",
                    message="I created the event, but Google Calendar is not configured right now.",
                    created_type="event",
                    created_id=event.id,
                    data=event.model_dump(mode="json"),
                ),
                source="google_calendar_create_heuristic",
            )
        result = service.google_calendar_service.sync_local_event(event, settings=service.get_settings())
        service.set_event_google_id(event.id, result.google_event_id)
        synced_event = service.get_event(event.id)
        return finish(
            AssistantCommandResponse(
                action="create_and_sync_event_google",
                message=f"Added '{synced_event.title}' to your Google Calendar.",
                created_type="event",
                created_id=synced_event.id,
                data={
                    "event": synced_event.model_dump(mode="json"),
                    "calendar": result.model_dump(mode="json"),
                },
            ),
            source="google_calendar_create_heuristic",
        )

    if direct.action == "confirm_calendar_clear_target":
        proposed = "clear my google calendar for today"
        target = _extract_agenda_target(request.text, request.now or service.current_time(service.get_settings()))
        if target is not None:
            proposed = f"clear my google calendar for {target.strftime('%Y-%m-%d')}"
        service.set_pending_confirmation(
            question=direct.message,
            proposed_command=proposed,
        )
        return finish(
            AssistantCommandResponse(
                action="clarification_required",
                message=direct.message,
                data={"proposed_command": proposed},
            ),
            source="rule_parser_clarification",
            parser_action=direct.action,
            proposed_command=proposed,
        )

    if direct.action != "unknown" and direct.action not in {"google_calendar_test_hint"}:
        return finish(direct, source="rule_parser", parser_action=direct.action)

    if _openai_enabled():
        try:
            plan = _route_with_openai(normalized, history=request.history)
        except Exception as exc:
            plan = None
            llm_error = type(exc).__name__
        else:
            llm_error = None

        if plan is not None:
            if plan.mode == "clarification" and plan.clarification_question:
                proposed = plan.suggested_confirmation_command or plan.canonical_command
                if proposed:
                    service.set_pending_confirmation(
                        question=plan.clarification_question,
                        proposed_command=proposed,
                    )
                return finish(AssistantCommandResponse(
                    action="clarification_required",
                    message=plan.clarification_question,
                    data={"proposed_command": proposed} if proposed else None,
                ), source="llm_clarification", llm_mode=plan.mode, llm_action=plan.action, proposed_command=proposed)

            if plan.mode == "tool" and plan.action:
                executed = _execute_plan(plan, request, service)
                if executed.action != "unknown":
                    return finish(
                        executed,
                        source="llm_tool",
                        llm_mode=plan.mode,
                        llm_action=plan.action,
                        target_title=plan.target_title,
                        title=plan.title,
                    )

            if plan.mode == "canonical_command" and plan.canonical_command:
                executed = handle_command(
                    AssistantCommandRequest(text=plan.canonical_command, now=request.now),
                    service,
                )
                if executed.action != "unknown":
                    return finish(
                        executed,
                        source="llm_canonical_command",
                        llm_mode=plan.mode,
                        canonical_command=plan.canonical_command,
                    )

            if direct.action == "unknown" and direct.message.startswith("I could not confidently classify that yet."):
                service.set_pending_confirmation(
                    question=direct.message,
                    proposed_command=f"{CLASSIFICATION_PREFIX}{request.text}",
                )
                return finish(
                    direct,
                    source="rule_parser_classification_clarification",
                    parser_action=direct.action,
                    llm_mode=plan.mode,
                    llm_action=plan.action,
                )

            if direct.action == "unknown":
                return finish(
                    AssistantCommandResponse(
                        action="clarification_required",
                        message=_llm_fallback_clarification_message(),
                    ),
                    source="llm_fallback_clarification",
                    reason="llm_no_executable_result",
                    llm_mode=plan.mode,
                    llm_action=plan.action,
                    canonical_command=plan.canonical_command,
                )

            return finish(
                direct,
                source="rule_parser_fallback",
                reason="llm_no_executable_result",
                llm_mode=plan.mode,
                llm_action=plan.action,
                canonical_command=plan.canonical_command,
            )

        if direct.action == "unknown":
            return finish(
                AssistantCommandResponse(
                    action="clarification_required",
                    message=_llm_fallback_clarification_message(),
                ),
                source="llm_fallback_clarification",
                reason="openai_error",
                error_type=llm_error,
            )

        return finish(
            direct,
            source="rule_parser_fallback",
            reason="openai_error",
            error_type=llm_error,
        )

    if direct.action == "unknown" and direct.message.startswith("I could not confidently classify that yet."):
        service.set_pending_confirmation(
            question=direct.message,
            proposed_command=f"{CLASSIFICATION_PREFIX}{request.text}",
        )
        return finish(
            direct,
            source="rule_parser_classification_clarification",
            parser_action=direct.action,
        )
    if direct.action == "unknown":
        return finish(
            AssistantCommandResponse(
                action="clarification_required",
                message=_llm_fallback_clarification_message(),
            ),
            source="llm_fallback_clarification",
            reason="openai_disabled",
        )
    if direct.action != "unknown":
        return finish(direct, source="rule_parser", parser_action=direct.action)
    return finish(direct, source="rule_parser_fallback", reason="openai_disabled")
