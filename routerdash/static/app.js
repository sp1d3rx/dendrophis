// Initial data injected by server
const INITIAL_LOGS = window.__initialLogs || [];
const INITIAL_ERROR = window.__initialError;

// --- Tab switching ---
function switchTab(tab) {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
    document.querySelector(`.tab[data-tab="${tab}"]`).classList.add("active");
    document.getElementById(`page-${tab}`).classList.add("active");
    location.hash = tab;
    
    if (tab === "connections") {
        loadConnections();
        loadBandwidth();
    }
}

// --- Hash-based navigation ---
if (location.hash) {
    const tab = location.hash.slice(1);
    if (["logs", "connections"].includes(tab)) switchTab(tab);
}

// --- Logs state ---
let logState = {
    page: 1,
    per_page: 25,
    total_pages: 1,
    total: 0,
    autoRefresh: false,
    timer: null,
    allLogs: INITIAL_LOGS,
    activeFilters: {},
};

function computeFilterValues(logs) {
    return {
        level: [...new Set(logs.map(e => e.level).filter(Boolean))].sort(),
        facility: [...new Set(logs.map(e => e.facility).filter(Boolean))].sort(),
        process: [...new Set(logs.map(e => e.process).filter(Boolean))].sort(),
        action: [...new Set(logs.map(e => e.action).filter(Boolean))].sort(),
        detail: [...new Set(logs.map(e => e.detail).filter(Boolean))].sort(),
        client: [...new Set(logs.map(e => e.client).filter(Boolean))].sort(),
        result: [...new Set(logs.map(e => e.result).filter(Boolean))].sort(),
    };
}

function applyLogFilters(logs) {
    const q = document.getElementById("search").value.trim().toLowerCase();
    return logs.filter(e => {
        if (q && !e.message.toLowerCase().includes(q)) return false;
        for (const [key, val] of Object.entries(logState.activeFilters)) {
            if (val && e[key] !== val) return false;
        }
        return true;
    });
}

function renderLogPage() {
    const filtered = applyLogFilters(logState.allLogs);
    const fv = computeFilterValues(filtered);
    logState.filterValues = fv;

    const total = filtered.length;
    const perPage = logState.per_page;
    const totalPages = Math.max(1, Math.ceil(total / perPage));
    logState.page = Math.max(1, Math.min(logState.page, totalPages));
    logState.total = total;
    logState.total_pages = totalPages;

    const start = (logState.page - 1) * perPage;
    const pageLogs = filtered.slice(start, start + perPage);

    const tbody = document.getElementById("log-body");
    if (!pageLogs.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="loading">No matching entries</td></tr>';
    } else {
        tbody.innerHTML = pageLogs.map(entry => `
            <tr>
                <td class="col-date">${esc(entry.date)}</td>
                <td class="col-level level-${esc(entry.level)}">${esc(entry.level)}</td>
                <td class="col-facility">${esc(entry.facility)}</td>
                <td class="col-process">${esc(entry.process)}</td>
                <td class="col-action">${esc(entry.action)}</td>
                <td class="col-detail">${esc(entry.detail)}</td>
                <td class="col-client">${esc(entry.client)}</td>
                <td class="col-result">${esc(entry.result)}</td>
            </tr>
        `).join("");
    }

    document.getElementById("count").textContent = `${total} entries`;
    document.getElementById("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    document.getElementById("prev-btn").disabled = logState.page <= 1;
    document.getElementById("next-btn").disabled = logState.page >= logState.total_pages;
    document.getElementById("page-info").textContent = `Page ${logState.page} of ${logState.total_pages}`;

    updateLogHeaderStates();
}

function closeAllMenus() {
    document.querySelectorAll(".filter-menu").forEach(el => el.remove());
}

function openFilterMenu(th, field) {
    closeAllMenus();
    const values = logState.filterValues[field] || [];
    if (!values.length) return;

    const menu = document.createElement("div");
    menu.className = "filter-menu";

    const allBtn = document.createElement("button");
    allBtn.textContent = "(All)";
    allBtn.className = !logState.activeFilters[field] ? "selected" : "";
    allBtn.onclick = (e) => {
        e.stopPropagation();
        logState.activeFilters[field] = "";
        logState.page = 1;
        updateLogHeaderStates();
        closeAllMenus();
        renderLogPage();
    };
    menu.appendChild(allBtn);

    for (const v of values) {
        const btn = document.createElement("button");
        btn.textContent = v;
        if (logState.activeFilters[field] === v) btn.className = "selected";
        btn.onclick = (e) => {
            e.stopPropagation();
            logState.activeFilters[field] = v;
            logState.page = 1;
            updateLogHeaderStates();
            closeAllMenus();
            renderLogPage();
        };
        menu.appendChild(btn);
    }

    th.appendChild(menu);
}

function updateLogHeaderStates() {
    document.querySelectorAll("th[data-filter]").forEach(th => {
        const field = th.dataset.filter;
        if (logState.activeFilters[field]) {
            th.classList.add("active-filter");
        } else {
            th.classList.remove("active-filter");
        }
    });
}

async function loadLogs() {
    try {
        const resp = await fetch("/api/logs?per_page=0");
        const data = await resp.json();
        if (data.error) {
            document.getElementById("error").innerHTML = `<div class="error">${data.error}</div>`;
            return;
        }
        document.getElementById("error").innerHTML = "";
        logState.allLogs = data.logs || [];
        logState.page = 1;
        renderLogPage();
    } catch (e) {
        document.getElementById("error").innerHTML = `<div class="error">Fetch failed: ${e.message}</div>`;
    }
}

async function fetchDelta() {
    const knownHashes = logState.allLogs.map(e => e.hash);
    try {
        const resp = await fetch("/api/logs/delta", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ known_hashes: knownHashes })
        });
        const data = await resp.json();
        if (data.error) {
            document.getElementById("error").innerHTML = `<div class="error">${data.error}</div>`;
            return;
        }
        document.getElementById("error").innerHTML = "";

        if (data.full_sync) {
            await loadLogs();
            return;
        }

        if (data.evicted && data.evicted.length > 0) {
            const evictedSet = new Set(data.evicted);
            logState.allLogs = logState.allLogs.filter(e => !evictedSet.has(e.hash));
        }

        if (data.new && data.new.length > 0) {
            logState.allLogs = [...data.new, ...logState.allLogs];
        }

        renderLogPage();
    } catch (e) {
        document.getElementById("error").innerHTML = `<div class="error">Delta failed: ${e.message}</div>`;
    }
}

