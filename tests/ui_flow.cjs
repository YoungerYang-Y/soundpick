const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
// Load browser ES modules themselves, including their import graph.
if (!vm.SourceTextModule) {
  const child = require('node:child_process').spawnSync(process.execPath,
    ['--experimental-vm-modules', __filename], { stdio: 'inherit' });
  process.exit(child.status ?? 1);
}
class Element {
  constructor(id, dataset = {}, attrs = '') {
    this.id = id; this.dataset = dataset; this.listeners = {}; this.attrs = {};
    this.hidden = /\bhidden(?:\s|>|$)/.test(attrs); this.disabled = /\bdisabled(?:\s|>|$)/.test(attrs);
    this.value = ''; this.files = []; this.textContent = '';
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
const caps = { media_available: true, platforms: [{ name: '抖音', available: true }], limits: {
  max_media_bytes: 209715200, max_duration_seconds: 1800, result_ttl_seconds: 3600, upload_timeout_seconds: 300,
} };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); await new Promise(resolve => setImmediate(resolve)); };
async function harness(initialCapabilities = caps) {
  const template = fs.readFileSync('app/templates/index.html', 'utf8');
  const elements = Object.fromEntries([...template.matchAll(/<[^>]*\bid="([\w-]+)"[^>]*>/g)].map(match => [match[1], new Element(match[1], {}, match[0])]));
  const modes = ['link', 'file'].map(mode => new Element('', { mode }));
  const panels = ['link', 'file'].map(panel => { const element = elements[`${panel}-panel`]; element.dataset.panel = panel; return element; });
  const steps = ['source', 'converting', 'ready'].map(step => new Element('', { step }));
  const document = new Element('document'); document.hidden = false;
  document.getElementById = id => { assert.ok(elements[id], id); return elements[id]; };
  document.querySelector = selector => selector.startsWith('#') ? elements[selector.slice(1)] : { value: 'test-csrf' };
  document.querySelectorAll = selector => ({ '[data-mode]': modes, '[data-panel]': panels, '[data-step]': steps })[selector];
  const window = new Element('window'); window.location = { origin: 'http://localhost' }; window.isSecureContext = true;
  let mockedNow = Date.now(), timerId = 0, randomCount = 0;
  const timers = new Map(), routes = [], requests = [], xhrs = [], shares = [];
  function queue(route, result, options = {}) { routes.push({ path: route, result, ...options }); }
  queue('/api/capabilities', initialCapabilities);
  async function fetch(route, options = {}) {
    requests.push({ path: route, options });
    const next = routes.shift(); assert.ok(next, `unexpected fetch ${route}`); assert.equal(route, next.path);
    let value;
    const result = next.result?.promise ?? next.result;
    if (next.delayAbort) value = await result; // Deliberately delayed errors reproduce callback ordering races.
    else value = await new Promise((resolve, reject) => {
      const abort = () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
      if (options.signal?.aborted) return abort();
      options.signal?.addEventListener('abort', abort, { once: true });
      Promise.resolve(result).then(resolve, reject).finally(() => options.signal?.removeEventListener('abort', abort));
    });
    if (value instanceof Error) throw value;
    if (value instanceof Response) return value;
    return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  class XHR extends Element {
    constructor() { super('xhr'); this.upload = new Element('progress'); this.headers = {}; this.completed = false; xhrs.push(this); }
    open(method, route) { this.method = method; this.path = route; }
    setRequestHeader(key, value) { this.headers[key] = value; }
    send(file) { this.body = file; }
    abort() { if (this.completed) return; this.completed = true; this.aborted = true; void this.trigger('abort'); }
    async respond(status, value) { this.completed = true; this.status = status; this.responseText = typeof value === 'string' ? value : JSON.stringify(value); await this.trigger('load'); }
  }
  const navigator = { clipboard: { readText: async () => { throw new Error('denied'); } },
    canShare: () => true, share: async data => { shares.push(data); throw Object.assign(new Error('cancelled'), { name: 'AbortError' }); } };
  class ControlledDate extends Date { static now() { return mockedNow; } }
  const context = vm.createContext({ document, window, navigator, fetch, File, Response, ReadableStream, Uint8Array, URL,
    Date: ControlledDate, XMLHttpRequest: XHR, AbortController,
    crypto: { getRandomValues: array => array.fill(0xaa + 0x11 * randomCount++) },
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; }, clearTimeout: id => timers.delete(id), console });
  const cache = new Map();
  async function load(filename) {
    filename = path.resolve(filename);
    if (cache.has(filename)) return cache.get(filename);
    const module = new vm.SourceTextModule(fs.readFileSync(filename, 'utf8'), { context, identifier: filename });
    cache.set(filename, module);
    await module.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
    return module;
  }
  const source = fs.readFileSync('app/static/app.js', 'utf8');
  if (/^import\s/m.test(source)) await (await load('app/static/app.js')).evaluate();
  else vm.runInContext(source, context);
  await flush();
  const task = (state, extra = {}) => ({ id: 'a'.repeat(32), state, message: '', title: '<b>来源标题</b>', author: '<img src=x>',
    duration_seconds: 2, expires_at: mockedNow / 1000 + 3600,
    preview_url: `/files/${'a'.repeat(32)}`, download_url: `/files/${'a'.repeat(32)}?download=1`, filename: '来源.mp3', ...extra });
  const click = async id => { assert.equal(elements[id].hidden, false, `${id} is hidden`); assert.equal(elements[id].disabled, false, `${id} is disabled`); await elements[id].trigger('click'); };
  const submit = () => elements['extraction-form'].trigger('submit');
  async function fireTimer(ms) { const entry = [...timers].find(([, timer]) => timer.ms === ms); assert.ok(entry, `missing timer ${ms}`); timers.delete(entry[0]); const pending = entry[1].fn(); await flush(); return pending; }
  return { ui: elements, document, window, navigator, timers, routes, requests, xhrs, shares, queue, task, click, submit, fireTimer,
    mode: value => modes[value === 'file' ? 1 : 0].trigger('click'), setNow: value => { mockedNow = value; } };
}
const tests = [];
function test(name, fn) { tests.push({ name, fn }); }
const taskPath = `/api/tasks/${'a'.repeat(32)}`;
async function startLink(h, state = 'resolving', extra = {}) { h.ui['share-text'].value = '分享链接'; h.queue('/api/tasks/link', h.task(state, extra)); await h.submit(); }

