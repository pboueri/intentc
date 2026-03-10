"use strict";

// ─── State ───
let selectedTarget = null;
let dagData = null;
let changesWs = null;
let agentWs = null;

// ─── Init ───
document.addEventListener("DOMContentLoaded", () => {
    loadProject();
    loadDag();
    connectChangesWs();

    document.getElementById("chat-send").addEventListener("click", sendChat);
    document.getElementById("chat-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendChat();
        }
    });

    initResizeHandles();
    initOutputPanel();
    initSettingsModal();
});

// ─── Project ───
async function loadProject() {
    try {
        const res = await fetch("/api/project");
        if (res.ok) {
            const data = await res.json();
            // Extract project name from frontmatter
            const match = data.content.match(/^---\n[\s\S]*?name:\s*(.+)/m);
            if (match) {
                document.getElementById("project-name").textContent = match[1].trim();
            }
        }
    } catch (e) {
        console.error("Failed to load project:", e);
    }
}

// ─── DAG ───
async function loadDag() {
    try {
        const res = await fetch("/api/dag");
        dagData = await res.json();
        renderDag(dagData);
    } catch (e) {
        console.error("Failed to load DAG:", e);
    }
}

function renderDag(data) {
    const svg = document.getElementById("dag-svg");
    svg.innerHTML = "";

    if (!data.nodes || data.nodes.length === 0) return;

    // Create arrowhead marker
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    defs.innerHTML = `
        <marker id="arrowhead" viewBox="0 0 10 7" refX="10" refY="3.5"
                markerWidth="8" markerHeight="6" orient="auto-start-reverse">
            <polygon points="0 0, 10 3.5, 0 7" fill="#3a3a5c"/>
        </marker>
    `;
    svg.appendChild(defs);

    // Layout: assign layers by longest path from root
    const nodeMap = {};
    data.nodes.forEach(n => { nodeMap[n.name] = { ...n, layer: 0, x: 0, y: 0 }; });

    // Build adjacency (depends_on means an edge FROM dependency TO this node)
    const dependents = {};  // name -> list of names that depend on it
    data.edges.forEach(e => {
        if (!dependents[e.to]) dependents[e.to] = [];
        dependents[e.to].push(e.from);
    });

    // Compute layers using BFS from roots
    const roots = data.nodes.filter(n => !n.depends_on || n.depends_on.length === 0 || (n.depends_on.length === 1 && n.depends_on[0] === ""));
    const queue = roots.map(n => n.name);
    const visited = new Set(queue);
    roots.forEach(n => { nodeMap[n.name].layer = 0; });

    while (queue.length > 0) {
        const name = queue.shift();
        const layer = nodeMap[name].layer;
        const deps = dependents[name] || [];
        deps.forEach(dep => {
            nodeMap[dep].layer = Math.max(nodeMap[dep].layer, layer + 1);
            if (!visited.has(dep)) {
                visited.add(dep);
                queue.push(dep);
            }
        });
    }

    // Ensure unvisited nodes get placed
    data.nodes.forEach(n => {
        if (!visited.has(n.name)) {
            nodeMap[n.name].layer = 0;
        }
    });

    // Group by layer
    const layers = {};
    Object.values(nodeMap).forEach(n => {
        if (!layers[n.layer]) layers[n.layer] = [];
        layers[n.layer].push(n);
    });

    // Sort within layers alphabetically
    Object.values(layers).forEach(layer => layer.sort((a, b) => a.name.localeCompare(b.name)));

    // Position nodes
    const nodeW = 140;
    const nodeH = 36;
    const padX = 20;
    const padY = 24;
    const startX = 16;
    const startY = 16;

    const maxLayerIdx = Math.max(...Object.keys(layers).map(Number));

    for (let i = 0; i <= maxLayerIdx; i++) {
        const layerNodes = layers[i] || [];
        layerNodes.forEach((n, j) => {
            n.x = startX + j * (nodeW + padX);
            n.y = startY + i * (nodeH + padY);
        });
    }

    // Compute SVG size
    const allNodes = Object.values(nodeMap);
    const maxX = Math.max(...allNodes.map(n => n.x + nodeW)) + startX;
    const maxY = Math.max(...allNodes.map(n => n.y + nodeH)) + startY;
    svg.setAttribute("viewBox", `0 0 ${maxX} ${maxY}`);
    svg.style.width = maxX + "px";
    svg.style.height = maxY + "px";

    // Draw edges first (behind nodes)
    data.edges.forEach(e => {
        const fromNode = nodeMap[e.from];
        const toNode = nodeMap[e.to];
        if (!fromNode || !toNode) return;

        // Arrow from dependency (to) to dependent (from)
        // i.e. toNode is upstream, fromNode is downstream
        const x1 = toNode.x + nodeW / 2;
        const y1 = toNode.y + nodeH;
        const x2 = fromNode.x + nodeW / 2;
        const y2 = fromNode.y;

        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        const midY = (y1 + y2) / 2;
        path.setAttribute("d", `M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`);
        path.setAttribute("class", "dag-edge");
        svg.appendChild(path);
    });

    // Draw nodes
    allNodes.forEach(n => {
        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        g.setAttribute("class", "dag-node" + (selectedTarget === n.name ? " selected" : ""));
        g.setAttribute("data-name", n.name);

        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", n.x);
        rect.setAttribute("y", n.y);
        rect.setAttribute("width", nodeW);
        rect.setAttribute("height", nodeH);
        g.appendChild(rect);

        // Status dot
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", n.x + 14);
        dot.setAttribute("cy", n.y + nodeH / 2);
        dot.setAttribute("class", "status-dot status-" + (n.status || "pending"));
        g.appendChild(dot);

        // Name text
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", n.x + 26);
        text.setAttribute("y", n.y + nodeH / 2 + 4);
        text.textContent = n.name;
        g.appendChild(text);

        g.addEventListener("click", () => selectTarget(n.name));
        svg.appendChild(g);
    });
}

