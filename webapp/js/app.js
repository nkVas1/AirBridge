/**
 * AirBridge PWA — Main Application Logic
 *
 * Handles authentication, file transfer via WebSocket,
 * drag-and-drop, progress tracking, and UI management.
 */

"use strict";

const AirBridge = (() => {
    // --- State ---
    let ws = null;
    let sessionId = "";
    let authenticated = false;
    let serverUrl = "";
    let chunkSize = 65536;
    let currentUpload = null;

    // --- DOM Elements ---
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    const els = {
        authScreen: () => $("#auth-screen"),
        mainScreen: () => $("#main-screen"),
        pinInput: () => $("#pin-input"),
        connectBtn: () => $("#connect-btn"),
        authError: () => $("#auth-error"),
        serverInfo: () => $("#server-info"),
        serverUrl: () => $("#server-url"),
        disconnectBtn: () => $("#disconnect-btn"),
        connectionStatus: () => $("#connection-status"),
        dropZone: () => $("#drop-zone"),
        fileInput: () => $("#file-input"),
        transferQueue: () => $("#transfer-queue"),
        transferList: () => $("#transfer-list"),
        fileList: () => $("#file-list"),
        refreshFiles: () => $("#refresh-files"),
    };

    // --- Utilities ---
    function formatSize(bytes) {
        if (bytes === 0) return "0 B";
        const units = ["B", "KB", "MB", "GB", "TB"];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + " " + units[i];
    }

    function formatSpeed(bps) {
        return formatSize(bps) + "/s";
    }

    function formatTime(seconds) {
        if (seconds <= 0) return "--";
        if (seconds < 60) return Math.ceil(seconds) + "s";
        if (seconds < 3600) return Math.floor(seconds / 60) + "m " + Math.ceil(seconds % 60) + "s";
        return Math.floor(seconds / 3600) + "h " + Math.floor((seconds % 3600) / 60) + "m";
    }

    function getFileIcon(filename) {
        const ext = filename.split(".").pop().toLowerCase();
        const icons = {
            pdf: "📄", doc: "📝", docx: "📝", txt: "📝",
            jpg: "🖼️", jpeg: "🖼️", png: "🖼️", gif: "🖼️", webp: "🖼️", svg: "🖼️", heic: "🖼️",
            mp4: "🎬", mov: "🎬", avi: "🎬", mkv: "🎬",
            mp3: "🎵", wav: "🎵", flac: "🎵", aac: "🎵", m4a: "🎵",
            zip: "📦", rar: "📦", "7z": "📦", tar: "📦", gz: "📦",
            html: "🌐", css: "🎨", js: "⚙️", py: "🐍", json: "📋",
            xls: "📊", xlsx: "📊", csv: "📊",
            ppt: "📊", pptx: "📊",
        };
        return icons[ext] || "📎";
    }

    function generateSessionId() {
        const arr = new Uint8Array(16);
        crypto.getRandomValues(arr);
        return Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
    }

    // --- WebSocket ---
    function connectWebSocket() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = protocol + "//" + location.host + "/ws";

        ws = new WebSocket(wsUrl);
        ws.binaryType = "arraybuffer";

        ws.onopen = () => {
            console.log("[AirBridge] WebSocket connected");
            // Authenticate
            ws.send(JSON.stringify({
                type: "auth",
                pin: els.pinInput().value.trim(),
                session_id: sessionId,
            }));
        };

        ws.onmessage = handleMessage;

        ws.onclose = () => {
            console.log("[AirBridge] WebSocket disconnected");
            updateConnectionStatus(false);
            if (authenticated) {
                // Auto-reconnect after 2 seconds
                setTimeout(() => {
                    if (authenticated) connectWebSocket();
                }, 2000);
            }
        };

        ws.onerror = (err) => {
            console.error("[AirBridge] WebSocket error:", err);
        };
    }

    function handleMessage(event) {
        if (event.data instanceof ArrayBuffer) {
            // Binary data = download chunk
            handleDownloadChunk(event.data);
            return;
        }

        const data = JSON.parse(event.data);
        console.log("[AirBridge] Message:", data.type);

        switch (data.type) {
            case "auth_result":
                handleAuthResult(data);
                break;
            case "upload_ready":
                handleUploadReady(data);
                break;
            case "chunk_ack":
                handleChunkAck(data);
                break;
            case "upload_complete":
                handleUploadComplete(data);
                break;
            case "download_start":
                handleDownloadStart(data);
                break;
            case "download_chunk":
                // Next message will be binary
                break;
            case "download_complete":
                handleDownloadComplete(data);
                break;
            case "error":
                console.error("[AirBridge] Server error:", data.message);
                showTransferError(data.message);
                break;
            case "pong":
                break;
            default:
                console.log("[AirBridge] Unknown message type:", data.type);
        }
    }

    // --- Authentication ---
    function handleAuthResult(data) {
        if (data.authenticated) {
            sessionId = data.session_id;
            authenticated = true;
            showScreen("main");
            updateConnectionStatus(true);
            loadFileList();
        } else {
            els.authError().hidden = false;
            els.pinInput().classList.add("error");
            els.pinInput().focus();
        }
    }

    async function authenticate() {
        const pin = els.pinInput().value.trim();
        if (pin.length < 1) {
            els.authError().hidden = false;
            return;
        }

        els.authError().hidden = true;
        sessionId = generateSessionId();
        connectWebSocket();
    }

    function disconnect() {
        authenticated = false;
        if (ws) {
            ws.close();
            ws = null;
        }
        sessionId = "";
        showScreen("auth");
        els.pinInput().value = "";
    }

    // --- File Upload ---
    function handleFiles(files) {
        if (!authenticated || !ws || ws.readyState !== WebSocket.OPEN) {
            console.error("[AirBridge] Not connected");
            return;
        }

        for (const file of files) {
            queueUpload(file);
        }
    }

    function queueUpload(file) {
        els.transferQueue().hidden = false;

        const itemId = "upload-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);

        // Create transfer UI element
        const itemEl = document.createElement("div");
        itemEl.className = "transfer-item";
        itemEl.id = itemId;
        itemEl.innerHTML = `
            <div class="transfer-header">
                <span class="transfer-filename" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
                <span class="transfer-size">${formatSize(file.size)}</span>
            </div>
            <div class="transfer-progress-bar">
                <div class="transfer-progress-fill" id="${itemId}-bar"></div>
            </div>
            <div class="transfer-stats">
                <span class="transfer-status" id="${itemId}-status">Preparing...</span>
                <span id="${itemId}-speed"></span>
            </div>
        `;
        els.transferList().prepend(itemEl);

        // Start upload via WebSocket
        ws.send(JSON.stringify({
            type: "upload_start",
            filename: file.name,
            size: file.size,
            mime_type: file.type || "application/octet-stream",
        }));

        // Store file reference for chunk sending
        currentUpload = {
            file: file,
            itemId: itemId,
            transferId: null,
            offset: 0,
            totalChunks: 0,
            sentChunks: 0,
        };
    }

    function handleUploadReady(data) {
        if (!currentUpload) return;

        currentUpload.transferId = data.transfer_id;
        currentUpload.totalChunks = data.total_chunks;
        chunkSize = data.chunk_size || chunkSize;

        updateTransferStatus(currentUpload.itemId, "Uploading...");
        sendNextChunk();
    }

    function sendNextChunk() {
        if (!currentUpload || !ws || ws.readyState !== WebSocket.OPEN) return;

        const { file, offset, transferId } = currentUpload;

        if (offset >= file.size) {
            return; // Wait for server confirmation
        }

        const end = Math.min(offset + chunkSize, file.size);
        const chunk = file.slice(offset, end);

        // Signal chunk metadata
        ws.send(JSON.stringify({
            type: "upload_chunk",
            transfer_id: transferId,
        }));

        // Read and send binary data
        const reader = new FileReader();
        reader.onload = (e) => {
            ws.send(e.target.result);
            currentUpload.offset = end;
            currentUpload.sentChunks++;
        };
        reader.readAsArrayBuffer(chunk);
    }

    function handleChunkAck(data) {
        if (!currentUpload) return;

        const { itemId } = currentUpload;
        const bar = document.getElementById(itemId + "-bar");
        const statusEl = document.getElementById(itemId + "-status");
        const speedEl = document.getElementById(itemId + "-speed");

        if (bar) bar.style.width = data.progress + "%";
        if (statusEl) statusEl.textContent = data.progress.toFixed(1) + "%";
        if (speedEl) {
            const speed = data.speed_bps > 0 ? formatSpeed(data.speed_bps) : "";
            const eta = data.eta_seconds > 0 ? " • " + formatTime(data.eta_seconds) + " left" : "";
            speedEl.textContent = speed + eta;
        }

        // Send next chunk
        sendNextChunk();
    }

    function handleUploadComplete(data) {
        if (!currentUpload) return;

        const { itemId } = currentUpload;
        const bar = document.getElementById(itemId + "-bar");
        const statusEl = document.getElementById(itemId + "-status");
        const speedEl = document.getElementById(itemId + "-speed");

        if (bar) {
            bar.style.width = "100%";
            bar.classList.add("complete");
        }
        if (statusEl) {
            statusEl.textContent = "✓ Complete";
            statusEl.classList.add("complete");
        }
        if (speedEl) speedEl.textContent = "";

        currentUpload = null;
        // Refresh file list
        setTimeout(loadFileList, 500);
    }

    function showTransferError(message) {
        if (!currentUpload) return;

        const { itemId } = currentUpload;
        const bar = document.getElementById(itemId + "-bar");
        const statusEl = document.getElementById(itemId + "-status");

        if (bar) bar.classList.add("error");
        if (statusEl) {
            statusEl.textContent = "✗ " + message;
            statusEl.classList.add("error");
        }
        currentUpload = null;
    }

    // --- File Download ---
    let downloadBuffer = [];
    let downloadInfo = null;

    function handleDownloadStart(data) {
        downloadInfo = data;
        downloadBuffer = [];
    }

    function handleDownloadChunk(arrayBuffer) {
        if (!downloadInfo) return;
        downloadBuffer.push(new Uint8Array(arrayBuffer));
    }

    function handleDownloadComplete(data) {
        if (!downloadInfo || downloadBuffer.length === 0) return;

        // Merge chunks
        const totalSize = downloadBuffer.reduce((acc, chunk) => acc + chunk.length, 0);
        const merged = new Uint8Array(totalSize);
        let offset = 0;
        for (const chunk of downloadBuffer) {
            merged.set(chunk, offset);
            offset += chunk.length;
        }

        // Trigger browser download
        const blob = new Blob([merged], { type: downloadInfo.mime_type || "application/octet-stream" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = downloadInfo.filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        downloadInfo = null;
        downloadBuffer = [];
    }

    // --- File List ---
    async function loadFileList() {
        if (!sessionId) return;

        try {
            const resp = await fetch("/api/files?session_id=" + encodeURIComponent(sessionId));
            if (!resp.ok) return;

            const data = await resp.json();
            renderFileList(data.files || []);
        } catch (err) {
            console.error("[AirBridge] Failed to load files:", err);
        }
    }

    function renderFileList(files) {
        const container = els.fileList();
        if (files.length === 0) {
            container.innerHTML = '<p class="empty-state">No files yet. Send files from your device!</p>';
            return;
        }

        container.innerHTML = files.map((f) => `
            <div class="file-item">
                <span class="file-icon">${getFileIcon(f.name)}</span>
                <div class="file-info">
                    <div class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
                    <div class="file-size">${formatSize(f.size)}</div>
                </div>
                <button class="file-download" data-filename="${escapeHtml(f.name)}">Download</button>
            </div>
        `).join("");

        // Attach download handlers
        container.querySelectorAll(".file-download").forEach((btn) => {
            btn.addEventListener("click", () => {
                const filename = btn.dataset.filename;
                downloadFile(filename);
            });
        });
    }

    function downloadFile(filename) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        ws.send(JSON.stringify({
            type: "download_request",
            filename: filename,
        }));
    }

    // --- UI Helpers ---
    function showScreen(name) {
        $$(".screen").forEach((s) => s.classList.remove("active"));
        if (name === "auth") {
            els.authScreen().classList.add("active");
        } else {
            els.mainScreen().classList.add("active");
        }
    }

    function updateConnectionStatus(connected) {
        const dot = els.connectionStatus();
        if (connected) {
            dot.classList.add("connected");
            dot.classList.remove("disconnected");
        } else {
            dot.classList.remove("connected");
            dot.classList.add("disconnected");
        }
    }

    function updateTransferStatus(itemId, status) {
        const statusEl = document.getElementById(itemId + "-status");
        if (statusEl) statusEl.textContent = status;
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // --- Event Setup ---
    function init() {
        // Auth
        els.connectBtn().addEventListener("click", authenticate);
        els.pinInput().addEventListener("keydown", (e) => {
            if (e.key === "Enter") authenticate();
        });
        els.pinInput().addEventListener("input", () => {
            els.authError().hidden = true;
            els.pinInput().classList.remove("error");
        });
        els.disconnectBtn().addEventListener("click", disconnect);

        // Tabs
        $$(".tab-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                $$(".tab-btn").forEach((b) => b.classList.remove("active"));
                $$(".tab-content").forEach((c) => c.classList.remove("active"));
                btn.classList.add("active");
                const tab = btn.dataset.tab;
                const tabContent = document.getElementById("tab-" + tab);
                if (tabContent) tabContent.classList.add("active");

                if (tab === "receive") loadFileList();
            });
        });

        // File selection
        const dropZone = els.dropZone();
        const fileInput = els.fileInput();

        dropZone.addEventListener("click", () => fileInput.click());

        fileInput.addEventListener("change", (e) => {
            if (e.target.files.length > 0) {
                handleFiles(e.target.files);
                fileInput.value = "";
            }
        });

        // Drag & Drop
        dropZone.addEventListener("dragover", (e) => {
            e.preventDefault();
            dropZone.classList.add("drag-over");
        });

        dropZone.addEventListener("dragleave", () => {
            dropZone.classList.remove("drag-over");
        });

        dropZone.addEventListener("drop", (e) => {
            e.preventDefault();
            dropZone.classList.remove("drag-over");
            if (e.dataTransfer.files.length > 0) {
                handleFiles(e.dataTransfer.files);
            }
        });

        // Refresh files
        els.refreshFiles().addEventListener("click", loadFileList);

        // Keep alive ping
        setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        }, 30000);

        console.log("[AirBridge] Initialized");
    }

    // --- Start ---
    document.addEventListener("DOMContentLoaded", init);

    return { init };
})();
