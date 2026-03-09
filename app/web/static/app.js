const state = {
  me: null,
  families: [],
  familyId: null,
  currentRole: null,
  members: [],
  tasks: [],
  specialTaskTemplates: [],
  availableSpecialTasks: [],
  events: [],
  rewards: [],
  redemptions: [],
  selectedRewardContribution: null,
  pointsBalances: [],
  pointsHistory: [],
  selectedTaskId: null,
  selectedSpecialTaskTemplateId: null,
  selectedMemberId: null,
  selectedRewardId: null,
  selectedPointsUserId: null,
  haSettings: null,
  haUserConfigs: [],
  channelStatus: null,
  tasksSort: "updated_desc",
  specialTasksSort: "updated_desc",
  taskEditorDirty: false,
  specialTaskEditorDirty: false,
  taskEditorInitialSnapshot: "",
  specialTaskEditorInitialSnapshot: "",
};

const authPanel = document.getElementById("auth-panel");
const appPanel = document.getElementById("app-panel");
const familySelect = document.getElementById("family-select");
const userInfo = document.getElementById("user-info");
const logOutput = document.getElementById("log-output");
const inlineEditorSectionIds = [
  "member-editor-section",
  "task-editor-section",
  "special-task-editor-section",
  "reward-editor-section",
  "points-adjust-section",
];
const inlineEditorHomes = {};
const TASK_REMINDER_LABELS = {
  15: "15 Min",
  30: "30 Min",
  60: "1 Stunde",
  120: "2 Stunden",
  1440: "1 Tag",
  2880: "2 Tage",
};
const WEEKDAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const DAILY_REMINDER_OFFSETS = [15, 30, 60, 120];
const ALL_REMINDER_OFFSETS = [15, 30, 60, 120, 1440, 2880];
const LOG_MAX_LINES = 1200;
const LIVE_REFRESH_DEBOUNCE_MS = 350;
const LIVE_RECONNECT_BASE_MS = 1000;
const LIVE_RECONNECT_MAX_MS = 15000;

let liveEventSource = null;
let liveReconnectTimer = null;
let liveRefreshTimer = null;
let liveRefreshInFlight = false;
let liveRefreshPending = false;
let liveReconnectDelayMs = LIVE_RECONNECT_BASE_MS;
let liveShouldRun = false;
let liveConnected = false;
let liveFamilyId = null;
let liveCursor = 0;
let specialTaskRefreshTimer = null;

function byId(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHtmlText(value, fallback = "-") {
  const text = String(value ?? "").trim();
  if (!text) return escapeHtml(fallback);
  return escapeHtml(text);
}

function log(message, data = null) {
  const line = `[${new Date().toISOString()}] ${message}`;
  const nextText = `${line}\n${data ? JSON.stringify(data, null, 2) : ""}\n\n${logOutput.textContent}`;
  const lines = nextText.split("\n");
  logOutput.textContent = lines.slice(0, LOG_MAX_LINES).join("\n");
}

function toggleHidden(id, hidden) {
  const element = byId(id);
  if (element) element.classList.toggle("hidden", Boolean(hidden));
}

function setInvalid(input, invalid) {
  if (!input) return;
  input.classList.toggle("invalid", Boolean(invalid));
}

function clearInvalid(ids) {
  ids.forEach((id) => setInvalid(byId(id), false));
}

function isValidEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

function setPasswordInputVisibility(inputIds, visible) {
  inputIds.forEach((id) => {
    const input = byId(id);
    if (!input) return;
    input.type = visible ? "text" : "password";
  });
}

function toIsoOrNull(datetimeLocal) {
  if (!datetimeLocal) return null;
  return new Date(datetimeLocal).toISOString();
}

function toLocalIsoNoTimezoneOrNull(datetimeLocal) {
  if (!datetimeLocal) return null;
  const value = String(datetimeLocal).trim();
  if (!value) return null;
  return value.length === 16 ? `${value}:00` : value;
}

function toDatetimeLocalValue(isoString) {
  if (!isoString) return "";
  const date = new Date(isoString);
  const timezoneOffset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - timezoneOffset).toISOString().slice(0, 16);
}

function toTimeValueFromIso(isoString) {
  if (!isoString) return "18:00";
  const localValue = toDatetimeLocalValue(isoString);
  return localValue ? localValue.slice(11, 16) : "18:00";
}

function fmtDate(value) {
  if (!value) return "-";
  try {
    const date = new Date(value);
    const datePart = date.toLocaleDateString("de-DE");
    const timePart = date.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    return `${datePart} ${timePart}`;
  } catch (_) {
    return value;
  }
}

function roleLabel(role) {
  const map = { admin: "Admin", parent: "Eltern", child: "Kind" };
  return map[role] || role;
}

function statusLabel(status) {
  const map = {
    open: "offen",
    submitted: "wartet auf Bestätigung",
    missed_submitted: "verpasst (wartet auf Entscheidung)",
    approved: "bestätigt",
    rejected: "abgelehnt",
    pending: "offen",
  };
  return map[status] || status;
}

function recurrenceLabel(recurrenceType) {
  const map = {
    none: "einmalig",
    daily: "täglich",
    weekly: "wöchentlich",
    monthly: "monatlich",
  };
  return map[recurrenceType] || recurrenceType;
}

function reminderOffsetLabel(minutes) {
  return TASK_REMINDER_LABELS[minutes] || `${minutes} Min`;
}

function reminderOffsetsText(offsets) {
  if (!Array.isArray(offsets) || offsets.length === 0) return "keine";
  const normalized = offsets
    .map((entry) => Number(entry))
    .filter((entry) => Number.isFinite(entry))
    .sort((a, b) => a - b);
  return normalized.map((entry) => reminderOffsetLabel(entry)).join(", ");
}

function getSelectedReminderOffsets(containerId) {
  const container = byId(containerId);
  if (!container) return [];
  return Array.from(container.querySelectorAll("input[type=\"checkbox\"]:checked"))
    .map((checkbox) => Number(checkbox.value))
    .filter((value) => Number.isFinite(value))
    .sort((a, b) => a - b);
}

function setSelectedReminderOffsets(containerId, offsets = []) {
  const container = byId(containerId);
  if (!container) return;
  const selected = new Set((offsets || []).map((entry) => Number(entry)));
  Array.from(container.querySelectorAll("input[type=\"checkbox\"]")).forEach((checkbox) => {
    checkbox.checked = selected.has(Number(checkbox.value));
  });
}

function applyReminderOptionRestrictions(containerId, allowedOffsets, clearHidden = true) {
  const container = byId(containerId);
  if (!container) return;
  const allowed = new Set((allowedOffsets || []).map((entry) => Number(entry)));
  Array.from(container.querySelectorAll("input[type=\"checkbox\"]")).forEach((checkbox) => {
    const value = Number(checkbox.value);
    const visible = allowed.has(value);
    if (!visible && clearHidden) checkbox.checked = false;
    checkbox.disabled = !visible;
    const chip = checkbox.closest(".reminder-option");
    if (chip) chip.classList.toggle("hidden", !visible);
  });
}

function getSelectedWeekdays(containerId) {
  const container = byId(containerId);
  if (!container) return [];
  return Array.from(container.querySelectorAll("input[type=\"checkbox\"]:checked"))
    .map((checkbox) => Number(checkbox.value))
    .filter((value) => Number.isInteger(value) && value >= 0 && value <= 6)
    .sort((a, b) => a - b);
}

function setSelectedWeekdays(containerId, weekdays = []) {
  const container = byId(containerId);
  if (!container) return;
  const selected = new Set((weekdays || []).map((entry) => Number(entry)));
  Array.from(container.querySelectorAll("input[type=\"checkbox\"]")).forEach((checkbox) => {
    checkbox.checked = selected.has(Number(checkbox.value));
  });
}

function weekdaysText(weekdays = []) {
  const normalized = Array.from(new Set((weekdays || []).map((entry) => Number(entry))))
    .filter((value) => Number.isInteger(value) && value >= 0 && value <= 6)
    .sort((a, b) => a - b);
  if (!normalized.length) return "Mo-So";
  if (normalized.length === 7) return "Mo-So";
  return normalized.map((weekday) => WEEKDAY_LABELS[weekday]).join(", ");
}

function weekdayFromDate(date) {
  const jsDay = date.getDay();
  return jsDay === 0 ? 6 : jsDay - 1;
}

function toLocalIsoFromDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}:00`;
}

function buildNextDailyDueIso(timeValue, weekdays = []) {
  if (!timeValue) return null;
  const [hourStr, minuteStr] = String(timeValue).split(":");
  const hours = Number(hourStr);
  const minutes = Number(minuteStr);
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;

  const allowed = new Set(
    (weekdays || []).map((entry) => Number(entry)).filter((value) => Number.isInteger(value) && value >= 0 && value <= 6)
  );
  if (!allowed.size) return null;

  const now = new Date();
  for (let offset = 0; offset < 14; offset += 1) {
    const candidate = new Date(now);
    candidate.setSeconds(0, 0);
    candidate.setDate(now.getDate() + offset);
    candidate.setHours(hours, minutes, 0, 0);
    if (!allowed.has(weekdayFromDate(candidate))) continue;
    if (candidate <= now) continue;
    return toLocalIsoFromDate(candidate);
  }
  return null;
}

function buildNextWeeklyDueIso(weekdayValue, timeValue) {
  const weekday = Number(weekdayValue);
  if (!Number.isInteger(weekday) || weekday < 0 || weekday > 6 || !timeValue) return null;
  const [hourStr, minuteStr] = String(timeValue).split(":");
  const hours = Number(hourStr);
  const minutes = Number(minuteStr);
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;

  const now = new Date();
  const candidate = new Date(now);
  candidate.setSeconds(0, 0);
  candidate.setHours(hours, minutes, 0, 0);

  const todayWeekday = weekdayFromDate(candidate);
  const delta = (weekday - todayWeekday + 7) % 7;
  candidate.setDate(candidate.getDate() + delta);
  if (candidate <= now) {
    candidate.setDate(candidate.getDate() + 7);
  }
  return toLocalIsoFromDate(candidate);
}

function specialIntervalLabel(intervalType) {
  const map = {
    daily: "täglich",
    weekly: "wöchentlich",
    monthly: "monatlich",
  };
  return map[intervalType] || intervalType;
}

function parseTimeValueToParts(timeValue) {
  if (!timeValue) return null;
  const parts = String(timeValue).split(":");
  if (parts.length !== 2) return null;
  const hours = Number(parts[0]);
  const minutes = Number(parts[1]);
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
  return { hours, minutes };
}

function normalizeTimeValueOrNull(timeValue) {
  const parsed = parseTimeValueToParts(timeValue);
  if (!parsed) return null;
  return `${String(parsed.hours).padStart(2, "0")}:${String(parsed.minutes).padStart(2, "0")}`;
}

function specialTaskScheduleMeta(entry) {
  if (entry.interval_type !== "daily") {
    return `Intervall: ${specialIntervalLabel(entry.interval_type)}`;
  }
  const weekdays = weekdaysText(entry.active_weekdays && entry.active_weekdays.length ? entry.active_weekdays : [0, 1, 2, 3, 4, 5, 6]);
  const dueText = entry.due_time_hhmm ? `fällig bis ${entry.due_time_hhmm}` : "ohne feste Uhrzeit";
  return `Intervall: täglich • Tage: ${weekdays} • ${dueText}`;
}

function isSpecialTaskAvailableNow(entry) {
  if (entry.interval_type !== "daily") return true;
  const weekdays = (entry.active_weekdays && entry.active_weekdays.length) ? entry.active_weekdays : [0, 1, 2, 3, 4, 5, 6];
  const now = new Date();
  const weekday = weekdayFromDate(now);
  if (!weekdays.includes(weekday)) return false;
  const dueParts = parseTimeValueToParts(entry.due_time_hhmm);
  if (!dueParts) return true;
  const dueAt = new Date(now);
  dueAt.setSeconds(0, 0);
  dueAt.setHours(dueParts.hours, dueParts.minutes, 0, 0);
  return now <= dueAt;
}

function syncSpecialTaskCreateTimingUI() {
  const daily = byId("special-task-interval").value === "daily";
  toggleHidden("special-task-due-time-row", !daily);
  toggleHidden("special-task-weekdays-row", !daily);
  if (!daily) {
    setInvalid(byId("special-task-due-time"), false);
    setInvalid(byId("special-task-weekdays-row"), false);
  }
}

function syncSpecialTaskEditorTimingUI() {
  const daily = byId("special-task-editor-interval").value === "daily";
  toggleHidden("special-task-editor-due-time-row", !daily);
  toggleHidden("special-task-editor-weekdays-row", !daily);
  if (!daily) {
    setInvalid(byId("special-task-editor-due-time"), false);
    setInvalid(byId("special-task-editor-weekdays-row"), false);
  }
}

function pointsSourceLabel(sourceType) {
  const map = {
    task_approval: "Aufgabe bestätigt",
    reward_redemption: "Belohnung eingelöst",
    reward_contribution: "Belohnungsbeitrag",
    task_penalty: "Minuspunkte Aufgabe",
    manual_adjustment: "Manuelle Anpassung",
  };
  return map[sourceType] || sourceType;
}

function pointsDeltaLabel(delta) {
  return delta > 0 ? `+${delta}` : String(delta);
}

function taskDueText(task) {
  if (task.due_at && task.recurrence_type === "daily") return `Nächste Fälligkeit: ${fmtDate(task.due_at)}`;
  if (task.due_at) return fmtDate(task.due_at);
  if (task.recurrence_type === "weekly") return "Diese Woche frei planbar";
  return "kein fester Zeitpunkt";
}

function taskRecurrenceText(task) {
  const base = recurrenceLabel(task.recurrence_type);
  if (task.recurrence_type === "daily") {
    return `${base} (${weekdaysText(task.active_weekdays || [])})`;
  }
  return base;
}

function taskScheduleMeta(task) {
  if (task.recurrence_type === "daily") {
    return `Wiederholung: ${taskRecurrenceText(task)} • ${taskDueText(task)}`;
  }
  return `Wiederholung: ${taskRecurrenceText(task)} • Fällig: ${taskDueText(task)}`;
}

function taskPenaltyText(task) {
  if (!task.penalty_enabled || Number(task.penalty_points || 0) <= 0) return "Minuspunkte: aus";
  return `Minuspunkte: -${task.penalty_points} bei verpasster Fälligkeit`;
}

function taskRecurrenceSortWeight(task) {
  const map = { none: 0, daily: 1, weekly: 2, monthly: 3 };
  return map[task?.recurrence_type] ?? 99;
}

function specialTaskIntervalSortWeight(entry) {
  const map = { daily: 0, weekly: 1, monthly: 2 };
  return map[entry?.interval_type] ?? 99;
}

function sortManagerTasks(list) {
  const selected = state.tasksSort || "updated_desc";
  const items = [...(list || [])];
  if (selected === "name_asc") {
    return items.sort((a, b) => String(a.title || "").localeCompare(String(b.title || ""), "de"));
  }
  if (selected === "recurrence") {
    return items.sort((a, b) =>
      taskRecurrenceSortWeight(a) - taskRecurrenceSortWeight(b)
      || String(a.title || "").localeCompare(String(b.title || ""), "de")
    );
  }
  return items.sort((a, b) =>
    new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime()
  );
}

function sortSpecialTaskTemplates(list) {
  const selected = state.specialTasksSort || "updated_desc";
  const items = [...(list || [])];
  if (selected === "name_asc") {
    return items.sort((a, b) => String(a.title || "").localeCompare(String(b.title || ""), "de"));
  }
  if (selected === "interval") {
    return items.sort((a, b) =>
      specialTaskIntervalSortWeight(a) - specialTaskIntervalSortWeight(b)
      || String(a.title || "").localeCompare(String(b.title || ""), "de")
    );
  }
  return items.sort((a, b) =>
    new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime()
  );
}

function taskEditorSnapshot() {
  return JSON.stringify({
    title: byId("task-editor-title")?.value || "",
    description: byId("task-editor-description")?.value || "",
    assignee_id: byId("task-editor-assignee")?.value || "",
    points: byId("task-editor-points")?.value || "",
    recurrence_type: byId("task-editor-recurrence")?.value || "",
    due_mode: byId("task-editor-due-mode")?.value || "",
    due: byId("task-editor-due")?.value || "",
    daily_time: byId("task-editor-daily-time")?.value || "",
    weekly_day: byId("task-editor-weekly-day")?.value || "",
    weekly_time: byId("task-editor-weekly-time")?.value || "",
    status: byId("task-editor-status")?.value || "",
    active: byId("task-editor-active")?.value || "",
    always_submittable: byId("task-editor-always-submittable")?.value || "",
    penalty_enabled: byId("task-editor-penalty-enabled")?.value || "",
    penalty_points: byId("task-editor-penalty-points")?.value || "",
    weekdays: getSelectedWeekdays("task-editor-weekdays"),
    reminders: getSelectedReminderOffsets("task-editor-reminder-options"),
  });
}

function specialTaskEditorSnapshot() {
  return JSON.stringify({
    title: byId("special-task-editor-title")?.value || "",
    description: byId("special-task-editor-description")?.value || "",
    points: byId("special-task-editor-points")?.value || "",
    interval_type: byId("special-task-editor-interval")?.value || "",
    due_time_hhmm: byId("special-task-editor-due-time")?.value || "",
    max_claims_per_interval: byId("special-task-editor-limit")?.value || "",
    is_active: byId("special-task-editor-active")?.value || "",
    weekdays: getSelectedWeekdays("special-task-editor-weekdays"),
  });
}

function updateTaskEditButtons() {
  const isOpen = isSectionOpen("task-editor-section");
  document.querySelectorAll("#tasks-manager-cards button[data-task-action=\"edit\"]").forEach((button) => {
    const taskId = Number(button.dataset.taskId || 0);
    if (!isOpen || !taskId || taskId !== state.selectedTaskId) {
      button.textContent = "Bearbeiten";
      return;
    }
    button.textContent = state.taskEditorDirty ? "Speichern" : "Schließen";
  });
}

function updateSpecialTaskEditButtons() {
  const isOpen = isSectionOpen("special-task-editor-section");
  document.querySelectorAll("#special-task-manager-cards button[data-special-task-action=\"edit\"]").forEach((button) => {
    const templateId = Number(button.dataset.specialTaskId || 0);
    if (!isOpen || !templateId || templateId !== state.selectedSpecialTaskTemplateId) {
      button.textContent = "Bearbeiten";
      return;
    }
    button.textContent = state.specialTaskEditorDirty ? "Speichern" : "Schließen";
  });
}

function syncTaskEditorDirtyState() {
  if (!state.selectedTaskId || !isSectionOpen("task-editor-section")) {
    state.taskEditorDirty = false;
  } else {
    state.taskEditorDirty = taskEditorSnapshot() !== state.taskEditorInitialSnapshot;
  }
  updateTaskEditButtons();
}

function syncSpecialTaskEditorDirtyState() {
  if (!state.selectedSpecialTaskTemplateId || !isSectionOpen("special-task-editor-section")) {
    state.specialTaskEditorDirty = false;
  } else {
    state.specialTaskEditorDirty = specialTaskEditorSnapshot() !== state.specialTaskEditorInitialSnapshot;
  }
  updateSpecialTaskEditButtons();
}

function parseDateSafe(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function startOfDay(date) {
  const value = new Date(date);
  value.setHours(0, 0, 0, 0);
  return value;
}

function isSameCalendarDay(first, second) {
  return first.getFullYear() === second.getFullYear()
    && first.getMonth() === second.getMonth()
    && first.getDate() === second.getDate();
}

function childTaskDueText(task) {
  if (task.special_template_id && !task.due_at) {
    return "Heute";
  }
  const dueDate = parseDateSafe(task.due_at);
  if (!dueDate) return taskDueText(task);

  const now = new Date();
  const tomorrow = startOfDay(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const timePart = dueDate.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

  if (isSameCalendarDay(dueDate, now)) {
    return `Heute ${timePart}`;
  }
  if (isSameCalendarDay(dueDate, tomorrow)) {
    return `Morgen ${timePart}`;
  }
  return fmtDate(task.due_at);
}

function childTaskCardMarkup(task, { overdue = false, actionable = true } = {}) {
  if (!actionable) {
    return `<article class="request-card ${overdue ? "overdue" : ""}">
      <span class="task-card-title">${safeHtmlText(task.title)}</span>
      <span class="task-card-meta">${childTaskDueText(task)} • ${task.points} Punkte${task.status === "rejected" ? " • erneut erledigen" : ""}</span>
    </article>`;
  }
  return `<article class="task-action-card ${overdue ? "overdue" : ""}">
    <span class="task-card-title">${safeHtmlText(task.title)}</span>
    <span class="task-card-meta">${childTaskDueText(task)} • ${task.points} Punkte${task.status === "rejected" ? " • erneut erledigen" : ""}</span>
    <div class="request-card-actions">
      <button class="child-task-btn done" data-task-id="${task.id}" data-task-action="submit_done">Erledigt</button>
      ${overdue ? `<button class="child-task-btn missed" data-task-id="${task.id}" data-task-action="report_missed">Nicht erledigt</button>` : ""}
    </div>
  </article>`;
}

function renderChildTaskCards(targetId, tasks, emptyText, { overdue = false, actionable = true } = {}) {
  const target = byId(targetId);
  if (!target) return;
  if (!Array.isArray(tasks) || tasks.length === 0) {
    target.innerHTML = `<p class="muted">${emptyText}</p>`;
    return;
  }
  target.innerHTML = tasks
    .map((task) => childTaskCardMarkup(task, { overdue, actionable }))
    .join("");
}

function openChildDashboardTodayList() {
  openTasksTabWithSection("child-task-categories-section");
}

function getTaskActivityDate(task) {
  return parseDateSafe(task.updated_at || task.created_at || task.due_at);
}

function recurringTaskKey(task) {
  if (task.recurrence_type === "none") return null;
  return [
    task.assignee_id,
    task.title || "",
    task.description || "",
    task.recurrence_type || "",
    task.special_template_id || 0,
  ].join("|");
}

function dueTimestamp(task) {
  const due = parseDateSafe(task.due_at);
  if (!due) return Number.POSITIVE_INFINITY;
  return due.getTime();
}

function newestRecurringEntries(tasks, strategy = "latest_activity") {
  const fixed = [];
  const latestByKey = new Map();
  tasks.forEach((task) => {
    const key = recurringTaskKey(task);
    if (!key) {
      fixed.push(task);
      return;
    }
    const existing = latestByKey.get(key);
    const currentTime = getTaskActivityDate(task)?.getTime() || 0;
    const existingTime = existing ? (getTaskActivityDate(existing)?.getTime() || 0) : -1;
    if (!existing) {
      latestByKey.set(key, task);
      return;
    }

    if (strategy === "earliest_due") {
      const currentDue = dueTimestamp(task);
      const existingDue = dueTimestamp(existing);
      if (currentDue < existingDue || (currentDue === existingDue && currentTime >= existingTime)) {
        latestByKey.set(key, task);
      }
      return;
    }

    if (currentTime >= existingTime) {
      latestByKey.set(key, task);
    }
  });
  return [...fixed, ...Array.from(latestByKey.values())];
}

function startOfWeek(baseDate = new Date()) {
  const date = new Date(baseDate);
  const day = (date.getDay() + 6) % 7;
  date.setHours(0, 0, 0, 0);
  date.setDate(date.getDate() - day);
  return date;
}

function getWeekRange(baseDate = new Date()) {
  const start = startOfWeek(baseDate);
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  return { start, end };
}

function isDateInRange(date, start, end) {
  return Boolean(date) && date >= start && date < end;
}

function getTaskStatusCounts(tasks) {
  return tasks.reduce(
    (acc, task) => {
      if (task.status === "missed_submitted") {
        acc.missed += 1;
        return acc;
      }
      if (Object.prototype.hasOwnProperty.call(acc, task.status)) {
        acc[task.status] += 1;
      }
      return acc;
    },
    { open: 0, submitted: 0, missed: 0, approved: 0, rejected: 0 }
  );
}

function setStatusProgress(status, count, total) {
  const percentage = total > 0 ? Math.round((count / total) * 100) : 0;
  const label = byId(`status-${status}-value`);
  const bar = byId(`status-${status}-bar`);
  if (label) label.textContent = `${count} (${percentage}%)`;
  if (bar) bar.style.width = `${percentage}%`;
}

function renderWeeklyTrend(tasks) {
  const barsTarget = byId("weekly-trend-bars");
  if (!barsTarget) return;

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const entries = [];
  for (let offset = 6; offset >= 0; offset -= 1) {
    const dayStart = new Date(today);
    dayStart.setDate(dayStart.getDate() - offset);
    const dayEnd = new Date(dayStart);
    dayEnd.setDate(dayEnd.getDate() + 1);

    const count = tasks.filter(
      (task) => task.status === "approved" && isDateInRange(getTaskActivityDate(task), dayStart, dayEnd)
    ).length;

    entries.push({
      label: dayStart.toLocaleDateString("de-DE", { weekday: "short" }).replace(".", ""),
      count,
    });
  }

  const maxCount = Math.max(1, ...entries.map((entry) => entry.count));
  barsTarget.innerHTML = entries
    .map((entry) => {
      const height = Math.max(6, Math.round((entry.count / maxCount) * 100));
      return `<div class="trend-bar-column">
        <span class="trend-bar-value">${entry.count}</span>
        <div class="trend-bar-track">
          <span class="trend-bar-fill" style="height: ${height}%"></span>
        </div>
        <span class="trend-bar-label">${entry.label}</span>
      </div>`;
    })
    .join("");

  const weeklyCaption = byId("dashboard-weekly-caption");
  if (weeklyCaption) {
    const totalApprovedLastSevenDays = entries.reduce((sum, entry) => sum + entry.count, 0);
    weeklyCaption.textContent = `${totalApprovedLastSevenDays} erledigte Aufgaben in den letzten 7 Tagen`;
  }
}

function renderDashboardAnalytics(tasks, statusCounts = getTaskStatusCounts(tasks)) {
  const totalStatusCount = statusCounts.open + statusCounts.submitted + statusCounts.missed + statusCounts.approved + statusCounts.rejected;
  const completionRate = totalStatusCount > 0 ? Math.round((statusCounts.approved / totalStatusCount) * 100) : 0;

  const { start, end } = getWeekRange(new Date());
  const approvedThisWeek = tasks.filter(
    (task) => task.status === "approved" && isDateInRange(getTaskActivityDate(task), start, end)
  );
  const openThisWeek = tasks.filter((task) => {
    if (task.status !== "open" && task.status !== "submitted") return false;
    if (task.recurrence_type === "weekly") return true;
    return isDateInRange(parseDateSafe(task.due_at), start, end);
  }).length;
  const pointsThisWeek = approvedThisWeek.reduce((sum, task) => sum + Number(task.points || 0), 0);

  const statApprovedWeek = byId("stat-approved-week");
  const statWeekOpen = byId("stat-week-open");
  const statWeekPoints = byId("stat-week-points");
  const statCompletionRate = byId("stat-completion-rate");
  const weekSummary = byId("dashboard-week-summary");

  if (statApprovedWeek) statApprovedWeek.textContent = String(approvedThisWeek.length);
  if (statWeekOpen) statWeekOpen.textContent = String(openThisWeek);
  if (statWeekPoints) statWeekPoints.textContent = String(pointsThisWeek);
  if (statCompletionRate) statCompletionRate.textContent = `${completionRate}%`;
  if (weekSummary) weekSummary.textContent = `Diese Woche: ${approvedThisWeek.length} erledigt • ${openThisWeek} offen`;

  setStatusProgress("open", statusCounts.open, totalStatusCount);
  setStatusProgress("submitted", statusCounts.submitted, totalStatusCount);
  setStatusProgress("missed", statusCounts.missed, totalStatusCount);
  setStatusProgress("approved", statusCounts.approved, totalStatusCount);
  setStatusProgress("rejected", statusCounts.rejected, totalStatusCount);
  renderWeeklyTrend(tasks);
}

function isManagerRole() {
  return state.currentRole === "admin" || state.currentRole === "parent";
}

function canManageMembers() {
  return state.currentRole === "admin";
}

function isChildRole() {
  return !isManagerRole();
}

function memberName(userId) {
  const member = state.members.find((m) => m.user_id === userId);
  return member ? member.display_name : "Unbekannt";
}

function memberNameHtml(userId) {
  return safeHtmlText(memberName(userId), "Unbekannt");
}

function getSelfMember() {
  if (!state.me) return null;
  return state.members.find((m) => m.user_id === state.me.id) || null;
}

function getSelectedFamilyId() {
  return Number(familySelect.value || state.familyId);
}

function getVisibleTasksForDashboard() {
  const activeOrDoneTasks = state.tasks.filter((task) => task.is_active !== false || task.status === "approved");
  if (!isChildRole() || !state.me) return activeOrDoneTasks;
  const now = new Date();
  return activeOrDoneTasks.filter(
    (task) =>
      task.assignee_id === state.me.id &&
      !(task.recurrence_type === "weekly" && task.due_at && new Date(task.due_at) > now)
  );
}

function isSectionOpen(sectionId) {
  const element = byId(sectionId);
  return Boolean(element) && !element.classList.contains("hidden");
}

function setSectionOpen(sectionId, buttonId, open, openLabel, closeLabel) {
  const section = byId(sectionId);
  if (section) section.classList.toggle("hidden", !open);
  const button = byId(buttonId);
  if (button) button.textContent = open ? closeLabel : openLabel;
}

function toggleSection(sectionId, buttonId, openLabel, closeLabel) {
  setSectionOpen(sectionId, buttonId, !isSectionOpen(sectionId), openLabel, closeLabel);
}

function getOwnBalance() {
  if (!state.me) return null;
  const own = state.pointsBalances.find((entry) => entry.user_id === state.me.id);
  return own ? own.balance : null;
}

async function api(path, { method = "GET", body = null } = {}) {
  const headers = { "Content-Type": "application/json" };

  const response = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : null,
    credentials: "same-origin",
  });

  const raw = await response.text();
  let payload = {};
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch (_) {
      payload = {};
    }
  }

  if (!response.ok) {
    const detail = payload?.detail || raw || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

function liveCursorStorageKey(familyId) {
  return `fp_live_cursor_${familyId}`;
}

function loadLiveCursor(familyId) {
  if (!familyId) return 0;
  const raw = localStorage.getItem(liveCursorStorageKey(familyId));
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
}

function saveLiveCursor(familyId, cursor) {
  if (!familyId) return;
  if (!Number.isInteger(cursor) || cursor < 0) return;
  localStorage.setItem(liveCursorStorageKey(familyId), String(cursor));
}

function clearLiveReconnectTimer() {
  if (liveReconnectTimer) {
    window.clearTimeout(liveReconnectTimer);
    liveReconnectTimer = null;
  }
}

function clearLiveRefreshTimer() {
  if (liveRefreshTimer) {
    window.clearTimeout(liveRefreshTimer);
    liveRefreshTimer = null;
  }
}

function closeLiveSource() {
  if (!liveEventSource) return;
  liveEventSource.close();
  liveEventSource = null;
}

function buildLiveStreamUrl(familyId) {
  const params = new URLSearchParams();
  if (liveCursor > 0) params.set("since_id", String(liveCursor));
  return `/families/${familyId}/live/stream?${params.toString()}`;
}

function queueLiveRefresh(reason = "live_update") {
  if (!liveShouldRun || !state.me || !getSelectedFamilyId()) return;
  if (liveRefreshTimer) return;
  liveRefreshTimer = window.setTimeout(async () => {
    liveRefreshTimer = null;
    if (liveRefreshInFlight) {
      liveRefreshPending = true;
      return;
    }

    liveRefreshInFlight = true;
    try {
      await refreshFamilyData();
    } catch (error) {
      log("Live-Refresh Fehler", { error: error.message, reason });
    } finally {
      liveRefreshInFlight = false;
      if (liveRefreshPending) {
        liveRefreshPending = false;
        queueLiveRefresh("queued_follow_up");
      }
    }
  }, LIVE_REFRESH_DEBOUNCE_MS);
}

function scheduleLiveReconnect(familyId) {
  if (!liveShouldRun || !state.me || !familyId) return;
  clearLiveReconnectTimer();
  const delay = liveReconnectDelayMs;
  liveReconnectTimer = window.setTimeout(() => {
    connectLiveUpdates(familyId);
  }, delay);
  liveReconnectDelayMs = Math.min(liveReconnectDelayMs * 2, LIVE_RECONNECT_MAX_MS);
}

function connectLiveUpdates(familyId) {
  if (!liveShouldRun || !state.me || !familyId) return;
  closeLiveSource();

  const streamUrl = buildLiveStreamUrl(familyId);
  let source = null;
  try {
    source = new EventSource(streamUrl);
  } catch (error) {
    log("Live-Updates Startfehler", { error: error.message });
    scheduleLiveReconnect(familyId);
    return;
  }
  liveEventSource = source;

  source.onopen = () => {
    liveReconnectDelayMs = LIVE_RECONNECT_BASE_MS;
    if (!liveConnected) {
      log("Live-Updates verbunden", { family_id: familyId });
    }
    liveConnected = true;
  };

  source.addEventListener("connected", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      const connectedCursor = Number(payload.since_id || 0);
      if (Number.isInteger(connectedCursor) && connectedCursor > liveCursor) {
        liveCursor = connectedCursor;
        saveLiveCursor(familyId, liveCursor);
      }
    } catch (_) {
      // Ignore malformed connected payload and continue streaming.
    }
  });

  source.addEventListener("family_update", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      const eventId = Number(payload.id || event.lastEventId || 0);
      if (Number.isInteger(eventId) && eventId > liveCursor) {
        liveCursor = eventId;
        saveLiveCursor(familyId, liveCursor);
      }
      if (payload.event_type === "notification.test") {
        const info = payload.payload || {};
        const title = info.title || "Testbenachrichtigung";
        const message = info.message || "";
        log(`Live-Benachrichtigung: ${title}`, { message, recipients: info.recipient_user_ids || [] });
      }
      if (payload.event_type === "task.submitted") {
        const info = payload.payload || {};
        if (info.source === "system_practical_test") {
          log("Praxis-Test Event eingegangen", {
            event_type: payload.event_type,
            task_id: info.task_id,
            assignee_id: info.assignee_id,
          });
        }
      }
      queueLiveRefresh(payload.event_type || "family_update");
    } catch (error) {
      log("Live-Event Parse Fehler", { error: error.message });
    }
  });

  source.onerror = () => {
    closeLiveSource();
    if (liveConnected) {
      log("Live-Updates getrennt, verbinde neu ...", { family_id: familyId });
    }
    liveConnected = false;
    scheduleLiveReconnect(familyId);
  };
}

function startLiveUpdates() {
  const familyId = getSelectedFamilyId();
  if (!state.me || !familyId) return;
  if (typeof EventSource === "undefined") {
    log("Live-Updates nicht verfügbar", { reason: "EventSource wird vom Browser nicht unterstützt" });
    return;
  }

  if (liveShouldRun && liveFamilyId === familyId && liveEventSource) return;
  liveShouldRun = true;
  liveFamilyId = familyId;
  liveCursor = loadLiveCursor(familyId);
  liveReconnectDelayMs = LIVE_RECONNECT_BASE_MS;
  clearLiveReconnectTimer();
  connectLiveUpdates(familyId);
}

function stopLiveUpdates({ resetCursor = false } = {}) {
  liveShouldRun = false;
  clearLiveReconnectTimer();
  clearLiveRefreshTimer();
  closeLiveSource();
  liveConnected = false;
  liveRefreshInFlight = false;
  liveRefreshPending = false;
  if (resetCursor && liveFamilyId) {
    localStorage.removeItem(liveCursorStorageKey(liveFamilyId));
    liveCursor = 0;
  }
  if (resetCursor) {
    liveFamilyId = null;
  }
}

function stopSpecialTaskRefreshTicker() {
  if (!specialTaskRefreshTimer) return;
  window.clearInterval(specialTaskRefreshTimer);
  specialTaskRefreshTimer = null;
}

function startSpecialTaskRefreshTicker() {
  stopSpecialTaskRefreshTicker();
  specialTaskRefreshTimer = window.setInterval(() => {
    if (!state.me || !isChildRole() || !getSelectedFamilyId()) return;
    loadSpecialTasks().catch((error) => log("Sonderaufgaben Auto-Refresh Fehler", { error: error.message }));
  }, 60000);
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
}

function getActiveTabName() {
  const active = document.querySelector(".tab.active");
  return active ? active.dataset.tab : "dashboard";
}

function fillSelect(id, options, includeEmpty = false, emptyLabel = "-") {
  const select = byId(id);
  if (!select) return;

  const currentValue = select.value;
  select.innerHTML = "";

  if (includeEmpty) {
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = emptyLabel;
    select.appendChild(emptyOption);
  }

  options.forEach((entry) => {
    const option = document.createElement("option");
    option.value = String(entry.value);
    option.textContent = entry.label;
    select.appendChild(option);
  });

  if (Array.from(select.options).some((option) => option.value === currentValue)) {
    select.value = currentValue;
  }
}

function applyMobileLabelsToTableBody(tbodyId) {
  const tbody = byId(tbodyId);
  if (!tbody) return;
  const table = tbody.closest("table");
  if (!table) return;

  const labels = Array.from(table.querySelectorAll("thead th")).map((th) => th.textContent.trim());
  Array.from(tbody.querySelectorAll("tr")).forEach((row) => {
    const cells = Array.from(row.children).filter((cell) => cell.tagName === "TD");
    cells.forEach((cell, index) => {
      const colspan = Number(cell.getAttribute("colspan") || "1");
      if (colspan > 1) {
        cell.removeAttribute("data-label");
        cell.classList.add("table-cell-full");
        return;
      }

      cell.classList.remove("table-cell-full");
      const label = labels[index] || "";
      if (label) cell.setAttribute("data-label", label);
      else cell.removeAttribute("data-label");
    });
  });
}

function applyMobileLabelsToTableBodies(tbodyIds) {
  tbodyIds.forEach((tbodyId) => applyMobileLabelsToTableBody(tbodyId));
}

function initInlineEditorHomes() {
  inlineEditorSectionIds.forEach((sectionId) => {
    if (inlineEditorHomes[sectionId]) return;
    const section = byId(sectionId);
    if (!section || !section.parentNode) return;

    const marker = document.createComment(`${sectionId}-home`);
    section.parentNode.insertBefore(marker, section);
    inlineEditorHomes[sectionId] = { marker, section };
  });
}

function removeInlineEditorRow(sectionId) {
  const inlineRow = byId(`${sectionId}-inline-row`);
  if (inlineRow && inlineRow.parentNode) {
    inlineRow.parentNode.removeChild(inlineRow);
  }
}

function restoreInlineEditorSection(sectionId) {
  const home = inlineEditorHomes[sectionId];
  if (!home) return;
  removeInlineEditorRow(sectionId);

  const { marker, section } = home;
  if (marker.parentNode && section) {
    marker.parentNode.insertBefore(section, marker.nextSibling);
  }
  if (section) {
    section.classList.remove("inline-editor-mounted");
  }
}

function restoreAllInlineEditorSections() {
  inlineEditorSectionIds.forEach((sectionId) => restoreInlineEditorSection(sectionId));
}

function mountInlineEditorSectionBelowTrigger(sectionId, triggerButton) {
  const home = inlineEditorHomes[sectionId];
  const section = home?.section;
  if (!section) return;

  restoreInlineEditorSection(sectionId);
  if (!triggerButton) return;

  const card = triggerButton.closest(".entity-card");
  if (card) {
    const inlineWrap = document.createElement("div");
    inlineWrap.id = `${sectionId}-inline-row`;
    inlineWrap.className = "inline-editor-card-slot";
    card.insertAdjacentElement("afterend", inlineWrap);
    inlineWrap.appendChild(section);
    section.classList.add("inline-editor-mounted");
    return;
  }

  const row = triggerButton.closest("tr");
  const table = row ? row.closest("table") : null;
  if (!row || !table) return;

  const headerCells = table.querySelectorAll("thead th").length;
  const rowCells = row.querySelectorAll("td,th").length;
  const colSpan = Math.max(1, headerCells || rowCells || 1);

  const inlineRow = document.createElement("tr");
  inlineRow.id = `${sectionId}-inline-row`;
  inlineRow.className = "inline-editor-row";

  const inlineCell = document.createElement("td");
  inlineCell.className = "inline-editor-cell";
  inlineCell.colSpan = colSpan;
  inlineRow.appendChild(inlineCell);

  row.insertAdjacentElement("afterend", inlineRow);
  inlineCell.appendChild(section);
  section.classList.add("inline-editor-mounted");

  requestAnimationFrame(() => {
    section.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

function syncDashboardStatsCardOrder() {
  const statsGrid = document.querySelector("#tab-dashboard .stats-grid");
  const childPointsCard = byId("stat-card-child-points");
  if (!statsGrid || !childPointsCard) return;

  if (isChildRole()) {
    statsGrid.prepend(childPointsCard);
  } else {
    statsGrid.append(childPointsCard);
  }
}

function openTaskHistoryFromDashboard() {
  if (isChildRole()) return;
  openTasksTabWithSection("task-history-section");
}

function openTasksTabWithSection(sectionId = null) {
  switchTab("tasks");
  if (!sectionId) return;
  const section = byId(sectionId);
  if (!section) return;
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

function applyRoleVisibility() {
  const child = isChildRole();

  toggleHidden("tab-btn-members", child);
  toggleHidden("tab-btn-system", child);
  toggleHidden("tab-members", child);
  toggleHidden("tab-system", child);

  toggleHidden("toggle-member-create-btn", !canManageMembers());
  if (!canManageMembers()) {
    setSectionOpen("member-create-section", "toggle-member-create-btn", false, "Neues Mitglied", "Eingabe schließen");
  }
  closeMemberEditor();
  toggleHidden("dashboard-members-section", child);
  toggleHidden("dashboard-pending-section", child);
  toggleHidden("dashboard-child-focus-section", !child);
  toggleHidden("dashboard-missed-card", child);
  toggleHidden("stat-card-child-points", !child);
  syncDashboardStatsCardOrder();

  toggleHidden("toggle-task-create-btn", child);
  if (child) {
    setSectionOpen("task-create-section", "toggle-task-create-btn", false, "Neue Aufgabe", "Eingabe schließen");
  }
  toggleHidden("tasks-table-section", child);
  toggleHidden("task-history-section", child);
  toggleHidden("task-review-cards-section", child);
  closeTaskEditor();
  toggleHidden("manager-special-task-section", child);
  toggleHidden("child-special-task-section", !child);
  closeSpecialTaskEditor();
  if (child) {
    setSectionOpen("special-task-create-section", "toggle-special-task-create-btn", false, "Neue Sonderaufgabe", "Eingabe schließen");
  }

  toggleHidden("child-task-categories-section", !child);
  toggleHidden("child-submitted-section", !child);
  toggleHidden("child-completed-section", !child);

  toggleHidden("toggle-event-create-btn", child);
  if (child) {
    setSectionOpen("event-create-section", "toggle-event-create-btn", false, "Neuer Termin", "Eingabe schließen");
  }

  toggleHidden("toggle-reward-create-btn", child);
  if (child) {
    setSectionOpen("reward-create-section", "toggle-reward-create-btn", false, "Neue Belohnung", "Eingabe schließen");
  }
  closeRewardEditor();
  toggleHidden("reward-redeem-section", !child);
  toggleHidden("reward-review-cards-section", child);

  toggleHidden("points-manager-block", child);
  closePointsAdjust();
  if (child) {
    state.selectedPointsUserId = state.me ? state.me.id : null;
  }

  if (child) {
    const activeTab = getActiveTabName();
    if (activeTab === "members" || activeTab === "system") {
      switchTab("dashboard");
    }
  }
}

async function initAuthPanel() {
  authPanel.classList.remove("hidden");
  appPanel.classList.add("hidden");
  toggleHidden("login-section", true);
  toggleHidden("bootstrap-section", true);
  if (byId("boot-password-visible")) byId("boot-password-visible").checked = false;
  if (byId("member-password-visible")) byId("member-password-visible").checked = false;
  setPasswordInputVisibility(["boot-password", "boot-password-confirm"], false);
  setPasswordInputVisibility(["member-password", "member-password-confirm"], false);

  try {
    const status = await api("/auth/bootstrap-status");
    if (status.bootstrap_required) {
      toggleHidden("bootstrap-section", false);
    } else {
      toggleHidden("login-section", false);
    }
  } catch (error) {
    toggleHidden("login-section", false);
    log("Bootstrap-Status konnte nicht geladen werden", { error: error.message });
  }
}

function syncTaskCreateTimingUI() {
  const recurrence = byId("task-recurrence").value;
  const daily = recurrence === "daily";
  const weekly = recurrence === "weekly";
  const dueMode = byId("task-due-mode").value;
  const weeklyExact = weekly && dueMode === "exact";

  if (!weekly) {
    byId("task-due-mode").value = "exact";
  }

  toggleHidden("task-daily-time-row", !daily);
  toggleHidden("task-weekdays-row", !daily);
  toggleHidden("task-weekly-day-row", !weeklyExact);
  toggleHidden("task-weekly-time-row", !weeklyExact);
  if (!daily) {
    setInvalid(byId("task-weekdays-row"), false);
    setInvalid(byId("task-daily-time"), false);
  }
  if (!weeklyExact) {
    setInvalid(byId("task-weekly-day"), false);
    setInvalid(byId("task-weekly-time"), false);
  }
  toggleHidden("task-due-mode-row", !weekly);
  const hideDue = daily || weekly;
  toggleHidden("task-due-row", hideDue);
  if (hideDue) byId("task-due").value = "";

  const hideReminders = weekly && dueMode === "week_flexible";
  toggleHidden("task-reminder-wrap", hideReminders);
  if (hideReminders) setSelectedReminderOffsets("task-reminder-options", []);
  applyReminderOptionRestrictions("task-reminder-options", daily ? DAILY_REMINDER_OFFSETS : ALL_REMINDER_OFFSETS);

  const penaltySupported = daily || weeklyExact;
  if (!penaltySupported) {
    byId("task-penalty-enabled").value = "false";
    byId("task-penalty-points").value = "5";
  }
  toggleHidden("task-penalty-enabled-row", !penaltySupported);
  const penaltyEnabled = penaltySupported && byId("task-penalty-enabled").value === "true";
  toggleHidden("task-penalty-points-row", !penaltyEnabled);
  setInvalid(byId("task-penalty-points"), false);
}

function syncTaskEditorTimingUI() {
  const recurrence = byId("task-editor-recurrence").value;
  const daily = recurrence === "daily";
  const weekly = recurrence === "weekly";
  const dueMode = byId("task-editor-due-mode").value;
  const weeklyExact = weekly && dueMode === "exact";

  if (!weekly) {
    byId("task-editor-due-mode").value = "exact";
  }

  toggleHidden("task-editor-daily-time-row", !daily);
  toggleHidden("task-editor-weekdays-row", !daily);
  toggleHidden("task-editor-weekly-day-row", !weeklyExact);
  toggleHidden("task-editor-weekly-time-row", !weeklyExact);
  if (!daily) {
    setInvalid(byId("task-editor-weekdays-row"), false);
    setInvalid(byId("task-editor-daily-time"), false);
  }
  if (!weeklyExact) {
    setInvalid(byId("task-editor-weekly-day"), false);
    setInvalid(byId("task-editor-weekly-time"), false);
  }
  toggleHidden("task-editor-due-mode-row", !weekly);
  const hideDue = daily || weekly;
  toggleHidden("task-editor-due-row", hideDue);
  if (hideDue) byId("task-editor-due").value = "";

  const hideReminders = weekly && dueMode === "week_flexible";
  toggleHidden("task-editor-reminder-wrap", hideReminders);
  if (hideReminders) setSelectedReminderOffsets("task-editor-reminder-options", []);
  applyReminderOptionRestrictions("task-editor-reminder-options", daily ? DAILY_REMINDER_OFFSETS : ALL_REMINDER_OFFSETS);

  const penaltySupported = daily || weeklyExact;
  if (!penaltySupported) {
    byId("task-editor-penalty-enabled").value = "false";
    byId("task-editor-penalty-points").value = "5";
  }
  toggleHidden("task-editor-penalty-enabled-row", !penaltySupported);
  const penaltyEnabled = penaltySupported && byId("task-editor-penalty-enabled").value === "true";
  toggleHidden("task-editor-penalty-points-row", !penaltyEnabled);
  setInvalid(byId("task-editor-penalty-points"), false);
}

function renderMembers() {
  const manageMembers = canManageMembers();
  const memberRows = state.members
    .map((member) => {
      const actions = manageMembers
        ? `<button data-member-action="edit" data-member-id="${member.user_id}">Bearbeiten</button> <button data-member-action="delete" data-member-id="${member.user_id}">Löschen</button>`
        : "-";
      return `<tr>
        <td>${safeHtmlText(member.display_name)}</td>
        <td>${safeHtmlText(member.email)}</td>
        <td>${safeHtmlText(member.ha_notify_service, "-")}</td>
        <td>${roleLabel(member.role)}</td>
        <td>${member.is_active ? "ja" : "nein"}</td>
        <td>${actions}</td>
      </tr>`;
    })
    .join("");

  const dashboardRows = state.members
    .map(
      (member) => `<tr>
        <td>${safeHtmlText(member.display_name)}</td>
        <td>${safeHtmlText(member.email)}</td>
        <td>${roleLabel(member.role)}</td>
        <td>${member.is_active ? "ja" : "nein"}</td>
      </tr>`
    )
    .join("");

  byId("members-body").innerHTML = memberRows;
  byId("dashboard-members-body").innerHTML = dashboardRows;
  applyMobileLabelsToTableBodies(["members-body", "dashboard-members-body"]);

  const memberOptions = state.members.map((member) => ({
    value: member.user_id,
    label: `${member.display_name} (${roleLabel(member.role)})`,
  }));

  fillSelect("task-assignee", memberOptions);
  fillSelect("task-editor-assignee", memberOptions);
  fillSelect("event-responsible", memberOptions, true, "keiner");
  populateChannelTestRecipients();

  byId("stat-members").textContent = String(state.members.length);
}

function fillMemberEditorForm() {
  const memberId = state.selectedMemberId;
  const member = state.members.find((entry) => entry.user_id === memberId);
  if (!member) return;

  byId("member-editor-name").value = member.display_name || "";
  byId("member-editor-role").value = member.role || "child";
  byId("member-editor-active").value = member.is_active ? "true" : "false";
  byId("member-editor-ha-notify-service").value = member.ha_notify_service || "";
  byId("member-editor-password").value = "";
}

function openMemberEditor(memberId, triggerButton = null) {
  state.selectedMemberId = memberId;
  fillMemberEditorForm();
  mountInlineEditorSectionBelowTrigger("member-editor-section", triggerButton);
  toggleHidden("member-editor-section", false);
}

function closeMemberEditor() {
  state.selectedMemberId = null;
  toggleHidden("member-editor-section", true);
  restoreInlineEditorSection("member-editor-section");
}

function renderDashboardPoints() {
  byId("dashboard-points-body").innerHTML = state.pointsBalances
    .map(
      (entry) => `<tr>
        <td>${safeHtmlText(entry.display_name)}</td>
        <td>${roleLabel(entry.role)}</td>
        <td>${entry.balance}</td>
      </tr>`
    )
    .join("");
  applyMobileLabelsToTableBodies(["dashboard-points-body"]);
}

function getPendingTaskRequests() {
  return state.tasks.filter((task) => task.status === "submitted" || task.status === "missed_submitted");
}

function getPendingRewardRequests() {
  return state.redemptions.filter((entry) => entry.status === "pending");
}

function renderDashboardPendingRequests() {
  const pendingTasks = getPendingTaskRequests();
  const submittedTasks = pendingTasks.filter((task) => task.status === "submitted");
  const missedTasks = pendingTasks.filter((task) => task.status === "missed_submitted");
  const pendingRewards = getPendingRewardRequests();

  byId("dashboard-pending-task-cards").innerHTML = submittedTasks.length
    ? submittedTasks
      .map((task) => {
        return `<article class="request-card">
          <p class="request-card-title">${memberNameHtml(task.assignee_id)} hat "${safeHtmlText(task.title)}" als erledigt gemeldet</p>
          <p class="request-card-meta">${taskDueText(task)} • ${task.points} Punkte</p>
          <div class="request-card-actions">
            <button data-dashboard-task-review-action="approved" data-task-id="${task.id}">Bestätigen</button>
            ${
              task.special_template_id
                ? `<button class="btn-secondary" data-dashboard-task-review-action="rejected_delete" data-task-id="${task.id}">Ablehnen & löschen</button>`
                : `<button class="btn-secondary" data-dashboard-task-review-action="rejected" data-task-id="${task.id}">Ablehnen</button>`
            }
          </div>
        </article>`;
      })
      .join("")
    : "<p class=\"muted\">Keine Aufgaben in Prüfung</p>";

  byId("dashboard-missed-task-cards").innerHTML = missedTasks.length
    ? missedTasks
      .map((task) => `<article class="request-card">
          <p class="request-card-title">${memberNameHtml(task.assignee_id)}: "${safeHtmlText(task.title)}" verpasst</p>
          <p class="request-card-meta">${taskDueText(task)} • Entscheidung erforderlich</p>
          <div class="request-card-actions">
            <button data-dashboard-missed-task-action="approve" data-task-id="${task.id}">Doch bestätigen</button>
            <button data-dashboard-missed-task-action="delete" data-task-id="${task.id}">Löschen</button>
            <button class="btn-secondary" data-dashboard-missed-task-action="penalty" data-task-id="${task.id}">Minuspunkte</button>
          </div>
        </article>`)
      .join("")
    : "<p class=\"muted\">Keine verpassten Aufgaben</p>";

  byId("dashboard-pending-reward-cards").innerHTML = pendingRewards.length
    ? pendingRewards
      .map((entry) => {
        const reward = state.rewards.find((r) => r.id === entry.reward_id);
        return `<article class="request-card">
          <p class="request-card-title">${memberNameHtml(entry.requested_by_id)} hat "${safeHtmlText(reward ? reward.title : "Belohnung")}" angefragt</p>
          <p class="request-card-meta">Angefragt am ${fmtDate(entry.requested_at)}</p>
          <div class="request-card-actions">
            <button data-dashboard-reward-review-action="approved" data-redemption-id="${entry.id}">Bestätigen</button>
            <button class="btn-secondary" data-dashboard-reward-review-action="rejected" data-redemption-id="${entry.id}">Ablehnen</button>
          </div>
        </article>`;
      })
      .join("")
    : "<p class=\"muted\">Keine offenen Belohnungsanfragen</p>";
}

function renderTasks() {
  const visibleTasks = getVisibleTasksForDashboard();
  const manager = isManagerRole();
  const statusCounts = getTaskStatusCounts(visibleTasks);

  byId("dashboard-tasks-body").innerHTML = visibleTasks
    .map(
      (task) => `<tr>
        <td>${safeHtmlText(task.title)}</td>
        <td>${memberNameHtml(task.assignee_id)}</td>
        <td>${statusLabel(task.status)}</td>
        <td>${task.points}</td>
        <td>${taskDueText(task)}</td>
      </tr>`
    )
    .join("");

  const managerTasks = manager ? sortManagerTasks(state.tasks.filter((task) => task.status !== "approved")) : [];
  const managerHistoryTasks = manager
    ? state.tasks
      .filter((task) => task.status === "approved")
      .sort((a, b) => new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime())
    : [];
  const tasksSortSelect = byId("tasks-sort-select");
  if (tasksSortSelect && tasksSortSelect.value !== state.tasksSort) {
    tasksSortSelect.value = state.tasksSort;
  }
  byId("tasks-manager-cards").innerHTML = manager
    ? managerTasks.length
      ? managerTasks
        .map(
          (task) => `<article class="entity-card entity-card-list">
            <div class="entity-card-head">
              <p class="entity-card-title">${safeHtmlText(task.title)}</p>
              <span class="entity-tag">${task.is_active === false ? "deaktiviert" : statusLabel(task.status)}</span>
            </div>
            <p class="entity-card-meta">${safeHtmlText(task.description, "Ohne Beschreibung")}</p>
            <p class="entity-card-meta">Zuständig: ${memberNameHtml(task.assignee_id)} • ${task.points} Punkte</p>
            <p class="entity-card-meta">${taskScheduleMeta(task)}</p>
            <p class="entity-card-meta">Zuletzt geändert: ${fmtDate(task.updated_at || task.created_at)}</p>
            <p class="entity-card-meta">${taskPenaltyText(task)}</p>
            <p class="entity-card-meta">Erinnerung: ${reminderOffsetsText(task.reminder_offsets_minutes)}</p>
            <div class="request-card-actions">
              <button data-task-action="edit" data-task-id="${task.id}">Bearbeiten</button>
              <button data-task-action="toggle-active" data-task-id="${task.id}">${task.is_active === false ? "Aktivieren" : "Deaktivieren"}</button>
              <button class="btn-secondary" data-task-action="delete" data-task-id="${task.id}">Löschen</button>
            </div>
          </article>`
        )
        .join("")
      : "<p class=\"muted\">Keine offenen oder wartenden Aufgaben.</p>"
    : "";
  updateTaskEditButtons();
  byId("task-history-cards").innerHTML = manager
    ? managerHistoryTasks.length
      ? managerHistoryTasks
        .map(
          (task) => `<article class="entity-card">
            <div class="entity-card-head">
              <p class="entity-card-title">${safeHtmlText(task.title)}</p>
              <span class="entity-tag">bestätigt</span>
            </div>
            <p class="entity-card-meta">${safeHtmlText(task.description, "Ohne Beschreibung")}</p>
            <p class="entity-card-meta">Zuständig: ${memberNameHtml(task.assignee_id)} • ${task.points} Punkte</p>
            <p class="entity-card-meta">Abgeschlossen am: ${fmtDate(task.updated_at || task.created_at)}</p>
            <p class="entity-card-meta">${taskScheduleMeta(task)}</p>
            <p class="entity-card-meta">${taskPenaltyText(task)}</p>
            <p class="entity-card-meta">Erinnerung: ${reminderOffsetsText(task.reminder_offsets_minutes)}</p>
          </article>`
        )
        .join("")
      : "<p class=\"muted\">Keine abgeschlossenen Aufgaben in der Historie.</p>"
    : "";
  applyMobileLabelsToTableBodies(["dashboard-tasks-body"]);

  byId("stat-open").textContent = String(statusCounts.open);
  byId("stat-submitted").textContent = String(statusCounts.submitted);
  byId("stat-missed").textContent = String(statusCounts.missed);
  byId("stat-approved").textContent = String(statusCounts.approved);
  byId("stat-rejected").textContent = String(statusCounts.rejected);

  renderChildTaskLists();
  renderManagerTaskReviewCards();
  renderDashboardPendingRequests();
  renderDashboardAnalytics(visibleTasks, statusCounts);
}

function renderChildTaskLists() {
  if (!state.me) return;

  const ownTasks = state.tasks.filter((task) => task.assignee_id === state.me.id);
  const ownVisibleTasks = ownTasks.filter((task) => task.is_active !== false || task.status === "approved");
  const now = new Date();
  const tomorrowStart = startOfDay(now);
  tomorrowStart.setDate(tomorrowStart.getDate() + 1);
  const actionableTasks = newestRecurringEntries(ownVisibleTasks
    .filter((task) => task.status === "open" || task.status === "rejected")
    .filter((task) => !(task.recurrence_type === "weekly" && task.due_at && new Date(task.due_at) > now)), "earliest_due");

  const overdueTasks = actionableTasks.filter((task) => task.due_at && new Date(task.due_at) < now);
  const weekTasks = actionableTasks.filter(
    (task) => task.recurrence_type === "weekly" && !(task.due_at && new Date(task.due_at) < now)
  );
  const todayTasks = actionableTasks.filter((task) => {
    if (task.recurrence_type === "weekly") return false;
    if (task.special_template_id) return true;
    const due = parseDateSafe(task.due_at);
    return Boolean(due) && due >= now && due < tomorrowStart;
  });
  const upcomingTasks = actionableTasks.filter((task) => {
    if (task.recurrence_type === "weekly") return false;
    if (task.special_template_id) return false;
    const due = parseDateSafe(task.due_at);
    if (!due) return true;
    return due >= tomorrowStart;
  });

  const waitingTasks = newestRecurringEntries(ownVisibleTasks.filter((task) => task.status === "submitted"));
  const missedTasks = newestRecurringEntries(ownVisibleTasks.filter((task) => task.status === "missed_submitted"));
  const completedTasks = ownVisibleTasks.filter(
    (task) => task.status === "approved" && task.recurrence_type === "none"
  );

  renderChildTaskCards("child-today-task-cards", todayTasks, "Heute keine fälligen Aufgaben");
  renderChildTaskCards("child-upcoming-task-cards", upcomingTasks, "Keine Aufgaben für die nächsten Tage", { actionable: false });
  renderChildTaskCards("child-week-task-cards", weekTasks, "Keine Wochenaufgaben");
  renderChildTaskCards("child-overdue-task-cards", overdueTasks, "Keine überfälligen Aufgaben", { overdue: true });

  byId("child-submitted-cards").innerHTML = waitingTasks.length
    ? waitingTasks
      .map(
        (task) => `<article class="request-card">
          <p class="request-card-title">${safeHtmlText(task.title)}</p>
          <p class="request-card-meta">Eingereicht: ${fmtDate(task.updated_at || task.created_at)} • ${childTaskDueText(task)}</p>
        </article>`
      )
      .join("")
    : "<p class=\"muted\">Keine Aufgaben in Prüfung</p>";

  byId("child-missed-task-cards").innerHTML = missedTasks.length
    ? missedTasks
      .map(
        (task) => `<article class="request-card">
          <p class="request-card-title">${safeHtmlText(task.title)}</p>
          <p class="request-card-meta">Verpasst • ${childTaskDueText(task)} • Entscheidung durch Eltern offen</p>
        </article>`
      )
      .join("")
    : "<p class=\"muted\">Keine verpassten Aufgaben</p>";

  renderChildDashboardFocus(todayTasks, missedTasks, overdueTasks);

  byId("child-completed-cards").innerHTML = completedTasks.length
    ? completedTasks
      .map(
        (task) => `<article class="request-card">
          <p class="request-card-title">${safeHtmlText(task.title)}</p>
          <p class="request-card-meta">Bestätigt • ${childTaskDueText(task)}</p>
        </article>`
      )
      .join("")
    : "<p class=\"muted\">Noch keine bestätigten Aufgaben</p>";
}

function renderChildDashboardFocus(todayTasks, missedTasks, overdueTasks) {
  const todayCard = byId("dashboard-child-today-focus");
  const missedCard = byId("dashboard-child-missed-focus");
  const todayCount = byId("dashboard-child-today-count");
  const todayMeta = byId("dashboard-child-today-meta");
  const missedCount = byId("dashboard-child-missed-count");
  const missedMeta = byId("dashboard-child-missed-meta");
  if (!todayCard || !missedCard || !todayCount || !todayMeta || !missedCount || !missedMeta) return;

  const totalToday = Array.isArray(todayTasks) ? todayTasks.length : 0;
  const totalOverdue = Array.isArray(overdueTasks) ? overdueTasks.length : 0;
  const totalTodayTile = totalToday + totalOverdue;
  const totalMissed = Array.isArray(missedTasks) ? missedTasks.length : 0;
  todayCount.textContent = String(totalTodayTile);
  missedCount.textContent = String(totalMissed);
  missedMeta.textContent = totalMissed > 0 ? `${totalMissed} warten auf Eltern-Entscheidung` : "Keine verpassten Aufgaben";

  const currentClasses = ["focus-normal", "focus-soon", "focus-overdue", "focus-empty"];
  todayCard.classList.remove(...currentClasses);
  if (totalOverdue > 0) {
    todayCard.classList.add("focus-overdue");
    todayMeta.textContent = `${totalOverdue} überfällig • jetzt zuerst erledigen`;
    return;
  }
  if (totalTodayTile === 0) {
    todayCard.classList.add("focus-empty");
    todayMeta.textContent = "Keine fälligen Aufgaben";
    return;
  }
  if (totalTodayTile <= 3) {
    todayCard.classList.add("focus-soon");
    todayMeta.textContent = `${totalTodayTile} Aufgabe(n) heute`;
  } else {
    todayCard.classList.add("focus-overdue");
    todayMeta.textContent = `${totalTodayTile} Aufgabe(n) heute`;
  }
}

async function handleChildTaskActionButton(button) {
  const taskId = Number(button.dataset.taskId);
  if (!taskId) return;
  const action = button.dataset.taskAction || "submit_done";

  if (action === "report_missed") {
    try {
      await reportMissedTaskById(taskId);
    } catch (error) {
      log("Nicht-erledigt melden fehlgeschlagen", { error: error.message });
    }
    return;
  }

  try {
    await submitTaskById(taskId, null);
  } catch (error) {
    log("Aufgabe konnte nicht eingereicht werden", { error: error.message });
  }
}

function renderManagerTaskReviewCards() {
  if (!isManagerRole()) return;
  const pendingTasks = getPendingTaskRequests();

  byId("manager-task-review-cards").innerHTML = pendingTasks.length
    ? pendingTasks
      .map((task) => {
        if (task.status === "missed_submitted") {
          return `<article class="request-card">
          <p class="request-card-title">${memberNameHtml(task.assignee_id)}: ${safeHtmlText(task.title)}</p>
          <p class="request-card-meta">Verpasst • ${taskDueText(task)}</p>
          <div class="request-card-actions">
            <button data-task-missed-review-action="approve" data-task-id="${task.id}">Doch bestätigen</button>
            <button data-task-missed-review-action="delete" data-task-id="${task.id}">Löschen</button>
            <button class="btn-secondary" data-task-missed-review-action="penalty" data-task-id="${task.id}">Minuspunkte</button>
          </div>
        </article>`;
        }
        return `<article class="request-card">
          <p class="request-card-title">${memberNameHtml(task.assignee_id)}: ${safeHtmlText(task.title)}</p>
          <p class="request-card-meta">${taskDueText(task)} • ${task.points} Punkte</p>
          <div class="request-card-actions">
            <button data-task-review-action="approved" data-task-id="${task.id}">Bestätigen</button>
            ${
              task.special_template_id
                ? `<button class="btn-secondary" data-task-review-action="rejected_delete" data-task-id="${task.id}">Ablehnen & löschen</button>`
                : `<button class="btn-secondary" data-task-review-action="rejected" data-task-id="${task.id}">Ablehnen</button>`
            }
          </div>
        </article>`;
      })
      .join("")
    : "<p class=\"muted\">Keine wartenden Aufgaben.</p>";
}

function fillSpecialTaskEditorForm() {
  const templateId = state.selectedSpecialTaskTemplateId;
  const template = state.specialTaskTemplates.find((entry) => entry.id === templateId);
  if (!template) return;

  byId("special-task-editor-title").value = template.title || "";
  byId("special-task-editor-description").value = template.description || "";
  byId("special-task-editor-points").value = String(template.points ?? 0);
  byId("special-task-editor-interval").value = template.interval_type || "weekly";
  byId("special-task-editor-due-time").value = template.due_time_hhmm || "18:00";
  setSelectedWeekdays(
    "special-task-editor-weekdays",
    (template.active_weekdays && template.active_weekdays.length) ? template.active_weekdays : [0, 1, 2, 3, 4, 5, 6]
  );
  byId("special-task-editor-limit").value = String(template.max_claims_per_interval ?? 1);
  byId("special-task-editor-active").value = template.is_active ? "true" : "false";
  syncSpecialTaskEditorTimingUI();
}

function openSpecialTaskEditor(templateId, triggerButton = null) {
  state.selectedSpecialTaskTemplateId = templateId;
  fillSpecialTaskEditorForm();
  state.specialTaskEditorInitialSnapshot = specialTaskEditorSnapshot();
  state.specialTaskEditorDirty = false;
  mountInlineEditorSectionBelowTrigger("special-task-editor-section", triggerButton);
  toggleHidden("special-task-editor-section", false);
  updateSpecialTaskEditButtons();
}

function closeSpecialTaskEditor() {
  state.selectedSpecialTaskTemplateId = null;
  state.specialTaskEditorInitialSnapshot = "";
  state.specialTaskEditorDirty = false;
  toggleHidden("special-task-editor-section", true);
  restoreInlineEditorSection("special-task-editor-section");
  updateSpecialTaskEditButtons();
}

function renderSpecialTaskTemplates() {
  const manager = isManagerRole();
  const sortedTemplates = sortSpecialTaskTemplates(state.specialTaskTemplates);
  const specialSortSelect = byId("special-tasks-sort-select");
  if (specialSortSelect && specialSortSelect.value !== state.specialTasksSort) {
    specialSortSelect.value = state.specialTasksSort;
  }

  byId("special-task-manager-cards").innerHTML = manager
    ? sortedTemplates.length
      ? sortedTemplates
        .map(
          (entry) => `<article class="entity-card entity-card-list">
            <div class="entity-card-head">
              <p class="entity-card-title">${safeHtmlText(entry.title)}</p>
              <span class="entity-tag">${entry.is_active ? "aktiv" : "deaktiviert"}</span>
            </div>
            <p class="entity-card-meta">${safeHtmlText(entry.description, "Ohne Beschreibung")}</p>
            <p class="entity-card-meta">Punkte: ${entry.points}</p>
            <p class="entity-card-meta">${specialTaskScheduleMeta(entry)}</p>
            <p class="entity-card-meta">Limit pro Intervall: ${entry.max_claims_per_interval}</p>
            <p class="entity-card-meta">Zuletzt geändert: ${fmtDate(entry.updated_at || entry.created_at)}</p>
            <div class="request-card-actions">
              <button data-special-task-action="edit" data-special-task-id="${entry.id}">Bearbeiten</button>
              <button class="btn-secondary" data-special-task-action="delete" data-special-task-id="${entry.id}">Löschen</button>
            </div>
          </article>`
        )
        .join("")
      : "<p class=\"muted\">Keine Sonderaufgaben vorhanden.</p>"
    : "";
  updateSpecialTaskEditButtons();

  if (state.selectedSpecialTaskTemplateId) {
    const exists = state.specialTaskTemplates.some((entry) => entry.id === state.selectedSpecialTaskTemplateId);
    if (!exists) closeSpecialTaskEditor();
    else {
      fillSpecialTaskEditorForm();
      state.specialTaskEditorInitialSnapshot = specialTaskEditorSnapshot();
      state.specialTaskEditorDirty = false;
      updateSpecialTaskEditButtons();
    }
  }
}

function renderChildSpecialTaskCards() {
  if (!isChildRole()) return;
  const list = (state.availableSpecialTasks || []).filter((entry) => isSpecialTaskAvailableNow(entry));
  byId("child-special-task-cards").innerHTML = list.length
    ? list
      .map((entry) => {
        const disabled = entry.remaining_count <= 0 ? "disabled" : "";
        const buttonText = entry.remaining_count <= 0 ? "Limit erreicht" : "Annehmen";
        const dailyMeta = entry.interval_type === "daily"
          ? ` • Tage: ${weekdaysText((entry.active_weekdays && entry.active_weekdays.length) ? entry.active_weekdays : [0, 1, 2, 3, 4, 5, 6])}`
          : "";
        const dueMeta = entry.interval_type === "daily" && entry.due_time_hhmm
          ? ` • heute bis ${entry.due_time_hhmm}`
          : "";
        return `<article class="request-card">
          <p class="request-card-title">${safeHtmlText(entry.title)}</p>
          <p class="request-card-meta">${safeHtmlText(entry.description, "Ohne Beschreibung")} • ${entry.points} Punkte</p>
          <p class="request-card-meta">Intervall: ${specialIntervalLabel(entry.interval_type)}${dailyMeta}${dueMeta} • Verfügbar: ${entry.remaining_count}/${entry.max_claims_per_interval}</p>
          <div class="request-card-actions">
            <button data-special-task-claim-id="${entry.id}" ${disabled}>${buttonText}</button>
          </div>
        </article>`;
      })
      .join("")
    : "<p class=\"muted\">Keine aktiven Sonderaufgaben vorhanden.</p>";
}

function fillTaskEditorForm() {
  const taskId = state.selectedTaskId;
  const task = state.tasks.find((entry) => entry.id === taskId);
  if (!task) return;

  const isWeeklyFlexible = task.recurrence_type === "weekly" && !task.due_at;

  byId("task-editor-title").value = task.title || "";
  byId("task-editor-description").value = task.description || "";
  byId("task-editor-assignee").value = String(task.assignee_id);
  byId("task-editor-recurrence").value = task.recurrence_type || "none";
  byId("task-editor-due-mode").value = isWeeklyFlexible ? "week_flexible" : "exact";
  byId("task-editor-due").value = toDatetimeLocalValue(task.due_at);
  byId("task-editor-daily-time").value = toTimeValueFromIso(task.due_at);
  if (task.recurrence_type === "weekly" && task.due_at) {
    const weeklyDate = new Date(task.due_at);
    byId("task-editor-weekly-day").value = String(weekdayFromDate(weeklyDate));
    byId("task-editor-weekly-time").value = toTimeValueFromIso(task.due_at);
  } else {
    byId("task-editor-weekly-day").value = "0";
    byId("task-editor-weekly-time").value = "09:00";
  }
  setSelectedWeekdays("task-editor-weekdays", (task.active_weekdays && task.active_weekdays.length) ? task.active_weekdays : [0, 1, 2, 3, 4, 5, 6]);
  setInvalid(byId("task-editor-weekdays-row"), false);
  byId("task-editor-points").value = String(task.points ?? 0);
  setSelectedReminderOffsets("task-editor-reminder-options", task.reminder_offsets_minutes || []);
  byId("task-editor-status").value = task.status || "open";
  byId("task-editor-active").value = task.is_active === false ? "false" : "true";
  byId("task-editor-always-submittable").value = task.always_submittable ? "true" : "false";
  byId("task-editor-penalty-enabled").value = task.penalty_enabled ? "true" : "false";
  byId("task-editor-penalty-points").value = String(task.penalty_points ?? 5);
  syncTaskEditorTimingUI();
}

function openTaskEditor(taskId, triggerButton = null) {
  state.selectedTaskId = taskId;
  fillTaskEditorForm();
  state.taskEditorInitialSnapshot = taskEditorSnapshot();
  state.taskEditorDirty = false;
  mountInlineEditorSectionBelowTrigger("task-editor-section", triggerButton);
  toggleHidden("task-editor-section", false);
  updateTaskEditButtons();
}

function closeTaskEditor() {
  state.selectedTaskId = null;
  state.taskEditorInitialSnapshot = "";
  state.taskEditorDirty = false;
  toggleHidden("task-editor-section", true);
  restoreInlineEditorSection("task-editor-section");
  updateTaskEditButtons();
}

function renderEvents() {
  byId("events-body").innerHTML = state.events
    .map(
      (event) => `<tr>
        <td>${safeHtmlText(event.title)}</td>
        <td>${event.responsible_user_id ? memberNameHtml(event.responsible_user_id) : "-"}</td>
        <td>${fmtDate(event.start_at)}</td>
        <td>${fmtDate(event.end_at)}</td>
      </tr>`
    )
    .join("");
  applyMobileLabelsToTableBodies(["events-body"]);
}

function renderRewards() {
  const manager = isManagerRole();
  byId("rewards-body").innerHTML = state.rewards
    .map((reward) => {
      const actions = manager
        ? `<button data-reward-action="edit" data-reward-id="${reward.id}">Bearbeiten</button> <button data-reward-action="delete" data-reward-id="${reward.id}">Löschen</button>`
        : "-";
      return `<tr>
        <td>${safeHtmlText(reward.title)}</td>
        <td>${safeHtmlText(reward.description)}</td>
        <td>${reward.cost_points}</td>
        <td>${reward.is_shareable ? "ja" : "nein"}</td>
        <td>${reward.is_active ? "aktiv" : "deaktiviert"}</td>
        <td>${actions}</td>
      </tr>`;
    })
    .join("");
  applyMobileLabelsToTableBodies(["rewards-body"]);

  fillSelect(
    "redeem-reward-select",
    state.rewards
      .filter((reward) => reward.is_active)
      .map((reward) => ({
        value: reward.id,
        label: `${reward.title} • ${reward.cost_points} Punkte${reward.is_shareable ? " • aufteilbar" : ""}`,
      }))
  );

  if (state.selectedRewardId) {
    const selectedRewardExists = state.rewards.some((reward) => reward.id === state.selectedRewardId);
    if (!selectedRewardExists) {
      closeRewardEditor();
    } else {
      fillRewardEditorForm();
    }
  }

  renderSelectedRewardContribution();
}

function contributionStatusLabel(status) {
  const map = {
    reserved: "reserviert",
    submitted: "eingereicht",
    released: "freigegeben",
    consumed: "eingelöst",
  };
  return map[status] || status;
}

function renderSelectedRewardContribution() {
  const headingEl = byId("reward-redeem-heading");
  const pointsLabel = byId("redeem-points-label");
  const statusEl = byId("reward-contribution-status");
  const listEl = byId("reward-contribution-list");
  const contributeBtn = byId("redeem-reward-btn");
  const pointsInput = byId("redeem-points");
  if (!headingEl || !pointsLabel || !statusEl || !listEl || !contributeBtn || !pointsInput) return;

  if (!isChildRole()) {
    statusEl.textContent = "";
    listEl.innerHTML = "";
    contributeBtn.disabled = false;
    return;
  }

  const rewardId = Number(byId("redeem-reward-select").value || 0);
  const reward = state.rewards.find((entry) => entry.id === rewardId);
  const progress = state.selectedRewardContribution;

  if (!rewardId || !reward) {
    headingEl.textContent = "Belohnung anfragen";
    pointsLabel.classList.remove("hidden");
    contributeBtn.textContent = "Einlösung anfragen";
    statusEl.textContent = "Belohnung auswählen.";
    listEl.innerHTML = "";
    contributeBtn.disabled = true;
    return;
  }

  if (!reward.is_shareable) {
    headingEl.textContent = "Belohnung direkt einlösen";
    pointsLabel.classList.add("hidden");
    pointsInput.value = String(reward.cost_points);
    contributeBtn.textContent = "Einlösung anfragen";
    listEl.innerHTML = "";
    const ownBalance = getOwnBalance();
    const hasEnoughPoints = ownBalance === null || ownBalance >= reward.cost_points;
    statusEl.textContent = `Nicht aufteilbar • Kosten: ${reward.cost_points} Punkte`;
    contributeBtn.disabled = !hasEnoughPoints;
    return;
  }

  headingEl.textContent = "Punkte zu Belohnung beitragen";
  pointsLabel.classList.remove("hidden");
  contributeBtn.textContent = "Beitrag hinzufügen";

  if (!progress || progress.reward_id !== rewardId) {
    statusEl.textContent = `Belohnung: ${reward.title} • Lade Sammelstatus ...`;
    listEl.innerHTML = "";
    contributeBtn.disabled = true;
    return;
  }

  const ownBalance = getOwnBalance();
  const maxByRemaining = Number(progress.remaining_points || 0);
  const maxByBalance = Number.isFinite(Number(ownBalance)) ? Number(ownBalance) : maxByRemaining;
  const maxSelectable = Math.max(Math.min(maxByRemaining, maxByBalance), 0);
  if (maxSelectable > 0) {
    pointsInput.max = String(maxSelectable);
    const current = Number(pointsInput.value || 0);
    if (!current || current > maxSelectable) {
      pointsInput.value = String(maxSelectable);
    }
  } else {
    pointsInput.value = "";
    pointsInput.removeAttribute("max");
  }

  const pendingText = progress.pending_redemption_id
    ? " • Anfrage wartet bereits auf Bestätigung."
    : "";
  statusEl.textContent = `Gesamt: ${progress.total_reserved}/${progress.cost_points} Punkte • Fehlen: ${progress.remaining_points}${pendingText}`;

  listEl.innerHTML = progress.contributions.length
    ? progress.contributions
      .map(
        (entry) => `<article class="request-card">
          <p class="request-card-title">${safeHtmlText(entry.user_name)}: ${entry.points_reserved} Punkte</p>
          <p class="request-card-meta">Status: ${contributionStatusLabel(entry.status)} • ${fmtDate(entry.created_at)}</p>
        </article>`
      )
      .join("")
    : "<p class=\"muted\">Noch keine Beiträge vorhanden.</p>";

  contributeBtn.disabled = Boolean(progress.pending_redemption_id) || progress.remaining_points <= 0 || maxSelectable <= 0;
}

async function refreshSelectedRewardContribution() {
  if (!isChildRole()) return;
  const familyId = getSelectedFamilyId();
  const rewardId = Number(byId("redeem-reward-select").value || 0);
  if (!familyId || !rewardId) {
    state.selectedRewardContribution = null;
    renderSelectedRewardContribution();
    return;
  }
  const reward = state.rewards.find((entry) => entry.id === rewardId);
  if (!reward || !reward.is_shareable) {
    state.selectedRewardContribution = null;
    renderSelectedRewardContribution();
    return;
  }
  state.selectedRewardContribution = await api(`/families/${familyId}/rewards/${rewardId}/contributions`);
  renderSelectedRewardContribution();
}

function fillRewardEditorForm() {
  const rewardId = state.selectedRewardId;
  const reward = state.rewards.find((entry) => entry.id === rewardId);
  if (!reward) return;

  byId("reward-editor-title").value = reward.title || "";
  byId("reward-editor-description").value = reward.description || "";
  byId("reward-editor-cost").value = String(reward.cost_points);
  byId("reward-editor-shareable").value = reward.is_shareable ? "true" : "false";
  byId("reward-editor-active").value = reward.is_active ? "true" : "false";
}

function openRewardEditor(rewardId, triggerButton = null) {
  state.selectedRewardId = rewardId;
  fillRewardEditorForm();
  mountInlineEditorSectionBelowTrigger("reward-editor-section", triggerButton);
  toggleHidden("reward-editor-section", false);
}

function closeRewardEditor() {
  state.selectedRewardId = null;
  toggleHidden("reward-editor-section", true);
  restoreInlineEditorSection("reward-editor-section");
}

function renderRedemptions() {
  const visible = state.redemptions;

  byId("redemptions-body").innerHTML = visible
    .map((entry) => {
      const reward = state.rewards.find((r) => r.id === entry.reward_id);
      return `<tr>
        <td>${safeHtmlText(reward ? reward.title : "Unbekannte Belohnung")}</td>
        <td>${memberNameHtml(entry.requested_by_id)}</td>
        <td>${statusLabel(entry.status)}</td>
        <td>${fmtDate(entry.requested_at)}</td>
      </tr>`;
    })
    .join("");
  applyMobileLabelsToTableBodies(["redemptions-body"]);
  renderManagerRewardReviewCards();
  renderDashboardPendingRequests();
}

function renderManagerRewardReviewCards() {
  if (!isManagerRole()) return;
  const pending = getPendingRewardRequests();

  byId("manager-reward-review-cards").innerHTML = pending.length
    ? pending
      .map((entry) => {
        const reward = state.rewards.find((r) => r.id === entry.reward_id);
        return `<article class="request-card">
          <p class="request-card-title">${memberNameHtml(entry.requested_by_id)}: ${safeHtmlText(reward ? reward.title : "Belohnung")}</p>
          <p class="request-card-meta">Angefragt: ${fmtDate(entry.requested_at)}</p>
          <div class="request-card-actions">
            <button data-reward-review-action="approved" data-redemption-id="${entry.id}">Bestätigen</button>
            <button class="btn-secondary" data-reward-review-action="rejected" data-redemption-id="${entry.id}">Ablehnen</button>
          </div>
        </article>`;
      })
      .join("")
    : "<p class=\"muted\">Keine wartenden Belohnungen.</p>";
}

function getPointsUserDisplayName(userId) {
  const entry = state.pointsBalances.find((item) => item.user_id === userId);
  return entry ? entry.display_name : memberName(userId);
}

function renderPointsUsers() {
  const manager = isManagerRole();
  byId("points-users-body").innerHTML = state.pointsBalances
    .map((entry) => {
      const actions = manager
        ? `<button data-points-action="history" data-user-id="${entry.user_id}">Historie</button> <button data-points-action="edit" data-user-id="${entry.user_id}">Bearbeiten</button>`
        : "-";
      return `<tr>
        <td>${safeHtmlText(entry.display_name)}</td>
        <td>${roleLabel(entry.role)}</td>
        <td>${entry.balance}</td>
        <td>${actions}</td>
      </tr>`;
    })
    .join("");
  applyMobileLabelsToTableBodies(["points-users-body"]);
}

function renderPointsHistory() {
  const rows = state.pointsHistory
    .map(
      (entry) => `<tr>
        <td>${fmtDate(entry.created_at)}</td>
        <td>${pointsDeltaLabel(entry.points_delta)}</td>
        <td>${pointsSourceLabel(entry.source_type)}</td>
        <td>${safeHtmlText(entry.description)}</td>
      </tr>`
    )
    .join("");

  byId("points-history-body").innerHTML = rows || "<tr><td colspan=\"4\">Keine Buchungen vorhanden</td></tr>";
  applyMobileLabelsToTableBodies(["points-history-body"]);
}

async function loadMembers() {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;

  state.members = await api(`/families/${familyId}/members`);
  const selfMembership = state.members.find((member) => state.me && member.user_id === state.me.id);
  state.currentRole = selfMembership ? selfMembership.role : "child";

  applyRoleVisibility();
  renderMembers();
}

async function loadTasks() {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;
  state.tasks = await api(`/families/${familyId}/tasks`);
  renderTasks();
}

async function loadSpecialTasks() {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;

  if (isChildRole()) {
    state.specialTaskTemplates = [];
    state.availableSpecialTasks = await api(`/families/${familyId}/special-tasks/available`);
    renderChildSpecialTaskCards();
    byId("special-task-manager-cards").innerHTML = "";
    return;
  }

  state.availableSpecialTasks = [];
  state.specialTaskTemplates = await api(`/families/${familyId}/special-tasks/templates`);
  renderSpecialTaskTemplates();
  byId("child-special-task-cards").innerHTML = "";
}

async function loadEvents() {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;
  state.events = await api(`/families/${familyId}/events`);
  renderEvents();
}

async function loadRewards() {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;
  state.rewards = await api(`/families/${familyId}/rewards`);
  renderRewards();
  if (isChildRole()) {
    await refreshSelectedRewardContribution();
  }
}

async function loadRedemptions() {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;
  state.redemptions = await api(`/families/${familyId}/redemptions`);
  renderRedemptions();
}

async function loadPointsBalances() {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;
  state.pointsBalances = await api(`/families/${familyId}/points/balances`);
  renderDashboardPoints();
  renderPointsUsers();
}

async function loadPointsHistory(userId) {
  const familyId = getSelectedFamilyId();
  if (!familyId) return;

  try {
    state.pointsHistory = await api(`/families/${familyId}/points/ledger/${userId}`);
    byId("points-history-info").textContent = "";
  } catch (error) {
    state.pointsHistory = [];
    byId("points-history-info").textContent = `Hinweis: ${error.message}`;
  }

  renderPointsHistory();
}

function resetHomeAssistantSettingsForm() {
  state.haSettings = null;
  state.haUserConfigs = [];
  state.channelStatus = null;
  const channelInput = byId("notification-channel");
  const baseUrlInput = byId("ha-base-url");
  const tokenInput = byId("ha-token");
  const verifySslInput = byId("ha-verify-ssl");
  const resultTarget = byId("ha-settings-result");
  const userResultTarget = byId("ha-user-config-result");
  const userSelect = byId("ha-user-select");
  const userService = byId("ha-user-service");
  const userEnabled = byId("ha-user-enabled");
  const userBody = byId("ha-user-config-body");

  if (channelInput) channelInput.value = "sse";
  if (baseUrlInput) baseUrlInput.value = "";
  if (tokenInput) {
    tokenInput.value = "";
    tokenInput.dataset.masked = "false";
  }
  if (verifySslInput) verifySslInput.value = "true";
  if (resultTarget) resultTarget.textContent = "";
  if (userResultTarget) userResultTarget.textContent = "";
  if (userSelect) userSelect.innerHTML = "";
  if (userService) userService.value = "";
  if (userEnabled) userEnabled.value = "false";
  if (byId("ha-user-child-new-task")) byId("ha-user-child-new-task").checked = true;
  if (byId("ha-user-manager-task-submitted")) byId("ha-user-manager-task-submitted").checked = true;
  if (byId("ha-user-manager-reward-requested")) byId("ha-user-manager-reward-requested").checked = true;
  if (byId("ha-user-task-due-reminder")) byId("ha-user-task-due-reminder").checked = true;
  if (userBody) userBody.innerHTML = "";
  closeAllChannelPanels();
  closeHomeAssistantUserModal();
}

function applyHomeAssistantSettingsToForm(settingsPayload) {
  state.haSettings = settingsPayload;
  const channelInput = byId("notification-channel");
  const baseUrlInput = byId("ha-base-url");
  const tokenInput = byId("ha-token");
  const verifySslInput = byId("ha-verify-ssl");

  if (channelInput) channelInput.value = settingsPayload.notification_channel || "sse";
  if (baseUrlInput) baseUrlInput.value = settingsPayload.ha_base_url || "";
  if (tokenInput) {
    tokenInput.value = settingsPayload.has_token ? "********" : "";
    tokenInput.dataset.masked = settingsPayload.has_token ? "true" : "false";
  }
  if (verifySslInput) verifySslInput.value = settingsPayload.verify_ssl ? "true" : "false";
}

function closeAllChannelPanels() {
  ["apns", "home_assistant", "sse"].forEach((channel) => toggleHidden(`channel-panel-${channel}`, true));
}

function openChannelPanel(channel) {
  closeAllChannelPanels();
  toggleHidden(`channel-panel-${channel}`, false);
}

function closeHomeAssistantUserModal() {
  toggleHidden("ha-user-editor-modal", true);
}

function openHomeAssistantUserModal(userId) {
  const userSelect = byId("ha-user-select");
  if (userSelect) userSelect.value = String(userId);
  populateHomeAssistantUserEditor(userId);
  toggleHidden("ha-user-editor-modal", false);
}

function toggleChannelPanel(channel) {
  const panel = byId(`channel-panel-${channel}`);
  if (!panel) return;
  const currentlyHidden = panel.classList.contains("hidden");
  closeAllChannelPanels();
  if (currentlyHidden) toggleHidden(`channel-panel-${channel}`, false);
}

function getActiveMembersForTests() {
  return state.members
    .filter((entry) => entry.is_active)
    .sort((a, b) => String(a.display_name || "").localeCompare(String(b.display_name || ""), "de"));
}

function populateChannelTestRecipients() {
  const options = getActiveMembersForTests().map((member) => ({
    value: member.user_id,
    label: `${member.display_name} (${roleLabel(member.role)})`,
  }));
  fillSelect("apns-test-recipient", options, true, "Nutzer auswählen");
  fillSelect("sse-test-recipient", options, true, "Nutzer auswählen");
}

function boolLabel(value) {
  return value ? "ja" : "nein";
}

function summarizeChannelStatus(channel, channelData, activeChannel) {
  const active = activeChannel === channel;
  const configured = channelData.configured !== false;
  const prefix = `Aktiv: ${boolLabel(active)} • Konfiguriert: ${boolLabel(configured)}`;
  if (channel === "apns") {
    return `${prefix} • Geräte: ${Number(channelData.device_count || 0)} • ${channelData.status || "-"}`;
  }
  if (channel === "home_assistant") {
    return `${prefix} • URL: ${boolLabel(Boolean(channelData.has_url))} • Token: ${boolLabel(Boolean(channelData.has_token))} • Nutzer: ${Number(channelData.configured_user_count || 0)} • ${channelData.status || "-"}`;
  }
  return `${prefix} • ${channelData.status || "-"}`;
}

function updateNotificationChannelRows(statusPayload) {
  state.channelStatus = statusPayload;
  const activeChannel = statusPayload.active_channel || "sse";
  const channels = statusPayload.channels || {};
  const channelInput = byId("notification-channel");
  if (channelInput) channelInput.value = activeChannel;

  ["apns", "home_assistant", "sse"].forEach((channel) => {
    const row = byId(`channel-row-${channel}`);
    const checkbox = byId(`channel-active-${channel}`);
    const statusLabel = byId(`channel-status-${channel}`);
    const detailLabel = byId(`${channel === "home_assistant" ? "ha" : channel}-detail-status`);
    const channelData = channels[channel] || {};
    const statusText = summarizeChannelStatus(channel, channelData, activeChannel);

    if (checkbox) checkbox.checked = activeChannel === channel;
    if (statusLabel) statusLabel.textContent = statusText;
    if (detailLabel) detailLabel.textContent = statusText;
    if (row) row.classList.toggle("channel-row-active", activeChannel === channel);
  });
}

async function loadNotificationChannelStatus() {
  const familyId = getSelectedFamilyId();
  if (!familyId || !isManagerRole()) {
    state.channelStatus = null;
    return;
  }
  const payload = await api(`/families/${familyId}/system/notification-channels-status`);
  updateNotificationChannelRows(payload);
}

async function setActiveNotificationChannel(channel) {
  if (!isManagerRole()) return;
  const familyId = getSelectedFamilyId();
  if (!familyId) return;

  await api(`/families/${familyId}/system/notification-channel`, {
    method: "PUT",
    body: { channel },
  });
  if (state.haSettings) {
    state.haSettings.notification_channel = channel;
    const channelInput = byId("notification-channel");
    if (channelInput) channelInput.value = channel;
  }
  await loadNotificationChannelStatus();
}

async function sendChannelTest(channel, recipientUserId, title, message, resultTargetId) {
  const familyId = getSelectedFamilyId();
  const resultTarget = byId(resultTargetId);
  if (!familyId || !recipientUserId) {
    if (resultTarget) resultTarget.textContent = "Bitte einen Empfänger auswählen.";
    return;
  }
  const response = await api(`/families/${familyId}/system/test-notification`, {
    method: "POST",
    body: {
      title: title || "Testbenachrichtigung",
      message: message || "Testnachricht",
      recipient_user_ids: [Number(recipientUserId)],
      test_channel: channel,
    },
  });
  let text = `Gesendet an ${response.recipient_display_names.join(", ") || "-"} (Kanal: ${response.delivery_mode})`;
  if (response.home_assistant_delivery) {
    const ha = response.home_assistant_delivery;
    text += ` • HA: ${ha.sent_count || 0} gesendet, ${ha.failed_count || 0} fehlgeschlagen, ${ha.skipped_count || 0} übersprungen`;
    if (Array.isArray(ha.failures) && ha.failures.length > 0) {
      text += ` • Fehler: ${ha.failures.join(" | ")}`;
    }
  }
  if (resultTarget) resultTarget.textContent = text;
  log("Kanal-Test gesendet", {
    channel,
    recipient_user_ids: [Number(recipientUserId)],
    response,
  });
}

function haEventsText(entry) {
  const events = [];
  if (entry.ha_child_new_task) events.push("Kind: neue Aufgabe");
  if (entry.ha_manager_task_submitted) events.push("Eltern: Aufgabe eingereicht");
  if (entry.ha_manager_reward_requested) events.push("Eltern: Belohnung");
  if (entry.ha_task_due_reminder) events.push("Fälligkeits-Erinnerung");
  return events.length ? events.join(", ") : "-";
}

function populateHomeAssistantUserEditor(userId) {
  const numericUserId = Number(userId);
  const selected = state.haUserConfigs.find((entry) => entry.user_id === numericUserId);
  if (!selected) return;

  const userNameLabel = byId("ha-user-modal-name");
  const userService = byId("ha-user-service");
  const userEnabled = byId("ha-user-enabled");
  if (userNameLabel) userNameLabel.textContent = `${selected.display_name} (${roleLabel(selected.role)})`;
  if (userService) userService.value = selected.ha_notify_service || "";
  if (userEnabled) userEnabled.value = selected.ha_notifications_enabled ? "true" : "false";
  if (byId("ha-user-child-new-task")) byId("ha-user-child-new-task").checked = Boolean(selected.ha_child_new_task);
  if (byId("ha-user-manager-task-submitted")) byId("ha-user-manager-task-submitted").checked = Boolean(selected.ha_manager_task_submitted);
  if (byId("ha-user-manager-reward-requested")) byId("ha-user-manager-reward-requested").checked = Boolean(selected.ha_manager_reward_requested);
  if (byId("ha-user-task-due-reminder")) byId("ha-user-task-due-reminder").checked = Boolean(selected.ha_task_due_reminder);
}

function renderHomeAssistantUserConfigs() {
  const userSelect = byId("ha-user-select");
  const userBody = byId("ha-user-config-body");
  if (!userSelect || !userBody) return;

  const currentValue = Number(userSelect.value || 0);
  const sorted = [...state.haUserConfigs].sort((a, b) =>
    String(a.display_name || "").localeCompare(String(b.display_name || ""), "de")
  );

  userSelect.innerHTML = sorted
    .map((entry) => `<option value="${entry.user_id}">${safeHtmlText(entry.display_name)} (${safeHtmlText(roleLabel(entry.role))})</option>`)
    .join("");

  const hasCurrent = sorted.some((entry) => entry.user_id === currentValue);
  if (hasCurrent) {
    userSelect.value = String(currentValue);
  } else if (sorted.length > 0) {
    userSelect.value = String(sorted[0].user_id);
  }

  userBody.innerHTML = sorted
    .map((entry) => `<tr>
      <td>${safeHtmlText(entry.display_name)}</td>
      <td>${safeHtmlText(roleLabel(entry.role))}</td>
      <td>${safeHtmlText(entry.ha_notify_service, "-")}</td>
      <td>${entry.ha_notifications_enabled ? "ja" : "nein"}</td>
      <td>${safeHtmlText(haEventsText(entry), "-")}</td>
      <td>
        <button data-ha-user-action="edit" data-user-id="${entry.user_id}">Bearbeiten</button>
        <button data-ha-user-action="test" data-user-id="${entry.user_id}">Testen</button>
      </td>
    </tr>`)
    .join("");
  applyMobileLabelsToTableBodies(["ha-user-config-body"]);

  if (sorted.length > 0) {
    populateHomeAssistantUserEditor(Number(userSelect.value));
  }
}

async function loadHomeAssistantUserConfigs({ showStatus = false } = {}) {
  const familyId = getSelectedFamilyId();
  const resultTarget = byId("ha-user-config-result");
  if (!familyId || !isManagerRole()) {
    state.haUserConfigs = [];
    renderHomeAssistantUserConfigs();
    return;
  }
  state.haUserConfigs = await api(`/families/${familyId}/system/home-assistant-users`);
  renderHomeAssistantUserConfigs();
  if (showStatus && resultTarget) {
    resultTarget.textContent = `${state.haUserConfigs.length} Nutzer-Konfiguration(en) geladen.`;
  }
}

async function loadHomeAssistantSettings({ showStatus = false } = {}) {
  const familyId = getSelectedFamilyId();
  const resultTarget = byId("ha-settings-result");
  if (!familyId || !isManagerRole()) {
    resetHomeAssistantSettingsForm();
    return;
  }

  const settingsPayload = await api(`/families/${familyId}/system/home-assistant-settings`);
  applyHomeAssistantSettingsToForm(settingsPayload);
  if (showStatus && resultTarget) {
    const tokenStatus = settingsPayload.has_token ? "Token ist hinterlegt." : "Kein Token hinterlegt.";
    resultTarget.textContent = `HA-Konfiguration geladen. ${tokenStatus}`;
  }
}

async function saveHomeAssistantSettings() {
  if (!isManagerRole()) return false;

  clearInvalid(["ha-base-url", "ha-token"]);
  const familyId = getSelectedFamilyId();
  if (!familyId) return false;

  const resultTarget = byId("ha-settings-result");
  const channelInput = byId("notification-channel");
  const baseUrlInput = byId("ha-base-url");
  const tokenInput = byId("ha-token");
  const verifySslInput = byId("ha-verify-ssl");
  if (!channelInput || !baseUrlInput || !tokenInput || !verifySslInput) return false;

  const notification_channel = channelInput.value || "sse";
  const ha_base_url = baseUrlInput.value.trim();
  const ha_token = tokenInput.value.trim();
  const verify_ssl = verifySslInput.value === "true";
  const maskedToken = tokenInput.dataset.masked === "true" && ha_token === "********";
  const keep_existing_token = maskedToken || !ha_token;
  const hasStoredToken = Boolean(state.haSettings && state.haSettings.has_token);
  const hasTokenAfterSave = keep_existing_token ? hasStoredToken : Boolean(ha_token);
  const ha_enabled = Boolean(ha_base_url) && hasTokenAfterSave;

  let invalid = false;
  const requiresHaConfig = notification_channel === "home_assistant";
  if (requiresHaConfig && !ha_base_url) {
    setInvalid(baseUrlInput, true);
    invalid = true;
  }
  if (requiresHaConfig && keep_existing_token && !hasStoredToken) {
    setInvalid(tokenInput, true);
    invalid = true;
  }
  if (invalid) {
    if (resultTarget) resultTarget.textContent = "Für den Kanal Home Assistant sind URL und Token erforderlich.";
    return false;
  }

  const response = await api(`/families/${familyId}/system/home-assistant-settings`, {
    method: "PUT",
    body: {
      ha_enabled,
      notification_channel,
      ha_base_url: ha_base_url || null,
      ha_token: keep_existing_token ? null : ha_token,
      verify_ssl,
      keep_existing_token,
    },
  });
  applyHomeAssistantSettingsToForm(response);
  if (resultTarget) {
    const tokenStatus = response.has_token ? "Token gespeichert." : "Kein Token gespeichert.";
    const configStatus = response.ha_enabled ? "HA aktiviert." : "HA deaktiviert.";
    resultTarget.textContent = `HA-Einstellungen gespeichert. ${configStatus} ${tokenStatus}`;
  }
  log("HA-Einstellungen gespeichert", {
    notification_channel: response.notification_channel,
    ha_enabled: response.ha_enabled,
    has_token: response.has_token,
  });
  return true;
}

async function saveHomeAssistantUserConfig() {
  if (!isManagerRole()) return;

  clearInvalid(["ha-user-service"]);
  const familyId = getSelectedFamilyId();
  const resultTarget = byId("ha-user-config-result");
  const userSelect = byId("ha-user-select");
  const userService = byId("ha-user-service");
  const userEnabled = byId("ha-user-enabled");
  if (!familyId || !userSelect || !userService || !userEnabled) return;

  const userId = Number(userSelect.value || 0);
  if (!userId) {
    if (resultTarget) resultTarget.textContent = "Bitte zuerst einen Nutzer auswählen.";
    return;
  }

  const ha_notify_service = userService.value.trim();
  const ha_notifications_enabled = userEnabled.value === "true";
  if (ha_notifications_enabled && !ha_notify_service) {
    setInvalid(userService, true);
    if (resultTarget) resultTarget.textContent = "Aktivierte Nutzer benötigen einen HA-Service.";
    return;
  }

  const response = await api(`/families/${familyId}/system/home-assistant-users/${userId}`, {
    method: "PUT",
    body: {
      ha_notify_service: ha_notify_service || null,
      ha_notifications_enabled,
      ha_child_new_task: Boolean(byId("ha-user-child-new-task") && byId("ha-user-child-new-task").checked),
      ha_manager_task_submitted: Boolean(byId("ha-user-manager-task-submitted") && byId("ha-user-manager-task-submitted").checked),
      ha_manager_reward_requested: Boolean(byId("ha-user-manager-reward-requested") && byId("ha-user-manager-reward-requested").checked),
      ha_task_due_reminder: Boolean(byId("ha-user-task-due-reminder") && byId("ha-user-task-due-reminder").checked),
    },
  });

  state.haUserConfigs = state.haUserConfigs.map((entry) => (entry.user_id === response.user_id ? response : entry));
  if (!state.haUserConfigs.some((entry) => entry.user_id === response.user_id)) {
    state.haUserConfigs.push(response);
  }
  renderHomeAssistantUserConfigs();
  if (resultTarget) resultTarget.textContent = `Nutzerkonfiguration gespeichert: ${response.display_name}`;
  log("HA Nutzerkonfiguration gespeichert", {
    user_id: response.user_id,
    display_name: response.display_name,
    service: response.ha_notify_service,
    notifications_enabled: response.ha_notifications_enabled,
  });
  closeHomeAssistantUserModal();
}

async function sendHomeAssistantUserTest(userIdOverride = null) {
  if (!isManagerRole()) return;

  const familyId = getSelectedFamilyId();
  const resultTarget = byId("ha-user-config-result");
  const userSelect = byId("ha-user-select");
  if (!familyId) return;

  const userId = userIdOverride ? Number(userIdOverride) : Number(userSelect ? userSelect.value || 0 : 0);
  if (!userId) {
    if (resultTarget) resultTarget.textContent = "Bitte zuerst einen Nutzer auswählen.";
    return;
  }

  const response = await api(`/families/${familyId}/system/home-assistant-users/${userId}/test`, {
    method: "POST",
    body: {
      title: "Home Assistant Test",
      message: "Testnachricht aus HomeQuests",
    },
  });
  const delivery = response.delivery || {};
  if (resultTarget) {
    resultTarget.textContent = `Test gesendet: ${response.sent ? "ja" : "nein"} (gesendet=${delivery.sent_count || 0}, fehlgeschlagen=${delivery.failed_count || 0}, übersprungen=${delivery.skipped_count || 0})`;
  }
  log("HA Nutzertest", {
    user_id: userId,
    sent: response.sent,
    delivery,
  });
}

async function refreshFamilyData() {
  restoreAllInlineEditorSections();
  await loadMembers();
  await Promise.all([loadTasks(), loadSpecialTasks(), loadEvents(), loadRewards(), loadRedemptions(), loadPointsBalances()]);
  if (isManagerRole()) {
    await loadNotificationChannelStatus().catch((error) =>
      log("Kanalstatus laden Fehler", { error: error.message })
    );
    await loadHomeAssistantSettings({ showStatus: false }).catch((error) =>
      log("HA Einstellungen laden Fehler", { error: error.message })
    );
    await loadHomeAssistantUserConfigs({ showStatus: false }).catch((error) =>
      log("HA Nutzer laden Fehler", { error: error.message })
    );
  } else {
    resetHomeAssistantSettingsForm();
  }

  if (isChildRole() && state.me) {
    const own = state.pointsBalances.find((entry) => entry.user_id === state.me.id);
    const ownBalance = own ? own.balance : null;
    byId("child-reward-points").textContent = ownBalance ?? "-";
    byId("stat-child-points-value").textContent = ownBalance ?? "-";
    state.selectedPointsUserId = state.me.id;
    byId("points-history-title").textContent = "Deine Punkte-Historie";
    await loadPointsHistory(state.me.id);
    toggleHidden("points-adjust-section", true);
  } else {
    byId("child-reward-points").textContent = "-";
    byId("stat-child-points-value").textContent = "-";
    state.selectedRewardContribution = null;
    if (
      state.selectedPointsUserId &&
      !state.pointsBalances.some((entry) => entry.user_id === state.selectedPointsUserId)
    ) {
      state.selectedPointsUserId = null;
    }
    if (!state.selectedPointsUserId && state.pointsBalances.length > 0) {
      state.selectedPointsUserId = state.pointsBalances[0].user_id;
    }
    if (state.selectedPointsUserId) {
      byId("points-history-title").textContent = `Punkte-Historie: ${getPointsUserDisplayName(state.selectedPointsUserId)}`;
      await loadPointsHistory(state.selectedPointsUserId);
    } else {
      state.pointsHistory = [];
      byId("points-history-title").textContent = "Punkte-Historie";
      byId("points-history-info").textContent = "Keine Nutzer vorhanden.";
      renderPointsHistory();
    }
  }

  renderSelectedRewardContribution();

  const emailPart = state.me.email ? ` (${state.me.email})` : "";
  userInfo.textContent = `Angemeldet als ${state.me.display_name}${emailPart} | Rolle: ${roleLabel(state.currentRole)}`;
}

async function refreshSession() {
  try {
    state.me = await api("/auth/me");
    state.families = await api("/families/my");

    if (familySelect) {
      familySelect.innerHTML = "";
      state.families.forEach((family) => {
        const option = document.createElement("option");
        option.value = String(family.id);
        option.textContent = family.name;
        familySelect.appendChild(option);
      });
    }

    state.familyId = state.families[0]?.id || null;
    if (!state.familyId) throw new Error("Keine Familie gefunden");

    if (familySelect) {
      familySelect.value = String(state.familyId);
    }
    toggleHidden("family-select-wrap", true);

    authPanel.classList.add("hidden");
    appPanel.classList.remove("hidden");

    await refreshFamilyData();
    startLiveUpdates();
    if (isChildRole()) startSpecialTaskRefreshTicker();
    else stopSpecialTaskRefreshTicker();
  } catch (error) {
    log("Session Fehler", { error: error.message });
    stopLiveUpdates({ resetCursor: true });
    stopSpecialTaskRefreshTicker();
    await logout();
  }
}

async function login() {
  clearInvalid(["login-email", "login-password"]);
  const loginInput = byId("login-email");
  const passwordInput = byId("login-password");

  const loginValue = loginInput.value.trim();
  const password = passwordInput.value;

  let invalid = false;
  if (!loginValue) {
    setInvalid(loginInput, true);
    invalid = true;
  }
  if (!password) {
    setInvalid(passwordInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Login: Bitte Pflichtfelder korrekt ausfuellen");
    return;
  }

  await api("/auth/login", { method: "POST", body: { login: loginValue, password } });
  await refreshSession();
}

async function bootstrap() {
  clearInvalid(["boot-name", "boot-email", "boot-password", "boot-password-confirm"]);
  const nameInput = byId("boot-name");
  const emailInput = byId("boot-email");
  const passwordInput = byId("boot-password");
  const passwordConfirmInput = byId("boot-password-confirm");

  const display_name = nameInput.value.trim();
  const email = emailInput.value.trim();
  const password = passwordInput.value;
  const password_confirm = passwordConfirmInput.value;

  let invalid = false;
  const validationMessages = [];
  if (!display_name) {
    setInvalid(nameInput, true);
    invalid = true;
    validationMessages.push("Name fehlt");
  }
  if (email && !isValidEmail(email)) {
    setInvalid(emailInput, true);
    invalid = true;
    validationMessages.push("E-Mail ist ungültig");
  }
  if (password.length < 8) {
    setInvalid(passwordInput, true);
    invalid = true;
    validationMessages.push("Passwort muss mindestens 8 Zeichen haben");
  }
  if (password !== password_confirm || password_confirm.length < 8) {
    setInvalid(passwordConfirmInput, true);
    invalid = true;
    validationMessages.push("Passwort-Bestätigung muss identisch sein und mindestens 8 Zeichen haben");
  }
  if (invalid) {
    log("Initialisierung: Bitte Eingaben prüfen", { details: validationMessages });
    return;
  }

  await api("/auth/bootstrap", {
    method: "POST",
    body: { display_name, email: email || null, password, password_confirm },
  });

  await refreshSession();
}

async function logout() {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch (_) {
    // Local cleanup still runs below.
  }
  restoreAllInlineEditorSections();
  stopLiveUpdates({ resetCursor: true });
  stopSpecialTaskRefreshTicker();
  state.me = null;
  state.families = [];
  state.familyId = null;
  state.currentRole = null;
  state.selectedTaskId = null;
  state.selectedSpecialTaskTemplateId = null;
  state.selectedMemberId = null;
  state.selectedRewardId = null;
  state.selectedRewardContribution = null;
  state.selectedPointsUserId = null;
  state.haSettings = null;
  state.haUserConfigs = [];
  state.specialTaskTemplates = [];
  state.availableSpecialTasks = [];
  state.pointsHistory = [];
  state.tasksSort = "updated_desc";
  state.specialTasksSort = "updated_desc";
  state.taskEditorDirty = false;
  state.specialTaskEditorDirty = false;
  state.taskEditorInitialSnapshot = "";
  state.specialTaskEditorInitialSnapshot = "";
  initAuthPanel().catch((error) => log("Auth-Ansicht Fehler", { error: error.message }));
}

async function createMember() {
  if (!canManageMembers()) return;

  clearInvalid(["member-name", "member-email", "member-ha-notify-service", "member-password", "member-password-confirm"]);
  const nameInput = byId("member-name");
  const emailInput = byId("member-email");
  const haNotifyServiceInput = byId("member-ha-notify-service");
  const passwordInput = byId("member-password");
  const passwordConfirmInput = byId("member-password-confirm");

  const display_name = nameInput.value.trim();
  const email = emailInput.value.trim();
  const ha_notify_service = haNotifyServiceInput.value.trim();
  const password = passwordInput.value;
  const password_confirm = passwordConfirmInput.value;

  let invalid = false;
  if (!display_name) {
    setInvalid(nameInput, true);
    invalid = true;
  }
  if (email && !isValidEmail(email)) {
    setInvalid(emailInput, true);
    invalid = true;
  }
  if (password.length < 8) {
    setInvalid(passwordInput, true);
    invalid = true;
  }
  if (password !== password_confirm || password_confirm.length < 8) {
    setInvalid(passwordConfirmInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Mitglied: Bitte Felder korrekt ausfuellen");
    return;
  }

  await api(`/families/${getSelectedFamilyId()}/members`, {
    method: "POST",
    body: {
      display_name,
      email: email || null,
      ha_notify_service: ha_notify_service || null,
      password,
      password_confirm,
      role: byId("member-role").value,
    },
  });

  nameInput.value = "";
  emailInput.value = "";
  haNotifyServiceInput.value = "";
  passwordInput.value = "";
  passwordConfirmInput.value = "";
  setSectionOpen("member-create-section", "toggle-member-create-btn", false, "Neues Mitglied", "Eingabe schließen");
  await refreshFamilyData();
}

async function updateMember() {
  if (!canManageMembers()) return;

  clearInvalid(["member-editor-name", "member-editor-ha-notify-service", "member-editor-password"]);
  const memberId = state.selectedMemberId;
  if (!memberId) {
    log("Bitte zuerst ein Mitglied in der Tabelle auf Bearbeiten klicken");
    return;
  }

  const nameInput = byId("member-editor-name");
  const haNotifyServiceInput = byId("member-editor-ha-notify-service");
  const passwordInput = byId("member-editor-password");

  const display_name = nameInput.value.trim();
  const ha_notify_service = haNotifyServiceInput.value.trim();
  const password = passwordInput.value;

  let invalid = false;
  if (!display_name) {
    setInvalid(nameInput, true);
    invalid = true;
  }
  if (password && password.length < 8) {
    setInvalid(passwordInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Mitglied bearbeiten: Bitte Felder korrekt ausfuellen");
    return;
  }

  await api(`/families/${getSelectedFamilyId()}/members/${memberId}`, {
    method: "PUT",
    body: {
      display_name,
      ha_notify_service: ha_notify_service || null,
      role: byId("member-editor-role").value,
      is_active: byId("member-editor-active").value === "true",
      password: password || null,
    },
  });

  closeMemberEditor();
  await refreshFamilyData();
}

async function deleteMember(memberId) {
  if (!canManageMembers()) return;
  await api(`/families/${getSelectedFamilyId()}/members/${memberId}`, { method: "DELETE" });
  if (state.selectedMemberId === memberId) {
    closeMemberEditor();
  }
  await refreshFamilyData();
}

async function createSpecialTaskTemplate() {
  if (!isManagerRole()) return;

  clearInvalid(["special-task-title", "special-task-points", "special-task-limit", "special-task-due-time"]);
  setInvalid(byId("special-task-weekdays-row"), false);
  const titleInput = byId("special-task-title");
  const pointsInput = byId("special-task-points");
  const limitInput = byId("special-task-limit");
  const dueTimeInput = byId("special-task-due-time");

  const title = titleInput.value.trim();
  const points = Number(pointsInput.value || 0);
  const max_claims_per_interval = Number(limitInput.value || 0);
  const interval_type = byId("special-task-interval").value;
  const active_weekdays = interval_type === "daily" ? getSelectedWeekdays("special-task-weekdays") : [];
  const due_time_hhmm = interval_type === "daily" ? normalizeTimeValueOrNull(dueTimeInput.value) : null;

  let invalid = false;
  if (!title) {
    setInvalid(titleInput, true);
    invalid = true;
  }
  if (Number.isNaN(points) || points < 0) {
    setInvalid(pointsInput, true);
    invalid = true;
  }
  if (Number.isNaN(max_claims_per_interval) || max_claims_per_interval < 1) {
    setInvalid(limitInput, true);
    invalid = true;
  }
  if (interval_type === "daily" && !due_time_hhmm) {
    setInvalid(dueTimeInput, true);
    invalid = true;
  }
  if (interval_type === "daily" && active_weekdays.length === 0) {
    setInvalid(byId("special-task-weekdays-row"), true);
    invalid = true;
  }
  if (invalid) {
    log("Sonderaufgabe: Bitte Felder korrekt ausfüllen");
    return;
  }

  await api(`/families/${getSelectedFamilyId()}/special-tasks/templates`, {
    method: "POST",
    body: {
      title,
      description: byId("special-task-description").value.trim() || null,
      points,
      interval_type,
      max_claims_per_interval,
      active_weekdays,
      due_time_hhmm,
      is_active: byId("special-task-active").value === "true",
    },
  });

  titleInput.value = "";
  byId("special-task-description").value = "";
  pointsInput.value = "5";
  limitInput.value = "1";
  byId("special-task-interval").value = "daily";
  byId("special-task-due-time").value = "18:00";
  setSelectedWeekdays("special-task-weekdays", [0, 1, 2, 3, 4, 5, 6]);
  byId("special-task-active").value = "true";
  syncSpecialTaskCreateTimingUI();
  setSectionOpen("special-task-create-section", "toggle-special-task-create-btn", false, "Neue Sonderaufgabe", "Eingabe schließen");
  await refreshFamilyData();
}

async function updateSpecialTaskTemplate() {
  if (!isManagerRole()) return;
  const templateId = state.selectedSpecialTaskTemplateId;
  if (!templateId) {
    log("Bitte zuerst eine Sonderaufgabe auf Bearbeiten klicken");
    return;
  }

  clearInvalid(["special-task-editor-title", "special-task-editor-points", "special-task-editor-limit", "special-task-editor-due-time"]);
  setInvalid(byId("special-task-editor-weekdays-row"), false);
  const titleInput = byId("special-task-editor-title");
  const pointsInput = byId("special-task-editor-points");
  const limitInput = byId("special-task-editor-limit");
  const dueTimeInput = byId("special-task-editor-due-time");

  const title = titleInput.value.trim();
  const points = Number(pointsInput.value || 0);
  const max_claims_per_interval = Number(limitInput.value || 0);
  const interval_type = byId("special-task-editor-interval").value;
  const active_weekdays = interval_type === "daily" ? getSelectedWeekdays("special-task-editor-weekdays") : [];
  const due_time_hhmm = interval_type === "daily" ? normalizeTimeValueOrNull(dueTimeInput.value) : null;

  let invalid = false;
  if (!title) {
    setInvalid(titleInput, true);
    invalid = true;
  }
  if (Number.isNaN(points) || points < 0) {
    setInvalid(pointsInput, true);
    invalid = true;
  }
  if (Number.isNaN(max_claims_per_interval) || max_claims_per_interval < 1) {
    setInvalid(limitInput, true);
    invalid = true;
  }
  if (interval_type === "daily" && !due_time_hhmm) {
    setInvalid(dueTimeInput, true);
    invalid = true;
  }
  if (interval_type === "daily" && active_weekdays.length === 0) {
    setInvalid(byId("special-task-editor-weekdays-row"), true);
    invalid = true;
  }
  if (invalid) {
    log("Sonderaufgabe bearbeiten: Bitte Felder korrekt ausfüllen");
    return;
  }

  await api(`/special-tasks/templates/${templateId}`, {
    method: "PUT",
    body: {
      title,
      description: byId("special-task-editor-description").value.trim() || null,
      points,
      interval_type,
      max_claims_per_interval,
      active_weekdays,
      due_time_hhmm,
      is_active: byId("special-task-editor-active").value === "true",
    },
  });

  closeSpecialTaskEditor();
  await refreshFamilyData();
}

async function deleteSpecialTaskTemplate(templateId) {
  if (!isManagerRole()) return;
  await api(`/special-tasks/templates/${templateId}`, { method: "DELETE" });
  if (state.selectedSpecialTaskTemplateId === templateId) {
    closeSpecialTaskEditor();
  }
  await refreshFamilyData();
}

async function claimSpecialTaskTemplate(templateId) {
  if (!isChildRole()) return;
  await api(`/special-tasks/templates/${templateId}/claim`, { method: "POST" });
  await refreshFamilyData();
}

async function createTask() {
  if (!isManagerRole()) return;

  clearInvalid(["task-title", "task-assignee", "task-points", "task-due", "task-daily-time", "task-weekly-day", "task-weekly-time", "task-penalty-points"]);
  const titleInput = byId("task-title");
  const assigneeInput = byId("task-assignee");
  const pointsInput = byId("task-points");
  const dueInput = byId("task-due");
  const dailyTimeInput = byId("task-daily-time");
  const weeklyDayInput = byId("task-weekly-day");
  const weeklyTimeInput = byId("task-weekly-time");
  const penaltyPointsInput = byId("task-penalty-points");

  const title = titleInput.value.trim();
  const assignee_id = Number(assigneeInput.value);
  const points = Number(pointsInput.value || 0);
  const recurrence_type = byId("task-recurrence").value;
  const always_submittable = byId("task-always-submittable").value === "true";
  const active_weekdays = recurrence_type === "daily" ? getSelectedWeekdays("task-weekdays") : [];
  const dueMode = recurrence_type === "weekly" ? byId("task-due-mode").value : "exact";
  const penaltySupported = recurrence_type === "daily" || (recurrence_type === "weekly" && dueMode === "exact");
  const penalty_enabled = penaltySupported && byId("task-penalty-enabled").value === "true";
  const penalty_points = penalty_enabled ? Number(penaltyPointsInput.value || 0) : 0;
  const dueRaw = dueInput.value;
  const reminder_offsets_minutes = (recurrence_type === "weekly" && dueMode === "week_flexible")
    ? []
    : getSelectedReminderOffsets("task-reminder-options");

  let invalid = false;
  if (!title) {
    setInvalid(titleInput, true);
    invalid = true;
  }
  if (!assignee_id) {
    setInvalid(assigneeInput, true);
    invalid = true;
  }
  if (Number.isNaN(points) || points < 0) {
    setInvalid(pointsInput, true);
    invalid = true;
  }
  if (recurrence_type === "daily" && !dailyTimeInput.value) {
    setInvalid(dailyTimeInput, true);
    invalid = true;
  }
  if (recurrence_type === "weekly" && dueMode === "exact" && !weeklyTimeInput.value) {
    setInvalid(weeklyTimeInput, true);
    invalid = true;
  }
  if (recurrence_type === "daily" && active_weekdays.length === 0) {
    setInvalid(byId("task-weekdays-row"), true);
    invalid = true;
  } else {
    setInvalid(byId("task-weekdays-row"), false);
  }
  if ((recurrence_type === "none" || recurrence_type === "monthly") && reminder_offsets_minutes.length > 0 && !dueRaw) {
    setInvalid(dueInput, true);
    invalid = true;
  }
  if (penalty_enabled && (Number.isNaN(penalty_points) || penalty_points < 1)) {
    setInvalid(penaltyPointsInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Aufgabe: Bitte Felder korrekt ausfüllen");
    return;
  }

  let due_at = null;
  if (recurrence_type === "daily") {
    due_at = buildNextDailyDueIso(dailyTimeInput.value, active_weekdays);
    if (!due_at) {
      setInvalid(dailyTimeInput, true);
      log("Aufgabe: Ungültige tägliche Uhrzeit oder Wochentage");
      return;
    }
  } else if (recurrence_type === "weekly" && dueMode === "exact") {
    due_at = buildNextWeeklyDueIso(weeklyDayInput.value, weeklyTimeInput.value);
    if (!due_at) {
      setInvalid(weeklyTimeInput, true);
      log("Aufgabe: Ungültige wöchentliche Uhrzeit oder Wochentag");
      return;
    }
  } else if (recurrence_type === "weekly" && dueMode === "week_flexible") {
    due_at = null;
  } else {
    due_at = toLocalIsoNoTimezoneOrNull(dueRaw);
  }

  await api(`/families/${getSelectedFamilyId()}/tasks`, {
    method: "POST",
    body: {
      title,
      description: byId("task-description").value.trim() || null,
      assignee_id,
      due_at,
      points,
      reminder_offsets_minutes,
      active_weekdays,
      recurrence_type,
      always_submittable,
      penalty_enabled,
      penalty_points,
    },
  });

  titleInput.value = "";
  byId("task-description").value = "";
  byId("task-due").value = "";
  byId("task-daily-time").value = "18:00";
  byId("task-weekly-day").value = "0";
  byId("task-weekly-time").value = "09:00";
  byId("task-always-submittable").value = "false";
  byId("task-penalty-enabled").value = "false";
  byId("task-penalty-points").value = "5";
  setSelectedWeekdays("task-weekdays", [0, 1, 2, 3, 4, 5, 6]);
  byId("task-due-mode").value = "exact";
  setSelectedReminderOffsets("task-reminder-options", []);
  setInvalid(byId("task-weekdays-row"), false);
  syncTaskCreateTimingUI();
  setSectionOpen("task-create-section", "toggle-task-create-btn", false, "Neue Aufgabe", "Eingabe schließen");
  await refreshFamilyData();
}

async function updateTask() {
  if (!isManagerRole()) return;

  clearInvalid(["task-editor-title", "task-editor-assignee", "task-editor-points", "task-editor-due", "task-editor-daily-time", "task-editor-weekly-day", "task-editor-weekly-time", "task-editor-penalty-points"]);
  const taskId = state.selectedTaskId;
  if (!taskId) {
    log("Bitte zuerst eine Aufgabe in der Tabelle auf Bearbeiten klicken");
    return;
  }

  const titleInput = byId("task-editor-title");
  const assigneeInput = byId("task-editor-assignee");
  const pointsInput = byId("task-editor-points");
  const dueInput = byId("task-editor-due");
  const dailyTimeInput = byId("task-editor-daily-time");
  const weeklyDayInput = byId("task-editor-weekly-day");
  const weeklyTimeInput = byId("task-editor-weekly-time");
  const penaltyPointsInput = byId("task-editor-penalty-points");

  const title = titleInput.value.trim();
  const assignee_id = Number(assigneeInput.value);
  const points = Number(pointsInput.value || 0);
  const recurrence_type = byId("task-editor-recurrence").value;
  const always_submittable = byId("task-editor-always-submittable").value === "true";
  const active_weekdays = recurrence_type === "daily" ? getSelectedWeekdays("task-editor-weekdays") : [];
  const dueMode = recurrence_type === "weekly" ? byId("task-editor-due-mode").value : "exact";
  const penaltySupported = recurrence_type === "daily" || (recurrence_type === "weekly" && dueMode === "exact");
  const penalty_enabled = penaltySupported && byId("task-editor-penalty-enabled").value === "true";
  const penalty_points = penalty_enabled ? Number(penaltyPointsInput.value || 0) : 0;
  const dueRaw = dueInput.value;
  const reminder_offsets_minutes = (recurrence_type === "weekly" && dueMode === "week_flexible")
    ? []
    : getSelectedReminderOffsets("task-editor-reminder-options");

  let invalid = false;
  if (!title) {
    setInvalid(titleInput, true);
    invalid = true;
  }
  if (!assignee_id) {
    setInvalid(assigneeInput, true);
    invalid = true;
  }
  if (Number.isNaN(points) || points < 0) {
    setInvalid(pointsInput, true);
    invalid = true;
  }
  if (recurrence_type === "daily" && !dailyTimeInput.value) {
    setInvalid(dailyTimeInput, true);
    invalid = true;
  }
  if (recurrence_type === "weekly" && dueMode === "exact" && !weeklyTimeInput.value) {
    setInvalid(weeklyTimeInput, true);
    invalid = true;
  }
  if (recurrence_type === "daily" && active_weekdays.length === 0) {
    setInvalid(byId("task-editor-weekdays-row"), true);
    invalid = true;
  } else {
    setInvalid(byId("task-editor-weekdays-row"), false);
  }
  if ((recurrence_type === "none" || recurrence_type === "monthly") && reminder_offsets_minutes.length > 0 && !dueRaw) {
    setInvalid(dueInput, true);
    invalid = true;
  }
  if (penalty_enabled && (Number.isNaN(penalty_points) || penalty_points < 1)) {
    setInvalid(penaltyPointsInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Aufgabe bearbeiten: Bitte Felder korrekt ausfüllen");
    return;
  }

  let due_at = null;
  if (recurrence_type === "daily") {
    due_at = buildNextDailyDueIso(dailyTimeInput.value, active_weekdays);
    if (!due_at) {
      setInvalid(dailyTimeInput, true);
      log("Aufgabe bearbeiten: Ungültige tägliche Uhrzeit oder Wochentage");
      return;
    }
  } else if (recurrence_type === "weekly" && dueMode === "exact") {
    due_at = buildNextWeeklyDueIso(weeklyDayInput.value, weeklyTimeInput.value);
    if (!due_at) {
      setInvalid(weeklyTimeInput, true);
      log("Aufgabe bearbeiten: Ungültige wöchentliche Uhrzeit oder Wochentag");
      return;
    }
  } else if (recurrence_type === "weekly" && dueMode === "week_flexible") {
    due_at = null;
  } else {
    due_at = toLocalIsoNoTimezoneOrNull(dueRaw);
  }

  await api(`/tasks/${taskId}`, {
    method: "PUT",
    body: {
      title,
      description: byId("task-editor-description").value.trim() || null,
      assignee_id,
      due_at,
      points,
      reminder_offsets_minutes,
      active_weekdays,
      always_submittable,
      penalty_enabled,
      penalty_points,
      is_active: byId("task-editor-active").value === "true",
      status: byId("task-editor-status").value,
      recurrence_type,
    },
  });

  closeTaskEditor();
  await refreshFamilyData();
}

async function submitTaskById(taskId, note = null) {
  await api(`/tasks/${taskId}/submit`, {
    method: "POST",
    body: { note },
  });
  await refreshFamilyData();
}

async function reportMissedTaskById(taskId) {
  await api(`/tasks/${taskId}/report-missed`, {
    method: "POST",
  });
  await refreshFamilyData();
}

async function deleteTask(taskId) {
  if (!isManagerRole()) return;
  await api(`/tasks/${taskId}`, { method: "DELETE" });
  if (state.selectedTaskId === taskId) {
    closeTaskEditor();
  }
  await refreshFamilyData();
}

async function setTaskActive(taskId, is_active) {
  if (!isManagerRole()) return;
  await api(`/tasks/${taskId}/active`, {
    method: "POST",
    body: { is_active },
  });
  await refreshFamilyData();
}

async function createEvent() {
  if (!isManagerRole()) return;

  clearInvalid(["event-title", "event-start", "event-end"]);
  const titleInput = byId("event-title");
  const startInput = byId("event-start");
  const endInput = byId("event-end");

  const title = titleInput.value.trim();
  const start = startInput.value;
  const end = endInput.value;

  let invalid = false;
  if (!title) {
    setInvalid(titleInput, true);
    invalid = true;
  }
  if (!start) {
    setInvalid(startInput, true);
    invalid = true;
  }
  if (!end) {
    setInvalid(endInput, true);
    invalid = true;
  }
  if (start && end && new Date(end) <= new Date(start)) {
    setInvalid(startInput, true);
    setInvalid(endInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Kalender: Bitte Felder korrekt ausfuellen");
    return;
  }

  const responsibleRaw = byId("event-responsible").value;
  await api(`/families/${getSelectedFamilyId()}/events`, {
    method: "POST",
    body: {
      title,
      description: byId("event-description").value.trim() || null,
      responsible_user_id: responsibleRaw ? Number(responsibleRaw) : null,
      start_at: toIsoOrNull(start),
      end_at: toIsoOrNull(end),
    },
  });

  titleInput.value = "";
  byId("event-description").value = "";
  startInput.value = "";
  endInput.value = "";
  setSectionOpen("event-create-section", "toggle-event-create-btn", false, "Neuer Termin", "Eingabe schließen");
  await refreshFamilyData();
}

async function createReward() {
  if (!isManagerRole()) return;

  clearInvalid(["reward-title", "reward-cost"]);
  const titleInput = byId("reward-title");
  const costInput = byId("reward-cost");

  const title = titleInput.value.trim();
  const cost_points = Number(costInput.value || 0);

  let invalid = false;
  if (!title) {
    setInvalid(titleInput, true);
    invalid = true;
  }
  if (Number.isNaN(cost_points) || cost_points < 1) {
    setInvalid(costInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Belohnung: Bitte Felder korrekt ausfuellen");
    return;
  }

  await api(`/families/${getSelectedFamilyId()}/rewards`, {
    method: "POST",
    body: {
      title,
      description: byId("reward-description").value.trim() || null,
      cost_points,
      is_shareable: byId("reward-shareable").value === "true",
      is_active: true,
    },
  });

  titleInput.value = "";
  byId("reward-description").value = "";
  byId("reward-shareable").value = "false";
  setSectionOpen("reward-create-section", "toggle-reward-create-btn", false, "Neue Belohnung", "Eingabe schließen");
  await refreshFamilyData();
}

async function updateReward() {
  if (!isManagerRole()) return;

  clearInvalid(["reward-editor-title", "reward-editor-cost"]);
  const rewardId = state.selectedRewardId;
  if (!rewardId) {
    log("Bitte zuerst eine Belohnung in der Tabelle auf Bearbeiten klicken");
    return;
  }

  const titleInput = byId("reward-editor-title");
  const costInput = byId("reward-editor-cost");

  const title = titleInput.value.trim();
  const cost_points = Number(costInput.value || 0);

  let invalid = false;
  if (!title) {
    setInvalid(titleInput, true);
    invalid = true;
  }
  if (Number.isNaN(cost_points) || cost_points < 1) {
    setInvalid(costInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Belohnung bearbeiten: Bitte Felder korrekt ausfuellen");
    return;
  }

  await api(`/rewards/${rewardId}`, {
    method: "PUT",
    body: {
      title,
      description: byId("reward-editor-description").value.trim() || null,
      cost_points,
      is_shareable: byId("reward-editor-shareable").value === "true",
      is_active: byId("reward-editor-active").value === "true",
    },
  });

  closeRewardEditor();
  await refreshFamilyData();
}

async function deleteReward(rewardId) {
  if (!isManagerRole()) return;
  await api(`/rewards/${rewardId}`, { method: "DELETE" });
  if (state.selectedRewardId === rewardId) {
    closeRewardEditor();
  }
  await refreshFamilyData();
}

async function redeemReward() {
  const rewardId = Number(byId("redeem-reward-select").value);
  if (!rewardId) {
    setInvalid(byId("redeem-reward-select"), true);
    return;
  }
  setInvalid(byId("redeem-reward-select"), false);

  const reward = state.rewards.find((entry) => entry.id === rewardId);
  if (!reward) {
    throw new Error("Belohnung nicht gefunden");
  }

  if (!reward.is_shareable) {
    const ownBalance = getOwnBalance();
    if (isChildRole() && ownBalance !== null && ownBalance < reward.cost_points) {
      window.alert(`Nicht genug Punkte. Du hast ${ownBalance}, benötigt: ${reward.cost_points}.`);
      return;
    }
    await api(`/rewards/${rewardId}/redeem`, {
      method: "POST",
      body: { comment: byId("redeem-comment").value.trim() || null },
    });
    byId("redeem-comment").value = "";
    await refreshFamilyData();
    return;
  }

  const points = Number(byId("redeem-points").value || 0);
  if (!Number.isFinite(points) || points < 1) {
    setInvalid(byId("redeem-points"), true);
    throw new Error("Bitte gültige Punkte für den Beitrag eingeben");
  }
  setInvalid(byId("redeem-points"), false);

  const progress = state.selectedRewardContribution;
  if (progress && progress.reward_id === rewardId) {
    if (progress.pending_redemption_id) {
      throw new Error("Für diese Belohnung läuft bereits eine Anfrage");
    }
    if (points > progress.remaining_points) {
      throw new Error(`Zu viele Punkte. Für diese Belohnung fehlen noch ${progress.remaining_points} Punkte.`);
    }
  }

  const ownBalance = getOwnBalance();
  if (isChildRole() && ownBalance !== null && ownBalance < points) {
    window.alert(`Nicht genug Punkte. Du hast ${ownBalance}, angefragt: ${points}.`);
    return;
  }

  await api(`/rewards/${rewardId}/contribute`, {
    method: "POST",
    body: { points, comment: byId("redeem-comment").value.trim() || null },
  });

  byId("redeem-comment").value = "";
  await refreshFamilyData();
}

async function reviewTaskRequest(taskId, decision) {
  if (!isManagerRole()) return;
  await api(`/tasks/${taskId}/review`, {
    method: "POST",
    body: { decision, comment: null },
  });
  await refreshFamilyData();
}

async function rejectAndDeleteSpecialTaskRequest(taskId) {
  if (!isManagerRole()) return;
  await api(`/tasks/${taskId}/review`, {
    method: "POST",
    body: { decision: "rejected", comment: null },
  });
  await api(`/tasks/${taskId}`, { method: "DELETE" });
  await refreshFamilyData();
}

async function reviewMissedTaskRequest(taskId, action) {
  if (!isManagerRole()) return;
  await api(`/tasks/${taskId}/missed-review`, {
    method: "POST",
    body: { action, comment: null },
  });
  await refreshFamilyData();
}

async function reviewRedemptionRequest(redemptionId, decision) {
  if (!isManagerRole()) return;
  await api(`/redemptions/${redemptionId}/review`, {
    method: "POST",
    body: {
      decision,
      comment: null,
    },
  });
  await refreshFamilyData();
}

async function showPointsHistory(userId) {
  state.selectedPointsUserId = userId;
  byId("points-history-title").textContent = `Punkte-Historie: ${getPointsUserDisplayName(userId)}`;
  await loadPointsHistory(userId);
}

function openPointsAdjust(userId, triggerButton = null) {
  state.selectedPointsUserId = userId;
  byId("points-adjust-user-label").textContent = `Nutzer: ${getPointsUserDisplayName(userId)}`;
  byId("points-adjust-delta").value = "";
  byId("points-adjust-description").value = "";
  mountInlineEditorSectionBelowTrigger("points-adjust-section", triggerButton);
  toggleHidden("points-adjust-section", false);
}

function closePointsAdjust() {
  toggleHidden("points-adjust-section", true);
  restoreInlineEditorSection("points-adjust-section");
}

async function savePointsAdjust() {
  if (!isManagerRole()) return;
  if (!state.selectedPointsUserId) {
    log("Bitte zuerst einen Nutzer waehlen");
    return;
  }

  clearInvalid(["points-adjust-delta", "points-adjust-description"]);
  const deltaInput = byId("points-adjust-delta");
  const descriptionInput = byId("points-adjust-description");
  const points_delta = Number(deltaInput.value);
  const description = descriptionInput.value.trim();

  let invalid = false;
  if (Number.isNaN(points_delta) || points_delta === 0) {
    setInvalid(deltaInput, true);
    invalid = true;
  }
  if (!description) {
    setInvalid(descriptionInput, true);
    invalid = true;
  }
  if (invalid) {
    log("Punkte bearbeiten: Bitte Felder korrekt ausfuellen");
    return;
  }

  await api(`/families/${getSelectedFamilyId()}/points/adjust`, {
    method: "POST",
    body: {
      user_id: state.selectedPointsUserId,
      points_delta,
      description,
    },
  });

  closePointsAdjust();
  await refreshFamilyData();
}

if (familySelect) {
  familySelect.addEventListener("change", async (event) => {
    stopLiveUpdates();
    stopSpecialTaskRefreshTicker();
    state.familyId = Number(event.target.value);
    await refreshFamilyData();
    startLiveUpdates();
    if (isChildRole()) startSpecialTaskRefreshTicker();
  });
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});

const statCardApproved = byId("stat-card-approved");
if (statCardApproved) {
  statCardApproved.addEventListener("click", openTaskHistoryFromDashboard);
}
const statCardOpen = byId("stat-card-open");
if (statCardOpen) {
  statCardOpen.addEventListener("click", () => {
    if (isChildRole()) {
      openChildDashboardTodayList();
      return;
    }
    openTasksTabWithSection("tasks-table-section");
  });
}
const statCardSubmitted = byId("stat-card-submitted");
if (statCardSubmitted) {
  statCardSubmitted.addEventListener("click", () => openTasksTabWithSection(isChildRole() ? "child-submitted-section" : "task-review-cards-section"));
}
const statCardMissed = byId("stat-card-missed");
if (statCardMissed) {
  statCardMissed.addEventListener("click", () => openTasksTabWithSection(isChildRole() ? "child-task-categories-section" : "task-review-cards-section"));
}
const statCardChildPoints = byId("stat-card-child-points");
if (statCardChildPoints) {
  statCardChildPoints.addEventListener("click", () => switchTab("points"));
}
const childTodayFocusCard = byId("dashboard-child-today-focus");
if (childTodayFocusCard) {
  childTodayFocusCard.addEventListener("click", openChildDashboardTodayList);
}
const childMissedFocusCard = byId("dashboard-child-missed-focus");
if (childMissedFocusCard) {
  childMissedFocusCard.addEventListener("click", () => openTasksTabWithSection("child-task-categories-section"));
}

byId("tasks-manager-cards").addEventListener("click", async (event) => {
  const actionButton = event.target.closest("button[data-task-action]");
  if (!actionButton) return;

  const taskId = Number(actionButton.dataset.taskId);
  if (!taskId) return;

  const action = actionButton.dataset.taskAction;
  if (action === "edit") {
    const sameTaskOpen = isSectionOpen("task-editor-section") && state.selectedTaskId === taskId;
    if (sameTaskOpen) {
      if (state.taskEditorDirty) {
        try {
          await updateTask();
        } catch (error) {
          log("Aufgabe speichern Fehler", { error: error.message });
        }
      } else {
        closeTaskEditor();
      }
    } else {
      openTaskEditor(taskId, actionButton);
    }
    return;
  }

  if (action === "delete") {
    const task = state.tasks.find((entry) => entry.id === taskId);
    const taskTitle = task ? task.title : "diese Aufgabe";
    const confirmed = window.confirm(`Aufgabe \"${taskTitle}\" wirklich löschen?`);
    if (!confirmed) return;
    try {
      await deleteTask(taskId);
    } catch (error) {
      log("Aufgabe löschen fehlgeschlagen", { error: error.message });
    }
  }

  if (action === "toggle-active") {
    const task = state.tasks.find((entry) => entry.id === taskId);
    if (!task) return;
    const nextActive = task.is_active === false;
    const actionLabel = nextActive ? "aktivieren" : "deaktivieren";
    const confirmed = window.confirm(`Aufgabe \"${task.title}\" wirklich ${actionLabel}?`);
    if (!confirmed) return;
    try {
      await setTaskActive(taskId, nextActive);
    } catch (error) {
      log("Aufgabe aktiv/deaktivieren fehlgeschlagen", { error: error.message });
    }
  }
});

