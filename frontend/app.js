/* 智慧治理 Demo 大屏前端逻辑 */

const $ = (sel) => document.querySelector(sel);

// ---------- 时钟 ----------
setInterval(() => {
  $("#clock").textContent = new Date().toLocaleString("zh-CN", { hour12: false });
}, 1000);

// ---------- 地图 ----------
const map = L.map("map", { zoomControl: false }).setView([39.977, 116.318], 14);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap",
  maxZoom: 18,
}).addTo(map);
L.control.zoom({ position: "bottomright" }).addTo(map);

const entityLayer = L.layerGroup().addTo(map);
const signalLayer = L.layerGroup().addTo(map);
const eventLayer = L.layerGroup().addTo(map);

const ENTITY_ICONS = {
  "下穿桥": "🌉", "下穿通道": "🛣️", "泵站": "⚙️", "井盖": "⭕", "水位传感器": "📡",
  "监控摄像头": "📷", "学校": "🏫", "医院": "🏥", "避难场所": "⛺", "道路": "🛤️",
  "排水管网": "🔧", "地铁站": "🚇",
};

function entityMarker(e) {
  const icon = L.divIcon({
    className: "",
    html: `<div style="font-size:15px;text-shadow:0 0 4px #000">${ENTITY_ICONS[e.type] || "📍"}</div>`,
    iconSize: [18, 18],
  });
  return L.marker([e.lat, e.lng], { icon }).bindPopup(
    `<b>${e.name}</b><br>一物一码：${e.code}<br>类型：${e.type}<br>责任部门：${e.dept}<br>状态：${e.status}<br>最近巡检：${e.last_check}`
  );
}

function pulseMarker(lat, lng, color, size) {
  const icon = L.divIcon({
    className: "",
    html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};box-shadow:0 0 12px ${color}"></div>`,
    iconSize: [size, size],
  });
  return L.marker([lat, lng], { icon });
}

// ---------- 初始化 ----------
async function init() {
  const res = await fetch("/api/bootstrap");
  const data = await res.json();
  const badge = $("#deepseek-badge");
  if (data.deepseek_enabled) {
    badge.textContent = "DeepSeek 已连接";
    badge.className = "badge badge-green";
  } else {
    badge.textContent = "演示兜底模式（未配置 DeepSeek Key）";
    badge.className = "badge badge-orange";
  }
  data.entities.forEach((e) => entityLayer.addLayer(entityMarker(e)));
}
init();

// ---------- 演练回放 ----------
let pollTimer = null;
let renderedSignals = new Set();
let renderedEvents = new Set();
let agentsStarted = false;
let currentEventId = null;
let proposedTasks = [];

$("#btn-start").addEventListener("click", async () => {
  await fetch("/api/replay/start", { method: "POST" });
  resetUI();
  $("#btn-start").disabled = true;
  $("#btn-start").textContent = "推演进行中…";
  pollTimer = setInterval(poll, 700);
});

$("#btn-reset").addEventListener("click", async () => {
  await fetch("/api/replay/reset", { method: "POST" });
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  resetUI();
  $("#btn-start").disabled = false;
  $("#btn-start").textContent = "开始暴雨推演";
});

function resetUI() {
  renderedSignals = new Set();
  renderedEvents = new Set();
  agentsStarted = false;
  currentEventId = null;
  proposedTasks = [];
  $("#signal-list").innerHTML = `<li class="placeholder">信号按时间轴回放中…</li>`;
  $("#event-list").innerHTML = `<div class="placeholder">信号达到时空聚类阈值后将融合为标准化事件</div>`;
  $("#agent-output").innerHTML = `<div class="placeholder">事件生成后自动触发 4 个智能体依次研判</div>`;
  $("#order-list").innerHTML = `<div class="placeholder">人工审核通过后自动生成跨部门工单</div>`;
  $("#review-body").innerHTML = `<div class="placeholder">事件办结后可生成全链路耗时复盘</div>`;
  $("#btn-approve").disabled = true;
  $("#btn-advance").disabled = true;
  signalLayer.clearLayers();
  eventLayer.clearLayers();
}

