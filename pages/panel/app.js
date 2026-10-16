// ============================================================
// 0. 常量与配置
// ============================================================

const CARD_FIELDS = [
    { key: "name",     label: "name.txt（展示名）" },
    { key: "desc",     label: "desc.txt（描述）" },
    { key: "announce", label: "announce.txt（抽卡提示）" },
    { key: "before",   label: "before.txt（前置气泡）" },
    { key: "after",    label: "after.txt（后置气泡）" },
];

const POOL_FIELDS = [
    { key: "display_name",      label: "显示名" },
    { key: "daily_limit",       label: "每次抽卡次数上限" },
    { key: "group_whitelist",   label: "群白名单", format: formatWhitelist },
    { key: "private_whitelist", label: "私聊白名单", format: formatWhitelist },
    { key: "default_announce",  label: "默认抽卡提示" },
];

function formatWhitelist(val) {
    if (!Array.isArray(val) || val.length === 0) return "";
    return val.join("、");
}

// 卡片图最大边长（前端上传时缩放）
const IMAGE_MAX_SIDE = 512;

// ============================================================
// 0.1 字段校验规则
// ============================================================

const FIELD_RULES = {
    display_name: {
        hint: "≤ 32 字符",
        validate: (v) => {
            if (!v) return "显示名不能为空";
            if (v.length > 32) return "显示名不能超过 32 个字符";
            return null;
        },
    },
    daily_limit: {
        hint: "正整数",
        validate: (v) => {
            if (!/^\d+$/.test(v)) return "次数必须是正整数";
            const n = parseInt(v, 10);
            if (n <= 0) return "次数必须是正整数";
            return null;
        },
    },
    group_whitelist: {
        hint: "纯数字群号，多个用「、」分隔。留空 = 对所有群开放。",
        validate: (v) => {
            if (!v) return null;
            const parts = v.split(/[,，、\s]+/).filter(Boolean);
            for (const p of parts) {
                if (!/^\d+$/.test(p)) return "群号必须是纯数字：" + p;
            }
            return null;
        },
    },

    private_whitelist: {
        hint: "纯数字用户QQ，多个用「、」分隔。留空 = 禁止所有私聊。",
        validate: (v) => {
            if (!v) return null;
            const parts = v.split(/[,，、\s]+/).filter(Boolean);
            for (const p of parts) {
                if (!/^\d+$/.test(p)) return "用户QQ必须是纯数字：" + p;
            }
            return null;
        },
    },

    default_announce: {
        hint: "≤ 50 字",
        validate: (v) => {
            if (v.length > 50) return "默认抽卡提示不能超过 50 字";
            return null;
        },
    },

    name: {
        hint: '≤ 32 字符，不含 \\ / : * ? " < > | _',
        validate: (v) => {
            if (!v) return null;
            if (v === "." || v === ".." || v.startsWith(".")) {
                return "名称不能以点号开头，也不能是 . 或 ..";
            }
            if (/[\\/:*?"<>|_]/.test(v)) {
                return '名称不能包含以下字符：\\ / : * ? " < > | _';
            }
            if (v.length > 32) return "名称不能超过 32 个字符";
            return null;
        },
    },
    desc: {
        hint: "≤ 150 字",
        validate: (v) => {
            if (v.length > 150) return "描述不能超过 150 字";
            return null;
        },
    },
    announce: {
        hint: "≤ 50 字",
        validate: (v) => {
            if (v.length > 50) return "抽卡提示不能超过 50 字";
            return null;
        },
    },
    before: {
        hint: "≤ 100 字",
        validate: (v) => {
            if (v.length > 100) return "前置气泡不能超过 100 字";
            return null;
        },
    },
    after: {
        hint: "≤ 100 字",
        validate: (v) => {
            if (v.length > 100) return "后置气泡不能超过 100 字";
            return null;
        },
    },
};
// ============================================================
// 0.5 Toast
// ============================================================

let _toastEl = null;
let _toastTimer = null;

function showToast(msg) {
    if (!_toastEl) {
        _toastEl = document.createElement("div");
        _toastEl.id = "__toast";
        _toastEl.style.cssText = [
            "position:fixed",
            "top:50%",
            "left:50%",
            "transform:translate(-50%, -50%)",
            "background:rgba(0,0,0,0.88)",
            "color:#fff",
            "padding:18px 36px",
            "border-radius:10px",
            "font-size:17px",
            "font-weight:500",
            "line-height:1.5",
            "letter-spacing:0.3px",
            "z-index:999999",
            "box-shadow:0 8px 40px rgba(0,0,0,0.7)",
            "pointer-events:none",
            "opacity:0",
            "transition:opacity 0.18s",
            "max-width:70vw",
            "text-align:center",
            "word-break:break-word",
            "border:1px solid rgba(255,255,255,0.12)"
        ].join(";");
        document.body.appendChild(_toastEl);
    }
    _toastEl.textContent = String(msg);
    _toastEl.style.opacity = "1";
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => {
        if (_toastEl) _toastEl.style.opacity = "0";
    }, 2500);
}

// ============================================================
// 1. 数据层
// ============================================================

const state = {
    pools: [],
    cards: [],

    poolSearch: "",
    cardSearch: "",

    poolSearchEditing: false,
    cardSearchEditing: false,

    poolSort: "default",
    cardSort: "default",

    selectedPool: null,
    selectedCard: null,

    detailType: null,
    poolDetail: null,
    cardDetail: null,

    thumbnails: {},

    editMode: false,
    editingPool: "",
    canUndo: false,

    addModalType: null,
};

// ============================================================
// 2. API 层
// ============================================================

async function waitForBridge(timeoutMs = 4000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
        if (window.AstrBotPluginPage && window.AstrBotPluginPage.apiGet) {
            return window.AstrBotPluginPage;
        }
        await new Promise(r => setTimeout(r, 50));
    }
    return null;
}

function isIgnorableError(path, e) {
    if (path === "gacha/thumbnail") return true;
    const m = (e && e.message) || "";
    if (m.includes("无图片")) return true;
    return false;
}

async function apiGet(path, params = {}) {
    const bridge = await waitForBridge();
    if (!bridge) {
        console.error("bridge 不可用");
        return { error: "bridge 不可用" };
    }
    try {
        const result = await bridge.apiGet(path, params);
        if (result && typeof result === "object" && result.status === "error") {
            throw new Error(result.message || "API 返回错误");
        }
        if (result && result.code === 200 && result.data !== undefined) {
            return result.data;
        }
        return result;
    } catch (e) {
        if (!isIgnorableError(path, e)) {
            console.error("GET 失败", path, e);
        }
        return { error: e.message || "请求失败" };
    }
}

async function apiGetPost(path, params = {}) {
    const bridge = await waitForBridge();
    if (!bridge) return { error: "bridge 不可用" };
    try {
        const result = await bridge.apiPost(path, params);
        if (result && typeof result === "object" && result.status === "error") {
            throw new Error(result.message || "API 返回错误");
        }
        if (result && result.code === 200 && result.data !== undefined) {
            return result.data;
        }
        return result;
    } catch (e) {
        console.error("POST 失败", path, e);
        return { error: e.message || "请求失败" };
    }
}

// ============================================================
// 3. 工具
// ============================================================

function escapeHtml(s) {
    if (s == null) return "";
    return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;",
        '"': "&quot;", "'": "&#39;"
    }[c]));
}

function matchSearch(text, keyword) {
    if (!keyword) return true;
    return String(text).toLowerCase().includes(keyword.toLowerCase());
}

