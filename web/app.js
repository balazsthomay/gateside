const state = {
  channel: "chat",
  history: [],
  scenarios: [],
  sending: false,
};

const el = (sel) => document.querySelector(sel);
const transcript = el("#transcript");
const traceList = el("#trace-list");
const messageInput = el("#message");
const sendBtn = el("#send");
const scenarioSelect = el("#scenario");

function setChannel(channel) {
  state.channel = channel;
  state.history = [];
  transcript.innerHTML = "";
  traceList.innerHTML = "";
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.channel === channel),
  );
  messageInput.placeholder =
    channel === "chat"
      ? "Type a chat message as a TicketSwap buyer at the gate…"
      : "Paste an email as a TicketSwap buyer (e.g. the morning after)…";
  populateScenarios();
}

function populateScenarios() {
  const filtered = state.scenarios.filter((s) => s.channel === state.channel);
  scenarioSelect.innerHTML =
    '<option value="">— choose one or type your own below —</option>';
  for (const s of filtered) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.title;
    scenarioSelect.appendChild(opt);
  }
}

function addBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.textContent = text;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
  return div;
}

function addTraceStep(step) {
  const li = document.createElement("li");
  li.className = "trace-item";
  if (step.kind === "tool_call" && step.name === "escalate_to_human") {
    li.classList.add("escalation");
  }
  if (step.kind === "final") {
    li.classList.add("final");
  }
  const head = document.createElement("div");
  head.className = "kind";
  head.textContent = step.kind;
  li.appendChild(head);

  if (step.name) {
    const nm = document.createElement("div");
    nm.className = "name";
    nm.textContent = step.name;
    li.appendChild(nm);
  }
  const body =
    step.kind === "tool_call"
      ? JSON.stringify(step.arguments, null, 2)
      : step.kind === "tool_result"
        ? JSON.stringify(step.result, null, 2)
        : step.content || "";
  if (body) {
    const pre = document.createElement("pre");
    pre.textContent = body;
    li.appendChild(pre);
  }
  traceList.appendChild(li);
  traceList.scrollTop = traceList.scrollHeight;
}

function addSessionDivider(label) {
  const div = document.createElement("li");
  div.className = "session-divider";
  div.textContent = label;
  traceList.appendChild(div);
}

async function send(message) {
  if (!message.trim() || state.sending) return;
  state.sending = true;
  sendBtn.disabled = true;
  addBubble("user", message);
  const thinking = addBubble("agent thinking", "…thinking");
  addSessionDivider(`turn @ ${new Date().toLocaleTimeString()}`);

  try {
    const resp = await fetch("/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        channel: state.channel,
        history: state.history,
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    thinking.classList.remove("thinking");
    thinking.textContent = data.reply || "(no reply)";

    for (const step of data.trace) {
      addTraceStep(step);
    }
    if (data.escalation) {
      const li = document.createElement("li");
      li.className = "trace-item escalation";
      li.innerHTML = `<div class="kind">ESCALATION</div><div class="name">${data.escalation.reason_code}</div><pre>${data.escalation.handoff_summary || ""}\ncase: ${data.escalation.case_id}</pre>`;
      traceList.appendChild(li);
    }

    state.history.push({ role: "user", content: message });
    state.history.push({ role: "assistant", content: data.reply });
  } catch (err) {
    thinking.classList.remove("thinking");
    thinking.textContent = `Error: ${err.message}`;
  } finally {
    state.sending = false;
    sendBtn.disabled = false;
    messageInput.value = "";
    messageInput.focus();
  }
}

document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => setChannel(t.dataset.channel));
});

el("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send(messageInput.value);
});

messageInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    send(messageInput.value);
  }
});

scenarioSelect.addEventListener("change", (e) => {
  const id = e.target.value;
  if (!id) return;
  const s = state.scenarios.find((x) => x.id === id);
  if (s) {
    messageInput.value = s.user_message;
    messageInput.focus();
  }
});

el("#reset").addEventListener("click", () => setChannel(state.channel));

async function bootstrap() {
  try {
    const resp = await fetch("/api/scenarios");
    state.scenarios = await resp.json();
  } catch (err) {
    console.warn("scenarios fetch failed", err);
  }
  setChannel("chat");
}

bootstrap();
