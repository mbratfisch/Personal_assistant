# Personal Accounts Firestore Schema

This app is now set up to support personal accounts by scoping all backend storage to a user id.

## Auth model

- Frontend signs users in and sends a Firebase ID token as:
  - `Authorization: Bearer <firebase_id_token>`
- Backend verifies the token with the Firebase Admin SDK.
- Verified token `uid` becomes the canonical backend `user_id`.
- If invitation mode is enabled, a newly authenticated user must redeem an invitation code once before protected app routes are available.

## Invitation gating model

Optional production gate:

- `APP_INVITATION_MODE=required`
- `APP_INVITATION_CODES=CODE1,CODE2,CODE3`

Behavior:

- A user may authenticate with Firebase first.
- The backend then checks whether that user has already redeemed an invitation code.
- If not, protected app routes return `403 Invitation code required for this account.`
- The frontend should call:
  - `GET /auth/invitation`
  - `POST /auth/invitation/redeem`

This lets you strictly control the first wave of users without exposing the whole product to anyone who can create a Firebase account.

Firestore storage for invite tracking:

- `personal_assistant_invitations/allowed_users`
- `personal_assistant_invitations/codes`

The first document maps backend user ids to the invite they redeemed.
The second document maps invite codes to the user who consumed them.

Admin controls:

- Admin access can be granted with:
  - `APP_ADMIN_UIDS=<comma separated firebase uids>`
  - or `APP_ADMIN_EMAILS=<comma separated emails>`
- Admin API endpoints:
  - `GET /admin/overview`
  - `GET /admin/users`
  - `GET /admin/invitations`
  - `POST /admin/invitations`
  - `PATCH /admin/invitations/{code}`
  - `DELETE /admin/invitations/{code}`

Invitation code storage now supports both:

- static env-based codes from `APP_INVITATION_CODES`
- dynamically created codes stored in the invitation registry

Dynamic invitation codes can be enabled/disabled and given a max use count.

## Telegram integration

Telegram can now be linked per user and routed into the same assistant engine used by the web app.

Required env for a real bot:

- `TELEGRAM_BOT_TOKEN=<bot token from BotFather>`
- `TELEGRAM_BOT_USERNAME=<bot username without @>`

Optional webhook hardening:

- `TELEGRAM_WEBHOOK_SECRET=<random secret token>`

Telegram API endpoints:

- `GET /integrations/telegram/status`
- `POST /integrations/telegram/link`
- `DELETE /integrations/telegram/status`
- `POST /integrations/telegram/webhook`

Telegram registry storage:

- Firestore: `personal_assistant_telegram/pending_links`
- Firestore: `personal_assistant_telegram/chat_links`
- Firestore: `personal_assistant_telegram/user_links`

This keeps:

- one-time account link codes
- Telegram chat-to-user mappings
- per-user Telegram connection metadata

## Storage model

The current Firestore shape is one document per user inside the configured collection.

Default collection:

- `personal_assistant`

Per-user assistant data:

- `personal_assistant/{user_id}`

That document stores the full `DatabaseModel`, including:

- settings
- notes
- tasks
- shopping_items
- bills
- events
- reminders
- action_history

## Google Calendar data per user

Google Calendar data is also scoped to the same Firestore user document.

Token documents:

- `personal_assistant/{user_id}/google_calendar_tokens/{profile_key}`

Internal connection metadata:

- `personal_assistant/{user_id}/_internal/google_calendar_connections`

This keeps each user's Google Calendar tokens and profile metadata isolated from every other user.

## Local JSON fallback

When running with JSON storage, the same personal-account idea is preserved under per-user folders:

- `data/users/{user_id}/assistant_db.json`
- `data/google_tokens/{user_id}/...`
- `data/google_oauth_states/{user_id}/...`
- `data/google_calendar_connections/{user_id}/google_calendar_connections.json`

## Recommended production env

- `APP_STORAGE_BACKEND=firestore`
- `APP_AUTH_MODE=required`
- `APP_INVITATION_MODE=required` if you want invite-only access
- `APP_INVITATION_CODES=<comma separated codes>`
- `FIREBASE_PROJECT_ID=<your project id>` if needed

## Notes

- Public routes such as `/`, `/docs`, `/openapi.json`, `/app`, and the Google OAuth callback can still load without a bearer token.
- Protected API routes use request-scoped user storage.
- Invitation helper routes:
  - `GET /auth/me`
  - `GET /auth/invitation`
  - `POST /auth/invitation/redeem`
- For a real multi-user frontend, prefer a Vercel app or another frontend that authenticates the user first, then calls the backend with a Firebase ID token.