function getWeightInfo(folder) {
    const weights = state.poolDetail?.weights || {};
    const w = Number(weights[folder]);
    const total = Object.values(weights).reduce((s, x) => {
        const n = Number(x);
        return s + (Number.isFinite(n) && n > 0 ? n : 0);
    }, 0);
    const safeW = Number.isFinite(w) && w > 0 ? w : 0;
    const pct = total > 0 ? (safeW / total * 100) : 0;
    return { weight: safeW, percent: pct };
}

function validateName(name) {
    if (!name) return "名称不能为空";
    if (name === "." || name === ".." || name.startsWith(".")) {
        return "名称不能以点号开头，也不能是 . 或 ..";
    }
    if (/[\\/:*?"<>|_]/.test(name)) {
        return '名称不能包含以下字符：\\ / : * ? " < > | _';
    }
    if (name.length > 32) return "名称不能超过 32 个字符";
    return null;
}

// ============================================================
// 4. 图片工具：读取文件 / 缩放
// ============================================================

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const result = reader.result || "";
            const idx = result.indexOf(",");
            resolve(idx >= 0 ? result.slice(idx + 1) : result);
        };
        reader.onerror = () => reject(new Error("读取文件失败"));
        reader.readAsDataURL(file);
    });
}

/**
 * 若最长边 > maxSide，用 Canvas 等比缩放到 maxSide 并导出 PNG。
 * 返回 { b64, origW, origH, newW, newH, scaled }
 */
function resizeImageToBase64(file, maxSide) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();

        img.onload = () => {
            const origW = img.naturalWidth;
            const origH = img.naturalHeight;
            const longest = Math.max(origW, origH);

            const cleanup = () => URL.revokeObjectURL(url);

            if (longest <= maxSide) {
                // 不需要缩放，直接读原图 base64
                readFileAsBase64(file)
                    .then(b64 => {
                        cleanup();
                        resolve({
                            b64,
                            origW, origH,
                            newW: origW,
                            newH: origH,
                            scaled: false,
                        });
                    })
                    .catch(err => {
                        cleanup();
                        reject(err);
                    });
                return;
            }

            const ratio = maxSide / longest;
            const newW = Math.round(origW * ratio);
            const newH = Math.round(origH * ratio);

            const canvas = document.createElement("canvas");
            canvas.width = newW;
            canvas.height = newH;
            const ctx = canvas.getContext("2d");
            ctx.drawImage(img, 0, 0, newW, newH);

            const dataUrl = canvas.toDataURL("image/png");
            const idx = dataUrl.indexOf(",");
            const b64 = idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl;

            cleanup();
            resolve({
                b64,
                origW, origH,
                newW, newH,
                scaled: true,
            });
        };

        img.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error("图片加载失败"));
        };

        img.src = url;
    });
}

// ============================================================
// 5. 缩略图
// ============================================================

async function loadThumbnail(pool, card, imgEl) {
    const key = `${pool}::${card}`;

    if (state.thumbnails[key] !== undefined) {
        const cached = state.thumbnails[key];
        if (cached) {
            imgEl.src = "data:image/png;base64," + cached;
            imgEl.classList.remove("empty");
        }
        return;
    }

    const data = await apiGet("gacha/thumbnail", { pool, card });
    if (!imgEl.isConnected) return;

    if (data.error || !data.thumbnail) {
        state.thumbnails[key] = null;
        return;
    }
    state.thumbnails[key] = data.thumbnail;
    imgEl.src = "data:image/png;base64," + data.thumbnail;
    imgEl.classList.remove("empty");
}

// ============================================================
// 6. 筛选行渲染
// ============================================================

function renderFilterRow(target) {
    const row = document.getElementById(`${target}FilterRow`);
    if (!row) return;
    row.innerHTML = "";
    row.classList.remove("visible");

    const editing = state[`${target}SearchEditing`];
    const keyword = state[`${target}Search`];

    if (editing) {
        row.classList.add("visible");

        const wrap = document.createElement("div");
        wrap.className = "search-input-wrap";

        const input = document.createElement("input");
        input.type = "text";
        input.value = keyword || "";
        input.placeholder = "输入筛选词";
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") commitSearch(target);
            if (e.key === "Escape") {
                state[`${target}SearchEditing`] = false;
                renderFilterRow(target);
            }
        });

        const btn = document.createElement("button");
        btn.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                              stroke="currentColor" stroke-width="3"
                              stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="20 6 9 17 4 12"/>
                         </svg>`;
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            commitSearch(target);
        });

        wrap.appendChild(input);
        wrap.appendChild(btn);
        row.appendChild(wrap);

        setTimeout(() => input.focus(), 0);
    } else if (keyword) {
        row.classList.add("visible");

        const tag = document.createElement("span");
        tag.className = "filter-tag";
        tag.textContent = keyword;
        tag.title = keyword;
        tag.addEventListener("click", (e) => {
            e.stopPropagation();
            state[`${target}SearchEditing`] = true;
            renderFilterRow(target);
        });
        row.appendChild(tag);
    }
}

function commitSearch(target) {
    const input = document.querySelector(`#${target}FilterRow input`);
    const value = input ? input.value.trim() : "";

    state[`${target}Search`] = value;
    state[`${target}SearchEditing`] = false;

    renderFilterRow(target);

    if (target === "pool") {
        renderPoolList();
    } else {
        renderCardList();
    }
}

// ============================================================
// 7. 列表渲染
// ============================================================

function renderPoolList() {
    const list = document.getElementById("poolList");
    list.innerHTML = "";

    const keyword = state.poolSearch.trim();
    let filtered = state.pools.filter(p =>
        matchSearch(p.display, keyword) || matchSearch(p.folder, keyword)
    );

    if (state.poolSort === "name-asc") {
        filtered = [...filtered].sort((a, b) =>
            a.display.localeCompare(b.display, "zh-CN"));
    } else if (state.poolSort === "name-desc") {
        filtered = [...filtered].sort((a, b) =>
            b.display.localeCompare(a.display, "zh-CN"));
    }

    if (filtered.length === 0) {
        list.innerHTML =
            `<li class="placeholder">${keyword ? "无匹配" : "暂无卡池"}</li>`;
        return;
    }

    filtered.forEach(pool => {
        const li = document.createElement("li");
        li.className = "pool-item";
        li.dataset.searchSkip = "card";

        if (state.selectedPool && state.selectedPool.folder === pool.folder) {
            li.classList.add("active");
        }
        if (state.editMode && state.editingPool === pool.folder) {
            li.classList.add("editing");
        }

        li.innerHTML =
            `<span class="pool-display">${escapeHtml(pool.display)}</span>` +
            `<span class="pool-folder">${escapeHtml(pool.folder)} · ${pool.card_count}</span>`;
        li.onclick = () => onSelectPool(pool);
        list.appendChild(li);
    });
}