async function poll() {
  const res = await fetch("/api/state");
  const data = await res.json();

  // 信号流
  for (const s of data.signals) {
    if (renderedSignals.has(s.id)) continue;
    renderedSignals.add(s.id);
    const list = $("#signal-list");
    const ph = list.querySelector(".placeholder");
    if (ph) ph.remove();
    const li = document.createElement("li");
    li.className = "signal-item";
    li.innerHTML = `
      <div class="s-title">${s.title}</div>
      <div class="s-meta"><span class="channel-tag">${s.channel}</span>${s.source} · 置信度 ${s.confidence}</div>
      <div class="s-detail">${s.detail}</div>`;
    list.prepend(li);
    signalLayer.addLayer(
      pulseMarker(s.lat, s.lng, "#2f7bff", 12).bindPopup(`<b>${s.title}</b><br>${s.source}<br>${s.detail}`)
    );
  }

  // 事件中心
  renderEvents(data.events, data.watching);

  if (data.finished && pollTimer && agentsStarted) {
    clearInterval(pollTimer);
    pollTimer = null;
    $("#btn-start").disabled = false;
    $("#btn-start").textContent = "重新推演";
  }
}

function renderEvents(events, watching) {
  if (!events.length && !watching.length) return;
  const box = $("#event-list");
  box.innerHTML = "";

  for (const w of watching) {
    const div = document.createElement("div");
    div.className = "watching-card";
    div.innerHTML = `<b>观察中 · ${w.location}</b><br>${w.note}<br>关联信号：${w.signal_ids.join("、")}`;
    box.appendChild(div);
  }

  for (const ev of events) {
    const div = document.createElement("div");
    div.className = "event-card";
    div.innerHTML = `
      <div class="e-title">${ev.title}</div>
      <div class="e-level">等级：${ev.level} · 综合置信度 ${ev.confidence} · ${ev.event_id}</div>
      <div class="e-basis">融合信号：${ev.signal_ids.join("、")}（${ev.channels.join(" / ")}）<br>
        ${ev.fusion_basis}<br>背景：${(ev.context || []).join("；") || "无"}</div>
      <span class="e-status">${ev.status}</span>`;
    box.appendChild(div);

    if (!renderedEvents.has(ev.event_id)) {
      renderedEvents.add(ev.event_id);
      eventLayer.addLayer(pulseMarker(ev.lat, ev.lng, "#e5484d", 16).bindPopup(`<b>${ev.title}</b><br>${ev.level}`));
      eventLayer.addLayer(
        L.circle([ev.lat, ev.lng], { radius: 450, color: "#e5484d", weight: 1, fillColor: "#e5484d", fillOpacity: 0.12 })
      );
      map.flyTo([ev.lat, ev.lng], 15, { duration: 1.2 });
      if (!agentsStarted) {
        agentsStarted = true;
        currentEventId = ev.event_id;
        setTimeout(() => runAgents(ev.event_id), 1000);
      }
    }
  }
}

// ---------- 多智能体研判（SSE） ----------
function runAgents(eventId) {
  const box = $("#agent-output");
  box.innerHTML = "";
  const blocks = {};

  const es = new EventSource(`/api/agents/run?event_id=${encodeURIComponent(eventId)}`);
  es.onmessage = (msg) => {
    const item = JSON.parse(msg.data);
    if (item.type === "agent_start") {
      const div = document.createElement("div");
      div.className = "agent-block";
      div.innerHTML = `
        <div class="agent-name"><span class="spinner"></span>${item.agent}<span style="color:var(--text-dim);font-weight:400;font-size:11px">｜${item.task}</span></div>
        <div class="agent-text"></div>`;
      box.appendChild(div);
      blocks[item.key] = div;
      box.scrollTop = box.scrollHeight;
    } else if (item.type === "delta" && blocks[item.key]) {
      blocks[item.key].querySelector(".agent-text").textContent += item.text;
      box.scrollTop = box.scrollHeight;
    } else if (item.type === "agent_end" && blocks[item.key]) {
      blocks[item.key].querySelector(".spinner").outerHTML = `<span class="done-mark">✓</span>`;
    } else if (item.type === "tasks") {
      proposedTasks = item.tasks;
      es.close();
      const note = document.createElement("div");
      note.className = "review-note";
      note.innerHTML = `研判完成，生成 ${item.tasks.length} 项任务分工（含 ${item.tasks.filter((t) => t.high_risk).length} 项高风险动作）。<br><b>依据"AI 只做辅助决策"原则，请指挥中心人工审核后执行。</b>`;
      box.appendChild(note);
      box.scrollTop = box.scrollHeight;
      $("#btn-approve").disabled = false;
    }
  };
  es.onerror = () => es.close();
}

