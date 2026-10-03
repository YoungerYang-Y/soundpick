const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(id, dataset = {}) {
    this.id = id; this.dataset = dataset; this.listeners = {}; this.attrs = {};
    this.hidden = false; this.disabled = false; this.value = ''; this.files = []; this.textContent = '';
    this.classList = { toggle() {}, remove() {} };
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async trigger(name, props = {}) { for (const fn of this.listeners[name] || []) await fn({ preventDefault() {}, ...props }); }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this.attrs[name]; if (name === 'href' || name === 'src') delete this[name]; }
  hasAttribute(name) { return name in this.attrs || Boolean(this[name]); }
  focus() { this.focused = true; }
  pause() {}
  load() {}
}
const template = fs.readFileSync('app/templates/index.html', 'utf8');
const ids = [...template.matchAll(/id="([\w-]+)"/g)].map(x => x[1]);
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
const modes = ['link', 'file'].map(mode => new Element('', { mode }));
const panels = ['link', 'file'].map(panel => new Element('', { panel }));
const steps = ['source', 'converting', 'ready'].map(step => new Element('', { step }));
const document = new Element('document');
document.hidden = false;
document.getElementById = id => { assert.ok(elements[id], id); return elements[id]; };
document.querySelector = selector => selector.startsWith('#') ? elements[selector.slice(1)] : { value: 'test-csrf' };
document.querySelectorAll = selector => ({ '[data-mode]': modes, '[data-panel]': panels, '[data-step]': steps })[selector];
const window = new Element('window'); window.location = { origin: 'http://localhost' }; window.isSecureContext = true;
let timers = new Map(), timerId = 0, routes = [], requests = [], shares = [];
const caps = { media_available: true, platforms: [{ name: '抖音', available: true }], limits: {
  max_media_bytes: 209715200, max_duration_seconds: 1800, result_ttl_seconds: 3600, upload_timeout_seconds: 300,
} };
const task = (state, extra = {}) => ({ id: 'a'.repeat(32), state, message: '', title: '<b>来源标题</b>', author: '<img src=x>',
  duration_seconds: 2, expires_at: Date.now() / 1000 + 3600,
  preview_url: `/files/${'a'.repeat(32)}`, download_url: `/files/${'a'.repeat(32)}?download=1`, filename: '来源.mp3', ...extra });
function queue(path, result) { routes.push({ path, result }); }
queue('/api/capabilities', caps);
async function fetch(path, options) {
  requests.push({ path, options });
  const next = routes.shift(); assert.ok(next, `unexpected fetch ${path}`); assert.equal(path, next.path);
  if (next.result instanceof Promise) next.result = await next.result;
  if (next.result instanceof Error) throw next.result;
  if (next.result instanceof Response) return next.result;
  return new Response(JSON.stringify(next.result), { status: 200, headers: { 'Content-Type': 'application/json' } });
}
let xhr;
class XHR extends Element {
  constructor() { super('xhr'); this.upload = new Element('progress'); this.headers = {}; xhr = this; }
  open(method, path) { this.method = method; this.path = path; }
  setRequestHeader(key, value) { this.headers[key] = value; }
  send(file) { this.body = file; }
  abort() { this.trigger('abort'); }
}
const navigator = { clipboard: { readText: async () => { throw new Error('denied'); } },
  canShare: () => true, share: async data => { shares.push(data); throw Object.assign(new Error('cancelled'), { name: 'AbortError' }); } };