// ─── Target Selection ───
async function selectTarget(name) {
    selectedTarget = name;
    document.getElementById("editor-target-name").textContent = name;

    // Update DAG selection visual
    document.querySelectorAll(".dag-node").forEach(g => {
        g.classList.toggle("selected", g.getAttribute("data-name") === name);
    });

    // Fetch target details
    try {
        const res = await fetch(`/api/targets/${encodeURIComponent(name)}`);
        if (!res.ok) {
            document.getElementById("editor-content").innerHTML = `<p class="placeholder">Target not found</p>`;
            return;
        }
        const target = await res.json();
        renderEditor(target);
    } catch (e) {
        console.error("Failed to load target:", e);
    }
}

function renderEditor(target) {
    const container = document.getElementById("editor-content");
    let html = "";

    // Action bar
    html += `<div class="action-bar">
        <button class="action-btn action-build" onclick="triggerAction('build', '${target.name}', this)">Build</button>
        <button class="action-btn action-clean" onclick="triggerAction('clean', '${target.name}', this)">Clean</button>
        <button class="action-btn action-validate" onclick="triggerAction('validate', '${target.name}', this)">Validate</button>
    </div>`;

    // Status section
    html += `<div class="status-bar">
        <span><span class="label">Status: </span><span class="value status-${target.status}">${target.status}</span></span>`;

    if (target.latest_build) {
        html += `<span><span class="label">Built: </span><span class="value">${target.latest_build.generated_at || "—"}</span></span>`;
        if (target.latest_build.error) {
            html += `<span><span class="label">Error: </span><span class="value status-failed">${escapeHtml(target.latest_build.error)}</span></span>`;
        }
    }

    html += `</div>`;

    // Spec section
    html += `<div class="editor-section">
        <div class="editor-section-header">
            <span>Spec — ${target.spec_path}</span>
            <button class="save-btn" onclick="saveSpec('${target.name}')">Save</button>
        </div>
        <textarea id="spec-editor" spellcheck="false">${escapeHtml(target.spec_content)}</textarea>
    </div>`;

    // Validation sections
    if (target.validations && target.validations.length > 0) {
        target.validations.forEach((v, i) => {
            html += `<div class="editor-section">
                <div class="editor-section-header">
                    <span>Validation — ${v.file_path}</span>
                    <button class="save-btn" onclick="saveValidation('${target.name}', '${v.file_path}', ${i})">Save</button>
                </div>
                <textarea id="val-editor-${i}" spellcheck="false">${escapeHtml(v.content)}</textarea>
            </div>`;
        });
    }

    // Build log section
    html += `<div class="editor-section">
        <div class="editor-section-header"><span>Build Log</span></div>
        <div id="build-log"><p class="placeholder" style="padding-top:8px">Loading...</p></div>
    </div>`;

    container.innerHTML = html;

    // Load build log asynchronously
    loadBuildLog(target.name);
}