byId("special-task-manager-cards").addEventListener("click", async (event) => {
  const actionButton = event.target.closest("button[data-special-task-action]");
  if (!actionButton) return;

  const templateId = Number(actionButton.dataset.specialTaskId);
  if (!templateId) return;

  const action = actionButton.dataset.specialTaskAction;
  if (action === "edit") {
    const sameTaskOpen = isSectionOpen("special-task-editor-section") && state.selectedSpecialTaskTemplateId === templateId;
    if (sameTaskOpen) {
      if (state.specialTaskEditorDirty) {
        try {
          await updateSpecialTaskTemplate();
        } catch (error) {
          log("Sonderaufgabe speichern Fehler", { error: error.message });
        }
      } else {
        closeSpecialTaskEditor();
      }
    } else {
      openSpecialTaskEditor(templateId, actionButton);
    }
    return;
  }

  if (action === "delete") {
    const template = state.specialTaskTemplates.find((entry) => entry.id === templateId);
    const title = template ? template.title : "diese Sonderaufgabe";
    const confirmed = window.confirm(`Sonderaufgabe \"${title}\" wirklich löschen?`);
    if (!confirmed) return;
    try {
      await deleteSpecialTaskTemplate(templateId);
    } catch (error) {
      log("Sonderaufgabe löschen fehlgeschlagen", { error: error.message });
    }
  }
});

