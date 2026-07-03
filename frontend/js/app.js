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
/** IV 路径第 7 步：按坏率+命中筛出的单变量池（与 IV 划格子池独立） */
let serialPickReviewData = null;
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
/** 第 4 步筛选路径：bad_rate（决策树支线）| iv_equal_freq（等频调箱支线） */
let screeningMode = "bad_rate";
const IV_MAX_FEATURES = 6;
/** IV 支线：两两组合格子 */
let ivPairOverview = null;
let ivSelectedPairs = new Set();
/** 精筛页勾选、进入第 7 步串联的组合 */
let ivFineSelectedPairs = new Set();
let ivActivePairKey = "";
/** 初筛页当前展开查看详情的组合 */
let ivCoarseExpandedPair = "";
/** @type {Map<string, string>} pairKey -> overview|monthly */
const ivCoarseDetailTab = new Map();
/** @type {Map<string, object>} pairKey -> grid detail */
const ivPairDetailCache = new Map();
/** @type {Map<string, {edges_a: number[], edges_b: number[]}>} */
const ivPairCustomEdges = new Map();
/** 精筛：pairKey -> Set<"i,j"> 用户勾选的准入格子 */
const ivPairSelectedCells = new Map();
/** 规则/勾选变更后需重新跑串联分析 */
let serialAnalysisStale = false;
/** @type {Map<string, object>} */
const lastFeatureDetail = new Map();

/** 多变量分析状态 */
let multivariateData = null;
/** @type {Set<string>} 选中的组合规则 rule_id */
let selectedCompoundRules = new Set();
let activeCompoundRuleId = "";
let mvSortMode = "bad_rate";
let chainSortMode = "bad_rate";
let chainPreviewAbort = null;
let chainPreviewTimer = null;
/** 单变量池（进入多变量步骤时从 step5 选定特征复制） */
let multivariatePoolFeatures = [];
/** 第 6 步选定的组合变量数 K */
let mvCombK = 3;
let mvTreeDepth = 3;
const MV_DEFAULT_MAX_LEAF_NODES = 4;
let mvMiningInProgress = false;