function changePage(delta) {
    logState.page = Math.max(1, logState.page + delta);
    if (logState.page > 1 && logState.autoRefresh) {
        toggleAutoRefresh();
    }
    renderLogPage();
}

function toggleAutoRefresh() {
    if (!logState.autoRefresh && logState.page > 1) return;

    logState.autoRefresh = !logState.autoRefresh;
    const btn = document.getElementById("auto-refresh-btn");
    if (logState.autoRefresh) {
        btn.textContent = "Auto: On";
        btn.classList.add("active");
        logState.timer = setInterval(fetchDelta, 15000);
    } else {
        btn.textContent = "Auto: Off";
        btn.classList.remove("active");
        clearInterval(logState.timer);
        logState.timer = null;
    }
}

// --- Connections state ---
let connState = {
    page: 1,
    per_page: 25,
    connections: [],
    autoRefresh: false,
    timer: null,
};

// --- Bandwidth state ---
const BW_HISTORY_LENGTH = 30;
let bwState = {
    devices: [],
    history: {},  // { wan: [{rx, tx}, ...], br-lan: [...], ... }
    autoRefresh: false,
    timer: null,
};

async function loadConnections() {
    try {
        const q = document.getElementById("conn-search").value.trim();
        const url = q ? `/api/connections?q=${encodeURIComponent(q)}` : "/api/connections";
        const resp = await fetch(url);
        const data = await resp.json();
        if (data.error) {
            document.getElementById("conn-error").innerHTML = `<div class="error">${data.error}</div>`;
            return;
        }
        document.getElementById("conn-error").innerHTML = "";
        connState.connections = data.connections || [];
        connState.page = 1;
        renderConnPage();
    } catch (e) {
        document.getElementById("conn-error").innerHTML = `<div class="error">Fetch failed: ${e.message}</div>`;
    }
}

async function loadBandwidth() {
    try {
        const resp = await fetch("/api/bandwidth");
        const data = await resp.json();
        if (data.error) {
            document.getElementById("bandwidth-section").innerHTML = `<div class="error">${data.error}</div>`;
            return;
        }
        for (const dev of (data.devices || [])) {
            if (!bwState.history[dev.name]) bwState.history[dev.name] = [];
            bwState.history[dev.name].push({ rx: dev.rx_bytes, tx: dev.tx_bytes });
            if (bwState.history[dev.name].length > BW_HISTORY_LENGTH)
                bwState.history[dev.name].shift();
        }
        bwState.devices = data.devices || [];
        renderBandwidth();
    } catch (e) {
        document.getElementById("bandwidth-section").innerHTML = `<div class="error">Bandwidth fetch failed: ${e.message}</div>`;
    }
}