byId("members-body").addEventListener("click", async (event) => {
  const actionButton = event.target.closest("button[data-member-action]");
  if (!actionButton) return;

  const memberId = Number(actionButton.dataset.memberId);
  if (!memberId) return;

  const action = actionButton.dataset.memberAction;
  if (action === "edit") {
    openMemberEditor(memberId, actionButton);
    return;
  }

  if (action === "delete") {
    const member = state.members.find((entry) => entry.user_id === memberId);
    const memberNameText = member ? member.display_name : "dieses Mitglied";
    const confirmed = window.confirm(`Mitglied \"${memberNameText}\" wirklich entfernen?`);
    if (!confirmed) return;
    try {
      await deleteMember(memberId);
    } catch (error) {
      log("Mitglied löschen fehlgeschlagen", { error: error.message });
    }
  }
});

byId("rewards-body").addEventListener("click", async (event) => {
  const actionButton = event.target.closest("button[data-reward-action]");
  if (!actionButton) return;

  const rewardId = Number(actionButton.dataset.rewardId);
  if (!rewardId) return;

  const action = actionButton.dataset.rewardAction;
  if (action === "edit") {
    openRewardEditor(rewardId, actionButton);
    return;
  }

  if (action === "delete") {
    const reward = state.rewards.find((entry) => entry.id === rewardId);
    const rewardTitle = reward ? reward.title : "diese Belohnung";
    const confirmed = window.confirm(`Belohnung \"${rewardTitle}\" wirklich löschen?`);
    if (!confirmed) return;
    try {
      await deleteReward(rewardId);
    } catch (error) {
      log("Belohnung löschen fehlgeschlagen", { error: error.message });
    }
  }
});