/** 第 7 步串联挑选：与第 5 步多变量挖树池独立的单变量勾选 */
let serialSelectedFeatures = new Set();
let serialActiveFeature = "";
let serialFeatureSortMode = "bad_rate";
let serialRejectPreviewTimer = null;
let serialRejectPreviewAbort = null;
let activeSerialRuleKey = "";

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
  const clusters = getSerialPickClusters().length && currentStep >= 7 && getScreeningMode() === "iv_equal_freq"
    ? getSerialPickClusters()
    : reviewData?.clusters;
  if (!clusters) {
    return serialPickReviewData?.all_features?.find((x) => x.feature === feature)
      || reviewData?.all_features?.find((x) => x.feature === feature)
      || null;
  }
  for (const g of clusters) {
    const f = g.features.find((x) => x.feature === feature);
    if (f) return f;
  }
  return serialPickReviewData?.all_features?.find((x) => x.feature === feature)
    || reviewData?.all_features?.find((x) => x.feature === feature)
    || null;
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
    loadScreeningModeFromStorage();
    syncScreeningBranchUI();
    if (!selectedFeatures.size) {
      alert("请至少勾选一个特征");
      return;
    }
    if (getScreeningMode() === "iv_equal_freq") {
      if (selectedFeatures.size < 2) {
        alert("IV 等频格子至少需要 2 个变量");
        return;
      }
      if (selectedFeatures.size > IV_MAX_FEATURES) {
        alert(`IV 等频格子最多 ${IV_MAX_FEATURES} 个变量，请减少勾选`);
        return;
      }
    }
    multivariatePoolFeatures = [...selectedFeatures].slice(0, IV_MAX_FEATURES);
  }
  if (n === 8) {
    if (!serialSelectedFeatures.size && !selectedCompoundRules.size) {
      alert("请先在第 7 步「串联挑选」勾选至少一个单变量或组合规则");
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
    loadScreeningModeFromStorage();
    syncScreeningBranchUI();
    syncStep5Labels();
    setStep(5);
    loadFeatureState();
    if (getScreeningMode() === "iv_equal_freq" && selectedFeatures.size > IV_MAX_FEATURES) {
      const kept = [...selectedFeatures].slice(0, IV_MAX_FEATURES);
      selectedFeatures.clear();
      kept.forEach((f) => selectedFeatures.add(f));
      saveFeatureState();
    }
    if (!reviewData?.clusters?.length) {
      await loadFeatureReviewPick();
    } else {
      renderFeatureClusters(reviewData.clusters);
      updateReviewCountBadge();
      updateSelectedCount();
      updateSelectAllCheckbox();
    }
    return;
  }
  if (n === 6) {
    setStep(6);
    if (getScreeningMode() === "iv_equal_freq") {
      initIvCoarseStep();
    } else {
      initMultivariateMineStep();
    }
    return;
  }
  if (n === 7) {
    setStep(7);
    await initChainPickStep();
    return;
  }
  if (n === 8) {
    setStep(8);
    initSerialStep();
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

// Method cards (Step 2 binning method only — do not bind screening-branch cards)
$$("#step-2 .method-card").forEach((card) => {
  card.addEventListener("click", () => {
    $$("#step-2 .method-card").forEach((c) => c.classList.remove("selected"));
    card.classList.add("selected");
    const input = card.querySelector('input[name="method"]');
    if (input) input.checked = true;
    updateBinNumVisibility();
  });
});

function selectScreeningMode(mode) {
  const next = mode === "iv_equal_freq" ? "iv_equal_freq" : "bad_rate";
  screeningMode = next;
  saveScreeningMode();
  syncScreeningBranchUI();
  reviewData = null;
  lastReviewQueryKey = "";
}

document.querySelectorAll(".screening-branch-cards .method-card").forEach((card) => {
  card.addEventListener("click", () => {
    const input = card.querySelector('input[name="screening-mode"]');
    if (!input) return;
    input.checked = true;
    selectScreeningMode(input.value);
    loadFeatureReviewSummary();
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
$("#refresh-review-iv")?.addEventListener("click", () => loadFeatureReviewSummary());

document.querySelectorAll('input[name="screening-mode"]').forEach((el) => {
  el.addEventListener("change", () => {
    if (!el.checked) return;
    selectScreeningMode(el.value);
    loadFeatureReviewSummary();
  });
});

$("#back-to-5-from-iv")?.addEventListener("click", () => setStep(5));
$("#to-iv-fine")?.addEventListener("click", () => {
  if (!ivSelectedPairs.size) {
    alert("请至少勾选一个两两组合");
    return;
  }
  ivFineSelectedPairs.clear();
  initIvFineStep();
});
$("#back-to-iv-coarse")?.addEventListener("click", () => initIvCoarseStep());
$("#iv-fine-select-all-pairs")?.addEventListener("change", (e) => {
  const on = e.target.checked;
  const combos = (ivPairOverview?.combos || []).filter((c) => ivSelectedPairs.has(c.pair_key));
  ivFineSelectedPairs.clear();
  if (on) combos.forEach((c) => ivFineSelectedPairs.add(c.pair_key));
  updateIvFineSelectedCount();
  renderIvFinePairList();
});
$("#iv-select-all-pairs")?.addEventListener("change", (e) => {
  const on = e.target.checked;
  if (!ivPairOverview?.combos) return;
  ivSelectedPairs.clear();
  if (on) ivPairOverview.combos.forEach((c) => ivSelectedPairs.add(c.pair_key));
  updateIvPairSelectedCount();
  renderIvCoarseList(ivPairOverview);
});
$("#to-step-7-from-iv")?.addEventListener("click", async () => {
  if (!ivFineSelectedPairs.size) {
    alert("请至少勾选一个要进入串联的组合");
    return;
  }
  const btn = $("#to-step-7-from-iv");
  const prevText = btn?.textContent;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "加载组合规则…";
  }
  try {
    await ensureIvPairDetails([...ivFineSelectedPairs]);
    const noCells = [...ivFineSelectedPairs].filter((k) => !getIvPairSelectedCells(k).size);
    if (noCells.length) {
      alert("请为每个组合在热力图中至少选择一个准入格子（点击格子勾选）。");
      return;
    }
    const { rules, skippedPairs } = buildIvCompoundRulesFromPairs([...ivFineSelectedPairs]);
    const pairsWithRules = new Set(rules.map((r) => r.pair_key));
    const failedPairs = [...ivFineSelectedPairs].filter((k) => !pairsWithRules.has(k));
    if (failedPairs.length) {
      const names = failedPairs.map((k) => {
        const c = ivPairOverview?.combos?.find((x) => x.pair_key === k);
        return formatIvPairComboLabel(c) || k;
      }).join("\n· ");
      alert(
        `已勾选 ${ivFineSelectedPairs.size} 个组合，但仅 ${pairsWithRules.size} 个生成了组合规则。\n\n`
        + `以下组合未生成规则（准入格可能为 0 样本或无法生成条件）：\n· ${names}\n\n`
        + "请返回精筛，为每个组合改选有效格子后再进入串联。"
      );
      return;
    }
    if (!rules.length) {
      alert("所选准入格无法生成组合规则（可能为 0 样本）。请改选其他格子或调整分箱边界。");
      return;
    }
    multivariateData = {
      rules,
      candidate_count: rules.length,
      pair_count: ivFineSelectedPairs.size,
      portfolio_bad_rate: rules[0]?.portfolio_bad_rate ?? 0,
      source: "iv_equal_freq",
    };
    selectedCompoundRules = new Set(rules.map((r) => r.rule_id));
    setStep(7);
    await initChainPickStep();
  } finally {
    if (btn) {
      btn.textContent = prevText || "下一步：串联挑选";
      updateIvFineSelectedCount();
    }
  }
});

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
  const mode = getScreeningMode();
  const params = new URLSearchParams();
  params.set("screening_mode", mode);
  if (mode === "iv_equal_freq") {
    params.set("iv_threshold", String(parseFloat($("#review-iv-threshold")?.value) || 0.02));
    params.set("bad_rate_threshold", "0.6");
    params.set("min_hit_count", "10");
  } else {
    const threshold = parseFloat($("#review-threshold")?.value) || 0.6;
    const minHit = parseInt($("#review-min-hit")?.value, 10) || 10;
    params.set("bad_rate_threshold", String(threshold));
    params.set("min_hit_count", String(minHit));
    params.set("iv_threshold", "0.02");
  }
  if (manualIncludeFeatures.size) {
    params.set("include_features", [...manualIncludeFeatures].join(","));
  }
  return params;
}

function buildSerialPickReviewQuery() {
  const params = new URLSearchParams();
  params.set("screening_mode", "bad_rate");
  params.set("bad_rate_threshold", String(getSerialPickBadRateThreshold()));
  params.set("min_hit_count", String(getSerialPickMinHit()));
  params.set("iv_threshold", "0.02");
  params.set("include_threshold_overview", "false");
  if (manualIncludeFeatures.size) {
    params.set("include_features", [...manualIncludeFeatures].join(","));
  }
  return params;
}

function getSerialPickBadRateThreshold() {
  return parseFloat($("#serial-pick-bad-rate-thresh")?.value) || 0.6;
}

function getSerialPickMinHit() {
  return parseInt($("#serial-pick-min-hit")?.value, 10) || 10;
}

function getSerialPickClusters() {
  if (getScreeningMode() === "iv_equal_freq") {
    return serialPickReviewData?.clusters || [];
  }
  return reviewData?.clusters || [];
}

function getAllSerialCandidateFeatures() {
  return getSerialPickClusters().flatMap((g) => g.features.map((f) => f.feature));
}

async function fetchSerialPickReviewData() {
  const jobId = getStoredJobId();
  if (!jobId) throw new Error("未找到分箱任务，请返回第 3 步重新执行分箱。");
  return api(`/api/binning/${jobId}/review?${buildSerialPickReviewQuery()}`, {
    headers: headers(false),
  });
}

async function loadSerialPickCandidates() {
  const el = $("#serial-feature-clusters");
  if (el) el.innerHTML = `<p class="sub">正在按坏率阈值加载单变量候选…</p>`;
  try {
    serialPickReviewData = await fetchSerialPickReviewData();
    for (const g of serialPickReviewData.clusters || []) {
      for (const f of g.features) initFeatureRule(f);
    }
    pruneSerialSelectedFeatures();
    const hint = $("#serial-pick-thresh-hint");
    if (hint) {
      hint.textContent = `当前 ${serialPickReviewData.total_candidates || 0} 个变量满足坏率 ≥ ${pct(getSerialPickBadRateThreshold())} 且命中 ≥ ${getSerialPickMinHit()} 人`;
    }
    renderSerialFeatureClusters(serialPickReviewData.clusters || []);
    updateChainPickHeaderCounts();
    updateSerialSelectAllCheckbox();
  } catch (err) {
    serialPickReviewData = null;
    if (el) el.innerHTML = `<p class="error">${err.message}</p>`;
    const countEl = $("#serial-review-count");
    if (countEl) countEl.textContent = "0";
  }
}

function pruneSerialSelectedFeatures() {
  const allowed = new Set(getAllSerialCandidateFeatures());
  for (const f of [...serialSelectedFeatures]) {
    if (!allowed.has(f)) serialSelectedFeatures.delete(f);
  }
  saveFeatureState();
}

function syncChainSerialThreshBar() {
  const bar = $("#chain-serial-thresh-bar");
  if (!bar) return;
  const iv = getScreeningMode() === "iv_equal_freq";
  bar.classList.toggle("hidden", !iv);
}

function shouldShowIvMetric(f) {
  return getScreeningMode() === "iv_equal_freq" && currentStep === 5 && f.iv_total != null;
}

function screeningModeStorageKey() {
  const jobId = getStoredJobId();
  return jobId ? `screening_mode_${jobId}` : "";
}

function getScreeningMode() {
  const checked = document.querySelector('input[name="screening-mode"]:checked');
  if (checked?.value === "iv_equal_freq") return "iv_equal_freq";
  if (checked?.value === "bad_rate") return "bad_rate";
  return screeningMode === "iv_equal_freq" ? "iv_equal_freq" : "bad_rate";
}

function loadScreeningModeFromStorage() {
  const key = screeningModeStorageKey();
  if (!key) return;
  const saved = localStorage.getItem(key);
  if (saved === "iv_equal_freq" || saved === "bad_rate") {
    screeningMode = saved;
  }
}

function saveScreeningMode() {
  const key = screeningModeStorageKey();
  if (key) localStorage.setItem(key, getScreeningMode());
}

function syncScreeningBranchUI() {
  const mode = getScreeningMode();
  document.querySelectorAll('input[name="screening-mode"]').forEach((el) => {
    el.checked = el.value === mode;
  });
  document.querySelectorAll(".screening-branch-cards .method-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.screeningMode === mode);
  });
  $("#review-controls-bad-rate")?.classList.toggle("hidden", mode !== "bad_rate");
  $("#review-controls-iv")?.classList.toggle("hidden", mode !== "iv_equal_freq");
  const method = reviewData?.params?.method;
  const warn = $("#review-iv-method-warn");
  if (warn) {
    warn.classList.toggle("hidden", mode !== "iv_equal_freq" || method === "quantile");
  }
  syncStep5Labels();
  syncStep5Layout();
  updateSelectAllCheckbox();
}

function syncStep5Layout() {
  const iv = getScreeningMode() === "iv_equal_freq";
  $("#pick-stage")?.classList.toggle("iv-pick-mode", iv);
  $("#step-5 .pick-impact-panel")?.classList.toggle("hidden", iv);
  const badOpt = $("#feature-sort")?.querySelector('option[value="bad_rate"]');
  if (badOpt) badOpt.textContent = iv ? "按 IV" : "按坏率";
}

function syncStep5Labels() {
  const iv = getScreeningMode() === "iv_equal_freq";
  const btn6 = $("#to-step-6");
  const skip7 = $("#to-step-7-skip");
  if (btn6) btn6.textContent = iv ? "下一步：组合初筛" : "下一步：多变量分箱";
  if (skip7) skip7.textContent = iv ? "跳过调箱，串联挑选" : "跳过挖树，串联挑选";
  const step5Desc = $("#step-5 .desc");
  if (step5Desc) {
    step5Desc.innerHTML = iv
      ? `勾选 IV 达标变量（<strong>最多 ${IV_MAX_FEATURES} 个</strong>），第 6 步穷举 C(n,2) 两两组合等频划格子初筛，精筛可调边界。串联单变量请在第 7 步勾选。`
      : "勾选变量作为<strong>多变量挖树池</strong>（第 6 步使用）。串联分析的单变量请在第 7 步「串联挑选」中勾选。";
  }
  updateReviewCountBadge();
  syncStep5Layout();
}

function setScreeningMode(mode) {
  selectScreeningMode(mode);
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
    serialSelected: [...serialSelectedFeatures],
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
    if (Array.isArray(data.serialSelected)) {
      serialSelectedFeatures.clear();
      for (const f of data.serialSelected) serialSelectedFeatures.add(f);
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
  loadScreeningModeFromStorage();
  syncScreeningBranchUI();

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
    pruneSelectedToCandidates();
    updateReviewCountBadge();
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
  const ivMode = data.screening_mode === "iv_equal_freq";
  const hint = ivMode
    ? `条件：IV(total) ≥ ${Number(data.iv_threshold ?? 0.02).toFixed(3)}（基于等频/分箱结果，下一步可逐箱查看逾期率并调整阈值）`
    : `条件：坏率 &gt; ${pct(data.bad_rate_threshold)} 且单箱命中 ≥ ${data.min_hit_count} 人（<strong>数字列</strong>：头/尾箱；<strong>类别列</strong>：任一超阈值类别，明细中勾选拒绝）`;
  el.innerHTML = `
    <div class="count-hero">
      <span class="count-num">${data.total_candidates}</span>
      <span class="count-label">个特征满足当前条件</span>
    </div>
    ${extraLine}
    <p class="sub count-hint">${hint}</p>
  `;
}

function renderReviewReport(data) {
  const el = $("#review-report");
  el.classList.remove("hidden");
  const ivMode = data.screening_mode === "iv_equal_freq";
  el.innerHTML = `
    <strong>筛选概览</strong>
    <p class="desc" style="margin:.75rem 0">${data.report_summary}</p>
    <dl class="info-grid">
      <div><dt>筛选路径</dt><dd>${ivMode ? "IV 等频" : "头尾坏率"}</dd></div>
      <div><dt>Train 大盘坏率</dt><dd>${pct(data.train_bad_rate)}</dd></div>
      <div><dt>Test 大盘坏率</dt><dd>${data.test_bad_rate != null ? pct(data.test_bad_rate) : "—"}</dd></div>
      ${ivMode
    ? `<div><dt>IV 阈值</dt><dd>${Number(data.iv_threshold ?? 0.02).toFixed(4)}</dd></div>`
    : `<div><dt>当前坏率阈值</dt><dd>${pct(data.bad_rate_threshold)}</dd></div>
         <div><dt>最少命中</dt><dd>${data.min_hit_count} 人</dd></div>`}
    </dl>
    ${ivMode ? "" : '<p class="warn" style="margin-top:.75rem">最少命中人数：数值变量指头/尾箱样本量，类别变量指单个超阈值类别的样本量。建议 ≥10；只抓极端高风险可提高到 20–50。</p>'}
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
  const ivMode = getScreeningMode() === "iv_equal_freq";
  wrap.innerHTML = `<p class="sub">${ivMode ? "IV" : "阈值"}概览计算中（约 3–8 秒，不阻塞选特征）…</p>`;
  try {
    const minHit = parseInt($("#review-min-hit")?.value, 10) || 10;
    const mode = getScreeningMode();
    const data = await api(
      `/api/binning/${getStoredJobId()}/threshold-overview?min_hit_count=${minHit}&screening_mode=${mode}`,
      { headers: headers(false) }
    );
    renderThresholdOverview(data.threshold_overview, mode);
  } catch (err) {
    wrap.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function renderThresholdOverview(rows, mode = getScreeningMode()) {
  const wrap = $("#threshold-overview");
  if (!rows?.length) {
    wrap.classList.add("hidden");
    return;
  }
  const ivMode = mode === "iv_equal_freq";
  wrap.classList.remove("hidden");
  wrap.innerHTML = `
    <h3 style="font-size:1rem;margin:1rem 0 .5rem">${ivMode ? "IV 阈值概览" : "阈值概览"}（辅助判断切割点）</h3>
    <div class="threshold-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>${ivMode ? "IV(total) 阈值" : "拒绝坏率阈值"}</th>
            <th>候选变量数</th>
            ${ivMode ? "" : "<th>平均单箱命中</th>"}
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${r.threshold_pct}</td>
              <td>${r.feature_count}</td>
              ${ivMode ? "" : `<td>${r.avg_hit || "—"}</td>`}
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
  if (shouldShowIvMetric(f)) {
    return Number(f.iv_total).toFixed(4);
  }
  const live = getFeatureLiveStats(f);
  if (live) return pct(live.bad_rate);
  if (isFeatureStatsPending(f)) return "…";
  return pct(f.max_bad_rate);
}

function formatFeatureMetricTitle(f) {
  if (shouldShowIvMetric(f)) {
    return `IV(total) ${Number(f.iv_total).toFixed(4)}`;
  }
  const live = getFeatureLiveStats(f);
  if (live) {
    return `当前规则命中坏率 ${pct(live.bad_rate)}`;
  }
  if (isFeatureStatsPending(f)) return "正在重算…";
  return `建议拒绝箱坏率 ${pct(f.max_bad_rate)}`;
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
  const ivMode = getScreeningMode() === "iv_equal_freq";
  const key = featureSortMode === "hit_count"
    ? "hit_count"
    : (ivMode ? "iv_total" : "max_bad_rate");
  return clusters.map((g) => ({
    ...g,
    features: [...g.features].sort((a, b) => {
      if (key === "hit_count") {
        const av = getFeatureHitCount(a) ?? a.hit_count;
        const bv = getFeatureHitCount(b) ?? b.hit_count;
        return bv - av;
      }
      if (key === "iv_total") {
        return (b.iv_total ?? 0) - (a.iv_total ?? 0);
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
    if (r.source_bins?.length && !r.userEdited) {
      rule.source_bins = [...r.source_bins];
    }
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
    return `<p class="sub">勾选单变量或组合规则后，此处实时展示 Train 拒绝前后指标。</p>`;
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
  if (getScreeningMode() === "iv_equal_freq" && currentStep === 5) return;
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
  if (getScreeningMode() === "iv_equal_freq" && currentStep === 5) return;
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
    const rateEl = row.querySelector(".rate, .iv-total-val");
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
      const ivMode = getScreeningMode() === "iv_equal_freq";
      rateEl.textContent = formatFeatureBadRate(meta);
      rateEl.classList.toggle("iv-total-val", ivMode);
      rateEl.classList.toggle("rate", !ivMode);
      rateEl.title = formatFeatureMetricTitle(meta);
    }
  });
  updateDetailLiveSubtitle();
}

function onRuleInputChange(feat) {
  const row = document.querySelector(`.feature-rule-line[data-feature="${CSS.escape(feat)}"]`);
  if (!row) return;
  const op = row.querySelector(".rule-op")?.value || ">";
  const th = parseFloat(row.querySelector(".rule-threshold")?.value) || 0;
  featureRules.set(feat, { operator: op, threshold: th, userEdited: true, source_bins: undefined });
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
  if (getScreeningMode() === "iv_equal_freq" && currentStep >= 7) {
    return getSerialPickBadRateThreshold();
  }
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
    source_bins: f.rule_source_bins?.length ? [...f.rule_source_bins] : undefined,
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

function renderFeatureRuleLine(f, options = {}) {
  initFeatureRule(f);
  const hideThreshold = Boolean(options.hideThreshold);
  if (hideThreshold) {
    return `
      <div class="feature-rule-line feature-rule-line-iv" data-feature="${f.feature}">
        <span class="rule-base rule-name-only">${f.feature}</span>
      </div>
    `;
  }
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
      <input type="text" class="rule-threshold" data-feature="${f.feature}"
        value="${rule.threshold}" inputmode="decimal" aria-label="拒绝阈值" />
    </div>
  `;
}

function shouldHideFeatureThresholdInList() {
  return getScreeningMode() === "iv_equal_freq" && currentStep === 5;
}

function refreshFeatureRuleLine(feature) {
  const meta = findFeatureMetaByName(feature);
  if (!meta) return;
  const row = document.querySelector(`.feature-row[data-feature="${CSS.escape(feature)}"]`);
  const line = row?.querySelector(".feature-rule-line");
  if (!line) return;
  const tmp = document.createElement("div");
  tmp.innerHTML = renderFeatureRuleLine(meta, { hideThreshold: shouldHideFeatureThresholdInList() });
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
  if (checked && getScreeningMode() === "iv_equal_freq" && selectedFeatures.size >= IV_MAX_FEATURES && !selectedFeatures.has(feature)) {
    alert(`IV 等频格子最多选择 ${IV_MAX_FEATURES} 个变量`);
    return false;
  }
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
  return true;
}

function getCandidateFeatureTotal() {
  if (reviewData?.total_candidates != null) return reviewData.total_candidates;
  if (reviewData?.clusters?.length) {
    return reviewData.clusters.reduce((n, g) => n + (g.features?.length || 0), 0);
  }
  return 0;
}

function updateReviewCountBadge() {
  const badge = $("#review-count");
  if (!badge) return;
  const total = getCandidateFeatureTotal();
  const selected = selectedFeatures.size;
  if (getScreeningMode() === "iv_equal_freq") {
    badge.textContent = `${selected}/${IV_MAX_FEATURES}`;
    badge.title = `已选 ${selected} 个 · 最多 ${IV_MAX_FEATURES} 个 · 共 ${total} 个候选`;
  } else {
    badge.textContent = String(total);
    badge.title = `共 ${total} 个候选特征`;
  }
}

function pruneSelectedToCandidates() {
  const allowed = new Set(getAllCandidateFeatures());
  if (!allowed.size) return;
  let changed = false;
  for (const f of [...selectedFeatures]) {
    if (!allowed.has(f)) {
      selectedFeatures.delete(f);
      featureRules.delete(f);
      liveFeatureStats.delete(f);
      changed = true;
    }
  }
  if (changed) {
    saveFeatureState();
    markSerialStale();
  }
}

function syncFeatureCheckboxLimits() {
  const full = getScreeningMode() === "iv_equal_freq" && selectedFeatures.size >= IV_MAX_FEATURES;
  $$("#feature-clusters input[type=checkbox][data-feature]").forEach((cb) => {
    const feat = cb.dataset.feature;
    const locked = full && !selectedFeatures.has(feat);
    cb.disabled = locked;
    cb.closest(".feature-row")?.classList.toggle("is-selection-locked", locked);
  });
  updateSelectAllCheckbox();
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
  const ivMode = getScreeningMode() === "iv_equal_freq";
  const ivFull = ivMode && selectedFeatures.size >= IV_MAX_FEATURES;
  cb.disabled = ivFull;
  const inList = all.filter((f) => selectedFeatures.has(f)).length;
  if (ivFull) {
    cb.checked = false;
    cb.indeterminate = selectedFeatures.size > 0;
    return;
  }
  cb.checked = inList > 0 && inList === all.length;
  cb.indeterminate = inList > 0 && inList < all.length;
}

function selectAllFeatures(checked) {
  if (checked) {
    const all = getAllCandidateFeatures();
    const limit = getScreeningMode() === "iv_equal_freq" ? IV_MAX_FEATURES : all.length;
    let n = 0;
    for (const feat of all) {
      if (n >= limit) break;
      setFeatureSelected(feat, true);
      n += 1;
    }
    if (getScreeningMode() === "iv_equal_freq" && all.length > IV_MAX_FEATURES) {
      alert(`IV 等频格子最多选 ${IV_MAX_FEATURES} 个，已自动勾选前 ${IV_MAX_FEATURES} 个`);
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
      ${g.features.map((f) => {
        const checked = selectedFeatures.has(f.feature);
        const locked = getScreeningMode() === "iv_equal_freq"
          && selectedFeatures.size >= IV_MAX_FEATURES
          && !checked;
        return `
        <div class="feature-row${activeFeature === f.feature ? " active" : ""}${locked ? " is-selection-locked" : ""}" data-feature="${f.feature}">
          <input type="checkbox" data-feature="${f.feature}" ${checked ? "checked" : ""}${locked ? " disabled" : ""} />
          <div class="feature-row-main">
            ${renderFeatureRuleLine(f, { hideThreshold: getScreeningMode() === "iv_equal_freq" })}
            ${f.manual_include ? '<span class="manual-badge">手动</span>' : ""}
            <span class="cn">${f.chinese_name}</span>
            ${f.value_type === "categorical" ? '<span class="rule-warn">类别变量：请在明细中勾选要拒绝的类别</span>' : ""}
            ${f.value_type !== "categorical" && !f.rule_meets_min_hit ? '<span class="rule-warn">建议规则样本偏少，请结合明细调整</span>' : ""}
          </div>
          <div class="feature-row-meta">
            <span class="${getScreeningMode() === "iv_equal_freq" ? "iv-total-val" : "rate"}">${formatFeatureBadRate(f)}</span>
            <span class="${effectClass(f.effect_label)}">${f.effect_label}</span>
            <span class="hit">${formatFeatureHitCount(f)}</span>
          </div>
        </div>
      `;
      }).join("")}
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
    row.querySelector('input[type="checkbox"]').addEventListener("change", (e) => {
      e.stopPropagation();
      const want = e.target.checked;
      if (!setFeatureSelected(feat, want)) {
        e.target.checked = !want;
        return;
      }
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
  syncFeatureCheckboxLimits();
  updateReviewCountBadge();
}

function syncFeatureCheckboxes() {
  $$("#feature-clusters input[type=checkbox][data-feature]").forEach((cb) => {
    cb.checked = selectedFeatures.has(cb.dataset.feature);
  });
  updateSelectAllCheckbox();
  syncFeatureCheckboxLimits();
}

function updateSelectedCount() {
  const n = $("#selected-count");
  const cnt = selectedFeatures.size;
  if (n) n.textContent = String(cnt);
  const btn6 = $("#to-step-6");
  if (btn6) btn6.disabled = cnt === 0;
  updateReviewCountBadge();
  syncFeatureCheckboxLimits();
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
          source_bins: data.rule.rule_source_bins?.length
            ? [...data.rule.rule_source_bins]
            : undefined,
          valueType: "numeric",
        });
        syncRuleInputs(feature);
        scheduleRejectPreview(feature);
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

function renderBinTables(detail, badRateThreshold, targetEl, options = {}) {
  const el = targetEl || $("#detail-bins");
  if (!el) return;
  if (detail.value_type === "categorical") {
    renderCategoricalBinTables(el, detail, badRateThreshold, options);
    return;
  }

  const bins = detail.bins || {};
  const trainRows = bins.train || [];
  const testRows = bins.test || [];
  const trainTotals = detail.binning_totals?.train || {};
  const testTotals = detail.binning_totals?.test || {};
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
        <h4>Train 分箱明细（${binCount} 箱）${formatBinningTotals(trainTotals)}</h4>
        ${renderBinTable(trainRows, badRateThreshold, null, trainTotals)}
      </div>
      <div>
        <h4>Test 分箱明细（${trainRows.length} 箱，与 Train 同序）${formatBinningTotals(testTotals)}</h4>
        ${renderBinTable(testRows, badRateThreshold, trainRows, testTotals)}
      </div>
    </div>
  `;
}

function renderCategoricalBinTables(el, detail, badRateThreshold, options = {}) {
  const feature = detail.feature;
  const showAllBins = Boolean(options.showAllBins);
  const categories = showAllBins
    ? (detail.bins?.train || []).map((r) => ({
      bin: r.bin,
      obs: r.obs,
      bad_rate: r.bad_rate,
      is_missing: String(r.bin).toLowerCase().includes("缺失") || String(r.bin).toLowerCase().includes("nan"),
    }))
    : (detail.high_bad_categories || []);
  const rule = getFeatureRule(feature);
  const selected = new Set(rule.values || []);
  const bins = detail.bins || {};
  const testByBin = Object.fromEntries((bins.test || []).map((r) => [r.bin, r]));

  if (!categories.length) {
    el.innerHTML = showAllBins
      ? `<p class="sub">暂无分箱数据。</p>`
      : `<p class="sub">当前阈值下没有坏率 &gt; ${pct(badRateThreshold)} 的类别。</p>`;
    return;
  }

  el.innerHTML = `
    <p class="sub bin-hint">${showAllBins
    ? "展示全部等频类别分箱与逾期率，请勾选要纳入拒绝规则的类别。"
    : `仅展示坏率 &gt; ${pct(badRateThreshold)} 的类别（样本 ≥ 10）。请勾选要纳入拒绝规则的类别；未勾选的不拒绝。`}</p>
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

function formatBinningTotals(totals) {
  if (!totals) return "";
  const iv = totals.total_iv;
  const ks = totals.total_ks;
  if (iv == null && ks == null) return "";
  const parts = [];
  if (iv != null) parts.push(`IV(total) ${Number(iv).toFixed(4)}`);
  if (ks != null) parts.push(`KS(total) ${Number(ks).toFixed(4)}`);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function renderBinTable(rows, badRateThreshold, binTemplate, totals) {
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
  const totalIv = totals?.total_iv;
  const totalKs = totals?.total_ks;
  const ivCell = totalIv != null ? Number(totalIv).toFixed(4) : "—";
  const ksCell = totalKs != null ? Number(totalKs).toFixed(4) : "—";
  return `
    <table class="data-table bin-detail-table">
      <thead>
        <tr>
          <th>箱</th>
          <th>#Obs</th>
          <th>#Bad</th>
          <th>坏率</th>
          <th>Lift</th>
          <th>IV(total)</th>
          <th>KS(total)</th>
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
            <td>${ivCell}</td>
            <td>${ksCell}</td>
          </tr>
        `;
        }).join("")}
        <tr class="bin-total-row">
          <td><strong>合计</strong></td>
          <td><strong>${totalObs}</strong></td>
          <td><strong>${totalBad}</strong></td>
          <td class="bar-col">${renderRateBar(totalBr, "bad")}</td>
          <td>—</td>
          <td><strong>${ivCell}</strong></td>
          <td><strong>${ksCell}</strong></td>
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
    const extraCls = isSummary ? "stability-sum-row" : (b.is_max_cell ? "stability-max-row" : "");
    const trCls = extraCls ? ` class="${extraCls}"` : "";
    let cells = `<td>${b.bin}${b.is_max_cell ? ' <span class="badge badge-sm">最高格</span>' : ""}</td>`;
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
    if (r.source_bins?.length && !r.userEdited) {
      rule.source_bins = [...r.source_bins];
    }
    if (r.operator === "in") {
      rule.values = [...(r.values || [])];
      if (!rule.values.length) return null;
    }
    return rule;
  }).filter(Boolean);
}

function syncRulesFromReview() {
  if (getScreeningMode() === "iv_equal_freq" && currentStep >= 7) {
    for (const feat of serialSelectedFeatures) {
      const meta = findFeatureMetaByName(feat);
      const cur = featureRules.get(feat);
      if (meta && !cur?.userEdited) initFeatureRule(meta);
    }
    return;
  }
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
          source_bins: cur?.source_bins?.length
            ? [...cur.source_bins]
            : (meta.rule_source_bins?.length ? [...meta.rule_source_bins] : undefined),
          valueType: "numeric",
        });
      }
    }
  }
}

function buildSerialRequestBody() {
  return {
    rules: buildFinalSerialRules(),
    sort_mode: $("#serial-sort")?.value || "selection",
    lift_min: parseFloat($("#serial-lift-min")?.value) || 1.1,
    min_hit: parseInt($("#serial-min-hit")?.value, 10) || 5,
  };
}

function buildSingleRulesForList(features) {
  syncRulesFromReview();
  return features.map((feat) => {
    const r = getFeatureRule(feat);
    const rule = {
      feature: feat,
      operator: r.operator,
      threshold: Number(r.threshold) || 0,
      rule_type: "single",
    };
    if (r.source_bins?.length && !r.userEdited) {
      rule.source_bins = [...r.source_bins];
    }
    if (r.operator === "in") {
      rule.values = [...(r.values || [])];
      if (!rule.values.length) return null;
    }
    return rule;
  }).filter(Boolean);
}

function buildCompoundRulesPayload() {
  return getFilteredMvRules()
    .filter((r) => selectedCompoundRules.has(r.rule_id))
    .map((r) => ({
      rule_type: "compound",
      rule_id: r.rule_id,
      rule_display: r.rule_display,
      conditions: r.conditions,
      features: r.features,
      feature: r.rule_id,
      operator: "compound",
      threshold: 0,
    }));
}

function buildFinalSerialRules() {
  const singles = buildSingleRulesForList([...serialSelectedFeatures]);
  const compounds = buildCompoundRulesPayload();
  return [...singles, ...compounds];
}

function getFilteredMvRules() {
  if (!multivariateData?.rules) return [];
  if (multivariateData.source === "iv_equal_freq") {
    return multivariateData.rules;
  }
  const th = parseFloat($("#mv-threshold")?.value) || 0.35;
  const minHit = parseInt($("#mv-min-hit")?.value, 10) || 10;
  return multivariateData.rules.filter(
    (r) => (r.bad_rate || 0) >= th && (r.hit_count || 0) >= minHit
  );
}

function updateChainPickHeaderCounts() {
  const serialEl = $("#serial-selected-count");
  if (serialEl) serialEl.textContent = String(serialSelectedFeatures.size);
  const mvEl = $("#mv-selected-count");
  if (mvEl) mvEl.textContent = String(selectedCompoundRules.size);
  const btn8 = $("#to-step-8");
  const canProceed = serialSelectedFeatures.size > 0 || selectedCompoundRules.size > 0;
  if (btn8) {
    btn8.disabled = !canProceed;
    btn8.title = canProceed ? "进入串联分析" : "请至少勾选一个单变量或组合规则";
  }
}

function updateMvSelectedCount() {
  updateChainPickHeaderCounts();
}

function showStep6SubView(view) {
  const mine = $("#step-6-mine-view");
  const thresh = $("#step-6-thresh-view");
  const ivWrap = $("#step-6-iv-view");
  const ivCoarse = $("#step-6-iv-coarse");
  const ivFine = $("#step-6-iv-fine");
  const isIv = view === "iv-coarse" || view === "iv-fine";
  if (mine) mine.classList.toggle("hidden", view !== "mine");
  if (thresh) thresh.classList.toggle("hidden", view !== "thresh");
  if (ivWrap) ivWrap.classList.toggle("hidden", !isIv);
  if (ivCoarse) ivCoarse.classList.toggle("hidden", view !== "iv-coarse");
  if (ivFine) ivFine.classList.toggle("hidden", view !== "iv-fine");
}

function initMvThresholdStep() {
  showStep6SubView("thresh");
  if (!multivariateData) {
    $("#mv-thresh-summary-card")?.classList.add("hidden");
    $("#mv-threshold-overview")?.classList.add("hidden");
    return;
  }
  renderMvThreshSummary();
  renderMvThresholdOverviewClient();
}

function renderMvThreshSummary() {
  const el = $("#mv-thresh-summary-card");
  if (!el || !multivariateData) return;
  const th = parseFloat($("#mv-threshold")?.value) || 0.35;
  const minHit = parseInt($("#mv-min-hit")?.value, 10) || 10;
  const filtered = getFilteredMvRules();
  const total = multivariateData.candidate_count ?? multivariateData.rules?.length ?? 0;
  el.classList.remove("hidden");
  el.innerHTML = `
    <p class="mv-thresh-highlight">满足所选阈值的组合规则：<strong>${filtered.length}</strong> 条
    <span class="sub">（坏率 ≥ ${pct(th)} 且全量命中 ≥ ${minHit} 人）</span></p>
    <p class="sub">共挖掘 ${total} 条候选 · K=${mvCombK} · 特征池 ${multivariatePoolFeatures.length} 个
    · 全量大盘坏率 ${pct(multivariateData.portfolio_bad_rate || 0)}
    · 评估口径与 notebook 一致（原始数值切分，非分箱边界）</p>
  `;
}

function renderMvThresholdOverviewClient() {
  const wrap = $("#mv-threshold-overview");
  if (!wrap || !multivariateData?.rules?.length) {
    wrap?.classList.add("hidden");
    return;
  }
  const minHit = parseInt($("#mv-min-hit")?.value, 10) || 10;
  const selectedTh = parseFloat($("#mv-threshold")?.value) || 0.35;
  const thresholds = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70];
  const rows = thresholds.map((th) => {
    const matched = multivariateData.rules.filter(
      (r) => (r.bad_rate || 0) >= th && (r.hit_count || 0) >= minHit
    );
    const avgHit = matched.length
      ? Math.round(matched.reduce((s, r) => s + (r.hit_count || 0), 0) / matched.length)
      : null;
    const isSelected = Math.abs(th - selectedTh) < 0.001;
    return {
      threshold_pct: `${Math.round(th * 100)}%`,
      rule_count: matched.length,
      avg_hit: avgHit,
      hint: isSelected ? "← 当前所选阈值" : "全量命中坏率 ≥ 阈值，且命中人数 ≥ 最少命中",
      isSelected,
    };
  });
  wrap.classList.remove("hidden");
  wrap.innerHTML = `
    <h3 style="font-size:1rem;margin:1rem 0 .5rem">不同坏率阈值下满足的组合规则数</h3>
    <div class="threshold-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>组合坏率阈值</th>
            <th>满足条数</th>
            <th>平均命中人数</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr class="${r.isSelected ? "threshold-row-active" : ""}">
              <td>${r.threshold_pct}</td>
              <td><strong>${r.rule_count}</strong></td>
              <td>${r.avg_hit ?? "—"}</td>
              <td style="text-align:left;font-size:.75rem;color:var(--muted)">${r.hint}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function applyMvThresholdSettings() {
  renderMvThreshSummary();
  renderMvThresholdOverviewClient();
  if (currentStep === 7) {
    applyMvFilter();
  }
}

function readMvMiningParams(fromModal = false) {
  const kRaw = parseInt(
    fromModal ? $("#mv-k-input")?.value : $("#mv-k-inline")?.value,
    10,
  );
  const depthRaw = parseInt(
    fromModal ? $("#mv-k-tree-depth")?.value : $("#mv-tree-depth")?.value,
    10,
  );
  return {
    k: Math.min(6, Math.max(2, Number.isFinite(kRaw) ? kRaw : 3)),
    tree_depth: Math.min(10, Math.max(1, Number.isFinite(depthRaw) ? depthRaw : 3)),
  };
}

function applyMvMiningParamsToInputs(params = {}) {
  const k = params.comb ?? mvCombK;
  const depth = params.tree_depth ?? mvTreeDepth;
  mvCombK = k;
  mvTreeDepth = depth;
  if ($("#mv-k-inline")) $("#mv-k-inline").value = String(k);
  if ($("#mv-tree-depth")) $("#mv-tree-depth").value = String(depth);
  if ($("#mv-k-input")) $("#mv-k-input").value = String(k);
  if ($("#mv-k-tree-depth")) $("#mv-k-tree-depth").value = String(depth);
}

function mvMiningParamsMatch(data) {
  if (!data?.params) return false;
  const cur = readMvMiningParams();
  const p = data.params;
  return p.comb === cur.k
    && Number(p.tree_depth) === cur.tree_depth;
}

// ——— Step 6: Multivariate mining ———

function renderMvPoolChips() {
  const el = $("#mv-pool-chips");
  const countEl = $("#mv-pool-count");
  const feats = multivariatePoolFeatures.length ? multivariatePoolFeatures : [...selectedFeatures];
  if (countEl) countEl.textContent = String(feats.length);
  if (!el) return;
  if (!feats.length) {
    el.innerHTML = `<p class="sub">请返回第 5 步勾选至少 ${mvCombK || 2} 个变量</p>`;
    return;
  }
  el.innerHTML = feats.map((f) => {
    const meta = findFeatureMetaByName(f);
    const label = meta?.chinese_name || f;
    return `<span class="manual-chip" title="${f}">${label}</span>`;
  }).join("");
}

function initMultivariateMineStep() {
  showStep6SubView("mine");
  multivariatePoolFeatures = multivariatePoolFeatures.length
    ? multivariatePoolFeatures
    : [...selectedFeatures];
  renderMvPoolChips();
  if (multivariateData?.params) {
    applyMvMiningParamsToInputs(multivariateData.params);
  } else {
    applyMvMiningParamsToInputs({ comb: mvCombK, tree_depth: mvTreeDepth });
  }
  const btnThresh = $("#to-step-6-thresh");
  if (multivariateData && mvMiningParamsMatch(multivariateData)) {
    renderMvMineSummary();
    if (btnThresh) btnThresh.disabled = false;
  } else {
    if (btnThresh) btnThresh.disabled = true;
    $("#mv-mine-summary")?.classList.add("hidden");
  }
}

function formatIvPairIvTotal(c) {
  if (c?.grid_iv_total != null) return Number(c.grid_iv_total).toFixed(4);
  return "—";
}

function bindIvCoarseListEvents() {
  const list = $("#iv-coarse-list");
  if (!list || list.dataset.ivBound) return;
  list.dataset.ivBound = "1";
  list.addEventListener("click", (e) => {
    if (e.target.closest(".iv-pair-check")) return;
    const btn = e.target.closest(".iv-pair-expand");
    const meta = e.target.closest(".iv-pair-meta");
    const card = e.target.closest(".iv-pair-card");
    const key = btn?.dataset.pairKey || card?.dataset.pairKey;
    if (!key) return;
    if (btn || meta) {
      e.preventDefault();
      toggleIvCoarseExpand(key);
    }
  });
  list.addEventListener("change", (e) => {
    const cb = e.target.closest('input[type="checkbox"][data-pair-key]');
    if (!cb) return;
    e.stopPropagation();
    setIvPairSelected(cb.dataset.pairKey, cb.checked);
  });
}

function initIvCoarseStep() {
  showStep6SubView("iv-coarse");
  ivCoarseExpandedPair = "";
  bindIvCoarseListEvents();
  multivariatePoolFeatures = multivariatePoolFeatures.length
    ? multivariatePoolFeatures.slice(0, IV_MAX_FEATURES)
    : [...selectedFeatures].slice(0, IV_MAX_FEATURES);
  loadIvPairOverview();
}

async function loadIvPairOverview() {
  const errEl = $("#iv-coarse-error");
  const listEl = $("#iv-coarse-list");
  errEl?.classList.add("hidden");
  if (listEl) listEl.innerHTML = `<p class="sub">正在生成 C(n,2) 等频组合格子…</p>`;

  const feats = [...multivariatePoolFeatures];
  if (feats.length < 2) {
    if (listEl) listEl.innerHTML = `<p class="sub">请返回第 5 步至少勾选 2 个变量（最多 ${IV_MAX_FEATURES} 个）。</p>`;
    return;
  }

  try {
    const data = await api(`/api/binning/${getStoredJobId()}/iv-pair-grids`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ features: feats }),
    });
    ivPairDetailCache.clear();
    ivPairOverview = data;
    (data.combos || []).forEach((c) => {
      if (c.grid) ivPairDetailCache.set(c.pair_key, c.grid);
    });
    renderIvCoarseSummary(data);
    renderIvCoarseList(data);
  } catch (err) {
    if (listEl) listEl.innerHTML = "";
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  }
}

function renderIvCoarseSummary(data) {
  const el = $("#iv-coarse-summary");
  const toolbar = $("#iv-coarse-toolbar");
  if (!el) return;
  el.classList.remove("hidden");
  toolbar?.classList.remove("hidden");
  const n = data.feature_count || 0;
  const pairs = data.pair_count || 0;
  el.innerHTML = `
    <div class="count-hero">
      <span class="count-num">${pairs}</span>
      <span class="count-label">种两两组合（C(${n},2)）</span>
    </div>
    <p class="sub count-hint">已选 ${n} 个变量 · 每种组合按等频分箱划交叉格子，按 IV(total) 排序。点「展开详情」或点击下方指标行查看热力图与按月稳定性。</p>
  `;
  updateIvPairSelectedCount();
}

function renderIvCoarseList(data) {
  const el = $("#iv-coarse-list");
  if (!el) return;
  const combos = data.combos || [];
  if (!combos.length) {
    el.innerHTML = `<p class="sub">未能生成组合格子${data.errors?.length ? `：${data.errors[0]}` : ""}</p>`;
    return;
  }
  el.innerHTML = combos.map((c) => {
    const checked = ivSelectedPairs.has(c.pair_key);
    const expanded = ivCoarseExpandedPair === c.pair_key;
    const ivText = formatIvPairIvTotal(c);
    return `
      <div class="iv-pair-card${checked ? " is-selected" : ""}${expanded ? " is-expanded" : ""}" data-pair-key="${c.pair_key}">
        <div class="iv-pair-head">
          <label class="iv-pair-check">
            <input type="checkbox" data-pair-key="${c.pair_key}" ${checked ? "checked" : ""} />
            <span class="iv-pair-title">${c.chinese_a || c.feature_a} × ${c.chinese_b || c.feature_b}</span>
          </label>
          <button type="button" class="btn ghost btn-sm iv-pair-expand" data-pair-key="${c.pair_key}" aria-expanded="${expanded}">
            ${expanded ? "收起" : "展开详情"}
          </button>
        </div>
        <div class="iv-pair-meta">
          <span>${c.row_bins}×${c.col_bins} 格</span>
          <span class="iv-total-val">IV(total) ${ivText}</span>
          <span class="sub">${c.sample_count} 样本</span>
        </div>
        <div class="iv-pair-detail${expanded ? "" : " hidden"}" data-pair-detail="${c.pair_key}">
          ${expanded ? renderIvCoarseDetailShell(c.pair_key) : ""}
        </div>
      </div>
    `;
  }).join("");

  if (ivCoarseExpandedPair) {
    loadIvCoarsePairDetail(ivCoarseExpandedPair);
  }
}

function renderIvCoarseDetailShell(pairKey) {
  const cached = ivPairDetailCache.get(pairKey);
  if (cached) return renderIvCoarseDetailContent(pairKey, cached);
  return `<p class="sub iv-coarse-loading">正在加载交叉格子详情…</p>`;
}

function renderIvCoarseDetailContent(pairKey, data) {
  const tab = ivCoarseDetailTab.get(pairKey) || "overview";
  return `
    <div class="iv-coarse-detail-inner" data-pair-key="${pairKey}">
      <div class="detail-tabs iv-coarse-detail-tabs">
        <button type="button" class="detail-tab${tab === "overview" ? " active" : ""}" data-iv-coarse-tab="overview">总览</button>
        <button type="button" class="detail-tab${tab === "monthly" ? " active" : ""}" data-iv-coarse-tab="monthly">按月</button>
      </div>
      <div class="iv-coarse-tab-panel${tab === "overview" ? "" : " hidden"}" data-iv-coarse-panel="overview">
        <p class="sub">${data.sample_count} 样本 · IV(total) <strong>${formatIvPairIvTotal(data)}</strong></p>
        ${renderIvGridHeatmap(data)}
      </div>
      <div class="iv-coarse-tab-panel${tab === "monthly" ? "" : " hidden"}" data-iv-coarse-panel="monthly">
        ${renderIvPairMonthlyStability(data)}
      </div>
    </div>
  `;
}

function findIvCoarsePairCard(pairKey) {
  if (!pairKey) return null;
  return document.querySelector(`#iv-coarse-list .iv-pair-card[data-pair-key="${CSS.escape(pairKey)}"]`);
}

async function toggleIvCoarseExpand(pairKey) {
  if (ivCoarseExpandedPair === pairKey) {
    ivCoarseExpandedPair = "";
  } else {
    ivCoarseExpandedPair = pairKey;
  }

  $$("#iv-coarse-list .iv-pair-card").forEach((card) => {
    const key = card.dataset.pairKey;
    const expanded = key === ivCoarseExpandedPair;
    card.classList.toggle("is-expanded", expanded);
    const detail = card.querySelector(".iv-pair-detail");
    const btn = card.querySelector(".iv-pair-expand");
    if (btn) {
      btn.textContent = expanded ? "收起" : "展开详情";
      btn.setAttribute("aria-expanded", String(expanded));
    }
    if (!detail) return;
    detail.classList.toggle("hidden", !expanded);
    if (expanded) {
      if (!detail.innerHTML.trim() || detail.querySelector(".iv-coarse-loading")) {
        detail.innerHTML = renderIvCoarseDetailShell(key);
      }
      loadIvCoarsePairDetail(key);
    }
  });
}

function renderIvPairMonthlyStability(data) {
  const stab = data?.monthly_stability;
  if (!stab?.months?.length) {
    return `<p class="sub">${stab?.note || "无月度稳定性数据"}</p>`;
  }
  const note = stab.note ? `<p class="warn">${stab.note}</p>` : "";
  const blocks = stab.months.map((month) => {
    const cellMap = Object.fromEntries(
      (stab.bins || [])
        .filter((b) => b.i != null && b.j != null)
        .map((b) => [`${b.i},${b.j}`, b.months?.[month] || { obs: 0, bad_rate: 0 }])
    );
    let maxCell = null;
    let maxBr = -1;
    for (const b of stab.bins || []) {
      const c = b.months?.[month];
      if (!c?.obs) continue;
      if (c.bad_rate >= maxBr) {
        maxBr = c.bad_rate;
        maxCell = { i: b.i, j: b.j };
      }
    }
    const total = stab.summary?.months?.[month];
    const sub = total
      ? `${total.obs ?? 0} 样本 · 坏率 ${pct(total.bad_rate ?? 0)}`
      : "";
    return `
      <div class="iv-month-heatmap-block">
        <h4 class="iv-month-title">${month}${sub ? `<span class="sub">${sub}</span>` : ""}</h4>
        ${renderIvGridHeatmap(data, {
          cellMap,
          maxCell,
        })}
      </div>
    `;
  }).join("");

  return `
    ${note}
    <p class="sub bin-hint">每月一张交叉热力图，格式与总览一致；加粗边框为该月最高坏率格。</p>
    <div class="iv-monthly-heatmaps">${blocks}</div>
  `;
}

/** 月度格子单元格：样本数 (坏率%) */
function renderObsBadRateCompactCell(obs, badRate, isMax = false) {
  if (!obs) {
    return `<td class="heatmap-cell heatmap-empty">—</td>`;
  }
  const intensity = Math.min(1, Math.max(0, badRate || 0));
  const alpha = 0.12 + intensity * 0.78;
  const bg = `rgba(59, 130, 246, ${alpha.toFixed(3)})`;
  const textColor = intensity > 0.55 ? "#fff" : "var(--text)";
  const maxCls = isMax ? " heatmap-max-cell" : "";
  return `<td class="heatmap-cell heatmap-compact${maxCls}" style="background:${bg};color:${textColor}">${obs} (${pct(badRate || 0)})</td>`;
}

/** 第 7 步：等频组合规则 — 仅展示用户选中的交叉格子在各月的样本量与坏率 */
function renderIvPairCellMonthlyTable(grid, i, j) {
  const stab = grid?.monthly_stability;
  if (!stab?.months?.length) {
    return `<p class="sub">${stab?.note || "无月度稳定性数据"}</p>`;
  }
  const cellBin = (stab.bins || []).find((b) => b.i === i && b.j === j);
  if (!cellBin) {
    return `<p class="sub">未找到选中格子的月度数据，请返回第 6 步精筛刷新格子。</p>`;
  }
  const binLabel = cellBin.bin || describeIvCellPick(grid, i, j);
  const note = stab.note ? `<p class="warn">${stab.note}</p>` : "";
  const months = stab.months;
  const head = months.map((m) => `<th class="month-head">${m}</th>`).join("");
  const isMax = Boolean(cellBin.is_max_cell);

  const brCells = months.map((m) => {
    const c = cellBin.months?.[m] || {};
    return renderObsBadRateCompactCell(c.obs ?? 0, c.bad_rate ?? 0, isMax);
  }).join("");
  const totalBr = renderObsBadRateCompactCell(
    cellBin.total?.obs ?? 0,
    cellBin.total?.bad_rate ?? 0,
    isMax
  );

  return `
    ${note}
    <div class="stab-block">
      <h4>Train 月度稳定性 · 选中格子</h4>
      <p class="sub iv-grid-footnote">格子：<strong>${binLabel}</strong> · 与第 6 步精筛「按月」热力图同口径；单元格格式为 <strong>样本数 (坏率)</strong>。</p>
      <div class="stability-scroll">
        <table class="data-table stability-table heatmap-table">
          <thead>
            <tr><th>指标</th>${head}<th class="month-head">合计</th></tr>
          </thead>
          <tbody>
            <tr><td><strong>坏率</strong></td>${brCells}${totalBr}</tr>
          </tbody>
        </table>
      </div>
    </div>
  `;
}

async function loadIvPairGridForRule(rule) {
  if (!rule?.pair_key) return null;
  const cached = ivPairDetailCache.get(rule.pair_key);
  if (cached) return cached;
  const combo = ivPairOverview?.combos?.find((c) => c.pair_key === rule.pair_key);
  if (!combo) return null;
  const data = await api(`/api/binning/${getStoredJobId()}/iv-pair-grid/detail`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(buildIvPairDetailPayload(combo, rule.pair_key)),
  });
  ivPairDetailCache.set(rule.pair_key, data);
  return data;
}

function renderIvGridHeatmap(data, opts = {}) {
  const cellMap = opts.cellMap || Object.fromEntries((data.cells || []).map((c) => [`${c.i},${c.j}`, c]));
  const maxCell = opts.maxCell !== undefined ? opts.maxCell : data.max_cell;
  const selectable = Boolean(opts.selectable && opts.pairKey);
  const selectedCells = opts.selectedCells || new Set();
  // rows=变量 a（↓ 行），cols=变量 b（→ 列）
  const colHead = (data.cols || []).map((c) => `<th title="${c.label}">${c.label.length > 12 ? `${c.label.slice(0, 10)}…` : c.label}</th>`).join("");
  const bodyRows = (data.rows || []).map((row) => {
    const cells = (data.cols || []).map((col) => {
      const cell = cellMap[`${row.index},${col.index}`];
      const isMax = maxCell && maxCell.i === row.index && maxCell.j === col.index;
      const picked = selectedCells.has(`${row.index},${col.index}`);
      const pickOpts = selectable
        ? { selectable: true, pairKey: opts.pairKey, i: row.index, j: col.index, selected: picked }
        : null;
      return renderBlueHeatmapCell(cell?.obs ?? 0, cell?.bad_rate ?? 0, isMax, pickOpts);
    }).join("");
    return `<tr><th class="row-head" title="${row.label}">${row.label.length > 12 ? `${row.label.slice(0, 10)}…` : row.label}</th>${cells}</tr>`;
  }).join("");

  const footnote = selectable
    ? "点击格子勾选/取消作为准入条件（可多选）；绿框=已选，蓝框=最高坏率格。行序：数值箱从上到下递增，special 固定在最末行/列。"
    : "格子内上方为样本数、下方为坏率；加粗边框为最高坏率格。行=边界(a)，列=边界(b)。";

  return `
    <div class="stability-scroll iv-grid-scroll">
      <table class="data-table stability-table heatmap-table iv-grid-table${selectable ? " iv-grid-table-pickable" : ""}">
        <thead>
          <tr><th>${data.chinese_a || data.feature_a} ↓ \\ ${data.chinese_b || data.feature_b} →</th>${colHead}</tr>
        </thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>
    <p class="sub iv-grid-footnote">${footnote}</p>
  `;
}

async function loadIvCoarsePairDetail(pairKey) {
  const card = findIvCoarsePairCard(pairKey);
  const panel = card?.querySelector(".iv-pair-detail");
  if (!panel) return;

  const cached = ivPairDetailCache.get(pairKey) || ivPairOverview?.combos?.find((c) => c.pair_key === pairKey)?.grid;
  if (cached) {
    ivPairDetailCache.set(pairKey, cached);
    panel.innerHTML = renderIvCoarseDetailContent(pairKey, cached);
    bindIvCoarseDetailTabs(panel, pairKey);
    return;
  }

  const combo = ivPairOverview?.combos?.find((c) => c.pair_key === pairKey);
  if (!combo) return;

  panel.innerHTML = `<p class="sub iv-coarse-loading">正在加载交叉格子详情…</p>`;
  try {
    const data = await api(`/api/binning/${getStoredJobId()}/iv-pair-grid/detail`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(buildIvPairDetailPayload(combo, pairKey)),
    });
    ivPairDetailCache.set(pairKey, data);
    if (ivCoarseExpandedPair !== pairKey) return;
    panel.innerHTML = renderIvCoarseDetailContent(pairKey, data);
    bindIvCoarseDetailTabs(panel, pairKey);
  } catch (err) {
    panel.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function bindIvCoarseDetailTabs(panel, pairKey) {
  panel.querySelectorAll("[data-iv-coarse-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      ivCoarseDetailTab.set(pairKey, tab.dataset.ivCoarseTab);
      const inner = panel.querySelector(".iv-coarse-detail-inner");
      if (!inner) return;
      inner.querySelectorAll("[data-iv-coarse-tab]").forEach((t) => {
        t.classList.toggle("active", t.dataset.ivCoarseTab === tab.dataset.ivCoarseTab);
      });
      inner.querySelectorAll("[data-iv-coarse-panel]").forEach((p) => {
        p.classList.toggle("hidden", p.dataset.ivCoarsePanel !== tab.dataset.ivCoarseTab);
      });
    });
  });
}

function setIvPairSelected(pairKey, checked) {
  if (checked) ivSelectedPairs.add(pairKey);
  else ivSelectedPairs.delete(pairKey);
  updateIvPairSelectedCount();
  $$(".iv-pair-card").forEach((card) => {
    const on = ivSelectedPairs.has(card.dataset.pairKey);
    card.classList.toggle("is-selected", on);
    const cb = card.querySelector('input[type="checkbox"]');
    if (cb) cb.checked = on;
  });
}

function updateIvPairSelectedCount() {
  const n = ivSelectedPairs.size;
  const countEl = $("#iv-pair-selected-count");
  if (countEl) countEl.textContent = String(n);
  const btn = $("#to-iv-fine");
  if (btn) btn.disabled = n === 0;
  const selectAll = $("#iv-select-all-pairs");
  if (selectAll && ivPairOverview?.combos?.length) {
    selectAll.checked = n > 0 && n === ivPairOverview.combos.length;
    selectAll.indeterminate = n > 0 && n < ivPairOverview.combos.length;
  }
}

function syncIvFineSelectedPairs() {
  for (const k of [...ivFineSelectedPairs]) {
    if (!ivSelectedPairs.has(k)) ivFineSelectedPairs.delete(k);
  }
}

function setIvFinePairSelected(pairKey, checked) {
  if (checked) ivFineSelectedPairs.add(pairKey);
  else ivFineSelectedPairs.delete(pairKey);
  updateIvFineSelectedCount();
  $$(".iv-fine-pair-row").forEach((row) => {
    const on = ivFineSelectedPairs.has(row.dataset.pairKey);
    row.classList.toggle("is-selected", on);
    const cb = row.querySelector(".iv-fine-pair-check");
    if (cb) cb.checked = on;
  });
}

function countIvFineAdmissionCells(pairKeys = [...ivFineSelectedPairs]) {
  let n = 0;
  for (const k of pairKeys) n += getIvPairSelectedCells(k).size;
  return n;
}

function updateIvFineSelectedCount() {
  const pairN = ivFineSelectedPairs.size;
  const cellN = countIvFineAdmissionCells();
  const countEl = $("#iv-fine-selected-count");
  if (countEl) countEl.textContent = String(pairN);
  const cellEl = $("#iv-fine-cell-count");
  if (cellEl) cellEl.textContent = String(cellN);
  const btn = $("#to-step-7-from-iv");
  if (btn) btn.disabled = pairN === 0;
  const combos = (ivPairOverview?.combos || []).filter((c) => ivSelectedPairs.has(c.pair_key));
  const selectAll = $("#iv-fine-select-all-pairs");
  if (selectAll && combos.length) {
    selectAll.checked = pairN > 0 && pairN === combos.length;
    selectAll.indeterminate = pairN > 0 && pairN < combos.length;
  }
}

function ivResolveBinBounds(edges, binIdx, binDef) {
  if (binDef?.is_special) return { special: true, label: binDef.label };
  let lo = binDef?.lo;
  let hi = binDef?.hi;
  if (lo == null && Array.isArray(edges) && edges.length > binIdx) {
    const v = edges[binIdx];
    if (v != null && Number.isFinite(v)) lo = v;
  }
  if (hi == null && Array.isArray(edges) && edges.length > binIdx + 1) {
    const v = edges[binIdx + 1];
    if (v != null && Number.isFinite(v)) hi = v;
  }
  return { lo, hi };
}

function ivBinToConditions(feature, edges, binIdx, binDef) {
  const bounds = ivResolveBinBounds(edges, binIdx, binDef);
  if (bounds.special) {
    const label = bounds.label || binDef?.label;
    if (!label) return [];
    return [{ feature, operator: "in", threshold: 0, values: [label], source_bins: [label] }];
  }
  const conditions = [];
  const { lo, hi } = bounds;
  if (Number.isFinite(lo)) conditions.push({ feature, operator: ">", threshold: lo });
  if (Number.isFinite(hi)) conditions.push({ feature, operator: "<=", threshold: hi });
  return conditions;
}

function ivCellKey(i, j) {
  return `${i},${j}`;
}

function getIvPairSelectedCells(pairKey) {
  return ivPairSelectedCells.get(pairKey) || new Set();
}

function ensureIvPairCellsDefault(pairKey, grid) {
  if (!ivPairSelectedCells.has(pairKey)) {
    ivPairSelectedCells.set(pairKey, new Set());
  }
  const set = ivPairSelectedCells.get(pairKey);
  if (!set.size && grid?.max_cell) {
    set.add(ivCellKey(grid.max_cell.i, grid.max_cell.j));
  }
}

function resetIvPairCellsToMax(pairKey, grid) {
  const set = new Set();
  if (grid?.max_cell) set.add(ivCellKey(grid.max_cell.i, grid.max_cell.j));
  ivPairSelectedCells.set(pairKey, set);
}

function toggleIvFineCell(pairKey, i, j, grid) {
  if (!ivPairSelectedCells.has(pairKey)) ivPairSelectedCells.set(pairKey, new Set());
  const set = ivPairSelectedCells.get(pairKey);
  const key = ivCellKey(i, j);
  if (set.has(key)) set.delete(key);
  else set.add(key);
  refreshIvFineHeatmapView(pairKey, grid);
  updateIvFineCellSummary(pairKey, grid);
  renderIvFinePairList();
}

function selectIvFineMaxCell(pairKey, grid) {
  resetIvPairCellsToMax(pairKey, grid);
  refreshIvFineHeatmapView(pairKey, grid);
  updateIvFineCellSummary(pairKey, grid);
  renderIvFinePairList();
}

function clearIvFineCells(pairKey, grid) {
  ivPairSelectedCells.set(pairKey, new Set());
  refreshIvFineHeatmapView(pairKey, grid);
  updateIvFineCellSummary(pairKey, grid);
  renderIvFinePairList();
}

function renderIvCellPickBar(pairKey, data) {
  const n = getIvPairSelectedCells(pairKey).size;
  return `
    <div class="iv-cell-pick-bar">
      <span class="iv-cell-pick-label">准入格子 <strong id="iv-cell-pick-count">${n}</strong></span>
      <button type="button" class="btn ghost btn-sm" id="iv-pick-max-cell">选用最高坏率格</button>
      <button type="button" class="btn ghost btn-sm" id="iv-clear-cells">清除选择</button>
    </div>
  `;
}

function describeIvCellPick(data, i, j) {
  const rowDef = (data.rows || []).find((r) => r.index === i);
  const colDef = (data.cols || []).find((c) => c.index === j);
  const cell = (data.cells || []).find((c) => c.i === i && c.j === j);
  const rowLbl = rowDef?.label || `行${i}`;
  const colLbl = colDef?.label || `列${j}`;
  const br = cell?.bad_rate != null ? pct(cell.bad_rate) : "—";
  const obs = cell?.obs ?? 0;
  return `${rowLbl} × ${colLbl} · ${obs} 人 · 坏率 ${br}`;
}

function updateIvFineCellSummary(pairKey, data) {
  const el = $("#iv-fine-summary");
  if (!el || !data) return;
  const picks = [...getIvPairSelectedCells(pairKey)];
  if (!picks.length) {
    el.innerHTML = `<p class="sub warn-text">尚未选择准入格子，请在热力图中点击格子，或点「选用最高坏率格」。</p>`;
    return;
  }
  const items = picks.map((key) => {
    const [i, j] = key.split(",").map(Number);
    const reason = ivCellSkipReason(data, i, j);
    const warn = reason ? ` <span class="warn-text">（${reason}）</span>` : "";
    return `<li>${describeIvCellPick(data, i, j)}${warn}</li>`;
  }).join("");
  el.innerHTML = `
    <p class="sub">已选 <strong>${picks.length}</strong> 个准入格：</p>
    <ul class="iv-cell-pick-list">${items}</ul>
  `;
}

function refreshIvFineHeatmapView(pairKey, data) {
  const panel = $("#iv-fine-detail-body")?.querySelector('[data-iv-fine-panel="overview"]');
  if (!panel) return;
  panel.innerHTML = `
    ${renderIvCellPickBar(pairKey, data)}
    ${renderIvGridHeatmap(data, {
      selectable: true,
      pairKey,
      selectedCells: getIvPairSelectedCells(pairKey),
    })}
  `;
  bindIvFineHeatmapCells(pairKey, data);
}

function bindIvFineHeatmapCells(pairKey, data) {
  const panel = $("#iv-fine-detail-body")?.querySelector('[data-iv-fine-panel="overview"]');
  if (!panel) return;

  panel.querySelector("#iv-pick-max-cell")?.addEventListener("click", () => selectIvFineMaxCell(pairKey, data));
  panel.querySelector("#iv-clear-cells")?.addEventListener("click", () => clearIvFineCells(pairKey, data));

  panel.querySelectorAll("[data-iv-cell]").forEach((cell) => {
    cell.addEventListener("click", () => {
      toggleIvFineCell(pairKey, parseInt(cell.dataset.i, 10), parseInt(cell.dataset.j, 10), data);
    });
    cell.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      toggleIvFineCell(pairKey, parseInt(cell.dataset.i, 10), parseInt(cell.dataset.j, 10), data);
    });
  });
}

function formatIvPairComboLabel(comboOrGrid) {
  if (!comboOrGrid) return "";
  const a = comboOrGrid.chinese_a || comboOrGrid.feature_a || "";
  const b = comboOrGrid.chinese_b || comboOrGrid.feature_b || "";
  return a && b ? `${a} × ${b}` : "";
}

function canIvCellBeAdmission(grid, i, j) {
  if (!grid) return false;
  const rowDef = (grid.rows || []).find((r) => r.index === i);
  const colDef = (grid.cols || []).find((c) => c.index === j);
  const conditions = [
    ...ivBinToConditions(grid.feature_a, grid.edges_a, i, rowDef),
    ...ivBinToConditions(grid.feature_b, grid.edges_b, j, colDef),
  ];
  return conditions.length > 0;
}

function ivCellSkipReason(grid, i, j) {
  const cell = (grid.cells || []).find((c) => c.i === i && c.j === j);
  if (!(cell?.obs > 0)) return "该格 0 样本";
  if (!canIvCellBeAdmission(grid, i, j)) return "无法生成准入条件";
  return "";
}

function ivCellToCompoundRule(grid, pairKey, i, j) {
  const rowDef = (grid.rows || []).find((r) => r.index === i);
  const colDef = (grid.cols || []).find((c) => c.index === j);
  const conditions = [
    ...ivBinToConditions(grid.feature_a, grid.edges_a, i, rowDef),
    ...ivBinToConditions(grid.feature_b, grid.edges_b, j, colDef),
  ];
  if (!conditions.length) return null;
  const cellData = (grid.cells || []).find((c) => c.i === i && c.j === j);
  return {
    rule_id: `iv_${pairKey}_${i}_${j}`,
    rule_type: "compound",
    rule_display: formatCompoundRuleCompact(conditions),
    conditions,
    features: [grid.feature_a, grid.feature_b],
    feature_labels: [grid.chinese_a || grid.feature_a, grid.chinese_b || grid.feature_b],
    pair_title: formatIvPairComboLabel(grid),
    distinct_feature_count: 2,
    n_conditions: conditions.length,
    bad_rate: cellData?.bad_rate ?? 0,
    hit_count: cellData?.obs ?? 0,
    train_bad_rate: cellData?.bad_rate ?? 0,
    grid_iv_total: grid.grid_iv_total,
    pair_key: pairKey,
    iv_pair: true,
    cell_i: i,
    cell_j: j,
    cell_label: describeIvCellPick(grid, i, j),
  };
}

function buildIvCompoundRulesFromPairs(pairKeys) {
  const rules = [];
  const skippedPairs = [];
  let portfolioBad = 0;
  for (const pairKey of pairKeys) {
    const combo = ivPairOverview?.combos?.find((c) => c.pair_key === pairKey);
    const grid = ivPairDetailCache.get(pairKey) || combo?.grid;
    if (!grid) {
      skippedPairs.push({ pairKey, combo, reason: "no_grid" });
      continue;
    }
    if (!portfolioBad && grid.overall_bad_rate != null) portfolioBad = grid.overall_bad_rate;
    ensureIvPairCellsDefault(pairKey, grid);
    const cells = getIvPairSelectedCells(pairKey);
    let pairRuleCount = 0;
    for (const key of cells) {
      const [i, j] = key.split(",").map(Number);
      const rule = ivCellToCompoundRule(grid, pairKey, i, j);
      if (rule) {
        rules.push(rule);
        pairRuleCount += 1;
      }
    }
    if (!pairRuleCount) {
      skippedPairs.push({ pairKey, combo, grid, reason: "no_valid_cell" });
    }
  }
  rules.sort(
    (a, b) => (b.grid_iv_total || 0) - (a.grid_iv_total || 0) || (b.bad_rate || 0) - (a.bad_rate || 0)
  );
  if (portfolioBad) rules.forEach((r) => { r.portfolio_bad_rate = portfolioBad; });
  return { rules, skippedPairs };
}

async function ensureIvPairDetails(pairKeys) {
  const pending = pairKeys.filter((k) => {
    const combo = ivPairOverview?.combos?.find((c) => c.pair_key === k);
    return combo && !ivPairDetailCache.has(k);
  });
  await Promise.all(pending.map(async (pairKey) => {
    const combo = ivPairOverview?.combos?.find((c) => c.pair_key === pairKey);
    if (!combo) return;
    try {
      const data = await api(`/api/binning/${getStoredJobId()}/iv-pair-grid/detail`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(buildIvPairDetailPayload(combo, pairKey)),
      });
      ivPairDetailCache.set(pairKey, data);
    } catch {
      /* 保留 overview 缓存 */
    }
  }));
}

