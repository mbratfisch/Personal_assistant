from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id() -> str:
    return str(uuid4())


class ItemStatus(str, Enum):
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class ReminderStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    dismissed = "dismissed"


class RecurrenceFrequency(str, Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    yearly = "yearly"


class GoogleEventType(str, Enum):
    default = "default"
    birthday = "birthday"
    focus_time = "focusTime"
    out_of_office = "outOfOffice"
    working_location = "workingLocation"


class NoteCreate(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)


class Note(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TaskCreate(BaseModel):
    title: str
    details: str = ""
    due_at: Optional[datetime] = None
    priority: str = "medium"
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: int = 1


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    details: Optional[str] = None
    due_at: Optional[datetime] = None
    priority: Optional[str] = None
    status: Optional[ItemStatus] = None
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: Optional[int] = None


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    details: str = ""
    due_at: Optional[datetime] = None
    priority: str = "medium"
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: int = 1
    google_event_id: Optional[str] = None
    status: ItemStatus = ItemStatus.active
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ShoppingItemCreate(BaseModel):
    name: str
    quantity: str = "1"
    notes: str = ""


class ShoppingItemUpdate(BaseModel):
    quantity: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[ItemStatus] = None


class ShoppingItem(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    quantity: str = "1"
    notes: str = ""
    status: ItemStatus = ItemStatus.active
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BillCreate(BaseModel):
    name: str
    amount: float
    currency: str = "USD"
    due_at: datetime
    notes: str = ""
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: int = 1


class BillUpdate(BaseModel):
    name: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    due_at: Optional[datetime] = None
    notes: Optional[str] = None
    status: Optional[ItemStatus] = None
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: Optional[int] = None


class Bill(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    amount: float
    currency: str = "USD"
    due_at: datetime
    notes: str = ""
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: int = 1
    status: ItemStatus = ItemStatus.active
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EventCreate(BaseModel):
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    location: str = ""
    notes: str = ""


class EventUpdate(BaseModel):
    title: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[ItemStatus] = None


class Event(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    location: str = ""
    notes: str = ""
    google_event_id: Optional[str] = None
    status: ItemStatus = ItemStatus.active
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ReminderCreate(BaseModel):
    title: str
    remind_at: datetime
    channel: str = "in_app"
    related_type: Optional[str] = None
    related_id: Optional[str] = None
    notes: str = ""
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: int = 1


class ReminderUpdate(BaseModel):
    title: Optional[str] = None
    remind_at: Optional[datetime] = None
    channel: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[ReminderStatus] = None
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: Optional[int] = None


class Reminder(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    remind_at: datetime
    channel: str = "in_app"
    related_type: Optional[str] = None
    related_id: Optional[str] = None
    notes: str = ""
    recurrence: Optional[RecurrenceFrequency] = None
    recurrence_interval: int = 1
    google_event_id: Optional[str] = None
    status: ReminderStatus = ReminderStatus.pending
    sent_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSettings(BaseModel):
    full_name: Optional[str] = None
    gmail_address: Optional[str] = None
    google_calendar_id: Optional[str] = None
    timezone: str = "America/Chicago"
    workday_start_hour: int = 9
    workday_end_hour: int = 17
    preferred_reminder_lead_minutes: int = 30
    default_task_priority: str = "medium"
    home_location: Optional[str] = None
    work_location: Optional[str] = None
    google_calendar_offer_seen: bool = False
    google_calendar_offer_declined: bool = False
    google_calendar_offer_updated_at: Optional[datetime] = None
    google_calendar_connected_profile_key: Optional[str] = None


class UserSettingsUpdate(BaseModel):
    full_name: Optional[str] = None
    gmail_address: Optional[str] = None
    google_calendar_id: Optional[str] = None
    timezone: Optional[str] = None
    workday_start_hour: Optional[int] = None
    workday_end_hour: Optional[int] = None
    preferred_reminder_lead_minutes: Optional[int] = None
    default_task_priority: Optional[str] = None
    home_location: Optional[str] = None
    work_location: Optional[str] = None
    google_calendar_offer_seen: Optional[bool] = None
    google_calendar_offer_declined: Optional[bool] = None
    google_calendar_offer_updated_at: Optional[datetime] = None
    google_calendar_connected_profile_key: Optional[str] = None


class GoogleCalendarAuthStartResponse(BaseModel):
    authorization_url: str = Field(
        description="Open this exact URL in the browser to authorize Google Calendar access. GPT should print this full URL directly to the user as a clickable link."
    )
    authorization_markdown_link: str = Field(
        description="A ready-to-display Markdown link for the authorization URL. GPT should prefer showing this or the full authorization_url directly."
    )
    display_message: str = Field(
        description="A user-facing message that already includes the real authorization link. GPT may print this message directly."
    )
    state: str = Field(description="OAuth state token for the current authorization session.")
    profile_key: str = Field(description="Calendar profile key associated with this authorization flow.")


class GoogleCalendarAuthStatus(BaseModel):
    connected: bool
    calendar_id: str = "primary"
    gmail_address: Optional[str] = None
    redirect_uri: Optional[str] = None
    has_refresh_token: bool = False
    profile_key: str = "primary"


class GoogleCalendarConnection(BaseModel):
    profile_key: str
    calendar_id: str
    gmail_address: Optional[str] = None
    token_path: str
    connected: bool = False
    has_refresh_token: bool = False
    last_used_at: datetime = Field(default_factory=datetime.utcnow)


class GoogleCalendarSetupStatus(BaseModel):
    connected: bool
    profile_key: str
    gmail_address: Optional[str] = None
    calendar_id: str = "primary"
    should_offer_setup: bool = False
    offer_seen: bool = False
    offer_declined: bool = False
    message: str


class GoogleCalendarConnectInfo(BaseModel):
    connect_url: str = Field(description="Exact absolute browser URL for starting the Google Calendar connection flow. GPT should copy this value exactly with no edits.")
    message: str


class TelegramLinkStartResponse(BaseModel):
    configured: bool = False
    bot_username: Optional[str] = None
    deep_link_url: Optional[str] = None
    link_code: Optional[str] = None
    expires_at: Optional[datetime] = None
    message: str


class TelegramConnectionStatus(BaseModel):
    configured: bool = False
    connected: bool = False
    bot_username: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_username: Optional[str] = None
    telegram_first_name: Optional[str] = None
    linked_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    message: str


class TelegramTestMessageResponse(BaseModel):
    sent: bool = False
    telegram_chat_id: Optional[str] = None
    message: str


class GoogleCalendarEventCreate(BaseModel):
    summary: str
    start_at: datetime
    end_at: Optional[datetime] = None
    event_type: GoogleEventType = GoogleEventType.default
    description: str = ""
    location: str = ""
    attendees: list[str] = Field(default_factory=list)
    reminder_minutes: list[int] = Field(default_factory=lambda: [30])
    send_updates: str = "none"
    all_day: bool = False
    recurrence: list[str] = Field(default_factory=list)
    visibility: Optional[str] = None
    transparency: Optional[str] = None
    birthday_label: Optional[str] = None
    focus_time_chat_status: Optional[str] = None
    focus_time_decline_mode: Optional[str] = None
    out_of_office_auto_decline_mode: Optional[str] = None
    out_of_office_decline_message: Optional[str] = None
    working_location_type: Optional[str] = None
    working_location_label: Optional[str] = None
    working_location_home_office: bool = False


class GoogleCalendarEventResult(BaseModel):
    calendar_id: str
    google_event_id: str
    html_link: str
    status: str


class GoogleCalendarEventSyncResponse(BaseModel):
    local_event: Event
    calendar: GoogleCalendarEventResult


class GoogleCalendarReminderSyncResponse(BaseModel):
    local_reminder: Reminder
    calendar: GoogleCalendarEventResult


class GoogleCalendarTaskSyncResponse(BaseModel):
    local_task: Task
    calendar: GoogleCalendarEventResult


class GoogleCalendarPullSyncResponse(BaseModel):
    start_at: datetime
    end_at: datetime
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    imported_events: list[Event] = Field(default_factory=list)


class GoogleCalendarClearResponse(BaseModel):
    start_at: datetime
    end_at: datetime
    deleted: int = 0
    skipped: int = 0
    deleted_google_event_ids: list[str] = Field(default_factory=list)
    deleted_titles: list[str] = Field(default_factory=list)


class AuthSessionStatus(BaseModel):
    user_id: str
    email: Optional[str] = None
    is_authenticated: bool = False
    is_admin: bool = False
    auth_mode: str
    invitation_required: bool = False
    invitation_redeemed: bool = False
    invitation_message: Optional[str] = None


class InvitationRedeemRequest(BaseModel):
    code: str


class InvitationStatus(BaseModel):
    invitation_required: bool
    invitation_redeemed: bool
    message: str
    invited_at: Optional[datetime] = None
    redeemed_code: Optional[str] = None


class AdminInvitationCode(BaseModel):
    code: str
    active: bool = True
    created_at: Optional[datetime] = None
    created_by_user_id: Optional[str] = None
    created_by_email: Optional[str] = None
    max_uses: int = 1
    redeemed_count: int = 0
    redeemed_by_users: list[str] = Field(default_factory=list)


class AdminInvitationCreateRequest(BaseModel):
    code: Optional[str] = None
    max_uses: int = 1
    active: bool = True


class AdminInvitationUpdateRequest(BaseModel):
    active: Optional[bool] = None
    max_uses: Optional[int] = None


class AdminUserSummary(BaseModel):
    user_id: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    invitation_redeemed: bool = False
    redeemed_code: Optional[str] = None
    invited_at: Optional[datetime] = None
    google_calendar_connected: bool = False


class AdminOverview(BaseModel):
    total_users: int = 0
    invited_users: int = 0
    google_calendar_connected_users: int = 0
    total_invitation_codes: int = 0
    active_invitation_codes: int = 0
    redeemed_invitation_codes: int = 0


class AssistantDiagnosticEntry(BaseModel):
    id: str = Field(default_factory=new_id)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    input_text: str
    response_action: str
    message: str
    status: str = "success"
    trace_source: Optional[str] = None
    trace_reason: Optional[str] = None
    llm_action: Optional[str] = None
    parser_action: Optional[str] = None


class AdminAssistantDiagnostic(BaseModel):
    user_id: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    occurred_at: datetime
    input_text: str
    response_action: str
    message: str
    status: str = "success"
    trace_source: Optional[str] = None
    trace_reason: Optional[str] = None
    llm_action: Optional[str] = None
    parser_action: Optional[str] = None


class AssistantCommandRequest(BaseModel):
    text: str
    now: Optional[datetime] = None


class AssistantCommandResponse(BaseModel):
    action: str
    message: str
    created_type: Optional[str] = None
    created_id: Optional[str] = None
    data: Optional[dict] = None


class UndoActionResult(BaseModel):
    undone: bool
    action_type: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    message: str
    data: Optional[dict] = None


class AssistantSummary(BaseModel):
    generated_at: datetime
    overdue_tasks: list[Task] = Field(default_factory=list)
    due_tasks_today: list[Task] = Field(default_factory=list)
    due_bills_this_week: list[Bill] = Field(default_factory=list)
    overdue_bills: list[Bill] = Field(default_factory=list)
    due_reminders: list[Reminder] = Field(default_factory=list)
    upcoming_events: list[Event] = Field(default_factory=list)
    priority_tasks: list[Task] = Field(default_factory=list)
    best_next_action: Optional[str] = None


class DailyAgenda(BaseModel):
    date: str
    tasks: list[Task] = Field(default_factory=list)
    bills: list[Bill] = Field(default_factory=list)
    reminders: list[Reminder] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    priority_tasks: list[Task] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    suggested_plan: list[str] = Field(default_factory=list)
    best_next_action: Optional[str] = None


class BriefingResponse(BaseModel):
    kind: str
    date: str
    overview: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class ActionHistoryEntry(BaseModel):
    id: str = Field(default_factory=new_id)
    action_type: str
    entity_type: str
    entity_id: str
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PendingAssistantConfirmation(BaseModel):
    question: str
    proposed_command: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DatabaseModel(BaseModel):
    settings: UserSettings = Field(default_factory=UserSettings)
    notes: list[Note] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    shopping_items: list[ShoppingItem] = Field(default_factory=list)
    bills: list[Bill] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    reminders: list[Reminder] = Field(default_factory=list)
    action_history: list[ActionHistoryEntry] = Field(default_factory=list)
    pending_confirmation: Optional[PendingAssistantConfirmation] = None
    assistant_diagnostics: list[AssistantDiagnosticEntry] = Field(default_factory=list)
