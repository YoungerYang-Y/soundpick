import { createTaskView, terminalStates, sizeLabel } from './task-view.js';
import { createFileSharing } from './file-share.js';

const taskStorageKey = 'soundpick.current-task.v1';
if (document.querySelector('#extraction-app')) initialize();

function initialize() {
  const element = id => document.getElementById(id);
  const ui = Object.fromEntries([
    'source-fields', 'share-text', 'video-file', 'paste-button', 'start-button',
    'limits-note', 'platforms-note', 'availability', 'availability-message', 'retry-capabilities',
    'form-feedback', 'task-panel', 'task-heading', 'task-stage', 'task-message',
    'upload-progress', 'upload-bar', 'upload-label', 'result-content', 'result-meta', 'audio-player',
    'download-button', 'video-download-button', 'video-feedback', 'prepare-share', 'share-button',
    'share-feedback', 'expiry-note', 'cancel-button', 'reconnect-button', 'new-task-button', 'abandon-recovery', 'recovery-note', 'file-details', 'process-steps',
  ].map(id => [id, element(id)]));
  const csrf = document.querySelector('input[name="csrf_token"]').value;
  // All task/transport lifetimes share this state; old generations cannot repaint a new view.
  const state = { mode: 'link', capabilities: null, busy: false, task: null, generation: 0,
    creation: null, xhr: null, pollController: null, pollTimer: null, expiryTimer: null,
    cancelRequested: false, cancellation: null, cancellationVersion: null, suspended: false, restoring: false };
  let view;
  const sharing = createFileSharing(ui, state, { expired, markExpired, responseError, errorMessage,
    fileUrl: url => view.safeFileUrl(url) });
  view = createTaskView(ui, state, sharing);
  const post = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    ...(body ? { body: JSON.stringify(body) } : {}) });

  function errorMessage(error) {
    if (error.status === 401) return '登录已失效，请刷新页面重新登录。';
    if (error.status === 403) return '页面验证已过期，请刷新页面后重试。';
    if (error.status === 404) return '任务不存在或已过期，请重新提取。';
    return error.status ? error.message : '网络连接中断，请检查网络后重试。';
  }
  async function responseError(response) {
    let detail = '请求未能完成，请重试。';
    try { const data = await response.json(); if (typeof data.detail === 'string') detail = data.detail; }
    catch { /* Keep a safe message for non-JSON errors. */ }
    return Object.assign(new Error(detail), { status: response.status });
  }
  async function api(path, options = {}) {
    const controller = options.signal ? null : new AbortController();
    const timeout = controller ? setTimeout(() => controller.abort(), 15000) : null;
    try {
      const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options,
        signal: options.signal || controller.signal });
      if (!response.ok) throw await responseError(response);
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  function stopPolling() {
    clearTimeout(state.pollTimer); state.pollTimer = null;
    state.pollController?.abort(); state.pollController = null;
  }
  function invalidate() { state.generation += 1; stopPolling(); }
  function resetTask() {
    invalidate(); state.xhr?.abort(); state.xhr = null;
    clearTimeout(state.expiryTimer); view.clearResult();
    state.task = null; state.creation = null; state.cancelRequested = false; state.busy = false; state.restoring = false;
    state.cancellation = null; state.cancellationVersion = null;
    clearStoredTask();
    ui['task-panel'].hidden = true; ui['reconnect-button'].hidden = true;
    ui['abandon-recovery'].hidden = true; ui['recovery-note'].hidden = true;
    view.syncBusy(); view.feedback();
  }
  function clearStoredTask() {
    try { sessionStorage.removeItem(taskStorageKey); } catch { /* Storage can be disabled by the browser. */ }
  }
  function saveStoredTask() {
    if (!state.task) return clearStoredTask();
    try {
      sessionStorage.setItem(taskStorageKey, JSON.stringify({
        id: state.task.id,
        confirmed: Boolean(state.creation?.confirmed),
        cancelRequested: state.cancelRequested,
      }));
    } catch { /* Current-page task handling remains available without storage. */ }
  }
  function readStoredTask() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(taskStorageKey) || 'null');
      if (!saved || typeof saved.id !== 'string' || !/^[a-f0-9]{32}$/.test(saved.id) ||
          typeof saved.confirmed !== 'boolean' || typeof saved.cancelRequested !== 'boolean') {
        clearStoredTask();
        return null;
      }
      return saved;
    } catch {
      clearStoredTask();
      return null;
    }
  }
  function failRestorationAuthentication(error) {
    if (!state.restoring || !state.task || (error.status !== 401 && error.status !== 403)) return false;
    state.restoring = false;
    clearStoredTask();
    state.creation = null;
    state.cancelRequested = false;
    renderTask({ id: state.task.id, state: 'failed', message: errorMessage(error) });
    return true;
  }
  function expired() { return Boolean(state.task?.expires_at && Date.now() >= state.task.expires_at * 1000); }
  function markExpired() {
    invalidate(); clearTimeout(state.expiryTimer); view.clearResult();
    state.task = null; state.creation = null; state.busy = false; state.restoring = false;
    clearStoredTask();
    ui['process-steps'].hidden = true; ui['task-heading'].textContent = '结果已过期';
    ui['task-stage'].textContent = '请重新提取'; ui['task-message'].textContent = '任务不存在或已过期，请重新提取。';
    ui['cancel-button'].hidden = true; ui['reconnect-button'].hidden = true; ui['new-task-button'].hidden = false;
    ui['abandon-recovery'].hidden = true; ui['recovery-note'].hidden = true;
    view.syncBusy();
  }
  function armExpiry() {
    clearTimeout(state.expiryTimer);
    if (!state.task?.expires_at) return;
    if (expired()) return markExpired();
    state.expiryTimer = setTimeout(markExpired, Math.min(state.task.expires_at * 1000 - Date.now(), 2147483647));
  }
  function renderTask(task) {
    if (!state.task || task.id !== state.task.id) throw new Error('任务响应不匹配');
    state.task = task; state.busy = !terminalStates.has(task.state);
    view.render();
    if (!state.busy) stopPolling();
    armExpiry();
  }
  async function loadCapabilities() {
    ui['retry-capabilities'].disabled = true; ui['availability-message'].textContent = '正在检查服务…';
    try {
      state.capabilities = await api('/api/capabilities');
      const limits = state.capabilities.limits;
      ui['limits-note'].textContent = `视频上限 ${sizeLabel(limits.max_media_bytes)} · 最长 ${Math.floor(limits.max_duration_seconds / 60)} 分钟 · 结果保留 ${Math.round(limits.result_ttl_seconds / 60)} 分钟`;
      const platforms = state.capabilities.platforms.filter(item => item.available).map(item => item.name);
      ui['platforms-note'].textContent = platforms.length ? `支持 ${platforms.join('、')} 分享链接，能否提取以实际结果为准。` : '分享链接暂不可用，请选择本地视频。';
      ui['availability-message'].textContent = state.capabilities.media_available
        ? (platforms.length ? '可以开始提取' : '仅本地视频可用') : '提取服务暂不可用，请联系维护者。';
      ui['availability'].classList.toggle('availability--ready', state.capabilities.media_available);
      ui['retry-capabilities'].hidden = state.capabilities.media_available;
    } catch (error) {
      state.capabilities = null; ui['availability-message'].textContent = errorMessage(error);
      ui['availability'].classList.remove('availability--ready'); ui['retry-capabilities'].hidden = false;
    } finally { ui['retry-capabilities'].disabled = false; view.syncBusy(); }
  }
  function schedulePoll() {
    clearTimeout(state.pollTimer);
    if (!state.suspended && state.task && !terminalStates.has(state.task.state) && !state.xhr) {
      state.pollTimer = setTimeout(pollTask, document.hidden ? 10000 : 1800);
    }
  }
  async function pollTask() {
    if (!state.task || terminalStates.has(state.task.state) || state.pollController || state.suspended || state.xhr) return;
    const version = state.generation, controller = new AbortController();
    const wasConfirmed = state.creation?.confirmed;
    state.pollController = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const task = await api(`/api/tasks/${state.task.id}`, { signal: controller.signal });
      if (version !== state.generation || state.pollController !== controller) return;
      state.restoring = false;
      state.creation.confirmed = true;
      // A status lookup supersedes the original creation response snapshot.
      state.creation.statusObserved = true;
      saveStoredTask();
      renderTask(task);
      if (state.cancelRequested && state.busy) { await cancelTask(); return; }
      if (task.state === 'receiving' && state.creation.file) { await sendUpload(state.creation, version); return; }
    } catch (error) {
      if (version !== state.generation || state.pollController !== controller) return;
      if (failRestorationAuthentication(error)) return;
      if (error.status === 404 && wasConfirmed) return markExpired();
      view.disconnected(error.status === 404
        ? '尚未确认任务是否创建。请重新连接确认，或取消处理。'
        : error.status ? errorMessage(error) : '连接中断，任务可能仍在处理。重新连接后可继续查看进度。');
      return;
    } finally {
      clearTimeout(timeout);
      if (state.pollController === controller) state.pollController = null;
    }
    if (version === state.generation) schedulePoll();
  }
  function upload(file, id, version) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest(); state.xhr = request;
      request.open('POST', `/api/tasks/${id}/upload`);
      request.setRequestHeader('X-CSRF-Token', csrf);
      request.setRequestHeader('Content-Type', 'application/octet-stream');
      request.timeout = state.capabilities.limits.upload_timeout_seconds * 1000;
      request.upload.addEventListener('progress', event => {
        if (version === state.generation && event.lengthComputable) {
          const progress = Math.round(event.loaded / event.total * 100);
          ui['upload-bar'].value = progress;
          ui['upload-label'].textContent = progress === 100 ? '上传 100% · 等待服务确认' : `上传 ${progress}%`;
        }
      });
      request.addEventListener('load', () => {
        let data;
        try { data = JSON.parse(request.responseText); } catch { /* Preserve HTTP status for non-JSON failures. */ }
        if (request.status >= 200 && request.status < 300 && data) resolve(data);
        else reject(Object.assign(new Error(typeof data?.detail === 'string' ? data.detail : '上传响应未获确认，请重新连接查看原任务。'),
          request.status >= 200 && request.status < 300 ? {} : { status: request.status }));
      });
      request.addEventListener('error', () => reject(new Error('上传连接中断')));
      request.addEventListener('timeout', () => reject(Object.assign(new Error('上传超时，请重新连接确认原任务。'), { status: 408 })));
      request.addEventListener('abort', () => reject(Object.assign(new Error('上传已中断，等待服务器确认。'), { name: 'AbortError' })));
      request.send(file);
    });
  }
  async function sendUpload(operation, version) {
    const file = operation.file; operation.file = null; // Never automatically repeat an uncertain upload.
    if (!file || state.cancelRequested || version !== state.generation) return;
    view.receiving(file);
    try {
      const task = await upload(file, state.task.id, version);
      if (version !== state.generation) return;
      state.xhr = null;
      renderTask(task); schedulePoll();
    } catch (error) {
      if (version !== state.generation) return;
      if (error.status === 404) return markExpired();
      view.disconnected(`${errorMessage(error)} 上传未获确认，请重新连接查看原任务，或取消处理。`);
    } finally { if (state.creation === operation) state.xhr = null; }
  }
  async function createTask(operation, version) {
    try {
      const task = await api(operation.path, post(operation.body));
      if (state.creation !== operation) return;
      operation.confirmed = true;
      saveStoredTask();
      if (state.suspended) return;
      if (state.cancelRequested) {
        // A cancel may have looked up the id before creation reached the server.
        if (state.cancellation) await state.cancellation;
        if (state.creation === operation && !state.suspended) await cancelTask();
        return;
      }
      if (version !== state.generation || operation.statusObserved) return;
      renderTask(task);
      if (operation.file && task.state === 'receiving') await sendUpload(operation, version);
      else schedulePoll();
    } catch (error) {
      if (version !== state.generation || state.suspended || state.creation !== operation || operation.statusObserved) return;
      if (error.status >= 400 && error.status < 500 && error.status !== 408) {
        clearStoredTask();
        renderTask({ id: state.task.id, state: 'failed', message: errorMessage(error) });
      } else view.disconnected(`${errorMessage(error)} 创建结果未获确认，请重新连接查看原任务，或取消处理。`);
    }
  }
  async function cancelTask() {
    if (!state.task || terminalStates.has(state.task.state)) return;
    if (state.cancellation) {
      if (state.cancellationVersion === state.generation) return state.cancellation;
      const operation = state.creation;
      await state.cancellation;
      if (state.creation !== operation) return;
      return cancelTask();
    }
    state.cancelRequested = true;
    saveStoredTask();
    invalidate(); state.xhr?.abort(); view.clearResult();
    const version = state.generation, id = state.task.id;
    const wasConfirmed = state.creation?.confirmed;
    ui['cancel-button'].disabled = true;
    ui['task-message'].textContent = '正在向服务器确认取消，请稍候…';
    const pending = (async () => {
      try {
        const task = await api(`/api/tasks/${id}/cancel`, post());
        if (version === state.generation) renderTask(task);
      } catch (error) {
        if (version !== state.generation) return;
        if (failRestorationAuthentication(error)) return;
        if (error.status === 404 && wasConfirmed) return markExpired();
        view.disconnected(`取消未获确认。${errorMessage(error)} 可重新连接查看原任务。`);
      } finally { if (state.cancellation === pending) state.cancellation = null; }
    })();
    state.cancellation = pending; state.cancellationVersion = version;
    await pending;
  }
  element('extraction-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (state.busy || !state.capabilities?.media_available) return;
    const file = ui['video-file'].files[0], text = ui['share-text'].value.trim();
    if (state.mode === 'link' && !text) { view.feedback('先粘贴分享链接或分享文本。'); ui['share-text'].focus(); return; }
    if (state.mode === 'file' && (!file || !file.size)) { view.feedback('先选择一个非空的视频文件。'); ui['video-file'].focus(); return; }
    if (state.mode === 'file' && file.size > state.capabilities.limits.max_media_bytes) {
      view.feedback(`视频超过 ${sizeLabel(state.capabilities.limits.max_media_bytes)} 的大小限制，请选择更小的文件。`); return;
    }
    resetTask();
    let id;
    try { id = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join(''); }
    catch { view.feedback('无法安全创建任务，请更新浏览器或刷新页面后重试。'); return; }
    state.task = { id, state: state.mode === 'file' ? 'receiving' : 'resolving' };
    state.busy = true;
    const operation = { path: state.mode === 'file' ? '/api/tasks/reservations' : '/api/tasks/link',
      body: state.mode === 'file' ? { task_id: id, filename: file.name.slice(0, 200) } : { task_id: id, share_text: text },
      file: state.mode === 'file' ? file : null, confirmed: false, statusObserved: false };
    state.creation = operation; saveStoredTask(); view.receiving(operation.file);
    await createTask(operation, state.generation);
  });
  document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => {
    if (state.busy) return;
    state.mode = button.dataset.mode;
    document.querySelectorAll('[data-mode]').forEach(item => {
      item.setAttribute('aria-pressed', String(item === button));
    });
    document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== state.mode; });
    view.feedback();
  }));
  ui['paste-button'].addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText(); if (!text.trim()) throw new Error('empty');
      ui['share-text'].value = text.slice(0, 4096);
      view.feedback(text.length > 4096 ? '分享文本过长，只粘贴了前 4096 个字符，请检查链接是否完整。' : '');
    } catch { view.feedback('无法读取剪贴板，请在输入框内长按并选择“粘贴”。'); }
    ui['share-text'].focus();
  });
  ui['video-file'].addEventListener('change', () => {
    const file = ui['video-file'].files[0]; ui['file-details'].hidden = !file;
    ui['file-details'].textContent = file ? `${file.name} · ${sizeLabel(file.size)}` : ''; view.feedback();
  });
  ui['retry-capabilities'].addEventListener('click', loadCapabilities);
  ui['new-task-button'].addEventListener('click', () => {
    if (state.busy) return;
    resetTask(); (state.mode === 'link' ? ui['share-text'] : ui['video-file']).focus();
  });
  ui['reconnect-button'].addEventListener('click', () => {
    if (expired()) return markExpired();
    ui['reconnect-button'].hidden = true; ui['task-message'].textContent = '正在重新连接，查看原任务进度…';
    void pollTask();
  });
  ui['cancel-button'].addEventListener('click', cancelTask);
  ui['abandon-recovery'].addEventListener('click', () => {
    resetTask();
    view.feedback('已放弃本页面恢复。后台取消尚未确认，原任务可能仍在处理。');
    (state.mode === 'link' ? ui['share-text'] : ui['video-file']).focus();
  });
  for (const id of ['download-button', 'video-download-button']) ui[id].addEventListener('click', event => {
    if (expired()) { event.preventDefault(); markExpired(); }
  });
  ui['audio-player'].addEventListener('play', () => { if (expired()) markExpired(); });
  ui['audio-player'].addEventListener('error', () => {
    if (expired()) return markExpired();
    if (state.task?.state === 'ready') ui['task-message'].textContent = '试听加载失败。请检查网络；下载也不可用时请重新提取。';
  });
  document.addEventListener('visibilitychange', () => {
    if (expired()) return markExpired();
    if (!document.hidden) { clearTimeout(state.pollTimer); void pollTask(); }
    else schedulePoll();
  });
  window.addEventListener('online', () => { void pollTask(); });
  window.addEventListener('pagehide', () => {
    state.suspended = true; invalidate(); state.xhr?.abort(); sharing.reset(); clearTimeout(state.expiryTimer);
  });
  window.addEventListener('pageshow', event => {
    state.suspended = false;
    if (!event.persisted || !state.task) return;
    if (expired()) return markExpired();
    armExpiry();
    if (state.task.state === 'ready') sharing.offer();
    else if (state.cancelRequested) void cancelTask();
    else void pollTask();
  });
  const restored = readStoredTask();
  if (restored) {
    state.task = { id: restored.id, state: 'resolving' };
    state.creation = { confirmed: restored.confirmed, file: null, statusObserved: false };
    state.cancelRequested = restored.cancelRequested;
    state.busy = true; state.restoring = true;
    view.receiving(null);
    ui['task-message'].textContent = '正在恢复上次任务状态…';
  }
  void loadCapabilities();
  if (restored) void pollTask();
}