function initIvFineStep() {
  showStep6SubView("iv-fine");
  ivPairCustomEdges.clear();
  syncIvFineSelectedPairs();
  renderIvFinePairList();
  const first = [...ivFineSelectedPairs][0] || [...ivSelectedPairs][0];
  if (first) openIvPairDetail(first);
}

function renderIvFinePairList() {
  const el = $("#iv-fine-pair-list");
  const countEl = $("#iv-fine-pair-count");
  const combos = (ivPairOverview?.combos || []).filter((c) => ivSelectedPairs.has(c.pair_key));
  if (countEl) countEl.textContent = String(combos.length);
  updateIvFineSelectedCount();
  if (!el) return;
  if (!combos.length) {
    el.innerHTML = `<p class="sub">请返回初筛勾选组合。</p>`;
    return;
  }
  el.innerHTML = combos.map((c) => {
    const checked = ivFineSelectedPairs.has(c.pair_key);
    const cellN = getIvPairSelectedCells(c.pair_key).size;
    const grid = ivPairDetailCache.get(c.pair_key) || c.grid;
    const invalidCell = [...getIvPairSelectedCells(c.pair_key)].some((key) => {
      const [i, j] = key.split(",").map(Number);
      return ivCellSkipReason(grid, i, j);
    });
    const cellBadge = cellN
      ? `<span class="badge ${invalidCell ? "warn" : "ok"}">${cellN} 格准入${invalidCell ? "!" : ""}</span>`
      : `<span class="badge warn">未选格</span>`;
    return `
    <div class="feature-row iv-pair-row iv-fine-pair-row${ivActivePairKey === c.pair_key ? " active" : ""}${checked ? " is-selected" : ""}" data-pair-key="${c.pair_key}">
      <label class="feature-check">
        <input type="checkbox" class="iv-fine-pair-check" data-pair-key="${c.pair_key}" ${checked ? "checked" : ""} />
      </label>
      <div class="feature-row-main">
        <span class="cn">${c.chinese_a || c.feature_a} × ${c.chinese_b || c.feature_b}</span>
      </div>
      <div class="feature-row-meta">
        <span class="iv-total-val">${formatIvPairIvTotal(c)}</span>
        <span class="hit">${c.row_bins}×${c.col_bins}</span>
        ${cellBadge}
      </div>
    </div>
  `;
  }).join("");
  el.querySelectorAll(".iv-fine-pair-check").forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
    cb.addEventListener("change", (e) => {
      setIvFinePairSelected(e.target.dataset.pairKey, e.target.checked);
    });
  });
  el.querySelectorAll(".iv-fine-pair-row").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.type === "checkbox") return;
      openIvPairDetail(row.dataset.pairKey);
    });
  });
}