function formatBwRate(bytesPerSec) {
    const bps = Math.round(bytesPerSec);
    if (bps >= 1048576) return (bps / 1048576).toFixed(1) + " MB/s";
    if (bps >= 1024) return (bps / 1024).toFixed(1) + " KB/s";
    return bps + " B/s";
}

function sparklineSVG(data, colorDown, colorUp, width, height) {
    if (!data || data.length < 2) {
        return `<svg class="sparkline" viewBox="0 0 ${width} ${height}"></svg>`;
    }
    const vals = data.map(d => [d.rx, d.tx]);
    const allVals = vals.flat();
    const max = Math.max(1, ...allVals);
    const padding = 2;
    const w = width - padding * 2;
    const h = height - padding * 2;

    const rxPoints = vals.map((d, i) => `${padding + (i / (vals.length - 1)) * w},${padding + h - (d[0] / max) * h}`).join(" ");
    const txPoints = vals.map((d, i) => `${padding + (i / (vals.length - 1)) * w},${padding + h - (d[1] / max) * h}`).join(" ");

    return `<svg class="sparkline" viewBox="0 0 ${width} ${height}">
        <polyline points="${rxPoints}" fill="none" stroke="${colorDown}" stroke-width="1.5"/>
        <polyline points="${txPoints}" fill="none" stroke="${colorUp}" stroke-width="1.5"/>
    </svg>`;
}

function renderBandwidth() {
    const container = document.getElementById("bandwidth-section");
    if (!bwState.devices.length) {
        container.innerHTML = "";
        return;
    }

    container.innerHTML = bwState.devices.map(dev => {
        const hasError = !!dev.error;
        const history = bwState.history[dev.name] || [];
        const spark = sparklineSVG(history, "#3fb950", "#d29922", 300, 40);
        return `
            <div class="bw-card">
                <div class="bw-card-header">
                    <span class="bw-dot${hasError ? ' error' : ''}"></span>
                    ${esc(dev.label)}
                </div>
                <div class="bw-card-body">
                    <span class="bw-stat">
                        <span class="bw-arrow down">↓</span>
                        <span class="bw-value">${formatBwRate(dev.rx_bytes)}</span>
                    </span>
                    <span class="bw-stat">
                        <span class="bw-arrow up">↑</span>
                        <span class="bw-value">${formatBwRate(dev.tx_bytes)}</span>
                    </span>
                </div>
                <div class="bw-sparkline">${spark}</div>
            </div>
        `;
    }).join("");
}