function renderCardList() {
    const list = document.getElementById("cardList");
    list.innerHTML = "";

    if (!state.selectedPool) {
        list.innerHTML = `<li class="placeholder">请先选择卡池</li>`;
        return;
    }

    const keyword = state.cardSearch.trim();
    let filtered = state.cards.filter(c =>
        matchSearch(c.display, keyword) || matchSearch(c.folder, keyword)
    );

    const sort = state.cardSort;
    if (sort === "name-asc") {
        filtered = [...filtered].sort((a, b) =>
            a.display.localeCompare(b.display, "zh-CN"));
    } else if (sort === "name-desc") {
        filtered = [...filtered].sort((a, b) =>
            b.display.localeCompare(a.display, "zh-CN"));
    } else if (sort === "weight-desc" || sort === "weight-asc") {
        const weights = state.poolDetail?.weights || {};
        filtered = [...filtered].sort((a, b) => {
            const wa = Number(weights[a.folder]) || 0;
            const wb = Number(weights[b.folder]) || 0;
            return sort === "weight-desc" ? wb - wa : wa - wb;
        });
    }

    if (filtered.length === 0) {
        list.innerHTML =
            `<li class="placeholder">${keyword ? "无匹配" : "暂无卡片"}</li>`;
        return;
    }

    const poolFolder = state.selectedPool.folder;

    filtered.forEach(card => {
        const li = document.createElement("li");
        li.className = "card-item";
        if (state.selectedCard && state.selectedCard.folder === card.folder) {
            li.classList.add("active");
        }

        const info = document.createElement("span");
        info.className = "card-item-info";

        const nameSpan = document.createElement("span");
        nameSpan.className = "card-item-name";
        nameSpan.textContent = card.display;
        info.appendChild(nameSpan);

        if (card.display !== card.folder) {
            const folderSpan = document.createElement("span");
            folderSpan.className = "card-item-folder";
            folderSpan.textContent = card.folder;
            info.appendChild(folderSpan);
        }

        li.appendChild(info);

        const key = `${poolFolder}::${card.folder}`;
        const cached = state.thumbnails[key];

        const img = document.createElement("img");
        img.className = "card-thumb";
        img.alt = "";

        if (cached && cached !== null) {
            img.src = "data:image/png;base64," + cached;
        } else if (cached === null) {
            img.classList.add("empty");
        } else {
            img.classList.add("empty");
            loadThumbnail(poolFolder, card.folder, img);
        }

        li.appendChild(img);
        li.onclick = () => onSelectCard(card);
        list.appendChild(li);
    });
}

// ============================================================
// 8. 详情渲染
// ============================================================

function renderDetail() {
    const emptyHint = document.getElementById("emptyHint");
    const poolDetailEl = document.getElementById("poolDetail");
    const cardDetailEl = document.getElementById("cardDetail");

    if (state.detailType === "pool" && state.poolDetail) {
        emptyHint.style.display = "none";
        cardDetailEl.style.display = "none";
        poolDetailEl.style.display = "flex";

        const delBtn = document.getElementById("deletePoolBtn");
        if (delBtn) {
            delBtn.style.display =
                (state.editMode && state.editingPool === state.selectedPool.folder)
                    ? "" : "none";
        }

        renderPoolDetail(state.poolDetail);
        return;
    }

    if (state.detailType === "card" && state.cardDetail) {
        emptyHint.style.display = "none";
        poolDetailEl.style.display = "none";
        cardDetailEl.style.display = "flex";

        renderCardDetail(state.cardDetail);
        return;
    }

    emptyHint.style.display = "block";
    poolDetailEl.style.display = "none";
    cardDetailEl.style.display = "none";
}

function renderPoolDetail(detail) {
    document.getElementById("poolTitle").textContent =
        "卡池：" + (detail.display || "");

    const simBtn = document.getElementById("simulateBtn");
    if (simBtn) {
        simBtn.style.display = state.editMode ? "none" : "";
    }

    renderPoolStats(detail.weights || {});
    renderPoolInfoRows(detail.pool_json || {});
    renderWeightTable(detail.weights || {});
}

function renderPoolStats(weights) {
    const wrap = document.getElementById("poolStats");
    wrap.innerHTML = "";

    const cardCount = state.cards.length;
    const weightEntries = Object.entries(weights || {});
    const weightCount = weightEntries.length;

    const totalWeight = weightEntries.reduce((s, [, w]) => {
        const n = Number(w);
        return s + (Number.isFinite(n) && n > 0 ? n : 0);
    }, 0);

    const items = [
        { label: "卡片数", value: cardCount },
        { label: "权重总和", value: totalWeight },
        { label: "权重记录", value: weightCount },
    ];

    items.forEach(item => {
        const div = document.createElement("div");
        div.className = "stat-item";
        div.innerHTML = `${item.label} <strong>${item.value}</strong>`;
        wrap.appendChild(div);
    });

    if (cardCount !== weightCount) {
        const div = document.createElement("div");
        div.className = "stat-item warn";
        div.innerHTML =
            `⚠ 卡片数与权重记录不一致（${cardCount} ≠ ${weightCount}）`;
        wrap.appendChild(div);
    }
}

function renderPoolInfoRows(poolJson) {
    const wrap = document.getElementById("poolInfoRows");
    wrap.innerHTML = "";

    POOL_FIELDS.forEach(field => {
        const raw = poolJson[field.key];
        const display = field.format
            ? field.format(raw)
            : (raw === undefined || raw === null ? "" : String(raw));

        wrap.appendChild(buildFieldElement(field.key, field.label, display));
    });
}

function renderCardDetail(detail) {
    document.getElementById("cardTitle").textContent =
        "卡片：" + (state.selectedCard ? state.selectedCard.folder : "");

    renderCardStats(state.selectedCard?.folder);

    const img = document.getElementById("detailImage");
    if (detail.image) {
        img.src = "data:image/png;base64," + detail.image;
    } else {
        img.src = "";
    }

    const container = document.getElementById("cardFields");
    container.innerHTML = "";

    CARD_FIELDS.forEach(field => {
        container.appendChild(
            buildFieldElement(field.key, field.label, detail[field.key] || "")
        );
    });

    updateImageToolbarVisibility();
}

function renderCardStats(folder) {
    const wrap = document.getElementById("cardStats");
    wrap.innerHTML = "";

    if (!folder) return;

    const info = getWeightInfo(folder);

    const wDiv = document.createElement("div");
    wDiv.className = "stat-item";
    wDiv.innerHTML = `权重 <strong>${info.weight}</strong>`;
    wrap.appendChild(wDiv);

    const pDiv = document.createElement("div");
    pDiv.className = "stat-item";
    pDiv.innerHTML = `概率 <strong>${info.percent.toFixed(1)}%</strong>`;
    wrap.appendChild(pDiv);

    // 浏览模式：右侧显示预览按钮
    if (!state.editMode) {
        const btn = document.createElement("button");
        btn.className = "card-stats-preview-btn";
        btn.textContent = "预览抽卡效果";
        btn.addEventListener("click", openPreviewModal);
        wrap.appendChild(btn);
    }
}
function buildFieldElement(fieldKey, labelText, valueText) {
    const div = document.createElement("div");
    div.className = "field";

    const labelRow = document.createElement("div");
    labelRow.className = "field-label-row";

    const label = document.createElement("label");
    label.textContent = labelText;
    labelRow.appendChild(label);

    // 编辑模式下，规则提示跟在 label 后面
    if (state.editMode) {
        const rule = FIELD_RULES[fieldKey];
        if (rule && rule.hint) {
            const hint = document.createElement("span");
            hint.className = "field-hint";
            hint.textContent = rule.hint;
            labelRow.appendChild(hint);
        }
    }

    const value = document.createElement("div");
    value.className = "field-value";
    value.dataset.field = fieldKey;
    value.textContent = valueText;
    value.dataset.oldValue = valueText || "";

    if (!valueText) value.classList.add("empty");

    if (state.editMode) {
        value.contentEditable = "true";
        value.addEventListener("focus", () => {
            lockPoolIfNeeded();
        });
        value.addEventListener("blur", () => submitFieldEdit(value, fieldKey));
        value.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                value.blur();
            }
            if (e.key === "Escape") {
                value.textContent = value.dataset.oldValue || "";
                value.blur();
            }
        });
    }

    div.appendChild(labelRow);
    div.appendChild(value);
    return div;
}
function updateImageToolbarVisibility() {
    const toolbar = document.getElementById("imageToolbar");
    if (toolbar) {
        toolbar.style.display =
            (state.editMode && state.selectedCard && state.editingPool) ? "flex" : "none";
    }
    const delBtn = document.getElementById("deleteCardBtn");
    if (delBtn) {
        delBtn.style.display = state.editMode ? "" : "none";
    }
}
async function lockPoolIfNeeded() {
    if (!state.editMode) return false;
    if (state.editingPool) return true;
    if (!state.selectedPool) return false;

    const res = await apiGetPost("gacha/edit/pool/load", { pool: state.selectedPool.folder });
    if (res.error) {
        showToast(res.error);
        return false;
    }
    state.editingPool = state.selectedPool.folder;
    state.canUndo = false;

        // 只更新按钮 / banner，不重建字段 DOM
    applyEditModeUI(false);
    // 单独刷新卡池列表（绿条）、图片工具栏、删除卡池按钮
    renderPoolList();
    updateImageToolbarVisibility();

    const delPoolBtn = document.getElementById("deletePoolBtn");
    if (delPoolBtn && state.selectedPool) {
        delPoolBtn.style.display =
            (state.editMode && state.editingPool === state.selectedPool.folder)
                ? "" : "none";
    }
    return true;
}