async function openIvPairDetail(pairKey, opts = {}) {
  ivActivePairKey = pairKey;
  $$(".iv-pair-row").forEach((r) => {
    r.classList.toggle("active", r.dataset.pairKey === pairKey);
  });
  const combo = ivPairOverview?.combos?.find((c) => c.pair_key === pairKey);
  if (!combo) return;

  $("#iv-fine-title").textContent = `${combo.chinese_a || combo.feature_a} × ${combo.chinese_b || combo.feature_b}`;
  const custom = ivPairCustomEdges.get(pairKey);
  const cached = ivPairDetailCache.get(pairKey) || combo.grid;
  const hasCustomEdges = Boolean(ivInnerCuts(custom?.edges_a) || ivInnerCuts(custom?.edges_b));

  if (cached && !hasCustomEdges && !opts.forceReload) {
    ivPairDetailCache.set(pairKey, cached);
    $("#iv-fine-subtitle").textContent = `${cached.sample_count} 样本 · IV(total) ${formatIvPairIvTotal(cached)}`;
    renderIvPairDetail(pairKey, cached, opts);
    return;
  }

  $("#iv-fine-subtitle").textContent = "加载交叉格子…";
  $("#iv-fine-detail-body").innerHTML = `<p class="sub">加载中…</p>`;
  $("#iv-fine-summary").innerHTML = `<p class="sub">${combo.feature_a} × ${combo.feature_b}</p>`;

  try {
    const data = await api(`/api/binning/${getStoredJobId()}/iv-pair-grid/detail`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(buildIvPairDetailPayload(combo, pairKey)),
    });
    ivPairDetailCache.set(pairKey, data);
    $("#iv-fine-subtitle").textContent = `${data.sample_count} 样本 · IV(total) ${formatIvPairIvTotal(data)}`;
    renderIvPairDetail(pairKey, data, opts);
  } catch (err) {
    $("#iv-fine-detail-body").innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function preserveIvPairCellsAfterRefresh(pairKey, grid, prevCells) {
  const validRows = new Set((grid.rows || []).map((r) => r.index));
  const validCols = new Set((grid.cols || []).map((c) => c.index));
  const kept = new Set();
  for (const key of prevCells || []) {
    const [i, j] = key.split(",").map(Number);
    if (validRows.has(i) && validCols.has(j)) kept.add(key);
  }
  if (kept.size) {
    ivPairSelectedCells.set(pairKey, kept);
  } else if (grid?.max_cell) {
    resetIvPairCellsToMax(pairKey, grid);
  } else {
    ivPairSelectedCells.set(pairKey, new Set());
  }
}

function renderIvPairDetail(pairKey, data, opts = {}) {
  if (opts.resetCells) resetIvPairCellsToMax(pairKey, data);
  else if (opts.preserveCells) preserveIvPairCellsAfterRefresh(pairKey, data, opts.preserveCells);
  else ensureIvPairCellsDefault(pairKey, data);
  $("#iv-fine-subtitle").textContent = `${data.sample_count} 样本 · IV(total) ${formatIvPairIvTotal(data)}`;

  const edgeEditorA = renderIvEdgeEditor(data.feature_a, data.chinese_a, data.edges_a, "a", pairKey);
  const edgeEditorB = renderIvEdgeEditor(data.feature_b, data.chinese_b, data.edges_b, "b", pairKey);

  $("#iv-fine-detail-body").innerHTML = `
    <div class="iv-edge-editors">
      ${edgeEditorA}
      ${edgeEditorB}
      <button type="button" class="btn primary btn-sm" id="iv-refresh-grid">刷新格子</button>
    </div>
    <div class="detail-tabs iv-fine-detail-tabs">
      <button type="button" class="detail-tab active" data-iv-fine-tab="overview">总览</button>
      <button type="button" class="detail-tab" data-iv-fine-tab="monthly">按月</button>
    </div>
    <div class="iv-fine-tab-panel" data-iv-fine-panel="overview">
      ${renderIvCellPickBar(pairKey, data)}
      ${renderIvGridHeatmap(data, {
        selectable: true,
        pairKey,
        selectedCells: getIvPairSelectedCells(pairKey),
      })}
    </div>
    <div class="iv-fine-tab-panel hidden" data-iv-fine-panel="monthly">
      ${renderIvPairMonthlyStability(data)}
    </div>
    <p class="sub">修改边界后点「刷新格子」；在总览热力图中点击格子选择准入条件（可多选）。</p>
  `;

  updateIvFineCellSummary(pairKey, data);
  bindIvFineHeatmapCells(pairKey, data);

  $("#iv-fine-detail-body")?.querySelectorAll("[data-iv-fine-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const body = $("#iv-fine-detail-body");
      body.querySelectorAll("[data-iv-fine-tab]").forEach((t) => {
        t.classList.toggle("active", t === tab);
      });
      body.querySelectorAll("[data-iv-fine-panel]").forEach((p) => {
        p.classList.toggle("hidden", p.dataset.ivFinePanel !== tab.dataset.ivFineTab);
      });
    });
  });

  $("#iv-refresh-grid")?.addEventListener("click", () => refreshIvPairGrid(pairKey));
  renderIvFinePairList();
}

