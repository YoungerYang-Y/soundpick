/** One bounded MP3 cache, prepared before the separate user-gesture share action. */
export function createFileSharing(ui, state, { expired, markExpired, responseError, errorMessage, fileUrl }) {
  let controller = null, reader = null, file = null, revision = 0;
  const supported = () => window.isSecureContext === true && typeof navigator.share === 'function' &&
    typeof navigator.canShare === 'function' && typeof File === 'function';
  function reset() {
    revision += 1;
    controller?.abort();
    controller = null;
    if (reader) void reader.cancel().catch(() => {});
    reader = null; file = null;
    ui['prepare-share'].disabled = false;
    ui['prepare-share'].textContent = '准备分享文件';
    ui['prepare-share'].hidden = true;
    ui['share-button'].hidden = true;
    ui['share-button'].disabled = false;
    ui['share-feedback'].textContent = '';
  }
  function offer() {
    if (file || controller) return;
    ui['prepare-share'].hidden = !supported();
    if (!supported()) ui['share-feedback'].textContent = '此浏览器不支持文件分享，请使用下载 MP3。';
  }
  ui['prepare-share'].addEventListener('click', async () => {
    if (!state.task || state.task.state !== 'ready' || expired()) return markExpired();
    if (controller || !supported()) return;
    const token = revision, task = state.task, ownController = new AbortController();
    controller = ownController;
    const current = () => token === revision && state.task === task && !expired();
    ui['prepare-share'].disabled = true;
    ui['prepare-share'].textContent = '正在准备文件…';
    ui['share-feedback'].textContent = '正在读取 MP3，请稍候；完成后再点击系统分享。';
    const timeout = setTimeout(() => ownController.abort(), 60000);
    let ownReader;
    try {
      const url = fileUrl(task.download_url);
      if (!url) throw new Error('share_url');
      const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', signal: ownController.signal });
      if (!current()) { await response.body?.cancel(); return; }
      if (!response.ok) throw await responseError(response);
      const seconds = Number.isFinite(task.duration_seconds) && task.duration_seconds > 0
        ? task.duration_seconds : state.capabilities?.limits.max_duration_seconds || 1800;
      const maxBytes = Math.ceil(seconds * 24000) + 1024 * 1024;
      if (Number(response.headers.get('Content-Length')) > maxBytes) {
        await response.body?.cancel();
        throw new Error('share_size');
      }
      ownReader = response.body?.getReader();
      if (!ownReader) throw new Error('share_stream');
      reader = ownReader;
      const chunks = []; let size = 0;
      for (;;) {
        const { done, value } = await ownReader.read();
        if (!current()) return;
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) { await ownReader.cancel(); throw new Error('share_size'); }
        chunks.push(value);
      }
      if (!size) throw new Error('share_empty');
      const prepared = new File(chunks, task.filename || '音拾.mp3', { type: 'audio/mpeg' });
      if (!navigator.canShare({ files: [prepared] })) {
        ui['share-feedback'].textContent = '此浏览器不能分享 MP3 文件，请使用下载 MP3。';
        ui['prepare-share'].hidden = true;
      } else {
        file = prepared;
        ui['prepare-share'].hidden = true;
        ui['share-button'].hidden = false;
        ui['share-feedback'].textContent = '文件已准备好，点击“打开系统分享”选择保存或分享目标。';
      }
    } catch (error) {
      if (!current()) return;
      if (error.status === 404) return markExpired();
      ui['share-feedback'].textContent = error.status ? errorMessage(error) : '分享文件准备失败，可重试或直接下载 MP3。';
    } finally {
      clearTimeout(timeout);
      if (reader === ownReader) reader = null;
      ownReader?.releaseLock();
      if (controller === ownController) controller = null;
      if (token === revision) {
        ui['prepare-share'].disabled = false;
        ui['prepare-share'].textContent = '准备分享文件';
      }
    }
  });
  ui['share-button'].addEventListener('click', async () => {
    if (expired()) return markExpired();
    if (!file) return;
    const token = revision;
    ui['share-button'].disabled = true;
    try {
      await navigator.share({ files: [file] });
      if (token === revision) ui['share-feedback'].textContent = '系统分享已关闭，请到所选应用或“文件”中确认保存结果。';
    } catch (error) {
      if (token !== revision) return;
      ui['share-feedback'].textContent = error.name === 'AbortError' ? '已取消分享，仍可下载 MP3。' : '系统分享未能打开，请使用下载 MP3。';
    } finally {
      if (token === revision) ui['share-button'].disabled = false;
    }
  });
  return { reset, offer };
}
