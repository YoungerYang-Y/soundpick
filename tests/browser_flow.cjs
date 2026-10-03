// Real browser + HTTP + FFmpeg regressions, using an isolated server and temporary data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require(process.env.SOUNDPICK_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const python = path.join(root, '.venv/bin/python');
const repeats = Number(process.env.SOUNDPICK_BROWSER_REPEAT || 5);
assert.ok(Number.isInteger(repeats) && repeats > 1, 'browser regression repeat must be > 1');
const deferred = () => { let resolve, reject; const promise = new Promise((r,j) => {resolve = r; reject = j;}); return {promise, resolve, reject}; };

async function freePort() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

async function main() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'soundpick-browser-'));
  let server, browser;
  try {
    const source = path.join(temporary, '完整视频.mp4');
    const ffmpeg = process.env.FFMPEG_BIN || 'ffmpeg';
    const realFFmpeg = execFileSync(python, ['-c', 'import shutil,sys;print(shutil.which(sys.argv[1]) or "")', ffmpeg], {encoding:'utf8'}).trim();
    assert.ok(realFFmpeg, 'FFmpeg must be available');
    execFileSync(realFFmpeg, ['-nostdin','-v','error','-f','lavfi','-i','color=c=red:s=160x120:r=10:d=3','-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','mpeg4','-c:a','aac',source], {timeout:15000});
    const paced = path.join(temporary, 'paced-ffmpeg');
    fs.writeFileSync(paced, `#!${python}\nimport os,sys\nargs=sys.argv[1:]\nargs.insert(args.index('-i'), '-re')\nos.execv(${JSON.stringify(realFFmpeg)},[${JSON.stringify(realFFmpeg)},*args])\n`, {mode:0o700});
    const port = await freePort();
    const base = `http://127.0.0.1:${port}`;
    const password = 'isolated-browser-test-password';
    server = spawn(python, ['-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port',String(port),'--no-access-log'], {
      cwd:root, stdio:['ignore','ignore','ignore'], env:{...process.env, APP_ENV:'local', COOKIE_SECURE:'false', APP_PASSWORD:password, SESSION_SECRET:'isolated-browser-session-secret'.repeat(3), DATA_DIR:path.join(temporary,'data'), FFMPEG_BIN:paced}
    });
    browser = await chromium.launch({headless:true, ...(process.env.SOUNDPICK_CHROMIUM_BIN ? {executablePath:process.env.SOUNDPICK_CHROMIUM_BIN} : {})});
    // Navigation retries only wait for the isolated server; test steps use observable conditions.
    const readiness = await browser.newContext();
    const deadline = Date.now() + 10000;
    while (true) {
      assert.equal(server.exitCode, null, 'isolated server exited during startup');
      try { if ((await readiness.request.get(`${base}/healthz`)).status() === 200) break; }
      catch (error) { if (Date.now() >= deadline) throw error; }
      assert.ok(Date.now() < deadline, 'isolated server did not become ready');
    }
    await readiness.close();

    for (let iteration=0; iteration<repeats; iteration++) {
      const context = await browser.newContext({viewport:{width:375,height:812}});
      try {
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base);
        await page.locator('input[name=password]').fill(password);
        await page.getByRole('button', {name:'登录', exact:true}).click();
        await page.locator('#source-fields:not([disabled])').waitFor();
        await page.getByRole('button', {name:'本地视频',exact:true}).click();
        await page.locator('#video-file').setInputFiles(source);
        await page.getByRole('button', {name:'开始提取',exact:true}).click();
        await page.locator('#video-download-button[href]').waitFor({timeout:20000});
        await page.locator('#audio-player').evaluate(audio => audio.load());
        await page.waitForFunction(() => document.getElementById('audio-player').readyState >= 2);
        const duration = await page.locator('#audio-player').evaluate(audio => audio.duration);
        assert.ok(duration > 2.8 && duration < 3.3);
        const audioResponse = await context.request.get(new URL(await page.locator('#download-button').getAttribute('href'), base).href);
        assert.equal(audioResponse.status(),200);
        assert.equal(audioResponse.headers()['content-type'],'audio/mpeg');
        assert.ok((await audioResponse.body()).length > 1000);
        const videoURL = new URL(await page.locator('#video-download-button').getAttribute('href'), base).href;
        const videoResponse = await context.request.get(videoURL);
        assert.equal(videoResponse.status(),200);
        assert.deepEqual(await videoResponse.body(),fs.readFileSync(source));
        const downloadEvent = page.waitForEvent('download');
        await page.getByRole('link', {name:'下载原视频 MP4',exact:true}).click();
        assert.equal((await downloadEvent).suggestedFilename(),'完整视频.mp4');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));

        // Delay only delivery of the real 202: the server has already started processing.
        await page.getByRole('button', {name:'提取另一个视频',exact:true}).click();
        const accepted = deferred(), releaseResponse = deferred();
        await page.route('**/api/tasks/*/upload', async route => {
          let response;
          try {
            response = await route.fetch({postData:fs.readFileSync(source)});
            assert.equal(response.status(),202, await response.text());
          } catch (error) { accepted.reject(error); await route.abort().catch(() => {}); return; }
          accepted.resolve(new URL(route.request().url()).pathname.split('/')[3]);
          await releaseResponse.promise;
          await route.fulfill({response}).catch(() => {}); // Browser abort is expected after cancellation.
        });
        await page.locator('#video-file').setInputFiles(source);
        await page.getByRole('button', {name:'开始提取',exact:true}).click();
        const taskID = await accepted.promise;
        const cancellation = page.waitForResponse(response => response.url().endsWith(`/api/tasks/${taskID}/cancel`) && response.status() === 200);
        await page.getByRole('button', {name:'取消处理',exact:true}).click();
        assert.equal((await cancellation).status(),200);
        releaseResponse.resolve();
        await page.waitForFunction(() => document.getElementById('task-heading').textContent.includes('已取消'));
        const taskResponse = await context.request.get(`${base}/api/tasks/${taskID}`);
        assert.equal((await taskResponse.json()).state,'cancelled');
        assert.equal(fs.existsSync(path.join(temporary,'data','.soundpick-tasks',taskID)),false);
        assert.deepEqual(errors,[]);
        console.log(`PASS browser ${iteration+1}/${repeats}: upload, MP3 preview, exact MP4 download, lost-202 cancellation, filesystem cleanup`);
      } finally { await context.close(); }
    }
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) {
      const stopped = once(server,'exit');
      server.kill('SIGTERM');
      const forced = setTimeout(() => server.kill('SIGKILL'), 10000);
      try { await stopped; } finally { clearTimeout(forced); }
    }
    fs.rmSync(temporary,{recursive:true,force:true});
  }
}
main().catch(error => { console.error(error); process.exitCode=1; });