function renderIvEdgeEditor(feat, cn, edges, axis, pairKey) {
  const inner = (edges || []).slice(1, -1);
  return `
    <div class="field iv-edge-field" data-axis="${axis}" data-pair-key="${pairKey}">
      <label>${cn || feat} 分箱边界（${axis}）</label>
      <input type="text" class="iv-edge-input" data-axis="${axis}" value="${inner.map((v) => v ?? "").join(", ")}" placeholder="逗号分隔，如 100, 200, 500" />
      <small>修改内部切分点，首尾为 -∞ / +∞ 固定</small>
    </div>
  `;
}

function ivInnerCuts(raw) {
  if (!raw?.length) return undefined;
  const inner = (raw.length > 2 && !Number.isFinite(raw[0])) ? raw.slice(1, -1) : raw;
  const finite = inner.filter((n) => Number.isFinite(n));
  return finite.length ? finite : undefined;
}

function buildIvPairDetailPayload(combo, pairKey) {
  const payload = {
    feature_a: combo.feature_a,
    feature_b: combo.feature_b,
  };
  const custom = ivPairCustomEdges.get(pairKey);
  const cutsA = ivInnerCuts(custom?.edges_a);
  const cutsB = ivInnerCuts(custom?.edges_b);
  if (cutsA) payload.bin_edges_a = cutsA;
  if (cutsB) payload.bin_edges_b = cutsB;
  return payload;
}

