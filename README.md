# Personal Assistant MVP

This project is a simple backend for a conversational personal assistant that can:

- save notes
- manage tasks
- manage shopping list items
- track bills to pay
- manage events
- create reminders
- understand simple natural-language commands
- handle recurring reminders, recurring tasks, and recurring bills

The backend is designed to be used primarily through a custom GPT via API actions.

It also includes a Google Calendar integration flow so your assistant can create calendar events and mirror reminders into Google Calendar.

## Stack

- Python 3.11+
- FastAPI
- Local JSON storage

## Why this architecture

This is a strong MVP for a GPT-powered assistant because it gives you:

- a clean API surface
- persistent storage without database setup
- clear entities the GPT can create and query
- a path to future notification integrations

## Project structure

```text
src/
  api_server.py
  assistant_models.py
  repository.py
  service.py
  reminder_engine.py
data/
  assistant_db.json
requirements.txt
```

## Run locally

1. Create a virtual environment
2. Install dependencies
3. Start the API

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn src.api_server:app --reload --host 127.0.0.1 --port 8000
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`
- Optional local web app: `http://127.0.0.1:8000/app`

## Cloud Run quick deploy

This repo is now container-ready with [Dockerfile](D:/Projects/Personal Assistant/Dockerfile).

Important:
- For local development, the app stores data in local JSON files under `data/`.
- For Cloud Run or multi-instance hosting, set `APP_STORAGE_BACKEND=firestore` so assistant data and Google Calendar credentials are stored durably in Firestore.
- For personal accounts / multiple users, set `APP_AUTH_MODE=required` and send Firebase ID tokens as `Authorization: Bearer <token>`.
- Per-user storage design is documented in [docs/personal_accounts_firestore_schema.md](D:/Projects/Personal%20Assistant/docs/personal_accounts_firestore_schema.md).

Quick deploy flow:

1. Create or select a Google Cloud project.
2. Enable:
- Cloud Run API
- Cloud Build API
- Artifact Registry API
- Firestore API

3. Authenticate:

```powershell
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

4. Build and deploy:

```powershell
gcloud run deploy personal-assistant `
  --source . `
  --region us-central1 `
  --allow-unauthenticated
```

5. Set environment variables after deploy:

```powershell
gcloud run services update personal-assistant `
  --region us-central1 `
  --set-env-vars APP_STORAGE_BACKEND=firestore,GOOGLE_CLIENT_ID=YOUR_CLIENT_ID,GOOGLE_CLIENT_SECRET=YOUR_CLIENT_SECRET,GOOGLE_REDIRECT_URI=https://YOUR_CLOUD_RUN_URL/integrations/google-calendar/auth/callback
```

6. Update the Google OAuth redirect URI in Google Cloud Console to:

```text
https://YOUR_CLOUD_RUN_URL/integrations/google-calendar/auth/callback
```

7. Test:
- `https://YOUR_CLOUD_RUN_URL/docs`
- `https://YOUR_CLOUD_RUN_URL/openapi.json`
- `https://YOUR_CLOUD_RUN_URL/connect-google-calendar`

8. Create a Firestore database in Native mode before using the hosted app for durable storage.

9. If you already have local assistant data in `data/assistant_db.json`, migrate it into Firestore:

```powershell
python scripts/migrate_assistant_db_to_firestore.py
```

10. After switching to Firestore, reconnect Google Calendar once so the hosted service stores the refresh token in Firestore as well.

## Conversational assistant endpoints

- `POST /assistant/command`
- `GET /summary`
- `GET /agenda`
- `POST /assistant/undo`
- `GET /briefings/morning`
- `GET /briefings/evening`
- `GET /briefings/tomorrow`

When Google Calendar is connected, `/summary`, `/agenda`, and the briefing endpoints also pull upcoming Google Calendar events as read-only planning context. This is a lightweight pull for visibility, not a full two-way event import.

For debugging raw hosted Google Calendar pulls by day, use:
- `GET /integrations/google-calendar/events/day`

## Hosted GPT prompt

- Hosted low-friction GPT instructions: [docs/gpt_instructions_hosted_minimal_ascii.md](D:/Projects/Personal%20Assistant/docs/gpt_instructions_hosted_minimal_ascii.md)
- `POST /tasks/{task_id}/complete`
- `POST /bills/{bill_id}/pay`

Example command payload:

```json
{
  "text": "remind me tomorrow at 8am to call my dentist every week"
}
```

Example supported commands:

- `save a note: the router code is taped under the desk`
- `add milk and eggs to my shopping list`
- `add 2 apples and milk to my shopping list`
- `do i already have milk on my shopping list?`
- `remind me tomorrow at 8am to call my dentist every week`
- `create a bill for internet due on 2026-04-10 for 79 monthly`
- `create task submit taxes tomorrow at 6pm`
- `i need to submit taxes tomorrow at 6pm`
- `schedule dinner with Ana on Friday at 7pm`
- `what's due`
- `what is on my shopping list`
- `show my tasks`
- `show my bills`
- `show my reminders`
- `bought apples`
- `remove milk from my shopping list`
- `cancel my reminder dentist`
- `cancel my task submit taxes`
- `cancel my event dentist appointment`
- `cancel my bill internet`
- `undo`
- `move my reminder dentist to friday at 3pm`
- `snooze my reminder dentist for 2 hours`
- `move my task submit taxes to monday at 10am`
- `rename my task submit taxes to file taxes`
- `pay internet`
- `change internet bill to 89`
- `change internet bill due on april 15`
- `what do i have today?`
- `what do i have tomorrow?`
- `agenda for friday`
- `what's my day?`
- `what should i do today?`
- `plan my day`
- `give me my morning briefing`
- `wrap up my day`
- `what do i need tomorrow?`

## Recurring behavior

- Completing a recurring task automatically creates the next occurrence.
- Paying a recurring bill automatically creates the next bill.
- Sending due recurring reminders automatically creates the next reminder occurrence.
- Supported recurrence values are `daily`, `weekly`, `monthly`, and `yearly`.

## Shopping dedupe behavior

- Adding an item that is already active on the shopping list updates the existing item instead of creating a duplicate.
- If both quantities are numeric, the quantity is incremented.
- You can also ask whether something is already on the list.

## Editing and undo behavior

- You can cancel or remove tasks, reminders, events, shopping items, bills, and notes with conversational commands.
- The assistant now detects ambiguous name matches and asks for a more specific title instead of silently changing the wrong item.
- The last supported create, update, cancel, complete, or pay action can be reversed with `undo` or `POST /assistant/undo`.

## Daily agenda behavior

- The assistant can answer date-focused questions like `what do i have today?`, `what do i have tomorrow?`, or `agenda for friday`.
- Daily agendas include tasks, bills, reminders, and events for that specific date.
- Daily agendas also include priority tasks, conflict warnings, a best next action, and a suggested execution order for the day.
- You can query daily planning directly with `GET /agenda` or conversationally with `POST /assistant/command`.

## Prioritization behavior

- `GET /summary` now includes `priority_tasks` and `best_next_action`.
- Day planning commands like `what should i do today?` or `what should i do first?` use urgency, task priority, reminders, and upcoming events to suggest the next move.

## Briefing workflows

- `GET /briefings/morning` returns a concise start-of-day overview, warnings, and next steps.
- `GET /briefings/evening` returns an end-of-day wrap-up plus tomorrow prep.
- `GET /briefings/tomorrow` returns a prep-oriented summary of the next day.
- The conversational command path also supports phrases like `give me my morning briefing`, `wrap up my day`, and `what do i need tomorrow?`

## Google Calendar setup

Before authorizing Google, save the person's name and the Gmail address or calendar email the assistant should target:

```http
PATCH /settings
{
  "full_name": "Your Name",
  "gmail_address": "yourname@gmail.com"
}
```

If you want to use a different calendar later, update either:

- `full_name` for the person profile
- `gmail_address` for the main Google account calendar
- `google_calendar_id` for a specific secondary/shared calendar ID
- `workday_start_hour` and `workday_end_hour` for preferred working hours
- `preferred_reminder_lead_minutes` for reminder defaults
- `default_task_priority` for conversational task creation
- `home_location` and `work_location` for future location-aware planning

Example profile update:

```http
PATCH /settings
{
  "full_name": "Your Name",
  "workday_start_hour": 8,
  "workday_end_hour": 16,
  "default_task_priority": "high"
}
```

1. In Google Cloud Console, create or choose a project.
2. Enable the Google Calendar API.
3. Create an OAuth client for a web application.
4. Add this redirect URI exactly:

```text
http://127.0.0.1:8000/integrations/google-calendar/auth/callback
```

5. Set these environment variables before starting the API:

```powershell
$env:GOOGLE_CLIENT_ID="your-client-id"
$env:GOOGLE_CLIENT_SECRET="your-client-secret"
$env:GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/integrations/google-calendar/auth/callback"
$env:GOOGLE_CALENDAR_ID="primary"
```

6. Start the API.
7. Open `POST /integrations/google-calendar/auth/start` from the docs or call it from your GPT action flow.
8. Open the returned `authorization_url` in your browser and approve access.
9. Google will redirect back to your callback endpoint and store your token in the profile-scoped Google token directory under `data/google_tokens/`.

## Google Calendar endpoints

