from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.assistant_models import (
    AdminAssistantDiagnostic,
    AdminInvitationCode,
    AdminInvitationCreateRequest,
    AdminInvitationUpdateRequest,
    AdminOverview,
    AdminUserSummary,
    AuthSessionStatus,
    AssistantCommandRequest,
    AssistantCommandResponse,
    AssistantSummary,
    Bill,
    BillCreate,
    BillUpdate,
    BriefingResponse,
    DailyAgenda,
    Event,
    EventCreate,
    EventUpdate,
    GoogleCalendarAuthStartResponse,
    GoogleCalendarAuthStatus,
    GoogleCalendarConnection,
    GoogleCalendarConnectInfo,
    GoogleCalendarClearResponse,
    GoogleCalendarSetupStatus,
    GoogleCalendarEventCreate,
    GoogleCalendarEventResult,
    GoogleCalendarPullSyncResponse,
    GoogleCalendarEventSyncResponse,
    GoogleCalendarReminderSyncResponse,
    GoogleCalendarTaskSyncResponse,
    InvitationRedeemRequest,
    InvitationStatus,
    ItemStatus,
    Note,
    NoteCreate,
    Reminder,
    ReminderCreate,
    ReminderStatus,
    ReminderUpdate,
    ShoppingItem,
    ShoppingItemCreate,
    ShoppingItemUpdate,
    Task,
    TaskCreate,
    TelegramTestMessageResponse,
    TelegramConnectionStatus,
    TelegramLinkStartResponse,
    TaskUpdate,
    UndoActionResult,
    UserSettings,
    UserSettingsUpdate,
)
from src.auth import (
    auth_mode,
    create_admin_invitation_code,
    delete_admin_invitation_code,
    enforce_admin_access,
    enforce_invitation_access,
    get_admin_overview,
    get_invitation_status,
    is_admin_user,
    list_admin_assistant_diagnostics,
    list_admin_invitation_codes,
    list_admin_users,
    redeem_invitation_code,
    resolve_auth_context,
    update_admin_invitation_code,
)
from src.google_calendar import GoogleCalendarService
from src.llm_assistant import handle_command_with_llm
from src.repository import create_repository
from src.request_scope import GoogleCalendarProxy, RequestScopedState, ServiceProxy, reset_request_state, set_request_state
from src.service import AssistantService
from src.telegram_integration import (
    disconnect_telegram,
    handle_telegram_update,
    send_telegram_test_message,
    start_telegram_link,
    telegram_connection_status,
    telegram_webhook_secret,
)

app = FastAPI(
    title="Personal Assistant API",
    description=(
        "A lightweight backend for a GPT-powered personal assistant. "
        "For most natural conversational requests, prefer POST /assistant/command. "
        "Use GET /summary for overview questions like what is due, what is overdue, or what is coming up. "
        "Use direct entity endpoints when precise structured control is needed."
    ),
    version="0.1.0",
    openapi_tags=[
        {"name": "assistant", "description": "Primary GPT-facing endpoints. Prefer these first for conversational use."},
        {"name": "settings", "description": "User profile and configuration values such as name, timezone, and calendar settings."},
        {"name": "notes", "description": "Long-form saved memory and written notes."},
        {"name": "tasks", "description": "Actionable to-dos, including completion and recurring task roll-forward."},
        {"name": "shopping", "description": "Shopping list items and status updates."},
        {"name": "bills", "description": "Bills to pay, due dates, and payment tracking."},
        {"name": "events", "description": "Scheduled commitments such as meetings and appointments."},
        {"name": "reminders", "description": "Reminder records and due reminder processing."},
        {"name": "google-calendar", "description": "Optional Google Calendar integration endpoints."},
        {"name": "telegram", "description": "Telegram bot linking and webhook endpoints."},
        {"name": "admin", "description": "Administrator endpoints for rollout control, users, and invitation management."},
        {"name": "system", "description": "Local utility and app shell endpoints."},
    ],
)