byId("dashboard-pending-section").addEventListener("click", async (event) => {
  const taskReviewButton = event.target.closest("button[data-dashboard-task-review-action]");
  if (taskReviewButton) {
    const taskId = Number(taskReviewButton.dataset.taskId);
    const decision = taskReviewButton.dataset.dashboardTaskReviewAction;
    if (!taskId || !decision) return;
    try {
      if (decision === "rejected_delete") {
        await rejectAndDeleteSpecialTaskRequest(taskId);
      } else {
        await reviewTaskRequest(taskId, decision);
      }
    } catch (error) {
      log("Aufgabe prüfen Fehler", { error: error.message });
    }
    return;
  }

  const missedTaskButton = event.target.closest("button[data-dashboard-missed-task-action]");
  if (missedTaskButton) {
    const taskId = Number(missedTaskButton.dataset.taskId);
    const action = missedTaskButton.dataset.dashboardMissedTaskAction;
    if (!taskId || !action) return;
    try {
      await reviewMissedTaskRequest(taskId, action);
    } catch (error) {
      log("Nicht-erledigt Prüfung Fehler", { error: error.message });
    }
    return;
  }

  const rewardReviewButton = event.target.closest("button[data-dashboard-reward-review-action]");
  if (rewardReviewButton) {
    const redemptionId = Number(rewardReviewButton.dataset.redemptionId);
    const decision = rewardReviewButton.dataset.dashboardRewardReviewAction;
    if (!redemptionId || !decision) return;
    try {
      await reviewRedemptionRequest(redemptionId, decision);
    } catch (error) {
      log("Belohnung prüfen Fehler", { error: error.message });
    }
  }
});