async function submitFieldEdit(el, fieldKey) {
    const newValue = el.textContent.trim();
    const oldValue = el.dataset.oldValue || "";
    if (newValue === oldValue) return;

    const rule = FIELD_RULES[fieldKey];
    if (rule && rule.validate) {
        const err = rule.validate(newValue);
        if (err) {
            showToast(err);
            el.textContent = oldValue;
            return;
        }
    }

    const ok = await lockPoolIfNeeded();
    if (!ok) {
        el.textContent = oldValue;
        return;
    }

    if (state.detailType === "card" && state.selectedCard) {
        const cmd = {
            op: "card_field",
            card: state.selectedCard.folder,
            field: fieldKey,
            content: newValue,
        };
        const res = await apiGetPost("gacha/edit/pool/apply", { command: cmd });
        if (res.error) {
            showToast("保存失败：" + res.error);
            el.textContent = oldValue;
            return;
        }
        el.dataset.oldValue = newValue;
        state.canUndo = true;
        applyEditModeUI();
        await reloadCurrentView();
        return;
    }

    if (state.detailType === "pool" && state.selectedPool) {
        let content = newValue;
        if (fieldKey === "daily_limit") {
            content = parseInt(newValue, 10);
        }
    if (fieldKey === "group_whitelist" || fieldKey === "private_whitelist") {
            content = newValue
                ? newValue.split(/[,，、\s]+/).filter(Boolean)
                : [];
        }
        // default_announce 直接传字符串，后端会 [:50] 截断

        const cmd = { op: "pool_field", field: fieldKey, content };

        const res = await apiGetPost("gacha/edit/pool/apply", { command: cmd });
        if (res.error) {
            showToast("保存失败：" + res.error);
            el.textContent = oldValue;
            return;
        }
        el.dataset.oldValue = newValue;
        state.canUndo = true;
        applyEditModeUI();
        await reloadCurrentView();
    }
}

function renderWeightTable(weights) {
    const wrap = document.getElementById("weightTableWrap");
    wrap.innerHTML = "";

    const entries = Object.entries(weights || {});

    const cardMap = Object.fromEntries(
        state.cards.map(c => [c.folder, c.display])
    );

    const table = document.createElement("table");
    table.className = "weight-table";

    const thead = document.createElement("thead");
    const trHead = document.createElement("tr");

    const thCard = document.createElement("th");
    thCard.textContent = "卡片";
    trHead.appendChild(thCard);

    const thWeight = document.createElement("th");
    thWeight.className = "col-weight";
    thWeight.textContent = "权重";
    if (state.editMode && state.editingPool) {
        const hint = document.createElement("span");
        hint.className = "field-hint";
        hint.textContent = "正整数 ≤ 1000";
        thWeight.appendChild(hint);
    }
    trHead.appendChild(thWeight);

    const thPct = document.createElement("th");
    thPct.className = "col-percent";
    thPct.textContent = "概率";
    trHead.appendChild(thPct);

    thead.appendChild(trHead);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");

    if (entries.length === 0) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 3;
        td.className = "empty-cell";
        td.textContent = "暂无卡片";
        tr.appendChild(td);
        tbody.appendChild(tr);
    } else {
        const safeWeights = entries.map(([card, w]) => {
            const n = Number(w);
            return [card, Number.isFinite(n) && n > 0 ? n : 0];
        });
        const total = safeWeights.reduce((s, [, w]) => s + w, 0);

        safeWeights.forEach(([card, w]) => {
            const pct = total > 0 ? (w / total * 100) : 0;

            const tr = document.createElement("tr");

            const tdCard = document.createElement("td");
            tdCard.textContent = cardMap[card] || card;
            tdCard.dataset.card = card;

            const tdWeight = document.createElement("td");
            tdWeight.className = "col-weight";
            tdWeight.textContent = w;
            tdWeight.dataset.card = card;
            tdWeight.dataset.col = "weight";

            if (state.editMode) {
                tdWeight.contentEditable = "true";
                tdWeight.dataset.oldValue = String(w);
                tdWeight.addEventListener("focus", () => {
                    lockPoolIfNeeded();
                });
                tdWeight.addEventListener("blur", async () => {
                    const raw = tdWeight.textContent.trim();
                    const newW = parseInt(raw, 10);

                    if (!/^\d+$/.test(raw) || !Number.isFinite(newW) ||
                        newW <= 0 || newW > 1000) {
                        showToast("权重必须是 1~1000 的正整数");
                        tdWeight.textContent = tdWeight.dataset.oldValue;
                        return;
                    }
                    if (String(newW) === tdWeight.dataset.oldValue) return;

                    const ok = await lockPoolIfNeeded();
                    if (!ok) {
                        tdWeight.textContent = tdWeight.dataset.oldValue;
                        return;
                    }

                    const res = await apiGetPost("gacha/edit/pool/apply", {
                        command: { op: "weight", card, weight: newW }
                    });
                    if (res.error) {
                        showToast("保存失败：" + res.error);
                        tdWeight.textContent = tdWeight.dataset.oldValue;
                        return;
                    }
                    tdWeight.dataset.oldValue = String(newW);
                    state.canUndo = true;
                    applyEditModeUI();
                    await reloadCurrentView();
                });
                tdWeight.addEventListener("keydown", (e) => {
                    if (e.key === "Enter") { e.preventDefault(); tdWeight.blur(); }
                    if (e.key === "Escape") {
                        tdWeight.textContent = tdWeight.dataset.oldValue;
                        tdWeight.blur();
                    }
                });
            }

            const tdPct = document.createElement("td");
            tdPct.className = "col-percent";
            tdPct.textContent = pct.toFixed(1) + "%";

            tr.appendChild(tdCard);
            tr.appendChild(tdWeight);
            tr.appendChild(tdPct);
            tbody.appendChild(tr);
        });
    }

    table.appendChild(tbody);
    wrap.appendChild(table);
}

// ============================================================
// 9. 排序下拉
// ============================================================

function closeAllSortMenus() {
    document.querySelectorAll(".sort-dropdown").forEach(d => d.classList.remove("open"));
    document.querySelectorAll(".sort-menu").forEach(m => m.classList.remove("open"));
}