// ─── Save Operations ───
async function saveSpec(name) {
    const content = document.getElementById("spec-editor").value;
    try {
        const res = await fetch(`/api/targets/${encodeURIComponent(name)}/spec`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({content}),
        });
        if (res.ok) {
            const btn = document.querySelector(".editor-section:first-child .save-btn");
            if (btn) { btn.classList.add("saved"); btn.textContent = "Saved"; setTimeout(() => { btn.classList.remove("saved"); btn.textContent = "Save"; }, 1500); }
        }
    } catch (e) {
        console.error("Save failed:", e);
    }
}

async function saveValidation(name, filePath, index) {
    const content = document.getElementById(`val-editor-${index}`).value;
    try {
        const res = await fetch(`/api/targets/${encodeURIComponent(name)}/validation`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({file_path: filePath, content}),
        });
        if (res.ok) {
            const btns = document.querySelectorAll(`.editor-section:nth-child(${index + 2}) .save-btn`);
            btns.forEach(btn => { btn.classList.add("saved"); btn.textContent = "Saved"; setTimeout(() => { btn.classList.remove("saved"); btn.textContent = "Save"; }, 1500); });
        }
    } catch (e) {
        console.error("Save validation failed:", e);
    }
}

// ─── Build Lifecycle ───
async function triggerAction(action, name, btn) {
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = action === "clean" ? "Cleaning..." : "Running...";

    try {
        const res = await fetch(`/api/targets/${encodeURIComponent(name)}/${action}`, {
            method: "POST",
        });
        const data = await res.json();

        if (action === "clean") {
            // Clean is synchronous, refresh immediately
            btn.textContent = "Done";
            setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 1000);
            loadDag();
            selectTarget(name);
        } else {
            // Build/validate are async — button stays disabled until WebSocket notifies
            btn.textContent = origText + "ing...";
        }
    } catch (e) {
        console.error(`${action} failed:`, e);
        btn.textContent = "Error";
        setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
    }
}