function renderConnPage() {
    const conns = connState.connections;
    const perPage = connState.per_page;
    const total = conns.length;
    const totalPages = Math.max(1, Math.ceil(total / perPage));
    connState.page = Math.max(1, Math.min(connState.page, totalPages));

    const start = (connState.page - 1) * perPage;
    const pageConns = conns.slice(start, start + perPage);

    const tbody = document.getElementById("conn-body");
    if (!pageConns.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="loading">No connections</td></tr>';
    } else {
        tbody.innerHTML = pageConns.map(connection => `
            <tr>
                <td class="col-conn-src" title="${esc(connection.src)}">${esc(connection.src_name || connection.src)}</td>
                <td class="col-conn-dst" title="${esc(connection.dst)}">${esc(connection.dst_name || connection.dst)}</td>
                <td class="col-conn-sport">${esc(String(connection.sport))}</td>
                <td class="col-conn-dport">${esc(String(connection.dport))}</td>
                <td class="col-conn-proto">${esc(portProtocol(connection.dport, connection.layer4))}</td>
                <td class="col-conn-l3 ${connection.layer3}">${esc(connection.layer3)}</td>
                <td class="col-conn-l4 ${connection.layer4}">${esc(connection.layer4)}</td>
                <td class="col-conn-bytes">${esc(formatBytes(connection.bytes))}</td>
                <td class="col-conn-packets">${esc(String(connection.packets))}</td>
            </tr>
        `).join("");
    }

    document.getElementById("conn-count").textContent = `${total} connections`;
    document.getElementById("conn-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    document.getElementById("conn-prev-btn").disabled = connState.page <= 1;
    document.getElementById("conn-next-btn").disabled = connState.page >= totalPages;
    document.getElementById("conn-page-info").textContent = `Page ${connState.page} of ${totalPages}`;
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
}

function changeConnPage(delta) {
    connState.page = Math.max(1, connState.page + delta);
    if (connState.page > 1 && connState.autoRefresh) {
        toggleConnAutoRefresh();
    }
    renderConnPage();
}

function toggleConnAutoRefresh() {
    if (!connState.autoRefresh && connState.page > 1) return;

    connState.autoRefresh = !connState.autoRefresh;
    const btn = document.getElementById("conn-auto-refresh-btn");
    if (connState.autoRefresh) {
        btn.textContent = "Auto: On";
        btn.classList.add("active");
        connState.timer = setInterval(loadConnections, 10000);
        bwState.timer = setInterval(loadBandwidth, 5000);
    } else {
        btn.textContent = "Auto: Off";
        btn.classList.remove("active");
        clearInterval(connState.timer);
        connState.timer = null;
        clearInterval(bwState.timer);
        bwState.timer = null;
    }
}

// --- Shared helpers ---
const WELL_KNOWN_TCP = {
    20: "FTP-DATA", 21: "FTP", 22: "SSH", 23: "TELNET", 25: "SMTP",
    53: "DNS", 67: "DHCP", 68: "DHCP", 69: "TFTP", 80: "HTTP",
    110: "POP3", 119: "NNTP", 123: "NTP", 143: "IMAP", 161: "SNMP",
    194: "IRC", 443: "HTTPS", 465: "SMTPS", 514: "SYSLOG", 587: "SMTP",
    993: "IMAPS", 995: "POP3S", 1433: "MSSQL", 1521: "ORACLE",
    3306: "MYSQL", 3389: "RDP", 5432: "PGSQL", 5900: "VNC",
    5901: "VNC", 8080: "HTTP-ALT", 8443: "HTTPS-ALT", 9090: "PROMETHEUS",
};

const WELL_KNOWN_UDP = {
    53: "DNS", 67: "DHCP", 68: "DHCP", 69: "TFTP", 123: "NTP",
    161: "SNMP", 162: "SNMPTRAP", 443: "QUIC", 1900: "SSDP", 514: "SYSLOG",
    5353: "mDNS", 8080: "HTTP-ALT",
};

function portProtocol(port, layer4) {
    if (!port || port === "") return "";
    const map = (layer4 === "udp") ? WELL_KNOWN_UDP : WELL_KNOWN_TCP;
    return map[port] || "";
}

function esc(inputVal) {
    if (inputVal === null || inputVal === undefined) return "";
    const cleanString = typeof inputVal === "string" ? inputVal : String(inputVal);
    return cleanString.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function formatItem(itemVal) {
    if (itemVal === null || itemVal === undefined) return "";
    if (typeof itemVal === "string") return itemVal;
    if (typeof itemVal === "object") {
        if (Array.isArray(itemVal)) {
            return itemVal.map(formatItem).join(", ");
        }
        const commonKeys = [
            "event",
            "description",
            "name",
            "ip",
            "device",
            "summary",
            "msg",
            "message",
            "text",
            "detail"
        ];
        for (const keyItem of commonKeys) {
            if (itemVal[keyItem] !== undefined && keyItem !== "summary" && itemVal[keyItem] !== null) {
                return formatItem(itemVal[keyItem]);
            }
        }
        const linesList = [];
        for (const [keyItem, valItem] of Object.entries(itemVal)) {
            const cleanKey = keyItem
                .replace(/_/g, " ")
                .replace(/([A-Z])/g, " $1")
                .trim()
                .replace(/^\w/, charItem => charItem.toUpperCase());
            const cleanVal = formatItem(valItem);
            if (cleanVal) {
                linesList.push(`${cleanKey}: ${cleanVal}`);
            }
        }
        if (linesList.length > 0) {
            return linesList.join("\n");
        }
        return JSON.stringify(itemVal);
    }
    return String(itemVal);
}

// --- Insights / LLM Analysis ---
let insightState = {
    page: 1,
    per_page: 50,
    total_pages: 1,
    total: 0,
    lastAction: null,
    lastUseFilters: false,
    lastResult: null,
    cachedResults: {},
};

async function runInsight(action, useFilters = false, isPageChange = false) {
    if (!isPageChange) {
        insightState.page = 1;
        insightState.cachedResults = {};
    }
    insightState.lastAction = action;
    insightState.lastUseFilters = useFilters;

    const statusEl = document.getElementById("insight-status");
    const updatedEl = document.getElementById("insight-updated");
    const countEl = document.getElementById("insight-count");
    const errorEl = document.getElementById("insight-error");
    const contentEl = document.getElementById("insight-content");
    const paginationEl = document.getElementById("insight-pagination");

    const cacheKey = `${action}-${insightState.page}`;
    const cachedData = insightState.cachedResults[cacheKey];

    // Compute logs lists and pagination parameters first to ensure UI is in correct state
    let targetLogs = logState.allLogs || INITIAL_LOGS || [];
    if (useFilters) {
        const q = document.getElementById("insight-search").value.trim().toLowerCase();
        const process = document.getElementById("insight-process").value;
        const level = document.getElementById("insight-level").value;

        if (q) targetLogs = targetLogs.filter(entry => entry.message.toLowerCase().includes(q));
        if (process) targetLogs = targetLogs.filter(entry => entry.process === process);
        if (level) {
            const levelMap = { err: "err", warn: "warn", info: "info", debug: "debug" };
            targetLogs = targetLogs.filter(entry => entry.level === levelMap[level]);
        }
    }

    if (!targetLogs.length) {
        errorEl.innerHTML = `<div class="error">No logs match the current filters. Try adjusting them.</div>`;
        statusEl.textContent = "Failed";
        contentEl.innerHTML = "";
        paginationEl.style.display = "none";
        return;
    }

    const total = targetLogs.length;
    const perPage = insightState.per_page;
    const totalPages = Math.max(1, Math.ceil(total / perPage));
    insightState.total = total;
    insightState.total_pages = totalPages;
    insightState.page = Math.max(1, Math.min(insightState.page, totalPages));

    const start = (insightState.page - 1) * perPage;
    const pageLogs = targetLogs.slice(start, start + perPage);

    if (cachedData) {
        statusEl.textContent = "Analysis complete (cached)";
        updatedEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        countEl.textContent = `${pageLogs.length} of ${total} logs selected (Page ${insightState.page} of ${totalPages})`;
        insightState.lastResult = cachedData;

        if (action === "classify") {
            renderClassifyResults(cachedData.entries || []);
        } else if (action === "anomaly") {
            renderAnomalyResults(cachedData);
        } else if (action === "summary") {
            renderSummaryResults(cachedData);
        }

        renderInsightPagination();
        return;
    }

    statusEl.textContent = "Analyzing...";
    updatedEl.textContent = "";
    countEl.textContent = "";
    errorEl.innerHTML = "";
    contentEl.innerHTML = '<div class="loading">Contacting LLM, please wait...</div>';
    paginationEl.style.display = "none";

    try {
        countEl.textContent = `${pageLogs.length} of ${total} logs selected (Page ${insightState.page} of ${totalPages})`;

        const body = {
            action: action,
            logs: pageLogs,
            batch_size: perPage
        };

        const resp = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.error) {
            errorEl.innerHTML = `<div class="error">${data.error}</div>`;
            statusEl.textContent = "Failed";
            contentEl.innerHTML = "";
            return;
        }

        statusEl.textContent = "Analysis complete";
        updatedEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        insightState.lastResult = data;
        insightState.cachedResults[cacheKey] = data;

        if (action === "classify") {
            renderClassifyResults(data.entries || []);
        } else if (action === "anomaly") {
            renderAnomalyResults(data);
        } else if (action === "summary") {
            renderSummaryResults(data);
        }

        renderInsightPagination();
    } catch (error) {
        errorEl.innerHTML = `<div class="error">Analysis failed: ${error.message}</div>`;
        statusEl.textContent = "Failed";
        contentEl.innerHTML = "";
    }
}

function renderInsightPagination() {
    const paginationEl = document.getElementById("insight-pagination");
    const prevBtn = document.getElementById("insight-prev-btn");
    const nextBtn = document.getElementById("insight-next-btn");
    const pageInfo = document.getElementById("insight-page-info");

    if (insightState.total_pages > 1) {
        paginationEl.style.display = "flex";
        prevBtn.disabled = insightState.page <= 1;
        nextBtn.disabled = insightState.page >= insightState.total_pages;
        pageInfo.textContent = `Page ${insightState.page} of ${insightState.total_pages}`;
    } else {
        paginationEl.style.display = "none";
    }
}

function changeInsightPage(delta) {
    insightState.page = Math.max(1, Math.min(insightState.page + delta, insightState.total_pages));
    runInsight(insightState.lastAction, insightState.lastUseFilters, true);
}

function renderClassifyResults(entries) {
    const contentEl = document.getElementById("insight-content");

    // Group by category and severity
    const byCategory = {};
    const bySeverity = {};
    entries.forEach(e => {
        const cat = e.category || "unknown";
        const sev = e.severity || "info";
        byCategory[cat] = (byCategory[cat] || 0) + 1;
        bySeverity[sev] = (bySeverity[sev] || 0) + 1;
    });

    let html = `<div class="status" style="margin-bottom:1rem;">
        <strong>By Severity:</strong> `;
    const sevColors = { critical: "#f85149", warning: "#d29922", info: "#58a6ff", debug: "#8b949e" };
    for (const [sev, count] of Object.entries(bySeverity)) {
        const color = sevColors[sev] || "#8b949e";
        html += `<span style="color:${color}">${sev}: ${count}</span> `;
    }
    html += `</div><div class="status" style="margin-bottom:1rem;"><strong>By Category:</strong> `;
    for (const [cat, count] of Object.entries(byCategory).sort((a,b) => b[1] - a[1])) {
        html += `<span>${cat}: ${count}</span> `;
    }
    html += `</div>`;

    // Show classified entries as cards
    html += `<div style="display:flex; flex-direction:column; gap:0.5rem;">`;
    entries.forEach(e => {
        const sevColor = sevColors[e.severity] || "#8b949e";
        const tags = (e.tags || []).map(t => `<span style="background:#21262d; padding:0.1rem 0.4rem; border-radius:3px; font-size:0.7rem; color:#8b949e;">${t}</span>`).join(" ");
        html += `<div style="background:#161b22; border:1px solid #30363d; border-left:3px solid ${sevColor}; padding:0.75rem; border-radius:4px;">
            <div style="display:flex; justify-content:space-between; margin-bottom:0.25rem;">
                <span style="font-weight:600; color:${sevColor}">${e.severity.toUpperCase()}</span>
                <span style="color:#8b949e; font-size:0.75rem;">[${e.category}]</span>
            </div>
            <div style="margin-bottom:0.5rem; font-size:0.85rem; color:#c9d1d9;">${esc(e.summary || "")}</div>
            <div style="font-size:0.75rem; color:#8b949e; margin-bottom:0.25rem;">${esc(e.message || "").substring(0, 200)}${(e.message || "").length > 200 ? "..." : ""}</div>
            <div>${tags}</div>
        </div>`;
    });
    html += `</div>`;

    contentEl.innerHTML = html;
}

function renderAnomalyResults(insightData) {
    const contentElement = document.getElementById("insight-content");
    let htmlContent = "";

    // Concerns
    if (insightData.concerns && insightData.concerns.length > 0) {
        const renderedConcerns = new Set();
        const uniqueConcerns = [];

        insightData.concerns.forEach(concernItem => {
            const concernKey = `${concernItem.type || "issue"}|${concernItem.description || ""}`;
            if (!renderedConcerns.has(concernKey)) {
                renderedConcerns.add(concernKey);
                uniqueConcerns.push(concernItem);
            }
        });

        if (uniqueConcerns.length > 0) {
            htmlContent += `<div style="margin-bottom:1rem;">
                <h3 style="color:#f85149; font-size:0.9rem; margin-bottom:0.5rem;">⚠ Concerns (${uniqueConcerns.length})</h3>`;
            uniqueConcerns.forEach(concernItem => {
                const severityColor = concernItem.severity === "high" ? "#f85149" : concernItem.severity === "medium" ? "#d29922" : "#8b949e";
                htmlContent += `<div style="background:#161b22; border:1px solid #30363d; border-left:3px solid ${severityColor}; padding:0.75rem; border-radius:4px; margin-bottom:0.5rem;">
                    <div style="font-weight:600; color:${severityColor}; font-size:0.85rem;">${esc(concernItem.type || "issue")}</div>
                    <div style="font-size:0.85rem; color:#c9d1d9; margin-top:0.25rem;">${esc(concernItem.description || "")}</div>
                </div>`;
            });
            htmlContent += `</div>`;
        } else {
            htmlContent += `<div style="background:#161b22; border:1px solid #30363d; padding:0.75rem; border-radius:4px; margin-bottom:1rem;">
                <div style="color:#3fb950; font-size:0.85rem;">✓ No concerns detected. Network activity looks normal.</div>
            </div>`;
        }
    } else {
        htmlContent += `<div style="background:#161b22; border:1px solid #30363d; padding:0.75rem; border-radius:4px; margin-bottom:1rem;">
            <div style="color:#3fb950; font-size:0.85rem;">✓ No concerns detected. Network activity looks normal.</div>
        </div>`;
    }

    // Summary
    if (insightData.summary) {
        htmlContent += `<div style="background:#161b22; border:1px solid #30363d; padding:0.75rem; border-radius:4px; margin-bottom:1rem;">
            <h3 style="color:#58a6ff; font-size:0.9rem; margin-bottom:0.5rem;">Overview</h3>
            <div style="font-size:0.85rem; color:#c9d1d9; white-space:pre-wrap;">${esc(formatItem(insightData.summary))}</div>
        </div>`;
    }

    // Suggestion
    if (insightData.suggestion) {
        let suggestionList = [];
        if (Array.isArray(insightData.suggestion)) {
            suggestionList = insightData.suggestion;
        } else if (typeof insightData.suggestion === "string") {
            suggestionList = [insightData.suggestion];
        }

        const validSuggestions = suggestionList.filter(suggestionItem => {
            const formatted = formatItem(suggestionItem);
            return formatted && formatted.toLowerCase() !== "none";
        });

        if (validSuggestions.length > 0) {
            htmlContent += `<div style="background:#161b22; border:1px solid #30363d; border-left:3px solid #58a6ff; padding:0.75rem; border-radius:4px;">
                <h3 style="color:#58a6ff; font-size:0.9rem; margin-bottom:0.5rem;">Recommendation</h3>`;
            if (validSuggestions.length === 1) {
                htmlContent += `<div style="font-size:0.85rem; color:#c9d1d9;">${esc(formatItem(validSuggestions[0]))}</div>`;
            } else {
                validSuggestions.forEach((suggestionItem, suggestionIndex) => {
                    htmlContent += `<div style="font-size:0.85rem; color:#c9d1d9; padding:0.15rem 0;">${suggestionIndex + 1}. ${esc(formatItem(suggestionItem))}</div>`;
                });
            }
            htmlContent += `</div>`;
        }
    }

    contentElement.innerHTML = htmlContent;
}

function renderSummaryResults(insightData) {
    const contentElement = document.getElementById("insight-content");
    let htmlContent = "";

    if (insightData.overview) {
        htmlContent += `<div style="background:#161b22; border:1px solid #30363d; padding:0.75rem; border-radius:4px; margin-bottom:1rem;">
            <h3 style="color:#58a6ff; font-size:0.9rem; margin-bottom:0.5rem;">Overview</h3>
            <div style="font-size:0.85rem; color:#c9d1d9; white-space:pre-wrap;">${esc(insightData.overview)}</div>
        </div>`;
    }

    if (insightData.key_events && insightData.key_events.length > 0) {
        htmlContent += `<div style="background:#161b22; border:1px solid #30363d; padding:0.75rem; border-radius:4px; margin-bottom:1rem;">
            <h3 style="color:#58a6ff; font-size:0.9rem; margin-bottom:0.5rem;">Key Events</h3>`;
        insightData.key_events.forEach((eventItem, eventIndex) => {
            htmlContent += `<div style="padding:0.25rem 0; font-size:0.85rem; color:#c9d1d9;">${eventIndex + 1}. ${esc(formatItem(eventItem))}</div>`;
        });
        htmlContent += `</div>`;
    }

    if (insightData.devices_mentioned && insightData.devices_mentioned.length > 0) {
        htmlContent += `<div style="background:#161b22; border:1px solid #30363d; padding:0.75rem; border-radius:4px; margin-bottom:1rem;">
            <h3 style="color:#58a6ff; font-size:0.9rem; margin-bottom:0.5rem;">Devices Mentioned</h3>`;
        insightData.devices_mentioned.forEach(deviceItem => {
            htmlContent += `<span style="display:inline-block; background:#21262d; padding:0.15rem 0.5rem; border-radius:3px; font-size:0.8rem; color:#c9d1d9; margin-right:0.35rem; margin-bottom:0.25rem;">${esc(formatItem(deviceItem))}</span>`;
        });
        htmlContent += `</div>`;
    }

    if (insightData.recommendations) {
        let recommendationList = [];
        if (Array.isArray(insightData.recommendations)) {
            recommendationList = insightData.recommendations;
        } else if (typeof insightData.recommendations === "string") {
            recommendationList = [insightData.recommendations];
        }

        const validRecommendations = recommendationList.filter(recommendationItem => {
            const formatted = formatItem(recommendationItem);
            return formatted && formatted.toLowerCase() !== "none";
        });

        if (validRecommendations.length > 0) {
            htmlContent += `<div style="background:#161b22; border:1px solid #30363d; border-left:3px solid #58a6ff; padding:0.75rem; border-radius:4px;">
                <h3 style="color:#58a6ff; font-size:0.9rem; margin-bottom:0.5rem;">Recommendation</h3>`;
            if (validRecommendations.length === 1) {
                htmlContent += `<div style="font-size:0.85rem; color:#c9d1d9;">${esc(formatItem(validRecommendations[0]))}</div>`;
            } else {
                validRecommendations.forEach((recommendationItem, recommendationIndex) => {
                    htmlContent += `<div style="font-size:0.85rem; color:#c9d1d9; padding:0.15rem 0;">${recommendationIndex + 1}. ${esc(formatItem(recommendationItem))}</div>`;
                });
            }
            htmlContent += `</div>`;
        }
    }

    contentElement.innerHTML = htmlContent;
}

// --- Connection Analysis ---
async function runConnectionAnalysis() {
    const cardEl = document.getElementById("conn-insight-card");
    const bodyEl = document.getElementById("conn-insight-body");
    const btnEl = document.getElementById("conn-analyze-btn");

    cardEl.style.display = "block";
    bodyEl.innerHTML = '<div class="loading">Analyzing active WAN connections, please wait...</div>';
    btnEl.disabled = true;

    try {
        const response = await fetch("/api/connections/analyze");
        const data = await response.json();

        if (data.error) {
            bodyEl.innerHTML = `<div class="error">${esc(data.error)}</div>`;
            btnEl.disabled = false;
            return;
        }

        let html = "";

        // Summary Block
        if (data.summary) {
            html += `
                <div class="insight-block">
                    <h4>Overview</h4>
                    <div style="white-space: pre-wrap;">${esc(formatItem(data.summary))}</div>
                </div>
            `;
        }

        // Top Consumers Block
        if (data.top_consumers && data.top_consumers.length > 0) {
            html += `
                <div class="insight-block">
                    <h4>Top Bandwidth Consumers</h4>
                    <ul style="list-style-type: none;">
            `;
            data.top_consumers.forEach(consumer => {
                const displayName = consumer.device_name && consumer.device_name !== consumer.device
                    ? `${consumer.device_name} (${consumer.device})`
                    : consumer.device;
                html += `
                    <li style="margin-bottom: 0.4rem;">
                        <strong>${esc(displayName)}</strong>: ${esc(formatItem(consumer.description))}
                    </li>
                `;
            });
            html += `
                    </ul>
                </div>
            `;
        }

        // Security Concerns Block
        if (data.security_concerns && data.security_concerns.length > 0) {
            html += `
                <div class="insight-block">
                    <h4>Potential Security Concerns</h4>
            `;
            data.security_concerns.forEach(concern => {
                const severityClass = `severity-${esc(concern.severity || "low")}`;
                html += `
                    <div class="insight-alert-item ${severityClass}">
                        <div class="insight-alert-title">${esc(formatItem(concern.type))} [${esc(concern.severity.toUpperCase())}]</div>
                        <div class="insight-alert-desc">${esc(formatItem(concern.description))}</div>
                    </div>
                `;
            });
            html += `</div>`;
        } else {
            html += `
                <div class="insight-block" style="border-left: 3px solid #3fb950; background: #13241b;">
                    <div style="color: #3fb950; font-weight: 600;">✓ No active security concerns detected in WAN traffic.</div>
                </div>
            `;
        }

        // Recommendations Block
        if (data.recommendations && data.recommendations.length > 0 && formatItem(data.recommendations[0]).toLowerCase() !== "none") {
            html += `
                <div class="insight-block">
                    <h4>Recommendations</h4>
            `;
            data.recommendations.forEach(recommendation => {
                html += `
                    <div class="insight-recommendation-item">• ${esc(formatItem(recommendation))}</div>
                `;
            });
            html += `</div>`;
        }

        bodyEl.innerHTML = html;
    } catch (error) {
        bodyEl.innerHTML = `<div class="error">Request failed: ${esc(error.message)}</div>`;
    } finally {
        btnEl.disabled = false;
    }
}

function closeConnectionAnalysis() {
    document.getElementById("conn-insight-card").style.display = "none";
}

// --- Event listeners ---
let searchTimer;
document.getElementById("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { logState.page = 1; renderLogPage(); }, 300);
});

let connSearchTimer;
document.getElementById("conn-search").addEventListener("input", () => {
    clearTimeout(connSearchTimer);
    connSearchTimer = setTimeout(loadConnections, 300);
});

document.querySelectorAll("th[data-filter]").forEach(th => {
    th.addEventListener("click", (e) => {
        if (e.target.closest(".filter-menu")) return;
        const field = th.dataset.filter;
        openFilterMenu(th, field);
    });
});

document.addEventListener("click", (e) => {
    if (!e.target.closest("th[data-filter]")) closeAllMenus();
});

// --- Initial render ---
if (INITIAL_ERROR) {
    document.getElementById("error").innerHTML = `<div class="error">${INITIAL_ERROR}</div>`;
    document.getElementById("log-body").innerHTML = '<tr><td colspan="8" class="loading">Failed to load logs</td></tr>';
} else {
    renderLogPage();
}