function parseIvEdgeInput(raw) {
  if (!String(raw || "").trim()) return null;
  const parts = String(raw).split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
  const nums = parts.map((p) => Number(p)).filter((n) => Number.isFinite(n));
  if (!nums.length) return null;
  return nums;
}

async function refreshIvPairGrid(pairKey) {
  const combo = ivPairOverview?.combos?.find((c) => c.pair_key === pairKey);
  if (!combo) return;
  const prevCells = new Set(getIvPairSelectedCells(pairKey));
  const edgesA = parseIvEdgeInput($('.iv-edge-input[data-axis="a"]')?.value);
  const edgesB = parseIvEdgeInput($('.iv-edge-input[data-axis="b"]')?.value);
  const prev = ivPairCustomEdges.get(pairKey) || {};
  ivPairCustomEdges.set(pairKey, {
    edges_a: edgesA ?? prev.edges_a,
    edges_b: edgesB ?? prev.edges_b,
  });
  await openIvPairDetail(pairKey, { forceReload: true, preserveCells: prevCells });
}

function renderMvMineSummary() {
  const el = $("#mv-mine-summary");
  if (!el || !multivariateData) return;
  el.classList.remove("hidden");
  const nIn = multivariateData.input_features?.length || 0;
  const nUse = multivariateData.mined_features?.length || 0;
  const raw = multivariateData.raw_rule_count || 0;
  const cand = multivariateData.candidate_count || 0;
  const deduped = multivariateData.deduped_count || 0;
  const skippedShort = multivariateData.skipped_short_count || 0;
  const combK = multivariateData.comb_k ?? mvCombK;
  const ms = multivariateData.mining_stats || {};
  const report = multivariateData.feature_report || [];

  const featureTable = report.length ? `
    <h4 style="margin:1rem 0 .5rem;font-size:.95rem">变量状态明细（每个变量一条说明）</h4>
    <div class="threshold-table-wrap">
      <table class="data-table mv-diag-table">
        <thead>
          <tr>
            <th>变量</th>
            <th>状态</th>
            <th>非缺失占比</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          ${report.map((r) => `
            <tr class="${r.status === "预处理剔除" ? "diag-row-warn" : ""}">
              <td title="${r.feature}">${r.chinese_name || r.feature}</td>
              <td>${r.status}</td>
              <td>${r.status === "参与挖掘" ? pct(r.non_null_rate || 0) : "—"}</td>
              <td style="text-align:left;font-size:.78rem">${r.reason || "—"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  ` : "";

  const comboRows = (ms.combo_samples || []).map((c) => `
    <tr>
      <td>${(c.combo || []).join(" + ")}</td>
      <td>${c.rows_used ?? c.rows_total ?? "—"}</td>
      <td style="text-align:left;font-size:.78rem">${c.result || "—"}</td>
    </tr>
  `).join("");

  const comboTable = comboRows ? `
    <h4 style="margin:1rem 0 .5rem;font-size:.95rem">K 组合挖树明细（最多展示 12 组）</h4>
    <div class="threshold-table-wrap">
      <table class="data-table mv-diag-table">
        <thead><tr><th>变量组合</th><th>可用行数</th><th>结果</th></tr></thead>
        <tbody>${comboRows}</tbody>
      </table>
    </div>
  ` : "";

  const zeroHint = cand === 0 ? `
    <p class="warn">${ms.zero_rules_hint || ms.error || "未找到符合要求的组合规则。"}</p>
    ${ms.combos_planned != null ? `<p class="sub">计划挖掘 ${ms.combos_planned} 组 · 成功训树 ${ms.combos_trained || 0} 组 · 叶规则 ${ms.raw_paths_extracted || 0} 条（Classifier 深度${ms.tree_depth || 3}）</p>` : ""}
    ${skippedShort ? `<p class="sub">${skippedShort} 条路径未能解析为有效条件。</p>` : ""}
  ` : "";

  el.innerHTML = `
    <h3>挖掘完成</h3>
    <p>您挑选 <strong>${nIn}</strong> 个 · 可挖树 <strong>${nUse}</strong> 个
    · 叶规则 <strong>${raw}</strong> 条 → 候选 <strong>${cand}</strong> 条
    ${deduped ? `（去重 ${deduped} 条）` : ""}</p>
    <p class="sub"><strong>K=${combK}</strong> · 树深度 <strong>${multivariateData.params?.tree_depth ?? ms.tree_depth ?? 3}</strong>：C(n,${combK}) 组合训树并提取叶规则（K=2 时穷举全部两两组合）。</p>
    ${zeroHint}
    ${featureTable}
    ${comboTable}
  `;
}

let mvProgressTimers = [];
let mvSimulatedProgress = 0;

const MV_PROGRESS_HINTS = [
  "正在读取 Train 数据…",
  "正在按 K 个变量一组做组合…",
  "正在按 C(n,K) 组合训 Classifier 决策树…",
  "正在提取所有叶节点规则（lift 筛选）…",
  "正在计算每条规则的命中人数与坏率…",
];

function setMvProgress(pct) {
  const clamped = Math.max(0, Math.min(100, pct));
  mvSimulatedProgress = clamped;
  const fill = $("#mv-progress-fill");
  const cat = $("#mv-progress-cat");
  if (fill) fill.style.width = `${clamped}%`;
  if (cat) cat.style.left = `${clamped}%`;
}

function startMvProgressAnimation() {
  stopMvProgressAnimation();
  setMvProgress(3);
  let dotsIdx = 0;
  let hintIdx = 0;
  mvProgressTimers.push(setInterval(() => {
    if (mvSimulatedProgress < 90) {
      const step = Math.max(0.5, (92 - mvSimulatedProgress) / 12);
      setMvProgress(mvSimulatedProgress + step);
    }
  }, 400));
  mvProgressTimers.push(setInterval(() => {
    dotsIdx = (dotsIdx + 1) % 4;
    const el = $("#mv-progress-dots");
    if (el) el.textContent = ["", ".", "..", "..."][dotsIdx];
  }, 450));
  mvProgressTimers.push(setInterval(() => {
    hintIdx = (hintIdx + 1) % MV_PROGRESS_HINTS.length;
    const el = $("#mv-progress-hint");
    if (el) el.textContent = MV_PROGRESS_HINTS[hintIdx];
  }, 2200));
}

function stopMvProgressAnimation() {
  mvProgressTimers.forEach(clearInterval);
  mvProgressTimers = [];
}

async function finishMvProgressAnimation() {
  setMvProgress(100);
  const hint = $("#mv-progress-hint");
  const dots = $("#mv-progress-dots");
  if (hint) hint.textContent = "挖掘完成！";
  if (dots) dots.textContent = "";
  await new Promise((r) => setTimeout(r, 400));
  stopMvProgressAnimation();
}

async function runMultivariateMining(opts = {}) {
  const fromModal = Boolean(opts.fromModal);
  const { k, tree_depth } = readMvMiningParams(fromModal);
  mvCombK = k;
  mvTreeDepth = tree_depth;
  applyMvMiningParamsToInputs({ comb: k, tree_depth });
  mvMiningInProgress = true;

  const errEl = $("#mv-mine-error");
  const progressEl = $("#mv-mine-progress");
  const summaryEl = $("#mv-mine-summary");
  errEl?.classList.add("hidden");
  summaryEl?.classList.add("hidden");
  progressEl?.classList.remove("hidden");
  $("#to-step-6-thresh") && ($("#to-step-6-thresh").disabled = true);
  startMvProgressAnimation();

  const jobId = getStoredJobId();
  if (!jobId || !multivariatePoolFeatures.length) {
    errEl.textContent = "缺少分箱任务或未选择特征，请返回第 5 步勾选变量";
    errEl.classList.remove("hidden");
    progressEl.classList.add("hidden");
    stopMvProgressAnimation();
    mvMiningInProgress = false;
    return;
  }
  if (multivariatePoolFeatures.length < k) {
    errEl.textContent = `已选 ${multivariatePoolFeatures.length} 个变量，K=${k} 过大。请减少 K 或返回上一步多选变量。`;
    errEl.classList.remove("hidden");
    progressEl.classList.add("hidden");
    stopMvProgressAnimation();
    mvMiningInProgress = false;
    return;
  }

  try {
    const data = await api(`/api/binning/${jobId}/multivariate/mine`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        features: multivariatePoolFeatures,
        comb: k,
        tree_depth,
        max_leaf_nodes: MV_DEFAULT_MAX_LEAF_NODES,
        min_samples_leaf_frac: 0.005,
        lift_threshold: 1.0,
        combo_random: k === 2 ? 1 : 0.1,
        sample_multiple: 3,
        need_rm_narow: false,
        skip_feature_filter: true,
      }),
    });
    await finishMvProgressAnimation();
    multivariateData = data;
    selectedCompoundRules = new Set();
    progressEl.classList.add("hidden");
    renderMvMineSummary();
    if ($("#to-step-6-thresh")) $("#to-step-6-thresh").disabled = false;
    if (!data.candidate_count) {
      errEl.textContent = "本次未从决策树解析出规则路径，请调整 K/变量后重挖；也可点「下一步」仅用单变量。";
      errEl.classList.remove("hidden");
    }
  } catch (err) {
    stopMvProgressAnimation();
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
    progressEl.classList.add("hidden");
    $("#to-step-6-thresh") && ($("#to-step-6-thresh").disabled = true);
  } finally {
    mvMiningInProgress = false;
  }
}

// ——— Step 7: Chain pick (serial singles + compound rules) ———

function setSerialFeatureSelected(feature, checked) {
  if (checked) {
    serialSelectedFeatures.add(feature);
    const meta = findFeatureMeta(feature);
    if (meta?.value_type === "categorical" && !getFeatureRule(feature).userEdited) {
      const bins = resolveHighBadBins(feature);
      if (setCategoricalAllSelected(feature, bins)) {
        refreshSerialFeatureRuleLine(feature);
        syncSerialCategoricalDetailCheckboxes(feature);
      }
    }
  } else {
    serialSelectedFeatures.delete(feature);
  }
  saveFeatureState();
  markSerialStale();
}

function sortedSerialClusters(clusters) {
  const key = chainSortMode === "hit_count" ? "hit_count" : "max_bad_rate";
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

function renderSerialFeatureClusters(clusters) {
  const el = $("#serial-feature-clusters");
  const countEl = $("#serial-review-count");
  if (!el) return;
  const pool = clusters ?? getSerialPickClusters();
  if (!pool?.length) {
    const iv = getScreeningMode() === "iv_equal_freq";
    el.innerHTML = iv
      ? `<p class="sub">当前坏率/命中阈值下无单变量候选。请调高上方「拒绝坏率阈值」或调低「最少拒绝人数」后点「刷新候选」。</p>`
      : `<p class="sub">当前阈值下无候选变量，请返回第 4 步调整阈值。</p>`;
    if (countEl) countEl.textContent = "0";
    return;
  }
  const total = pool.reduce((n, g) => n + g.features.length, 0);
  if (countEl) countEl.textContent = String(total);

  const sorted = sortedSerialClusters(pool);
  el.innerHTML = sorted.map((g) => `
    <div class="feature-cluster" data-group="${g.group_id}">
      <div class="feature-cluster-title">${g.group_name}（${g.features.length}）</div>
      ${g.features.map((f) => `
        <div class="feature-row serial-feature-row${serialActiveFeature === f.feature ? " active" : ""}" data-feature="${f.feature}">
          <input type="checkbox" data-feature="${f.feature}" ${serialSelectedFeatures.has(f.feature) ? "checked" : ""} />
          <div class="feature-row-main">
            ${renderFeatureRuleLine(f)}
            ${f.manual_include ? '<span class="manual-badge">手动</span>' : ""}
            <span class="cn">${f.chinese_name}</span>
          </div>
          <div class="feature-row-meta">
            <span class="rate" title="${formatFeatureMetricTitle(f)}">${formatFeatureBadRate(f)}</span>
            <span class="${effectClass(f.effect_label)}">${f.effect_label}</span>
            <span class="hit">${formatFeatureHitCount(f)}</span>
          </div>
        </div>
      `).join("")}
    </div>
  `).join("");

  el.querySelectorAll(".rule-op, .rule-threshold").forEach((input) => {
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("change", (e) => onSerialRuleInputChange(e.target.dataset.feature));
    if (input.classList.contains("rule-threshold")) {
      input.addEventListener("input", (e) => onSerialRuleInputChange(e.target.dataset.feature));
    }
  });

  el.querySelectorAll(".serial-feature-row").forEach((row) => {
    const feat = row.dataset.feature;
    row.querySelector('input[type="checkbox"]').addEventListener("click", (e) => {
      e.stopPropagation();
      setSerialFeatureSelected(feat, e.target.checked);
      updateChainPickHeaderCounts();
      scheduleChainPreview();
    });
    row.addEventListener("click", (e) => {
      if (e.target.type === "checkbox") return;
      openSerialPickFeatureDetail(feat);
    });
  });
  updateSerialSelectAllCheckbox();
}

function refreshSerialFeatureRuleLine(feature) {
  const meta = findFeatureMetaByName(feature);
  if (!meta) return;
  const row = document.querySelector(`#serial-feature-clusters .serial-feature-row[data-feature="${CSS.escape(feature)}"] .feature-row-main`);
  if (!row) return;
  const cn = row.querySelector(".cn");
  row.innerHTML = `${renderFeatureRuleLine(meta)}${meta.manual_include ? '<span class="manual-badge">手动</span>' : ""}<span class="cn">${meta.chinese_name}</span>`;
}

function onSerialRuleInputChange(feature) {
  onRuleInputChange(feature);
  refreshSerialFeatureRuleLine(feature);
  if (serialSelectedFeatures.has(feature)) scheduleChainPreview();
}

function syncSerialCategoricalDetailCheckboxes(feature) {
  syncCategoricalDetailCheckboxes(feature);
}

function updateMvSelectAllCheckbox() {
  const cb = $("#mv-select-all-rules");
  if (!cb) return;
  const all = getFilteredMvRules();
  if (!all.length) {
    cb.checked = false;
    cb.indeterminate = false;
    cb.disabled = true;
    return;
  }
  cb.disabled = false;
  const ids = all.map((r) => r.rule_id);
  const n = ids.filter((id) => selectedCompoundRules.has(id)).length;
  cb.checked = n > 0 && n === ids.length;
  cb.indeterminate = n > 0 && n < ids.length;
}

function updateSerialSelectAllCheckbox() {
  const cb = $("#serial-select-all-features");
  if (!cb) return;
  const all = getAllSerialCandidateFeatures();
  if (!all.length) {
    cb.checked = false;
    cb.indeterminate = false;
    cb.disabled = true;
    return;
  }
  cb.disabled = false;
  const n = serialSelectedFeatures.size;
  cb.checked = n > 0 && n === all.length;
  cb.indeterminate = n > 0 && n < all.length;
}

function getSerialPreviewRules() {
  const byFeature = new Map();
  const putRule = (f) => {
    if (!f) return;
    const r = getFeatureRule(f);
    const rule = { feature: f, operator: r.operator, threshold: Number(r.threshold) || 0 };
    if (r.source_bins?.length && !r.userEdited) rule.source_bins = [...r.source_bins];
    if (r.operator === "in") {
      rule.values = [...(r.values || [])];
      if (!rule.values.length) return;
    }
    byFeature.set(f, rule);
  };
  for (const f of serialSelectedFeatures) putRule(f);
  return [...byFeature.values()];
}

function getChainPreviewPayload() {
  return {
    singles: getSerialPreviewRules(),
    compounds: buildCompoundRulesPayload(),
  };
}

function scheduleChainPreview() {
  clearTimeout(chainPreviewTimer);
  chainPreviewTimer = setTimeout(() => refreshChainPreview(), 280);
}

async function refreshChainPreview() {
  const card = $("#chain-impact-card");
  if (!card || !getStoredJobId()) return;
  chainPreviewAbort?.abort();
  chainPreviewAbort = new AbortController();
  const { signal } = chainPreviewAbort;
  const { singles, compounds } = getChainPreviewPayload();
  const snapshot = JSON.stringify({ singles, compounds });
  const hasRules = singles.length > 0 || compounds.length > 0;

  try {
    const data = await api(`/api/binning/${getStoredJobId()}/multivariate/reject-preview`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ compound_rules: compounds, single_rules: singles }),
      signal,
    });
    if (JSON.stringify(getChainPreviewPayload()) !== snapshot) return;
    if (!hasRules) {
      card.innerHTML = renderImpactBaselineOnly(data);
      return;
    }
    const scope = `串联已选 ${serialSelectedFeatures.size} 个单变量 · ${selectedCompoundRules.size} 条组合（并集拒绝）`;
    card.innerHTML = renderMixedImpactCard(data, scope, singles.length + compounds.length);
  } catch (err) {
    if (err.name === "AbortError") return;
    card.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function renderMixedImpactCard(data, scope, ruleCount) {
  const b = data.baseline;
  const a = data.after;
  const r = data.rejected;
  const perRuleHtml = (data.per_rule || []).map((pr) => (
    `<li><strong>${pr.rule_display}</strong> → 拒绝 <strong>${pr.hit_count}</strong> 人 · 坏率 <strong>${pct(pr.bad_rate || 0)}</strong></li>`
  )).join("");
  const perRuleSum = (data.per_rule || []).reduce((n, pr) => n + (pr.hit_count || 0), 0);
  const unionHint = (data.per_rule || []).length > 1 && perRuleSum > (r.count || 0)
    ? `<p class="sub impact-union-hint">各规则命中 ${perRuleSum} 人次，并集去重后拒绝 ${r.count} 人（同一人可被多条规则同时命中）</p>`
    : "";
  return `
    <p class="impact-scope">${scope} · Train 全量 · 共 ${ruleCount} 条预览</p>
    ${unionHint}
    <div class="impact-section">
      <h4>拒绝前（Train 全量）</h4>
      <div class="impact-grid">
        <div class="impact-stat"><span class="label">样本量</span><span class="val">${b.count.toLocaleString()}</span></div>
        <div class="impact-stat"><span class="label">坏率</span><span class="val">${pct(b.bad_rate)}</span></div>
        <div class="impact-stat"><span class="label">金额逾期率</span><span class="val">${pct(b.money_bad_rate)}</span></div>
      </div>
    </div>
    <div class="impact-section">
      <h4>被拒绝样本</h4>
      <div class="impact-grid">
        <div class="impact-stat"><span class="label">样本量</span><span class="val warn">${(r.count || 0).toLocaleString()}</span></div>
        <div class="impact-stat"><span class="label">坏率</span><span class="val warn">${pct(r.bad_rate || 0)}</span></div>
        <div class="impact-stat"><span class="label">金额逾期率</span><span class="val">${pct(r.money_bad_rate || 0)}</span></div>
      </div>
    </div>
    <div class="impact-section">
      <h4>拒绝后（剩余样本）</h4>
      <div class="impact-grid">
        <div class="impact-stat"><span class="label">样本量</span><span class="val good">${a.count.toLocaleString()}</span></div>
        <div class="impact-stat"><span class="label">坏率</span><span class="val ${a.bad_rate < b.bad_rate ? "good" : "warn"}">${pct(a.bad_rate)}</span></div>
        <div class="impact-stat"><span class="label">金额逾期率</span><span class="val">${pct(a.money_bad_rate)}</span></div>
      </div>
    </div>
    ${perRuleHtml ? `<ul class="impact-rules-list">${perRuleHtml}</ul>` : ""}
  `;
}

async function openSerialPickFeatureDetail(feature) {
  setChainAccordion("single");
  serialActiveFeature = feature;
  activeCompoundRuleId = "";
  $$("#serial-feature-clusters .serial-feature-row").forEach((r) => {
    r.classList.toggle("active", r.dataset.feature === feature);
  });
  $$("#mv-rule-list .mv-rule-row").forEach((r) => r.classList.remove("active"));
  $("#chain-pick-stage")?.classList.add("is-split");
  $("#serial-feature-detail-panel")?.setAttribute("aria-hidden", "false");
  $("#mv-detail-panel")?.setAttribute("aria-hidden", "true");

  const threshold = getReviewThreshold();
  const cached = lastFeatureDetail.get(feature);
  if (cached?.bins?.train?.length) {
    applySerialPickFeatureDetail(feature, cached, threshold);
    return;
  }

  $("#serial-pick-detail-title").textContent = `${feature} · 加载中…`;
  $("#serial-pick-detail-subtitle").textContent = "";
  $("#serial-pick-detail-bins").innerHTML = `<p class="sub">加载分箱明细…</p>`;
  $("#serial-pick-detail-stability").innerHTML = "";
  $$("#serial-pick-detail-tabs .detail-tab").forEach((b) => b.classList.remove("active"));
  $$("#serial-pick-detail-tabs .detail-tab")[0]?.classList.add("active");
  $("#serial-pick-detail-bins").classList.remove("hidden");
  $("#serial-pick-detail-stability").classList.add("hidden");

  try {
    const data = await api(
      `/api/binning/${getStoredJobId()}/feature-detail?${new URLSearchParams({
        feature,
        bad_rate_threshold: String(threshold),
        include_stability: "false",
      })}`,
      { headers: headers() }
    );
    lastFeatureDetail.set(feature, data);
    applySerialPickFeatureDetail(feature, data, threshold);
  } catch (err) {
    $("#serial-pick-detail-bins").innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function applySerialPickFeatureDetail(feature, data, threshold) {
  const meta = findFeatureMetaByName(feature);
  $("#serial-pick-detail-title").textContent = meta?.chinese_name || feature;
  $("#serial-pick-detail-subtitle").textContent = meta?.rule_display || feature;
  renderBinTables(data, threshold, $("#serial-pick-detail-bins"));
  $("#serial-pick-detail-stability").innerHTML = "";
}

async function loadSerialPickFeatureStability(feature) {
  const el = $("#serial-pick-detail-stability");
  if (el) el.innerHTML = `<p class="sub">正在加载月度稳定性…</p>`;
  const threshold = getReviewThreshold();
  try {
    const data = await api(
      `/api/binning/${getStoredJobId()}/feature-detail?${new URLSearchParams({
        feature,
        bad_rate_threshold: String(threshold),
        include_stability: "true",
      })}`,
      { headers: headers() }
    );
    lastFeatureDetail.set(feature, data);
    if (el) {
      el.innerHTML = `
        ${data.stability_note ? `<p class="warn">${data.stability_note}</p>` : ""}
        ${renderStabilityCheckHint(data.stability_check)}
        <p class="sub bin-hint">坏率（蓝）与金额逾期率（绿）列带条形图。</p>
        <div class="stability-panels">
          <div><h4>Train 月度稳定性</h4><div class="stability-scroll">${renderStabilityTable(data.stability?.train)}</div></div>
          <div><h4>Test 月度稳定性</h4><div class="stability-scroll">${renderStabilityTable(data.stability?.test)}</div></div>
        </div>`;
    }
  } catch (err) {
    if (el) el.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function closeSerialPickDetail() {
  serialActiveFeature = "";
  if (!activeCompoundRuleId) {
    $("#chain-pick-stage")?.classList.remove("is-split");
    $("#serial-feature-detail-panel")?.setAttribute("aria-hidden", "true");
  } else {
    $("#serial-feature-detail-panel")?.setAttribute("aria-hidden", "true");
  }
  $$("#serial-feature-clusters .serial-feature-row").forEach((r) => r.classList.remove("active"));
}

let chainAccordionOpen = "single";
let chainAccordionBound = false;

function setChainAccordion(which) {
  if (which !== "single" && which !== "combo") return;
  if (chainAccordionOpen === which) return;
  chainAccordionOpen = which;
  applyChainAccordion();
}

function applyChainAccordion() {
  $$("[data-chain-accordion]").forEach((sec) => {
    const id = sec.dataset.chainAccordion;
    const open = chainAccordionOpen === id;
    sec.classList.toggle("is-expanded", open);
    sec.classList.toggle("is-collapsed", !open);
    const head = sec.querySelector(".chain-accordion-head");
    if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
  });
}

function initChainAccordion() {
  if (!chainAccordionBound) {
    chainAccordionBound = true;
    $$(".chain-accordion-head").forEach((head) => {
      head.addEventListener("click", (e) => {
        if (e.target.closest(".feature-list-select-all")) return;
        const sec = head.closest("[data-chain-accordion]");
        if (sec?.dataset.chainAccordion) setChainAccordion(sec.dataset.chainAccordion);
      });
      head.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        if (e.target.closest(".feature-list-select-all")) return;
        const sec = head.closest("[data-chain-accordion]");
        if (sec?.dataset.chainAccordion) setChainAccordion(sec.dataset.chainAccordion);
      });
    });
  }
  applyChainAccordion();
}

async function initChainPickStep() {
  currentJobId = getStoredJobId();
  loadFeatureState();
  syncStep5Labels();
  syncChainSerialThreshBar();
  const back6 = $("#back-to-6-thresh");
  if (back6) {
    back6.textContent = getScreeningMode() === "iv_equal_freq" ? "返回等频调箱" : "返回组合阈值";
  }
  const step7Desc = $("#step-7 .desc");
  if (step7Desc) {
    step7Desc.innerHTML = getScreeningMode() === "iv_equal_freq"
      ? "IV 仅用于第 5–6 步划格子。此处<strong>单变量</strong>按头尾坏率+拒绝人数重新筛选；<strong>组合</strong>来自精筛热力图勾选的准入格（<strong>每格一条规则</strong>，同一组合多格会生成多条）。右侧展示 Train 拒绝前后指标。"
      : "在此勾选用于<strong>串联分析</strong>的单变量与组合规则（与第 5 步多变量挖树池独立）。右侧统一展示 Train 拒绝前后指标；点击特征/规则可查看分箱或月度热力图。";
  }
  if (!reviewData?.clusters?.length) {
    await loadFeatureReviewPick();
  }
  if (multivariateData?.source === "iv_equal_freq" && multivariateData.rules?.length) {
    chainAccordionOpen = "combo";
  }
  initChainAccordion();
  if (getScreeningMode() === "iv_equal_freq") {
    await loadSerialPickCandidates();
  } else {
    renderSerialFeatureClusters(reviewData?.clusters || []);
  }
  updateChainPickHeaderCounts();
  initMultivariatePickSection();
}

function initMultivariatePickSection() {
  const card = $("#chain-impact-card");
  const isIv = getScreeningMode() === "iv_equal_freq";
  if (!multivariateData) {
    const listEl = $("#mv-rule-list");
    if (listEl) {
      listEl.innerHTML = isIv
        ? `<p class="sub">无等频组合规则。请返回第 6 步「精筛」勾选组合并点击「下一步：串联挑选」。</p>`
        : `<p class="sub">无组合规则（未执行第 6 步，或尚未点击「开始多变量挖掘」）。</p>`;
    }
    const countEl = $("#mv-rule-count");
    if (countEl) countEl.textContent = "0";
    if (card) card.innerHTML = `<p class="sub">勾选单变量或组合规则后，此处实时展示 Train 拒绝前后指标。</p>`;
    closeMvDetail();
    updateMvSelectedCount();
    updateMvSelectAllCheckbox();
    scheduleChainPreview();
    return;
  }
  applyMvFilter();
  updateMvSelectedCount();
  scheduleChainPreview();
}

function applyMvFilter() {
  renderMvRuleList();
  updateMvSelectedCount();
  scheduleChainPreview();
}

function formatRuleThreshold(val) {
  const n = Number(val);
  if (!Number.isFinite(n)) return String(val ?? "");
  if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n));
  return String(Number(n.toPrecision(8)));
}

function formatCompoundRuleCompact(conditions) {
  if (!conditions?.length) return "";
  const grouped = new Map();
  const binParts = [];
  for (const c of conditions) {
    const feat = c.feature || "";
    const op = c.operator || ">";
    if (op === "in") {
      const bins = c.values?.length ? c.values : c.source_bins;
      if (bins?.length) binParts.push(`${feat} ∈ {${bins.join(", ")}}`);
      continue;
    }
    const th = Number(c.threshold) || 0;
    if (!grouped.has(feat)) grouped.set(feat, { lo: null, hi: null });
    const g = grouped.get(feat);
    if (op === ">" || op === ">=") {
      if (g.lo === null || th >= g.lo) g.lo = th;
    } else if (op === "<" || op === "<=") {
      if (g.hi === null || th <= g.hi) g.hi = th;
    }
  }
  const parts = [];
  for (const [feat, b] of grouped) {
    const lo = formatRuleThreshold(b.lo);
    const hi = formatRuleThreshold(b.hi);
    if (b.lo !== null && b.hi !== null) {
      parts.push(`${lo} <= ${feat} < ${hi}`);
    } else if (b.lo !== null) {
      parts.push(`${lo} <= ${feat}`);
    } else if (b.hi !== null) {
      parts.push(`${feat} < ${hi}`);
    }
  }
  return [...parts, ...binParts].join(" and ");
}

function compoundRuleLabel(rule) {
  if (rule.conditions?.length) return formatCompoundRuleCompact(rule.conditions);
  return rule.rule_display || "";
}

function sortedMvRules(rules) {
  const key = chainSortMode === "hit_count" ? "hit_count" : "bad_rate";
  return [...rules].sort((a, b) => (b[key] || 0) - (a[key] || 0));
}

function renderMvRuleList() {
  const el = $("#mv-rule-list");
  const countEl = $("#mv-rule-count");
  const rules = sortedMvRules(getFilteredMvRules());
  if (countEl) countEl.textContent = String(rules.length);

  if (!rules.length) {
    const isIv = multivariateData?.source === "iv_equal_freq";
    el.innerHTML = isIv
      ? `<p class="sub">无等频组合规则。请返回第 6 步精筛勾选要进入串联的组合。</p>`
      : `<p class="sub">当前阈值下无满足条件的组合规则，请返回第 6 步「组合规则阈值」页调低坏率阈值或减少最少命中人数。</p>`;
    updateMvSelectAllCheckbox();
    return;
  }

  el.innerHTML = rules.map((r) => {
    const checked = selectedCompoundRules.has(r.rule_id);
    const active = activeCompoundRuleId === r.rule_id;
    const labels = (r.feature_labels || r.features || []).join(" · ");
    const nVar = r.distinct_feature_count ?? (r.features || []).length;
    const condDisplay = compoundRuleLabel(r);
    const nCond = r.n_conditions ?? r.conditions?.length ?? 0;
    const ivMeta = r.iv_pair && r.grid_iv_total != null
      ? ` · IV(total) ${formatIvPairIvTotal(r)}`
      : "";
    const titleLine = r.iv_pair && r.pair_title ? r.pair_title : condDisplay;
    const subLine = r.iv_pair && r.pair_title
      ? `${condDisplay}${r.cell_label ? ` · ${r.cell_label}` : ""}${ivMeta}`
      : `${labels} · <span class="badge ok">${nVar} 变量</span>${nCond > nVar ? ` · ${nCond} 条切分` : ""}${ivMeta}`;
    return `
      <div class="feature-row mv-rule-row${active ? " active" : ""}" data-rule-id="${r.rule_id}">
        <label class="feature-check">
          <input type="checkbox" class="mv-rule-check" data-rule-id="${r.rule_id}" ${checked ? "checked" : ""} />
        </label>
        <div class="feature-main mv-rule-main" data-rule-id="${r.rule_id}">
          <div class="feature-name" title="${titleLine}">${titleLine}</div>
          <div class="feature-meta sub">${subLine}</div>
        </div>
        <div class="feature-stats">
          <span class="rate" title="全量坏率">${pct(r.bad_rate || 0)}</span>
          <span class="hit">${r.hit_count || 0} 人</span>
          <span class="sub" style="font-size:.7rem" title="Train 坏率">T ${pct(r.train_bad_rate || 0)}</span>
        </div>
      </div>
    `;
  }).join("");

  el.querySelectorAll(".mv-rule-check").forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
    cb.addEventListener("change", (e) => {
      const id = e.target.dataset.ruleId;
      if (e.target.checked) selectedCompoundRules.add(id);
      else selectedCompoundRules.delete(id);
      updateMvSelectAllCheckbox();
      updateMvSelectedCount();
      scheduleChainPreview();
    });
  });
  updateMvSelectAllCheckbox();
  el.querySelectorAll(".mv-rule-row").forEach((row) => {
    const id = row.dataset.ruleId;
    row.addEventListener("click", (e) => {
      if (e.target.type === "checkbox") return;
      openMvRuleDetail(id);
    });
  });
}