def _allowed_origins() -> list[str]:
    raw = os.getenv(
        "APP_CORS_ALLOWED_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _frontend_app_url() -> str:
    return (os.getenv("APP_FRONTEND_URL") or "http://localhost:8080").strip().rstrip("/")


def _frontend_calendar_redirect(status: str, message: str | None = None) -> str:
    query = {"googleCalendar": status}
    if message:
        query["message"] = message
    return f"{_frontend_app_url()}/calendar?{urlencode(query, quote_via=quote_plus)}"


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def build_assistant_service(user_id: str | None) -> AssistantService:
    google_service = GoogleCalendarService(user_id=user_id)
    return AssistantService(create_repository(user_id=user_id), google_calendar_service=google_service)


bootstrap_google_calendar = GoogleCalendarService()
google_calendar = GoogleCalendarProxy()
service = ServiceProxy()
web_dir = Path(__file__).resolve().parent / "web"

if web_dir.exists():
    app.mount("/static", StaticFiles(directory=web_dir), name="static")


@app.middleware("http")
async def attach_request_scoped_services(request: Request, call_next):
    try:
        auth = resolve_auth_context(request)
        enforce_invitation_access(request, auth)
        scoped_service = build_assistant_service(auth.user_id)
        request.state.auth = auth
        token = set_request_state(
            RequestScopedState(
                auth=auth,
                service=scoped_service,
                google_calendar=scoped_service.google_calendar_service,
            )
        )
        try:
            return await call_next(request)
        finally:
            reset_request_state(token)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/", tags=["system"], summary="Health check", description="Simple health check endpoint to confirm the API is running.")
def root() -> dict[str, str]:
    return {"message": "Personal Assistant API is running."}


@app.get("/auth/me", response_model=AuthSessionStatus, tags=["system"], summary="Get current auth session")
def auth_me(request: Request) -> AuthSessionStatus:
    auth = request.state.auth
    invitation = get_invitation_status(auth.user_id) if auth.is_authenticated else {
        "invitation_required": False,
        "invitation_redeemed": False,
        "message": None,
    }
    return AuthSessionStatus(
        user_id=auth.user_id,
        email=auth.email,
        is_authenticated=auth.is_authenticated,
        is_admin=is_admin_user(auth),
        auth_mode=auth_mode(),
        invitation_required=invitation["invitation_required"],
        invitation_redeemed=invitation["invitation_redeemed"],
        invitation_message=invitation["message"],
    )


@app.get("/auth/invitation", response_model=InvitationStatus, tags=["system"], summary="Get invitation access status")
def auth_invitation_status(request: Request) -> InvitationStatus:
    auth = request.state.auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return InvitationStatus(**get_invitation_status(auth.user_id))


@app.post("/auth/invitation/redeem", response_model=InvitationStatus, tags=["system"], summary="Redeem invitation code")
def auth_invitation_redeem(payload: InvitationRedeemRequest, request: Request) -> InvitationStatus:
    auth = request.state.auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return InvitationStatus(**redeem_invitation_code(auth.user_id, auth.email, payload.code))


@app.get(
    "/integrations/telegram/status",
    response_model=TelegramConnectionStatus,
    tags=["telegram"],
    summary="Get Telegram connection status",
)
def telegram_status(request: Request) -> TelegramConnectionStatus:
    auth = request.state.auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return telegram_connection_status(auth.user_id)


@app.post(
    "/integrations/telegram/link",
    response_model=TelegramLinkStartResponse,
    tags=["telegram"],
    summary="Start Telegram link flow",
)
def telegram_link(request: Request) -> TelegramLinkStartResponse:
    auth = request.state.auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return start_telegram_link(auth.user_id, auth.email)


@app.delete(
    "/integrations/telegram/status",
    response_model=TelegramConnectionStatus,
    tags=["telegram"],
    summary="Disconnect Telegram",
)
def telegram_disconnect(request: Request) -> TelegramConnectionStatus:
    auth = request.state.auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return disconnect_telegram(auth.user_id)


@app.post(
    "/integrations/telegram/test-message",
    response_model=TelegramTestMessageResponse,
    tags=["telegram"],
    summary="Send Telegram test message",
)
def telegram_test_message(request: Request) -> TelegramTestMessageResponse:
    auth = request.state.auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return send_telegram_test_message(auth.user_id)


@app.post(
    "/integrations/telegram/webhook",
    tags=["telegram"],
    summary="Telegram webhook endpoint",
)
async def telegram_webhook(request: Request) -> dict[str, object]:
    expected_secret = telegram_webhook_secret()
    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if expected_secret and received_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")
    update = await request.json()
    return handle_telegram_update(update)


@app.get("/admin/overview", response_model=AdminOverview, tags=["admin"], summary="Get admin overview metrics")
def admin_overview(request: Request) -> AdminOverview:
    enforce_admin_access(request.state.auth)
    return AdminOverview(**get_admin_overview())


@app.get("/admin/users", response_model=list[AdminUserSummary], tags=["admin"], summary="List users")
def admin_users(request: Request) -> list[AdminUserSummary]:
    enforce_admin_access(request.state.auth)
    return [AdminUserSummary(**item) for item in list_admin_users()]


@app.get("/admin/assistant-diagnostics", response_model=list[AdminAssistantDiagnostic], tags=["admin"], summary="List recent assistant diagnostics")
def admin_assistant_diagnostics(request: Request, limit: int = 25) -> list[AdminAssistantDiagnostic]:
    enforce_admin_access(request.state.auth)
    limit = max(1, min(limit, 100))
    return [AdminAssistantDiagnostic(**item) for item in list_admin_assistant_diagnostics(limit=limit)]


@app.get("/admin/invitations", response_model=list[AdminInvitationCode], tags=["admin"], summary="List invitation codes")
def admin_invitations(request: Request) -> list[AdminInvitationCode]:
    enforce_admin_access(request.state.auth)
    return [AdminInvitationCode(**item) for item in list_admin_invitation_codes()]


@app.post("/admin/invitations", response_model=AdminInvitationCode, tags=["admin"], summary="Create invitation code")
def admin_create_invitation(payload: AdminInvitationCreateRequest, request: Request) -> AdminInvitationCode:
    auth = request.state.auth
    enforce_admin_access(auth)
    created = create_admin_invitation_code(
        created_by_user_id=auth.user_id,
        created_by_email=auth.email,
        code=payload.code,
        max_uses=payload.max_uses,
        active=payload.active,
    )
    return AdminInvitationCode(**created)


@app.patch("/admin/invitations/{code}", response_model=AdminInvitationCode, tags=["admin"], summary="Update invitation code")
def admin_update_invitation(code: str, payload: AdminInvitationUpdateRequest, request: Request) -> AdminInvitationCode:
    enforce_admin_access(request.state.auth)
    updated = update_admin_invitation_code(code, payload.model_dump(exclude_unset=True))
    return AdminInvitationCode(**updated)


@app.delete("/admin/invitations/{code}", tags=["admin"], summary="Delete invitation code")
def admin_delete_invitation(code: str, request: Request) -> dict[str, str]:
    enforce_admin_access(request.state.auth)
    delete_admin_invitation_code(code)
    return {"status": "deleted", "code": code}


@app.get("/app", response_class=HTMLResponse, tags=["system"], summary="Open optional local web app")
def app_shell() -> str:
    return (web_dir / "index.html").read_text(encoding="utf-8")


@app.get(
    "/connect-google-calendar",
    response_class=HTMLResponse,
    tags=["google-calendar"],
    summary="User-friendly Google Calendar connect page",
    description="A simple browser page for non-technical users that starts the Google Calendar connection flow.",
)
def google_calendar_connect_page() -> str:
    return """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Connect Google Calendar</title>
    <style>
      body {
        font-family: Arial, sans-serif;
        margin: 0;
        background: linear-gradient(180deg, #f6f7fb 0%, #eef2f7 100%);
        color: #1f2937;
      }
      .wrap {
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 24px;
      }
      .card {
        max-width: 560px;
        width: 100%;
        background: white;
        border-radius: 18px;
        box-shadow: 0 20px 60px rgba(15, 23, 42, 0.12);
        padding: 32px;
      }
      h1 {
        margin-top: 0;
        font-size: 32px;
        line-height: 1.15;
      }
      p {
        font-size: 16px;
        line-height: 1.6;
      }
      .button {
        display: inline-block;
        margin-top: 12px;
        background: #2563eb;
        color: white;
        text-decoration: none;
        padding: 14px 20px;
        border-radius: 12px;
        font-weight: 700;
      }
      .muted {
        margin-top: 18px;
        color: #6b7280;
        font-size: 14px;
      }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="card">
        <h1>Connect Google Calendar</h1>
        <p>To finish connecting your Google Calendar, continue to Google and approve access.</p>
        <a class="button" href="/integrations/google-calendar/connect">Continue With Google</a>
        <p class="muted">If you do not want to connect right now, you can close this page and do it later.</p>
      </div>
    </div>
  </body>
</html>
"""


@app.post("/notes", response_model=Note, tags=["notes"], summary="Create note")
def create_note(payload: NoteCreate) -> Note:
    return service.create_note(payload)


@app.get("/settings", response_model=UserSettings, tags=["settings"], summary="Get user settings")
def get_settings() -> UserSettings:
    return service.get_settings()


@app.patch("/settings", response_model=UserSettings, tags=["settings"], summary="Update user settings")
def update_settings(payload: UserSettingsUpdate) -> UserSettings:
    return service.update_settings(payload)


@app.get("/notes", response_model=list[Note], tags=["notes"], summary="List notes")
def list_notes(query: str | None = None) -> list[Note]:
    return service.list_notes(query=query)


@app.delete("/notes/{note_id}", tags=["notes"], summary="Delete note")
def delete_note(note_id: str) -> dict[str, str]:
    service.delete_note(note_id)
    return {"status": "deleted", "note_id": note_id}


@app.post("/tasks", response_model=Task, tags=["tasks"], summary="Create task")
def create_task(payload: TaskCreate) -> Task:
    return service.create_task(payload)


@app.get("/tasks", response_model=list[Task], tags=["tasks"], summary="List tasks")
def list_tasks(status: ItemStatus | None = None) -> list[Task]:
    return service.list_tasks(status=status)


@app.patch("/tasks/{task_id}", response_model=Task, tags=["tasks"], summary="Update task")
def update_task(task_id: str, payload: TaskUpdate) -> Task:
    return service.update_task(task_id, payload)


@app.post("/tasks/{task_id}/complete", response_model=Task, tags=["tasks"], summary="Complete task")
def complete_task(task_id: str) -> Task:
    return service.complete_task(task_id)


@app.delete("/tasks/{task_id}", response_model=Task, tags=["tasks"], summary="Cancel task")
def delete_task(task_id: str) -> Task:
    return service.update_task(task_id, TaskUpdate(status=ItemStatus.cancelled))


@app.post(
    "/integrations/google-calendar/sync/tasks/{task_id}",
    response_model=GoogleCalendarEventResult,
    tags=["google-calendar"],
    summary="Sync task to Google Calendar",
)
def google_calendar_sync_task(task_id: str) -> GoogleCalendarEventResult:
    task = service.get_task(task_id)
    result = google_calendar.sync_local_task(task, settings=service.get_settings())
    service.set_task_google_id(task_id, result.google_event_id)
    return result


@app.post("/shopping-items", response_model=ShoppingItem, tags=["shopping"], summary="Create shopping item")
def create_shopping_item(payload: ShoppingItemCreate) -> ShoppingItem:
    return service.create_shopping_item(payload)


@app.get("/shopping-items", response_model=list[ShoppingItem], tags=["shopping"], summary="List shopping items")
def list_shopping_items(status: ItemStatus | None = None) -> list[ShoppingItem]:
    return service.list_shopping_items(status=status)


@app.patch("/shopping-items/{item_id}", response_model=ShoppingItem, tags=["shopping"], summary="Update shopping item")
def update_shopping_item(item_id: str, payload: ShoppingItemUpdate) -> ShoppingItem:
    return service.update_shopping_item(item_id, payload)


@app.post("/bills", response_model=Bill, tags=["bills"], summary="Create bill")
def create_bill(payload: BillCreate) -> Bill:
    return service.create_bill(payload)


@app.get("/bills", response_model=list[Bill], tags=["bills"], summary="List bills")
def list_bills(
    status: ItemStatus | None = None,
    due_before: datetime | None = None,
) -> list[Bill]:
    return service.list_bills(status=status, due_before=due_before)


@app.patch("/bills/{bill_id}", response_model=Bill, tags=["bills"], summary="Update bill")
def update_bill(bill_id: str, payload: BillUpdate) -> Bill:
    return service.update_bill(bill_id, payload)


@app.post("/bills/{bill_id}/pay", response_model=Bill, tags=["bills"], summary="Mark bill paid")
def pay_bill(bill_id: str) -> Bill:
    return service.mark_bill_paid(bill_id)


@app.delete("/bills/{bill_id}", response_model=Bill, tags=["bills"], summary="Cancel bill")
def delete_bill(bill_id: str) -> Bill:
    return service.update_bill(bill_id, BillUpdate(status=ItemStatus.cancelled))


@app.post("/events", response_model=Event, tags=["events"], summary="Create event")
def create_event(payload: EventCreate) -> Event:
    return service.create_event(payload)


@app.get("/events", response_model=list[Event], tags=["events"], summary="List events")
def list_events(starts_before: datetime | None = None) -> list[Event]:
    return service.list_events(starts_before=starts_before)


@app.patch("/events/{event_id}", response_model=Event, tags=["events"], summary="Update event")
def update_event(event_id: str, payload: EventUpdate) -> Event:
    return service.update_event(event_id, payload)


@app.delete("/events/{event_id}", response_model=Event, tags=["events"], summary="Cancel event")
def delete_event(event_id: str) -> Event:
    return service.update_event(event_id, EventUpdate(status=ItemStatus.cancelled))


@app.post(
    "/integrations/google-calendar/auth/start",
    response_model=GoogleCalendarAuthStartResponse,
    tags=["google-calendar"],
    summary="Start Google Calendar OAuth",
    description=(
        "Starts Google Calendar authorization and returns the real Google OAuth link. "
        "GPT should use this endpoint only after the needed calendar email/profile is known, "
        "and then immediately show the returned real URL to the user. "
        "GPT should not invent placeholder links, should not ask the user to say 'generate link', "
        "and should not replace the returned URL with a generic Google URL."
    ),
)
def google_calendar_auth_start() -> GoogleCalendarAuthStartResponse:
    service.mark_google_calendar_offer_seen()
    return google_calendar.start_auth(settings=service.get_settings())


@app.post(
    "/integrations/google-calendar/auth/start-text",
    response_class=PlainTextResponse,
    tags=["google-calendar"],
    summary="Start Google Calendar OAuth and return raw URL",
    description="Returns only the full raw Google OAuth authorization URL as plain text. Use this when UI layers truncate or mangle the richer JSON response.",
)
def google_calendar_auth_start_text() -> str:
    service.mark_google_calendar_offer_seen()
    return google_calendar.start_auth_url(settings=service.get_settings())


@app.get(
    "/integrations/google-calendar/connect",
    response_class=RedirectResponse,
    tags=["google-calendar"],
    summary="Open Google Calendar connection flow in browser",
    description="Browser-friendly endpoint for non-technical users. Opening this URL immediately redirects to the real Google OAuth authorization page for the current calendar profile.",
)
def google_calendar_connect() -> RedirectResponse:
    service.mark_google_calendar_offer_seen()
    authorization_url = google_calendar.start_auth_url(settings=service.get_settings())
    return RedirectResponse(url=authorization_url, status_code=307)


@app.get(
    "/integrations/google-calendar/connect-info",
    response_model=GoogleCalendarConnectInfo,
    tags=["google-calendar"],
    summary="Get exact browser connect URL",
    description="Returns the exact absolute backend URL that should be opened in the browser to start Google Calendar connection. GPT should copy connect_url exactly with no edits.",
)
def google_calendar_connect_info(request: Request) -> GoogleCalendarConnectInfo:
    connect_url = str(request.url_for("google_calendar_connect_page"))
    return GoogleCalendarConnectInfo(
        connect_url=connect_url,
        message="Open this exact link in the browser to start Google Calendar connection.",
    )


@app.get(
    "/integrations/google-calendar/auth/callback",
    response_class=RedirectResponse,
    tags=["google-calendar"],
    summary="Google Calendar OAuth callback",
)
def google_calendar_auth_callback(code: str, state: str) -> RedirectResponse:
    try:
        state_payload = bootstrap_google_calendar.decode_state_any_user(state)
        callback_service = build_assistant_service(state_payload.get("user_id"))
        callback_google = callback_service.google_calendar_service
        current_settings = callback_service.get_settings()
        status = callback_google.finish_auth(code=code, state=state, settings=current_settings)
        if not current_settings.gmail_address and status.gmail_address:
            callback_service.update_settings(UserSettingsUpdate(gmail_address=status.gmail_address))
        callback_service.set_google_calendar_connected_profile(status.profile_key)
        message = "Google Calendar is now connected."
        if status.gmail_address:
            message = f"Google Calendar connected for {status.gmail_address}."
        return RedirectResponse(url=_frontend_calendar_redirect("connected", message), status_code=303)
    except HTTPException as exc:
        return RedirectResponse(
            url=_frontend_calendar_redirect("error", str(exc.detail)),
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=_frontend_calendar_redirect(
                "error",
                f"Google Calendar callback failed: {type(exc).__name__}: {exc}",
            ),
            status_code=303,
        )


@app.get("/integrations/google-calendar/status", response_model=GoogleCalendarAuthStatus, tags=["google-calendar"], summary="Get Google Calendar status")
def google_calendar_status() -> GoogleCalendarAuthStatus:
    return google_calendar.auth_status(settings=service.get_settings())


@app.get(
    "/integrations/google-calendar/test",
    response_model=GoogleCalendarAuthStatus,
    tags=["google-calendar"],
    summary="Test Google Calendar connection",
    description="Performs a lightweight calendar API call against the selected calendar profile and returns the resulting connection status if successful.",
)
def google_calendar_test() -> GoogleCalendarAuthStatus:
    return google_calendar.test_connection(settings=service.get_settings())


@app.get(
    "/integrations/google-calendar/setup-status",
    response_model=GoogleCalendarSetupStatus,
    tags=["google-calendar"],
    summary="Get Google Calendar onboarding status",
    description="GPT-friendly endpoint for deciding whether to offer Google Calendar setup, respect a prior decline, or continue a connect/switch flow.",
)
def google_calendar_setup_status() -> GoogleCalendarSetupStatus:
    settings = service.get_settings()
    auth_status = google_calendar.auth_status(settings=settings)
    should_offer_setup = not auth_status.connected and not settings.google_calendar_offer_declined
    if auth_status.connected:
        message = "Google Calendar is already connected for the current profile."
    elif settings.google_calendar_offer_declined:
        message = "The user previously declined Google Calendar setup. Do not ask again unless they request it."
    elif not settings.gmail_address and not settings.google_calendar_id:
        message = "Google Calendar is not connected. You may offer setup and ask which Gmail or calendar email to use."
    else:
        message = "Google Calendar is not connected for the selected profile. You may continue the setup flow."
    return GoogleCalendarSetupStatus(
        connected=auth_status.connected,
        profile_key=auth_status.profile_key,
        gmail_address=settings.gmail_address,
        calendar_id=auth_status.calendar_id,
        should_offer_setup=should_offer_setup,
        offer_seen=settings.google_calendar_offer_seen,
        offer_declined=settings.google_calendar_offer_declined,
        message=message,
    )


@app.get(
    "/integrations/google-calendar/connections",
    response_model=list[GoogleCalendarConnection],
    tags=["google-calendar"],
    summary="List known Google Calendar connection profiles",
)
def google_calendar_connections() -> list[GoogleCalendarConnection]:
    return google_calendar.list_connections()


@app.post(
    "/integrations/google-calendar/decline",
    response_model=GoogleCalendarSetupStatus,
    tags=["google-calendar"],
    summary="Record that the user declined Google Calendar setup",
)
def google_calendar_decline() -> GoogleCalendarSetupStatus:
    service.decline_google_calendar_offer()
    return google_calendar_setup_status()


@app.post(
    "/integrations/google-calendar/reset-offer",
    response_model=GoogleCalendarSetupStatus,
    tags=["google-calendar"],
    summary="Re-enable Google Calendar setup prompts",
)
def google_calendar_reset_offer() -> GoogleCalendarSetupStatus:
    service.clear_google_calendar_decline()
    return google_calendar_setup_status()


@app.delete("/integrations/google-calendar/status", response_model=GoogleCalendarAuthStatus, tags=["google-calendar"], summary="Disconnect Google Calendar")
def google_calendar_disconnect() -> GoogleCalendarAuthStatus:
    status = google_calendar.disconnect(settings=service.get_settings())
    service.set_google_calendar_connected_profile(None)
    return status


@app.post("/integrations/google-calendar/events", response_model=GoogleCalendarEventResult, tags=["google-calendar"], summary="Create Google Calendar event")
def google_calendar_create_event(payload: GoogleCalendarEventCreate) -> GoogleCalendarEventResult:
    return google_calendar.create_event(payload, settings=service.get_settings())


@app.get(
    "/integrations/google-calendar/events/day",
    response_model=list[Event],
    tags=["google-calendar"],
    summary="List Google Calendar events for a day",
    description="Returns raw Google Calendar events for the requested local day using the configured calendar profile.",
)
def google_calendar_events_for_day(for_date: datetime | None = None) -> list[Event]:
    settings = service.get_settings()
    target = for_date or service.current_time(settings)
    day_start = datetime.combine(target.date(), datetime.min.time())
    day_end = datetime.combine(target.date(), datetime.max.time())
    return google_calendar.list_events_between(day_start, day_end, settings=settings)


@app.post(
    "/integrations/google-calendar/sync/upcoming",
    response_model=GoogleCalendarPullSyncResponse,
    tags=["google-calendar"],
    summary="Pull upcoming Google Calendar events into local events",
    description="Imports or updates local event records from Google Calendar for the next N days.",
)
def google_calendar_sync_upcoming(days: int = 14) -> GoogleCalendarPullSyncResponse:
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90.")
    start_at = service.current_time(service.get_settings())
    end_at = start_at + timedelta(days=days)
    return service.sync_google_events_window(start_at=start_at, end_at=end_at)


@app.post(
    "/integrations/google-calendar/clear/day",
    response_model=GoogleCalendarClearResponse,
    tags=["google-calendar"],
    summary="Clear Google Calendar events for a day",
    description="Deletes Google Calendar events for the requested local day on the connected calendar profile.",
)
def google_calendar_clear_day(for_date: datetime | None = None) -> GoogleCalendarClearResponse:
    settings = service.get_settings()
    target = for_date or service.current_time(settings)
    return service.clear_google_calendar_for_date(target)


@app.post(
    "/integrations/google-calendar/create-and-sync/event",
    response_model=GoogleCalendarEventSyncResponse,
    tags=["google-calendar"],
    summary="Create local event and sync it to Google Calendar",
)
def google_calendar_create_and_sync_event(payload: EventCreate) -> GoogleCalendarEventSyncResponse:
    local_event = service.find_duplicate_event(payload) or service.create_event(payload)
    if local_event.google_event_id:
        return GoogleCalendarEventSyncResponse(
            local_event=local_event,
            calendar=GoogleCalendarEventResult(
                calendar_id=google_calendar._calendar_id(service.get_settings()),
                google_event_id=local_event.google_event_id,
                html_link="",
                status="existing",
            ),
        )
    calendar_result = google_calendar.sync_local_event(local_event, settings=service.get_settings())
    service.set_event_google_id(local_event.id, calendar_result.google_event_id)
    return GoogleCalendarEventSyncResponse(
        local_event=service.get_event(local_event.id),
        calendar=calendar_result,
    )


@app.post("/integrations/google-calendar/sync/events/{event_id}", response_model=GoogleCalendarEventResult, tags=["google-calendar"], summary="Sync local event to Google Calendar")
def google_calendar_sync_event(event_id: str) -> GoogleCalendarEventResult:
    event = service.get_event(event_id)
    result = google_calendar.sync_local_event(event, settings=service.get_settings())
    service.set_event_google_id(event_id, result.google_event_id)
    return result


@app.post("/reminders", response_model=Reminder, tags=["reminders"], summary="Create reminder")
def create_reminder(payload: ReminderCreate) -> Reminder:
    return service.create_reminder(payload)


@app.get("/reminders", response_model=list[Reminder], tags=["reminders"], summary="List reminders")
def list_reminders(status: ReminderStatus | None = None) -> list[Reminder]:
    return service.list_reminders(status=status)


@app.get("/reminders/due", response_model=list[Reminder], tags=["reminders"], summary="List due reminders")
def due_reminders() -> list[Reminder]:
    return service.get_due_reminders()


@app.get(
    "/summary",
    response_model=AssistantSummary,
    tags=["assistant"],
    summary="Get due and upcoming summary",
    description="Best endpoint for questions like what is due, what is overdue, or what is coming up this week.",
)
def get_summary() -> AssistantSummary:
    return service.get_summary()


@app.get(
    "/agenda",
    response_model=DailyAgenda,
    tags=["assistant"],
    summary="Get daily agenda",
    description=(
        "Returns the plan for a specific day, including tasks, bills, reminders, events, "
        "priority tasks, conflict warnings, and a suggested execution order."
    ),
)
def get_agenda(date: datetime | None = None) -> DailyAgenda:
    return service.get_agenda_for_date(date or service.current_time(service.get_settings()))


@app.get(
    "/agenda/today",
    response_model=DailyAgenda,
    tags=["assistant"],
    summary="Get today's agenda",
    description="Returns today's agenda using the configured user timezone instead of relying on the caller to resolve 'today'.",
)
def get_today_agenda() -> DailyAgenda:
    now = service.current_time(service.get_settings())
    return service.get_agenda_for_date(now)


@app.get(
    "/agenda/tomorrow",
    response_model=DailyAgenda,
    tags=["assistant"],
    summary="Get tomorrow's agenda",
    description="Returns tomorrow's agenda using the configured user timezone instead of relying on the caller to resolve 'tomorrow'.",
)
def get_tomorrow_agenda() -> DailyAgenda:
    now = service.current_time(service.get_settings())
    return service.get_agenda_for_date(now + timedelta(days=1))


@app.post(
    "/assistant/command",
    response_model=AssistantCommandResponse,
    tags=["assistant"],
    summary="Handle natural language command",
    description=(
        "Primary GPT-facing endpoint for conversational requests. "
        "Use this first for common natural phrases such as adding shopping items, creating reminders, "
        "creating simple tasks or bills, listing items, paying bills, or asking what is due."
    ),
)
def assistant_command(payload: AssistantCommandRequest) -> AssistantCommandResponse:
    return handle_command_with_llm(payload, service)


@app.post(
    "/assistant/undo",
    response_model=UndoActionResult,
    tags=["assistant"],
    summary="Undo the last supported change",
    description="Reverses the most recent create, update, cancel, complete, or pay action that was recorded by the assistant service.",
)
def assistant_undo() -> UndoActionResult:
    return service.undo_last_action()


@app.get(
    "/briefings/morning",
    response_model=BriefingResponse,
    tags=["assistant"],
    summary="Get morning briefing",
    description="Returns a concise morning overview, warnings, and next steps for today.",
)
def morning_briefing() -> BriefingResponse:
    return service.get_morning_briefing(now=service.current_time(service.get_settings()))


@app.get(
    "/briefings/evening",
    response_model=BriefingResponse,
    tags=["assistant"],
    summary="Get evening briefing",
    description="Returns a wrap-up of today plus a look ahead at tomorrow.",
)
def evening_briefing() -> BriefingResponse:
    return service.get_evening_briefing(now=service.current_time(service.get_settings()))


@app.get(
    "/briefings/tomorrow",
    response_model=BriefingResponse,
    tags=["assistant"],
    summary="Get tomorrow briefing",
    description="Returns a prep-oriented view of tomorrow's schedule and next steps.",
)
def tomorrow_briefing() -> BriefingResponse:
    return service.get_tomorrow_briefing(now=service.current_time(service.get_settings()))


@app.post("/reminders/send-due", response_model=list[Reminder], tags=["reminders"], summary="Process due reminders")
def send_due_reminders() -> list[Reminder]:
    return service.mark_due_reminders_sent()


@app.patch("/reminders/{reminder_id}", response_model=Reminder, tags=["reminders"], summary="Update reminder")
def update_reminder(reminder_id: str, payload: ReminderUpdate) -> Reminder:
    return service.update_reminder(reminder_id, payload)


@app.delete("/reminders/{reminder_id}", response_model=Reminder, tags=["reminders"], summary="Dismiss reminder")
def delete_reminder(reminder_id: str) -> Reminder:
    return service.update_reminder(reminder_id, ReminderUpdate(status=ReminderStatus.dismissed))


@app.post("/integrations/google-calendar/sync/reminders/{reminder_id}", response_model=GoogleCalendarEventResult, tags=["google-calendar"], summary="Sync reminder to Google Calendar")
def google_calendar_sync_reminder(reminder_id: str) -> GoogleCalendarEventResult:
    reminder = service.get_reminder(reminder_id)
    result = google_calendar.sync_local_reminder(reminder, settings=service.get_settings())
    service.set_reminder_google_id(reminder_id, result.google_event_id)
    return result


@app.post(
    "/integrations/google-calendar/create-and-sync/reminder",
    response_model=GoogleCalendarReminderSyncResponse,
    tags=["google-calendar"],
    summary="Create local reminder and sync it to Google Calendar",
)
def google_calendar_create_and_sync_reminder(payload: ReminderCreate) -> GoogleCalendarReminderSyncResponse:
    local_reminder = service.find_duplicate_reminder(payload) or service.create_reminder(payload)
    if local_reminder.google_event_id:
        return GoogleCalendarReminderSyncResponse(
            local_reminder=local_reminder,
            calendar=GoogleCalendarEventResult(
                calendar_id=google_calendar._calendar_id(service.get_settings()),
                google_event_id=local_reminder.google_event_id,
                html_link="",
                status="existing",
            ),
        )
    calendar_result = google_calendar.sync_local_reminder(local_reminder, settings=service.get_settings())
    service.set_reminder_google_id(local_reminder.id, calendar_result.google_event_id)
    return GoogleCalendarReminderSyncResponse(
        local_reminder=service.get_reminder(local_reminder.id),
        calendar=calendar_result,
    )


@app.post(
    "/integrations/google-calendar/create-and-sync/task",
    response_model=GoogleCalendarTaskSyncResponse,
    tags=["google-calendar"],
    summary="Create local task and sync it to Google Calendar",
)
def google_calendar_create_and_sync_task(payload: TaskCreate) -> GoogleCalendarTaskSyncResponse:
    local_task = service.find_duplicate_task(payload) or service.create_task(payload)
    if local_task.google_event_id:
        return GoogleCalendarTaskSyncResponse(
            local_task=local_task,
            calendar=GoogleCalendarEventResult(
                calendar_id=google_calendar._calendar_id(service.get_settings()),
                google_event_id=local_task.google_event_id,
                html_link="",
                status="existing",
            ),
        )
    calendar_result = google_calendar.sync_local_task(local_task, settings=service.get_settings())
    service.set_task_google_id(local_task.id, calendar_result.google_event_id)
    return GoogleCalendarTaskSyncResponse(
        local_task=service.get_task(local_task.id),
        calendar=calendar_result,
    )