function openSortMenu(target) {
    const dropdown = document.getElementById(`${target}SortDropdown`);
    const menu = document.getElementById(`${target}SortMenu`);
    const currentValue = target === "pool" ? state.poolSort : state.cardSort;

    menu.querySelectorAll("li").forEach(li => {
        li.classList.toggle("active", li.dataset.value === currentValue);
    });

    dropdown.classList.add("open");
    menu.classList.add("open");
}

function setupSortDropdown(target) {
    const trigger = document.getElementById(`${target}SortTrigger`);
    const dropdown = document.getElementById(`${target}SortDropdown`);
    const menu = document.getElementById(`${target}SortMenu`);

    trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const isOpen = dropdown.classList.contains("open");
        closeAllSortMenus();
        if (!isOpen) openSortMenu(target);
    });

    menu.querySelectorAll("li").forEach(li => {
        li.addEventListener("click", (e) => {
            e.stopPropagation();
            const value = li.dataset.value;

            if (target === "pool") {
                state.poolSort = value;
                renderPoolList();
            } else {
                state.cardSort = value;
                renderCardList();
            }
            closeAllSortMenus();
        });
    });
}

// ============================================================
// 10. 筛选图标 & 全局自动提交
// ============================================================

function setupSearchToggle(target) {
    const toggle = document.getElementById(`${target}SearchToggle`);
    toggle.addEventListener("click", (e) => {
        e.stopPropagation();
        closeAllSortMenus();

        if (state[`${target}SearchEditing`]) {
            commitSearch(target);
            return;
        }

        state[`${target}SearchEditing`] = true;
        renderFilterRow(target);
    });
}

function setupSearchAutoClose() {
    document.addEventListener("click", (e) => {
        ["pool", "card"].forEach(target => {
            if (!state[`${target}SearchEditing`]) return;

            const row = document.getElementById(`${target}FilterRow`);
            if (row && row.contains(e.target)) return;

            const toggle = document.getElementById(`${target}SearchToggle`);
            if (toggle && toggle.contains(e.target)) return;

            const skipEl = e.target.closest("[data-search-skip]");
            const skipValue = skipEl ? skipEl.dataset.searchSkip : "";
            if (skipValue === "all" || skipValue === target) return;

            commitSearch(target);
        });
    });
}

// ============================================================
// 11. 选择卡池/卡片
// ============================================================

async function onSelectPool(pool) {
    if (state.editMode) {
        if (state.editingPool && state.editingPool !== pool.folder) {
            showToast("请先保存或放弃当前卡池的修改（顶部按钮）");
            return;
        }
        if (!state.editingPool) {
            const res = await apiGetPost("gacha/edit/pool/load", { pool: pool.folder });
            if (res.error) {
                showToast(res.error);
                return;
            }
            state.editingPool = pool.folder;
            state.canUndo = false;
            applyEditModeUI();
        }
    }

    state.selectedPool = pool;
    state.selectedCard = null;
    state.cards = [];
    state.detailType = null;
    state.poolDetail = null;
    state.cardDetail = null;

    state.cardSearch = "";
    state.cardSearchEditing = false;

    renderFilterRow("card");
    renderPoolList();
    renderCardList();
    renderDetail();

    const [cardsData, poolData] = await Promise.all([
        apiGet("gacha/cards", { pool: pool.folder }),
        apiGet("gacha/pool_json", { pool: pool.folder }),
    ]);

    if (!cardsData.error) {
        state.cards = cardsData.cards || [];
    }
    if (!poolData.error) {
        state.poolDetail = poolData;
        state.detailType = "pool";
    }

    renderCardList();
    renderDetail();
}

async function onSelectCard(card) {
    state.selectedCard = card;
    state.detailType = null;
    state.cardDetail = null;
    renderCardList();
    renderDetail();

    const data = await apiGet("gacha/card", {
        pool: state.selectedPool.folder,
        card: card.folder,
    });
    if (!data.error) {
        state.cardDetail = data;
        state.detailType = "card";
    }
    renderDetail();
}

// ============================================================
// 12. 编辑模式
// ============================================================

function applyEditModeUI(refreshDetail = true) {
    document.body.classList.toggle("editing", state.editMode);

    const normalEl = document.getElementById("normalActions");
    const editEl = document.getElementById("editActions");
    if (normalEl) normalEl.style.display = state.editMode ? "none" : "flex";
    if (editEl) editEl.style.display = state.editMode ? "flex" : "none";

    const undoBtn = document.getElementById("undoBtn");
    if (undoBtn) undoBtn.disabled = !state.canUndo;

    const saveBtn = document.getElementById("saveEditBtn");
    if (saveBtn) saveBtn.disabled = !state.editingPool;

    const discardBtn = document.getElementById("discardEditBtn");
    if (discardBtn) discardBtn.disabled = !state.editingPool;

    const poolAddBtn = document.getElementById("poolAddBtn");
    const cardAddBtn = document.getElementById("cardAddBtn");
    if (poolAddBtn) poolAddBtn.style.display = state.editMode ? "" : "none";
    if (cardAddBtn) cardAddBtn.style.display = state.editMode ? "" : "none";

    const banner = document.getElementById("editBannerText");
    if (banner) {
        banner.textContent = state.editingPool
            ? "正在编辑：" + state.editingPool
            : "编辑模式已开启，修改任意内容将自动进入编辑";
    }

    if (refreshDetail) {
        renderDetail();
    }
}

async function checkEditStatus() {
    const data = await apiGet("gacha/edit/status");
    if (data.error) return;
    if (data.locked) {
        state.editMode = true;
        state.editingPool = data.editing_pool || "";
        state.canUndo = !!data.can_undo;
        applyEditModeUI();
    }
}

async function onEnterEdit() {
    const res = await apiGetPost("gacha/edit/enter");
    if (res.error) {
        showToast(res.error);
        return;
    }

    state.editMode = true;
    state.editingPool = "";
    state.canUndo = false;

    await loadPools();

    if (state.selectedPool) {
        const found = state.pools.find(p => p.folder === state.selectedPool.folder);
        if (found) {
            state.selectedPool = found;

            const [cardsData, poolData] = await Promise.all([
                apiGet("gacha/cards", { pool: found.folder }),
                apiGet("gacha/pool_json", { pool: found.folder }),
            ]);
            if (!cardsData.error) state.cards = cardsData.cards || [];
            if (!poolData.error) {
                state.poolDetail = poolData;
                state.detailType = "pool";
            }

            if (state.selectedCard) {
                const foundCard = state.cards.find(c => c.folder === state.selectedCard.folder);
                if (foundCard) {
                    state.selectedCard = foundCard;
                    const cardData = await apiGet("gacha/card", {
                        pool: found.folder,
                        card: foundCard.folder,
                    });
                    if (!cardData.error) {
                        state.cardDetail = cardData;
                        state.detailType = "card";
                    }
                } else {
                    state.selectedCard = null;
                    state.cardDetail = null;
                }
            }
        } else {
            state.selectedPool = null;
            state.selectedCard = null;
            state.cards = [];
            state.poolDetail = null;
            state.cardDetail = null;
            state.detailType = null;
        }
    }

    applyEditModeUI();
    renderPoolList();
    renderCardList();
    renderDetail();

    showToast("已进入编辑模式");
}

async function onUndo() {
    if (!state.editingPool) {
        showToast("尚无未保存的修改");
        return;
    }

    const res = await apiGetPost("gacha/edit/pool/undo");
    if (res.error) {
        showToast(res.error);
        return;
    }

    const st = await apiGet("gacha/edit/status");
    if (!st.error) state.canUndo = !!st.can_undo;
    applyEditModeUI();

    await loadPools();
    await reloadCurrentView();
    showToast("已撤回一步");
}

