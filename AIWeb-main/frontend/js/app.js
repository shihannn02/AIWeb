const API = "";

let token = localStorage.getItem("risk_token") || "";
let fileId = "";
let fileMeta = {};
let trainFileId = "";
let testFileId = "";
let defaultDropCols = [];
let dropColsSelection = new Set();
let lastValidationData = null;
let lastValidationPayloadKey = "";
let autoLabelFixDone = false;
let currentJobId = "";
let reviewData = null;
let selectedFeatures = new Set();
let activeFeature = "";
/** @type {Map<string, {operator: string, threshold: number, values?: string[], valueType?: string, userEdited?: boolean}>} */
const featureRules = new Map();
let featureSortMode = "bad_rate";
/** @type {Map<string, { ruleKey: string, hit_count: number, bad_rate: number, money_bad_rate?: number }>} */
const liveFeatureStats = new Map();
let rejectPreviewTimer = null;
let rejectPreviewAbort = null;
let serialAnalysisData = null;
let currentStep = 1;
let maxReachedStep = 1;
/** @type {Set<string>} 手动追加、未达阈值但仍进入候选的特征 */
let manualIncludeFeatures = new Set();
/** 规则/勾选变更后需重新跑串联分析 */
let serialAnalysisStale = false;
/** @type {Map<string, object>} */
const lastFeatureDetail = new Map();

function normThreshold(val) {
  const n = parseFloat(val);
  return Number.isFinite(n) ? String(n) : "0";
}

function currentRuleKey(feature) {
  const r = getFeatureRule(feature);
  if (r.operator === "in") {
    const vals = [...(r.values || [])].sort().join("|");
    return `${feature}|in|${vals}`;
  }
  return `${feature}|${r.operator}|${normThreshold(r.threshold)}`;
}

function ruleKeyFromParts(feature, operator, threshold, values) {
  if (operator === "in") {
    const vals = [...(values || [])].sort().join("|");
    return `${feature}|in|${vals}`;
  }
  return `${feature}|${operator}|${normThreshold(threshold)}`;
}

function isCategoricalFeature(f) {
  if (!f) return false;
  if (typeof f === "string") {
    const r = getFeatureRule(f);
    if (r.operator === "in" || r.valueType === "categorical") return true;
    const meta = findFeatureMetaByName(f);
    return meta?.value_type === "categorical" || meta?.rule_operator === "in";
  }
  const r = getFeatureRule(f.feature);
  if (r.operator === "in" || r.valueType === "categorical") return true;
  return f.value_type === "categorical" || f.rule_operator === "in";
}

function findFeatureMetaByName(feature) {
  if (!reviewData?.clusters) return null;
  for (const g of reviewData.clusters) {
    const f = g.features.find((x) => x.feature === feature);
    if (f) return f;
  }
  return null;
}

function resolveRuleBadRate(pr) {
  if (pr.hit_count > 0 && pr.bad_count != null) {
    return pr.bad_count / pr.hit_count;
  }
  if (pr.bad_rate != null && !Number.isNaN(Number(pr.bad_rate))) {
    return Number(pr.bad_rate);
  }
  const meta = findFeatureMetaByName(pr.feature);
  return meta?.max_bad_rate ?? 0;
}

function getFeatureLiveStats(f) {
  const live = liveFeatureStats.get(f.feature);
  if (live && live.ruleKey === currentRuleKey(f.feature)) return live;
  return null;
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function headers(json = true) {
  const h = { Authorization: `Bearer ${token}` };
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, options);
  if (res.status === 401) {
    logout();
    throw new Error("登录已过期，请重新登录");
  }
  const data = res.headers.get("content-type")?.includes("json")
    ? await res.json()
    : null;
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((d) => d.msg).join("; ")
        : res.statusText;
    throw new Error(msg || res.statusText);
  }
  return data;
}

function showView(name) {
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#${name}-view`).classList.add("active");
}

function setStep(n) {
  n = Number(n);
  currentStep = n;
  maxReachedStep = Math.max(maxReachedStep, n);
  $$(".step-item").forEach((s) => {
    const step = Number(s.dataset.step);
    s.classList.toggle("active", step === n);
    s.classList.toggle("visited", step <= maxReachedStep);
  });
  $$(".panel").forEach((p) => p.classList.remove("active"));
  $(`#step-${n}`).classList.add("active");
}

async function navigateToStep(n) {
  n = Number(n);
  if (n === currentStep) return;

  if (n >= 2 && !fileId) {
    alert("请先上传数据文件");
    return;
  }
  if (n >= 4 && !getStoredJobId()) {
    alert("请先完成分箱");
    return;
  }
  if (n === 6) {
    if (!selectedFeatures.size) {
      alert("请至少勾选一个特征");
      return;
    }
    const missingCategorical = [...selectedFeatures].filter((feat) => {
      const meta = findFeatureMetaByName(feat);
      if (!meta || meta.value_type !== "categorical") return false;
      const r = getFeatureRule(feat);
      return r.operator === "in" && !(r.values?.length);
    });
    if (missingCategorical.length) {
      alert(`以下类别变量尚未选择要拒绝的类别：${missingCategorical.join("、")}`);
      return;
    }
    if (!buildSelectedRules().length) {
      alert("请至少勾选一个有效规则");
      return;
    }
  }

  if (n === 2) {
    setStep(2);
    await refreshValidation();
    return;
  }
  if (n === 3) {
    const data = await refreshValidation();
    if (data) {
      renderRunStats(data);
      renderRunSummary();
    } else {
      renderRunStats(null);
    }
    setStep(3);
    return;
  }
  if (n === 4) {
    currentJobId = getStoredJobId();
    setStep(4);
    await loadFeatureReviewSummary();
    return;
  }
  if (n === 5) {
    currentJobId = getStoredJobId();
    setStep(5);
    loadFeatureState();
    if (!reviewData?.clusters?.length) {
      await loadFeatureReviewPick();
    } else {
      renderFeatureClusters(reviewData.clusters);
      updateSelectedCount();
      updateSelectAllCheckbox();
    }
    return;
  }
  if (n === 6) {
    setStep(6);
    await loadSerialAnalysis();
    return;
  }
  setStep(n);
}

function logout() {
  token = "";
  localStorage.removeItem("risk_token");
  showView("login");
}

// Login
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = $("#login-error");
  errEl.classList.add("hidden");
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        username: $("#username").value,
        password: $("#password").value,
      }),
    });
    token = data.access_token;
    localStorage.setItem("risk_token", token);
    showView("main");
    setStep(1);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

$("#logout-btn").addEventListener("click", logout);

if (token) {
  api("/api/health").then(() => {
    showView("main");
    setStep(1);
  }).catch(logout);
}

// Upload
const uploadZone = $("#upload-zone");
const fileInput = $("#file-input");

$("#pick-file").addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});

uploadZone.addEventListener("click", () => fileInput.click());

uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  uploadZone.classList.add("dragover");
});

uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("dragover"));

uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) handleFile(fileInput.files[0]);
});