byId("manager-task-review-cards").addEventListener("click", async (event) => {
  const missedButton = event.target.closest("button[data-task-missed-review-action]");
  if (missedButton) {
    const taskId = Number(missedButton.dataset.taskId);
    const action = missedButton.dataset.taskMissedReviewAction;
    if (!taskId || !action) return;
    try {
      await reviewMissedTaskRequest(taskId, action);
    } catch (error) {
      log("Nicht-erledigt Prüfung Fehler", { error: error.message });
    }
    return;
  }

  const button = event.target.closest("button[data-task-review-action]");
  if (!button) return;

  const taskId = Number(button.dataset.taskId);
  const decision = button.dataset.taskReviewAction;
  if (!taskId || !decision) return;

  try {
    if (decision === "rejected_delete") {
      await rejectAndDeleteSpecialTaskRequest(taskId);
    } else {
      await reviewTaskRequest(taskId, decision);
    }
  } catch (error) {
    log("Aufgabe prüfen Fehler", { error: error.message });
  }
});

byId("manager-reward-review-cards").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-reward-review-action]");
  if (!button) return;

  const redemptionId = Number(button.dataset.redemptionId);
  const decision = button.dataset.rewardReviewAction;
  if (!redemptionId || !decision) return;

  try {
    await reviewRedemptionRequest(redemptionId, decision);
  } catch (error) {
    log("Belohnung prüfen Fehler", { error: error.message });
  }
});