async function onSaveEdit() {
    if (!state.editingPool) {
        showToast("没有需要保存的修改");
        return;
    }

    const btn = document.getElementById("saveEditBtn");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "保存中...";

    const res = await apiGetPost("gacha/edit/pool/save");

    btn.disabled = false;
    btn.textContent = originalText;

    if (res.error) {
        showToast("保存失败：" + res.error);
        return;
    }

    state.editingPool = "";
    state.canUndo = false;
    applyEditModeUI();

    await refreshPreservingSelection();
    showToast("已保存，可继续编辑");
}

async function onDiscardEdit() {
    if (!state.editingPool) {
        showToast("没有可放弃的修改");
        return;
    }

    const poolName = state.editingPool;
    openConfirm(
        "放弃修改",
        "即将放弃卡池「" + poolName + "」的所有未保存修改（字段、权重、卡片、图片）。\n\n" +
        "卡池本身会被保留，只丢弃本次编辑中尚未保存的改动。该操作无法反悔。",
        async () => {
            const res = await apiGetPost("gacha/edit/pool/cancel");
            if (res.error) {
                showToast(res.error);
                return;
            }
            state.editingPool = "";
            state.canUndo = false;
            applyEditModeUI();
            await refreshPreservingSelection();
            showToast("已放弃全部修改（卡池已保留）");
        }
    );
}

async function onEndEdit() {
    if (state.editingPool) {
        const res = await apiGetPost("gacha/edit/pool/save");
        if (res.error) {
            showToast("保存失败：" + res.error);
            return;
        }
    }

    const res2 = await apiGetPost("gacha/edit/exit");
    if (res2.error) {
        showToast(res2.error);
        return;
    }

    state.editMode = false;
    state.editingPool = "";
    state.canUndo = false;
    applyEditModeUI();

    await refreshPreservingSelection();
    showToast("已结束编辑并保存");
}

// ============================================================
// 13. 刷新当前视图
// ============================================================

async function reloadCurrentView() {
    if (!state.selectedPool) {
        renderPoolList();
        renderCardList();
        renderDetail();
        return;
    }

    const [cardsData, poolData] = await Promise.all([
        apiGet("gacha/cards", { pool: state.selectedPool.folder }),
        apiGet("gacha/pool_json", { pool: state.selectedPool.folder }),
    ]);

    if (!cardsData.error) state.cards = cardsData.cards || [];
    if (!poolData.error) state.poolDetail = poolData;

    if (state.selectedCard) {
        const cardData = await apiGet("gacha/card", {
            pool: state.selectedPool.folder,
            card: state.selectedCard.folder,
        });
        if (!cardData.error) {
            state.cardDetail = cardData;
            state.detailType = "card";
        }
    } else if (!poolData.error) {
        state.detailType = "pool";
    }

    renderPoolList();
    renderCardList();
    renderDetail();
}

async function refreshPreservingSelection() {
    const poolFolder = state.selectedPool ? state.selectedPool.folder : null;
    const cardFolder = state.selectedCard ? state.selectedCard.folder : null;

    state.thumbnails = {};

    const poolsData = await apiGet("gacha/pools");
    if (!poolsData.error) state.pools = poolsData.pools || [];

    if (poolFolder) {
        const foundPool = state.pools.find(p => p.folder === poolFolder);
        if (foundPool) {
            state.selectedPool = foundPool;

            const [cardsData, poolData] = await Promise.all([
                apiGet("gacha/cards", { pool: poolFolder }),
                apiGet("gacha/pool_json", { pool: poolFolder }),
            ]);
            if (!cardsData.error) state.cards = cardsData.cards || [];
            if (!poolData.error) state.poolDetail = poolData;

            if (cardFolder) {
                const foundCard = state.cards.find(c => c.folder === cardFolder);
                if (foundCard) {
                    state.selectedCard = foundCard;
                    const cardData = await apiGet("gacha/card", {
                        pool: poolFolder,
                        card: cardFolder,
                    });
                    if (!cardData.error) {
                        state.cardDetail = cardData;
                        state.detailType = "card";
                    } else {
                        state.selectedCard = null;
                        state.detailType = "pool";
                    }
                } else {
                    state.selectedCard = null;
                    state.detailType = "pool";
                }
            } else {
                state.selectedCard = null;
                state.detailType = "pool";
            }
        } else {
            state.selectedPool = null;
            state.selectedCard = null;
            state.cards = [];
            state.poolDetail = null;
            state.cardDetail = null;
            state.detailType = null;
        }
    } else {
        state.selectedCard = null;
        state.detailType = null;
    }

    renderPoolList();
    renderCardList();
    renderDetail();
}

// ============================================================
// 14. 全局刷新
// ============================================================

async function onRefresh() {
    if (state.editMode) return;

    const btn = document.getElementById("refreshBtn");
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "刷新中...";

    state.pools = [];
    state.cards = [];
    state.selectedPool = null;
    state.selectedCard = null;
    state.detailType = null;
    state.poolDetail = null;
    state.cardDetail = null;

    state.poolSearch = "";
    state.cardSearch = "";
    state.poolSearchEditing = false;
    state.cardSearchEditing = false;
    state.poolSort = "default";
    state.cardSort = "default";

    state.thumbnails = {};

    renderFilterRow("pool");
    renderFilterRow("card");
    closeAllSortMenus();

    renderPoolList();
    renderCardList();
    renderDetail();

    await loadPools();

    btn.disabled = false;
    btn.textContent = originalText;
}

// ============================================================
// 15. 模拟抽卡 / 图片放大
// ============================================================

async function onSimulateDraw() {
    if (!state.selectedPool) {
        showToast("请先选择卡池");
        return;
    }

    const pool = state.selectedPool.folder;
    const data = await apiGet("gacha/simulate", { pool });
    if (data.error) {
        showToast(data.error);
        return;
    }

    openSimulateModal(data);
}

function openSimulateModal(detail) {
    document.getElementById("simulateTitle").textContent =
        "抽到了：" + (detail.display || detail.card);

    const img = document.getElementById("simulateImage");
    if (detail.image) {
        img.src = "data:image/png;base64," + detail.image;
        img.style.cursor = "zoom-in";
        img.onclick = () => openImageModal(img.src);
    } else {
        img.src = "";
        img.onclick = null;
    }

    const container = document.getElementById("simulateFields");
    container.innerHTML = "";

    const savedEditMode = state.editMode;
    const savedEditingPool = state.editingPool;
    state.editMode = false;
    state.editingPool = "";

    CARD_FIELDS.forEach(field => {
        container.appendChild(
            buildFieldElement(field.key, field.label, detail[field.key] || "")
        );
    });

    state.editMode = savedEditMode;
    state.editingPool = savedEditingPool;

    document.getElementById("simulateModal").style.display = "flex";
}

function closeSimulateModal() {
    document.getElementById("simulateModal").style.display = "none";
}

function openImageModal(src) {
    if (!src) return;
    document.getElementById("imageModalImg").src = src;
    document.getElementById("imageModal").style.display = "flex";
}

function closeImageModal() {
    document.getElementById("imageModal").style.display = "none";
    document.getElementById("imageModalImg").src = "";
}

// ============================================================
// 16. 抽卡效果预览
// ============================================================

