const state = { data: null, pageIndex: 0, dirty: false };
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function message(text, error = false) {
  const node = $("#message");
  node.textContent = text;
  node.classList.toggle("error", error);
}

function field(label, control) {
  const wrap = document.createElement("label");
  wrap.className = "field";
  const title = document.createElement("span");
  title.textContent = label;
  wrap.append(title, control);
  return wrap;
}

function input(value, onChange, type = "text") {
  const node = document.createElement("input");
  node.type = type;
  node.value = value ?? "";
  node.addEventListener("input", () => { onChange(type === "number" ? Number(node.value) : node.value); state.dirty = true; });
  return node;
}

function select(values, value, onChange) {
  const node = document.createElement("select");
  values.forEach((item) => {
    const option = document.createElement("option");
    option.value = item; option.textContent = item; option.selected = item === value;
    node.append(option);
  });
  node.addEventListener("change", () => { onChange(node.value); state.dirty = true; });
  return node;
}

function render() {
  const data = state.data;
  $("#chapter").value = data?.chapter || "";
  const project = data?.project;
  $("#workspace").classList.toggle("empty", !project);
  document.querySelector("aside").hidden = !project;
  $("#page-editor").hidden = !project;
  $("#empty-state").hidden = !!project;
  if (!project) return;
  const pages = project.pages || [];
  state.pageIndex = Math.min(state.pageIndex, Math.max(0, pages.length - 1));
  const nav = $("#pages"); nav.replaceChildren();
  pages.forEach((page, index) => {
    const button = document.createElement("button");
    button.className = `page-button ${index === state.pageIndex ? "active" : ""}`;
    button.textContent = `${page.id} · ${(page.regions || []).length}`;
    button.onclick = () => { state.pageIndex = index; render(); };
    nav.append(button);
  });
  if (!pages.length) return;
  const page = pages[state.pageIndex];
  $("#page-title").textContent = page.id;
  $("#region-count").textContent = `${page.regions.length} реплик`;
  $("#preview").src = `/api/page/${encodeURIComponent(page.image)}?v=${Date.now()}`;
  const regions = $("#regions"); regions.replaceChildren();
  page.regions.forEach((region, index) => regions.append(renderRegion(region, index)));
}

function renderRegion(region, index) {
  region.prosody ||= { rate: "medium", pitch: "medium", pause_before_ms: 0, pause_after_ms: 0 };
  region.f5 ||= { reference_audio: "", reference_text: "", speed: 1.0, nfe_step: 32 };
  region.engine ||= "silero";
  const card = document.createElement("article"); card.className = "region";
  const head = document.createElement("div"); head.className = "region-head";
  const id = document.createElement("span"); id.className = "region-id"; id.textContent = `${index + 1}. ${region.id}`;
  const toggle = document.createElement("label"); toggle.className = "switch";
  const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = region.speak !== false;
  checkbox.onchange = () => { region.speak = checkbox.checked; state.dirty = true; };
  toggle.append(checkbox, document.createTextNode("Озвучивать")); head.append(id, toggle); card.append(head);

  const visible = document.createElement("textarea"); visible.value = region.text || "";
  visible.oninput = () => { region.text = visible.value; state.dirty = true; };
  card.append(field("Перевод", visible));
  const spoken = document.createElement("textarea"); spoken.value = region.tts_text || region.text || "";
  spoken.oninput = () => { region.tts_text = spoken.value; state.dirty = true; };
  card.append(field("Текст для произношения · + перед ударной гласной", spoken));

  const identity = document.createElement("div"); identity.className = "grid";
  identity.append(
    field("Движок", select(state.data.engines, region.engine, (v) => { region.engine = v; render(); })),
    field("Персонаж", input(region.speaker || "", (v) => region.speaker = v)),
  ); card.append(identity);

  if (region.engine === "f5") {
    const referenceRow = document.createElement("div"); referenceRow.className = "reference-row";
    const referenceInput = input(region.f5.reference_audio || "", (v) => region.f5.reference_audio = v);
    const choose = document.createElement("button"); choose.type = "button"; choose.className = "secondary"; choose.textContent = "Выбрать аудио…";
    choose.onclick = async () => {
      try {
        const result = await api("/api/browse-audio", { method: "POST", body: "{}" });
        if (result.audio) { region.f5.reference_audio = result.audio; state.dirty = true; render(); }
      } catch (error) { message(error.message, true); }
    };
    referenceRow.append(referenceInput, choose);
    card.append(field("Референс голоса", referenceRow));
    const referenceText = document.createElement("textarea"); referenceText.value = region.f5.reference_text || "";
    referenceText.placeholder = "Можно оставить пустым для авторасшифровки";
    referenceText.oninput = () => { region.f5.reference_text = referenceText.value; state.dirty = true; };
    card.append(field("Текст референса · необязательно", referenceText));
    const f5Settings = document.createElement("div"); f5Settings.className = "grid";
    const speed = input(region.f5.speed ?? 1.0, (v) => region.f5.speed = v, "number"); speed.min = "0.3"; speed.max = "2"; speed.step = "0.05";
    const nfe = input(region.f5.nfe_step ?? 32, (v) => region.f5.nfe_step = v, "number"); nfe.min = "4"; nfe.max = "64"; nfe.step = "2";
    f5Settings.append(field("Скорость F5 · 0.3–2.0", speed), field("Качество NFE · 4–64", nfe));
    card.append(f5Settings);
  } else {
    card.append(field("Голос Silero", select(state.data.voices, region.voice || "aidar", (v) => region.voice = v)));
    const sileroProsody = document.createElement("div"); sileroProsody.className = "grid";
    sileroProsody.append(
      field("Темп", select(state.data.rates, region.prosody.rate || "medium", (v) => region.prosody.rate = v)),
      field("Высота", select(state.data.pitches, region.prosody.pitch || "medium", (v) => region.prosody.pitch = v)),
    ); card.append(sileroProsody);
  }
  const pauses = document.createElement("div"); pauses.className = "grid";
  pauses.append(
    field("Пауза до, мс", input(region.prosody.pause_before_ms || 0, (v) => region.prosody.pause_before_ms = v, "number")),
    field("Пауза после, мс", input(region.prosody.pause_after_ms || 0, (v) => region.prosody.pause_after_ms = v, "number")),
  ); card.append(pauses);
  return card;
}