byId("points-users-body").addEventListener("click", async (event) => {
  const actionButton = event.target.closest("button[data-points-action]");
  if (!actionButton) return;

  const userId = Number(actionButton.dataset.userId);
  if (!userId) return;

  const action = actionButton.dataset.pointsAction;
  if (action === "history") {
    try {
      await showPointsHistory(userId);
    } catch (error) {
      log("Punkte-Historie Fehler", { error: error.message });
    }
    return;
  }

  if (action === "edit") {
    openPointsAdjust(userId, actionButton);
  }
});

byId("ha-user-config-body").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-ha-user-action]");
  if (!button) return;
  const userId = Number(button.dataset.userId);
  if (!userId) return;

  const action = button.dataset.haUserAction;
  if (action === "edit") {
    openHomeAssistantUserModal(userId);
    return;
  }
  if (action === "test") {
    try {
      await sendHomeAssistantUserTest(userId);
    } catch (error) {
      log("HA Nutzertest Fehler", { error: error.message });
    }
  }
});

byId("child-task-categories-section").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-task-id]");
  if (!button) return;
  await handleChildTaskActionButton(button);
});

byId("child-special-task-section").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-special-task-claim-id]");
  if (!button) return;
  if (button.disabled) return;

  const templateId = Number(button.dataset.specialTaskClaimId);
  if (!templateId) return;

  try {
    await claimSpecialTaskTemplate(templateId);
  } catch (error) {
    window.alert(error.message);
    log("Sonderaufgabe annehmen fehlgeschlagen", { error: error.message });
  }
});

