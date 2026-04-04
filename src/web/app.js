const conversation = document.getElementById("conversation");
const commandForm = document.getElementById("command-form");
const commandInput = document.getElementById("command-input");
const refreshButton = document.getElementById("refresh-summary");
const calendarTestButton = document.getElementById("calendar-test-button");
const calendarSyncButton = document.getElementById("calendar-sync-button");

const endpoints = {
  summary: "/summary",
  todayAgenda: "/agenda/today",
  tomorrowAgenda: "/agenda/tomorrow",
  command: "/assistant/command",
  tasks: "/tasks?status=active",
  reminders: "/reminders?status=pending",
  bills: "/bills?status=active",
  shopping: "/shopping-items?status=active",
  calendarStatus: "/integrations/google-calendar/status",
  calendarTest: "/integrations/google-calendar/test",
  calendarConnectInfo: "/integrations/google-calendar/connect-info",
  calendarSyncUpcoming: "/integrations/google-calendar/sync/upcoming?days=14",
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "No date";
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function formatDay(value) {
  if (!value) return "Unknown day";
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
    }).format(new Date(`${value}T12:00:00`));
  } catch {
    return value;
  }
}

function addBubble(role, content) {
  const article = document.createElement("article");
  article.className = `bubble bubble-${role}`;
  article.innerHTML = `<p>${escapeHtml(content)}</p>`;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
}

function renderSummary(summary) {
  document.getElementById("metric-tasks").textContent = summary.due_tasks_today.length;
  document.getElementById("metric-bills").textContent = summary.due_bills_this_week.length;
  document.getElementById("metric-reminders").textContent = summary.due_reminders.length;
  document.getElementById("summary-timestamp").textContent = `Updated ${formatDate(summary.generated_at)}`;

  const tiles = [
    ["Overdue Tasks", summary.overdue_tasks.length],
    ["Tasks Today", summary.due_tasks_today.length],
    ["Bills This Week", summary.due_bills_this_week.length],
    ["Overdue Bills", summary.overdue_bills.length],
    ["Due Reminders", summary.due_reminders.length],
    ["Upcoming Events", summary.upcoming_events.length],
  ];

  document.getElementById("summary-grid").innerHTML = tiles
    .map(
      ([label, value]) => `
        <article class="summary-tile">
          <span class="metric-label">${escapeHtml(label)}</span>
          <strong>${value}</strong>
        </article>
      `
    )
    .join("");
}

function renderList(targetId, items, type) {
  const target = document.getElementById(targetId);
  if (!items.length) {
    target.innerHTML = `<div class="list-empty">Nothing active here right now.</div>`;
    return;
  }

  target.innerHTML = items
    .map((item) => {
      const title = item.title || item.name;
      const secondary =
        item.due_at ||
        item.remind_at ||
        item.starts_at ||
        `${item.amount ? `$${item.amount}` : ""}${item.quantity ? `Qty ${item.quantity}` : ""}`;
      return `
        <article class="list-item">
          <div class="list-item-header">
            <span class="list-item-title">${escapeHtml(title)}</span>
            <span class="status-pill">${escapeHtml(type)}</span>
          </div>
          <div class="item-meta">${escapeHtml(formatDate(secondary))}</div>
        </article>
      `;
    })
    .join("");
}

function renderAgenda(targetId, timestampId, agenda, emptyMessage) {
  document.getElementById(timestampId).textContent = formatDay(agenda.date);
  const target = document.getElementById(targetId);
  const sections = [
    { title: "Events", items: agenda.events, field: "starts_at" },
    { title: "Reminders", items: agenda.reminders, field: "remind_at" },
    { title: "Tasks", items: agenda.tasks, field: "due_at" },
  ].filter((section) => section.items.length);

  const conflictHtml = agenda.conflicts.slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const nextAction = agenda.best_next_action
    ? `<div class="agenda-callout"><strong>Best next action:</strong> ${escapeHtml(agenda.best_next_action)}</div>`
    : "";

  if (!sections.length && !conflictHtml && !nextAction) {
    target.innerHTML = `<div class="list-empty">${escapeHtml(emptyMessage)}</div>`;
    return;
  }

  target.innerHTML = `
    ${nextAction}
    ${sections
      .map(
        (section) => `
          <div class="agenda-section">
            <div class="agenda-section-title">${escapeHtml(section.title)}</div>
            <div class="list-stack">
              ${section.items
                .slice(0, 4)
                .map(
                  (item) => `
                    <article class="list-item">
                      <div class="list-item-header">
                        <span class="list-item-title">${escapeHtml(item.title || item.name)}</span>
                      </div>
                      <div class="item-meta">${escapeHtml(formatDate(item[section.field]))}</div>
                    </article>
                  `
                )
                .join("")}
            </div>
          </div>
        `
      )
      .join("")}
    ${conflictHtml ? `<div class="agenda-section"><div class="agenda-section-title">Watchouts</div><ul class="conflict-list">${conflictHtml}</ul></div>` : ""}
  `;
}

