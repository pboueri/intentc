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
    const container = document.getElementById("module-tree");
    container.innerHTML = "";

    if (!data.tree || !data.tree.children) return;

    // Store flat node data for upstream lookups
    dagData = data;

    function createTreeNode(node, depth) {
        const div = document.createElement("div");
        div.className = "tree-item";
        div.style.paddingLeft = (depth * 16) + "px";

        if (node.type === "module") {
            // Module: collapsible header
            const header = document.createElement("div");
            header.className = "tree-module";

            const toggle = document.createElement("span");
            toggle.className = "tree-toggle";
            toggle.textContent = "\u25BC"; // ▼
            header.appendChild(toggle);

            const label = document.createElement("span");
            label.className = "tree-module-label";
            label.textContent = node.name;
            header.appendChild(label);

            div.appendChild(header);

            const childContainer = document.createElement("div");
            childContainer.className = "tree-children";

            if (node.children) {
                node.children.forEach(child => {
                    childContainer.appendChild(createTreeNode(child, depth + 1));
                });
            }
            div.appendChild(childContainer);

            // Toggle collapse
            header.addEventListener("click", (e) => {
                e.stopPropagation();
                const isCollapsed = childContainer.classList.toggle("collapsed");
                toggle.textContent = isCollapsed ? "\u25B6" : "\u25BC"; // ▶ or ▼
            });
        } else {
            // Feature: clickable leaf node
            const leaf = document.createElement("div");
            leaf.className = "tree-feature" + (selectedTarget === node.path ? " selected" : "");
            leaf.setAttribute("data-path", node.path);

            const dot = document.createElement("span");
            dot.className = "tree-status status-" + (node.status || "pending");
            leaf.appendChild(dot);

            const label = document.createElement("span");
            label.className = "tree-feature-label";
            label.textContent = node.name;
            leaf.appendChild(label);

            leaf.addEventListener("click", (e) => {
                e.stopPropagation();
                selectTarget(node.path);
            });

            div.appendChild(leaf);
        }

        return div;
    }

    data.tree.children.forEach(child => {
        container.appendChild(createTreeNode(child, 0));
    });
}

// ─── Target Selection ───
async function selectTarget(name) {
    selectedTarget = name;
    document.getElementById("editor-target-name").textContent = name;

    // Update tree selection visual
    document.querySelectorAll(".tree-feature").forEach(el => {
        el.classList.toggle("selected", el.getAttribute("data-path") === name);
    });

    // Fetch target details and upstream deps in parallel
    try {
        const [targetRes, upstreamRes] = await Promise.all([
            fetch(`/api/targets/${encodeURIComponent(name)}`),
            fetch(`/api/targets/${encodeURIComponent(name)}/upstream`),
        ]);

        if (!targetRes.ok) {
            document.getElementById("editor-content").innerHTML = `<p class="placeholder">Target not found</p>`;
            return;
        }

        const target = await targetRes.json();
        const upstream = upstreamRes.ok ? await upstreamRes.json() : { upstream: [] };
        renderEditor(target, upstream.upstream);
    } catch (e) {
        console.error("Failed to load target:", e);
    }
}

function renderEditor(target, upstream) {
    const container = document.getElementById("editor-content");
    let html = "";

    // Action bar
    html += `<div class="action-bar">
        <button class="action-btn action-build" onclick="triggerAction('build', '${target.name}', this)">Build</button>
        <button class="action-btn action-clean" onclick="triggerAction('clean', '${target.name}', this)">Clean</button>
        <button class="action-btn action-validate" onclick="triggerAction('validate', '${target.name}', this)">Validate</button>
    </div>`;

    // Upstream dependencies section
    if (upstream && upstream.length > 0) {
        html += `<div class="upstream-deps">
            <div class="upstream-header">Upstream Dependencies</div>
            <div class="upstream-chain">`;
        upstream.forEach((dep, i) => {
            html += `<span class="upstream-dep" onclick="selectTarget('${dep}')">${dep}</span>`;
            if (i < upstream.length - 1) html += `<span class="upstream-arrow">\u2192</span>`;
        });
        html += `</div></div>`;
    }

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
            if (selectedTarget && msg.path) {
                const intentPrefix = "intent/";
                let relPath = msg.path;
                if (relPath.startsWith(intentPrefix)) {
                    relPath = relPath.substring(intentPrefix.length);
                }
                const lastSlash = relPath.lastIndexOf("/");
                const dirPath = lastSlash >= 0 ? relPath.substring(0, lastSlash) : relPath;
                if (dirPath === selectedTarget) {
                    selectTarget(selectedTarget);
                }
            }
        } else if (msg.type === "build_complete" || msg.type === "validate_complete") {
            // Show completion in output panel
            if (msg.type === "build_complete") {
                appendOutputLine(msg.success ? `Build complete: ${msg.target} succeeded` : `Build complete: ${msg.target} FAILED — ${msg.error || "unknown error"}`);
            } else {
                appendOutputLine(`Validation complete: ${msg.target} — ${msg.passed || 0}/${msg.total || 0} passed`);
            }
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
