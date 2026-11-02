// Intent: actual browser Whisper download/runtime/word timestamp interoperability.
// Input is the short jfk.wav speech fixture in the official Transformers.js documentation dataset; not music accuracy.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const out = path.resolve('evidence/precision'), requests = [], errors = [];
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_EXECUTABLE, headless: true });
  const context = await browser.newContext({ acceptDownloads: true }); const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  context.on('request', r => { if (/^https?:/.test(r.url()) && !r.url().startsWith('http://127.0.0.1:5173/')) requests.push({ method: r.method(), url: r.url() }); });
  try {
    await page.goto('http://127.0.0.1:5173/'); await page.waitForLoadState('networkidle');
    await page.getByTestId('audio-file').setInputFiles(path.join(out, 'speech-fixture.wav'));
    await page.getByLabel('단어 전사', { exact: true }).selectOption('base'); await page.getByLabel('가사 언어', { exact: true }).selectOption('english');
    await page.getByRole('button', { name: '정밀 분석 시작', exact: true }).click();
    await page.getByRole('region', { name: '분석 결과' }).waitFor({ timeout: 600000 });
    const d = page.waitForEvent('download'); await page.getByRole('button', { name: 'JSON', exact: true }).click(); await (await d).saveAs(path.join(out, 'speech-report.json'));
    const report = JSON.parse(fs.readFileSync(path.join(out, 'speech-report.json'), 'utf8'));
    assert.equal(report.detail.lyrics.status, 'complete', JSON.stringify(report.detail.lyrics.warnings));
    assert.ok(report.detail.lyrics.words.length > 5); assert.ok(report.detail.lyrics.words.every(w => w.end > w.start && w.end <= report.signal.duration));
    assert.deepEqual(errors, []); assert.ok(requests.length > 0); assert.ok(requests.every(r => r.method === 'GET' || r.method === 'HEAD'));
    console.log(JSON.stringify({ words: report.detail.lyrics.words, model: report.detail.lyrics.model, externalStaticRequests: requests.length, errors }));
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'lyrics-network.json'), JSON.stringify({ requests, errors }, null, 2)); }
})().catch(e => { console.error(e); process.exitCode = 1; });