async function handleFile(file) {
  if (!token) {
    alert("请先登录后再上传文件");
    logout();
    return;
  }

  const form = new FormData();
  form.append("file", file);
  const sizeMb = file.size / (1024 * 1024);
  const isExcel = /\.xlsx?$/i.test(file.name);
  uploadZone.querySelector(".upload-inner p").textContent = isExcel
    ? `正在解析 Excel（${sizeMb.toFixed(1)} MB，本地也可能需 10–60 秒）…`
    : sizeMb > 5
      ? `正在解析数据（${sizeMb.toFixed(1)} MB，本地处理中）…`
      : "正在解析数据…";

  try {
    const res = await fetch(`${API}/api/upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (res.status === 401) {
      logout();
      throw new Error("登录已过期，请重新登录后再上传");
    }
    const data = await res.headers.get("content-type")?.includes("json")
      ? await res.json()
      : null;
    if (!res.ok) {
      const detail = data?.detail;
      const msg = typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg).join("; ")
          : "上传失败";
      throw new Error(msg);
    }

    fileId = data.file_id;
    lastValidationData = null;
    lastValidationPayloadKey = "";
    fileMeta = data;
    defaultDropCols = data.default_drop_cols || [];
    dropColsSelection = new Set(defaultDropCols);
    renderFileInfo(data, file.name);
    populateSelects(data);
    renderDropColsPanel();
    $("#to-step-2").disabled = false;
    uploadZone.querySelector(".upload-inner p").innerHTML =
      `已上传：<strong>${file.name}</strong> — 拖拽或 <button type="button" class="link-btn" id="pick-file">重新选择</button>`;
    $("#pick-file")?.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
  } catch (err) {
    uploadZone.querySelector(".upload-inner p").textContent = `上传失败：${err.message}`;
  }
}

function renderFileInfo(data, filename) {
  const el = $("#file-info");
  el.classList.remove("hidden");
  el.innerHTML = `
    <strong>${filename}</strong>
    <dl class="info-grid">
      <div><dt>行数</dt><dd>${data.rows.toLocaleString()}</dd></div>
      <div><dt>列数</dt><dd>${data.columns}</dd></div>
    </dl>
  `;
}

function populateSelects(data) {
  const labelSel = $("#label-col");
  const timeSel = $("#time-col");
  const allCols = data.column_names || [];
  const defaultLabel = data.suggested_label || data.label || allCols[0];
  const defaultTime = data.suggested_time_col || data.time_candidates?.[0] || allCols[0];

  const options = allCols.map((c) => `<option value="${c}">${c}</option>`).join("");
  labelSel.innerHTML = options;
  timeSel.innerHTML = options;

  labelSel.value = allCols.includes(defaultLabel) ? defaultLabel : allCols[0];
  timeSel.value = allCols.includes(defaultTime) ? defaultTime : allCols[0];
  autoLabelFixDone = false;
  renderDropColsPanel();
}

function getProtectedDropCols() {
  const protectedCols = new Set();
  const label = $("#label-col")?.value;
  const time = $("#time-col")?.value;
  const splitMode = $("#split-mode")?.value;
  if (label) protectedCols.add(label);
  if (time && (splitMode === "ai" || splitMode === "cutoff")) protectedCols.add(time);
  return protectedCols;
}

function renderDropColsPanel() {
  const panel = $("#drop-cols-panel");
  if (!panel || !fileMeta.column_names) return;

  const protectedCols = getProtectedDropCols();
  const query = ($("#drop-cols-search")?.value || "").trim().toLowerCase();

  panel.innerHTML = `
    <div class="drop-cols-header">
      <span aria-hidden="true">选</span>
      <span>变量名</span>
    </div>
    ${fileMeta.column_names.map((col) => {
    const isProtected = protectedCols.has(col);
    const checked = dropColsSelection.has(col) && !isProtected;
    const hidden = query && !col.toLowerCase().includes(query);
    return `
      <label class="drop-cols-item${isProtected ? " disabled" : ""}${hidden ? " hidden-by-search" : ""}" data-col="${col}">
        <input type="checkbox" value="${col}" ${checked ? "checked" : ""} ${isProtected ? "disabled" : ""} />
        <span>${col}${isProtected ? "（保留）" : ""}</span>
      </label>
    `;
  }).join("")}`;

  panel.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) dropColsSelection.add(cb.value);
      else dropColsSelection.delete(cb.value);
      updateDropColsCount();
    });
  });

  updateDropColsCount();
}

function updateDropColsCount() {
  const protectedCols = getProtectedDropCols();
  const count = [...dropColsSelection].filter((c) => !protectedCols.has(c)).length;
  const el = $("#drop-cols-count");
  if (el) el.textContent = String(count);
}

function getSelectedDropCols() {
  const protectedCols = getProtectedDropCols();
  return [...dropColsSelection].filter((c) => !protectedCols.has(c));
}

$("#drop-cols-search")?.addEventListener("input", renderDropColsPanel);

$("#drop-cols-default")?.addEventListener("click", () => {
  dropColsSelection = new Set(defaultDropCols);
  renderDropColsPanel();
});

$("#drop-cols-clear")?.addEventListener("click", () => {
  dropColsSelection.clear();
  renderDropColsPanel();
});

// Method cards
$$(".method-card").forEach((card) => {
  card.addEventListener("click", () => {
    $$(".method-card").forEach((c) => c.classList.remove("selected"));
    card.classList.add("selected");
    card.querySelector("input").checked = true;
    updateBinNumVisibility();
  });
});

function updateBinNumVisibility() {
  const method = document.querySelector('input[name="method"]:checked').value;
  $("#bin-num-field").classList.toggle("hidden", method === "headtail5");
}

$("#split-mode").addEventListener("change", () => {
  const mode = $("#split-mode").value;
  $("#oot-ratio-field").classList.toggle("hidden", mode !== "ai");
  $("#cutoff-field").classList.toggle("hidden", mode !== "cutoff");
  $("#manual-split-field").classList.toggle("hidden", mode !== "manual");
  renderDropColsPanel();
  refreshValidation();
});

// Navigation
$$(".step-item").forEach((item) => {
  item.addEventListener("click", () => {
    const step = Number(item.dataset.step);
    if (step > maxReachedStep) return;
    navigateToStep(step);
  });
});

$("#to-step-2").addEventListener("click", async () => {
  setStep(2);
  await refreshValidation();
});

$("#back-to-1").addEventListener("click", () => setStep(1));
$("#to-step-3").addEventListener("click", async () => {
  const btn = $("#to-step-3");
  if (!fileId) {
    alert("请先返回第 1 步上传数据文件");
    return;
  }
  if (btn.disabled) return;

  const key = validationPayloadKey();
  const hasCached = lastValidationData && lastValidationPayloadKey === key;
  const oldText = btn.textContent;

  if (!hasCached) {
    btn.disabled = true;
    btn.textContent = "验证中…";
  }
  try {
    const data = await refreshValidation();
    if (!data) {
      renderRunStats(null);
      setStep(3);
      return;
    }
    renderRunStats(data);
    renderRunSummary();
    setStep(3);
  } catch (err) {
    alert(err.message || "无法进入下一步，请检查参数或重新登录");
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
});
$("#back-to-2").addEventListener("click", () => setStep(2));

function pct(rate) {
  return `${(rate * 100).toFixed(2)}%`;
}

function renderValidationInfo(data) {
  const warning = data.label_warning
    ? `<p class="warn">${data.label_warning}</p>`
    : "";

  $("#validation-info").innerHTML = `
    <strong>数据验证</strong>
    ${warning}
    <dl class="info-grid">
      <div><dt>行数</dt><dd>${data.rows.toLocaleString()}</dd></div>
      <div><dt>列数</dt><dd>${data.columns}</dd></div>
      <div><dt>有效样本(0/1)</dt><dd>${data.valid_samples.toLocaleString()}</dd></div>
      <div><dt>大盘坏率</dt><dd>${pct(data.portfolio_bad_rate)}</dd></div>
    </dl>
  `;
}

function readOotRatio() {
  const splitMode = $("#split-mode")?.value || "ai";
  if (splitMode !== "ai") return 0;
  const v = parseFloat($("#oot-ratio")?.value);
  return Number.isFinite(v) ? v : 0.2;
}

function buildValidatePayload() {
  const splitMode = $("#split-mode")?.value || "ai";
  return {
    label: $("#label-col").value,
    split_mode: splitMode,
    time_col: $("#time-col").value,
    oot_ratio: readOotRatio(),
    cutoff_date: $("#cutoff-date")?.value || null,
    train_file_id: trainFileId || null,
    test_file_id: testFileId || null,
  };
}

function payloadToQuery(payload) {
  const params = new URLSearchParams();
  params.set("label", payload.label);
  params.set("split_mode", payload.split_mode);
  if (payload.time_col) params.set("time_col", payload.time_col);
  params.set("oot_ratio", String(payload.oot_ratio));
  if (payload.cutoff_date) params.set("cutoff_date", payload.cutoff_date);
  if (payload.train_file_id) params.set("train_file_id", payload.train_file_id);
  if (payload.test_file_id) params.set("test_file_id", payload.test_file_id);
  return params;
}

function validationPayloadKey(payload = buildValidatePayload()) {
  return JSON.stringify(payload);
}

async function refreshValidation(options = {}) {
  const { force = false } = options;
  if (!fileId) return null;

  const payload = buildValidatePayload();
  const key = validationPayloadKey(payload);

  if (!force && lastValidationData && lastValidationPayloadKey === key) {
    renderValidationInfo(lastValidationData);
    return lastValidationData;
  }

  const infoEl = $("#validation-info");
  if (infoEl) infoEl.innerHTML = `<p class="sub">正在验证数据，请稍候…</p>`;

  const label = payload.label;

  try {
    const data = await api(`/api/files/${fileId}/validate`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        label: payload.label,
        split_mode: payload.split_mode,
        time_col: payload.time_col || null,
        oot_ratio: payload.oot_ratio,
        cutoff_date: payload.cutoff_date,
        train_file_id: payload.train_file_id,
        test_file_id: payload.test_file_id,
      }),
    });

    // 标签列无 0/1 时自动切到推荐列（仅一次，避免死循环）
    if (
      !autoLabelFixDone &&
      data.valid_samples === 0 &&
      data.suggested_label &&
      data.suggested_label !== label
    ) {
      autoLabelFixDone = true;
      $("#label-col").value = data.suggested_label;
      return refreshValidation();
    }

    lastValidationData = data;
    lastValidationPayloadKey = key;
    renderValidationInfo(data);
    return data;
  } catch (err) {
    lastValidationData = null;
    lastValidationPayloadKey = "";
    $("#validation-info").innerHTML = `<span class="error">${err.message}</span>`;
    return null;
  }
}

function renderRunStats(data) {
  const el = $("#run-stats");
  if (!el) return;

  if (!data) {
    el.innerHTML = `<span class="error">无法加载样本统计，请返回上一步检查参数</span>`;
    return;
  }

  const split = data.split;
  const dash = "—";
  const splitHint = data.split_hint || data.split_error || "";

  const trainSamples = split?.train ? split.train.valid_samples.toLocaleString() : dash;
  const trainRate = split?.train ? pct(split.train.bad_rate) : dash;
  const testSamples = split?.test ? split.test.valid_samples.toLocaleString() : dash;
  const testRate = split?.test ? pct(split.test.bad_rate) : dash;

  el.innerHTML = `
    <h3>样本概览</h3>
    <table class="run-stats-table">
      <thead>
        <tr>
          <th></th>
          <th>样本数（0/1）</th>
          <th>坏率</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="row-label">大盘</td>
          <td class="num">${data.valid_samples.toLocaleString()}</td>
          <td class="num">${pct(data.portfolio_bad_rate)}</td>
        </tr>
        <tr>
          <td class="row-label">Train</td>
          <td class="num">${trainSamples}</td>
          <td class="num">${trainRate}</td>
        </tr>
        <tr>
          <td class="row-label">Test</td>
          <td class="num">${testSamples}</td>
          <td class="num">${testRate}</td>
        </tr>
      </tbody>
    </table>
    ${splitHint ? `<p class="warn">${splitHint}</p>` : ""}
  `;
}

$("#label-col").addEventListener("change", () => {
  autoLabelFixDone = false;
  renderDropColsPanel();
  refreshValidation();
});
$("#time-col").addEventListener("change", () => {
  renderDropColsPanel();
  refreshValidation();
});
$("#oot-ratio").addEventListener("change", refreshValidation);
$("#oot-ratio").addEventListener("input", refreshValidation);
$("#cutoff-date").addEventListener("input", refreshValidation);

async function uploadSplitFile(file, type) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API}/api/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail);
  if (type === "train") {
    trainFileId = data.file_id;
    $("#train-file-name").textContent = file.name;
  } else {
    testFileId = data.file_id;
    $("#test-file-name").textContent = file.name;
  }
  await refreshValidation();
}

$("#train-file").addEventListener("change", (e) => {
  if (e.target.files[0]) uploadSplitFile(e.target.files[0], "train");
});
$("#test-file").addEventListener("change", (e) => {
  if (e.target.files[0]) uploadSplitFile(e.target.files[0], "test");
});

function renderRunSummary() {
  const method = document.querySelector('input[name="method"]:checked').value;
  const methodNames = { quantile: "等频", chisquare: "卡方", headtail5: "头尾5%" };
  const splitNames = { ai: "AI 自动划分", cutoff: "条件划分", manual: "自行划分" };
  const splitMode = $("#split-mode").value;
  let splitDetail = "";
  if (splitMode === "ai") {
    const oot = $("#oot-ratio").value;
    splitDetail = `<div><dt>OOT 比例</dt><dd>${oot}</dd></div>`;
  } else if (splitMode === "cutoff") {
    splitDetail = `<div><dt>截止日</dt><dd>${$("#cutoff-date").value || "未填写"}</dd></div>
      <div><dt>说明</dt><dd>条件划分按截止日切分，与 OOT 比例无关</dd></div>`;
  }
  $("#run-summary").innerHTML = `
    <strong>即将执行</strong>
    <dl class="info-grid">
      <div><dt>分箱方法</dt><dd>${methodNames[method]}</dd></div>
      <div><dt>标签列</dt><dd>${$("#label-col").value}</dd></div>
      <div><dt>切分方式</dt><dd>${splitNames[splitMode]}</dd></div>
      ${splitDetail}
      <div><dt>箱数</dt><dd>${method === "headtail5" ? "自动(头尾5%)" : $("#bin-num").value}</dd></div>
    </dl>
  `;
}

// Progress animation with walking cat
let progressTimers = [];
let simulatedProgress = 0;

const BINNING_POLL_INTERVAL_MS = 2000;
const BINNING_POLL_TIMEOUT_MS = 2 * 60 * 60 * 1000;

const PROGRESS_HINTS = [
  "正在读取数据喵~",
  "正在过滤标签 0/1 喵~",
  "正在切分 Train / Test 喵~",
  "正在 FIT 学习分箱边界 喵~",
  "正在 APPLY 到 Test 喵~",
  "正在计算 bad_rate / IV / KS 喵~",
  "正在写入 Excel 喵~",
  "小猫快跑，马上就好喵~",
];

function setProgress(pct) {
  const clamped = Math.max(0, Math.min(100, pct));
  simulatedProgress = clamped;
  const fill = $("#progress-fill");
  const cat = $("#progress-cat");
  if (fill) fill.style.width = `${clamped}%`;
  if (cat) cat.style.left = `${clamped}%`;
}

function startProgressAnimation(method) {
  stopProgressAnimation();
  setProgress(2);

  let dotsIdx = 0;
  let hintIdx = 0;

  const hintEl = $("#progress-hint");
  if (hintEl && method === "headtail5") {
    hintEl.textContent = "头尾5% 变量多时较久（最长约 2 小时），请勿关闭页面喵~";
  }

  progressTimers.push(setInterval(() => {
    if (simulatedProgress < 92) {
      const step = Math.max(0.4, (95 - simulatedProgress) / 18);
      setProgress(simulatedProgress + step);
    }
  }, 500));

  progressTimers.push(setInterval(() => {
    dotsIdx = (dotsIdx + 1) % 4;
    const dots = ["", ".", "..", "..."];
    const el = $("#progress-dots");
    if (el) el.textContent = dots[dotsIdx];
  }, 450));

  progressTimers.push(setInterval(() => {
    hintIdx = (hintIdx + 1) % PROGRESS_HINTS.length;
    const el = $("#progress-hint");
    if (el && method !== "headtail5") {
      el.textContent = PROGRESS_HINTS[hintIdx];
    }
  }, 2800));
}

function stopProgressAnimation() {
  progressTimers.forEach(clearInterval);
  progressTimers = [];
}

async function finishProgressAnimation() {
  setProgress(100);
  const hint = $("#progress-hint");
  const dots = $("#progress-dots");
  if (hint) hint.textContent = "分箱完成，小猫到站啦！🎉";
  if (dots) dots.textContent = "";
  await sleep(600);
}

// Run binning
$("#run-binning").addEventListener("click", async () => {
  $("#progress-area").classList.remove("hidden");
  $("#result-area").classList.add("hidden");
  $("#error-area").classList.add("hidden");
  $("#run-binning").disabled = true;
  const method = document.querySelector('input[name="method"]:checked').value;
  startProgressAnimation(method);
  const body = {
    file_id: fileId,
    method,
    label: $("#label-col").value,
    time_col: $("#time-col").value,
    bin_num: parseInt($("#bin-num").value, 10),
    init_bin_num: 20,
    oot_ratio: readOotRatio(),
    split_mode: $("#split-mode").value,
    cutoff_date: $("#cutoff-date").value || null,
    train_file_id: trainFileId || null,
    test_file_id: testFileId || null,
    drop_cols: getSelectedDropCols(),
  };

  try {
    const { job_id } = await api("/api/binning/run", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(body),
    });

    await pollJob(job_id);
    stopProgressAnimation();
    await finishProgressAnimation();
  } catch (err) {
    stopProgressAnimation();
    showError(err.message);
  } finally {
    $("#run-binning").disabled = false;
    if ($("#error-area").classList.contains("hidden")) {
      // 成功时保留进度条展示一会儿；失败则收起
    } else {
      $("#progress-area").classList.add("hidden");
    }
  }
});

async function pollJob(jobId) {
  const maxAttempts = Math.ceil(BINNING_POLL_TIMEOUT_MS / BINNING_POLL_INTERVAL_MS);
  for (let i = 0; i < maxAttempts; i++) {
    const job = await api(`/api/binning/${jobId}`, { headers: headers(false) });
    if (typeof job.progress === "number" && job.progress > simulatedProgress) {
      setProgress(job.progress);
    }
    if (job.status === "completed") {
      showResult(job, jobId);
      return;
    }
    if (job.status === "failed") {
      throw new Error(job.message);
    }
    await sleep(BINNING_POLL_INTERVAL_MS);
  }
  throw new Error("分箱超时（已等待 2 小时），请稍后重试或缩小变量数");
}

function showResult(job, jobId) {
  currentJobId = jobId;
  sessionStorage.setItem("risk_job_id", jobId);
  $("#result-area").classList.remove("hidden");
  $("#to-step-4").classList.remove("hidden");
  $("#result-summary").textContent = JSON.stringify(job.summary, null, 2);
  const link = $("#download-link");
  link.href = `/api/binning/${jobId}/download`;
  link.onclick = async (e) => {
    e.preventDefault();
    const res = await fetch(link.href, { headers: { Authorization: `Bearer ${token}` } });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = job.summary.output_file || "分箱结果.xlsx";
    a.click();
    URL.revokeObjectURL(url);
  };
}

function showError(msg) {
  const el = $("#error-area");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ——— Step 4 / 5: Feature review ———

function getStoredJobId() {
  return currentJobId || sessionStorage.getItem("risk_job_id") || "";
}

$("#to-step-4")?.addEventListener("click", async () => {
  currentJobId = getStoredJobId();
  if (!currentJobId) {
    alert("请先完成分箱");
    return;
  }
  setStep(4);
  await loadFeatureReviewSummary();
});

$("#to-step-5")?.addEventListener("click", async () => {
  currentJobId = getStoredJobId();
  if (!currentJobId) {
    alert("请先完成分箱");
    return;
  }
  resetPickStage();
  setStep(5);
  const sameQuery = reviewQueryKey() === lastReviewQueryKey;
  if (reviewData?.clusters?.length && sameQuery) {
    renderFeatureClusters(reviewData.clusters);
    updateSelectedCount();
    updateSelectAllCheckbox();
    warmupReviewCache();
    scheduleRejectPreview();
    return;
  }
  await loadFeatureReviewPick();
});

$("#refresh-pick")?.addEventListener("click", () => loadFeatureReviewPick());

$("#feature-sort")?.addEventListener("change", (e) => {
  featureSortMode = e.target.value;
  if (reviewData?.clusters) renderFeatureClusters(reviewData.clusters);
});

$("#close-detail")?.addEventListener("click", () => closePickDetail());

function resetPickStage() {
  activeFeature = "";
  liveFeatureStats.clear();
  clearTimeout(rejectPreviewTimer);
  rejectPreviewAbort?.abort();
  const stage = $("#pick-stage");
  if (stage) stage.classList.remove("is-split");
  const panel = $("#feature-detail-panel");
  if (panel) panel.setAttribute("aria-hidden", "true");
}

function closePickDetail() {
  activeFeature = "";
  $("#pick-stage")?.classList.remove("is-split");
  $("#feature-detail-panel")?.setAttribute("aria-hidden", "true");
  $$(".feature-row").forEach((r) => r.classList.remove("active"));
}

$("#back-to-3")?.addEventListener("click", () => setStep(3));
$("#back-to-4")?.addEventListener("click", () => setStep(4));

$("#refresh-review")?.addEventListener("click", () => loadFeatureReviewSummary());

$("#manual-feature-search")?.addEventListener("input", (e) => {
  renderManualFeatureSuggestions(e.target.value);
});
$("#manual-feature-search")?.addEventListener("focus", (e) => {
  if (e.target.value.trim()) renderManualFeatureSuggestions(e.target.value);
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".manual-include-search-wrap")) hideManualSuggestions();
});

$("#clear-selected")?.addEventListener("click", () => {
  selectedFeatures.clear();
  featureRules.clear();
  liveFeatureStats.clear();
  saveFeatureState();
  markSerialStale();
  syncFeatureCheckboxes();
  updateSelectedCount();
  scheduleRejectPreview();
});

$("#select-all-features")?.addEventListener("change", (e) => {
  selectAllFeatures(e.target.checked);
});

$$(".detail-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".detail-tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $("#detail-bins").classList.toggle("hidden", tab !== "bins");
    $("#detail-stability").classList.toggle("hidden", tab !== "stability");
    if (tab === "stability" && activeFeature) {
      loadFeatureStability(activeFeature);
    }
  });
});

function buildReviewQuery() {
  const threshold = parseFloat($("#review-threshold").value) || 0.6;
  const minHit = parseInt($("#review-min-hit").value, 10) || 10;
  const params = new URLSearchParams();
  params.set("bad_rate_threshold", String(threshold));
  params.set("min_hit_count", String(minHit));
  if (manualIncludeFeatures.size) {
    params.set("include_features", [...manualIncludeFeatures].join(","));
  }
  return params;
}

function manualIncludeStorageKey() {
  const jobId = getStoredJobId();
  return jobId ? `manual_include_${jobId}` : "";
}

function loadManualIncludesFromStorage() {
  const key = manualIncludeStorageKey();
  if (!key) {
    manualIncludeFeatures.clear();
    return;
  }
  try {
    const raw = localStorage.getItem(key);
    manualIncludeFeatures = new Set(raw ? JSON.parse(raw) : []);
  } catch {
    manualIncludeFeatures.clear();
  }
}

function saveFeatureState() {
  const jobId = getStoredJobId();
  if (!jobId) return;
  const rules = {};
  for (const [k, v] of featureRules) rules[k] = v;
  localStorage.setItem(`feature_state_${jobId}`, JSON.stringify({
    rules,
    selected: [...selectedFeatures],
  }));
}

function loadFeatureState() {
  const jobId = getStoredJobId();
  if (!jobId) return;
  try {
    const raw = localStorage.getItem(`feature_state_${jobId}`);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (data.rules && typeof data.rules === "object") {
      featureRules.clear();
      for (const [k, v] of Object.entries(data.rules)) {
        featureRules.set(k, v);
      }
    }
    if (Array.isArray(data.selected)) {
      selectedFeatures.clear();
      for (const f of data.selected) selectedFeatures.add(f);
    }
  } catch {
    /* ignore corrupt storage */
  }
}

function markSerialStale() {
  serialAnalysisStale = true;
  const meta = $("#serial-meta");
  if (meta && serialAnalysisData) {
    meta.textContent = `${meta.textContent.split(" · ")[0] || ""} · ⚠ 规则已修改，请点击「重新计算」更新串联结果`;
  }
}

function saveManualIncludes() {
  const key = manualIncludeStorageKey();
  if (key) {
    localStorage.setItem(key, JSON.stringify([...manualIncludeFeatures]));
  }
}

function addManualIncludeFeature(feature) {
  if (!feature || manualIncludeFeatures.has(feature)) return;
  manualIncludeFeatures.add(feature);
  saveManualIncludes();
  renderManualIncludeChips();
  const search = $("#manual-feature-search");
  if (search) search.value = "";
  hideManualSuggestions();
  loadFeatureReviewSummary();
}

function removeManualIncludeFeature(feature) {
  if (!manualIncludeFeatures.has(feature)) return;
  manualIncludeFeatures.delete(feature);
  saveManualIncludes();
  renderManualIncludeChips();
  loadFeatureReviewSummary();
}

function hideManualSuggestions() {
  const box = $("#manual-feature-suggestions");
  if (box) {
    box.classList.add("hidden");
    box.innerHTML = "";
  }
}

function renderManualIncludeChips() {
  const el = $("#manual-include-chips");
  if (!el) return;
  if (!manualIncludeFeatures.size) {
    el.innerHTML = `<p class="sub">暂无手动追加；在上方搜索并点击变量即可加入。</p>`;
    return;
  }
  const metaMap = new Map((reviewData?.all_features || []).map((f) => [f.feature, f]));
  el.innerHTML = [...manualIncludeFeatures].map((feat) => {
    const meta = metaMap.get(feat);
    const cn = meta?.chinese_name ? ` · ${meta.chinese_name}` : "";
    return `
      <span class="manual-chip" data-feature="${feat}">
        <span>${feat}${cn}</span>
        <button type="button" class="manual-chip-remove" data-feature="${feat}" title="移除">×</button>
      </span>
    `;
  }).join("");
  el.querySelectorAll(".manual-chip-remove").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      removeManualIncludeFeature(btn.dataset.feature);
    });
  });
}

function renderManualFeatureSuggestions(query) {
  const box = $("#manual-feature-suggestions");
  if (!box) return;
  const q = (query || "").trim().toLowerCase();
  if (!q || !reviewData?.all_features?.length) {
    hideManualSuggestions();
    return;
  }
  const matches = reviewData.all_features.filter((f) => {
    const hay = `${f.feature} ${f.chinese_name || ""}`.toLowerCase();
    return hay.includes(q);
  }).slice(0, 20);

  if (!matches.length) {
    box.innerHTML = `<p class="sub">无匹配变量</p>`;
    box.classList.remove("hidden");
    return;
  }

  box.innerHTML = matches.map((f) => {
    const already = manualIncludeFeatures.has(f.feature);
    const auto = f.is_candidate && !already;
    const tag = auto ? "已满足阈值" : already ? "已追加" : "未达阈值";
    return `
      <button type="button" class="manual-suggestion-item${already || auto ? " disabled" : ""}" data-feature="${f.feature}" ${already || auto ? "disabled" : ""}>
        <span class="name">${f.feature}</span>
        <span class="cn">${f.chinese_name || "—"}</span>
        <span class="tag">${tag}</span>
      </button>
    `;
  }).join("");

  box.querySelectorAll(".manual-suggestion-item:not(.disabled)").forEach((btn) => {
    btn.addEventListener("click", () => addManualIncludeFeature(btn.dataset.feature));
  });
  box.classList.remove("hidden");
}

async function fetchReviewData(options = {}) {
  const jobId = getStoredJobId();
  if (!jobId) {
    throw new Error("未找到分箱任务，请返回第 3 步重新执行分箱。");
  }
  currentJobId = jobId;
  const query = buildReviewQuery();
  if (options.skipOverview) {
    query.set("include_threshold_overview", "false");
  }
  return api(`/api/binning/${jobId}/review?${query}`, {
    headers: headers(false),
  });
}

async function loadFeatureReviewSummary() {
  const errEl = $("#review-error");
  errEl.classList.add("hidden");
  loadManualIncludesFromStorage();

  $("#review-summary-card").classList.remove("hidden");
  $("#review-summary-card").innerHTML = `<p class="sub">统计中...</p>`;

  try {
    reviewData = await fetchReviewData({ skipOverview: true });
    lastReviewQueryKey = reviewQueryKey();
    liveFeatureStats.clear();
    renderReviewSummaryCount(reviewData);
    renderManualIncludeChips();
    renderReviewReport(reviewData);
    loadThresholdOverviewLazy();
    const btn5 = $("#to-step-5");
    if (btn5) btn5.disabled = false;
  } catch (err) {
    reviewData = null;
    $("#review-summary-card").classList.add("hidden");
    errEl.textContent = err.message.includes("Not Allowed")
      ? `${err.message} — 请重启后端服务后再试（需加载最新 API）`
      : err.message;
    errEl.classList.remove("hidden");
    const btn5 = $("#to-step-5");
    if (btn5) btn5.disabled = true;
  }
}

async function loadFeatureReviewPick() {
  const clustersEl = $("#feature-clusters");
  const errEl = $("#review-error");
  loadManualIncludesFromStorage();
  loadFeatureState();
  if (clustersEl) {
    clustersEl.innerHTML = `<p class="sub">正在加载候选特征...</p>`;
  }
  try {
    const warm = warmupReviewCache();
    reviewData = await fetchReviewData({ skipOverview: true });
    await warm;
    lastReviewQueryKey = reviewQueryKey();
    liveFeatureStats.clear();
    if (!reviewData.total_candidates) {
      if (clustersEl) {
        clustersEl.innerHTML = `<p class="sub">当前阈值下无候选变量，请返回 Step 4 调低阈值、手动追加特征，或减少最少命中人数后点「刷新候选」。</p>`;
      }
      $("#review-count").textContent = "0";
      updateSelectedCount();
      return;
    }
    renderFeatureClusters(reviewData.clusters);
    $("#review-count").textContent = String(reviewData.total_candidates);
    updateSelectedCount();
    scheduleRejectPreview();
    if (errEl) errEl.classList.add("hidden");
  } catch (err) {
    if (clustersEl) {
      clustersEl.innerHTML = `<p class="error">${err.message}</p>`;
    }
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  }
}

function renderReviewSummaryCount(data) {
  const el = $("#review-summary-card");
  el.classList.remove("hidden");
  const manual = data.manual_includes || 0;
  const auto = data.auto_candidates ?? (data.total_candidates - manual);
  const extraLine = manual
    ? `<p class="sub count-extra">其中自动筛出 ${auto} 个，手动追加 ${manual} 个</p>`
    : "";
  el.innerHTML = `
    <div class="count-hero">
      <span class="count-num">${data.total_candidates}</span>
      <span class="count-label">个特征满足当前条件</span>
    </div>
    ${extraLine}
    <p class="sub count-hint">条件：坏率 &gt; ${pct(data.bad_rate_threshold)} 且单箱命中 ≥ ${data.min_hit_count} 人（<strong>数字列</strong>：头/尾箱；<strong>类别列</strong>：任一超阈值类别，明细中勾选拒绝）</p>
  `;
}

function renderReviewReport(data) {
  const el = $("#review-report");
  el.classList.remove("hidden");
  el.innerHTML = `
    <strong>筛选概览</strong>
    <p class="desc" style="margin:.75rem 0">${data.report_summary}</p>
    <dl class="info-grid">
      <div><dt>Train 大盘坏率</dt><dd>${pct(data.train_bad_rate)}</dd></div>
      <div><dt>Test 大盘坏率</dt><dd>${data.test_bad_rate != null ? pct(data.test_bad_rate) : "—"}</dd></div>
      <div><dt>当前阈值</dt><dd>${pct(data.bad_rate_threshold)}</dd></div>
      <div><dt>最少命中</dt><dd>${data.min_hit_count} 人</dd></div>
    </dl>
    <p class="warn" style="margin-top:.75rem">最少命中人数：数值变量指头/尾箱样本量，类别变量指单个超阈值类别的样本量。建议 ≥10；只抓极端高风险可提高到 20–50。</p>
  `;
}

let lastReviewQueryKey = "";

function reviewQueryKey() {
  const q = buildReviewQuery();
  q.sort();
  return q.toString();
}

function warmupReviewCache() {
  const jobId = getStoredJobId();
  if (!jobId) return Promise.resolve();
  return api(`/api/binning/${jobId}/warmup`, {
    method: "POST",
    headers: headers(false),
  }).catch(() => {});
}

async function loadThresholdOverviewLazy() {
  const wrap = $("#threshold-overview");
  if (!wrap) return;
  wrap.classList.remove("hidden");
  wrap.innerHTML = `<p class="sub">阈值概览计算中（约 3–8 秒，不阻塞选特征）…</p>`;
  try {
    const minHit = parseInt($("#review-min-hit").value, 10) || 10;
    const data = await api(
      `/api/binning/${getStoredJobId()}/threshold-overview?min_hit_count=${minHit}`,
      { headers: headers(false) }
    );
    renderThresholdOverview(data.threshold_overview);
  } catch (err) {
    wrap.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function renderThresholdOverview(rows) {
  const wrap = $("#threshold-overview");
  if (!rows?.length) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  wrap.innerHTML = `
    <h3 style="font-size:1rem;margin:1rem 0 .5rem">阈值概览（辅助判断切割点）</h3>
    <div class="threshold-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>拒绝坏率阈值</th>
            <th>候选变量数</th>
            <th>平均单箱命中</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${r.threshold_pct}</td>
              <td>${r.feature_count}</td>
              <td>${r.avg_hit || "—"}</td>
              <td style="text-align:left;font-size:.75rem;color:var(--muted)">${r.hint}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function isFeatureStatsPending(f) {
  const rule = getFeatureRule(f.feature);
  return Boolean(rule.userEdited && !getFeatureLiveStats(f));
}

function formatFeatureHitCount(f) {
  const live = getFeatureLiveStats(f);
  if (live) return `${live.hit_count} 人`;
  if (isFeatureStatsPending(f)) return "…";
  return `${f.hit_count} 人`;
}

function formatFeatureBadRate(f) {
  const live = getFeatureLiveStats(f);
  if (live) return pct(live.bad_rate);
  if (isFeatureStatsPending(f)) return "…";
  return pct(f.max_bad_rate);
}

function getFeatureHitCount(f) {
  const live = getFeatureLiveStats(f);
  if (live) return live.hit_count;
  if (isFeatureStatsPending(f)) return null;
  return f.hit_count;
}

function getFeatureBadRate(f) {
  const live = getFeatureLiveStats(f);
  if (live) return live.bad_rate;
  if (isFeatureStatsPending(f)) return null;
  return f.max_bad_rate;
}

function sortedClusters(clusters) {
  const key = featureSortMode === "hit_count" ? "hit_count" : "max_bad_rate";
  return clusters.map((g) => ({
    ...g,
    features: [...g.features].sort((a, b) => {
      if (key === "hit_count") {
        const av = getFeatureHitCount(a) ?? a.hit_count;
        const bv = getFeatureHitCount(b) ?? b.hit_count;
        return bv - av;
      }
      const av = getFeatureBadRate(a) ?? a.max_bad_rate;
      const bv = getFeatureBadRate(b) ?? b.max_bad_rate;
      return bv - av;
    }),
  }));
}

function getPreviewRules(forceFeature) {
  if (forceFeature === null) return [];

  const byFeature = new Map();
  const putRule = (f) => {
    if (!f) return;
    const r = getFeatureRule(f);
    const rule = {
      feature: f,
      operator: r.operator,
      threshold: Number(r.threshold) || 0,
    };
    if (r.operator === "in") {
      rule.values = [...(r.values || [])];
      if (!rule.values.length) return;
    }
    byFeature.set(f, rule);
  };

  for (const f of selectedFeatures) putRule(f);

  if (typeof forceFeature === "string") {
    putRule(forceFeature);
  } else if (selectedFeatures.size === 0 && activeFeature) {
    putRule(activeFeature);
  }

  return [...byFeature.values()];
}

function renderImpactCard(data, rules) {
  const b = data.baseline;
  const a = data.after;
  const r = data.rejected;
  const scope = selectedFeatures.size > 0
    ? (rules.length > selectedFeatures.size
      ? `已勾选 ${selectedFeatures.size} 个 + 当前预览 1 个规则（并集拒绝）`
      : `已勾选 ${selectedFeatures.size} 个规则（并集拒绝）`)
    : `当前预览：${rules[0]?.feature || ""}`;

  const perRuleHtml = (data.per_rule || []).map((pr) => (
    `<li><strong>${pr.rule_display}</strong> → 拒绝 <strong>${pr.hit_count}</strong> 人 · 坏率 <strong>${pct(pr.bad_rate || 0)}</strong></li>`
  )).join("");

  return `
    <p class="impact-scope">${scope} · Train 全量</p>
    <div class="impact-section">
      <h4>拒绝前（Train 全量）</h4>
      <div class="impact-grid">
        <div class="impact-stat"><span class="label">样本量</span><span class="val">${b.count.toLocaleString()}</span></div>
        <div class="impact-stat"><span class="label">逾期率</span><span class="val">${pct(b.bad_rate)}</span></div>
        <div class="impact-stat"><span class="label">金额逾期率</span><span class="val">${pct(b.money_bad_rate)}</span></div>
        <div class="impact-stat"><span class="label">拒绝量</span><span class="val">0</span></div>
      </div>
    </div>
    <div class="impact-section">
      <h4>拒绝后（剩余样本）</h4>
      <div class="impact-grid">
        <div class="impact-stat"><span class="label">样本量</span><span class="val good">${a.count.toLocaleString()}</span></div>
        <div class="impact-stat"><span class="label">逾期率</span><span class="val ${a.bad_rate < b.bad_rate ? "good" : "warn"}">${pct(a.bad_rate)}</span></div>
        <div class="impact-stat"><span class="label">金额逾期率</span><span class="val">${pct(a.money_bad_rate)}</span></div>
        <div class="impact-stat"><span class="label">拒绝量</span><span class="val warn">${r.count.toLocaleString()}</span></div>
      </div>
    </div>
    <div class="impact-section">
      <h4>被拒绝人群</h4>
      <div class="impact-grid">
        <div class="impact-stat"><span class="label">人数</span><span class="val warn">${r.count.toLocaleString()}</span></div>
        <div class="impact-stat"><span class="label">逾期率</span><span class="val">${pct(r.bad_rate)}</span></div>
        <div class="impact-stat" style="grid-column:1/-1"><span class="label">金额逾期率</span><span class="val">${pct(r.money_bad_rate)}</span></div>
      </div>
      ${perRuleHtml ? `<ul class="impact-rules-list">${perRuleHtml}</ul>` : ""}
    </div>
  `;
}

function renderImpactBaselineOnly(data) {
  const b = data?.baseline;
  if (!b) {
    return `<p class="sub">勾选或点击特征后，此处实时展示 Train 拒绝前后指标。</p>`;
  }
  return `
    <p class="impact-scope">Train 全量基准（未应用拒绝规则）</p>
    <div class="impact-grid">
      <div class="impact-stat"><span class="label">样本量</span><span class="val">${b.count.toLocaleString()}</span></div>
      <div class="impact-stat"><span class="label">逾期率</span><span class="val">${pct(b.bad_rate)}</span></div>
      <div class="impact-stat"><span class="label">金额逾期率</span><span class="val">${pct(b.money_bad_rate)}</span></div>
      <div class="impact-stat"><span class="label">拒绝量</span><span class="val">0</span></div>
    </div>
  `;
}

async function refreshRejectPreview(forceFeature) {
  const card = $("#reject-impact-card");
  if (!card || !getStoredJobId()) return;

  rejectPreviewAbort?.abort();
  rejectPreviewAbort = new AbortController();
  const { signal } = rejectPreviewAbort;

  const rules = getPreviewRules(forceFeature);
  const rulesSnapshot = JSON.stringify(rules);

  try {
    const data = await api(`/api/binning/${getStoredJobId()}/reject-preview`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ rules }),
      signal,
    });

    if (JSON.stringify(getPreviewRules(forceFeature)) !== rulesSnapshot) return;

    for (const pr of data.per_rule || []) {
      const rk = ruleKeyFromParts(pr.feature, pr.operator, pr.threshold, pr.values);
      liveFeatureStats.set(pr.feature, {
        ruleKey: rk,
        hit_count: pr.hit_count,
        bad_rate: resolveRuleBadRate(pr),
        money_bad_rate: pr.money_bad_rate,
      });
    }
    const previewFeatures = new Set(rules.map((r) => r.feature));
    for (const key of [...liveFeatureStats.keys()]) {
      if (!previewFeatures.has(key)) {
        liveFeatureStats.delete(key);
      }
    }
    updateFeatureLiveDisplays();
    updateDetailLiveSubtitle();
    card.innerHTML = rules.length
      ? renderImpactCard(data, rules)
      : renderImpactBaselineOnly(data);
  } catch (err) {
    if (err.name === "AbortError") return;
    card.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function scheduleRejectPreview(forceFeature) {
  clearTimeout(rejectPreviewTimer);
  rejectPreviewTimer = setTimeout(() => refreshRejectPreview(forceFeature), 280);
}

function updateDetailLiveSubtitle() {
  if (!activeFeature) return;
  const meta = findFeatureMeta(activeFeature);
  if (!meta) return;
  const rule = getFeatureRule(activeFeature);
  const live = getFeatureLiveStats(meta);
  if (!rule.userEdited && !live) return;
  const hitText = live ? `${live.hit_count} 人` : "…";
  const rateText = live ? pct(live.bad_rate) : "…";
  const base = meta.reason?.replace(/命中\s*[\d,]+(?:\.\d+)?\s*人/g, "").replace(/最高箱坏率\s*[\d.]+%/g, "").trim() || meta.reason || "";
  $("#detail-subtitle").textContent = `${meta.chinese_name} · ${base} · 当前规则命中 ${hitText}，坏率 ${rateText}`;
}

function updateFeatureLiveDisplays() {
  $$(".feature-row").forEach((row) => {
    const feat = row.dataset.feature;
    const hitEl = row.querySelector(".hit");
    const rateEl = row.querySelector(".rate");
    const meta = reviewData?.clusters
      ?.flatMap((g) => g.features)
      .find((f) => f.feature === feat);
    if (!meta) return;
    const live = getFeatureLiveStats(meta);
    const pending = isFeatureStatsPending(meta);
    if (hitEl) {
      hitEl.textContent = formatFeatureHitCount(meta);
    }
    if (rateEl) {
      const isCat = isCategoricalFeature(meta);
      rateEl.textContent = formatFeatureBadRate(meta);
      rateEl.title = live
        ? isCat
          ? `候选最高类别坏率 ${pct(meta.max_bad_rate)}；当前规则命中坏率 ${pct(live.bad_rate)}`
          : `建议拒绝箱合并坏率 ${pct(meta.max_bad_rate)}；当前规则命中坏率 ${pct(live.bad_rate)}`
        : pending
          ? "正在按当前阈值重算命中人数与坏率"
          : isCat
            ? `超阈值类别中最高坏率 ${pct(meta.max_bad_rate)}`
            : `建议拒绝箱合并坏率 ${pct(meta.max_bad_rate)}`;
    }
  });
  updateDetailLiveSubtitle();
}

function onRuleInputChange(feat) {
  const row = document.querySelector(`.feature-rule-line[data-feature="${CSS.escape(feat)}"]`);
  if (!row) return;
  const op = row.querySelector(".rule-op")?.value || ">";
  const th = parseFloat(row.querySelector(".rule-threshold")?.value) || 0;
  featureRules.set(feat, { operator: op, threshold: th, userEdited: true });
  liveFeatureStats.delete(feat);
  saveFeatureState();
  markSerialStale();
  updateFeatureLiveDisplays();
  if (activeFeature === feat) {
    const r = getFeatureRule(feat);
    $("#detail-title").textContent = formatRuleDisplay(feat, r.operator, r.threshold);
  }
  scheduleRejectPreview(feat);
}

function getReviewThreshold() {
  return parseFloat($("#review-threshold")?.value) || 0.6;
}

function initFeatureRule(f) {
  const cur = featureRules.get(f.feature);
  if (cur?.userEdited) return;
  if (f.value_type === "categorical" || f.rule_operator === "in") {
    const keepValues = cur?.values?.length ? cur.values : (f.rule_values || []);
    featureRules.set(f.feature, {
      operator: "in",
      threshold: 0,
      values: [...keepValues],
      valueType: "categorical",
    });
    return;
  }
  featureRules.set(f.feature, {
    operator: f.rule_operator || ">",
    threshold: f.rule_threshold ?? 0,
    valueType: "numeric",
  });
}

function formatRuleDisplay(feature, operator, threshold, values) {
  if (operator === "in") {
    const vals = values || [];
    if (!vals.length) return `${feature} in (请勾选类别)`;
    const shown = vals.slice(0, 6).join(", ");
    const suffix = vals.length > 6 ? `, …共${vals.length}类` : "";
    return `${feature} in (${shown}${suffix})`;
  }
  const t = Number.isInteger(threshold) ? String(threshold) : String(threshold);
  return `${feature}${operator}${t}`;
}

function getFeatureRule(feature) {
  return featureRules.get(feature) || { operator: ">", threshold: 0, values: [] };
}

function renderFeatureRuleLine(f) {
  initFeatureRule(f);
  const rule = getFeatureRule(f.feature);
  if (isCategoricalFeature(f)) {
    return `
      <div class="feature-rule-line" data-feature="${f.feature}">
        <span class="rule-base rule-categorical">${formatRuleDisplay(f.feature, "in", 0, rule.values)}</span>
      </div>
    `;
  }
  const ops = ["<", "<=", ">", ">="];
  return `
    <div class="feature-rule-line" data-feature="${f.feature}">
      <span class="rule-base">${f.feature}</span>
      <select class="rule-op" data-feature="${f.feature}" aria-label="比较符">
        ${ops.map((o) => `<option value="${o}" ${rule.operator === o ? "selected" : ""}>${o}</option>`).join("")}
      </select>
      <input type="number" class="rule-threshold" data-feature="${f.feature}"
        value="${rule.threshold}" step="any" aria-label="拒绝阈值" />
    </div>
  `;
}

function refreshFeatureRuleLine(feature) {
  const meta = findFeatureMetaByName(feature);
  if (!meta) return;
  const row = document.querySelector(`.feature-row[data-feature="${CSS.escape(feature)}"]`);
  const line = row?.querySelector(".feature-rule-line");
  if (!line) return;
  const tmp = document.createElement("div");
  tmp.innerHTML = renderFeatureRuleLine(meta);
  line.replaceWith(tmp.firstElementChild);
}

function onCategorySelectionChange(feature, bin, checked) {
  const cur = getFeatureRule(feature);
  const vals = new Set(cur.values || []);
  if (checked) vals.add(bin);
  else vals.delete(bin);
  featureRules.set(feature, {
    ...cur,
    operator: "in",
    threshold: 0,
    values: [...vals],
    valueType: "categorical",
    userEdited: true,
  });
  liveFeatureStats.delete(feature);
  saveFeatureState();
  markSerialStale();
  refreshFeatureRuleLine(feature);
  if (activeFeature === feature) {
    const r = getFeatureRule(feature);
    $("#detail-title").textContent = formatRuleDisplay(feature, "in", 0, r.values);
  }
  updateFeatureLiveDisplays();
  scheduleRejectPreview(feature);
}

function effectClass(label) {
  if (label === "好") return "effect-good";
  if (label === "U型人工判断") return "effect-u";
  if (label === "一般") return "effect-fair";
  return "";
}

function getAllCandidateFeatures() {
  if (!reviewData?.clusters) return [];
  return reviewData.clusters.flatMap((g) => g.features.map((f) => f.feature));
}

function setFeatureSelected(feature, checked) {
  if (checked) {
    selectedFeatures.add(feature);
    const meta = findFeatureMeta(feature);
    if (meta?.value_type === "categorical" && !getFeatureRule(feature).userEdited) {
      const bins = resolveHighBadBins(feature);
      if (setCategoricalAllSelected(feature, bins)) {
        refreshFeatureRuleLine(feature);
        syncCategoricalDetailCheckboxes(feature);
      }
    }
  } else {
    selectedFeatures.delete(feature);
    featureRules.delete(feature);
    liveFeatureStats.delete(feature);
  }
  saveFeatureState();
  markSerialStale();
}

function updateSelectAllCheckbox() {
  const cb = $("#select-all-features");
  if (!cb) return;
  const all = getAllCandidateFeatures();
  if (!all.length) {
    cb.checked = false;
    cb.indeterminate = false;
    cb.disabled = true;
    return;
  }
  cb.disabled = false;
  const n = selectedFeatures.size;
  cb.checked = n > 0 && n === all.length;
  cb.indeterminate = n > 0 && n < all.length;
}

function selectAllFeatures(checked) {
  if (checked) {
    for (const feat of getAllCandidateFeatures()) {
      setFeatureSelected(feat, true);
    }
  } else {
    selectedFeatures.clear();
    featureRules.clear();
    liveFeatureStats.clear();
  }
  syncFeatureCheckboxes();
  updateSelectedCount();
  updateFeatureLiveDisplays();
  updateSelectAllCheckbox();
  scheduleRejectPreview(checked ? undefined : null);
}

function renderFeatureClusters(clusters) {
  const el = $("#feature-clusters");
  if (!clusters?.length) {
    el.innerHTML = `<p class="sub">当前阈值下无候选变量，可调低阈值或减少最少命中人数后刷新。</p>`;
    return;
  }

  const sorted = sortedClusters(clusters);
  el.innerHTML = sorted.map((g) => `
    <div class="feature-cluster" data-group="${g.group_id}">
      <div class="feature-cluster-title">${g.group_name}（${g.features.length}）</div>
      ${g.features.map((f) => `
        <div class="feature-row${activeFeature === f.feature ? " active" : ""}" data-feature="${f.feature}">
          <input type="checkbox" data-feature="${f.feature}" ${selectedFeatures.has(f.feature) ? "checked" : ""} />
          <div class="feature-row-main">
            ${renderFeatureRuleLine(f)}
            ${f.manual_include ? '<span class="manual-badge">手动</span>' : ""}
            <span class="cn">${f.chinese_name}</span>
            ${f.value_type === "categorical" ? '<span class="rule-warn">类别变量：请在明细中勾选要拒绝的类别</span>' : ""}
            ${f.value_type !== "categorical" && !f.rule_meets_min_hit ? '<span class="rule-warn">建议规则样本偏少，请结合明细调整</span>' : ""}
          </div>
          <div class="feature-row-meta">
            <span class="rate">${formatFeatureBadRate(f)}</span>
            <span class="${effectClass(f.effect_label)}">${f.effect_label}</span>
            <span class="hit">${formatFeatureHitCount(f)}</span>
          </div>
        </div>
      `).join("")}
    </div>
  `).join("");

  el.querySelectorAll(".rule-op, .rule-threshold").forEach((input) => {
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("change", (e) => {
      onRuleInputChange(e.target.dataset.feature);
    });
    if (input.classList.contains("rule-threshold")) {
      input.addEventListener("input", (e) => {
        onRuleInputChange(e.target.dataset.feature);
      });
    }
  });

  el.querySelectorAll(".feature-row").forEach((row) => {
    const feat = row.dataset.feature;
    row.querySelector('input[type="checkbox"]').addEventListener("click", (e) => {
      e.stopPropagation();
      setFeatureSelected(feat, e.target.checked);
      updateSelectedCount();
      updateFeatureLiveDisplays();
      updateSelectAllCheckbox();
      scheduleRejectPreview(selectedFeatures.size ? undefined : (activeFeature || null));
    });
    row.addEventListener("click", (e) => {
      if (e.target.type === "checkbox") return;
      openFeatureDetail(feat);
    });
  });

  if (activeFeature) scheduleRejectPreview(activeFeature);
  updateSelectAllCheckbox();
}

function syncFeatureCheckboxes() {
  $$("#feature-clusters input[type=checkbox][data-feature]").forEach((cb) => {
    cb.checked = selectedFeatures.has(cb.dataset.feature);
  });
  updateSelectAllCheckbox();
}

function updateSelectedCount() {
  const n = $("#selected-count");
  const cnt = selectedFeatures.size;
  if (n) n.textContent = String(cnt);
  const btn6 = $("#to-step-6");
  if (btn6) btn6.disabled = cnt === 0;
}

async function openFeatureDetail(feature, skipSplitAnim = false) {
  activeFeature = feature;

  $$(".feature-row").forEach((r) => {
    const isActive = r.dataset.feature === feature;
    r.classList.remove("active");
    if (isActive) {
      requestAnimationFrame(() => r.classList.add("active"));
    }
  });

  if (!skipSplitAnim) {
    $("#pick-stage")?.classList.add("is-split");
    requestAnimationFrame(() => {
      const row = document.querySelector(`.feature-row[data-feature="${CSS.escape(feature)}"]`);
      row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }
  $("#feature-detail-panel")?.setAttribute("aria-hidden", "false");

  const threshold = getReviewThreshold();
  const cached = lastFeatureDetail.get(feature);
  if (cached?.bins?.train?.length) {
    applyFeatureDetail(feature, cached, threshold);
    scheduleRejectPreview(feature);
    return;
  }

  $("#detail-title").textContent = `${feature} · 加载中…`;
  $("#detail-subtitle").textContent = "";
  $("#detail-bins").innerHTML = `<p class="sub">加载分箱明细...</p>`;
  $("#detail-stability").innerHTML = "";

  $$(".detail-tab").forEach((b) => b.classList.remove("active"));
  $$(".detail-tab")[0]?.classList.add("active");
  $("#detail-bins").classList.remove("hidden");
  $("#detail-stability").classList.add("hidden");

  const body = $(".detail-body");
  if (body && !skipSplitAnim) {
    body.scrollTop = 0;
    body.style.animation = "none";
    void body.offsetHeight;
    body.style.animation = "";
  }

  scheduleRejectPreview(feature);

  const query = new URLSearchParams({
    feature,
    bad_rate_threshold: String(threshold),
    include_stability: "false",
  });

  try {
    const data = await api(
      `/api/binning/${getStoredJobId()}/feature-detail?${query}`,
      { headers: headers(false) }
    );
    lastFeatureDetail.set(feature, data);
    applyFeatureDetail(feature, data, threshold);
  } catch (err) {
    $("#detail-bins").innerHTML = `<span class="error">${err.message}</span>`;
  }
}

function applyFeatureDetail(feature, data, threshold) {
  if (data.rule) {
    const cur = getFeatureRule(feature);
    if (!cur.userEdited) {
      if (data.value_type === "categorical") {
        if (!selectedFeatures.has(feature)) {
          featureRules.set(feature, {
            operator: "in",
            threshold: 0,
            values: [...(data.rule.rule_values || [])],
            valueType: "categorical",
          });
        }
      } else {
        featureRules.set(feature, {
          operator: data.rule.rule_operator || ">",
          threshold: data.rule.rule_threshold ?? 0,
          valueType: "numeric",
        });
        syncRuleInputs(feature);
      }
    }
  }
  const meta = findFeatureMeta(feature);
  const curRule = getFeatureRule(feature);
  const subHint = data.value_type === "categorical"
    ? (selectedFeatures.has(feature)
      ? (curRule.userEdited
        ? "已手动调整拒绝类别，切换其他变量后再回来仍会保留"
        : "已勾选该变量：下方超阈值类别默认全选，可取消不需要的")
      : "勾选左侧变量后，下方超阈值类别将默认全选")
    : (() => {
        const bins = data.rule?.rule_source_bins;
        if (bins?.length > 1) {
          const side = meta?.rule_type === "head" ? "头" : "尾";
          const br = data.rule?.rule_source_bad_rate ?? meta?.max_bad_rate ?? 0;
          return `建议拒绝连续${side}箱 ${bins.join("、")}（合并坏率 ${pct(br)}）`;
        }
        return `建议箱 ${data.rule?.rule_source_bin || ""}`;
      })();
  $("#detail-subtitle").textContent = `${data.chinese_name} · ${meta?.reason || ""} · ${subHint}`;
  renderBinTables(data, threshold);
  if (data.value_type === "categorical" && selectedFeatures.has(feature)) {
    const cur = getFeatureRule(feature);
    if (!cur.userEdited && !(cur.values?.length)) {
      const bins = highBadBinsFromDetail(data);
      if (setCategoricalAllSelected(feature, bins)) {
        refreshFeatureRuleLine(feature);
      }
    }
    syncCategoricalDetailCheckboxes(feature);
  }
  const r = getFeatureRule(feature);
  $("#detail-title").textContent = data.value_type === "categorical"
    ? formatRuleDisplay(feature, "in", 0, r.values)
    : (data.rule?.rule_display || formatRuleDisplay(feature, r.operator, r.threshold));
  renderStabilityTables(
    data.stability,
    data.stability_note || "点击「月度稳定性」标签按需加载",
    data.stability_check
  );
}

async function loadFeatureStability(feature) {
  const cached = lastFeatureDetail.get(feature);
  if (cached?.stability?.train?.bins?.length && !cached?.stability_deferred) {
    renderStabilityTables(cached.stability, cached.stability_note, cached.stability_check);
    return;
  }

  const el = $("#detail-stability");
  if (el) el.innerHTML = `<p class="sub">正在加载月度稳定性…</p>`;

  const threshold = getReviewThreshold();
  const query = new URLSearchParams({
    feature,
    bad_rate_threshold: String(threshold),
    include_stability: "true",
  });

  try {
    const data = await api(
      `/api/binning/${getStoredJobId()}/feature-detail?${query}`,
      { headers: headers(false) }
    );
    lastFeatureDetail.set(feature, { ...(cached || {}), ...data });
    renderStabilityTables(data.stability, data.stability_note, data.stability_check);
  } catch (err) {
    if (el) el.innerHTML = `<span class="error">${err.message}</span>`;
  }
}

function syncRuleInputs(feature) {
  const r = getFeatureRule(feature);
  const row = document.querySelector(`.feature-rule-line[data-feature="${CSS.escape(feature)}"]`);
  if (!row) return;
  const op = row.querySelector(".rule-op");
  const th = row.querySelector(".rule-threshold");
  if (op) op.value = r.operator;
  if (th) th.value = r.threshold;
}

function findFeatureMeta(feature) {
  if (!reviewData?.clusters) return null;
  for (const g of reviewData.clusters) {
    const f = g.features.find((x) => x.feature === feature);
    if (f) return f;
  }
  return null;
}

function highBadBinsFromMeta(feature) {
  const meta = findFeatureMeta(feature);
  return (meta?.high_bad_categories || []).map((c) => String(c.bin));
}

function highBadBinsFromDetail(detail) {
  return (detail?.high_bad_categories || []).map((c) => String(c.bin));
}

function highBadBinsFromDetailPanel(feature) {
  return [...document.querySelectorAll(`.cat-bin-select[data-feature="${CSS.escape(feature)}"]`)]
    .map((cb) => cb.dataset.bin)
    .filter(Boolean);
}

function resolveHighBadBins(feature) {
  const fromMeta = highBadBinsFromMeta(feature);
  if (fromMeta.length) return fromMeta;
  const cached = lastFeatureDetail.get(feature);
  if (cached) {
    const fromCache = highBadBinsFromDetail(cached);
    if (fromCache.length) return fromCache;
  }
  return highBadBinsFromDetailPanel(feature);
}

/** 勾选类别变量时，默认选中全部超阈值类别（首次，非手动调整后）。 */
function setCategoricalAllSelected(feature, bins) {
  if (!bins?.length) return false;
  featureRules.set(feature, {
    operator: "in",
    threshold: 0,
    values: [...bins],
    valueType: "categorical",
    userEdited: false,
  });
  return true;
}

function syncCategoricalDetailCheckboxes(feature) {
  if (activeFeature !== feature) return;
  const r = getFeatureRule(feature);
  const selected = new Set(r.values || []);
  $("#detail-title").textContent = formatRuleDisplay(feature, "in", 0, r.values);
  document.querySelectorAll(`.cat-bin-select[data-feature="${CSS.escape(feature)}"]`).forEach((cb) => {
    const bin = cb.dataset.bin;
    cb.checked = selected.has(bin) || [...selected].some((v) => String(v) === String(bin));
  });
  const hint = $("#detail-bins")?.querySelector(".cat-select-hint");
  const n = selected.size;
  if (hint) {
    hint.textContent = n
      ? `已选 ${n} 个类别（默认全选，可取消不需要的）`
      : "尚未勾选类别";
  }
}

function renderBinTables(detail, badRateThreshold) {
  const el = $("#detail-bins");
  if (detail.value_type === "categorical") {
    renderCategoricalBinTables(el, detail, badRateThreshold);
    return;
  }

  const bins = detail.bins || {};
  const trainRows = bins.train || [];
  const testRows = bins.test || [];
  const binCount = trainRows.length;
  const sameBins = binCount > 0 && testRows.length === binCount
    && trainRows.every((r, i) => r.bin === testRows[i]?.bin);

  const testObsSum = detail.test_obs_sum ?? testRows.reduce((s, r) => s + (Number(r.obs) || 0), 0);
  const testRowCount = detail.test_row_count ?? null;
  const countHint = testRowCount != null
    ? `Test 样本 ${testRowCount} 人，分箱合计 ${testObsSum} 人${testRowCount === testObsSum ? "" : "（不一致请重新分箱或联系管理员）"}。`
    : `Test 分箱合计 ${testObsSum} 人。`;

  el.innerHTML = `
    <p class="sub bin-hint">共 ${binCount} 个分箱（含缺失值箱）。${countHint}Test 与 Train 使用<strong>相同分箱边界</strong>；缺失/特殊值箱排在最前。</p>
    ${!sameBins && testRows.length ? '<p class="warn">Test 分箱与 Train 未完全对齐，请刷新或重新分箱。</p>' : ""}
    <div class="stack-table-wrap">
      <div>
        <h4>Train 分箱明细（${binCount} 箱）</h4>
        ${renderBinTable(trainRows, badRateThreshold)}
      </div>
      <div>
        <h4>Test 分箱明细（${trainRows.length} 箱，与 Train 同序）</h4>
        ${renderBinTable(testRows, badRateThreshold, trainRows)}
      </div>
    </div>
  `;
}

function renderCategoricalBinTables(el, detail, badRateThreshold) {
  const feature = detail.feature;
  const categories = detail.high_bad_categories || [];
  const rule = getFeatureRule(feature);
  const selected = new Set(rule.values || []);
  const bins = detail.bins || {};
  const testByBin = Object.fromEntries((bins.test || []).map((r) => [r.bin, r]));

  if (!categories.length) {
    el.innerHTML = `<p class="sub">当前阈值下没有坏率 &gt; ${pct(badRateThreshold)} 的类别。</p>`;
    return;
  }

  el.innerHTML = `
    <p class="sub bin-hint">仅展示坏率 &gt; ${pct(badRateThreshold)} 的类别（样本 ≥ 10）。请勾选要纳入拒绝规则的类别；未勾选的不拒绝。</p>
    <table class="data-table bin-detail-table cat-select-table">
      <thead>
        <tr>
          <th>拒绝</th>
          <th>类别</th>
          <th>Train #Obs</th>
          <th>Train 坏率</th>
          <th>Test #Obs</th>
          <th>Test 坏率</th>
        </tr>
      </thead>
      <tbody>
        ${categories.map((c) => {
          const testRow = testByBin[c.bin] || {};
          const missTag = c.is_missing ? ' <span class="cat-miss-tag">缺失</span>' : "";
          return `
          <tr class="bin-high-bad">
            <td>
              <input type="checkbox" class="cat-bin-select" data-feature="${feature}" data-bin="${escapeAttr(c.bin)}"
                ${selected.has(c.bin) || [...selected].some((v) => String(v) === String(c.bin)) ? "checked" : ""} aria-label="拒绝类别 ${escapeAttr(c.bin)}" />
            </td>
            <td>${escapeHtml(c.bin)}${missTag}</td>
            <td>${c.obs}</td>
            <td class="bar-col">${renderRateBar(c.bad_rate || 0, "bad")}</td>
            <td>${testRow.obs ?? "—"}</td>
            <td class="bar-col">${testRow.obs ? renderRateBar(testRow.bad_rate || 0, "bad") : "—"}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>
    <p class="sub cat-select-hint">${selected.size
      ? `已选 ${selected.size} 个类别（默认全选，可取消不需要的）`
      : "勾选左侧变量后，此处将默认全选超阈值类别"}</p>
  `;

  el.querySelectorAll(".cat-bin-select").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      e.stopPropagation();
      onCategorySelectionChange(cb.dataset.feature, cb.dataset.bin, cb.checked);
      const hint = el.querySelector(".cat-select-hint");
      const n = getFeatureRule(feature).values?.length || 0;
      if (hint) {
        hint.textContent = n
          ? `已选 ${n} 个类别`
          : "尚未勾选类别，勾选左侧变量后请在明细中选择要拒绝的类别";
      }
    });
  });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

function renderBinTable(rows, badRateThreshold, binTemplate) {
  const ordered = binTemplate?.length
    ? binTemplate.map((t) => rows?.find((r) => r.bin === t.bin) || {
        bin: t.bin, obs: 0, bad: 0, bad_rate: 0, lift: null,
        is_rule_bin: t.is_rule_bin, is_high_bad: false,
      })
    : (rows || []);
  if (!ordered.length) return `<p class="sub">无数据</p>`;
  const totalObs = ordered.reduce((s, r) => s + (Number(r.obs) || 0), 0);
  const totalBad = ordered.reduce((s, r) => s + (Number(r.bad) || 0), 0);
  const totalBr = totalObs ? totalBad / totalObs : 0;
  return `
    <table class="data-table bin-detail-table">
      <thead>
        <tr>
          <th>箱</th>
          <th>#Obs</th>
          <th>#Bad</th>
          <th>坏率</th>
          <th>Lift</th>
        </tr>
      </thead>
      <tbody>
        ${ordered.map((r) => {
          const cls = [
            r.is_rule_bin ? "bin-rule" : "",
            r.is_high_bad ? "bin-high-bad" : "",
          ].filter(Boolean).join(" ");
          return `
          <tr class="${cls}">
            <td>${r.bin}</td>
            <td>${r.obs}</td>
            <td>${r.bad}</td>
            <td class="bar-col">${renderRateBar(r.bad_rate || 0, "bad")}</td>
            <td>${r.lift != null ? r.lift.toFixed(2) : "—"}</td>
          </tr>
        `;
        }).join("")}
        <tr class="bin-total-row">
          <td><strong>合计</strong></td>
          <td><strong>${totalObs}</strong></td>
          <td><strong>${totalBad}</strong></td>
          <td class="bar-col">${renderRateBar(totalBr, "bad")}</td>
          <td>—</td>
        </tr>
      </tbody>
    </table>
  `;
}

function renderRateBar(rate, kind) {
  const w = Math.min(100, Math.max(0, (rate || 0) * 100));
  const cls = kind === "money" ? "rate-bar-money" : "rate-bar-bad";
  return `<span class="rate-bar-wrap bar-col"><span class="rate-bar ${cls}" style="width:${w}%"></span><span class="rate-bar-text">${pct(rate || 0)}</span></span>`;
}

function renderStabilityCheckHint(check) {
  if (!check) return "";
  const parts = [];
  if (check.train) {
    const t = check.train;
    const ok = t.match;
    parts.push(
      `Train 合计 ${t.stability_obs} 人${ok ? "，与分箱一致" : `，分箱 ${t.binning_obs} 人（不一致请检查）`}`
    );
  }
  if (check.test?.binning_obs) {
    const t = check.test;
    const ok = t.match;
    parts.push(
      `Test 合计 ${t.stability_obs} 人${ok ? "，与分箱一致" : `，分箱 ${t.binning_obs} 人（不一致请检查）`}`
    );
  }
  if (!parts.length) return "";
  return `<p class="sub stability-check">${parts.join(" · ")}</p>`;
}

function renderStabilityTables(stability, note, check) {
  const el = $("#detail-stability");
  const noteHtml = note ? `<p class="warn">${note}</p>` : "";
  el.innerHTML = `
    ${noteHtml}
    ${renderStabilityCheckHint(check)}
    <p class="sub bin-hint">坏率（蓝）与金额逾期率（绿）列带条形图，便于跨月对比。表格底部「合计」行与分箱总人数应对齐。</p>
    <div class="stack-table-wrap">
      <div>
        <h4>Train 月度稳定性</h4>
        <div class="stability-scroll">${renderStabilityTable(stability?.train)}</div>
      </div>
      <div>
        <h4>Test 月度稳定性</h4>
        <div class="stability-scroll">${renderStabilityTable(stability?.test)}</div>
      </div>
    </div>
  `;
}

function renderStabilityTable(data) {
  if (!data?.bins?.length) return `<p class="sub">无月度数据</p>`;
  const months = data.months || [];
  const cols = data.columns || ["总人数", "逾期数", "坏率", "金额逾期率"];

  let header = `<tr><th rowspan="2">箱</th>`;
  months.forEach((m) => {
    header += `<th colspan="4" class="month-head">${m}</th>`;
  });
  header += `<th colspan="4" class="month-head">合计</th></tr><tr>`;
  for (let i = 0; i <= months.length; i++) {
    cols.forEach((c) => { header += `<th class="sub-head">${c}</th>`; });
  }
  header += `</tr>`;

  const renderRow = (b, isSummary) => {
    const trCls = isSummary ? ' class="stability-sum-row"' : "";
    let cells = `<td>${b.bin}</td>`;
    months.forEach((m) => {
      const c = b.months?.[m] || {};
      cells += `<td>${c.obs ?? 0}</td><td>${c.bad ?? 0}</td>`;
      cells += `<td class="bar-col">${renderRateBar(c.bad_rate || 0, "bad")}</td>`;
      cells += `<td class="bar-col">${renderRateBar(c.money_bad_rate || 0, "money")}</td>`;
    });
    const t = b.total || {};
    cells += `<td>${t.obs ?? 0}</td><td>${t.bad ?? 0}</td>`;
    cells += `<td class="bar-col">${renderRateBar(t.bad_rate || 0, "bad")}</td>`;
    cells += `<td class="bar-col">${renderRateBar(t.money_bad_rate || 0, "money")}</td>`;
    return `<tr${trCls}>${cells}</tr>`;
  };

  const body = data.bins.map((b) => renderRow(b, false)).join("");
  const summary = data.summary ? renderRow(data.summary, true) : "";

  return `<table class="data-table stability-table"><thead>${header}</thead><tbody>${body}${summary}</tbody></table>`;
}

// ——— Step 6: Serial analysis ———

function syncRuleFromDom(feat) {
  const row = document.querySelector(`.feature-rule-line[data-feature="${CSS.escape(feat)}"]`);
  if (!row) return;
  const opEl = row.querySelector(".rule-op");
  const thEl = row.querySelector(".rule-threshold");
  if (!opEl && !thEl) return;
  const cur = getFeatureRule(feat);
  const op = opEl?.value || cur.operator || ">";
  const th = thEl ? parseFloat(thEl.value) : Number(cur.threshold) || 0;
  featureRules.set(feat, {
    ...cur,
    operator: op,
    threshold: Number.isFinite(th) ? th : 0,
    userEdited: true,
  });
}

function buildSelectedRules() {
  syncRulesFromReview();
  for (const feat of selectedFeatures) syncRuleFromDom(feat);
  return [...selectedFeatures].map((feat) => {
    const r = getFeatureRule(feat);
    const rule = {
      feature: feat,
      operator: r.operator,
      threshold: Number(r.threshold) || 0,
    };
    if (r.operator === "in") {
      rule.values = [...(r.values || [])];
      if (!rule.values.length) return null;
    }
    return rule;
  }).filter(Boolean);
}

function syncRulesFromReview() {
  if (!reviewData?.clusters) return;
  for (const feat of selectedFeatures) {
    const meta = findFeatureMetaByName(feat);
    const cur = featureRules.get(feat);
    if (meta && !cur?.userEdited) {
      if (meta.value_type === "categorical" || meta.rule_operator === "in") {
        const keepValues = cur?.values?.length ? cur.values : (meta.rule_values || []);
        featureRules.set(feat, {
          operator: "in",
          threshold: 0,
          values: [...keepValues],
          valueType: "categorical",
        });
      } else {
        featureRules.set(feat, {
          operator: cur?.operator || meta.rule_operator || ">",
          threshold: cur?.threshold ?? meta.rule_threshold ?? 0,
          valueType: "numeric",
        });
      }
    }
  }
}

function buildSerialRequestBody() {
  return {
    rules: buildSelectedRules(),
    sort_mode: $("#serial-sort")?.value || "selection",
    lift_min: parseFloat($("#serial-lift-min")?.value) || 1.1,
    min_hit: parseInt($("#serial-min-hit")?.value, 10) || 5,
  };
}

$("#to-step-6")?.addEventListener("click", async () => {
  if (!selectedFeatures.size) {
    alert("请至少勾选一个特征");
    return;
  }
  const rules = buildSelectedRules();
  const missingCategorical = [...selectedFeatures].filter((feat) => {
    const meta = findFeatureMetaByName(feat);
    if (!meta || meta.value_type !== "categorical") return false;
    const r = getFeatureRule(feat);
    return r.operator === "in" && !(r.values?.length);
  });
  if (missingCategorical.length) {
    alert(`以下类别变量尚未选择要拒绝的类别：${missingCategorical.join("、")}`);
    return;
  }
  if (!rules.length) {
    alert("请至少勾选一个有效规则");
    return;
  }
  setStep(6);
  await loadSerialAnalysis();
});

$("#back-to-5")?.addEventListener("click", () => setStep(5));

$("#run-serial")?.addEventListener("click", () => loadSerialAnalysis());

$("#serial-sort")?.addEventListener("change", () => {
  if (serialAnalysisData) loadSerialAnalysis();
});

async function loadSerialAnalysis() {
  const errEl = $("#serial-error");
  const summaryEl = $("#serial-summary");
  const rulesEl = $("#serial-rules");
  const metaEl = $("#serial-meta");
  const exportBtn = $("#export-serial");

  errEl.classList.add("hidden");
  summaryEl.innerHTML = `<p class="sub">计算中…</p>`;
  rulesEl.innerHTML = "";
  metaEl.textContent = "";
  exportBtn.disabled = true;
  serialAnalysisData = null;

  const jobId = getStoredJobId();
  if (!jobId) {
    errEl.textContent = "未找到分箱任务";
    errEl.classList.remove("hidden");
    summaryEl.innerHTML = "";
    return;
  }

  try {
    const data = await api(`/api/binning/${jobId}/serial-analysis`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(buildSerialRequestBody()),
    });
    serialAnalysisData = data;
    serialAnalysisStale = false;
    renderSerialAnalysis(data);
    exportBtn.disabled = false;
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
    summaryEl.innerHTML = "";
  }
}

function renderSerialAnalysis(data) {
  const sortLabels = {
    selection: "挑选顺序",
    bad_rate: "全量坏率",
    hit_count: "全量命中数",
  };
  $("#serial-meta").textContent =
    `输入 ${data.rule_count_input} 条规则，Train 串联应用 ${data.rule_count_applied} 条（Test/全量同步跟随）`
    + `（排序：${sortLabels[data.sort_mode] || data.sort_mode}，Train 准入：Lift≥${data.lift_min}，最少命中≥${data.min_hit}）`;

  const s = data.summary;
  $("#serial-summary").innerHTML = `
    <h3>串联后汇总</h3>
    <div class="serial-table-wrap">
      <table class="data-table serial-summary-table">
        <thead>
          <tr>
            <th>指标</th>
            <th>Train</th>
            <th>Test</th>
            <th>全量</th>
          </tr>
        </thead>
        <tbody>
          ${renderSerialSummaryRows(s)}
        </tbody>
      </table>
    </div>
  `;

  $("#serial-rules").innerHTML = `
    <h3>规则明细（Train / Test / 全量）</h3>
    <div class="serial-table-wrap wide">
      <table class="data-table serial-detail-table">
        <thead>
          <tr>
            <th rowspan="2">#</th>
            <th rowspan="2">规则</th>
            <th rowspan="2">状态</th>
            <th colspan="5">Train</th>
            <th colspan="5">Test</th>
            <th colspan="5">全量</th>
          </tr>
          <tr>
            ${["Train", "Test", "全量"].map(() => `
              <th>规则命中</th><th>串联命中</th><th>串联坏率</th><th>Lift</th><th>拒绝率</th>
            `).join("")}
          </tr>
        </thead>
        <tbody>
          ${data.rules.map((r) => renderSerialRuleRow(r)).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderSerialSummaryRows(summary) {
  const rows = [
    ["总样本", "total", (v) => v.toLocaleString()],
    ["剩余样本", "final_count", (v) => v.toLocaleString()],
    ["件数逾期率(前)", "orig_bad_rate", pct],
    ["件数逾期率(后)", "final_bad_rate", pct],
    ["金额逾期率(前)", "orig_money_bad_rate", pct],
    ["金额逾期率(后)", "final_money_bad_rate", pct],
    ["通过率", "pass_rate", pct],
    ["逾期人数(前→后)", null, (_, seg) => `${seg.total_bad.toLocaleString()} → ${seg.final_bad.toLocaleString()}`],
  ];
  return rows.map(([label, key, fmt]) => {
    const cells = ["train", "test", "full"].map((k) => {
      const seg = summary[k] || {};
      if (!key) return `<td>${fmt(null, seg)}</td>`;
      const val = seg[key];
      return `<td>${val != null ? fmt(val) : "—"}</td>`;
    }).join("");
    return `<tr><td>${label}</td>${cells}</tr>`;
  }).join("");
}

function renderSerialRuleRow(r) {
  const status = r.status === "dropped"
    ? `<span class="badge warn" title="${r.drop_reason || "未达串联标准"}">丢弃</span>`
    : '<span class="badge ok">应用</span>';
  const segCells = (key) => {
    const seg = r[key] || {};
    const cls = r.status === "dropped" ? " class=\"muted-cell\"" : "";
    return `
      <td${cls}>${seg.rule_hit ?? 0}</td>
      <td${cls}>${seg.serial_hit ?? 0}</td>
      <td${cls}>${pct(seg.serial_bad_rate || 0)}</td>
      <td${cls}>${(seg.lift ?? 0).toFixed(2)}</td>
      <td${cls}>${pct(seg.reject_rate || 0)}</td>
    `;
  };
  const title = r.chinese_name ? `${r.rule_display} · ${r.chinese_name}` : r.rule_display;
  const reason = r.drop_reason ? `<div class="drop-reason">${r.drop_reason}</div>` : "";
  return `
    <tr>
      <td>${r.order}</td>
      <td class="rule-cell">
        <div class="rule-cell-text" title="${title}">${r.rule_display}</div>
        ${reason}
      </td>
      <td>${status}</td>
      ${segCells("train")}
      ${segCells("test")}
      ${segCells("full")}
    </tr>
  `;
}

$("#export-serial")?.addEventListener("click", async () => {
  const jobId = getStoredJobId();
  if (!jobId || !selectedFeatures.size) return;
  try {
    const res = await fetch(`${API}/api/binning/${jobId}/serial-analysis/export`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(buildSerialRequestBody()),
    });
    if (res.status === 401) {
      logout();
      throw new Error("登录已过期");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(data?.detail || res.statusText);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `serial_${jobId.slice(0, 8)}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`导出失败：${err.message}`);
  }
});

updateBinNumVisibility();
$("#split-mode").dispatchEvent(new Event("change"));
