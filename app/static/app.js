"use strict";

(() => {
  if (!document.querySelector("#extraction-app")) return;
  const element = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "source-fields", "share-text", "video-file", "paste-button", "start-button",
    "limits-note", "platforms-note", "availability", "availability-message",
    "retry-capabilities", "form-feedback", "task-panel", "task-heading", "task-stage",
    "task-message", "upload-progress", "upload-bar", "upload-label", "result-content",
    "result-meta", "audio-player", "download-button", "video-download-button", "video-feedback",
    "prepare-share", "share-button", "share-feedback", "expiry-note", "cancel-button",
    "reconnect-button", "new-task-button",
    "file-details", "process-steps",
  ].map((id) => [id, element(id)]));
  const csrf = document.querySelector('input[name="csrf_token"]').value;
  const terminalStates = new Set(["ready", "failed", "cancelled"]);
  const stateLabels = {
    receiving: "正在接收视频", resolving: "正在解析分享内容", downloading: "正在取得原视频",
    converting: "正在提取完整音轨", ready: "音轨准备好了", failed: "这次未能完成", cancelled: "已取消处理",
  };
  let mode = "link";
  let capabilities = null;
  let busy = false;
  let currentTask = null;
  let uploadRequest = null;
  let pollTimer = null;
  let expiryTimer = null;
  let pollController = null;
  let shareController = null;
  let shareFile = null;
  let generation = 0;

  function feedback(message = "") {
    ui["form-feedback"].textContent = message;
    ui["form-feedback"].hidden = !message;
  }

  function setBusy(value) {
    busy = value;
    ui["source-fields"].disabled = value || !capabilities?.media_available;
    ui["start-button"].textContent = value ? "处理中…" : "开始提取";
  }

  function supportsFileSharing() {
    return window.isSecureContext === true && typeof navigator.share === "function" &&
      typeof navigator.canShare === "function" && typeof File === "function";
  }

  function clearShare() {
    shareController?.abort();
    shareController = null;
    shareFile = null;
    ui["prepare-share"].disabled = false;
    ui["prepare-share"].textContent = "准备分享文件";
    ui["prepare-share"].hidden = true;
    ui["share-button"].hidden = true;
    ui["share-button"].disabled = false;
    ui["share-feedback"].textContent = "";
  }

  function clearResult() {
    clearShare();
    ui["audio-player"].pause();
    ui["audio-player"].removeAttribute("src");
    ui["audio-player"].load();
    ui["download-button"].removeAttribute("href");
    ui["video-download-button"].removeAttribute("href");
    ui["video-download-button"].hidden = true;
    ui["video-feedback"].textContent = "";
    ui["result-content"].hidden = true;
  }

  function stopPolling() {
    clearTimeout(pollTimer);
    pollTimer = null;
    pollController?.abort();
    pollController = null;
  }

  function resetTask() {
    generation += 1;
    stopPolling();
    clearTimeout(expiryTimer);
    clearResult();
    currentTask = null;
    ui["task-panel"].hidden = true;
    ui["reconnect-button"].hidden = true;
    setBusy(false);
    feedback();
  }

  function errorMessage(error) {
    if (error.status === 401) return "登录已失效，请刷新页面重新登录。";
    if (error.status === 403) return "页面验证已过期，请刷新页面后重试。";
    if (error.status === 404) return "任务不存在或已过期，请重新提取。";
    if (error.status === 409) return error.message || "服务正在处理另一个视频，请稍后重试。";
    if (error.status === 413) return error.message || "视频超过大小限制，请选择更小的文件。";
    if (error.status === 503) return error.message || "提取服务暂不可用，请稍后重试。";
    return error.status ? error.message : "网络连接中断，请检查网络后重试。";
  }

  async function responseError(response) {
    let detail = "请求未能完成，请重试。";
    try {
      const data = await response.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch { /* HTML or empty errors still have a safe fallback. */ }
    return Object.assign(new Error(detail), { status: response.status });
  }

  async function api(path, options = {}) {
    const controller = options.signal ? null : new AbortController();
    const timeout = controller ? setTimeout(() => controller.abort(), 15000) : null;
    try {
      const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options,
        signal: options.signal || controller.signal });
      if (!response.ok) throw await responseError(response);
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  function sizeLabel(bytes) {
    return `${(bytes / 1024 / 1024).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} MiB`;
  }

  async function loadCapabilities() {
    ui["retry-capabilities"].disabled = true;
    ui["availability-message"].textContent = "正在检查服务…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      capabilities = await api("/api/capabilities", { signal: controller.signal });
      const limits = capabilities.limits;
      ui["limits-note"].textContent = `视频上限 ${sizeLabel(limits.max_media_bytes)} · 最长 ${Math.floor(limits.max_duration_seconds / 60)} 分钟 · 结果保留 ${Math.round(limits.result_ttl_seconds / 60)} 分钟`;
      const platforms = capabilities.platforms.filter((item) => item.available).map((item) => item.name);
      ui["platforms-note"].textContent = platforms.length ? `已启用 ${platforms.join("、")} 链接解析；具体视频以提取结果为准。` : "链接解析暂不可用，可选择本地视频。";
      ui["availability-message"].textContent = capabilities.media_available ? "服务可用，一次处理一个视频" : "媒体工具尚未就绪，请联系服务维护者后重新检查。";
      ui["availability"].classList.toggle("availability--ready", capabilities.media_available);
      ui["retry-capabilities"].hidden = capabilities.media_available;
      setBusy(busy);
    } catch (error) {
      capabilities = null;
      ui["availability-message"].textContent = errorMessage(error);
      ui["availability"].classList.remove("availability--ready");
      ui["retry-capabilities"].hidden = false;
      setBusy(busy);
    } finally {
      clearTimeout(timeout);
      ui["retry-capabilities"].disabled = false;
    }
  }

  function expired() {
    return currentTask?.expires_at && Date.now() >= currentTask.expires_at * 1000;
  }

  function markExpired() {
    generation += 1;
    clearTimeout(expiryTimer);
    stopPolling();
    clearResult();
    currentTask = null;
    ui["process-steps"].hidden = true;
    ui["task-heading"].textContent = "结果已过期";
    ui["task-stage"].textContent = "请重新提取";
    ui["task-message"].textContent = "任务不存在或已过期，请重新提取。";
    ui["cancel-button"].hidden = true;
    ui["reconnect-button"].hidden = true;
    ui["new-task-button"].hidden = false;
    setBusy(false);
  }

  function armExpiry() {
    clearTimeout(expiryTimer);
    if (!currentTask?.expires_at) return;
    if (expired()) return markExpired();
    expiryTimer = setTimeout(markExpired, Math.min(currentTask.expires_at * 1000 - Date.now(), 2147483647));
  }

  function safeFileUrl(url) {
    if (typeof url !== "string") return null;
    const parsed = new URL(url, window.location.origin);
    return parsed.origin === window.location.origin && parsed.pathname === `/files/${currentTask.id}` ? parsed.href : null;
  }

  function safeVideoUrl(url) {
    try {
      const safeUrl = safeFileUrl(url);
      if (!safeUrl) return null;
      const parsed = new URL(safeUrl);
      return parsed.searchParams.get("kind") === "video" && parsed.searchParams.get("download") === "1"
        ? safeUrl : null;
    } catch { return null; }
  }

  function renderTask(task) {
    const previousState = currentTask?.state;
    currentTask = task;
    if (task.state !== "ready") clearResult();
    const terminal = terminalStates.has(task.state);
    ui["task-panel"].hidden = false;
    ui["task-heading"].textContent = stateLabels[task.state] || "正在处理";
    ui["task-stage"].textContent = terminal ? (task.state === "ready" ? "可以试听和保存" : "处理已结束") : "正在处理";
    ui["task-message"].textContent = task.message || stateLabels[task.state] || "请稍候";
    ui["upload-progress"].hidden = true;
    ui["cancel-button"].hidden = terminal;
    ui["cancel-button"].disabled = false;
    ui["reconnect-button"].hidden = true;
    ui["new-task-button"].hidden = !terminal;
    ui["new-task-button"].textContent = task.state === "ready" ? "提取另一个视频" : "重新提取";
    setBusy(!terminal);
    ui["process-steps"].hidden = task.state === "failed" || task.state === "cancelled";
    const step = task.state === "ready" ? 2 : task.state === "converting" ? 1 : 0;
    document.querySelectorAll("[data-step]").forEach((item, index) => {
      item.classList.toggle("is-current", index === step);
      item.classList.toggle("is-complete", index < step);
      if (index === step) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
    if (task.state === "ready" && previousState !== "ready") {
      const preview = safeFileUrl(task.preview_url);
      const download = safeFileUrl(task.download_url);
      if (!preview || !download) {
        ui["task-message"].textContent = "结果链接不可用，请重新提取。";
      } else {
        ui["task-heading"].textContent = task.title || "完整原音轨";
        ui["result-meta"].textContent = [task.author ? `作者：${task.author}` : null,
          Number.isFinite(task.duration_seconds) ? `时长 ${Math.floor(task.duration_seconds / 60)}:${String(Math.floor(task.duration_seconds % 60)).padStart(2, "0")}` : null].filter(Boolean).join(" · ");
        ui["audio-player"].src = preview;
        ui["download-button"].href = download;
        const videoDownload = safeVideoUrl(task.video_download_url);
        if (videoDownload) {
          ui["video-download-button"].href = videoDownload;
          ui["video-download-button"].hidden = false;
          ui["video-feedback"].textContent = "";
        } else {
          ui["video-download-button"].removeAttribute("href");
          ui["video-download-button"].hidden = true;
          ui["video-feedback"].textContent = typeof task.video_message === "string" && task.video_message.trim()
            ? task.video_message
            : "原视频暂无法保存为 MP4，MP3 仍可下载";
        }
        ui["result-content"].hidden = false;
        if (supportsFileSharing()) {
          ui["prepare-share"].hidden = false;
        } else {
          ui["share-feedback"].textContent = "此浏览器不支持文件分享，请使用下载 MP3。";
        }
        ui["expiry-note"].textContent = task.expires_at ? `可下载至 ${new Date(task.expires_at * 1000).toLocaleString("zh-CN")}，到期需重新提取。` : "请及时下载，临时结果会自动清理。";
      }
    }
    if (terminal) stopPolling();
    armExpiry();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (currentTask && !terminalStates.has(currentTask.state)) {
      pollTimer = setTimeout(pollTask, document.hidden ? 10000 : 1800);
    }
  }

  async function pollTask() {
    if (!currentTask || terminalStates.has(currentTask.state) || pollController) return;
    const version = generation;
    const controller = new AbortController();
    pollController = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const task = await api(`/api/tasks/${currentTask.id}`, { signal: controller.signal });
      if (version !== generation) return;
      renderTask(task);
    } catch (error) {
      if (version !== generation) return;
      if (error.status === 404) {
        markExpired();
      } else {
        ui["task-message"].textContent = error.status ? errorMessage(error) : "连接中断，任务可能仍在处理。重新连接后可继续查看进度。";
        ui["reconnect-button"].hidden = false;
        // Keep the task ID and busy state; never create a replacement on a polling error.
      }
      return;
    } finally {
      clearTimeout(timeout);
      if (pollController === controller) pollController = null;
    }
    if (version === generation) schedulePoll();
  }

  function showReceiving(file) {
    ui["task-panel"].hidden = false;
    ui["process-steps"].hidden = false;
    document.querySelectorAll("[data-step]").forEach((item, index) => {
      item.classList.toggle("is-current", index === 0);
      item.classList.remove("is-complete");
      if (index === 0) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
    ui["task-stage"].textContent = file ? "正在上传" : "正在提交";
    ui["task-heading"].textContent = file ? "正在接收视频" : "正在提交分享内容";
    ui["task-message"].textContent = file ? "保持页面打开。上传完成后会继续提取声音。" : "正在创建任务，请稍候。";
    ui["upload-progress"].hidden = !file;
    ui["upload-bar"].value = 0;
    ui["upload-label"].textContent = "上传 0%";
    ui["cancel-button"].hidden = !file;
    ui["cancel-button"].disabled = false;
    ui["new-task-button"].hidden = true;
    ui["task-heading"].focus();
  }

  function upload(file) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      uploadRequest = request;
      request.open("POST", `/api/tasks/upload?filename=${encodeURIComponent(file.name.slice(0, 200))}`);
      request.setRequestHeader("X-CSRF-Token", csrf);
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.timeout = capabilities.limits.upload_timeout_seconds * 1000;
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          const progress = Math.round(event.loaded / event.total * 100);
          ui["upload-bar"].value = progress;
          ui["upload-label"].textContent = progress === 100 ? "上传 100% · 等待服务确认" : `上传 ${progress}%`;
        }
      });
      request.addEventListener("load", () => {
        try {
          const data = JSON.parse(request.responseText);
          if (request.status >= 200 && request.status < 300) resolve(data);
          else reject(Object.assign(new Error(typeof data.detail === "string" ? data.detail : "上传未能完成"), { status: request.status }));
        } catch { reject(new Error("上传响应无法读取，请重新连接服务后重试。")); }
      });
      request.addEventListener("error", () => reject(new Error("上传连接中断")));
      request.addEventListener("timeout", () => reject(Object.assign(new Error("上传超时，请检查网络或选择更小的视频后重试。"), { status: 408 })));
      request.addEventListener("abort", () => reject(Object.assign(new Error("已停止上传，可重新选择视频。"), { name: "AbortError" })));
      request.send(file);
    });
  }

  element("extraction-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !capabilities?.media_available) return;
    const file = ui["video-file"].files[0];
    const text = ui["share-text"].value.trim();
    if (mode === "link" && !text) {
      feedback("先粘贴分享链接或分享文本。");
      ui["share-text"].focus();
      return;
    }
    if (mode === "file" && (!file || !file.size)) {
      feedback("先选择一个非空的视频文件。");
      ui["video-file"].focus();
      return;
    }
    if (mode === "file" && file.size > capabilities.limits.max_media_bytes) {
      feedback(`视频超过 ${sizeLabel(capabilities.limits.max_media_bytes)} 的大小限制，请选择更小的文件。`);
      return;
    }
    resetTask();
    const version = generation;
    setBusy(true);
    showReceiving(mode === "file" ? file : null);
    try {
      const task = mode === "file" ? await upload(file) : await api("/api/tasks/link", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ share_text: text }),
      });
      if (version !== generation) return;
      renderTask(task);
      schedulePoll();
    } catch (error) {
      if (version !== generation) return;
      ui["task-heading"].textContent = error.name === "AbortError" ? "已停止上传" : "未能创建任务";
      ui["task-stage"].textContent = "可以重试";
      ui["task-message"].textContent = error.name === "AbortError" ? error.message : errorMessage(error);
      ui["cancel-button"].hidden = true;
      ui["upload-progress"].hidden = true;
      ui["process-steps"].hidden = true;
      ui["new-task-button"].hidden = false;
      ui["new-task-button"].textContent = "重新提取";
      setBusy(false);
    } finally {
      uploadRequest = null;
    }
  });

  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      mode = button.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
        item.setAttribute("aria-pressed", String(item === button));
      });
      document.querySelectorAll("[data-panel]").forEach((panel) => { panel.hidden = panel.dataset.panel !== mode; });
      feedback();
    });
  });

  ui["paste-button"].addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (!text.trim()) throw new Error("empty");
      ui["share-text"].value = text.slice(0, 4096);
      feedback(text.length > 4096 ? "分享文本过长，只粘贴了前 4096 个字符，请检查链接是否完整。" : "");
    } catch {
      feedback("无法读取剪贴板，请在输入框内长按并选择“粘贴”。");
    }
    ui["share-text"].focus();
  });

  ui["video-file"].addEventListener("change", () => {
    const file = ui["video-file"].files[0];
    ui["file-details"].hidden = !file;
    ui["file-details"].textContent = file ? `${file.name} · ${sizeLabel(file.size)}` : "";
    feedback();
  });
  ui["retry-capabilities"].addEventListener("click", loadCapabilities);
  ui["new-task-button"].addEventListener("click", () => {
    resetTask();
    (mode === "link" ? ui["share-text"] : ui["video-file"]).focus();
  });
  ui["reconnect-button"].addEventListener("click", () => {
    if (expired()) return markExpired();
    ui["reconnect-button"].hidden = true;
    ui["task-message"].textContent = "正在重新连接，查看原任务进度…";
    pollTask();
  });

  ui["cancel-button"].addEventListener("click", async () => {
    clearResult();
    if (uploadRequest) return uploadRequest.abort();
    if (!currentTask || terminalStates.has(currentTask.state)) return;
    ui["cancel-button"].disabled = true;
    // Invalidate any poll whose abort rejection may arrive after cancellation completes.
    generation += 1;
    const version = generation;
    stopPolling();
    try {
      const task = await api(`/api/tasks/${currentTask.id}/cancel`, { method: "POST", headers: { "X-CSRF-Token": csrf } });
      if (version === generation) renderTask(task);
    } catch (error) {
      if (version !== generation) return;
      if (error.status === 404) return markExpired();
      ui["task-message"].textContent = `取消未获确认。${errorMessage(error)} 可重新连接查看原任务。`;
      ui["reconnect-button"].hidden = false;
      ui["cancel-button"].disabled = false;
    }
  });

  ui["download-button"].addEventListener("click", (event) => {
    if (expired()) {
      event.preventDefault();
      markExpired();
    }
  });
  ui["video-download-button"].addEventListener("click", (event) => {
    if (expired()) {
      event.preventDefault();
      markExpired();
    }
  });
  ui["audio-player"].addEventListener("play", () => { if (expired()) markExpired(); });
  ui["audio-player"].addEventListener("error", () => {
    if (expired()) return markExpired();
    if (currentTask?.state === "ready") ui["task-message"].textContent = "试听加载失败。请检查网络；下载也不可用时请重新提取。";
  });

  ui["prepare-share"].addEventListener("click", async () => {
    if (!currentTask || expired()) return markExpired();
    const version = generation;
    const task = currentTask;
    const controller = new AbortController();
    shareController = controller;
    ui["prepare-share"].disabled = true;
    ui["prepare-share"].textContent = "正在准备文件…";
    ui["share-feedback"].textContent = "正在读取 MP3，请稍候；完成后再点击系统分享。";
    const timeout = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch(safeFileUrl(task.download_url), { credentials: "same-origin", cache: "no-store", signal: controller.signal });
      if (!response.ok) throw await responseError(response);
      // Bound the one cached file using the configured 192 kbps output and a small header allowance.
      const maxBytes = Math.ceil((task.duration_seconds || capabilities.limits.max_duration_seconds) * 24000) + 1024 * 1024;
      if (Number(response.headers.get("Content-Length")) > maxBytes) throw new Error("share_size");
      const reader = response.body?.getReader();
      if (!reader) throw new Error("share_stream");
      const chunks = [];
      let size = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) {
          await reader.cancel();
          throw new Error("share_size");
        }
        chunks.push(value);
      }
      if (version !== generation || expired()) return;
      const file = new File(chunks, task.filename || "音拾.mp3", { type: "audio/mpeg" });
      if (!navigator.canShare({ files: [file] })) {
        ui["share-feedback"].textContent = "此浏览器不能分享 MP3 文件，请使用下载 MP3。";
        ui["prepare-share"].hidden = true;
      } else {
        shareFile = file;
        ui["prepare-share"].hidden = true;
        ui["share-button"].hidden = false;
        ui["share-feedback"].textContent = "文件已准备好，点击“打开系统分享”选择保存或分享目标。";
      }
    } catch (error) {
      if (version !== generation || expired()) return;
      if (error.status === 404) return markExpired();
      ui["share-feedback"].textContent = error.status ? errorMessage(error) : "分享文件准备失败，可重试或直接下载 MP3。";
    } finally {
      clearTimeout(timeout);
      if (shareController === controller) shareController = null;
      if (version === generation) {
        ui["prepare-share"].disabled = false;
        ui["prepare-share"].textContent = "准备分享文件";
      }
    }
  });

  ui["share-button"].addEventListener("click", async () => {
    if (expired()) return markExpired();
    if (!shareFile) return;
    const version = generation;
    ui["share-button"].disabled = true;
    try {
      // Invoke directly in this user gesture; file preparation happened in a separate click.
      await navigator.share({ files: [shareFile] });
      if (version === generation) ui["share-feedback"].textContent = "系统分享已关闭，请到所选应用或“文件”中确认保存结果。";
    } catch (error) {
      if (version !== generation) return;
      ui["share-feedback"].textContent = error.name === "AbortError" ? "已取消分享，仍可下载 MP3。" : "系统分享未能打开，请使用下载 MP3。";
    } finally {
      if (version === generation) ui["share-button"].disabled = false;
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (expired()) return markExpired();
    if (!document.hidden && currentTask && !terminalStates.has(currentTask.state)) {
      clearTimeout(pollTimer);
      pollTask();
    } else schedulePoll();
  });
  window.addEventListener("online", () => {
    if (currentTask && !terminalStates.has(currentTask.state)) pollTask();
  });
  window.addEventListener("pagehide", () => {
    stopPolling();
    clearShare();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted || !currentTask) return;
    if (expired()) return markExpired();
    armExpiry();
    if (currentTask.state === "ready" && ui["download-button"].hasAttribute("href") &&
        supportsFileSharing()) {
      ui["prepare-share"].hidden = false;
    } else if (!terminalStates.has(currentTask.state)) pollTask();
  });
  loadCapabilities();
})();