byId("toggle-member-create-btn").addEventListener("click", () =>
  toggleSection("member-create-section", "toggle-member-create-btn", "Neues Mitglied", "Eingabe schließen")
);
byId("toggle-task-create-btn").addEventListener("click", () =>
  toggleSection("task-create-section", "toggle-task-create-btn", "Neue Aufgabe", "Eingabe schließen")
);
byId("toggle-special-task-create-btn").addEventListener("click", () =>
  toggleSection("special-task-create-section", "toggle-special-task-create-btn", "Neue Sonderaufgabe", "Eingabe schließen")
);
byId("toggle-event-create-btn").addEventListener("click", () =>
  toggleSection("event-create-section", "toggle-event-create-btn", "Neuer Termin", "Eingabe schließen")
);
byId("toggle-reward-create-btn").addEventListener("click", () =>
  toggleSection("reward-create-section", "toggle-reward-create-btn", "Neue Belohnung", "Eingabe schließen")
);

byId("task-recurrence").addEventListener("change", syncTaskCreateTimingUI);
byId("task-due-mode").addEventListener("change", syncTaskCreateTimingUI);
byId("task-penalty-enabled").addEventListener("change", syncTaskCreateTimingUI);
byId("task-editor-recurrence").addEventListener("change", syncTaskEditorTimingUI);
byId("task-editor-due-mode").addEventListener("change", syncTaskEditorTimingUI);
byId("task-editor-penalty-enabled").addEventListener("change", syncTaskEditorTimingUI);
byId("special-task-interval").addEventListener("change", syncSpecialTaskCreateTimingUI);
byId("special-task-editor-interval").addEventListener("change", syncSpecialTaskEditorTimingUI);
byId("tasks-sort-select").addEventListener("change", (event) => {
  state.tasksSort = event.target.value || "updated_desc";
  renderTasks();
});
byId("special-tasks-sort-select").addEventListener("change", (event) => {
  state.specialTasksSort = event.target.value || "updated_desc";
  renderSpecialTaskTemplates();
});
byId("boot-password-visible").addEventListener("change", (event) =>
  setPasswordInputVisibility(["boot-password", "boot-password-confirm"], event.target.checked)
);
byId("member-password-visible").addEventListener("change", (event) =>
  setPasswordInputVisibility(["member-password", "member-password-confirm"], event.target.checked)
);
byId("task-editor-section").addEventListener("input", syncTaskEditorDirtyState);
byId("task-editor-section").addEventListener("change", syncTaskEditorDirtyState);
byId("special-task-editor-section").addEventListener("input", syncSpecialTaskEditorDirtyState);
byId("special-task-editor-section").addEventListener("change", syncSpecialTaskEditorDirtyState);

byId("login-btn").addEventListener("click", () => login().catch((error) => log("Login Fehler", { error: error.message })));
byId("bootstrap-btn").addEventListener("click", () => bootstrap().catch((error) => log("Initialisierung Fehler", { error: error.message })));
byId("logout-btn").addEventListener("click", logout);

byId("create-member-btn").addEventListener("click", () => createMember().catch((error) => log("Mitglied Fehler", { error: error.message })));
byId("member-editor-save-btn").addEventListener("click", () => updateMember().catch((error) => log("Mitglied bearbeiten Fehler", { error: error.message })));
byId("member-editor-cancel-btn").addEventListener("click", closeMemberEditor);
byId("create-task-btn").addEventListener("click", () => createTask().catch((error) => log("Aufgabe Fehler", { error: error.message })));
byId("create-special-task-btn").addEventListener("click", () => createSpecialTaskTemplate().catch((error) => log("Sonderaufgabe Fehler", { error: error.message })));
byId("special-task-editor-save-btn").addEventListener("click", () => updateSpecialTaskTemplate().catch((error) => log("Sonderaufgabe bearbeiten Fehler", { error: error.message })));
byId("special-task-editor-cancel-btn").addEventListener("click", closeSpecialTaskEditor);
byId("task-editor-save-btn").addEventListener("click", () => updateTask().catch((error) => log("Aufgabe bearbeiten Fehler", { error: error.message })));
byId("task-editor-cancel-btn").addEventListener("click", closeTaskEditor);
byId("create-event-btn").addEventListener("click", () => createEvent().catch((error) => log("Kalender Fehler", { error: error.message })));
byId("create-reward-btn").addEventListener("click", () => createReward().catch((error) => log("Belohnung Fehler", { error: error.message })));
byId("reward-editor-save-btn").addEventListener("click", () => updateReward().catch((error) => log("Belohnung bearbeiten Fehler", { error: error.message })));
byId("reward-editor-cancel-btn").addEventListener("click", closeRewardEditor);
byId("redeem-reward-select").addEventListener("change", () =>
  refreshSelectedRewardContribution().catch((error) => log("Belohnungsbeitrag Fehler", { error: error.message }))
);
byId("points-adjust-save-btn").addEventListener("click", () => savePointsAdjust().catch((error) => log("Punkte bearbeiten Fehler", { error: error.message })));
byId("points-adjust-cancel-btn").addEventListener("click", closePointsAdjust);
byId("ha-save-btn").addEventListener("click", () =>
  saveHomeAssistantSettings().catch((error) => log("HA Einstellungen speichern Fehler", { error: error.message }))
);
byId("ha-user-save-btn").addEventListener("click", () =>
  saveHomeAssistantUserConfig().catch((error) => log("HA Nutzer speichern Fehler", { error: error.message }))
);
byId("ha-user-modal-cancel-btn").addEventListener("click", closeHomeAssistantUserModal);
byId("apns-panel-close-btn").addEventListener("click", () => closeAllChannelPanels());
byId("sse-panel-close-btn").addEventListener("click", () => closeAllChannelPanels());
byId("ha-panel-close-btn").addEventListener("click", () =>
  saveHomeAssistantSettings()
    .then((saved) => {
      if (saved) closeAllChannelPanels();
    })
    .catch((error) => log("HA Einstellungen speichern Fehler", { error: error.message }))
);
byId("apns-test-send-btn").addEventListener("click", () =>
  sendChannelTest(
    "apns",
    byId("apns-test-recipient").value,
    byId("apns-test-title").value,
    byId("apns-test-message").value,
    "apns-test-result"
  ).catch((error) => log("APNs Test Fehler", { error: error.message }))
);
byId("sse-test-send-btn").addEventListener("click", () =>
  sendChannelTest(
    "sse",
    byId("sse-test-recipient").value,
    byId("sse-test-title").value,
    byId("sse-test-message").value,
    "sse-test-result"
  ).catch((error) => log("SSE Test Fehler", { error: error.message }))
);
["apns", "home_assistant", "sse"].forEach((channel) => {
  byId(`channel-edit-${channel}-btn`).addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await loadNotificationChannelStatus();
      if (channel === "home_assistant") {
        await Promise.all([loadHomeAssistantSettings(), loadHomeAssistantUserConfigs()]);
      }
      toggleChannelPanel(channel);
    } catch (error) {
      log("Kanal öffnen Fehler", { channel, error: error.message });
    }
  });
  byId(`channel-active-${channel}`).addEventListener("change", async (event) => {
    if (!event.target.checked) {
      event.target.checked = true;
      return;
    }
    try {
      await setActiveNotificationChannel(channel);
    } catch (error) {
      await loadNotificationChannelStatus().catch(() => null);
      log("Kanal aktivieren Fehler", { channel, error: error.message });
    }
  });
});
byId("ha-token").addEventListener("focus", () => {
  const input = byId("ha-token");
  if (input.dataset.masked === "true" && input.value === "********") {
    input.value = "";
    input.dataset.masked = "false";
  }
});
byId("ha-token").addEventListener("blur", () => {
  const input = byId("ha-token");
  if (!input.value.trim() && state.haSettings && state.haSettings.has_token) {
    input.value = "********";
    input.dataset.masked = "true";
  }
});
byId("redeem-reward-btn").addEventListener("click", () =>
  redeemReward().catch((error) => {
    window.alert(error.message);
    log("Einlösung Fehler", { error: error.message });
  })
);

initInlineEditorHomes();
setSelectedWeekdays("task-weekdays", [0, 1, 2, 3, 4, 5, 6]);
setSelectedWeekdays("task-editor-weekdays", [0, 1, 2, 3, 4, 5, 6]);
setSelectedWeekdays("special-task-weekdays", [0, 1, 2, 3, 4, 5, 6]);
setSelectedWeekdays("special-task-editor-weekdays", [0, 1, 2, 3, 4, 5, 6]);
byId("task-daily-time").value = byId("task-daily-time").value || "18:00";
byId("task-editor-daily-time").value = byId("task-editor-daily-time").value || "18:00";
byId("special-task-due-time").value = byId("special-task-due-time").value || "18:00";
byId("special-task-editor-due-time").value = byId("special-task-editor-due-time").value || "18:00";
byId("task-weekly-day").value = byId("task-weekly-day").value || "0";
byId("task-weekly-time").value = byId("task-weekly-time").value || "09:00";
byId("task-editor-weekly-day").value = byId("task-editor-weekly-day").value || "0";
byId("task-editor-weekly-time").value = byId("task-editor-weekly-time").value || "09:00";
byId("task-always-submittable").value = byId("task-always-submittable").value || "false";
byId("task-editor-always-submittable").value = byId("task-editor-always-submittable").value || "false";
byId("task-penalty-enabled").value = byId("task-penalty-enabled").value || "false";
byId("task-penalty-points").value = byId("task-penalty-points").value || "5";
byId("task-editor-penalty-enabled").value = byId("task-editor-penalty-enabled").value || "false";
byId("task-editor-penalty-points").value = byId("task-editor-penalty-points").value || "5";
byId("reward-shareable").value = byId("reward-shareable").value || "false";
byId("reward-editor-shareable").value = byId("reward-editor-shareable").value || "false";
byId("redeem-points").value = byId("redeem-points").value || "10";
syncTaskCreateTimingUI();
syncTaskEditorTimingUI();
syncSpecialTaskCreateTimingUI();
syncSpecialTaskEditorTimingUI();
refreshSession().catch((error) => log("Initialisierung fehlgeschlagen", { error: error.message }));