// ---------- 人工审核 → 工单 ----------
$("#btn-approve").addEventListener("click", async () => {
  if (!currentEventId) return;
  const res = await fetch("/api/workorders/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_id: currentEventId }),
  });
  const data = await res.json();
  if (!data.ok) return;
  $("#btn-approve").disabled = true;
  $("#btn-approve").textContent = "已审核 · 工单已派发";
  renderOrders(data.orders);
  $("#btn-advance").disabled = false;
  refreshState();
});

const STATE_INDEX = { "已生成": 0, "已签收": 1, "处置中": 2, "复核中": 3, "已办结": 4 };

function renderOrders(orderList) {
  const box = $("#order-list");
  box.innerHTML = "";
  for (const o of orderList) {
    const div = document.createElement("div");
    div.className = "order-item";
    div.innerHTML = `
      <div>
        <div class="o-title">${o.order_id} · ${o.task}${o.high_risk ? ' <span style="color:var(--danger);font-size:10px">[高风险·人工已审]</span>' : ""}</div>
        <div class="o-dept">${o.dept} · 时限 ${o.deadline_min} 分钟</div>
      </div>
      <span class="state-chip st-${STATE_INDEX[o.state] ?? 0}">${o.state}</span>`;
    box.appendChild(div);
  }
}

$("#btn-advance").addEventListener("click", async () => {
  const res = await fetch("/api/workorders/advance", { method: "POST" });
  const data = await res.json();
  renderOrders(data.orders);
  if (data.all_done) {
    $("#btn-advance").disabled = true;
    refreshState();
    loadReview();
  }
});

async function refreshState() {
  const res = await fetch("/api/state");
  const data = await res.json();
  renderEvents(data.events, data.watching);
}

// ---------- 复盘 ----------
$("#btn-review").addEventListener("click", loadReview);

async function loadReview() {
  const res = await fetch("/api/review");
  const data = await res.json();
  const box = $("#review-body");
  box.innerHTML = "";
  for (const m of data.metrics) {
    const div = document.createElement("div");
    div.className = "metric-row";
    div.innerHTML = `<span>${m.name}</span><span class="m-val">${m.seconds == null ? "—" : m.seconds + " 秒"}</span>`;
    box.appendChild(div);
  }
  const note = document.createElement("div");
  note.className = "review-note";
  note.textContent = data.note;
  box.appendChild(note);
}

// ---------- 政务问答 ----------
async function askQuestion() {
  const q = $("#qa-input").value.trim();
  if (!q) return;
  $("#qa-input").value = "";
  const hist = $("#qa-history");
  const hint = hist.querySelector(".qa-hint");
  if (hint) hint.remove();

  const qDiv = document.createElement("div");
  qDiv.className = "qa-msg qa-q";
  qDiv.innerHTML = `<span></span>`;
  qDiv.querySelector("span").textContent = q;
  hist.appendChild(qDiv);

  const aDiv = document.createElement("div");
  aDiv.className = "qa-msg qa-a";
  aDiv.innerHTML = `<span>思考中…</span>`;
  hist.appendChild(aDiv);
  hist.scrollTop = hist.scrollHeight;

  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: q }),
  });
  const data = await res.json();
  aDiv.querySelector("span").textContent = data.answer;

  if (data.sources && data.sources.length) {
    const srcDiv = document.createElement("div");
    srcDiv.className = "qa-sources";
    srcDiv.innerHTML =
      "依据来源：" +
      data.sources
        .map((s) => `<div class="src-item"><b>[${s.chunk_id}]</b> 《${s.doc_title}》（${s.source}）</div>`)
        .join("");
    aDiv.appendChild(srcDiv);
  }
  const modeDiv = document.createElement("div");
  modeDiv.className = "qa-mode";
  modeDiv.textContent = `生成方式：${data.mode}`;
  aDiv.appendChild(modeDiv);
  hist.scrollTop = hist.scrollHeight;
}

$("#qa-send").addEventListener("click", askQuestion);
$("#qa-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") askQuestion();
});
