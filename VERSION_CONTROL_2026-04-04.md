# Version Control Snapshot - 2026-04-04

This file records the manual backend and frontend fixes applied during the production auth, calendar, and Telegram stabilization work on 2026-04-04.

## Frontend repo

Repository:
- `D:\Projects\your-daily-compass`

Relevant pushed commits:
- `fbb87f2` - `Fix mobile Google auth redirect and polish onboarding`
- `2bc75e3` - `Make settings page resilient and remove favicon`
- `dc1fb61` - `Add mobile fallback for Google Calendar view`

## Backend folder

Workspace:
- `D:\Projects\Personal Assistant`

Note:
- This folder is not a git repository on this machine, so the backend changes below were preserved here manually.

## Backend changes

### 1. Google Calendar stale token fallback

File:
- `src/google_calendar.py`

Purpose:
- Prevent `/integrations/google-calendar/status` from throwing `400` when a saved token exists but is stale or unreadable.
- Return a safe disconnected status instead, so the UI can reconnect cleanly.

Current code in `auth_status()`:

```python
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
```

### 2. Telegram natural-language calendar parsing

File:
- `src/nlp.py`

Purpose:
- Make Telegram understand common natural agenda requests like:
  - `check my calendar`
  - `check my calendar for tomorrow`
  - `what's on my calendar today`

Added agenda trigger phrases:

```python
            "calendar for today",
            "calendar for tomorrow",
            "check my calendar",
            "check calendar",
            "what's on my calendar",
            "whats on my calendar",
            "what is on my calendar",
            "can you check my calendar",
```

## Deploy command used

Run from:
- `D:\Projects\Personal Assistant`

```powershell
gcloud config set project moonlit-oven-492114-e1
gcloud run deploy personal-assistant `
  --source . `
  --region us-central1 `
  --allow-unauthenticated
```

## Production outcomes after fixes

- Mobile Google sign-in works on `personalpilot.app`
- `www.personalpilot.app` no longer breaks Firebase redirect auth
- Google Calendar connection, test, and sync work from the app
- Mobile calendar page uses direct-open fallback instead of broken embedded Google iframe
- Telegram account linking works
- Telegram now understands natural calendar questions like `check my calendar for tomorrow`

