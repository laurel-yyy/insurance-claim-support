// Test UI for the claims SOP agent. Plain ES module, no build step.
// Every value from the server is rendered with textContent, never innerHTML.

const PHASES = ["VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"];
const STAGE_LABELS = { identity: "Identity", authorization: "Authorization", consent: "Consent" };
const TERMINAL_LABELS = {
  ESCALATED: "Transferred to a live representative",
  ENDED: "Conversation ended",
};
const BANNERS = {
  VERIFIED: ["Identity verified", "ok"],
  CONSENT_REQUESTED: ["Waiting for the policyholder's approval", "wait"],
  CONSENT_APPROVED: ["The policyholder approved this call", "ok"],
  CONSENT_DECLINED: ["The policyholder declined", "stop"],
  CONSENT_TIMEOUT: ["The policyholder didn't respond", "stop"],
  VERIFICATION_LOCKED: ["Verification locked", "stop"],
  ESCALATED: ["Transferred to a live representative", "wait"],
  EMAIL_SENT: ["Summary email sent", "ok"],
};
const STEP_DELAY_MS = 700;

const $ = (id) => document.getElementById(id);
const ui = {
  title: $("title"), date: $("demo-date"), scenario: $("scenario"), play: $("play"), step: $("step"),
  consent: $("consent"), newButton: $("new"), keyStatus: $("key-status"), log: $("log"), typing: $("typing"),
  quick: $("quick-replies"), form: $("composer"), input: $("message"), send: $("send"), route: $("route"),
  substage: $("substage"), lockLabel: $("lock-label"), chips: $("chips"), terminal: $("terminal"),
  layout: document.querySelector(".layout"), dialog: $("key-dialog"), keyForm: $("key-form"), keyInput: $("key-input"),
};

const state = {
  health: null, sessionId: null, apiKey: null, scenarios: [], scenario: null, stepIndex: 0,
  busy: false, playing: false, agentName: "Agent",
};

class ApiError extends Error {}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* keep the generic message */ }
    throw new ApiError(detail);
  }
  return response.json();
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function formatDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
  });
}

// --- conversation -------------------------------------------------------------------------------

function addMessage(role, text) {
  const node = el("div", null, `msg ${role}`);
  node.append(el("span", role === "agent" ? state.agentName : role === "caller" ? "Caller" : "Problem", "who"));
  node.append(document.createTextNode(text));
  ui.log.append(node);
  ui.log.scrollTop = ui.log.scrollHeight;
}

function addBanners(events) {
  const seen = new Set();
  for (const event of events) {
    const banner = BANNERS[event.type];
    if (!banner || seen.has(event.type)) continue;
    seen.add(event.type);
    ui.log.append(el("p", banner[0], `banner ${banner[1]}`));
  }
}

function setBusy(busy) {
  state.busy = busy;
  ui.typing.hidden = !busy;
  const ready = Boolean(state.sessionId) && !busy;
  ui.input.disabled = !ready;
  ui.send.disabled = !ready;
  ui.step.disabled = !ready || !state.scenario || state.stepIndex >= state.scenario.turns.length;
  ui.play.disabled = !ready || !state.scenario || state.playing;
  for (const button of ui.quick.querySelectorAll("button")) button.disabled = !ready;
}

function renderQuickReplies(replies) {
  ui.quick.replaceChildren();
  for (const text of replies) {
    const button = el("button", text);
    button.type = "button";
    button.addEventListener("click", () => send(text));
    ui.quick.append(button);
  }
}

async function send(text) {
  const message = text.trim();
  if (!message || !state.sessionId || state.busy) return;
  addMessage("caller", message);
  ui.input.value = "";
  setBusy(true);
  try {
    const turn = await api(`/api/sessions/${state.sessionId}/messages`, {
      method: "POST", body: JSON.stringify({ text: message }),
    });
    addBanners(turn.debug.events_this_turn);
    addMessage("agent", turn.reply);
    render(turn);
  } catch (error) {
    addMessage("error", error.message);
  } finally {
    setBusy(false);
    if (!state.playing) ui.input.focus();
  }
}

// --- SOP route ----------------------------------------------------------------------------------

