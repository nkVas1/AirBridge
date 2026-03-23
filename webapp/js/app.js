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
    const uploadQueue = [];       // Queue of files waiting to be uploaded
    let uploadInProgress = false; // Guard against concurrent uploads

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
        batchActions: () => $("#batch-actions"),
        selectAllBtn: () => $("#select-all-btn"),
        downloadSelectedBtn: () => $("#download-selected-btn"),
    };

    // Image extensions for preview generation
    const IMAGE_EXTENSIONS = new Set([
        "jpg", "jpeg", "png", "gif", "webp", "bmp", "ico", "svg",
        "jfif", "tiff", "tif", "avif", "heif", "heic",
    ]);

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
        const ext = getFileExt(filename);
        const icons = {
            pdf: "📄", doc: "📝", docx: "📝", txt: "📝",
            jpg: "🖼️", jpeg: "🖼️", png: "🖼️", gif: "🖼️", webp: "🖼️", svg: "🖼️", heic: "🖼️",
            jfif: "🖼️", tiff: "🖼️", tif: "🖼️", avif: "🖼️", heif: "🖼️",
            mp4: "🎬", mov: "🎬", avi: "🎬", mkv: "🎬", webm: "🎬", m4v: "🎬",
            flv: "🎬", wmv: "🎬", "3gp": "🎬", ts: "🎬",
            mp3: "🎵", wav: "🎵", flac: "🎵", aac: "🎵", m4a: "🎵",
            zip: "📦", rar: "📦", "7z": "📦", tar: "📦", gz: "📦",
            html: "🌐", css: "🎨", js: "⚙️", py: "🐍", json: "📋",
            xls: "📊", xlsx: "📊", csv: "📊",
            ppt: "📊", pptx: "📊",
        };
        return icons[ext] || "📎";
    }

    function getFileExt(filename) {
        return filename.split(".").pop().toLowerCase();
    }

    function isImageFile(filename) {
        return IMAGE_EXTENSIONS.has(getFileExt(filename));
    }

    const VIDEO_EXTENSIONS = new Set([
        "mp4", "webm", "ogg", "mov", "mkv", "avi", "m4v", "3gp",
        "flv", "wmv", "ts", "mts", "m2ts",
    ]);

    const AUDIO_EXTENSIONS = new Set([
        "mp3", "wav", "ogg", "flac", "aac", "m4a", "webm",
    ]);

    const TEXT_EXTENSIONS = new Set([
        "txt", "md", "json", "js", "ts", "py", "html", "css", "xml",
        "csv", "yaml", "yml", "toml", "ini", "cfg", "conf", "log",
        "sh", "bash", "zsh", "bat", "ps1", "rb", "go", "rs",
        "java", "c", "cpp", "h", "hpp", "cs", "swift", "kt",
        "sql", "graphql", "dockerfile", "makefile", "gitignore",
    ]);

    function isVideoFile(filename) {
        return VIDEO_EXTENSIONS.has(getFileExt(filename));
    }

    function isAudioFile(filename) {
        return AUDIO_EXTENSIONS.has(getFileExt(filename));
    }

    function isTextFile(filename) {
        return TEXT_EXTENSIONS.has(getFileExt(filename));
    }

    function isPdfFile(filename) {
        return getFileExt(filename) === "pdf";
    }

    function generateSessionId() {
        const arr = new Uint8Array(16);
        crypto.getRandomValues(arr);
        return Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
    }

    /**
     * Generate a small thumbnail preview from a File object.
     * Returns a promise that resolves to a base64 data URL or null.
     */
    function generateThumbnail(file, maxSize) {
        maxSize = maxSize || 48;
        return new Promise((resolve) => {
            if (!file.type.startsWith("image/") && !isImageFile(file.name)) {
                resolve(null);
                return;
            }
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    const canvas = document.createElement("canvas");
                    let w = img.width;
                    let h = img.height;
                    if (w > h) {
                        if (w > maxSize) { h = Math.round(h * maxSize / w); w = maxSize; }
                    } else {
                        if (h > maxSize) { w = Math.round(w * maxSize / h); h = maxSize; }
                    }
                    canvas.width = w;
                    canvas.height = h;
                    const ctx = canvas.getContext("2d");
                    ctx.imageSmoothingEnabled = false;
                    ctx.drawImage(img, 0, 0, w, h);
                    resolve(canvas.toDataURL("image/jpeg", 0.5));
                };
                img.onerror = () => resolve(null);
                img.src = e.target.result;
            };
            reader.onerror = () => resolve(null);
            reader.readAsDataURL(file);
        });
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
        currentUpload = null;
        uploadQueue.length = 0;
        uploadInProgress = false;
        showScreen("auth");
        els.pinInput().value = "";
    }

    // --- File Upload (Queue-based) ---
    async function handleFiles(files) {
        if (!authenticated || !ws || ws.readyState !== WebSocket.OPEN) {
            console.error("[AirBridge] Not connected");
            return;
        }

        // Add all files to the queue (await each to ensure thumbnails are generated)
        for (const file of files) {
            await addToUploadQueue(file);
        }

        // Start processing if not already uploading
        processUploadQueue();
    }

    async function addToUploadQueue(file) {
        els.transferQueue().hidden = false;

        const itemId = "upload-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);

        // Generate thumbnail preview for image files
        let thumbnailHtml = '<span class="transfer-icon">' + getFileIcon(file.name) + '</span>';
        const thumb = await generateThumbnail(file, 40);
        if (thumb) {
            thumbnailHtml = '<img class="transfer-thumbnail" src="' + thumb + '" alt="preview">';
        }

        // Create transfer UI element
        const itemEl = document.createElement("div");
        itemEl.className = "transfer-item";
        itemEl.id = itemId;
        itemEl.innerHTML =
            '<div class="transfer-header">' +
                thumbnailHtml +
                '<div class="transfer-header-text">' +
                    '<span class="transfer-filename" title="' + escapeHtml(file.name) + '">' + escapeHtml(file.name) + '</span>' +
                    '<span class="transfer-size">' + formatSize(file.size) + '</span>' +
                '</div>' +
                '<button class="transfer-cancel-btn" id="' + itemId + '-cancel" title="Cancel">✕</button>' +
            '</div>' +
            '<div class="transfer-progress-bar">' +
                '<div class="transfer-progress-fill" id="' + itemId + '-bar"></div>' +
            '</div>' +
            '<div class="transfer-stats">' +
                '<span class="transfer-status" id="' + itemId + '-status">Queued</span>' +
                '<span id="' + itemId + '-speed"></span>' +
            '</div>';
        els.transferList().prepend(itemEl);

        // Attach cancel handler
        const cancelBtn = document.getElementById(itemId + "-cancel");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                cancelUpload(itemId);
            });
        }

        // Add to queue (don't send upload_start yet!)
        uploadQueue.push({
            file: file,
            itemId: itemId,
        });
    }

    function cancelUpload(itemId) {
        // Check if it's queued (not yet uploading)
        const queueIndex = uploadQueue.findIndex((item) => item.itemId === itemId);
        if (queueIndex !== -1) {
            uploadQueue.splice(queueIndex, 1);
            removeTransferItem(itemId);
            return;
        }

        // Check if it's the current upload
        if (currentUpload && currentUpload.itemId === itemId) {
            // Mark as cancelled in UI
            const bar = document.getElementById(itemId + "-bar");
            const statusEl = document.getElementById(itemId + "-status");
            if (bar) bar.classList.add("error");
            if (statusEl) {
                statusEl.textContent = "✗ Cancelled";
                statusEl.classList.add("error");
            }
            hideCancelBtn(itemId);

            currentUpload = null;
            uploadInProgress = false;

            // Process next file in queue
            processUploadQueue();
            return;
        }

        // Already completed/errored — just remove from UI
        removeTransferItem(itemId);
    }

    function removeTransferItem(itemId) {
        const el = document.getElementById(itemId);
        if (el) {
            el.style.animation = "fadeOut 0.2s ease";
            el.addEventListener("animationend", () => el.remove());
        }
    }

    function hideCancelBtn(itemId) {
        const btn = document.getElementById(itemId + "-cancel");
        if (btn) btn.hidden = true;
    }

    function processUploadQueue() {
        if (uploadInProgress) return;
        if (uploadQueue.length === 0) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        uploadInProgress = true;
        const next = uploadQueue.shift();

        // Now send upload_start for this specific file
        updateTransferStatus(next.itemId, "Preparing...");

        ws.send(JSON.stringify({
            type: "upload_start",
            filename: next.file.name,
            size: next.file.size,
            mime_type: next.file.type || "application/octet-stream",
        }));

        // Set as current upload
        currentUpload = {
            file: next.file,
            itemId: next.itemId,
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
        hideCancelBtn(itemId);

        currentUpload = null;
        uploadInProgress = false;

        // Process next file in queue
        if (uploadQueue.length > 0) {
            processUploadQueue();
        } else {
            // All uploads done — refresh file list
            setTimeout(loadFileList, 500);
        }
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
        hideCancelBtn(itemId);
        currentUpload = null;
        uploadInProgress = false;

        // Try next file in queue even if this one failed
        if (uploadQueue.length > 0) {
            processUploadQueue();
        }
    }

    // --- File Download ---
    let downloadBuffer = [];
    let downloadInfo = null;
    let downloadQueue = [];       // Queue for batch downloads
    let downloadInProgress = false;

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
        downloadInProgress = false;

        // Process next download in batch queue
        if (downloadQueue.length > 0) {
            processDownloadQueue();
        }
    }

    function downloadFile(filename) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        ws.send(JSON.stringify({
            type: "download_request",
            filename: filename,
        }));
    }

    // --- Batch Download ---
    function downloadSelected() {
        const checkboxes = document.querySelectorAll(".file-select:checked");
        if (checkboxes.length === 0) return;

        downloadQueue = [];
        checkboxes.forEach((cb) => {
            downloadQueue.push(cb.dataset.filename);
        });
        downloadInProgress = false;
        processDownloadQueue();
    }

    function processDownloadQueue() {
        if (downloadInProgress) return;
        if (downloadQueue.length === 0) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        downloadInProgress = true;
        const filename = downloadQueue.shift();
        downloadFile(filename);
    }

    function toggleSelectAll() {
        const checkboxes = document.querySelectorAll(".file-select");
        const allChecked = Array.from(checkboxes).every((cb) => cb.checked);
        checkboxes.forEach((cb) => { cb.checked = !allChecked; });
        updateBatchActions();
    }

    function updateBatchActions() {
        const checked = document.querySelectorAll(".file-select:checked").length;
        const dlBtn = els.downloadSelectedBtn();
        if (dlBtn) {
            dlBtn.disabled = checked === 0;
            dlBtn.textContent = checked > 0 ? "Download (" + checked + ")" : "Download";
        }
    }

    // --- File List ---
    let currentSortField = "date"; // "date", "size", "type", "name"
    let currentSortOrder = "desc"; // "asc", "desc"

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

    function sortFiles(files) {
        const sorted = [...files];
        sorted.sort((a, b) => {
            let cmp = 0;
            switch (currentSortField) {
                case "date":
                    cmp = (a.modified_at || 0) - (b.modified_at || 0);
                    break;
                case "size":
                    cmp = a.size - b.size;
                    break;
                case "type": {
                    const extA = a.name.includes(".") ? a.name.split(".").pop().toLowerCase() : "";
                    const extB = b.name.includes(".") ? b.name.split(".").pop().toLowerCase() : "";
                    cmp = extA.localeCompare(extB);
                    break;
                }
                case "name":
                    cmp = a.name.localeCompare(b.name);
                    break;
            }
            return currentSortOrder === "asc" ? cmp : -cmp;
        });
        return sorted;
    }

    function formatDate(timestamp) {
        if (!timestamp) return "";
        const d = new Date(timestamp * 1000);
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const fileDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
        const diffDays = Math.floor((today - fileDay) / (1000 * 60 * 60 * 24));

        const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

        if (diffDays === 0) return "Today, " + time;
        if (diffDays === 1) return "Yesterday, " + time;
        if (diffDays < 7) return diffDays + "d ago, " + time;
        return d.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + time;
    }

    function renderFileList(files) {
        const container = els.fileList();
        const batchActions = els.batchActions();

        if (files.length === 0) {
            container.innerHTML = '<p class="empty-state">No files yet. Send files from your device!</p>';
            if (batchActions) batchActions.hidden = true;
            return;
        }

        if (batchActions) batchActions.hidden = false;

        // Sort files
        const sorted = sortFiles(files);

        container.innerHTML = sorted.map((f) => {
            const previewHtml = isImageFile(f.name)
                ? '<img class="file-thumbnail" src="/api/files/' + encodeURIComponent(f.name) + '?session_id=' + encodeURIComponent(sessionId) + '" alt="preview" loading="lazy">'
                : '<span class="file-icon">' + getFileIcon(f.name) + '</span>';

            const dateHtml = f.modified_at
                ? '<span class="file-date">' + formatDate(f.modified_at) + '</span>'
                : '';

            return '<div class="file-item">' +
                '<input type="checkbox" class="file-select" data-filename="' + escapeHtml(f.name) + '">' +
                previewHtml +
                '<div class="file-info">' +
                    '<div class="file-name" title="' + escapeHtml(f.name) + '">' + escapeHtml(f.name) + '</div>' +
                    '<div class="file-meta">' +
                        '<span class="file-size">' + formatSize(f.size) + '</span>' +
                        dateHtml +
                    '</div>' +
                '</div>' +
                '<button class="file-download" data-filename="' + escapeHtml(f.name) + '" aria-label="Download ' + escapeHtml(f.name) + '">⬇</button>' +
            '</div>';
        }).join("");

        // Attach download handlers
        container.querySelectorAll(".file-download").forEach((btn) => {
            btn.addEventListener("click", () => {
                const filename = btn.dataset.filename;
                downloadFile(filename);
            });
        });

        // Attach checkbox change handlers
        container.querySelectorAll(".file-select").forEach((cb) => {
            cb.addEventListener("change", updateBatchActions);
        });

        // Attach preview handlers on file icons and thumbnails
        container.querySelectorAll(".file-icon, .file-thumbnail").forEach((el) => {
            const fileItem = el.closest(".file-item");
            if (!fileItem) return;
            const checkbox = fileItem.querySelector(".file-select");
            if (!checkbox) return;
            el.addEventListener("click", (e) => {
                e.stopPropagation();
                openPreviewModal(checkbox.dataset.filename);
            });
        });

        updateBatchActions();
    }

    // --- File Preview Modal ---
    function openPreviewModal(filename) {
        const modal = $("#preview-modal");
        const content = $("#preview-content");
        const filenameEl = $("#preview-filename");

        if (!modal || !content) return;

        filenameEl.textContent = filename;
        content.replaceChildren();
        const loadingSpan = document.createElement("span");
        loadingSpan.className = "preview-loading";
        loadingSpan.textContent = "Loading...";
        content.appendChild(loadingSpan);
        modal.hidden = false;
        modal.classList.remove("fullscreen");
        document.body.style.overflow = "hidden";

        const fileUrl = "/api/files/" + encodeURIComponent(filename) + "?session_id=" + encodeURIComponent(sessionId);

        if (isImageFile(filename)) {
            const img = document.createElement("img");
            img.alt = filename;
            img.src = fileUrl;
            img.onload = () => { content.replaceChildren(img); };
            img.onerror = () => { showPreviewError(content, filename); };
        } else if (isVideoFile(filename)) {
            const video = document.createElement("video");
            video.controls = true;
            video.playsInline = true;
            video.preload = "auto";
            video.src = fileUrl;
            video.onerror = () => { showPreviewError(content, filename); };
            content.replaceChildren(video);
        } else if (isAudioFile(filename)) {
            const audio = document.createElement("audio");
            audio.controls = true;
            audio.preload = "metadata";
            audio.src = fileUrl;
            audio.onerror = () => { showPreviewError(content, filename); };
            content.replaceChildren(audio);
        } else if (isPdfFile(filename)) {
            renderPdfPreview(content, fileUrl, filename);
        } else if (isTextFile(filename)) {
            fetch(fileUrl, { method: "HEAD" })
                .then((headResp) => {
                    const size = parseInt(headResp.headers.get("Content-Length") || "0", 10);
                    // Limit text preview to 5 MB
                    if (size > 5 * 1024 * 1024) {
                        showPreviewError(content, filename);
                        return;
                    }
                    return fetch(fileUrl);
                })
                .then((resp) => {
                    if (!resp || !resp.ok) throw new Error("Failed to load");
                    return resp.text();
                })
                .then((text) => {
                    if (text === undefined) return;
                    const pre = document.createElement("pre");
                    pre.textContent = text;
                    content.replaceChildren(pre);
                })
                .catch(() => { showPreviewError(content, filename); });
        } else {
            showPreviewError(content, filename);
        }
    }

    // --- PDF Preview with pdf.js ---
    function renderPdfPreview(container, fileUrl, filename) {
        const script = document.createElement("script");
        script.type = "module";
        script.textContent = `
            import { GlobalWorkerOptions, getDocument } from "/static/js/vendor/pdf.min.mjs";
            GlobalWorkerOptions.workerSrc = "/static/js/vendor/pdf.worker.min.mjs";

            const container = document.getElementById("preview-content");
            const url = ${JSON.stringify(fileUrl)};

            try {
                const pdf = await getDocument(url).promise;
                const totalPages = pdf.numPages;

                // Build viewer UI
                const wrapper = document.createElement("div");
                wrapper.className = "pdf-viewer";

                const navBar = document.createElement("div");
                navBar.className = "pdf-nav";

                const prevBtn = document.createElement("button");
                prevBtn.className = "preview-btn pdf-nav-btn";
                prevBtn.textContent = "◀";
                prevBtn.title = "Previous page";

                const pageInfo = document.createElement("span");
                pageInfo.className = "pdf-page-info";

                const nextBtn = document.createElement("button");
                nextBtn.className = "preview-btn pdf-nav-btn";
                nextBtn.textContent = "▶";
                nextBtn.title = "Next page";

                navBar.append(prevBtn, pageInfo, nextBtn);

                const canvasContainer = document.createElement("div");
                canvasContainer.className = "pdf-canvas-container";

                const canvas = document.createElement("canvas");
                canvas.className = "pdf-canvas";
                canvasContainer.appendChild(canvas);

                wrapper.append(navBar, canvasContainer);
                container.replaceChildren(wrapper);

                let currentPage = 1;
                const ctx = canvas.getContext("2d");

                async function renderPage(num) {
                    const page = await pdf.getPage(num);
                    const containerWidth = canvasContainer.clientWidth || 800;
                    const baseViewport = page.getViewport({ scale: 1 });
                    const scale = Math.min(containerWidth / baseViewport.width, 3);
                    const viewport = page.getViewport({ scale });

                    canvas.width = viewport.width;
                    canvas.height = viewport.height;

                    await page.render({ canvasContext: ctx, viewport }).promise;
                    pageInfo.textContent = num + " / " + totalPages;
                    prevBtn.disabled = num <= 1;
                    nextBtn.disabled = num >= totalPages;
                }

                prevBtn.addEventListener("click", () => {
                    if (currentPage > 1) { currentPage--; renderPage(currentPage); }
                });
                nextBtn.addEventListener("click", () => {
                    if (currentPage < totalPages) { currentPage++; renderPage(currentPage); }
                });

                renderPage(1);
            } catch (err) {
                container.innerHTML =
                    '<div class="preview-unsupported">' +
                        '<span class="preview-unsupported-icon">📄</span>' +
                        'Preview not available for this file type.<br>' +
                        'Use the download button to save it.' +
                    '</div>';
            }
        `;
        document.body.appendChild(script);
        // Module scripts run asynchronously; remove the element after it has been queued.
        setTimeout(() => script.remove(), 0);
    }

    function showPreviewError(container, filename) {
        container.innerHTML =
            '<div class="preview-unsupported">' +
                '<span class="preview-unsupported-icon">' + getFileIcon(filename) + '</span>' +
                'Preview not available for this file type.<br>' +
                'Use the download button to save it.' +
            '</div>';
    }

    function closePreviewModal() {
        const modal = $("#preview-modal");
        if (!modal || modal.hidden) return;

        // Pause any playing media
        const video = modal.querySelector("video");
        const audio = modal.querySelector("audio");
        if (video) { video.pause(); video.removeAttribute("src"); video.load(); }
        if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); }

        modal.hidden = true;
        modal.classList.remove("fullscreen");
        document.body.style.overflow = "";
    }

    function togglePreviewFullscreen() {
        const modal = $("#preview-modal");
        if (!modal || modal.hidden) return;
        modal.classList.toggle("fullscreen");
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
                // Copy to static array before clearing the input —
                // FileList is a live reference that empties when value is reset.
                const files = Array.from(e.target.files);
                fileInput.value = "";
                handleFiles(files);
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

        // Sort controls
        const sortSelect = $("#sort-select");
        if (sortSelect) {
            sortSelect.addEventListener("change", () => {
                currentSortField = sortSelect.value;
                loadFileList();
            });
        }
        const sortOrderBtn = $("#sort-order-btn");
        if (sortOrderBtn) {
            sortOrderBtn.addEventListener("click", () => {
                currentSortOrder = currentSortOrder === "asc" ? "desc" : "asc";
                sortOrderBtn.textContent = currentSortOrder === "asc" ? "▲" : "▼";
                sortOrderBtn.title = currentSortOrder === "asc" ? "Ascending" : "Descending";
                loadFileList();
            });
        }

        // Batch download controls
        const selectAllBtn = els.selectAllBtn();
        if (selectAllBtn) selectAllBtn.addEventListener("click", toggleSelectAll);
        const dlSelectedBtn = els.downloadSelectedBtn();
        if (dlSelectedBtn) dlSelectedBtn.addEventListener("click", downloadSelected);

        // Keep alive ping
        setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        }, 30000);

        // Preview modal controls
        const previewCloseBtn = $("#preview-close-btn");
        if (previewCloseBtn) previewCloseBtn.addEventListener("click", closePreviewModal);

        const previewFullscreenBtn = $("#preview-fullscreen-btn");
        if (previewFullscreenBtn) previewFullscreenBtn.addEventListener("click", togglePreviewFullscreen);

        const previewOverlay = $(".preview-overlay");
        if (previewOverlay) previewOverlay.addEventListener("click", closePreviewModal);

        document.addEventListener("keydown", (e) => {
            const modal = $("#preview-modal");
            if (e.key === "Escape" && modal && !modal.hidden) closePreviewModal();
        });

        console.log("[AirBridge] Initialized");
    }

    // --- Start ---
    document.addEventListener("DOMContentLoaded", init);

    return { init };
})();
