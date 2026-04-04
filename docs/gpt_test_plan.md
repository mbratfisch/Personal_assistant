# GPT Test Plan

Use this checklist to validate the Custom GPT end-to-end against the assistant backend.

## Before you start

1. Start the backend:

```powershell
.\start_assistant.ps1
```

2. Confirm the GPT action is using the current public OpenAPI URL.
3. Open your Custom GPT.
4. Start with a clean chat thread if possible.

## What to verify overall

- The GPT calls the backend successfully.
- The GPT chooses the right entity type.
- The GPT uses planning endpoints when appropriate.
- The GPT asks only one clarification question when needed.
- The GPT does not keep asking about Google Calendar after a decline.
- The GPT can switch and disconnect Google Calendar intentionally.

## Test group 1: Basic capture

Prompt:
`Add milk and eggs to my shopping list`

Expected:
- GPT confirms the items were added.
- Shopping list items are stored.

Prompt:
`Remind me tomorrow at 8am to call my dentist`

Expected:
- GPT confirms the reminder.
- Reminder has the right date and time.

Prompt:
`I need to submit taxes tomorrow at 6pm`

Expected:
- GPT creates a task.
- Task appears as a task, not a note.

Prompt:
`Remember that my passport is in the blue drawer`

Expected:
- GPT stores a note.

## Test group 2: Reading and summaries

Prompt:
`What's due?`

Expected:
- GPT returns a due-style summary.
- It includes tasks, reminders, bills, and nearby events where relevant.

Prompt:
`What do I have today?`

Expected:
- GPT returns the day agenda.
- Response should reflect tasks, reminders, events, bills, conflicts, and suggested plan if available.

Prompt:
`What should I do first?`

Expected:
- GPT uses planning behavior.
- Response should reflect `best_next_action` rather than a generic summary.

## Test group 3: Editing and undo

Prompt:
`Move my reminder dentist to friday at 3pm`

Expected:
- GPT confirms the reminder was moved.

Prompt:
`Cancel my reminder dentist`

Expected:
- GPT confirms the reminder was dismissed/cancelled.

Prompt:
`Undo`

Expected:
- GPT confirms the last change was reversed.

Prompt:
`Remove milk from my shopping list`

Expected:
- GPT confirms the shopping item was removed.

## Test group 4: Ambiguity handling

Create two similar tasks first:

Prompt:
`I need to call mom tomorrow at 9am`

Prompt:
`I need to call manager tomorrow at 10am`

Then test:

Prompt:
`Cancel my task call`

Expected:
- GPT should not guess.
- GPT should ask for a more specific task name.

## Test group 5: Daily briefings

Prompt:
`Give me my morning briefing`

Expected:
- GPT returns a concise start-of-day overview.
- It includes overview, warnings, and next steps.

Prompt:
`Wrap up my day`

Expected:
- GPT returns an evening wrap-up.
- It includes unfinished items and tomorrow prep.

Prompt:
`What do I need tomorrow?`

Expected:
- GPT returns a tomorrow-focused briefing.

## Test group 6: Help behavior

Prompt:
`Help`

Expected:
- GPT gives a broad tutorial.
- It explains the major categories and gives examples.

Prompt:
`How do I add a new task?`

Expected:
- GPT explains only task creation.
- It should not dump the full tutorial.

Prompt:
`How do I connect Google Calendar?`

Expected:
- GPT explains only the calendar connection flow.

## Test group 7: Google Calendar onboarding

### Case A: first offer

Precondition:
- Google Calendar is not connected.
- No previous decline recorded.

Prompt:
`I want help managing my schedule`

Expected:
- GPT may offer Google Calendar once if setup-status says it should.

### Case B: decline

Prompt:
`No, I don't want to connect Google Calendar right now`

Expected:
- GPT records the decline.
- GPT should not keep asking in later normal chats.

### Case C: explicit connect later

Prompt:
`Let's connect with Google Calendar`

Expected:
- GPT resumes the setup flow.
- GPT asks for the Gmail/calendar email only if needed.
- GPT starts auth after settings are ready.

### Case D: switch calendar

Prompt:
`Use another Google Calendar`

Expected:
- GPT asks which Gmail or calendar email to use.
- GPT updates settings and starts the new auth flow.

### Case E: disconnect

Prompt:
`Disconnect Google Calendar`

Expected:
- GPT removes the connection and confirms it.

## Suggested test order

1. Basic capture
2. Reading and summaries
3. Editing and undo
4. Ambiguity handling
5. Daily briefings
6. Help behavior
7. Google Calendar onboarding

## Pass/fail quick guide

Pass:
- Correct item type
- Correct confirmation
- Correct planning behavior
- Clarifies ambiguity
- Honors Google Calendar decline
- Can resume connect flow later

Fail:
- Wrong entity type
- Generic or missing confirmation
- No clarification on ambiguous item names
- Keeps pushing Google Calendar after decline
- Uses broad help when the user asked about one specific feature

## Notes template

Copy this when testing:

```text
Test:
Prompt:
Expected:
Actual:
Pass/Fail:
Notes:
```