function renderRoute(debug) {
  const phase = debug.phase;
  const reached = PHASES.indexOf(phase);
  const verified = debug.verification.verified;
  for (const item of ui.route.children) {
    const index = PHASES.indexOf(item.dataset.phase);
    item.classList.toggle("current", item.dataset.phase === phase);
    item.classList.toggle("done", reached > index || (reached === -1 && verified && index === 0));
    if (item.dataset.phase === phase) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  }
  const verifyItem = ui.route.querySelector('[data-phase="VERIFY_ID"]');
  verifyItem.classList.toggle("unlocked", verified);
  ui.lockLabel.textContent = verified ? "Unlocked" : "Locked";
  ui.substage.textContent = debug.verify_stage ? `Stage: ${STAGE_LABELS[debug.verify_stage] ?? debug.verify_stage}` : "";
  ui.chips.replaceChildren();
  for (const hint of debug.memory.hints ?? []) {
    const chip = el("li", hint, verified ? "used" : "held");
    chip.title = verified ? "Used after verification" : "Held for later";
    ui.chips.append(chip);
  }
  ui.terminal.hidden = !(phase in TERMINAL_LABELS);
  ui.terminal.textContent = TERMINAL_LABELS[phase] ?? "";
}

// --- inspector ----------------------------------------------------------------------------------

function facts(entries) {
  const list = el("dl", null, "facts");
  for (const [label, value] of entries) {
    list.append(el("dt", label));
    list.append(el("dd", value === null || value === undefined || value === "" ? "none" : value));
  }
  return list;
}

function json(value) {
  return el("pre", JSON.stringify(value, null, 2), "json");
}

function renderMemory(debug) {
  const pane = $("pane-memory");
  const memory = debug.memory;
  const checklist = Object.entries(debug.identity_checklist).map(([field, status]) => [field.replace("_", " "), status]);
  pane.replaceChildren(
    el("h3", "Identity checklist"), facts(checklist),
    el("h3", "Verification"),
    facts([
      ["Status", debug.verification.status],
      ["Failed attempts", debug.verification.failed_attempts],
      ["Caller role", debug.representative.caller_role],
      ["Authorization", debug.representative.authorization],
      ["Consent", debug.representative.consent_status],
    ]),
    el("h3", "Memory"),
    facts([
      ["Hints", (memory.hints ?? []).join("; ")],
      ["Deferred questions", (memory.deferred_questions ?? []).map((q) => `${q.text}${q.answered ? " (answered)" : ""}`).join("; ")],
      ["Selected claim", memory.selected_case_id],
      ["Path", memory.selected_path],
      ["Unavailable documents", (memory.unavailable_documents ?? []).join(", ")],
    ]),
    el("h3", "Counters"), json(debug.counters),
  );
}

function renderTurn(debug) {
  const pane = $("pane-turn");
  const events = el("ul", null, "events");
  for (const event of debug.events_this_turn) events.append(el("li", event.type));
  const last = debug.last_turn ?? {};
  pane.replaceChildren(
    el("h3", "Events this turn"), debug.events_this_turn.length ? events : el("p", "No events yet.", "empty"),
    el("h3", "Pending question"), json(debug.pending_question),
    el("h3", "Guard"), json(last.guard ?? []),
    el("h3", "Tool calls"), json(last.tool_calls ?? []),
    el("h3", "Directive"), json(last.directive ?? null),
    el("h3", "NLU"), json(last.nlu ?? null),
    el("h3", "Latency and tokens"), json({ latency_ms: last.latency_ms, llm_calls: last.llm_calls ?? [] }),
    el("h3", "Full timeline"), json(debug.timeline.map((e) => `${e.turn} ${e.phase} ${e.type}`)),
  );
}

async function renderOutbox(debug) {
  const pane = $("pane-outbox");
  const parts = [];
  if (debug.email_draft) {
    parts.push(el("h3", `Email draft (${debug.email_draft.generated_by})`));
    parts.push(facts([["To", debug.email_draft.to], ["Subject", debug.email_draft.subject]]));
    parts.push(el("pre", debug.email_draft.text, "email"));
  }
  try {
    const emails = await api(`/api/sessions/${state.sessionId}/outbox`);
    for (const email of emails) {
      parts.push(el("h3", "Sent email"));
      parts.push(facts([["To", email.to], ["Subject", email.subject], ["Sent", email.sent_at]]));
      parts.push(el("pre", email.text, "email"));
    }
  } catch { /* the draft and ticket are still useful */ }
  if (debug.handoff) {
    parts.push(el("h3", "Handoff ticket"));
    parts.push(json(debug.handoff));
  }
  pane.replaceChildren(...(parts.length ? parts : [el("p", "Emails and handoff tickets appear here once they exist.", "empty")]));
}

function render(turn) {
  renderRoute(turn.debug);
  renderQuickReplies(turn.quick_replies);
  renderMemory(turn.debug);
  renderTurn(turn.debug);
  renderOutbox(turn.debug);
}

function setupTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    for (const other of tabs) {
      const selected = other === tab;
      other.setAttribute("aria-selected", String(selected));
      other.tabIndex = selected ? 0 : -1;
      $(other.getAttribute("aria-controls")).hidden = !selected;
    }
    tab.focus();
  };
  for (const tab of tabs) {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      const i = tabs.indexOf(tab);
      if (event.key === "ArrowRight") select(tabs[(i + 1) % tabs.length]);
      if (event.key === "ArrowLeft") select(tabs[(i - 1 + tabs.length) % tabs.length]);
    });
  }
  for (const button of document.querySelectorAll("#views button")) {
    button.addEventListener("click", () => {
      ui.layout.dataset.view = button.dataset.view;
      for (const other of document.querySelectorAll("#views button")) {
        other.setAttribute("aria-pressed", String(other === button));
      }
    });
  }
}

// --- sessions, scenarios, API key ---------------------------------------------------------------

async function newConversation() {
  setBusy(true);
  ui.log.replaceChildren();
  ui.quick.replaceChildren();
  state.stepIndex = 0;
  try {
    const body = { consent_scenario: ui.consent.value || null };
    if (state.apiKey) body.api_key = state.apiKey;
    const turn = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    state.sessionId = turn.session_id;
    addMessage("agent", turn.reply);
    render(turn);
  } catch (error) {
    state.sessionId = null;
    addMessage("error", error.message);
  } finally {
    setBusy(false);
    if (state.sessionId) ui.input.focus();
  }
}

function chooseScenario() {
  state.scenario = state.scenarios.find((s) => s.name === ui.scenario.value) ?? null;
  state.stepIndex = 0;
  if (state.scenario) ui.consent.value = state.scenario.consent_scenario;
  setBusy(state.busy);
}

async function stepScenario() {
  if (!state.scenario || state.stepIndex >= state.scenario.turns.length) return;
  const text = state.scenario.turns[state.stepIndex];
  state.stepIndex += 1;
  await send(text);
}

async function playScenario() {
  if (!state.scenario) return;
  state.playing = true;
  await newConversation();
  while (state.sessionId && state.stepIndex < state.scenario.turns.length) {
    await stepScenario();
    await new Promise((resolve) => setTimeout(resolve, STEP_DELAY_MS));
  }
  state.playing = false;
  setBusy(false);
}

function describeKey(health) {
  if (health.llm_configured) return "Using the server's API key";
  if (state.apiKey) return "Using your API key for this page";
  return health.allow_client_api_key ? "No API key yet" : "No API key configured";
}

async function askForKey() {
  ui.keyInput.value = "";
  ui.dialog.showModal();
  await new Promise((resolve) => ui.dialog.addEventListener("close", resolve, { once: true }));
  const key = ui.keyInput.value.trim();
  ui.keyInput.value = "";
  if (key) state.apiKey = key;
  ui.keyStatus.textContent = describeKey(state.health);
}

async function init() {
  setupTabs();
  ui.form.addEventListener("submit", (event) => { event.preventDefault(); send(ui.input.value); });
  ui.newButton.addEventListener("click", newConversation);
  ui.scenario.addEventListener("change", chooseScenario);
  ui.step.addEventListener("click", stepScenario);
  ui.play.addEventListener("click", playScenario);

  const health = await api("/api/health");
  state.health = health;
  state.agentName = health.agent_name;
  ui.typing.textContent = `${health.agent_name} is typing`;
  ui.title.textContent = `${health.company_name} claims support demo`;
  ui.date.textContent = `Demo date ${formatDate(health.demo_today)}`;
  for (const name of health.data_summary.consent_scenarios) {
    const option = el("option", name);
    option.value = name;
    ui.consent.append(option);
  }
  state.scenarios = await api("/api/scenarios").catch(() => []);
  for (const scenario of state.scenarios) {
    const option = el("option", scenario.name.replaceAll("_", " "));
    option.value = scenario.name;
    option.title = scenario.description;
    ui.scenario.append(option);
  }
  ui.keyStatus.textContent = describeKey(health);
  if (!health.llm_configured && !health.allow_client_api_key) {
    addMessage("error", "No API key is configured. Set ANTHROPIC_API_KEY on the server and reload this page.");
    ui.newButton.disabled = true;
    return;
  }
  if (!health.llm_configured) await askForKey();
  await newConversation();
}

init().catch((error) => addMessage("error", `The demo couldn't load: ${error.message}`));