async function save() {
  if (!state.data?.project) return;
  await api("/api/save", { method: "POST", body: JSON.stringify({ project: state.data.project }) });
  state.dirty = false; message("Сценарий сохранён.");
}

async function build() {
  if (!state.data?.project) return;
  await api("/api/build", { method: "POST", body: JSON.stringify({ project: state.data.project }) });
  state.dirty = false; message("Сборка запущена."); pollBuild();
}

async function pollBuild() {
  try {
    const fresh = await api("/api/state");
    const wasBuilding = state.data?.building;
    state.data.building = fresh.building; state.data.build_ok = fresh.build_ok; state.data.build_log = fresh.build_log;
    renderBuild();
    if (fresh.building) setTimeout(pollBuild, 1000);
    else if (wasBuilding) message(fresh.build_ok ? "Видео собрано." : "Сборка завершилась с ошибкой.", !fresh.build_ok);
  } catch (error) { message(error.message, true); }
}

function renderBuild() {
  const data = state.data || {};
  const status = $("#build-status");
  status.className = `pill ${data.building ? "running" : data.build_ok === true ? "ok" : data.build_ok === false ? "error" : ""}`;
  status.textContent = data.building ? "Выполняется" : data.build_ok === true ? "Готово" : data.build_ok === false ? "Ошибка" : "Ожидание";
  $("#build-log").textContent = (data.build_log || []).join("\n") || "Здесь появится журнал сборки.";
  $("#build").disabled = !!data.building;
}

$("#open").onclick = async () => {
  try { state.data = await api("/api/open", { method: "POST", body: JSON.stringify({ chapter: $("#chapter").value }) }); state.pageIndex = 0; render(); renderBuild(); message("Глава открыта."); }
  catch (error) { message(error.message, true); }
};
$("#browse").onclick = async () => {
  try { state.data = await api("/api/browse", { method: "POST", body: "{}" }); state.pageIndex = 0; render(); renderBuild(); }
  catch (error) { message(error.message, true); }
};
$("#save").onclick = () => save().catch((error) => message(error.message, true));
$("#build").onclick = () => build().catch((error) => message(error.message, true));
window.addEventListener("beforeunload", (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });

api("/api/state").then((data) => { state.data = data; render(); renderBuild(); if (data.building) pollBuild(); }).catch((error) => message(error.message, true));
