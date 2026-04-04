# Custom GPT Setup

This project is meant to use a GPT as the main interface.

## What the GPT will do

The GPT should be the user-facing assistant.

It will:

- receive the user's natural conversation
- decide which API action to call
- create and update notes, tasks, reminders, bills, events, and shopping items
- read summaries and current lists
- optionally use Google Calendar later

## Run the backend

Start the API locally:

```powershell
.\.venv\Scripts\python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8000
```

Important local URLs:

- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`
- API docs: `http://127.0.0.1:8000/docs`

## Create the Custom GPT

1. Open ChatGPT.
2. Create a new Custom GPT.
3. In the GPT configuration, add your instructions.
4. In `Actions`, import the OpenAPI schema from:

```text
http://127.0.0.1:8000/openapi.json
```

5. Save the GPT.

## Recommended GPT behavior

Use the GPT instructions from [gpt_integration.md](D:/Projects/Personal Assistant/docs/gpt_integration.md).

That file already tells the GPT:

- when to use notes, tasks, reminders, bills, events, and shopping items
- when to use `/assistant/command`
- when to use `/summary`
- when to ask for missing user settings
- how to behave when Google Calendar is needed later

## Best action strategy

For this project, the best GPT action strategy is:

1. Use `POST /assistant/command` for common conversational requests.
2. Use `GET /summary` when the user asks for an overview.
3. Use direct endpoints when the user is giving explicit structured instructions or when the GPT needs precise updates.

Good examples for `/assistant/command`:

- "add milk and eggs to my shopping list"
- "remind me tomorrow at 8am to call my dentist"
- "i need to submit taxes tomorrow at 6pm"
- "pay internet"
- "what's due"

Good examples for direct endpoints:

- updating a specific task with a known ID
- editing settings
- syncing with Google Calendar

## Important local limitation

If the GPT must call the API from outside your own machine, `127.0.0.1` will not be reachable.

In that case you will need to expose the API with a public HTTPS URL later, for example using:

- Cloudflare Tunnel
- ngrok
- a deployed server

For now, this setup is still useful for local development and testing.

## Recommended next product phase

The next best GPT-focused phase is to improve the action metadata for the OpenAPI schema, so the GPT chooses the right endpoints more reliably and with less prompt guidance.