// 构造一条消息：头像 + 名字 + 气泡
function buildPreviewMessage(content) {
    const msg = document.createElement("div");
    msg.className = "preview-msg";

    const avatar = document.createElement("div");
    avatar.className = "preview-avatar";
    msg.appendChild(avatar);

    const body = document.createElement("div");
    body.className = "preview-msg-body";

    const name = document.createElement("div");
    name.className = "preview-msg-name";
    name.textContent = "bot";
    body.appendChild(name);

    const bubble = document.createElement("div");
    bubble.className = "preview-bubble";
    if (typeof content === "string") {
        bubble.textContent = content;
    } else {
        bubble.appendChild(content);
    }
    body.appendChild(bubble);

    msg.appendChild(body);
    return msg;
}
function openPreviewModal() {
    if (!state.cardDetail || !state.selectedCard) {
        showToast("请先选择卡片");
        return;
    }

    const detail = state.cardDetail;
    const chat = document.getElementById("previewChat");
    chat.innerHTML = "";

    // before：独立一条消息
    if (detail.before && detail.before.trim()) {
        chat.appendChild(buildPreviewMessage(detail.before));
    }

    // 主消息：@ + announce + 图片 + desc + 次数，全部在一个气泡里
    const mainBubble = document.createElement("div");

    const atLine = document.createElement("div");
    atLine.className = "preview-line";
    atLine.textContent = "@用户";
    mainBubble.appendChild(atLine);

    const announce = (detail.announce && detail.announce.trim()) || "";
    const displayName = (detail.name && detail.name.trim()) || state.selectedCard.folder;
    const announceLine = document.createElement("div");
    announceLine.className = "preview-line";
    announceLine.textContent = announce
        ? `${announce}「${displayName}」！`
        : `「${displayName}」！`;
    mainBubble.appendChild(announceLine);

    if (detail.image) {
        const imgWrap = document.createElement("div");
        imgWrap.className = "preview-img-wrap";

        const img = document.createElement("img");
        img.className = "preview-img";
        img.src = "data:image/png;base64," + detail.image;
        img.onclick = () => openImageModal(img.src);
        imgWrap.appendChild(img);

        const sizeTag = document.createElement("div");
        sizeTag.className = "preview-img-size";
        imgWrap.appendChild(sizeTag);

        img.onload = () => {
            const w = img.naturalWidth;
            const h = img.naturalHeight;
            const longest = Math.max(w, h);
            if (longest > IMAGE_MAX_SIDE) {
                const ratio = IMAGE_MAX_SIDE / longest;
                const newW = Math.round(w * ratio);
                const newH = Math.round(h * ratio);
                sizeTag.textContent =
                    `原图 ${w}×${h} → 展示 ${newW}×${newH}（超出 512 会被缩放）`;
            } else {
                sizeTag.textContent = `原图 ${w}×${h}（未超 512，不缩放）`;
            }
        };

        mainBubble.appendChild(imgWrap);
    }

    const desc = detail.desc || "";
    if (desc) {
        const descLine = document.createElement("div");
        descLine.className = "preview-line";
        descLine.textContent = desc;
        mainBubble.appendChild(descLine);
    }

    const countLine = document.createElement("div");
    countLine.className = "preview-line";
    countLine.textContent = "今日已抽：1/1";
    mainBubble.appendChild(countLine);

    chat.appendChild(buildPreviewMessage(mainBubble));

    // after：独立一条消息
    if (detail.after && detail.after.trim()) {
        chat.appendChild(buildPreviewMessage(detail.after));
    }

    document.getElementById("previewModal").style.display = "flex";
}

function closePreviewModal() {
    document.getElementById("previewModal").style.display = "none";
}

// ============================================================
// 17. 二次确认弹窗
// ============================================================

let _confirmCallback = null;

function openConfirm(title, body, onOk) {
    document.getElementById("confirmModalTitle").textContent = title;
    document.getElementById("confirmModalBody").textContent = body;
    _confirmCallback = onOk;
    document.getElementById("confirmModal").style.display = "flex";
}

function closeConfirm() {
    _confirmCallback = null;
    document.getElementById("confirmModal").style.display = "none";
}

async function onConfirmOk() {
    const cb = _confirmCallback;
    closeConfirm();
    if (cb) await cb();
}

// ============================================================
// 18. 删除卡池 / 删除卡片
// ============================================================

async function onDeletePool() {
    if (!state.selectedPool) return;
    if (!state.editMode) {
        showToast("请先进入编辑模式");
        return;
    }
    if (state.editingPool !== state.selectedPool.folder) {
        showToast("请先点击该卡池，进入编辑状态");
        return;
    }

    const pool = state.selectedPool;
    openConfirm(
        "删除卡池",
        "即将删除卡池「" + pool.display + "」（文件夹：" + pool.folder +
        "）。\n\n该操作无法反悔，删除后该卡池的所有卡片、图片与配置将永久消失。",
        async () => {
            const res = await apiGetPost("gacha/pool/delete", { pool: pool.folder });
            if (res.error) {
                showToast(res.error);
                return;
            }
            state.selectedPool = null;
            state.selectedCard = null;
            state.cards = [];
            state.detailType = null;
            state.poolDetail = null;
            state.cardDetail = null;
            state.editingPool = "";
            state.canUndo = false;
            applyEditModeUI();
            renderPoolList();
            renderCardList();
            renderDetail();
            await loadPools();
            showToast("卡池已删除");
        }
    );
}

async function onDeleteCard() {
    if (!state.selectedCard || !state.selectedPool) return;
    if (!state.editMode) {
        showToast("请先进入编辑模式");
        return;
    }

    const ok = await lockPoolIfNeeded();
    if (!ok) return;

    const card = state.selectedCard;
    openConfirm(
        "删除卡片",
        "即将删除卡片「" + card.display + "」（文件夹：" + card.folder +
        "）。\n\n该操作会暂存到草稿，需保存或结束编辑时才会真正落盘。",
        async () => {
            const cmd = { op: "delete_card", card: card.folder };
            const res = await apiGetPost("gacha/edit/pool/apply", { command: cmd });
            if (res.error) {
                showToast(res.error);
                return;
            }
            state.selectedCard = null;
            state.cardDetail = null;
            state.detailType = "pool";
            state.canUndo = true;
            applyEditModeUI();
            await reloadCurrentView();
            showToast("删除已暂存，需保存或结束编辑落盘");
        }
    );
}

// ============================================================
// 19. 图片：上传 / 替换 / 删除
// ============================================================

async function onUploadImage() {
    if (!state.editMode) {
        showToast("请先进入编辑模式");
        return;
    }
    if (!state.selectedCard) {
        showToast("请先点击卡片");
        return;
    }
    const ok = await lockPoolIfNeeded();
    if (!ok) return;
    document.getElementById("imageFileInput").click();
}

async function onImageFileChange(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".png")) {
        showToast("只支持 PNG 格式");
        return;
    }
    if (file.size > 5 * 1024 * 1024) {
        showToast("图片不能超过 5MB");
        return;
    }

    const ok = await lockPoolIfNeeded();
    if (!ok) return;

    try {
        const resized = await resizeImageToBase64(file, IMAGE_MAX_SIDE);

        const cmd = {
            op: "set_card_image",
            card: state.selectedCard.folder,
            filename: file.name,
            data: resized.b64,
        };
        const res = await apiGetPost("gacha/edit/pool/apply", { command: cmd });
        if (res.error) {
            showToast("上传失败：" + res.error);
            return;
        }
        state.canUndo = true;
        applyEditModeUI();
        await reloadCurrentView();

        if (resized.scaled) {
            showToast(`图片已暂存（${resized.origW}×${resized.origH} → ${resized.newW}×${resized.newH}）`);
        } else {
            showToast("图片已暂存，需保存或结束编辑落盘");
        }
    } catch (err) {
        showToast("处理图片失败：" + (err.message || "未知错误"));
    }
}