function renderBlueHeatmapCell(obs, badRate, isMax = false, pickOpts = null) {
  const intensity = Math.min(1, Math.max(0, badRate || 0));
  const alpha = 0.12 + intensity * 0.78;
  const bg = `rgba(59, 130, 246, ${alpha.toFixed(3)})`;
  const textColor = intensity > 0.55 ? "#fff" : "var(--text)";
  const picked = pickOpts?.selected;
  const maxCls = isMax && !picked ? " heatmap-max-cell" : "";
  const pickCls = picked ? " heatmap-pick-cell" : "";
  const clickable = pickOpts?.selectable ? " heatmap-cell-pickable" : "";
  const attrs = pickOpts?.selectable
    ? ` data-iv-cell="1" data-pair-key="${pickOpts.pairKey}" data-i="${pickOpts.i}" data-j="${pickOpts.j}" role="button" tabindex="0" title="点击勾选/取消作为准入条件"`
    : "";
  if (!obs) {
    return `<td class="heatmap-cell heatmap-empty${maxCls}${pickCls}${clickable}"${attrs}>—</td>`;
  }
  const pickMark = picked ? `<span class="heatmap-pick-mark" aria-hidden="true">✓</span>` : "";
  return `<td class="heatmap-cell${maxCls}${pickCls}${clickable}"${attrs} style="background:${bg};color:${textColor}">
    ${pickMark}
    <div class="heatmap-obs">${obs}</div>
    <div class="heatmap-rate">${pct(badRate || 0)}</div>
  </td>`;
}