test('pagehide invalidates a delayed poll failure before pageshow renders ready', async () => {
  const h = await harness(); await startLink(h);
  const old = deferred(); h.queue(taskPath, old, { delayAbort: true });
  const timer = [...h.timers].find(([, value]) => value.ms === 1800); h.timers.delete(timer[0]);
  const polling = timer[1].fn(); await flush();
  await h.window.trigger('pagehide');
  h.queue(taskPath, h.task('ready')); await h.window.trigger('pageshow', { persisted: true }); await flush();
  assert.equal(h.ui['result-content'].hidden, false);
  const readyMessage = h.ui['task-message'].textContent;
  old.reject(Object.assign(new Error('late abort'), { name: 'AbortError' })); await polling; await flush();
  assert.equal(h.ui['task-message'].textContent, readyMessage);
  assert.equal(h.ui['reconnect-button'].hidden, true);
});
test('upload reserves its known id before XHR and renders a successful load', async () => {
  const h = await harness(); await h.mode('file');
  const file = new File(['1234'], '测试视频.mp4', { type: 'video/mp4' }); h.ui['video-file'].files = [file];
  h.queue('/api/tasks/reservations', h.task('receiving'));
  const pending = h.submit(); await flush();
  assert.equal(h.xhrs.length, 1);
  assert.equal(h.xhrs[0].path, `${taskPath}/upload`);
  const reservation = JSON.parse(h.requests.at(-1).options.body);
  assert.equal(reservation.task_id, 'a'.repeat(32)); assert.equal(reservation.filename, file.name);
  await h.xhrs[0].upload.trigger('progress', { lengthComputable: true, loaded: 2, total: 4 });
  assert.equal(h.ui['upload-bar'].value, 50);
  await h.xhrs[0].respond(202, h.task('converting')); await pending;
  assert.equal(h.ui['task-heading'].textContent, '正在提取完整音轨');
  assert.equal(h.ui['source-fields'].disabled, true);
  assert.ok([...h.timers.values()].some(timer => timer.ms === 1800), 'successful upload schedules polling');
});

