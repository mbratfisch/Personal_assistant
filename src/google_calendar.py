from __future__ import annotations

import json
import os
import re
import secrets
import hashlib
import hmac
import base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from dateutil.parser import isoparse

from src.assistant_models import (
    Event,
    GoogleCalendarClearResponse,
    GoogleCalendarAuthStartResponse,
    GoogleCalendarAuthStatus,
    GoogleCalendarConnection,
    GoogleCalendarEventCreate,
    GoogleCalendarEventResult,
    GoogleEventType,
    Reminder,
    Task,
    UserSettings,
)

GOOGLE_CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar.events"]
GOOGLE_IDENTITY_SCOPE = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
GOOGLE_OAUTH_SCOPES = GOOGLE_CALENDAR_SCOPE + GOOGLE_IDENTITY_SCOPE


class GoogleCalendarService:
    def __init__(
        self,
        token_dir: str = "data/google_tokens",
        state_dir: str = "data/google_oauth_states",
        connections_path: str = "data/google_calendar_connections.json",
        user_id: str | None = None,
    ) -> None:
        self.user_id = user_id
        user_key = self._user_key()
        self.token_dir = Path(token_dir) / user_key
        self.state_dir = Path(state_dir) / user_key
        self.connections_path = Path(connections_path).parent / user_key / Path(connections_path).name
        self.storage_backend = (os.getenv("APP_STORAGE_BACKEND") or "json").strip().lower()
        self.firestore_collection = os.getenv("FIRESTORE_COLLECTION", "personal_assistant")
        self.firestore_tokens_collection = os.getenv("FIRESTORE_GOOGLE_TOKENS_COLLECTION", "google_calendar_tokens")
        self.firestore_connections_document = os.getenv("FIRESTORE_GOOGLE_CONNECTIONS_DOCUMENT", "google_calendar_connections")
        self.firestore_project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")
        self._firestore_client: Any | None = None
        if self.storage_backend == "json":
            self.token_dir.mkdir(parents=True, exist_ok=True)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.connections_path.parent.mkdir(parents=True, exist_ok=True)

    def _user_key(self) -> str:
        raw = (self.user_id or "default").strip()
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)
        return safe or "default"

    def _firestore_enabled(self) -> bool:
        return self.storage_backend == "firestore"

    def _get_firestore_client(self) -> Any:
        if self._firestore_client is not None:
            return self._firestore_client
        try:
            from google.cloud import firestore
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail="Firestore dependencies are not installed. Add google-cloud-firestore to requirements.",
            ) from exc
        try:
            if self.firestore_project_id:
                self._firestore_client = firestore.Client(project=self.firestore_project_id)
            else:
                self._firestore_client = firestore.Client()
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not initialize Firestore client: {type(exc).__name__}: {exc}",
            ) from exc
        return self._firestore_client

    def _firestore_tokens(self) -> Any:
        return (
            self._get_firestore_client()
            .collection(self.firestore_collection)
            .document(self._user_key())
            .collection(self.firestore_tokens_collection)
        )

    def _firestore_connections_doc(self) -> Any:
        return (
            self._get_firestore_client()
            .collection(self.firestore_collection)
            .document(self._user_key())
            .collection("_internal")
            .document(self.firestore_connections_document)
        )

    def _state_secret(self) -> bytes:
        configured = os.getenv("GOOGLE_OAUTH_STATE_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET")
        if not configured:
            raise HTTPException(
                status_code=400,
                detail="Missing Google OAuth state secret. Set GOOGLE_CLIENT_SECRET or GOOGLE_OAUTH_STATE_SECRET.",
            )
        return configured.encode("utf-8")

    def _encode_state_token(self, settings: UserSettings | None = None) -> str:
        payload = {
            "nonce": secrets.token_urlsafe(18),
            "user_id": self.user_id,
            "profile_key": self._profile_key(settings),
            "issued_at": int(datetime.utcnow().timestamp()),
        }
        raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_token = base64.urlsafe_b64encode(raw_payload).decode("ascii").rstrip("=")
        signature = hmac.new(self._state_secret(), payload_token.encode("utf-8"), hashlib.sha256).digest()
        signature_token = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"{payload_token}.{signature_token}"

    def _decode_state_token(self, state: str, settings: UserSettings | None = None) -> dict[str, Any]:
        try:
            payload_token, signature_token = state.split(".", 1)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid OAuth state.") from exc

        expected_signature = hmac.new(
            self._state_secret(),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        actual_signature = base64.urlsafe_b64decode(signature_token + "=" * (-len(signature_token) % 4))
        if not hmac.compare_digest(expected_signature, actual_signature):
            raise HTTPException(status_code=400, detail="Invalid OAuth state.")

        raw_payload = base64.urlsafe_b64decode(payload_token + "=" * (-len(payload_token) % 4))
        payload = json.loads(raw_payload.decode("utf-8"))
        if payload.get("user_id") != self.user_id:
            raise HTTPException(status_code=400, detail="OAuth state does not match the current user.")
        if payload.get("profile_key") != self._profile_key(settings):
            raise HTTPException(status_code=400, detail="OAuth state does not match the selected calendar profile.")
        issued_at = int(payload.get("issued_at", 0))
        if issued_at <= 0 or datetime.utcnow().timestamp() - issued_at > 3600:
            raise HTTPException(status_code=400, detail="OAuth state has expired. Start the connection again.")
        return payload

    def decode_state_any_user(self, state: str) -> dict[str, Any]:
        try:
            payload_token, signature_token = state.split(".", 1)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid OAuth state.") from exc

        expected_signature = hmac.new(
            self._state_secret(),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        actual_signature = base64.urlsafe_b64decode(signature_token + "=" * (-len(signature_token) % 4))
        if not hmac.compare_digest(expected_signature, actual_signature):
            raise HTTPException(status_code=400, detail="Invalid OAuth state.")

        raw_payload = base64.urlsafe_b64decode(payload_token + "=" * (-len(payload_token) % 4))
        payload = json.loads(raw_payload.decode("utf-8"))
        issued_at = int(payload.get("issued_at", 0))
        if issued_at <= 0 or datetime.utcnow().timestamp() - issued_at > 3600:
            raise HTTPException(status_code=400, detail="OAuth state has expired. Start the connection again.")
        return payload

    def _load_google_dependencies(self) -> tuple[Any, Any, Any]:
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import Flow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Google Calendar dependencies are not installed. "
                    "Install requirements.txt before using this integration."
                ),
            ) from exc
        return Credentials, Flow, build

    def _client_config(self) -> dict[str, Any]:
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        redirect_uri = os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://127.0.0.1:8000/integrations/google-calendar/auth/callback",
        )
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Missing Google OAuth configuration. "
                    "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
                ),
            )
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }

    def _redirect_uri(self) -> str:
        return os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://127.0.0.1:8000/integrations/google-calendar/auth/callback",
        )

    def _timezone(self, settings: UserSettings | None = None) -> ZoneInfo:
        timezone_name = settings.timezone if settings and settings.timezone else "UTC"
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _calendar_id(self, settings: UserSettings | None = None) -> str:
        if settings and settings.google_calendar_id:
            return settings.google_calendar_id
        if settings and settings.gmail_address:
            return settings.gmail_address
        return os.getenv("GOOGLE_CALENDAR_ID", "primary")

    def _profile_key(self, settings: UserSettings | None = None) -> str:
        base = self._calendar_id(settings)
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", base.strip().lower())
        return safe or "primary"

    def _token_path(self, settings: UserSettings | None = None) -> Path:
        return self.token_dir / f"{self._profile_key(settings)}.json"

    def _state_path(self, settings: UserSettings | None = None) -> Path:
        return self.state_dir / f"{self._profile_key(settings)}.json"

    def _load_connections(self) -> list[GoogleCalendarConnection]:
        if self._firestore_enabled():
            snapshot = self._firestore_connections_doc().get()
            if not snapshot.exists:
                return []
            payload = snapshot.to_dict() or {}
            return [GoogleCalendarConnection(**item) for item in payload.get("connections", [])]
        if not self.connections_path.exists():
            return []
        payload = json.loads(self.connections_path.read_text(encoding="utf-8"))
        return [GoogleCalendarConnection(**item) for item in payload]

    def _save_connections(self, connections: list[GoogleCalendarConnection]) -> None:
        if self._firestore_enabled():
            self._firestore_connections_doc().set(
                {"connections": [connection.model_dump(mode="json") for connection in connections]}
            )
            return
        self.connections_path.write_text(
            json.dumps([connection.model_dump(mode="json") for connection in connections], indent=2),
            encoding="utf-8",
        )

    def _upsert_connection(
        self,
        settings: UserSettings | None,
        connected: bool,
        has_refresh_token: bool,
    ) -> GoogleCalendarConnection:
        profile_key = self._profile_key(settings)
        calendar_id = self._calendar_id(settings)
        token_path = (
            f"firestore://{self.firestore_collection}/{self._user_key()}/{self.firestore_tokens_collection}/{profile_key}"
            if self._firestore_enabled()
            else str(self._token_path(settings))
        )
        connections = self._load_connections()
        existing = next((item for item in connections if item.profile_key == profile_key), None)
        if existing is None:
            existing = GoogleCalendarConnection(
                profile_key=profile_key,
                calendar_id=calendar_id,
                gmail_address=settings.gmail_address if settings else None,
                token_path=token_path,
            )
            connections.append(existing)
        existing.calendar_id = calendar_id
        existing.gmail_address = settings.gmail_address if settings else None
        existing.token_path = token_path
        existing.connected = connected
        existing.has_refresh_token = has_refresh_token
        existing.last_used_at = datetime.utcnow()
        self._save_connections(connections)
        return existing

    def list_connections(self) -> list[GoogleCalendarConnection]:
        return self._load_connections()

    def _find_connection(self, settings: UserSettings | None = None) -> GoogleCalendarConnection | None:
        profile_key = self._profile_key(settings)
        return next((item for item in self._load_connections() if item.profile_key == profile_key), None)

    def _write_state(self, state: str, settings: UserSettings | None = None) -> None:
        if self._firestore_enabled():
            self._firestore_tokens().document(f"state_{self._profile_key(settings)}").set({"state": state})
            return
        self._state_path(settings).write_text(json.dumps({"state": state}), encoding="utf-8")

    def _read_state(self, settings: UserSettings | None = None) -> str | None:
        if self._firestore_enabled():
            snapshot = self._firestore_tokens().document(f"state_{self._profile_key(settings)}").get()
            if not snapshot.exists:
                return None
            payload = snapshot.to_dict() or {}
            return payload.get("state")
        state_path = self._state_path(settings)
        if not state_path.exists():
            return None
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return payload.get("state")

    def _save_credentials(self, credentials: Any, settings: UserSettings | None = None) -> None:
        if self._firestore_enabled():
            self._firestore_tokens().document(self._profile_key(settings)).set({"credentials_json": credentials.to_json()})
            return
        self._token_path(settings).write_text(credentials.to_json(), encoding="utf-8")

    def _load_credentials(self, settings: UserSettings | None = None) -> Any:
        Credentials, _, _ = self._load_google_dependencies()
        if self._firestore_enabled():
            snapshot = self._firestore_tokens().document(self._profile_key(settings)).get()
            if not snapshot.exists:
                raise HTTPException(status_code=400, detail="Google Calendar is not connected yet.")
            payload = snapshot.to_dict() or {}
            credentials_json = payload.get("credentials_json")
            if not credentials_json:
                raise HTTPException(status_code=400, detail="Google Calendar credentials are missing.")
            credentials = Credentials.from_authorized_user_info(json.loads(credentials_json), GOOGLE_OAUTH_SCOPES)
        else:
            token_path = self._token_path(settings)
            if not token_path.exists():
                raise HTTPException(status_code=400, detail="Google Calendar is not connected yet.")
            credentials = Credentials.from_authorized_user_file(str(token_path), GOOGLE_OAUTH_SCOPES)
        if credentials.expired and credentials.refresh_token:
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
            self._save_credentials(credentials, settings=settings)
        return credentials

    def _fetch_authenticated_email(self, credentials: Any) -> str | None:
        try:
            _, _, build = self._load_google_dependencies()
            oauth_client = build("oauth2", "v2", credentials=credentials)
            profile = oauth_client.userinfo().get().execute()
            email = profile.get("email")
            return email.strip() if isinstance(email, str) and email.strip() else None
        except Exception:
            return None

    def auth_status(self, settings: UserSettings | None = None) -> GoogleCalendarAuthStatus:
        connection = self._find_connection(settings)
        gmail_address = settings.gmail_address if settings and settings.gmail_address else (connection.gmail_address if connection else None)
        has_token = False
        if self._firestore_enabled():
            has_token = self._firestore_tokens().document(self._profile_key(settings)).get().exists
        else:
            has_token = self._token_path(settings).exists()
        if not has_token:
            return GoogleCalendarAuthStatus(
                connected=False,
                calendar_id=self._calendar_id(settings),
                gmail_address=gmail_address,
                redirect_uri=self._redirect_uri(),
                has_refresh_token=False,
                profile_key=self._profile_key(settings),
            )
        try:
            credentials = self._load_credentials(settings=settings)
        except HTTPException:
            # Treat unreadable or stale stored credentials as a disconnected state
            # so the UI can recover by reconnecting instead of crashing on status load.
            self._upsert_connection(settings, connected=False, has_refresh_token=False)
            return GoogleCalendarAuthStatus(
                connected=False,
                calendar_id=self._calendar_id(settings),
                gmail_address=gmail_address,
                redirect_uri=self._redirect_uri(),
                has_refresh_token=False,
                profile_key=self._profile_key(settings),
            )
        if not gmail_address:
            gmail_address = self._fetch_authenticated_email(credentials)
        upsert_settings = settings
        if settings is not None and gmail_address and not settings.gmail_address and not settings.google_calendar_id:
            upsert_settings = settings.model_copy(update={"gmail_address": gmail_address})
        self._upsert_connection(upsert_settings, connected=bool(credentials.valid), has_refresh_token=bool(credentials.refresh_token))
        return GoogleCalendarAuthStatus(
            connected=bool(credentials.valid),
            calendar_id=self._calendar_id(settings),
            gmail_address=gmail_address,
            redirect_uri=self._redirect_uri(),
            has_refresh_token=bool(credentials.refresh_token),
            profile_key=self._profile_key(settings),
        )

    def start_auth(self, settings: UserSettings | None = None) -> GoogleCalendarAuthStartResponse:
        _, Flow, _ = self._load_google_dependencies()
        state = self._encode_state_token(settings=settings)
        flow = Flow.from_client_config(
            self._client_config(),
            scopes=GOOGLE_OAUTH_SCOPES,
            state=state,
        )
        flow.redirect_uri = self._redirect_uri()
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        markdown_link = f"[Authorize Google Calendar Access]({authorization_url})"
        display_message = (
            "Open this link to connect Google Calendar: "
            f"{authorization_url}"
        )
        return GoogleCalendarAuthStartResponse(
            authorization_url=authorization_url,
            authorization_markdown_link=markdown_link,
            display_message=display_message,
            state=state,
            profile_key=self._profile_key(settings),
        )

    def start_auth_url(self, settings: UserSettings | None = None) -> str:
        return self.start_auth(settings=settings).authorization_url

    def finish_auth(self, code: str, state: str, settings: UserSettings | None = None) -> GoogleCalendarAuthStatus:
        _, Flow, _ = self._load_google_dependencies()
        self._decode_state_token(state=state, settings=settings)
        flow = Flow.from_client_config(
            self._client_config(),
            scopes=GOOGLE_OAUTH_SCOPES,
            state=state,
        )
        flow.redirect_uri = self._redirect_uri()
        flow.fetch_token(code=code)
        authenticated_email = self._fetch_authenticated_email(flow.credentials)
        target_settings = settings
        if settings is not None and not settings.gmail_address and not settings.google_calendar_id and authenticated_email:
            target_settings = settings.model_copy(update={"gmail_address": authenticated_email})
        self._save_credentials(flow.credentials, settings=target_settings)
        self._upsert_connection(target_settings, connected=True, has_refresh_token=bool(flow.credentials.refresh_token))
        status = self.auth_status(settings=target_settings)
        if authenticated_email:
            status.gmail_address = authenticated_email
        return status

    def disconnect(self, settings: UserSettings | None = None) -> GoogleCalendarAuthStatus:
        token_path = self._token_path(settings)
        state_path = self._state_path(settings)
        if self._firestore_enabled():
            self._firestore_tokens().document(self._profile_key(settings)).delete()
            self._firestore_tokens().document(f"state_{self._profile_key(settings)}").delete()
        else:
            if token_path.exists():
                token_path.unlink()
            if state_path.exists():
                state_path.unlink()
        self._upsert_connection(settings, connected=False, has_refresh_token=False)
        return self.auth_status(settings=settings)

    def test_connection(self, settings: UserSettings | None = None) -> GoogleCalendarAuthStatus:
        client = self._build_client(settings=settings)
        calendar_id = self._calendar_id(settings)
        client.events().list(
            calendarId=calendar_id,
            maxResults=1,
            singleEvents=True,
            timeMin=datetime.utcnow().isoformat() + "Z",
        ).execute()
        return self.auth_status(settings=settings)

    def _build_client(self, settings: UserSettings | None = None) -> Any:
        _, _, build = self._load_google_dependencies()
        credentials = self._load_credentials(settings=settings)
        return build("calendar", "v3", credentials=credentials)

    def _make_event_body(
        self,
        payload: GoogleCalendarEventCreate,
        settings: UserSettings | None = None,
    ) -> dict[str, Any]:
        end_at = payload.end_at or (payload.start_at + timedelta(minutes=30))
        timezone = settings.timezone if settings and settings.timezone else "America/Chicago"
        start_block: dict[str, Any]
        end_block: dict[str, Any]
        if payload.all_day or payload.event_type == GoogleEventType.birthday:
            all_day_end = end_at.date()
            if all_day_end <= payload.start_at.date():
                all_day_end = payload.start_at.date() + timedelta(days=1)
            start_block = {"date": payload.start_at.date().isoformat()}
            end_block = {"date": all_day_end.isoformat()}
        else:
            start_block = {"dateTime": payload.start_at.isoformat(), "timeZone": timezone}
            end_block = {"dateTime": end_at.isoformat(), "timeZone": timezone}

        event_body: dict[str, Any] = {
            "summary": payload.summary,
            "location": payload.location,
            "description": payload.description,
            "start": start_block,
            "end": end_block,
            "eventType": payload.event_type.value,
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": minutes} for minutes in payload.reminder_minutes],
            },
        }
        if payload.recurrence:
            event_body["recurrence"] = payload.recurrence
        if payload.visibility:
            event_body["visibility"] = payload.visibility
        if payload.transparency:
            event_body["transparency"] = payload.transparency
        if payload.attendees:
            event_body["attendees"] = [{"email": email} for email in payload.attendees]
        if payload.event_type == GoogleEventType.birthday:
            event_body["birthdayProperties"] = {"type": payload.birthday_label or "birthday"}
            if not payload.recurrence:
                event_body["recurrence"] = ["RRULE:FREQ=YEARLY"]
        if payload.event_type == GoogleEventType.focus_time:
            event_body["focusTimeProperties"] = {
                "autoDeclineMode": payload.focus_time_decline_mode or "declineNone",
                "chatStatus": payload.focus_time_chat_status or "available",
            }
        if payload.event_type == GoogleEventType.out_of_office:
            event_body["outOfOfficeProperties"] = {
                "autoDeclineMode": payload.out_of_office_auto_decline_mode or "declineNone",
                "declineMessage": payload.out_of_office_decline_message or "",
            }
        if payload.event_type == GoogleEventType.working_location:
            if payload.working_location_type == "office":
                event_body["workingLocationProperties"] = {
                    "type": "officeLocation",
                    "officeLocation": {"label": payload.working_location_label or "Office"},
                }
            elif payload.working_location_home_office:
                event_body["workingLocationProperties"] = {"type": "homeOffice"}
            else:
                event_body["workingLocationProperties"] = {
                    "type": "customLocation",
                    "customLocation": {"label": payload.working_location_label or payload.location or "Custom"},
                }
        return event_body

    def create_event(
        self,
        payload: GoogleCalendarEventCreate,
        settings: UserSettings | None = None,
    ) -> GoogleCalendarEventResult:
        client = self._build_client(settings=settings)
        calendar_id = self._calendar_id(settings)
        created = (
            client.events()
            .insert(
                calendarId=calendar_id,
                body=self._make_event_body(payload, settings=settings),
                sendUpdates=payload.send_updates,
            )
            .execute()
        )
        return GoogleCalendarEventResult(
            calendar_id=calendar_id,
            google_event_id=created["id"],
            html_link=created["htmlLink"],
            status=created["status"],
        )

    def list_events_between(
        self,
        start_at: datetime,
        end_at: datetime,
        settings: UserSettings | None = None,
    ) -> list[Event]:
        client = self._build_client(settings=settings)
        calendar_id = self._calendar_id(settings)
        response = (
            client.events()
            .list(
                calendarId=calendar_id,
                timeMin=start_at.replace(tzinfo=self._timezone(settings)).astimezone(ZoneInfo("UTC")).isoformat(),
                timeMax=end_at.replace(tzinfo=self._timezone(settings)).astimezone(ZoneInfo("UTC")).isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events: list[Event] = []
        for item in response.get("items", []):
            if item.get("status") == "cancelled":
                continue
            google_id = item.get("id")
            start_info = item.get("start", {})
            end_info = item.get("end", {})
            start_value = start_info.get("dateTime") or start_info.get("date")
            end_value = end_info.get("dateTime") or end_info.get("date")
            if not start_value:
                continue
            starts_at = isoparse(start_value)
            ends_at = isoparse(end_value) if end_value else None
            target_timezone = self._timezone(settings)
            if starts_at.tzinfo is not None:
                starts_at = starts_at.astimezone(target_timezone).replace(tzinfo=None)
            if ends_at is not None and ends_at.tzinfo is not None:
                ends_at = ends_at.astimezone(target_timezone).replace(tzinfo=None)
            updated_at_raw = item.get("updated")
            updated_at = isoparse(updated_at_raw).replace(tzinfo=None) if updated_at_raw else datetime.utcnow()
            events.append(
                Event(
                    id=f"google-{google_id}",
                    title=item.get("summary") or "(Untitled Google event)",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    location=item.get("location") or "",
                    notes=item.get("description") or "",
                    google_event_id=google_id,
                    status="active",
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )
        return events

    def delete_event(self, google_event_id: str, settings: UserSettings | None = None) -> None:
        client = self._build_client(settings=settings)
        calendar_id = self._calendar_id(settings)
        client.events().delete(
            calendarId=calendar_id,
            eventId=google_event_id,
            sendUpdates="none",
        ).execute()

    def clear_events_between(
        self,
        start_at: datetime,
        end_at: datetime,
        settings: UserSettings | None = None,
    ) -> GoogleCalendarClearResponse:
        events = self.list_events_between(start_at=start_at, end_at=end_at, settings=settings)
        deleted_ids: list[str] = []
        deleted_titles: list[str] = []
        skipped = 0

        for event in events:
            if not event.google_event_id:
                skipped += 1
                continue
            self.delete_event(event.google_event_id, settings=settings)
            deleted_ids.append(event.google_event_id)
            deleted_titles.append(event.title)

        return GoogleCalendarClearResponse(
            start_at=start_at,
            end_at=end_at,
            deleted=len(deleted_ids),
            skipped=skipped,
            deleted_google_event_ids=deleted_ids,
            deleted_titles=deleted_titles,
        )

    def sync_local_event(self, event: Event, settings: UserSettings | None = None) -> GoogleCalendarEventResult:
        return self.create_event(
            GoogleCalendarEventCreate(
                summary=event.title,
                start_at=event.starts_at,
                end_at=event.ends_at,
                description=event.notes,
                location=event.location,
            ),
            settings=settings,
        )

    def sync_local_reminder(
        self,
        reminder: Reminder,
        settings: UserSettings | None = None,
    ) -> GoogleCalendarEventResult:
        return self.create_event(
            GoogleCalendarEventCreate(
                summary=reminder.title,
                start_at=reminder.remind_at,
                end_at=reminder.remind_at + timedelta(minutes=15),
                description=reminder.notes,
                reminder_minutes=[0],
            ),
            settings=settings,
        )

    def sync_local_task(self, task: Task, settings: UserSettings | None = None) -> GoogleCalendarEventResult:
        start_at = task.due_at or (task.created_at + timedelta(hours=1))
        return self.create_event(
            GoogleCalendarEventCreate(
                summary=f"Task: {task.title}",
                start_at=start_at,
                end_at=start_at + timedelta(minutes=30),
                description=task.details,
                event_type=GoogleEventType.default,
                reminder_minutes=[30, 5],
            ),
            settings=settings,
        )
