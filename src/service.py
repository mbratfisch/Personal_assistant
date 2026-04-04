from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from src.assistant_models import (
    ActionHistoryEntry,
    AssistantSummary,
    AssistantCommandResponse,
    AssistantDiagnosticEntry,
    DailyAgenda,
    Bill,
    BillCreate,
    BillUpdate,
    BriefingResponse,
    DatabaseModel,
    Event,
    EventCreate,
    EventUpdate,
    GoogleCalendarClearResponse,
    GoogleCalendarPullSyncResponse,
    ItemStatus,
    Note,
    NoteCreate,
    PendingAssistantConfirmation,
    RecurrenceFrequency,
    Reminder,
    ReminderCreate,
    ReminderStatus,
    ReminderUpdate,
    ShoppingItem,
    ShoppingItemCreate,
    ShoppingItemUpdate,
    Task,
    TaskCreate,
    TaskUpdate,
    UndoActionResult,
    UserSettings,
    UserSettingsUpdate,
)
from src.reminder_engine import advance_datetime, find_due_reminders
from src.repository import AssistantRepository

if TYPE_CHECKING:
    from src.google_calendar import GoogleCalendarService

T = TypeVar("T")


class AssistantService:
    def __init__(
        self,
        repository: AssistantRepository,
        google_calendar_service: "GoogleCalendarService | None" = None,
    ) -> None:
        self.repository = repository
        self.google_calendar_service = google_calendar_service

    def _load(self) -> DatabaseModel:
        return self.repository.load()

    def _save(self, db: DatabaseModel) -> DatabaseModel:
        return self.repository.save(db)

    def _get_by_id(self, items: list[T], item_id: str) -> T:
        for item in items:
            if getattr(item, "id") == item_id:
                return item
        raise HTTPException(status_code=404, detail=f"Item with id '{item_id}' was not found.")

    def _find_by_title(self, items: list[T], title: str, field_name: str = "title") -> T | None:
        query = title.strip().lower()
        if not query:
            return None
        exact_match = next(
            (
                item
                for item in items
                if str(getattr(item, field_name, "")).strip().lower() == query
            ),
            None,
        )
        if exact_match is not None:
            return exact_match
        return next(
            (
                item
                for item in items
                if query in str(getattr(item, field_name, "")).strip().lower()
            ),
            None,
        )

    def _find_matches_by_title(self, items: list[T], title: str, field_name: str = "title") -> list[T]:
        query = title.strip().lower()
        if not query:
            return []
        exact_matches = [
            item
            for item in items
            if str(getattr(item, field_name, "")).strip().lower() == query
        ]
        if exact_matches:
            return exact_matches
        return [
            item
            for item in items
            if query in str(getattr(item, field_name, "")).strip().lower()
        ]

    def _history_limit(self, db: DatabaseModel) -> None:
        if len(db.action_history) > 100:
            db.action_history = db.action_history[-100:]

    def _diagnostic_limit(self, db: DatabaseModel) -> None:
        if len(db.assistant_diagnostics) > 100:
            db.assistant_diagnostics = db.assistant_diagnostics[-100:]

    def _record_history(
        self,
        db: DatabaseModel,
        action_type: str,
        entity_type: str,
        entity_id: str,
        before: dict | None = None,
        after: dict | None = None,
    ) -> None:
        db.action_history.append(
            ActionHistoryEntry(
                action_type=action_type,
                entity_type=entity_type,
                entity_id=entity_id,
                before=before,
                after=after,
            )
        )
        self._history_limit(db)

    def record_assistant_diagnostic(self, input_text: str, response: AssistantCommandResponse) -> AssistantDiagnosticEntry:
        db = self._load()
        trace = dict(response.data or {}).get("trace") or {}
        status = "success"
        if response.action == "clarification_required":
            status = "clarification"
        elif response.action == "unknown" or str(trace.get("reason") or "").startswith("openai_"):
            status = "error"
        elif str(trace.get("source") or "").endswith("fallback"):
            status = "error"
        diagnostic = AssistantDiagnosticEntry(
            input_text=input_text,
            response_action=response.action,
            message=response.message,
            status=status,
            trace_source=trace.get("source"),
            trace_reason=trace.get("reason"),
            llm_action=trace.get("llm_action"),
            parser_action=trace.get("parser_action"),
        )
        db.assistant_diagnostics.append(diagnostic)
        self._diagnostic_limit(db)
        self._save(db)
        return diagnostic

    def _entity_collection(self, db: DatabaseModel, entity_type: str) -> list:
        mapping = {
            "note": db.notes,
            "task": db.tasks,
            "shopping_item": db.shopping_items,
            "bill": db.bills,
            "event": db.events,
            "reminder": db.reminders,
        }
        if entity_type not in mapping:
            raise HTTPException(status_code=400, detail=f"Unsupported entity type '{entity_type}'.")
        return mapping[entity_type]

    def _restore_entity(self, entity_type: str, payload: dict):
        mapping = {
            "note": Note,
            "task": Task,
            "shopping_item": ShoppingItem,
            "bill": Bill,
            "event": Event,
            "reminder": Reminder,
        }
        if entity_type not in mapping:
            raise HTTPException(status_code=400, detail=f"Unsupported entity type '{entity_type}'.")
        return mapping[entity_type](**payload)

    def _replace_or_append_entity(self, collection: list, entity) -> None:
        for index, item in enumerate(collection):
            if getattr(item, "id") == getattr(entity, "id"):
                collection[index] = entity
                return
        collection.append(entity)

    def _remove_entity_by_id(self, collection: list, entity_id: str) -> None:
        for index, item in enumerate(collection):
            if getattr(item, "id") == entity_id:
                collection.pop(index)
                return

    def _priority_rank(self, priority: str) -> int:
        normalized = priority.strip().lower()
        if normalized == "high":
            return 0
        if normalized == "medium":
            return 1
        return 2

    def _normalize_priority(self, priority: str | None) -> str:
        normalized = (priority or "medium").strip().lower()
        if normalized in {"high", "medium", "low"}:
            return normalized
        return "medium"

    def _is_outside_workday(self, value: datetime, settings: UserSettings) -> bool:
        start_hour = max(0, min(settings.workday_start_hour, 23))
        end_hour = max(0, min(settings.workday_end_hour, 23))
        return value.hour < start_hour or value.hour > end_hour

    def _workday_label(self, settings: UserSettings) -> str:
        start_label = datetime(2000, 1, 1, settings.workday_start_hour, 0).strftime("%I:%M %p").lstrip("0")
        end_label = datetime(2000, 1, 1, settings.workday_end_hour, 0).strftime("%I:%M %p").lstrip("0")
        return f"{start_label} to {end_label}"

    def _task_score(self, task: Task, reference_time: datetime) -> int:
        score = 0
        score += {"high": 90, "medium": 60, "low": 30}.get(task.priority.strip().lower(), 40)
        if task.due_at is None:
            return score
        if task.due_at < reference_time:
            score += 120
        else:
            delta = task.due_at - reference_time
            if delta <= timedelta(hours=2):
                score += 80
            elif delta <= timedelta(hours=8):
                score += 45
            elif delta <= timedelta(days=1):
                score += 20
        return score

    def _sort_tasks_for_plan(self, tasks: list[Task], reference_time: datetime | None = None) -> list[Task]:
        now = reference_time or self.current_time()
        return sorted(
            tasks,
            key=lambda task: (
                -self._task_score(task, now),
                task.due_at or datetime.max,
                task.created_at,
            ),
        )

    def _sort_events(self, events: list[Event]) -> list[Event]:
        return sorted(events, key=lambda event: (event.starts_at, event.ends_at or event.starts_at))

    def _timezone(self, settings: UserSettings | None = None) -> ZoneInfo:
        timezone_name = settings.timezone if settings and settings.timezone else "UTC"
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def current_time(self, settings: UserSettings | None = None) -> datetime:
        return datetime.now(self._timezone(settings)).replace(tzinfo=None)

    def _google_events_between(
        self,
        start_at: datetime,
        end_at: datetime,
        settings: UserSettings,
        local_events: list[Event] | None = None,
    ) -> list[Event]:
        if self.google_calendar_service is None:
            return []
        try:
            status = self.google_calendar_service.auth_status(settings=settings)
            if not status.connected:
                return []
            pulled_events = self.google_calendar_service.list_events_between(
                start_at=start_at,
                end_at=end_at,
                settings=settings,
            )
        except Exception:
            return []

        known_google_ids = {
            event.google_event_id
            for event in (local_events or [])
            if event.google_event_id
        }
        return [
            event
            for event in pulled_events
            if not event.google_event_id or event.google_event_id not in known_google_ids
        ]

    def _detect_agenda_conflicts(
        self,
        tasks: list[Task],
        bills: list[Bill],
        reminders: list[Reminder],
        events: list[Event],
        settings: UserSettings,
    ) -> list[str]:
        conflicts: list[str] = []
        ordered_events = self._sort_events(events)

        for index, current in enumerate(ordered_events):
            current_end = current.ends_at or (current.starts_at + timedelta(hours=1))
            for other in ordered_events[index + 1 :]:
                other_end = other.ends_at or (other.starts_at + timedelta(hours=1))
                if other.starts_at >= current_end:
                    break
                if current.starts_at < other_end and other.starts_at < current_end:
                    conflicts.append(
                        f"'{current.title}' overlaps with '{other.title}' around {current.starts_at.strftime('%I:%M %p').lstrip('0')}."
                    )

        for reminder in sorted(reminders, key=lambda item: item.remind_at):
            for event in ordered_events:
                event_end = event.ends_at or (event.starts_at + timedelta(hours=1))
                if event.starts_at - timedelta(minutes=30) <= reminder.remind_at <= event_end:
                    conflicts.append(
                        f"Reminder '{reminder.title}' lands close to event '{event.title}' at {reminder.remind_at.strftime('%I:%M %p').lstrip('0')}."
                    )
                    break

        timed_tasks = [task for task in tasks if task.due_at is not None]
        for task in timed_tasks:
            for event in ordered_events:
                event_end = event.ends_at or (event.starts_at + timedelta(hours=1))
                if event.starts_at - timedelta(minutes=30) <= task.due_at <= event_end:
                    conflicts.append(
                        f"Task '{task.title}' is due during or right before '{event.title}'."
                    )
                    break

        morning_load = sum(1 for event in ordered_events if event.starts_at.hour < 12)
        morning_load += sum(1 for reminder in reminders if reminder.remind_at.hour < 12)
        morning_load += sum(1 for task in timed_tasks if task.due_at and task.due_at.hour < 12)
        if morning_load >= 3:
            conflicts.append("You have a busy morning with 3 or more scheduled items before noon.")

        if len([bill for bill in bills if bill.due_at.hour < 18]) >= 2:
            conflicts.append("You have multiple bills due today, so it may help to batch payments together.")

        for event in ordered_events:
            if self._is_outside_workday(event.starts_at, settings):
                conflicts.append(
                    f"Event '{event.title}' is outside your preferred work hours ({self._workday_label(settings)})."
                )

        for task in timed_tasks:
            if task.due_at is not None and self._is_outside_workday(task.due_at, settings):
                conflicts.append(
                    f"Task '{task.title}' is due outside your preferred work hours ({self._workday_label(settings)})."
                )

        deduped: list[str] = []
        for conflict in conflicts:
            if conflict not in deduped:
                deduped.append(conflict)
        return deduped

    def _build_suggested_plan(
        self,
        target_date: datetime,
        tasks: list[Task],
        bills: list[Bill],
        reminders: list[Reminder],
        events: list[Event],
        settings: UserSettings,
    ) -> list[str]:
        suggestions: list[str] = []
        ordered_events = self._sort_events(events)
        current_local = self.current_time(settings)
        reference_time = current_local if target_date.date() == current_local.date() else datetime.combine(target_date.date(), datetime.min.time())
        ordered_tasks = self._sort_tasks_for_plan(tasks, reference_time=reference_time)
        ordered_reminders = sorted(reminders, key=lambda reminder: reminder.remind_at)
        ordered_bills = sorted(bills, key=lambda bill: bill.due_at)

        for event in ordered_events:
            start_text = event.starts_at.strftime("%I:%M %p").lstrip("0")
            suggestions.append(f"Prepare for '{event.title}' by {start_text}.")

        for reminder in ordered_reminders:
            remind_text = reminder.remind_at.strftime("%I:%M %p").lstrip("0")
            suggestions.append(f"Handle reminder '{reminder.title}' at {remind_text}.")

        for task in ordered_tasks[:3]:
            if task.due_at is not None:
                due_text = task.due_at.strftime("%I:%M %p").lstrip("0")
                suggestions.append(f"Complete task '{task.title}' before {due_text}.")
            else:
                suggestions.append(f"Make progress on task '{task.title}'.")

        for bill in ordered_bills:
            suggestions.append(f"Pay bill '{bill.name}' today to avoid missing the due date.")

        if any(self._is_outside_workday(event.starts_at, settings) for event in ordered_events):
            suggestions.append(
                f"You have commitments outside your preferred work hours ({self._workday_label(settings)}), so consider adjusting your day."
            )

        if not suggestions:
            suggestions.append(
                f"{target_date.strftime('%A')} is clear right now. Use the open time for planning, errands, or priority work."
            )

        deduped: list[str] = []
        for suggestion in suggestions:
            if suggestion not in deduped:
                deduped.append(suggestion)
        return deduped[:6]

    def _build_best_next_action(
        self,
        reference_time: datetime,
        tasks: list[Task],
        bills: list[Bill],
        reminders: list[Reminder],
        events: list[Event],
    ) -> str | None:
        pending_reminders = sorted(reminders, key=lambda reminder: reminder.remind_at)
        if pending_reminders:
            next_reminder = pending_reminders[0]
            if next_reminder.remind_at <= reference_time + timedelta(hours=1):
                time_text = next_reminder.remind_at.strftime("%I:%M %p").lstrip("0")
                return f"Handle reminder '{next_reminder.title}' by {time_text}."

        ordered_events = self._sort_events(events)
        if ordered_events:
            next_event = ordered_events[0]
            if next_event.starts_at <= reference_time + timedelta(hours=2):
                start_text = next_event.starts_at.strftime("%I:%M %p").lstrip("0")
                return f"Prepare for '{next_event.title}' before {start_text}."

        ordered_tasks = self._sort_tasks_for_plan(tasks, reference_time=reference_time)
        if ordered_tasks:
            next_task = ordered_tasks[0]
            if next_task.due_at is not None:
                due_text = next_task.due_at.strftime("%I:%M %p").lstrip("0")
                return f"Focus on task '{next_task.title}' next so it is done before {due_text}."
            return f"Focus on task '{next_task.title}' next."

        overdue_bills = sorted([bill for bill in bills if bill.due_at <= reference_time], key=lambda bill: bill.due_at)
        if overdue_bills:
            return f"Pay overdue bill '{overdue_bills[0].name}' next."

        if bills:
            soonest_bill = sorted(bills, key=lambda bill: bill.due_at)[0]
            return f"Plan to pay bill '{soonest_bill.name}' today."

        return None

    def create_note(self, payload: NoteCreate) -> Note:
        db = self._load()
        note = Note(**payload.model_dump())
        db.notes.append(note)
        self._record_history(
            db,
            action_type="create",
            entity_type="note",
            entity_id=note.id,
            after=note.model_dump(mode="json"),
        )
        self._save(db)
        return note

    def get_settings(self) -> UserSettings:
        db = self._load()
        return db.settings

    def get_pending_confirmation(self) -> PendingAssistantConfirmation | None:
        db = self._load()
        return db.pending_confirmation

    def set_pending_confirmation(self, question: str, proposed_command: str) -> PendingAssistantConfirmation:
        db = self._load()
        db.pending_confirmation = PendingAssistantConfirmation(
            question=question,
            proposed_command=proposed_command,
        )
        self._save(db)
        return db.pending_confirmation

    def clear_pending_confirmation(self) -> None:
        db = self._load()
        if db.pending_confirmation is None:
            return
        db.pending_confirmation = None
        self._save(db)

    def update_settings(self, payload: UserSettingsUpdate) -> UserSettings:
        db = self._load()
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(db.settings, key, value)
        db.settings.default_task_priority = self._normalize_priority(db.settings.default_task_priority)
        db.settings.workday_start_hour = max(0, min(db.settings.workday_start_hour, 23))
        db.settings.workday_end_hour = max(0, min(db.settings.workday_end_hour, 23))
        db.settings.preferred_reminder_lead_minutes = max(0, db.settings.preferred_reminder_lead_minutes)
        self._save(db)
        return db.settings

    def mark_google_calendar_offer_seen(self) -> UserSettings:
        db = self._load()
        db.settings.google_calendar_offer_seen = True
        db.settings.google_calendar_offer_updated_at = datetime.utcnow()
        self._save(db)
        return db.settings

    def decline_google_calendar_offer(self) -> UserSettings:
        db = self._load()
        db.settings.google_calendar_offer_seen = True
        db.settings.google_calendar_offer_declined = True
        db.settings.google_calendar_offer_updated_at = datetime.utcnow()
        self._save(db)
        return db.settings

    def clear_google_calendar_decline(self) -> UserSettings:
        db = self._load()
        db.settings.google_calendar_offer_seen = True
        db.settings.google_calendar_offer_declined = False
        db.settings.google_calendar_offer_updated_at = datetime.utcnow()
        self._save(db)
        return db.settings

    def set_google_calendar_connected_profile(self, profile_key: str | None) -> UserSettings:
        db = self._load()
        db.settings.google_calendar_connected_profile_key = profile_key
        db.settings.google_calendar_offer_seen = True
        db.settings.google_calendar_offer_declined = False
        db.settings.google_calendar_offer_updated_at = datetime.utcnow()
        self._save(db)
        return db.settings

    def list_notes(self, query: str | None = None) -> list[Note]:
        db = self._load()
        if not query:
            return db.notes
        q = query.lower()
        return [n for n in db.notes if q in n.title.lower() or q in n.content.lower()]

    def find_note_by_title(self, title: str) -> Note | None:
        db = self._load()
        return self._find_by_title(db.notes, title, field_name="title")

    def delete_note(self, note_id: str) -> None:
        db = self._load()
        note = self._get_by_id(db.notes, note_id)
        before = note.model_dump(mode="json")
        self._remove_entity_by_id(db.notes, note_id)
        self._record_history(
            db,
            action_type="delete",
            entity_type="note",
            entity_id=note_id,
            before=before,
        )
        self._save(db)

    def create_task(self, payload: TaskCreate) -> Task:
        db = self._load()
        task = Task(**payload.model_dump())
        db.tasks.append(task)
        self._record_history(
            db,
            action_type="create",
            entity_type="task",
            entity_id=task.id,
            after=task.model_dump(mode="json"),
        )
        self._save(db)
        return task

    def find_duplicate_task(self, payload: TaskCreate) -> Task | None:
        db = self._load()
        for task in db.tasks:
            if task.status != ItemStatus.active:
                continue
            if task.title.strip().lower() != payload.title.strip().lower():
                continue
            if (task.details or "").strip() != (payload.details or "").strip():
                continue
            if task.due_at != payload.due_at:
                continue
            return task
        return None

    def list_tasks(self, status: ItemStatus | None = None) -> list[Task]:
        db = self._load()
        if status is None:
            return db.tasks
        return [task for task in db.tasks if task.status == status]

    def find_task_by_title(self, title: str) -> Task | None:
        db = self._load()
        return self._find_by_title(db.tasks, title, field_name="title")

    def find_task_matches_by_title(self, title: str) -> list[Task]:
        db = self._load()
        return self._find_matches_by_title(
            [task for task in db.tasks if task.status == ItemStatus.active],
            title,
            field_name="title",
        )

    def update_task(self, task_id: str, payload: TaskUpdate) -> Task:
        db = self._load()
        task = self._get_by_id(db.tasks, task_id)
        before = task.model_dump(mode="json")
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(task, key, value)
        task.updated_at = datetime.utcnow()
        self._record_history(
            db,
            action_type="update",
            entity_type="task",
            entity_id=task.id,
            before=before,
            after=task.model_dump(mode="json"),
        )
        self._save(db)
        return task

    def complete_task(self, task_id: str) -> Task:
        db = self._load()
        task = self._get_by_id(db.tasks, task_id)
        before = task.model_dump(mode="json")
        task.status = ItemStatus.completed
        task.updated_at = datetime.utcnow()
        generated_task_id: str | None = None
        if task.recurrence and task.due_at:
            next_due_at = advance_datetime(task.due_at, task.recurrence, task.recurrence_interval)
            if next_due_at is not None:
                next_task = Task(
                    title=task.title,
                    details=task.details,
                    due_at=next_due_at,
                    priority=task.priority,
                    recurrence=task.recurrence,
                    recurrence_interval=task.recurrence_interval,
                )
                db.tasks.append(next_task)
                generated_task_id = next_task.id
        after = task.model_dump(mode="json")
        if generated_task_id is not None:
            after["generated_recurring_id"] = generated_task_id
        self._record_history(
            db,
            action_type="complete",
            entity_type="task",
            entity_id=task.id,
            before=before,
            after=after,
        )
        self._save(db)
        return task

    def get_task(self, task_id: str) -> Task:
        db = self._load()
        return self._get_by_id(db.tasks, task_id)

    def set_task_google_id(self, task_id: str, google_event_id: str) -> Task:
        db = self._load()
        task = self._get_by_id(db.tasks, task_id)
        task.google_event_id = google_event_id
        task.updated_at = datetime.utcnow()
        self._save(db)
        return task

    def create_shopping_item(self, payload: ShoppingItemCreate) -> ShoppingItem:
        db = self._load()
        item = ShoppingItem(**payload.model_dump())
        db.shopping_items.append(item)
        self._record_history(
            db,
            action_type="create",
            entity_type="shopping_item",
            entity_id=item.id,
            after=item.model_dump(mode="json"),
        )
        self._save(db)
        return item

    def add_or_increment_shopping_item(self, payload: ShoppingItemCreate) -> tuple[ShoppingItem, bool]:
        db = self._load()
        existing = next(
            (
                item
                for item in db.shopping_items
                if item.status == ItemStatus.active and item.name.strip().lower() == payload.name.strip().lower()
            ),
            None,
        )
        if existing is not None:
            before = existing.model_dump(mode="json")
            try:
                current_qty = int(existing.quantity)
                incoming_qty = int(payload.quantity)
                existing.quantity = str(current_qty + incoming_qty)
            except ValueError:
                if payload.quantity and payload.quantity != "1":
                    existing.quantity = f"{existing.quantity} + {payload.quantity}"
            existing.updated_at = datetime.utcnow()
            self._record_history(
                db,
                action_type="update",
                entity_type="shopping_item",
                entity_id=existing.id,
                before=before,
                after=existing.model_dump(mode="json"),
            )
            self._save(db)
            return existing, True
        item = ShoppingItem(**payload.model_dump())
        db.shopping_items.append(item)
        self._record_history(
            db,
            action_type="create",
            entity_type="shopping_item",
            entity_id=item.id,
            after=item.model_dump(mode="json"),
        )
        self._save(db)
        return item, False

    def list_shopping_items(self, status: ItemStatus | None = None) -> list[ShoppingItem]:
        db = self._load()
        if status is None:
            return db.shopping_items
        return [item for item in db.shopping_items if item.status == status]

    def find_shopping_item_by_name(self, name: str) -> ShoppingItem | None:
        db = self._load()
        return self._find_by_title(db.shopping_items, name, field_name="name")

    def find_shopping_item_matches_by_name(self, name: str) -> list[ShoppingItem]:
        db = self._load()
        return self._find_matches_by_title(
            [item for item in db.shopping_items if item.status == ItemStatus.active],
            name,
            field_name="name",
        )

    def update_shopping_item(self, item_id: str, payload: ShoppingItemUpdate) -> ShoppingItem:
        db = self._load()
        item = self._get_by_id(db.shopping_items, item_id)
        before = item.model_dump(mode="json")
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(item, key, value)
        item.updated_at = datetime.utcnow()
        self._record_history(
            db,
            action_type="update",
            entity_type="shopping_item",
            entity_id=item.id,
            before=before,
            after=item.model_dump(mode="json"),
        )
        self._save(db)
        return item

    def clear_active_shopping_items(self) -> list[ShoppingItem]:
        db = self._load()
        cleared: list[ShoppingItem] = []
        for item in db.shopping_items:
            if item.status != ItemStatus.active:
                continue
            before = item.model_dump(mode="json")
            item.status = ItemStatus.cancelled
            item.updated_at = datetime.utcnow()
            self._record_history(
                db,
                action_type="update",
                entity_type="shopping_item",
                entity_id=item.id,
                before=before,
                after=item.model_dump(mode="json"),
            )
            cleared.append(item)
        self._save(db)
        return cleared

    def create_bill(self, payload: BillCreate) -> Bill:
        db = self._load()
        bill = Bill(**payload.model_dump())
        db.bills.append(bill)
        self._record_history(
            db,
            action_type="create",
            entity_type="bill",
            entity_id=bill.id,
            after=bill.model_dump(mode="json"),
        )
        self._save(db)
        return bill

    def list_bills(self, status: ItemStatus | None = None, due_before: datetime | None = None) -> list[Bill]:
        db = self._load()
        bills = db.bills
        if status is not None:
            bills = [bill for bill in bills if bill.status == status]
        if due_before is not None:
            bills = [bill for bill in bills if bill.due_at <= due_before]
        return bills

    def find_bill_by_name(self, name: str) -> Bill | None:
        db = self._load()
        return self._find_by_title(db.bills, name, field_name="name")

    def find_bill_matches_by_name(self, name: str) -> list[Bill]:
        db = self._load()
        return self._find_matches_by_title(
            [bill for bill in db.bills if bill.status == ItemStatus.active],
            name,
            field_name="name",
        )

    def update_bill(self, bill_id: str, payload: BillUpdate) -> Bill:
        db = self._load()
        bill = self._get_by_id(db.bills, bill_id)
        before = bill.model_dump(mode="json")
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(bill, key, value)
        bill.updated_at = datetime.utcnow()
        self._record_history(
            db,
            action_type="update",
            entity_type="bill",
            entity_id=bill.id,
            before=before,
            after=bill.model_dump(mode="json"),
        )
        self._save(db)
        return bill

    def mark_bill_paid(self, bill_id: str) -> Bill:
        db = self._load()
        bill = self._get_by_id(db.bills, bill_id)
        before = bill.model_dump(mode="json")
        bill.status = ItemStatus.completed
        bill.updated_at = datetime.utcnow()
        generated_bill_id: str | None = None
        if bill.recurrence:
            next_due_at = advance_datetime(bill.due_at, bill.recurrence, bill.recurrence_interval)
            if next_due_at is not None:
                next_bill = Bill(
                    name=bill.name,
                    amount=bill.amount,
                    currency=bill.currency,
                    due_at=next_due_at,
                    notes=bill.notes,
                    recurrence=bill.recurrence,
                    recurrence_interval=bill.recurrence_interval,
                )
                db.bills.append(next_bill)
                generated_bill_id = next_bill.id
        after = bill.model_dump(mode="json")
        if generated_bill_id is not None:
            after["generated_recurring_id"] = generated_bill_id
        self._record_history(
            db,
            action_type="pay",
            entity_type="bill",
            entity_id=bill.id,
            before=before,
            after=after,
        )
        self._save(db)
        return bill

    def create_event(self, payload: EventCreate) -> Event:
        db = self._load()
        event = Event(**payload.model_dump())
        db.events.append(event)
        self._record_history(
            db,
            action_type="create",
            entity_type="event",
            entity_id=event.id,
            after=event.model_dump(mode="json"),
        )
        self._save(db)
        return event

    def find_duplicate_event(self, payload: EventCreate) -> Event | None:
        db = self._load()
        for event in db.events:
            if event.status != ItemStatus.active:
                continue
            if event.title.strip().lower() != payload.title.strip().lower():
                continue
            if event.starts_at != payload.starts_at or event.ends_at != payload.ends_at:
                continue
            if (event.location or "").strip() != (payload.location or "").strip():
                continue
            return event
        return None

    def list_events(self, starts_before: datetime | None = None) -> list[Event]:
        db = self._load()
        if starts_before is None:
            return db.events
        return [event for event in db.events if event.starts_at <= starts_before]

    def find_event_by_title(self, title: str) -> Event | None:
        db = self._load()
        return self._find_by_title(db.events, title, field_name="title")

    def find_event_matches_by_title(self, title: str) -> list[Event]:
        db = self._load()
        return self._find_matches_by_title(
            [event for event in db.events if event.status == ItemStatus.active],
            title,
            field_name="title",
        )

    def update_event(self, event_id: str, payload: EventUpdate) -> Event:
        db = self._load()
        event = self._get_by_id(db.events, event_id)
        before = event.model_dump(mode="json")
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(event, key, value)
        event.updated_at = datetime.utcnow()
        self._record_history(
            db,
            action_type="update",
            entity_type="event",
            entity_id=event.id,
            before=before,
            after=event.model_dump(mode="json"),
        )
        self._save(db)
        return event

    def get_event(self, event_id: str) -> Event:
        db = self._load()
        return self._get_by_id(db.events, event_id)

    def set_event_google_id(self, event_id: str, google_event_id: str) -> Event:
        db = self._load()
        event = self._get_by_id(db.events, event_id)
        event.google_event_id = google_event_id
        event.updated_at = datetime.utcnow()
        self._save(db)
        return event

    def sync_google_events_window(self, start_at: datetime, end_at: datetime) -> GoogleCalendarPullSyncResponse:
        db = self._load()
        settings = db.settings
        if self.google_calendar_service is None:
            raise HTTPException(status_code=400, detail="Google Calendar service is not configured.")

        status = self.google_calendar_service.auth_status(settings=settings)
        if not status.connected:
            raise HTTPException(status_code=400, detail="Google Calendar is not connected.")

        pulled_events = self.google_calendar_service.list_events_between(
            start_at=start_at,
            end_at=end_at,
            settings=settings,
        )

        created = 0
        updated = 0
        unchanged = 0
        imported_events: list[Event] = []

        for pulled in pulled_events:
            existing = next(
                (event for event in db.events if event.google_event_id and event.google_event_id == pulled.google_event_id),
                None,
            )
            if existing is None:
                imported = Event(
                    title=pulled.title,
                    starts_at=pulled.starts_at,
                    ends_at=pulled.ends_at,
                    location=pulled.location,
                    notes=pulled.notes,
                    google_event_id=pulled.google_event_id,
                    status=ItemStatus.active,
                )
                db.events.append(imported)
                self._record_history(
                    db,
                    action_type="create",
                    entity_type="event",
                    entity_id=imported.id,
                    after=imported.model_dump(mode="json"),
                )
                imported_events.append(imported)
                created += 1
                continue

            changed = (
                existing.title != pulled.title
                or existing.starts_at != pulled.starts_at
                or existing.ends_at != pulled.ends_at
                or existing.location != pulled.location
                or existing.notes != pulled.notes
                or existing.status != ItemStatus.active
            )
            if changed:
                before = existing.model_dump(mode="json")
                existing.title = pulled.title
                existing.starts_at = pulled.starts_at
                existing.ends_at = pulled.ends_at
                existing.location = pulled.location
                existing.notes = pulled.notes
                existing.status = ItemStatus.active
                existing.updated_at = datetime.utcnow()
                self._record_history(
                    db,
                    action_type="update",
                    entity_type="event",
                    entity_id=existing.id,
                    before=before,
                    after=existing.model_dump(mode="json"),
                )
                updated += 1
            else:
                unchanged += 1
            imported_events.append(existing)

        self._save(db)
        return GoogleCalendarPullSyncResponse(
            start_at=start_at,
            end_at=end_at,
            created=created,
            updated=updated,
            unchanged=unchanged,
            imported_events=self._sort_events(imported_events),
        )

    def clear_google_calendar_window(self, start_at: datetime, end_at: datetime) -> GoogleCalendarClearResponse:
        db = self._load()
        settings = db.settings
        if self.google_calendar_service is None:
            raise HTTPException(status_code=400, detail="Google Calendar service is not configured.")

        status = self.google_calendar_service.auth_status(settings=settings)
        if not status.connected:
            raise HTTPException(status_code=400, detail="Google Calendar is not connected.")

        result = self.google_calendar_service.clear_events_between(
            start_at=start_at,
            end_at=end_at,
            settings=settings,
        )

        deleted_id_set = set(result.deleted_google_event_ids)
        for event in db.events:
            if event.google_event_id in deleted_id_set and event.status == ItemStatus.active:
                before = event.model_dump(mode="json")
                event.status = ItemStatus.cancelled
                event.updated_at = datetime.utcnow()
                self._record_history(
                    db,
                    action_type="update",
                    entity_type="event",
                    entity_id=event.id,
                    before=before,
                    after=event.model_dump(mode="json"),
                )

        self._save(db)
        return result

    def clear_google_calendar_for_date(self, target_date: datetime) -> GoogleCalendarClearResponse:
        day_start = datetime.combine(target_date.date(), datetime.min.time())
        day_end = datetime.combine(target_date.date(), datetime.max.time())
        return self.clear_google_calendar_window(start_at=day_start, end_at=day_end)

    def create_reminder(self, payload: ReminderCreate) -> Reminder:
        db = self._load()
        reminder = Reminder(**payload.model_dump())
        db.reminders.append(reminder)
        self._record_history(
            db,
            action_type="create",
            entity_type="reminder",
            entity_id=reminder.id,
            after=reminder.model_dump(mode="json"),
        )
        self._save(db)
        return reminder

    def find_duplicate_reminder(self, payload: ReminderCreate) -> Reminder | None:
        db = self._load()
        for reminder in db.reminders:
            if reminder.status != ReminderStatus.pending:
                continue
            if reminder.title.strip().lower() != payload.title.strip().lower():
                continue
            if reminder.remind_at != payload.remind_at:
                continue
            if (reminder.notes or "").strip() != (payload.notes or "").strip():
                continue
            return reminder
        return None

    def list_reminders(self, status: ReminderStatus | None = None) -> list[Reminder]:
        db = self._load()
        if status is None:
            return db.reminders
        return [reminder for reminder in db.reminders if reminder.status == status]

    def find_reminder_by_title(self, title: str) -> Reminder | None:
        db = self._load()
        return self._find_by_title(db.reminders, title, field_name="title")

    def find_reminder_matches_by_title(self, title: str) -> list[Reminder]:
        db = self._load()
        return self._find_matches_by_title(
            [reminder for reminder in db.reminders if reminder.status == ReminderStatus.pending],
            title,
            field_name="title",
        )

    def update_reminder(self, reminder_id: str, payload: ReminderUpdate) -> Reminder:
        db = self._load()
        reminder = self._get_by_id(db.reminders, reminder_id)
        before = reminder.model_dump(mode="json")
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(reminder, key, value)
        reminder.updated_at = datetime.utcnow()
        if reminder.status == ReminderStatus.sent and reminder.sent_at is None:
            reminder.sent_at = datetime.utcnow()
        self._record_history(
            db,
            action_type="update",
            entity_type="reminder",
            entity_id=reminder.id,
            before=before,
            after=reminder.model_dump(mode="json"),
        )
        self._save(db)
        return reminder

    def get_reminder(self, reminder_id: str) -> Reminder:
        db = self._load()
        return self._get_by_id(db.reminders, reminder_id)

    def set_reminder_google_id(self, reminder_id: str, google_event_id: str) -> Reminder:
        db = self._load()
        reminder = self._get_by_id(db.reminders, reminder_id)
        reminder.google_event_id = google_event_id
        reminder.updated_at = datetime.utcnow()
        self._save(db)
        return reminder

    def get_due_reminders(self) -> list[Reminder]:
        db = self._load()
        return find_due_reminders(db.reminders)

    def mark_due_reminders_sent(self) -> list[Reminder]:
        db = self._load()
        due = find_due_reminders(db.reminders)
        for reminder in due:
            reminder.status = ReminderStatus.sent
            reminder.sent_at = datetime.utcnow()
            reminder.updated_at = datetime.utcnow()
            if reminder.recurrence:
                next_remind_at = advance_datetime(
                    reminder.remind_at,
                    reminder.recurrence,
                    reminder.recurrence_interval,
                )
                if next_remind_at is not None:
                    db.reminders.append(
                        Reminder(
                            title=reminder.title,
                            remind_at=next_remind_at,
                            channel=reminder.channel,
                            related_type=reminder.related_type,
                            related_id=reminder.related_id,
                            notes=reminder.notes,
                            recurrence=reminder.recurrence,
                            recurrence_interval=reminder.recurrence_interval,
                        )
                    )
        self._save(db)
        return due

    def undo_last_action(self) -> UndoActionResult:
        db = self._load()
        if not db.action_history:
            return UndoActionResult(undone=False, message="There is no recent action to undo.")

        entry = db.action_history.pop()
        collection = self._entity_collection(db, entry.entity_type)

        if entry.action_type == "create":
            self._remove_entity_by_id(collection, entry.entity_id)
            self._save(db)
            return UndoActionResult(
                undone=True,
                action_type=entry.action_type,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                message=f"Undid the creation of that {entry.entity_type.replace('_', ' ')}.",
                data=entry.after,
            )

        if entry.before is not None:
            restored = self._restore_entity(entry.entity_type, entry.before)
            self._replace_or_append_entity(collection, restored)
            generated_id = entry.after.get("generated_recurring_id") if entry.after else None
            if generated_id:
                self._remove_entity_by_id(collection, generated_id)
            self._save(db)
            return UndoActionResult(
                undone=True,
                action_type=entry.action_type,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                message=f"Undid the last change to that {entry.entity_type.replace('_', ' ')}.",
                data=entry.before,
            )

        return UndoActionResult(
            undone=False,
            action_type=entry.action_type,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            message="I found a recent action, but it could not be safely undone.",
        )

    def get_summary(self, now: datetime | None = None) -> AssistantSummary:
        db = self._load()
        current_time = now or self.current_time(db.settings)
        today_end = datetime.combine(current_time.date(), datetime.max.time())
        week_end = current_time + timedelta(days=7)
        active_tasks = [task for task in db.tasks if task.status == ItemStatus.active]
        priority_tasks = self._sort_tasks_for_plan(
            [task for task in active_tasks if task.due_at is None or task.due_at <= week_end],
            reference_time=current_time,
        )[:3]
        due_reminders = find_due_reminders(db.reminders, now=current_time)
        local_upcoming_events = [
            event
            for event in db.events
            if event.status == ItemStatus.active and current_time <= event.starts_at <= week_end
        ]
        google_upcoming_events = self._google_events_between(
            start_at=current_time,
            end_at=week_end,
            settings=db.settings,
            local_events=local_upcoming_events,
        )
        upcoming_events = self._sort_events(local_upcoming_events + google_upcoming_events)
        due_bills_this_week = [
            bill
            for bill in db.bills
            if bill.status == ItemStatus.active and current_time <= bill.due_at <= week_end
        ]
        overdue_bills = [
            bill
            for bill in db.bills
            if bill.status == ItemStatus.active and bill.due_at < current_time
        ]
        return AssistantSummary(
            generated_at=current_time,
            overdue_tasks=[
                task
                for task in active_tasks
                if task.due_at is not None and task.due_at < current_time
            ],
            due_tasks_today=[
                task
                for task in active_tasks
                if task.due_at is not None and current_time <= task.due_at <= today_end
            ],
            overdue_bills=overdue_bills,
            due_bills_this_week=due_bills_this_week,
            due_reminders=due_reminders,
            upcoming_events=upcoming_events,
            priority_tasks=priority_tasks,
            best_next_action=self._build_best_next_action(
                current_time,
                tasks=active_tasks,
                bills=overdue_bills + due_bills_this_week,
                reminders=due_reminders,
                events=upcoming_events,
            ),
        )

    def get_agenda_for_date(self, target_date: datetime) -> DailyAgenda:
        db = self._load()
        settings = db.settings
        day_start = datetime.combine(target_date.date(), datetime.min.time())
        day_end = datetime.combine(target_date.date(), datetime.max.time())
        current_local = self.current_time(settings)
        reference_time = current_local if target_date.date() == current_local.date() else day_start
        tasks = [
            task
            for task in db.tasks
            if task.status == ItemStatus.active and task.due_at is not None and day_start <= task.due_at <= day_end
        ]
        bills = [
            bill
            for bill in db.bills
            if bill.status == ItemStatus.active and day_start <= bill.due_at <= day_end
        ]
        reminders = [
            reminder
            for reminder in db.reminders
            if reminder.status == ReminderStatus.pending and day_start <= reminder.remind_at <= day_end
        ]
        local_events = [
            event
            for event in db.events
            if event.status == ItemStatus.active and day_start <= event.starts_at <= day_end
        ]
        google_events = self._google_events_between(
            start_at=day_start,
            end_at=day_end,
            settings=settings,
            local_events=local_events,
        )
        events = self._sort_events(local_events + google_events)
        priority_tasks = self._sort_tasks_for_plan(tasks, reference_time=reference_time)[:3]
        conflicts = self._detect_agenda_conflicts(tasks, bills, reminders, events, settings)
        suggested_plan = self._build_suggested_plan(target_date, tasks, bills, reminders, events, settings)
        return DailyAgenda(
            date=target_date.date().isoformat(),
            tasks=tasks,
            bills=bills,
            reminders=reminders,
            events=events,
            priority_tasks=priority_tasks,
            conflicts=conflicts,
            suggested_plan=suggested_plan,
            best_next_action=self._build_best_next_action(reference_time, tasks, bills, reminders, events),
        )

    def get_morning_briefing(self, now: datetime | None = None) -> BriefingResponse:
        current_time = now or self.current_time(self.get_settings())
        agenda = self.get_agenda_for_date(current_time)
        summary = self.get_summary(now=current_time)
        overview = [
            f"You have {len(agenda.events)} event(s), {len(agenda.reminders)} reminder(s), and {len(agenda.tasks)} task(s) scheduled today.",
        ]
        if summary.best_next_action:
            overview.append(summary.best_next_action)
        warnings = agenda.conflicts[:4]
        if summary.overdue_tasks:
            warnings.append(f"You still have {len(summary.overdue_tasks)} overdue task(s).")
        if summary.overdue_bills:
            warnings.append(f"You still have {len(summary.overdue_bills)} overdue bill(s).")
        next_steps = agenda.suggested_plan[:4]
        return BriefingResponse(
            kind="morning",
            date=current_time.date().isoformat(),
            overview=overview,
            warnings=warnings[:5],
            next_steps=next_steps,
        )

    def get_evening_briefing(self, now: datetime | None = None) -> BriefingResponse:
        db = self._load()
        current_time = now or self.current_time(db.settings)
        today_end = datetime.combine(current_time.date(), datetime.max.time())
        tomorrow = current_time + timedelta(days=1)
        tomorrow_agenda = self.get_agenda_for_date(tomorrow)
        unfinished_tasks = [
            task
            for task in db.tasks
            if task.status == ItemStatus.active and task.due_at is not None and task.due_at <= today_end
        ]
        completed_today = [
            task
            for task in db.tasks
            if task.status == ItemStatus.completed and task.updated_at.date() == current_time.date()
        ]
        overview = [
            f"You completed {len(completed_today)} task(s) today.",
            f"You have {len(unfinished_tasks)} unfinished task(s) still open from today or earlier.",
            f"Tomorrow currently has {len(tomorrow_agenda.events)} event(s), {len(tomorrow_agenda.reminders)} reminder(s), and {len(tomorrow_agenda.tasks)} task(s).",
        ]
        warnings = []
        if unfinished_tasks:
            warnings.append(f"Wrap up or reschedule '{unfinished_tasks[0].title}' before ending the day.")
        warnings.extend(tomorrow_agenda.conflicts[:3])
        next_steps = tomorrow_agenda.suggested_plan[:4]
        return BriefingResponse(
            kind="evening",
            date=current_time.date().isoformat(),
            overview=overview,
            warnings=warnings[:5],
            next_steps=next_steps,
        )

    def get_tomorrow_briefing(self, now: datetime | None = None) -> BriefingResponse:
        current_time = now or self.current_time(self.get_settings())
        tomorrow = current_time + timedelta(days=1)
        agenda = self.get_agenda_for_date(tomorrow)
        overview = [
            f"Tomorrow has {len(agenda.events)} event(s), {len(agenda.reminders)} reminder(s), {len(agenda.tasks)} task(s), and {len(agenda.bills)} bill(s).",
        ]
        if agenda.best_next_action:
            overview.append(agenda.best_next_action)
        return BriefingResponse(
            kind="tomorrow",
            date=tomorrow.date().isoformat(),
            overview=overview,
            warnings=agenda.conflicts[:5],
            next_steps=agenda.suggested_plan[:4],
        )