async function loadBuildLog(name) {
    const container = document.getElementById("build-log");
    if (!container) return;

    try {
        const res = await fetch(`/api/targets/${encodeURIComponent(name)}/builds`);
        if (!res.ok) { container.innerHTML = `<p class="placeholder" style="padding-top:8px">No builds</p>`; return; }
        const data = await res.json();

        if (!data.builds || data.builds.length === 0) {
            container.innerHTML = `<p class="placeholder" style="padding-top:8px">No builds yet</p>`;
            return;
        }

        let html = "";
        data.builds.forEach(b => {
            const statusClass = b.success ? "status-built" : "status-failed";
            const statusText = b.success ? "passed" : "failed";
            const ts = b.generated_at ? new Date(b.generated_at).toLocaleString() : "—";
            const errorHtml = b.error ? `<div class="build-log-error">${escapeHtml(b.error)}</div>` : "";
            const filesHtml = b.files && b.files.length > 0
                ? `<details class="build-log-files"><summary>${b.files.length} file(s)</summary><ul>${b.files.map(f => `<li>${escapeHtml(f)}</li>`).join("")}</ul></details>`
                : "";

            html += `<div class="build-log-entry">
                <div class="build-log-header">
                    <span class="build-log-id">${escapeHtml(b.generation_id)}</span>
                    <span class="build-log-status ${statusClass}">${statusText}</span>
                    <span class="build-log-time">${ts}</span>
                </div>
                ${errorHtml}${filesHtml}
            </div>`;
        });

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<p class="placeholder" style="padding-top:8px">Failed to load</p>`;
    }
}

// ─── WebSocket: File Changes ───
function connectChangesWs() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    changesWs = new WebSocket(`${protocol}//${location.host}/ws/changes`);

    changesWs.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "build_output") {
            appendOutputLine(msg.line);
        } else if (msg.type === "file_changed") {
            loadDag();
            if (selectedTarget && msg.path.includes(`/${selectedTarget}/`)) {
                selectTarget(selectedTarget);
            }
        } else if (msg.type === "build_complete" || msg.type === "validate_complete") {
            // Refresh DAG and editor to show updated status
            loadDag();
            if (selectedTarget === msg.target) {
                selectTarget(selectedTarget);
            }
            // Re-enable action buttons
            document.querySelectorAll(".action-btn").forEach(btn => {
                btn.disabled = false;
                btn.textContent = btn.textContent.replace("ing...", "");
            });
        }
    };

    changesWs.onclose = () => {
        // Reconnect after delay
        setTimeout(connectChangesWs, 2000);
    };
}

// ─── WebSocket: Agent Chat ───
function connectAgentWs() {
    if (agentWs && agentWs.readyState === WebSocket.OPEN) return agentWs;

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    agentWs = new WebSocket(`${protocol}//${location.host}/ws/agent`);

    agentWs.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        const messages = document.getElementById("chat-messages");

        if (msg.type === "chunk") {
            // Append to last agent message or create new
            let last = messages.querySelector(".chat-msg.agent:last-child");
            if (!last) {
                last = document.createElement("div");
                last.className = "chat-msg agent";
                messages.appendChild(last);
            }
            last.textContent += msg.content;
            messages.scrollTop = messages.scrollHeight;
        } else if (msg.type === "done") {
            document.getElementById("chat-send").disabled = false;
            document.getElementById("chat-input").disabled = false;
        } else if (msg.type === "error") {
            const errDiv = document.createElement("div");
            errDiv.className = "chat-msg error";
            errDiv.textContent = msg.message;
            messages.appendChild(errDiv);
            messages.scrollTop = messages.scrollHeight;
            document.getElementById("chat-send").disabled = false;
            document.getElementById("chat-input").disabled = false;
        }
    };

    agentWs.onclose = () => {
        agentWs = null;
    };

    return agentWs;
}

function sendChat() {
    const input = document.getElementById("chat-input");
    const prompt = input.value.trim();
    if (!prompt) return;

    // Add user message
    const messages = document.getElementById("chat-messages");
    const userDiv = document.createElement("div");
    userDiv.className = "chat-msg user";
    userDiv.textContent = prompt;
    messages.appendChild(userDiv);
    messages.scrollTop = messages.scrollHeight;

    // Clear input and disable while waiting
    input.value = "";
    document.getElementById("chat-send").disabled = true;
    input.disabled = true;

    // Create a new agent message div for streaming
    const agentDiv = document.createElement("div");
    agentDiv.className = "chat-msg agent";
    messages.appendChild(agentDiv);

    // Connect and send
    const ws = connectAgentWs();

    const send = () => {
        ws.send(JSON.stringify({
            prompt: prompt,
            target: selectedTarget || ""
        }));
    };

    if (ws.readyState === WebSocket.OPEN) {
        send();
    } else {
        ws.addEventListener("open", send, {once: true});
    }
}