async function onDeleteImage() {
    if (!state.editMode) {
        showToast("请先进入编辑模式");
        return;
    }
    if (!state.selectedCard) {
        showToast("请先点击卡片");
        return;
    }

    const ok = await lockPoolIfNeeded();
    if (!ok) return;

    const card = state.selectedCard;
    openConfirm(
        "删除图片",
        "即将删除卡片「" + card.display + "」的图片。\n\n该操作会暂存到草稿，需保存或结束编辑时才会真正落盘。",
        async () => {
            const cmd = { op: "delete_card_image", card: card.folder };
            const res = await apiGetPost("gacha/edit/pool/apply", { command: cmd });
            if (res.error) {
                showToast(res.error);
                return;
            }
            state.canUndo = true;
            applyEditModeUI();
            await reloadCurrentView();
            showToast("删除已暂存，需保存或结束编辑落盘");
        }
    );
}

// ============================================================
// 20. 新建卡池 / 添加卡片 弹窗
// ============================================================

function openAddModal(type) {
    state.addModalType = type;

    const titleEl = document.getElementById("addModalTitle");
    const rulesEl = document.getElementById("addModalRules");
    const tipEl = document.getElementById("addModalTip");
    const inputEl = document.getElementById("addModalInput");

    const rulesText = '名称规范：≤ 32 字符，不含 \\ / : * ? " < > | _ ，不能以点号开头';
    const tipText = "此名称为文件夹名称，建议用简单好记的代号；";

    if (type === "pool") {
        titleEl.textContent = "新建卡池";
        inputEl.placeholder = "输入卡池文件夹名称";
    } else {
        titleEl.textContent = "添加卡片";
        inputEl.placeholder = "输入卡片文件夹名称";
    }
    rulesEl.textContent = rulesText;
    tipEl.textContent = tipText;

    inputEl.value = "";
    document.getElementById("addModal").style.display = "flex";
    setTimeout(() => inputEl.focus(), 50);
}

function closeAddModal() {
    state.addModalType = null;
    document.getElementById("addModal").style.display = "none";
}

async function onAddModalConfirm() {
    const inputEl = document.getElementById("addModalInput");
    const name = inputEl.value.trim();
    const type = state.addModalType;

    const err = validateName(name);
    if (err) {
        showToast(err);
        return;
    }

    if (type === "pool") {
        const res = await apiGetPost("gacha/pool/create", { pool_name: name });
        if (res.error) {
            showToast(res.error);
            return;
        }
        showToast("卡池「" + name + "」已创建");
        closeAddModal();
        await loadPools();
    } else if (type === "card") {
        const ok = await lockPoolIfNeeded();
        if (!ok) return;

        const cmd = { op: "add_card", card: name };
        const res = await apiGetPost("gacha/edit/pool/apply", { command: cmd });
        if (res.error) {
            showToast(res.error);
            return;
        }
        showToast("卡片已暂存，需保存或结束编辑落盘");
        closeAddModal();
        state.canUndo = true;
        applyEditModeUI();
        await reloadCurrentView();
    }
}

// ============================================================
// 21. 初始化
// ============================================================

async function loadPools() {
    const data = await apiGet("gacha/pools");
    if (!data.error) {
        state.pools = data.pools || [];
    }
    renderPoolList();
}

function bindEvents() {
    document.getElementById("editToggle")
        .addEventListener("click", onEnterEdit);
    document.getElementById("undoBtn")
        .addEventListener("click", onUndo);
    document.getElementById("saveEditBtn")
        .addEventListener("click", onSaveEdit);
    document.getElementById("discardEditBtn")
        .addEventListener("click", onDiscardEdit);
    document.getElementById("endEditBtn")
        .addEventListener("click", onEndEdit);

    document.getElementById("refreshBtn")
        .addEventListener("click", onRefresh);

    setupSearchToggle("pool");
    setupSearchToggle("card");
    setupSortDropdown("pool");
    setupSortDropdown("card");

    document.getElementById("simulateBtn")
        .addEventListener("click", onSimulateDraw);
    document.getElementById("simulateClose")
        .addEventListener("click", closeSimulateModal);
    document.getElementById("simulateMask")
        .addEventListener("click", closeSimulateModal);

    document.getElementById("detailImage")
        .addEventListener("click", async (e) => {
            // 编辑模式下点击图片：先锁定当前卡池
            if (state.editMode) {
                await lockPoolIfNeeded();
            }
            if (e.target.src) openImageModal(e.target.src);
        });
    document.getElementById("imageModalMask")
        .addEventListener("click", closeImageModal);
    document.getElementById("imageModalImg")
        .addEventListener("click", closeImageModal);

    // 加号按钮
    document.getElementById("poolAddBtn")
        .addEventListener("click", () => {
            if (!state.editMode) {
                showToast("请先进入编辑模式");
                return;
            }
            openAddModal("pool");
        });

    document.getElementById("cardAddBtn")
        .addEventListener("click", () => {
            if (!state.editMode) {
                showToast("请先进入编辑模式");
                return;
            }
            openAddModal("card");
        });

    // 删除按钮
    document.getElementById("deletePoolBtn")
        .addEventListener("click", onDeletePool);
    document.getElementById("deleteCardBtn")
        .addEventListener("click", onDeleteCard);

    // 图片按钮
    document.getElementById("uploadImageBtn")
        .addEventListener("click", onUploadImage);
    document.getElementById("deleteImageBtn")
        .addEventListener("click", onDeleteImage);
    document.getElementById("imageFileInput")
        .addEventListener("change", onImageFileChange);

    // 预览按钮
    document.getElementById("previewBtn")
        .addEventListener("click", openPreviewModal);
    document.getElementById("previewModalMask")
        .addEventListener("click", closePreviewModal);
    document.getElementById("previewModalClose")
        .addEventListener("click", closePreviewModal);

    // 新建/添加弹窗
    document.getElementById("addModalClose")
        .addEventListener("click", closeAddModal);
    document.getElementById("addModalCancel")
        .addEventListener("click", closeAddModal);
    document.getElementById("addModalMask")
        .addEventListener("click", closeAddModal);
    document.getElementById("addModalConfirm")
        .addEventListener("click", onAddModalConfirm);

    document.getElementById("addModalInput")
        .addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                onAddModalConfirm();
            }
            if (e.key === "Escape") {
                closeAddModal();
            }
        });

    // 二次确认弹窗
    document.getElementById("confirmModalMask")
        .addEventListener("click", closeConfirm);
    document.getElementById("confirmModalCancel")
        .addEventListener("click", closeConfirm);
    document.getElementById("confirmModalOk")
        .addEventListener("click", onConfirmOk);

    document.addEventListener("click", () => {
        closeAllSortMenus();
    });

    document.addEventListener("keydown", (e) => {
        if (!state.editMode) return;
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
            const active = document.activeElement;
            const inInput = active && (
                active.tagName === "INPUT" ||
                active.tagName === "TEXTAREA" ||
                active.isContentEditable
            );
            if (inInput) return;
            e.preventDefault();
            onUndo();
        }
    });
}

async function init() {
    setupSearchAutoClose();
    bindEvents();
    renderFilterRow("pool");
    renderFilterRow("card");
    renderPoolList();
    renderCardList();
    renderDetail();
    await loadPools();
    await checkEditStatus();
}

init();