- `GET /settings`
- `PATCH /settings`
- `POST /integrations/google-calendar/auth/start`
- `POST /integrations/google-calendar/auth/start-text`
- `GET /integrations/google-calendar/connect`
- `GET /integrations/google-calendar/auth/callback`
- `GET /integrations/google-calendar/status`
- `GET /integrations/google-calendar/test`
- `GET /integrations/google-calendar/setup-status`
- `GET /integrations/google-calendar/connections`
- `POST /integrations/google-calendar/decline`
- `POST /integrations/google-calendar/reset-offer`
- `DELETE /integrations/google-calendar/status`
- `POST /integrations/google-calendar/events`
- `POST /integrations/google-calendar/create-and-sync/event`
- `POST /integrations/google-calendar/sync/events/{event_id}`
- `POST /integrations/google-calendar/create-and-sync/task`
- `POST /integrations/google-calendar/sync/tasks/{task_id}`
- `POST /integrations/google-calendar/create-and-sync/reminder`
- `POST /integrations/google-calendar/sync/reminders/{reminder_id}`

## Google Calendar connection state

- Google Calendar tokens and OAuth state are now stored per calendar profile instead of a single shared token file.
- The profile key is derived from the active `gmail_address` or `google_calendar_id`.
- `GET /integrations/google-calendar/connections` lists known connection profiles and their token locations.
- `GET /integrations/google-calendar/setup-status` gives the GPT a safe way to decide whether it should offer Google Calendar setup.
- If the user declines setup, `POST /integrations/google-calendar/decline` records that choice so the GPT does not keep asking.
- If the user later wants to connect after declining, `POST /integrations/google-calendar/reset-offer` re-enables the setup flow.
- `GET /integrations/google-calendar/test` performs a lightweight live API check against the selected calendar profile.
- The `create-and-sync` endpoints let the GPT create a local event, task, or reminder and push it to Google Calendar in one step.
- `GET /integrations/google-calendar/connect` is the browser-friendly option for non-technical users because it redirects straight into the Google OAuth flow.

## Supported Google Calendar entry types

The direct Google Calendar endpoint supports these Google event types:

- `default` for meetings and general events
- `birthday` for all-day recurring birthdays
- `focusTime`
- `outOfOffice`
- `workingLocation`

Assistant tasks can also be synced into Google Calendar as task-style scheduled events with reminders.

## Example prompts for your GPT

- "Save a note: my Wi-Fi password is in the router box."
- "Add eggs and rice to my shopping list."
- "Remind me tomorrow at 8am to call my dentist."
- "Create a bill for internet due on April 10 for 79 dollars."
- "Schedule dinner with Ana on April 6 at 7pm."
- "What bills are due this week?"
- "What reminders are overdue?"

## Connect it to a GPT

1. Run this API locally or deploy it.
2. Open your custom GPT configuration.
3. Add an action using the OpenAPI schema from `/openapi.json`.
4. Give the GPT instructions to use the API for memory and planning tasks.

A ready-to-paste instruction set is in [gpt_integration.md](D:/Projects/Personal Assistant/docs/gpt_integration.md).
An end-to-end setup guide for a Custom GPT is in [custom_gpt_setup.md](D:/Projects/Personal Assistant/docs/custom_gpt_setup.md).
A manual GPT validation checklist is in [gpt_test_plan.md](D:/Projects/Personal Assistant/docs/gpt_test_plan.md).
A short regression checklist is in [gpt_smoke_test.md](D:/Projects/Personal Assistant/docs/gpt_smoke_test.md).

Suggested GPT behavior:

- Use `notes` for long-form personal memory.
- Use `tasks` for actionable to-dos.
- Use `shopping-items` for groceries or purchase lists.
- Use `bills` for money owed with due dates.
- Use `events` for calendar-style commitments.
- Use `reminders` whenever the user asks to be alerted later.
- If the user's name or Gmail/calendar email is missing, ask for it before syncing to Google Calendar.
- If the user changes Gmail accounts or wants another calendar, update `/settings` first and then use the Google Calendar endpoints.
- When the user wants something on their Google Calendar, create a local `event` and then call the Google Calendar sync endpoint.
- When the user wants a task to show up on Calendar, create a local `task` and sync it with `/integrations/google-calendar/sync/tasks/{task_id}`.
- When the user wants a reminder pushed through Calendar, sync the reminder to Google Calendar so Google can handle popup or device notifications.

## Current behavior

This project is ready to use as the backend for a conversational GPT assistant.

The recommended interface is a Custom GPT that calls this API through actions.

This version can also push reminder behavior through Google Calendar by creating calendar events with popup reminders.

The assistant still does not directly send native phone push notifications itself. Instead, it relies on Google Calendar notifications after the event or reminder has been synced there.

## Next upgrades

- two-way Google Calendar sync
- email or Telegram notifications
- recurring reminders
- recurring bills
- user authentication
- vector search over notes
- voice interface