test('aborting upload retains the known task until server cancellation is confirmed', async () => {
  const h = await harness(); await h.mode('file');
  h.ui['video-file'].files = [new File(['1234'], '视频.mp4')];
  h.queue('/api/tasks/reservations', h.task('receiving'));
  const pending = h.submit(); await flush();
  h.queue(`${taskPath}/cancel`, new TypeError('offline'));
  await h.click('cancel-button'); await pending; await flush();
  assert.equal(h.xhrs[0].aborted, true);
  assert.equal(h.ui['source-fields'].disabled, true);
  assert.equal(h.ui['new-task-button'].hidden, true);
  assert.equal(h.ui['reconnect-button'].hidden, false);
  assert.match(h.ui['task-message'].textContent, /取消未获确认/);
  h.queue(taskPath, h.task('cancelled')); await h.click('reconnect-button'); await flush();
  assert.equal(h.ui['task-heading'].textContent, '已取消处理');
  assert.equal(h.ui['source-fields'].disabled, false);
});
test('creation completion retries cancellation after an earlier in-flight 404', async () => {
  const h = await harness(), creation = deferred(), cancellation = deferred();
  h.ui['share-text'].value = '链接'; h.queue('/api/tasks/link', creation);
  const submitting = h.submit(); await flush();
  h.queue(`${taskPath}/cancel`, cancellation);
  const cancelling = h.click('cancel-button'); await flush();
  creation.resolve(h.task('resolving')); await flush();
  h.queue(`${taskPath}/cancel`, h.task('cancelled'));
  cancellation.resolve(new Response(JSON.stringify({ detail: 'not yet created' }), { status: 404 }));
  await cancelling; await submitting; await flush();
  assert.equal(h.ui['task-heading'].textContent, '已取消处理');
  assert.equal(h.ui['source-fields'].disabled, false);
  assert.equal(h.routes.length, 0);
});

