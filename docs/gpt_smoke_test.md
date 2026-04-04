# GPT Smoke Test

Use this short checklist for a fast end-to-end GPT regression test.

## Before you start

1. Run:

```powershell
.\start_assistant.ps1
```

2. Confirm the Custom GPT action uses the current public OpenAPI URL.
3. Start a fresh GPT chat if possible.

## Smoke test prompts

### 1. Shopping list

Prompt:
`Add milk and eggs to my shopping list`

Pass if:
- GPT confirms both items were added.

### 2. Reminder

Prompt:
`Remind me tomorrow at 8am to call my dentist`

Pass if:
- GPT creates a reminder with the right time.

### 3. Task

Prompt:
`I need to submit taxes tomorrow at 6pm`

Pass if:
- GPT creates a task, not a note.

### 4. Due summary

Prompt:
`What's due?`

Pass if:
- GPT gives a useful overview of due items.
- GPT does not dump raw JSON or endpoint language.

### 5. Day planning

Prompt:
`What should I do first?`

Pass if:
- GPT gives a next-action style answer, not just a list dump.
- GPT sounds like a planner, not an API client.

### 5b. Relative date sanity

Prompt:
`What do I have tomorrow?`

Pass if:
- GPT uses the right local date.
- GPT mentions the exact weekday/date once.
- GPT reflects Google Calendar events returned by `/agenda/tomorrow`.

### 6. Briefing

Prompt:
`Give me my morning briefing`

Pass if:
- GPT returns an overview with warnings or next steps.

### 7. Edit

Prompt:
`Move my reminder dentist to friday at 3pm`

Pass if:
- GPT confirms the reminder was moved.

### 8. Undo

Prompt:
`Undo`

Pass if:
- GPT confirms the last action was reversed.

### 9. Help behavior

Prompt:
`How do I add a new task?`

Pass if:
- GPT explains only task creation.
- It should not dump the full assistant tutorial.

### 10. Google Calendar onboarding behavior

Prompt:
`Let's connect with Google Calendar`

Pass if:
- GPT begins the Google Calendar setup flow.
- GPT asks only the next missing question.

### 11. Plain reminder vs synced reminder

Prompt:
`Remind me tomorrow at 8am to call my dentist`

Pass if:
- GPT treats it as a normal reminder.
- GPT does not claim Google sync unless asked.

Prompt:
`Create a reminder for tomorrow at 8am to call my dentist and put it on Google Calendar`

Pass if:
- GPT confirms both the reminder details and that it was synced to Google Calendar.
- Repeating the same prompt should not create another duplicate synced item.

## Quick fail signs

- Wrong entity type
- No confirmation after successful action
- No clarification for ambiguous matches
- No undo behavior
- Help answer is too broad for a specific help question
- Google Calendar flow is pushy or repetitive after a decline
- Relative dates use the wrong day
- Replies sound like tool traces instead of a personal assistant