function renderCalendarStatus(status) {
  const statusPill = document.getElementById("calendar-status-pill");
  const statusText = document.getElementById("calendar-status-text");
  const email = document.getElementById("calendar-email");
  const profile = document.getElementById("calendar-profile");

  statusPill.textContent = status.connected ? "Connected" : "Not connected";
  statusPill.classList.toggle("status-pill-off", !status.connected);
  statusText.textContent = status.connected
    ? "Google Calendar is connected. You can test the live link, run an upcoming pull sync, or jump into the hosted connect flow."
    : "Google Calendar is not connected yet. Use the connect button to finish the hosted OAuth flow.";
  email.textContent = status.gmail_address || "Not set";
  profile.textContent = status.profile_key || "primary";
}

function renderSyncResult(result, label = "Sync complete") {
  const target = document.getElementById("calendar-sync-result");
  target.textContent = `${label}: ${result.created} created, ${result.updated} updated, ${result.unchanged} unchanged.`;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
    },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }

  return response.json();
}

async function refreshDashboard() {
  const [summary, tasks, reminders, bills, shopping, calendarStatus, connectInfo, todayAgenda, tomorrowAgenda] = await Promise.all([
    fetchJson(endpoints.summary),
    fetchJson(endpoints.tasks),
    fetchJson(endpoints.reminders),
    fetchJson(endpoints.bills),
    fetchJson(endpoints.shopping),
    fetchJson(endpoints.calendarStatus),
    fetchJson(endpoints.calendarConnectInfo),
    fetchJson(endpoints.todayAgenda),
    fetchJson(endpoints.tomorrowAgenda),
  ]);

  renderSummary(summary);
  renderList("tasks-list", tasks, "Task");
  renderList("reminders-list", reminders, "Reminder");
  renderList("bills-list", bills, "Bill");
  renderList("shopping-list", shopping, "Shopping");
  renderCalendarStatus(calendarStatus);
  document.getElementById("calendar-connect-link").setAttribute("href", connectInfo.connect_url);
  renderAgenda("today-agenda", "today-agenda-date", todayAgenda, "Nothing is scheduled for today.");
  renderAgenda("tomorrow-agenda", "tomorrow-agenda-date", tomorrowAgenda, "Nothing is scheduled for tomorrow.");
}

async function submitCommand(event) {
  event.preventDefault();
  const text = commandInput.value.trim();
  if (!text) return;

  addBubble("user", text);
  commandInput.value = "";

  try {
    const result = await fetchJson(endpoints.command, {
      method: "POST",
      body: JSON.stringify({ text }),
    });

    const details = [];
    if (result.created_type) {
      details.push(`Created ${result.created_type}`);
    }
    if (result.data?.items?.length) {
      details.push(`${result.data.items.length} item(s)`);
    }

    addBubble("assistant", details.length ? `${result.message} ${details.join(" · ")}` : result.message);
    await refreshDashboard();
  } catch (error) {
    addBubble("assistant", `Something went wrong: ${error.message}`);
  }
}

async function testCalendarConnection() {
  try {
    const result = await fetchJson(endpoints.calendarTest);
    renderCalendarStatus(result);
    addBubble(
      "assistant",
      result.connected
        ? `Google Calendar is connected for ${result.gmail_address || result.calendar_id}.`
        : "Google Calendar is not connected right now."
    );
  } catch (error) {
    addBubble("assistant", `Calendar test failed: ${error.message}`);
  }
}

async function syncUpcomingWindow() {
  try {
    const result = await fetchJson(endpoints.calendarSyncUpcoming, {
      method: "POST",
    });
    renderSyncResult(result);
    addBubble(
      "assistant",
      `Upcoming Google events synced: ${result.created} created, ${result.updated} updated, ${result.unchanged} unchanged.`
    );
    await refreshDashboard();
  } catch (error) {
    addBubble("assistant", `Upcoming sync failed: ${error.message}`);
  }
}

commandForm.addEventListener("submit", submitCommand);
refreshButton.addEventListener("click", refreshDashboard);
calendarTestButton.addEventListener("click", testCalendarConnection);
calendarSyncButton.addEventListener("click", syncUpcomingWindow);

document.querySelectorAll(".suggestion-chip").forEach((button) => {
  button.addEventListener("click", () => {
    commandInput.value = button.dataset.command || "";
    commandInput.focus();
  });
});

refreshDashboard().catch((error) => {
  addBubble("assistant", `Dashboard load failed: ${error.message}`);
});