const httpError = (status, detail = '服务器拒绝请求') => new Response(JSON.stringify({ detail }), { status });
async function beginUpload(h) {
  await h.mode('file'); h.ui['video-file'].files = [new File(['1234'], '测试视频.mp4', { type: 'video/mp4' })];
  h.queue('/api/tasks/reservations', h.task('receiving'));
  const pending = h.submit(); await flush(); return { pending, xhr: h.xhrs.at(-1) };
}
test('capabilities failure keeps initially disabled controls locked until a successful retry', async () => {
  const h = await harness(new TypeError('offline'));
  assert.equal(h.ui['source-fields'].disabled, true); assert.equal(h.ui['retry-capabilities'].hidden, false);
  assert.equal(h.ui['task-panel'].hidden, true); assert.equal(h.ui['result-content'].hidden, true);
  await h.submit(); assert.equal(h.requests.length, 1);
  h.queue('/api/capabilities', caps); await h.click('retry-capabilities');
  assert.equal(h.ui['source-fields'].disabled, false); assert.match(h.ui['limits-note'].textContent, /200 MiB/);
});
test('empty inputs, clipboard rejection and oversized files cannot create a task', async () => {
  const h = await harness(); await h.submit(); assert.match(h.ui['form-feedback'].textContent, /先粘贴/);
  await h.click('paste-button'); assert.match(h.ui['form-feedback'].textContent, /长按/);
  await h.mode('file'); await h.submit(); assert.match(h.ui['form-feedback'].textContent, /非空/);
  h.ui['video-file'].files = [{ size: caps.limits.max_media_bytes + 1, name: '大视频.mp4' }];
  await h.ui['video-file'].trigger('change'); assert.match(h.ui['file-details'].textContent, /大视频/);
  await h.submit(); assert.match(h.ui['form-feedback'].textContent, /大小限制/);
  assert.equal(h.xhrs.length, 0); assert.equal(h.requests.length, 1);
});
test('creation response loss is recovered using the same client-generated link id', async () => {
  const h = await harness(); h.ui['share-text'].value = '分享链接'; h.queue('/api/tasks/link', new TypeError('response lost'));
  await h.submit();
  assert.equal(JSON.parse(h.requests.at(-1).options.body).task_id, 'a'.repeat(32));
  assert.equal(h.ui['source-fields'].disabled, true); assert.equal(h.ui['new-task-button'].hidden, true);
  h.queue(taskPath, h.task('ready')); await h.click('reconnect-button'); await flush();
  assert.equal(h.ui['result-content'].hidden, false); assert.equal(h.ui['source-fields'].disabled, false);
});
test('reservation response loss can confirm its id before beginning the upload', async () => {
  const h = await harness(); await h.mode('file'); h.ui['video-file'].files = [new File(['video'], '视频.mp4')];
  h.queue('/api/tasks/reservations', new TypeError('response lost')); await h.submit();
  assert.equal(h.xhrs.length, 0);
  h.queue(taskPath, h.task('receiving')); await h.click('reconnect-button'); await flush();
  assert.equal(h.xhrs[0].path, `${taskPath}/upload`);
  await h.xhrs[0].respond(202, h.task('ready')); await flush(); assert.equal(h.ui['result-content'].hidden, false);
});
test('an unknown id 404 after creation loss is not claimed as cancelled or expired', async () => {
  const h = await harness(); h.ui['share-text'].value = '分享链接'; h.queue('/api/tasks/link', new TypeError('response lost')); await h.submit();
  h.queue(taskPath, httpError(404)); await h.click('reconnect-button'); await flush();
  assert.equal(h.ui['source-fields'].disabled, true); assert.match(h.ui['task-message'].textContent, /尚未确认/);
  h.queue(`${taskPath}/cancel`, httpError(404)); await h.click('cancel-button');
  assert.match(h.ui['task-message'].textContent, /取消未获确认/); assert.equal(h.ui['new-task-button'].hidden, true);
});
for (const status of [401, 403, 413]) test(`XHR load HTTP ${status} keeps its reserved id for cancellation`, async () => {
  const h = await harness(); const { pending, xhr } = await beginUpload(h);
  await xhr.respond(status, { detail: '服务器拒绝上传' }); await pending;
  assert.equal(h.ui['source-fields'].disabled, true); assert.equal(h.ui['reconnect-button'].hidden, false);
  assert.match(h.ui['task-message'].textContent, status === 401 ? /登录已失效/ : status === 403 ? /页面验证已过期/ : /服务器拒绝上传/);
  h.queue(`${taskPath}/cancel`, h.task('cancelled')); await h.click('cancel-button');
  assert.equal(h.ui['task-heading'].textContent, '已取消处理');
});
test('XHR timeout and invalid success JSON recover server processing without repeating the upload', async () => {
  for (const event of ['timeout', 'invalid-json', 'error']) {
    const h = await harness(); const { pending, xhr } = await beginUpload(h);
    if (event === 'invalid-json') await xhr.respond(202, 'not json'); else { xhr.completed = true; await xhr.trigger(event); }
    await pending; assert.equal(h.ui['source-fields'].disabled, true); assert.equal(h.ui['new-task-button'].hidden, true);
    h.queue(taskPath, h.task('converting')); await h.click('reconnect-button'); await flush();
    assert.equal(h.xhrs.length, 1); assert.equal(h.ui['task-heading'].textContent, '正在提取完整音轨');
  }
});
test('XHR HTTP 404 for a confirmed reservation releases controls with an expired message', async () => {
  const h = await harness(); const { pending, xhr } = await beginUpload(h);
  await xhr.respond(404, { detail: '任务不存在' }); await pending;
  assert.equal(h.ui['source-fields'].disabled, false); assert.match(h.ui['task-message'].textContent, /过期/);
});
for (const status of [401, 403, 404]) test(`poll HTTP ${status} has explicit auth or expiry handling`, async () => {
  const h = await harness(); await startLink(h); h.queue(taskPath, httpError(status)); await h.fireTimer(1800);
  if (status === 404) { assert.equal(h.ui['source-fields'].disabled, false); assert.match(h.ui['task-message'].textContent, /过期/); }
  else { assert.equal(h.ui['source-fields'].disabled, true); assert.match(h.ui['task-message'].textContent, status === 401 ? /登录已失效/ : /页面验证已过期/); }
});
for (const status of [401, 403]) test(`creation HTTP ${status} is a confirmed rejection that can be retried after refreshing`, async () => {
  const h = await harness(); h.ui['share-text'].value = '分享链接'; h.queue('/api/tasks/link', httpError(status)); await h.submit();
  assert.equal(h.ui['source-fields'].disabled, false); assert.equal(h.ui['new-task-button'].hidden, false);
  assert.match(h.ui['task-message'].textContent, status === 401 ? /登录已失效/ : /页面验证已过期/);
});
test('a delayed cancelled poll cannot overwrite the server cancellation result', async () => {
  const h = await harness(); await startLink(h);
  const old = deferred(); h.queue(taskPath, old, { delayAbort: true });
  const timer = [...h.timers].find(([, value]) => value.ms === 1800); h.timers.delete(timer[0]); const polling = timer[1].fn(); await flush();
  h.queue(`${taskPath}/cancel`, h.task('cancelled')); await h.click('cancel-button');
  const message = h.ui['task-message'].textContent;
  old.reject(Object.assign(new Error('delayed abort'), { name: 'AbortError' })); await polling;
  assert.equal(h.ui['task-heading'].textContent, '已取消处理'); assert.equal(h.ui['task-message'].textContent, message);
  assert.equal(h.ui['reconnect-button'].hidden, true);
});
test('ready renders untrusted metadata as text and only task-bound same-origin video URLs', async () => {
  const h = await harness(); const video = `/files/${'a'.repeat(32)}?kind=video&download=1`;
  await startLink(h, 'ready', { video_download_url: video, video_filename: '来源.mp4' });
  assert.equal(h.ui['task-heading'].textContent, '<b>来源标题</b>'); assert.match(h.ui['result-meta'].textContent, /<img src=x>/);
  assert.equal(h.ui['video-download-button'].href, `http://localhost${video}`);
  await h.click('new-task-button'); assert.equal(h.ui['video-download-button'].href, undefined);
  const id = 'b'.repeat(32); await startLink(h, 'ready', { id, preview_url: `/files/${id}`, download_url: `/files/${id}?download=1`, video_download_url: 'https://evil.example/files/x?kind=video&download=1' });
  assert.equal(h.ui['video-download-button'].hidden, true); assert.match(h.ui['video-feedback'].textContent, /原视频暂无法保存/);
});
test('successful share preparation keeps a separate user gesture and handles user cancellation', async () => {
  const h = await harness(); await startLink(h, 'ready');
  h.queue(`http://localhost/files/${'a'.repeat(32)}?download=1`, new Response(new Uint8Array([1, 2, 3]), { headers: { 'Content-Length': '3' } }));
  await h.click('prepare-share'); assert.equal(h.shares.length, 0); assert.equal(h.ui['share-button'].hidden, false);
  await h.click('share-button'); assert.equal(h.shares.length, 1); assert.equal(h.shares[0].files[0].size, 3);
  assert.match(h.ui['share-feedback'].textContent, /已取消分享/);
});
test('canShare false and insecure origins preserve the ordinary download fallback', async () => {
  const h = await harness(); await startLink(h, 'ready'); h.navigator.canShare = () => false;
  h.queue(`http://localhost/files/${'a'.repeat(32)}?download=1`, new Response(new Uint8Array([1])));
  await h.click('prepare-share'); assert.equal(h.ui['share-button'].hidden, true); assert.match(h.ui['share-feedback'].textContent, /不能分享/);
  assert.ok(h.ui['download-button'].href);
  const insecure = await harness(); insecure.window.isSecureContext = false; await startLink(insecure, 'ready');
  assert.equal(insecure.ui['prepare-share'].hidden, true); assert.match(insecure.ui['share-feedback'].textContent, /不支持文件分享/);
});
test('share size limits count actual streamed bytes and cancel an oversized body', async () => {
  for (const advertised of [false, true]) {
    const h = await harness(); await startLink(h, 'ready'); let cancelled = false;
    const stream = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(1100000)); }, cancel() { cancelled = true; } });
    h.queue(`http://localhost/files/${'a'.repeat(32)}?download=1`, new Response(stream, advertised ? { headers: { 'Content-Length': '99999999' } } : {}));
    await h.click('prepare-share'); assert.equal(cancelled, true); assert.equal(h.ui['share-button'].hidden, true);
    assert.match(h.ui['share-feedback'].textContent, /准备失败/); assert.equal(h.ui['prepare-share'].disabled, false);
  }
});
for (const status of [401, 403, 404]) test(`share preparation HTTP ${status} shows auth or expiry feedback`, async () => {
  const h = await harness(); await startLink(h, 'ready'); h.queue(`http://localhost/files/${'a'.repeat(32)}?download=1`, httpError(status));
  await h.click('prepare-share');
  assert.match(status === 404 ? h.ui['task-message'].textContent : h.ui['share-feedback'].textContent,
    status === 401 ? /登录已失效/ : status === 403 ? /页面验证已过期/ : /过期/);
});
test('reset during share preparation aborts its request and ignores a deliberately delayed completion', async () => {
  const h = await harness(); await startLink(h, 'ready'); const late = deferred();
  h.queue(`http://localhost/files/${'a'.repeat(32)}?download=1`, late, { delayAbort: true });
  const preparing = h.click('prepare-share'); await flush(); const signal = h.requests.at(-1).options.signal;
  await h.click('new-task-button'); assert.equal(signal.aborted, true);
  late.resolve(new Response(new Uint8Array([1, 2, 3]))); await preparing;
  assert.equal(h.ui['result-content'].hidden, true); assert.equal(h.ui['share-button'].hidden, true); assert.equal(h.ui['share-feedback'].textContent, '');
});
test('reset while share bytes are pending cancels the reader and releases the cache', async () => {
  const h = await harness(); await startLink(h, 'ready'); let cancelled = false;
  h.queue(`http://localhost/files/${'a'.repeat(32)}?download=1`, new Response(new ReadableStream({ cancel() { cancelled = true; } })));
  const preparing = h.click('prepare-share'); await flush(); await h.click('new-task-button'); await preparing;
  assert.equal(cancelled, true); assert.equal(h.ui['share-button'].hidden, true);
});
test('expired MP4 clicks block navigation and clear both result links', async () => {
  const h = await harness(); const expires = Date.now() / 1000 + 5;
  await startLink(h, 'ready', { expires_at: expires, video_download_url: `/files/${'a'.repeat(32)}?kind=video&download=1` });
  h.setNow(expires * 1000 + 1); let prevented = false;
  await h.ui['video-download-button'].trigger('click', { preventDefault() { prevented = true; } });
  assert.equal(prevented, true); assert.equal(h.ui['download-button'].href, undefined); assert.equal(h.ui['video-download-button'].href, undefined);
  assert.equal(h.ui['result-content'].hidden, true); assert.match(h.ui['task-message'].textContent, /过期/);
});
test('pageshow reconfirms a cancellation whose old response belongs to the hidden page', async () => {
  const h = await harness(); await startLink(h); const oldCancel = deferred();
  h.queue(`${taskPath}/cancel`, oldCancel, { delayAbort: true });
  const cancelling = h.click('cancel-button'); await flush();
  await h.window.trigger('pagehide'); await h.window.trigger('pageshow', { persisted: true });
  h.queue(`${taskPath}/cancel`, h.task('cancelled'));
  oldCancel.resolve(h.task('cancelled')); await cancelling; await flush();
  assert.equal(h.ui['task-heading'].textContent, '已取消处理');
  assert.equal(h.ui['source-fields'].disabled, false); assert.equal(h.routes.length, 0);
});
test('pageshow also resumes an idle page so later tasks can render and poll', async () => {
  const h = await harness(); await h.window.trigger('pagehide'); await h.window.trigger('pageshow', { persisted: true });
  await startLink(h); assert.equal(h.ui['task-heading'].textContent, '正在解析分享内容');
  assert.ok([...h.timers.values()].some(timer => timer.ms === 1800));
});
(async () => {
  for (const entry of tests) { await entry.fn(); console.log(`PASS: ${entry.name}`); }
  console.log(`PASS: ${tests.length} browser-module behavior scenarios`);
})().catch(error => { console.error(error); process.exitCode = 1; });
