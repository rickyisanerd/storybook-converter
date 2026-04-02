/**
 * upload.js — Audiobook Converter front-end logic
 *
 * Responsibilities:
 *  1. Handle form submission → POST /api/upload
 *  2. Poll /api/status/<job_id> every 2 s for progress updates
 *  3. Render chapter-by-chapter progress list + progress bar
 *  4. Show download links when conversion is complete
 *  5. Fetch and display the manifest JSON on demand
 */

"use strict";

// ── DOM refs ──────────────────────────────────────────────────────────────
const form            = document.getElementById("upload-form");
const fileInput       = document.getElementById("file-input");
const fileLabel       = document.getElementById("file-label");
const dropZone        = document.getElementById("drop-zone");
const engineSelect    = document.getElementById("engine-select");
const voiceGroup      = document.getElementById("voice-group");
const rateSlider      = document.getElementById("rate-slider");
const rateDisplay     = document.getElementById("rate-display");
const submitBtn       = document.getElementById("submit-btn");

const uploadSection   = document.getElementById("upload-section");
const progressSection = document.getElementById("progress-section");
const resultsSection  = document.getElementById("results-section");

const jobIdDisplay    = document.getElementById("job-id-display");
const statusBadge     = document.getElementById("status-badge");
const progressBar     = document.getElementById("progress-bar");
const progressLabel   = document.getElementById("progress-label");
const chapterList     = document.getElementById("chapter-list");
const errorBox        = document.getElementById("error-box");

const bookTitleDisplay = document.getElementById("book-title-display");
const downloadList     = document.getElementById("download-list");
const manifestBtn      = document.getElementById("manifest-btn");
const manifestViewer   = document.getElementById("manifest-viewer");
const manifestContent  = document.getElementById("manifest-content");
const convertAnotherBtn = document.getElementById("convert-another-btn");

// ── State ─────────────────────────────────────────────────────────────────
let currentJobId   = null;
let pollIntervalId = null;

// ── Rate slider ───────────────────────────────────────────────────────────
rateSlider.addEventListener("input", () => {
  const v = parseInt(rateSlider.value, 10);
  rateDisplay.textContent = v >= 0 ? `+${v}%` : `${v}%`;
});

// ── Engine toggle: hide voice selector for non-edge-tts engines ───────────
engineSelect.addEventListener("change", () => {
  voiceGroup.classList.toggle("hidden", engineSelect.value !== "edge-tts");
});

// ── File drop zone ────────────────────────────────────────────────────────
fileInput.addEventListener("change", () => {
  updateFileLabel();
});

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
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    updateFileLabel();
  }
});

function updateFileLabel() {
  if (fileInput.files && fileInput.files[0]) {
    const name = fileInput.files[0].name;
    fileLabel.textContent = `✔ ${name}`;
    dropZone.classList.add("has-file");
  } else {
    fileLabel.innerHTML = 'Drag &amp; drop or <u>browse</u>';
    dropZone.classList.remove("has-file");
  }
}

// ── Form submission ───────────────────────────────────────────────────────
form.addEventListener("submit", async (e) => {
  e.preventDefault();

  if (!fileInput.files || !fileInput.files[0]) {
    alert("Please select a manuscript file.");
    return;
  }

  const rateVal = parseInt(rateSlider.value, 10);
  const rateStr = rateVal >= 0 ? `+${rateVal}%` : `${rateVal}%`;

  const formData = new FormData();
  formData.append("file",   fileInput.files[0]);
  formData.append("engine", engineSelect.value);
  formData.append("voice",  document.getElementById("voice-select").value);
  formData.append("rate",   rateStr);

  submitBtn.disabled = true;
  submitBtn.textContent = "Uploading…";

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || `Server error ${res.status}`);
    }

    currentJobId = data.job_id;
    startPolling(currentJobId);
    showProgressSection(currentJobId);

  } catch (err) {
    submitBtn.disabled = false;
    submitBtn.textContent = "🎙️ Convert to Audiobook";
    alert(`Upload failed: ${err.message}`);
  }
});

// ── Show / hide sections ──────────────────────────────────────────────────
function showProgressSection(jobId) {
  uploadSection.classList.add("hidden");
  progressSection.classList.remove("hidden");
  resultsSection.classList.add("hidden");

  jobIdDisplay.textContent = jobId;
  setStatusBadge("pending");
  progressBar.style.width = "0%";
  progressLabel.textContent = "Waiting to start…";
  chapterList.innerHTML = "";
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
}

function showResultsSection(jobId, jobData) {
  progressSection.classList.add("hidden");
  resultsSection.classList.remove("hidden");

  bookTitleDisplay.textContent = jobData.book_title || "Audiobook";
  downloadList.innerHTML = "";

  (jobData.chapters || []).forEach((ch) => {
    if (!ch.file) return;
    const li = document.createElement("li");
    li.className = "download-item";
    li.innerHTML = `
      <span class="chapter-num">#${ch.number}</span>
      <span style="flex:1">${escapeHtml(ch.title)}</span>
      <a href="/api/download/${jobId}/${encodeURIComponent(ch.file)}"
         download="${escapeHtml(ch.file)}">
        ⬇ Download
      </a>`;
    downloadList.appendChild(li);
  });
}

