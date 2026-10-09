/** DOM presentation; task ownership and request lifetimes stay in the controller. */
export const terminalStates = new Set(['ready', 'failed', 'cancelled']);
const labels = {
  receiving: '正在接收视频', resolving: '正在解析分享内容', downloading: '正在取得原视频',
  converting: '正在提取完整音轨', ready: '音轨准备好了', failed: '这次未能完成', cancelled: '已取消处理',
};
export const sizeLabel = bytes => `${(bytes / 1024 / 1024).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} MiB`;

export function createTaskView(ui, state, sharing) {
  function feedback(message = '') {
    ui['form-feedback'].textContent = message;
    ui['form-feedback'].hidden = !message;
  }
  function syncBusy() {
    ui['source-fields'].disabled = state.busy || !state.capabilities?.media_available;
    ui['start-button'].textContent = state.busy ? '处理中…' : '开始提取';
  }
  function safeFileUrl(url) {
    if (typeof url !== 'string' || !state.task) return null;
    try {
      const parsed = new URL(url, window.location.origin);
      return parsed.origin === window.location.origin && parsed.pathname === `/files/${state.task.id}` ? parsed.href : null;
    } catch { return null; }
  }
  function safeVideoUrl(url) {
    const safe = safeFileUrl(url);
    if (!safe) return null;
    const parsed = new URL(safe);
    return parsed.searchParams.get('kind') === 'video' && parsed.searchParams.get('download') === '1' ? safe : null;
  }
  function clearResult() {
    sharing.reset();
    ui['audio-player'].pause();
    ui['audio-player'].removeAttribute('src');
    ui['audio-player'].load();
    ui['download-button'].removeAttribute('href');
    ui['video-download-button'].removeAttribute('href');
    ui['video-download-button'].hidden = true;
    ui['video-feedback'].textContent = '';
    ui['result-content'].hidden = true;
  }
  function render() {
    const task = state.task;
    if (task.state !== 'ready') clearResult();
    const terminal = terminalStates.has(task.state);
    ui['task-panel'].hidden = false;
    ui['task-heading'].textContent = labels[task.state] || '正在处理';
    ui['task-stage'].textContent = terminal ? (task.state === 'ready' ? '提取完成' : '处理已结束') : '正在处理';
    ui['task-message'].textContent = task.message || labels[task.state] || '请稍候';
    ui['upload-progress'].hidden = true;
    ui['cancel-button'].hidden = terminal;
    ui['cancel-button'].disabled = false;
    ui['reconnect-button'].hidden = true;
    ui['abandon-recovery'].hidden = !state.restoring || terminal;
    ui['recovery-note'].hidden = ui['abandon-recovery'].hidden;
    ui['new-task-button'].hidden = !terminal;
    ui['new-task-button'].textContent = task.state === 'ready' ? '提取另一个视频' : '重新提取';
    syncBusy();
    ui['process-steps'].hidden = terminal;
    const step = task.state === 'ready' ? 2 : task.state === 'converting' ? 1 : 0;
    document.querySelectorAll('[data-step]').forEach((item, index) => {
      item.classList.toggle('is-current', index === step);
      item.classList.toggle('is-complete', index < step);
      if (index === step) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
    if (task.state !== 'ready') return;
    const preview = safeFileUrl(task.preview_url), download = safeFileUrl(task.download_url);
    if (!preview || !download) {
      clearResult();
      ui['task-message'].textContent = '结果链接不可用，请重新提取。';
      return;
    }
    ui['task-heading'].textContent = task.title || '完整原音轨';
    ui['result-meta'].textContent = [task.author ? `作者：${task.author}` : null,
      Number.isFinite(task.duration_seconds) ? `时长 ${Math.floor(task.duration_seconds / 60)}:${String(Math.floor(task.duration_seconds % 60)).padStart(2, '0')}` : null].filter(Boolean).join(' · ');
    ui['audio-player'].src = preview;
    ui['download-button'].href = download;
    const video = safeVideoUrl(task.video_download_url);
    ui['video-download-button'].hidden = !video;
    if (video) ui['video-download-button'].href = video;
    else ui['video-download-button'].removeAttribute('href');
    ui['video-feedback'].textContent = video ? '' : (typeof task.video_message === 'string' && task.video_message.trim()
      ? task.video_message : '原视频暂无法保存为 MP4，MP3 仍可下载');
    ui['result-content'].hidden = false;
    sharing.offer();
    ui['expiry-note'].textContent = task.expires_at ? `可下载至 ${new Date(task.expires_at * 1000).toLocaleString('zh-CN')}，到期需重新提取。` : '请及时下载，临时结果会自动清理。';
  }
  function receiving(file) {
    render();
    ui['task-stage'].textContent = file ? '正在上传' : '正在提交';
    ui['task-heading'].textContent = file ? '正在接收视频' : '正在提交分享内容';
    ui['task-message'].textContent = file ? '保持页面打开，上传后会自动提取。' : '正在创建任务，请稍候。';
    ui['upload-progress'].hidden = !file;
    ui['upload-bar'].value = 0;
    ui['upload-label'].textContent = '上传 0%';
    ui['task-heading'].focus();
  }
  function disconnected(message) {
    ui['task-message'].textContent = message;
    ui['upload-progress'].hidden = true;
    ui['reconnect-button'].hidden = false;
    ui['cancel-button'].disabled = false;
    ui['new-task-button'].hidden = true;
    ui['abandon-recovery'].hidden = false;
    ui['recovery-note'].hidden = false;
    syncBusy();
  }
  return { feedback, syncBusy, safeFileUrl, clearResult, render, receiving, disconnected };
}