// ─── Resize Handles ───
function initResizeHandles() {
    document.querySelectorAll(".resize-handle").forEach(handle => {
        handle.addEventListener("mousedown", (e) => {
            e.preventDefault();
            const leftPane = document.getElementById(handle.dataset.left);
            const rightPane = document.getElementById(handle.dataset.right);
            const startX = e.clientX;
            const startLeftW = leftPane.getBoundingClientRect().width;
            const startRightW = rightPane.getBoundingClientRect().width;

            handle.classList.add("active");
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";

            const onMove = (e) => {
                const dx = e.clientX - startX;
                const newLeft = Math.max(120, startLeftW + dx);
                const newRight = Math.max(120, startRightW - dx);

                leftPane.style.width = newLeft + "px";
                leftPane.style.flex = "none";
                rightPane.style.width = newRight + "px";
                rightPane.style.flex = "none";
            };

            const onUp = () => {
                handle.classList.remove("active");
                document.body.style.cursor = "";
                document.body.style.userSelect = "";
                document.removeEventListener("mousemove", onMove);
                document.removeEventListener("mouseup", onUp);
            };

            document.addEventListener("mousemove", onMove);
            document.addEventListener("mouseup", onUp);
        });
    });
}

// ─── Build Output Panel ───
function appendOutputLine(line) {
    const log = document.getElementById("output-log");
    if (!log) return;
    const div = document.createElement("div");
    div.className = "output-line";
    const ts = new Date().toLocaleTimeString();
    div.innerHTML = `<span class="ts">${ts}</span>${escapeHtml(line)}`;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function initOutputPanel() {
    const clearBtn = document.getElementById("output-clear");
    if (clearBtn) {
        clearBtn.addEventListener("click", () => {
            document.getElementById("output-log").innerHTML = "";
        });
    }

    // Vertical resize handle for output panel
    const handle = document.getElementById("output-resize-handle");
    if (!handle) return;

    handle.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const panel = document.getElementById("output-panel");
        const startY = e.clientY;
        const startH = panel.getBoundingClientRect().height;

        handle.classList.add("active");
        document.body.style.cursor = "row-resize";
        document.body.style.userSelect = "none";

        const onMove = (e) => {
            const dy = startY - e.clientY;
            panel.style.height = Math.max(60, startH + dy) + "px";
        };

        const onUp = () => {
            handle.classList.remove("active");
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
        };

        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
    });
}

// ─── Settings Modal ───
function initSettingsModal() {
    const overlay = document.getElementById("settings-overlay");
    const editor = document.getElementById("settings-editor");
    const openBtn = document.getElementById("settings-btn");
    const closeBtn = document.getElementById("settings-close");
    const cancelBtn = document.getElementById("settings-cancel");
    const saveBtn = document.getElementById("settings-save");

    if (!overlay || !openBtn) return;

    openBtn.addEventListener("click", async () => {
        try {
            const res = await fetch("/api/config");
            if (res.ok) {
                const data = await res.json();
                editor.value = data.content;
            } else {
                editor.value = "# config.yaml not found";
            }
        } catch (e) {
            editor.value = "# Failed to load config";
        }
        overlay.classList.remove("hidden");
    });

    const closeModal = () => overlay.classList.add("hidden");
    closeBtn.addEventListener("click", closeModal);
    cancelBtn.addEventListener("click", closeModal);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeModal();
    });

    saveBtn.addEventListener("click", async () => {
        try {
            const res = await fetch("/api/config", {
                method: "PUT",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({content: editor.value}),
            });
            if (res.ok) {
                closeModal();
            }
        } catch (e) {
            console.error("Failed to save config:", e);
        }
    });
}

// ─── Utilities ───
function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}