// ── Polling ───────────────────────────────────────────────────────────────
function startPolling(jobId) {
  if (pollIntervalId) clearInterval(pollIntervalId);
  pollIntervalId = setInterval(() => pollStatus(jobId), 2000);
  // Immediate first poll
  pollStatus(jobId);
}

function stopPolling() {
  if (pollIntervalId) {
    clearInterval(pollIntervalId);
    pollIntervalId = null;
  }
}

async function pollStatus(jobId) {
  try {
    const res  = await fetch(`/api/status/${jobId}`);
    const data = await res.json();

    if (!res.ok) {
      stopPolling();
      showError(data.error || "Unknown error fetching status.");
      return;
    }

    renderProgress(data);

    if (data.status === "complete") {
      stopPolling();
      showResultsSection(jobId, data);
    } else if (data.status === "error") {
      stopPolling();
      showError(data.error || "Conversion failed.");
    }

  } catch (err) {
    // Network hiccup — keep polling, don't crash
    console.warn("Poll error:", err);
  }
}

// ── Render progress ───────────────────────────────────────────────────────
function renderProgress(data) {
  setStatusBadge(data.status);

  const { completed = 0, total = 0 } = data.progress || {};
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  progressBar.style.width = `${pct}%`;

  if (data.status === "pending") {
    progressLabel.textContent = "Waiting to start…";
  } else if (data.status === "processing") {
    progressLabel.textContent =
      total > 0
        ? `Converting chapter ${completed + 1} of ${total}…`
        : "Processing…";
  } else if (data.status === "complete") {
    progressLabel.textContent = `Done — ${total} chapter${total !== 1 ? "s" : ""} converted.`;
    progressBar.style.width = "100%";
  }

  // Rebuild chapter list only when chapter count changes (avoids flicker)
  const chapters = data.chapters || [];
  if (chapterList.children.length !== chapters.length) {
    chapterList.innerHTML = "";
    chapters.forEach((ch) => {
      const li = document.createElement("li");
      li.className = "chapter-item";
      li.id = `ch-item-${ch.number}`;
      li.innerHTML = `
        <span class="chapter-icon">📄</span>
        <span class="chapter-title">${escapeHtml(ch.title)}</span>
        <span class="chapter-status">pending</span>`;
      chapterList.appendChild(li);
    });
  }

  // Update individual chapter states
  chapters.forEach((ch, idx) => {
    const li = document.getElementById(`ch-item-${ch.number}`);
    if (!li) return;

    const isActive = !ch.done && idx === (data.progress?.completed ?? 0);
    li.classList.toggle("done",   ch.done);
    li.classList.toggle("active", isActive && data.status === "processing");

    const iconEl   = li.querySelector(".chapter-icon");
    const statusEl = li.querySelector(".chapter-status");

    if (ch.done) {
      iconEl.textContent   = "✅";
      statusEl.textContent = "done";
    } else if (isActive && data.status === "processing") {
      iconEl.textContent   = "🔄";
      statusEl.textContent = "converting…";
    } else {
      iconEl.textContent   = "📄";
      statusEl.textContent = "pending";
    }
  });
}

function setStatusBadge(status) {
  statusBadge.textContent = status;
  statusBadge.className   = `status-badge ${status}`;
}

function showError(message) {
  setStatusBadge("error");
  errorBox.textContent = `Error: ${message}`;
  errorBox.classList.remove("hidden");
}

// ── Manifest viewer ───────────────────────────────────────────────────────
manifestBtn.addEventListener("click", async () => {
  if (!currentJobId) return;

  const isVisible = !manifestViewer.classList.contains("hidden");
  if (isVisible) {
    manifestViewer.classList.add("hidden");
    manifestBtn.textContent = "📋 View Manifest";
    return;
  }

  try {
    const res  = await fetch(`/api/manifest/${currentJobId}`);
    const data = await res.json();
    manifestContent.textContent = JSON.stringify(data, null, 2);
    manifestViewer.classList.remove("hidden");
    manifestBtn.textContent = "📋 Hide Manifest";
  } catch (err) {
    alert(`Could not load manifest: ${err.message}`);
  }
});

// ── Convert another ───────────────────────────────────────────────────────
convertAnotherBtn.addEventListener("click", () => {
  currentJobId = null;
  stopPolling();

  // Reset form
  form.reset();
  updateFileLabel();
  rateDisplay.textContent = "-5%";
  voiceGroup.classList.remove("hidden");
  submitBtn.disabled = false;
  submitBtn.textContent = "🎙️ Convert to Audiobook";

  // Reset manifest viewer
  manifestViewer.classList.add("hidden");
  manifestBtn.textContent = "📋 View Manifest";

  // Show upload section
  resultsSection.classList.add("hidden");
  progressSection.classList.add("hidden");
  uploadSection.classList.remove("hidden");
});

// ── Helpers ───────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