function renderMvHeatmapTable(side, label, opts = {}) {
  if (!side?.rows?.length) {
    return `<div class="stab-block"><h4>${label}</h4></div>`;
  }
  const months = side.months || [];
  const head = months.map((m) => `<th class="month-head">${m}</th>`).join("");
  const cells = months.map((m) => {
    const row = side.rows.find((r) => r.month === m);
    return renderBlueHeatmapCell(row?.obs ?? 0, row?.bad_rate ?? 0);
  }).join("");
  const total = side.total || {};
  const totalCell = renderBlueHeatmapCell(total.obs ?? 0, total.bad_rate ?? 0);

  return `
    <div class="stab-block">
      <h4>${label}</h4>
      <div class="stability-scroll">
        <table class="data-table stability-table heatmap-table">
          <thead>
            <tr><th>组合命中</th>${head}<th class="month-head">合计</th></tr>
          </thead>
          <tbody>
            <tr><td><strong>命中坏率</strong></td>${cells}${totalCell}</tr>
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderMvStabilityHeatmap(data) {
  const timeHint = data.time_col ? ` · 时间列 ${data.time_col}` : "";
  const note = data.note ? `<p class="warn">${data.note}</p>` : "";
  return note
    + renderMvHeatmapTable(data.train, `Train 月度稳定性${timeHint}`, {
      emptyHint: "Train 未能解析出月份，请回到第 1 步确认时间列（如 apply_date）是否正确。",
    })
    + renderMvHeatmapTable(data.test, "Test 月度稳定性", {
      isTest: true,
      noTestSplit: data.no_test_split,
      emptyHint: "Test 无月度数据。",
    });
}

async function openMvRuleDetail(ruleId) {
  setChainAccordion("combo");
  serialActiveFeature = "";
  activeCompoundRuleId = ruleId;
  closeSerialPickDetail();
  renderMvRuleList();
  $$("#serial-feature-clusters .serial-feature-row").forEach((r) => r.classList.remove("active"));
  $("#chain-pick-stage")?.classList.add("is-split");
  $("#serial-feature-detail-panel")?.setAttribute("aria-hidden", "true");
  $("#mv-detail-panel")?.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => {
    document.querySelector(`.mv-rule-row[data-rule-id="${CSS.escape(ruleId)}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });

  const rule = getFilteredMvRules().find((r) => r.rule_id === ruleId)
    || multivariateData?.rules?.find((r) => r.rule_id === ruleId);
  if (!rule) {
    $("#mv-detail-title").textContent = "—";
    $("#mv-detail-subtitle").textContent = "";
    $("#mv-detail-stability").innerHTML = `<p class="error">未找到该规则，请返回第 6 步调整阈值后重试。</p>`;
    return;
  }

  $("#mv-detail-title").textContent = compoundRuleLabel(rule);
  const cellHint = rule.iv_pair && rule.cell_label ? ` · ${rule.cell_label}` : "";
  $("#mv-detail-subtitle").textContent = `${(rule.feature_labels || []).join(" · ")} · ${rule.distinct_feature_count ?? (rule.features || []).length} 个不同变量 · 全量命中 ${rule.hit_count} 人 · 坏率 ${pct(rule.bad_rate)}${cellHint}`;
  $("#mv-detail-stability").innerHTML = `<p class="sub">加载月度稳定性…</p>`;

  if (rule.iv_pair && rule.cell_i != null && rule.cell_j != null) {
    try {
      const grid = await loadIvPairGridForRule(rule);
      if (grid) {
        $("#mv-detail-stability").innerHTML = renderIvPairCellMonthlyTable(grid, rule.cell_i, rule.cell_j);
        return;
      }
    } catch (err) {
      $("#mv-detail-stability").innerHTML = `<p class="error">${err.message}</p>`;
      return;
    }
  }

  try {
    const data = await api(`/api/binning/${getStoredJobId()}/multivariate/stability`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ conditions: rule.conditions }),
    });
    $("#mv-detail-stability").innerHTML = renderMvStabilityHeatmap(data);
  } catch (err) {
    $("#mv-detail-stability").innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function closeMvDetail() {
  activeCompoundRuleId = "";
  $("#mv-detail-panel")?.setAttribute("aria-hidden", "true");
  if (!serialActiveFeature) {
    $("#chain-pick-stage")?.classList.remove("is-split");
  }
  renderMvRuleList();
  scheduleChainPreview();
}

function validateMvMiningParams(fromModal = false) {
  const { k, tree_depth } = readMvMiningParams(fromModal);
  if (k < 2 || k > 6) {
    alert("K 建议取 2–6");
    return null;
  }
  if (tree_depth < 1 || tree_depth > 10) {
    alert("树深度建议取 1–10");
    return null;
  }
  if (multivariatePoolFeatures.length < k) {
    alert(`已选 ${multivariatePoolFeatures.length} 个变量，K=${k} 过大`);
    return null;
  }
  return { k, tree_depth };
}

$("#start-multivariate-mine")?.addEventListener("click", () => {
  if (!validateMvMiningParams()) return;
  multivariateData = null;
  runMultivariateMining();
});

$("#mv-k-confirm")?.addEventListener("click", () => {
  if (!validateMvMiningParams(true)) return;
  $("#mv-k-modal")?.classList.add("hidden");
  multivariateData = null;
  runMultivariateMining({ fromModal: true });
});

$("#mv-k-cancel")?.addEventListener("click", () => {
  $("#mv-k-modal")?.classList.add("hidden");
});

$("#refresh-mv-thresh")?.addEventListener("click", () => applyMvThresholdSettings());
$("#mv-threshold")?.addEventListener("change", () => applyMvThresholdSettings());
$("#mv-min-hit")?.addEventListener("change", () => applyMvThresholdSettings());
$("#chain-sort")?.addEventListener("change", (e) => {
  chainSortMode = e.target.value;
  mvSortMode = chainSortMode;
  serialFeatureSortMode = chainSortMode;
  renderSerialFeatureClusters(reviewData?.clusters || []);
  renderMvRuleList();
});
$("#mv-close-detail")?.addEventListener("click", closeMvDetail);

$("#to-step-6-thresh")?.addEventListener("click", async () => {
  if (!multivariateData) {
    alert("请先点击「开始多变量挖掘」");
    return;
  }
  setStep(6);
  initMvThresholdStep();
});

$("#back-to-6-mine")?.addEventListener("click", () => {
  setStep(6);
  showStep6SubView("mine");
  initMultivariateMineStep();
});

$("#to-step-7-from-thresh")?.addEventListener("click", async () => {
  applyMvThresholdSettings();
  setStep(7);
  await initChainPickStep();
});

$("#back-to-6-thresh")?.addEventListener("click", async () => {
  if (getScreeningMode() === "iv_equal_freq") {
    setStep(6);
    if (ivSelectedPairs.size) initIvFineStep();
    else initIvCoarseStep();
    return;
  }
  if (multivariateData) {
    setStep(6);
    initMvThresholdStep();
  } else {
    setStep(6);
    showStep6SubView("mine");
    initMultivariateMineStep();
  }
});

$("#to-step-8")?.addEventListener("click", async () => {
  if (!serialSelectedFeatures.size && !selectedCompoundRules.size) {
    alert("请至少勾选一个单变量或组合规则");
    return;
  }
  setStep(8);
  initSerialStep();
});

$("#back-to-7")?.addEventListener("click", async () => {
  setStep(7);
  await initChainPickStep();
});

$("#serial-select-all-features")?.addEventListener("change", (e) => {
  const checked = e.target.checked;
  if (checked) {
    for (const feat of getAllSerialCandidateFeatures()) setSerialFeatureSelected(feat, true);
  } else {
    for (const feat of getAllSerialCandidateFeatures()) setSerialFeatureSelected(feat, false);
  }
  renderSerialFeatureClusters(getSerialPickClusters());
  updateChainPickHeaderCounts();
  scheduleChainPreview();
});

$("#serial-pick-apply-thresh")?.addEventListener("click", () => loadSerialPickCandidates());
$("#serial-pick-bad-rate-thresh")?.addEventListener("change", () => loadSerialPickCandidates());
$("#serial-pick-min-hit")?.addEventListener("change", () => loadSerialPickCandidates());

$("#mv-select-all-rules")?.addEventListener("change", (e) => {
  const checked = e.target.checked;
  const rules = getFilteredMvRules();
  if (checked) {
    rules.forEach((r) => selectedCompoundRules.add(r.rule_id));
  } else {
    rules.forEach((r) => selectedCompoundRules.delete(r.rule_id));
  }
  renderMvRuleList();
  updateChainPickHeaderCounts();
  scheduleChainPreview();
});

$("#serial-pick-close-detail")?.addEventListener("click", closeSerialPickDetail);

$("#serial-pick-detail-tabs")?.addEventListener("click", (e) => {
  const tab = e.target.closest(".detail-tab");
  if (!tab || !serialActiveFeature) return;
  const name = tab.dataset.serialPickTab;
  $$("#serial-pick-detail-tabs .detail-tab").forEach((t) => t.classList.toggle("active", t === tab));
  $("#serial-pick-detail-bins").classList.toggle("hidden", name !== "bins");
  $("#serial-pick-detail-stability").classList.toggle("hidden", name !== "stability");
  if (name === "stability") loadSerialPickFeatureStability(serialActiveFeature);
});

$("#to-step-6")?.addEventListener("click", async () => {
  loadScreeningModeFromStorage();
  syncScreeningBranchUI();
  const ivMode = getScreeningMode() === "iv_equal_freq";
  if (!selectedFeatures.size) {
    alert(ivMode ? "请至少勾选 2 个变量" : "请至少勾选一个特征作为多变量挖掘池");
    return;
  }
  if (ivMode) {
    if (selectedFeatures.size < 2) {
      alert("IV 等频格子至少需要 2 个变量");
      return;
    }
    if (selectedFeatures.size > IV_MAX_FEATURES) {
      alert(`IV 等频格子最多 ${IV_MAX_FEATURES} 个变量，请减少勾选`);
      return;
    }
  } else {
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
    selectedCompoundRules = new Set();
    multivariateData = null;
  }
  await navigateToStep(6);
});

$("#to-step-7-skip")?.addEventListener("click", async () => {
  selectedCompoundRules = new Set();
  multivariateData = null;
  setStep(7);
  await initChainPickStep();
});

$("#back-to-5")?.addEventListener("click", () => {
  setStep(5);
  showStep6SubView("mine");
});

// ——— Step 8: Serial analysis ———

function renderSerialSelectionSummary() {
  const el = $("#serial-selection-summary");
  if (!el) return;
  const singles = buildSingleRulesForList([...serialSelectedFeatures]);
  const compounds = buildCompoundRulesPayload();
  el.innerHTML = `
    <p>本次串联输入：<strong>${singles.length}</strong> 条单变量 · <strong>${compounds.length}</strong> 条组合规则</p>
  `;
}

function initSerialStep() {
  renderSerialSelectionSummary();
  closeSerialDetail();
  loadSerialAnalysis();
}

async function openSerialRuleDetail(btn) {
  const type = btn.dataset.serialType;
  const key = btn.dataset.serialKey;
  activeSerialRuleKey = key;

  const panel = $("#serial-detail-panel");
  const tabs = $("#serial-detail-tabs");
  panel?.classList.remove("hidden");
  panel?.setAttribute("aria-hidden", "false");

  if (type === "single") {
    const feat = btn.dataset.feature;
    tabs?.classList.remove("hidden");
    $("#serial-detail-bins").classList.remove("hidden");
    $("#serial-detail-stability").classList.add("hidden");
    $$("#serial-detail-tabs .detail-tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.serialTab === "bins");
    });
    await openFeatureDetailForSerial(feat);
    return;
  }

  tabs?.classList.add("hidden");
  $("#serial-detail-bins").classList.add("hidden");
  $("#serial-detail-stability").classList.remove("hidden");
  const ruleId = btn.dataset.ruleId;
  const rule = getFilteredMvRules().find((r) => r.rule_id === ruleId)
    || multivariateData?.rules?.find((r) => r.rule_id === ruleId);
  if (!rule) return;

  $("#serial-detail-title").textContent = rule.rule_display;
  $("#serial-detail-subtitle").textContent = `${(rule.feature_labels || []).join(" · ")} · 命中 ${rule.hit_count} 人 · 坏率 ${pct(rule.bad_rate)}`;
  $("#serial-detail-stability").innerHTML = `<p class="sub">加载月度稳定性…</p>`;

  try {
    const data = await api(`/api/binning/${getStoredJobId()}/multivariate/stability`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ conditions: rule.conditions }),
    });
    $("#serial-detail-stability").innerHTML = renderMvStabilityHeatmap(data);
  } catch (err) {
    $("#serial-detail-stability").innerHTML = `<p class="error">${err.message}</p>`;
  }
}

async function openFeatureDetailForSerial(feature) {
  const meta = findFeatureMetaByName(feature);
  const threshold = getReviewThreshold();
  $("#serial-detail-title").textContent = meta?.chinese_name || feature;
  $("#serial-detail-subtitle").textContent = meta?.rule_display || feature;
  const binsEl = $("#serial-detail-bins");
  const stabEl = $("#serial-detail-stability");
  if (binsEl) binsEl.innerHTML = `<p class="sub">加载中…</p>`;
  if (stabEl) stabEl.innerHTML = "";

  try {
    const query = new URLSearchParams({
      feature,
      bad_rate_threshold: String(threshold),
      include_stability: "true",
    });
    const data = await api(
      `/api/binning/${getStoredJobId()}/feature-detail?${query}`,
      { headers: headers() }
    );
    lastFeatureDetail.set(feature, data);
    renderBinTables(data, threshold, binsEl);
    if (stabEl) {
      stabEl.innerHTML = `
        ${data.stability_note ? `<p class="warn">${data.stability_note}</p>` : ""}
        ${renderStabilityCheckHint(data.stability_check)}
        <p class="sub bin-hint">坏率（蓝）与金额逾期率（绿）列带条形图，便于跨月对比。</p>
        <div class="stack-table-wrap">
          <div><h4>Train 月度稳定性</h4><div class="stability-scroll">${renderStabilityTable(data.stability?.train)}</div></div>
          <div><h4>Test 月度稳定性</h4><div class="stability-scroll">${renderStabilityTable(data.stability?.test)}</div></div>
        </div>
      `;
    }
  } catch (err) {
    if (binsEl) binsEl.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function closeSerialDetail() {
  activeSerialRuleKey = "";
  $("#serial-detail-panel")?.classList.add("hidden");
  $("#serial-detail-panel")?.setAttribute("aria-hidden", "true");
}

$("#serial-close-detail")?.addEventListener("click", closeSerialDetail);

$("#serial-detail-tabs")?.addEventListener("click", (e) => {
  const tab = e.target.closest(".detail-tab");
  if (!tab) return;
  const name = tab.dataset.serialTab;
  $$("#serial-detail-tabs .detail-tab").forEach((t) => t.classList.toggle("active", t === tab));
  $("#serial-detail-bins").classList.toggle("hidden", name !== "bins");
  $("#serial-detail-stability").classList.toggle("hidden", name !== "stability");
});

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
  if (!jobId || !buildFinalSerialRules().length) return;
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