let mockedNow = Date.now();
class ControlledDate extends Date { static now() { return mockedNow; } }
vm.runInNewContext(fs.readFileSync('app/static/app.js', 'utf8'), {
  document, window, navigator, fetch, File, Response, URL, Date: ControlledDate, XMLHttpRequest: XHR, AbortController,
  setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; }, clearTimeout: id => timers.delete(id), console,
});
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const click = id => elements[id].trigger('click');
const submit = () => elements['extraction-form'].trigger('submit');
async function fireTimer(ms) { const entry = [...timers].find(([,t]) => t.ms === ms); assert.ok(entry, `missing timer ${ms}`); timers.delete(entry[0]); await entry[1].fn(); await flush(); }
(async () => {
  await flush();
  assert.equal(elements['source-fields'].disabled, false);
  assert.match(elements['limits-note'].textContent, /200 MiB/);
  await submit(); assert.match(elements['form-feedback'].textContent, /先粘贴/); assert.equal(requests.length, 1);
  await click('paste-button'); assert.match(elements['form-feedback'].textContent, /长按/); assert.ok(elements['share-text'].focused);
  elements['share-text'].value = '分享链接'; queue('/api/tasks/link', task('resolving'));
  await submit(); assert.equal(requests.at(-1).options.headers['X-CSRF-Token'], 'test-csrf');
  assert.equal(elements['source-fields'].disabled, true);
  queue(`/api/tasks/${'a'.repeat(32)}`, new TypeError('offline'));
  await fireTimer(1800); assert.equal(elements['reconnect-button'].hidden, false);
  assert.equal(elements['source-fields'].disabled, true); // Preserve active task across disconnect.
  // A ready task may expose a same-origin MP4 download independently from MP3 sharing.
  const videoUrl = `/files/${'a'.repeat(32)}?kind=video&download=1`;
  queue(`/api/tasks/${'a'.repeat(32)}`, task('ready', {
    video_download_url: videoUrl, video_filename: '来源.mp4', video_message: '',
  }));
  await click('reconnect-button'); await flush();
  assert.equal(elements['task-heading'].textContent, '<b>来源标题</b>');
  assert.match(elements['result-meta'].textContent, /<img src=x>/);
  assert.equal(elements['result-content'].hidden, false); assert.equal(elements['prepare-share'].hidden, false);
  assert.equal(elements['video-download-button'].hidden, false);
  assert.equal(elements['video-download-button'].href, `http://localhost${videoUrl}`);
  assert.equal(elements['video-feedback'].textContent, '');
  assert.equal(shares.length, 0);
  queue(`http://localhost/files/${'a'.repeat(32)}?download=1`, new Response(new Uint8Array([1, 2, 3]), { headers: { 'Content-Length': '3' } }));
  await click('prepare-share'); assert.equal(elements['share-button'].hidden, false); assert.equal(shares.length, 0);
  await click('share-button'); assert.equal(shares.length, 1); assert.equal(shares[0].files[0].size, 3);
  assert.match(elements['share-feedback'].textContent, /已取消分享/);
  const expiry = [...timers].find(([, t]) => t.ms > 1000000); assert.ok(expiry);
  timers.delete(expiry[0]); expiry[1].fn();
  assert.equal(elements['result-content'].hidden, true); assert.equal(elements['download-button'].href, undefined);
  assert.equal(elements['video-download-button'].hidden, true);
  assert.equal(elements['video-download-button'].href, undefined);
  assert.match(elements['task-message'].textContent, /过期/);
  await click('new-task-button'); await modes[1].trigger('click');
  const file = new File(['1234'], '测试视频.mp4', { type: 'video/mp4' }); elements['video-file'].files = [file];
  const pending = submit(); await flush(); assert.equal(xhr.body, file); assert.equal(xhr.headers['X-CSRF-Token'], 'test-csrf');
  assert.equal(xhr.path, '/api/tasks/upload?filename=' + encodeURIComponent(file.name));
  await xhr.upload.trigger('progress', { lengthComputable: true, loaded: 2, total: 4 });
  assert.equal(elements['upload-bar'].value, 50);
  await click('cancel-button'); await pending; assert.match(elements['task-heading'].textContent, /已停止上传/);
  assert.equal(elements['source-fields'].disabled, false);
  elements['video-file'].files = [{ size: 209715201 }]; const oldXHR = xhr;
  await submit(); assert.match(elements['form-feedback'].textContent, /大小限制/); assert.equal(xhr, oldXHR);
  // A cancelled poll may reject after the cancellation response rendered its terminal state.
  await modes[0].trigger('click'); queue('/api/tasks/link', task('resolving')); await submit();
  let rejectOldPoll;
  queue(`/api/tasks/${'a'.repeat(32)}`, new Promise((resolve, reject) => { rejectOldPoll = reject; }));
  const pendingTimer = [...timers].find(([,t]) => t.ms === 1800); timers.delete(pendingTimer[0]);
  const pendingPoll = pendingTimer[1].fn(); await flush();
  queue(`/api/tasks/${'a'.repeat(32)}/cancel`, task('cancelled'));
  await click('cancel-button'); assert.equal(elements['task-heading'].textContent, '已取消处理');
  const cancelledMessage = elements['task-message'].textContent;
  rejectOldPoll(Object.assign(new Error('abort'), { name: 'AbortError' }));
  await pendingPoll; await flush();
  assert.equal(elements['task-heading'].textContent, '已取消处理');
  assert.equal(elements['task-message'].textContent, cancelledMessage);
  assert.equal(elements['reconnect-button'].hidden, true);
  // Even when APIs exist, a non-secure origin must offer download rather than a preparation action.
  await click('new-task-button'); window.isSecureContext = false;
  queue('/api/tasks/link', task('ready')); await submit();
  assert.equal(elements['prepare-share'].hidden, true);
  assert.match(elements['share-feedback'].textContent, /不支持文件分享/);
  assert.equal(elements['video-download-button'].hidden, true); // Older TaskView remains MP3-compatible.
  assert.match(elements['video-feedback'].textContent, /原视频暂无法保存为 MP4，MP3 仍可下载/);
  await click('new-task-button'); window.isSecureContext = true;
  queue('/api/tasks/link', task('ready', { video_download_url: 'https://evil.example/files/x?kind=video&download=1' }));
  await submit();
  assert.equal(elements['video-download-button'].hidden, true);
  assert.equal(elements['video-download-button'].href, undefined);
  assert.equal(elements['download-button'].href, `http://localhost/files/${'a'.repeat(32)}?download=1`);
  assert.match(elements['video-feedback'].textContent, /原视频暂无法保存为 MP4，MP3 仍可下载/);
  await click('new-task-button');
  const expiringVideoTask = task('ready', { video_download_url: videoUrl, video_filename: '来源.mp4', video_message: '' });
  queue('/api/tasks/link', expiringVideoTask);
  await submit();
  assert.equal(elements['video-download-button'].hidden, false);
  mockedNow = (expiringVideoTask.expires_at * 1000) + 1;
  let expiryClickPrevented = false;
  await elements['video-download-button'].trigger('click', { preventDefault() { expiryClickPrevented = true; } });
  assert.equal(expiryClickPrevented, true);
  assert.equal(elements['video-download-button'].hidden, true);
  assert.equal(elements['video-download-button'].href, undefined);
  await click('new-task-button');
  assert.equal(elements['video-download-button'].hidden, true);
  assert.equal(elements['video-download-button'].href, undefined);
  mockedNow = Date.now();
  queue('/api/tasks/link', task('resolving')); await submit();
  queue(`/api/tasks/${'a'.repeat(32)}/cancel`, task('cancelled', {
    video_download_url: videoUrl, video_filename: '来源.mp4', video_message: '',
  }));
  await click('cancel-button');
  assert.equal(elements['video-download-button'].hidden, true);
  assert.equal(elements['video-download-button'].href, undefined);
  assert.equal(routes.length, 0, JSON.stringify(routes.map(route => route.path)));
  console.log('PASS: capabilities, validation, clipboard fallback, retained-task reconnection, safe text, MP3 sharing and cancellation, expiry cleanup, MP4 same-origin/unavailable/new-task/cancel cleanup, raw upload progress/cancellation, size limit, late-abort cancellation race and insecure-origin sharing fallback');
})().catch(error => { console.error(error); process.exitCode = 1; });
