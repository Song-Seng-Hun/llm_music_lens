// Integration intent: browser decoding/worker/playback/storage can fail while
// pure DSP tests pass. Uses generated audio and local Sites identity only.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const runtimeRequire = process.env.PLAYWRIGHT_MODULE ? createRequire(path.join(path.resolve(process.env.PLAYWRIGHT_MODULE), 'package.json')) : require;
const { chromium } = process.env.PLAYWRIGHT_MODULE ? runtimeRequire(process.env.PLAYWRIGHT_MODULE) : require('playwright');
const root = path.resolve(__dirname, '..');
const evidence = path.join(root, 'evidence', 'keyless'); fs.mkdirSync(evidence, { recursive: true });
const base = 'http://127.0.0.1:5173';
const checks = [], issues = [], savedIds = [], externalRequests = [];
function wav(duration=12) {
  const rate=16000, bytes=Buffer.alloc(44+rate*duration*2);
  bytes.write('RIFF'); bytes.writeUInt32LE(bytes.length-8,4); bytes.write('WAVEfmt ',8); bytes.writeUInt32LE(16,16); bytes.writeUInt16LE(1,20); bytes.writeUInt16LE(1,22); bytes.writeUInt32LE(rate,24); bytes.writeUInt32LE(rate*2,28); bytes.writeUInt16LE(2,32); bytes.writeUInt16LE(16,34); bytes.write('data',36); bytes.writeUInt32LE(bytes.length-44,40);
  for(let i=0;i<rate*duration;i++){const beat=i%(rate/2), t=i/rate;const click=beat<320?Math.sin(beat*.38)*Math.exp(-beat/60)*.7:0;const tone=.08*Math.sin(2*Math.PI*440*t);bytes.writeInt16LE(Math.round(Math.max(-1,Math.min(1,click+tone))*32767),44+i*2);}
  return bytes;
}
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
  const context=await browser.newContext({viewport:{width:1440,height:1050},acceptDownloads:true});
  const page=await context.newPage(); page.on('pageerror', e=>issues.push(e.message)); page.on('request', r=>{if(/^https?:/.test(r.url()) && new URL(r.url()).origin!==base) externalRequests.push(r.url());});
  try {
    const response=await page.goto(base); assert.equal(response.status(),200); await page.waitForLoadState('networkidle');
    await page.getByRole('heading',{name:'좋은 음악의 이유를 듣다.'}).waitFor();
    await page.screenshot({path:path.join(evidence,'home-desktop.png'),fullPage:true}); checks.push('desktop home loaded');
    const anon=await context.request.get(base+'/api/reports');assert.equal(anon.status(),401);
    const spoof=await context.request.get(base+'/api/reports',{headers:{'oai-authenticated-user-id':'not-a-user','oai-authenticated-user-email':'nobody@example.test'}});assert.equal(spoof.status(),401);checks.push('anonymous and caller-supplied identity rejected');
    const cross=await context.request.post(base+'/api/reports',{headers:{Origin:'https://different.example'},data:{}});assert.equal(cross.status(),403);checks.push('cross-origin write rejected');
    await page.getByRole('button',{name:'YouTube 링크'}).click(); await page.getByRole('button',{name:'보내주신 예시 곡 넣기'}).click();
    assert.equal(await page.locator('#youtube-url').inputValue(),'https://www.youtube.com/watch?v=ALxVpirKqRQ');
    await page.getByRole('button',{name:'참고 주소 확인',exact:true}).click(); assert.equal(await page.getByRole('link',{name:'YouTube에서 영상 열기'}).getAttribute('href'),'https://www.youtube.com/watch?v=ALxVpirKqRQ'); assert.equal(await page.getByRole('region',{name:'분석 결과'}).count(),0); checks.push('YouTube reference does not fabricate analysis');
    await page.getByRole('button',{name:'음원 파일',exact:true}).click();
    await page.getByLabel('분석 깊이', {exact:true}).selectOption('quick');
    await page.getByTestId('audio-file').setInputFiles({name:'Fixture — 120 BPM + A4.wav',mimeType:'audio/wav',buffer:wav()});
    await page.getByRole('button',{name:'파일 분석',exact:true}).click();
    await page.getByRole('region',{name:'분석 결과'}).waitFor({timeout:60000});
    const bpm=await page.getByTestId('bpm').innerText();assert.match(bpm,/120/);checks.push('actual browser decode → worker → 120 BPM report');
    await page.getByRole('tab',{name:'음높이 · 화음'}).click();await page.getByRole('img',{name:'시간에 따른 추정 기본 주파수'}).waitFor();
    await page.getByRole('slider',{name:'재생 위치'}).fill('3');await page.waitForTimeout(250);assert.ok(await page.locator('audio').evaluate(a=>a.currentTime)>=3);checks.push('pitch panel and playback seek');
    const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'JSON',exact:true}).click();const download=await downloadPromise;await download.saveAs(path.join(evidence,'browser-report.json'));const exported=JSON.parse(fs.readFileSync(path.join(evidence,'browser-report.json'),'utf8'));assert.equal(exported.schema,'music-lens/1');assert.equal(exported.provenance.mode,'local-signal');checks.push('JSON export preserves actual report');
    const handoffPromise=page.waitForEvent('download');await page.getByRole('button',{name:'AI 팀 전달문',exact:true}).click();const handoffDownload=await handoffPromise;await handoffDownload.saveAs(path.join(evidence,'browser-handoff.md'));const handoff=fs.readFileSync(path.join(evidence,'browser-handoff.md'),'utf8');assert.ok(handoff.includes(exported.source.sha256));assert.match(handoff,/cannot access and listen to the original audio/);checks.push('team handoff exports source and uncertainty without a model call');
    await page.screenshot({path:path.join(evidence,'analysis-desktop.png'),fullPage:true});
    const signin=await context.request.get(base+'/signin-with-chatgpt?return_to=/');assert.equal(signin.status(),200);
    const status=await (await context.request.get(base+'/api/status')).json();assert.equal(status.user.name,'Seedy');
    const stored=await context.request.post(base+'/api/reports',{data:exported});assert.equal(stored.status(),201);const storedReport=(await stored.json()).report;savedIds.push(storedReport.id);assert.equal(storedReport.provenance.mode,'imported');
    const listed=await (await context.request.get(base+'/api/reports')).json();assert.ok(listed.reports.some(x=>x.id===storedReport.id));
    const fetched=await context.request.get(base+'/api/reports/'+storedReport.id);assert.equal(fetched.status(),200);checks.push('D1 save/list/read with explicit imported provenance');
    const rpc=async(method,params={})=>(await context.request.post(base+'/mcp',{data:{jsonrpc:'2.0',id:1,method,params}})).json();
    const init=await rpc('initialize',{protocolVersion:'2025-06-18',capabilities:{},clientInfo:{name:'local-test',version:'1'}});assert.equal(init.result.serverInfo.name,'music-lens');
    const tools=await rpc('tools/list');assert.equal(tools.result.tools.length,3);assert.ok(tools.result.tools.every(t=>t.annotations.readOnlyHint));
    const report=await rpc('tools/call',{name:'get_music_report',arguments:{id:storedReport.id}});assert.equal(report.result.isError,false);
    const tooLong=await rpc('tools/call',{name:'get_music_timeline',arguments:{id:storedReport.id,start:0,end:121}});assert.equal(tooLong.result.isError,true);checks.push('MCP initialize/list/read/bounded timeline through real route');
    const removed=await context.request.post(base+'/api/analyze',{data:{}});assert.equal(removed.status(),404); const removedTool=await rpc('tools/call',{name:'analyze_youtube',arguments:{}});assert.equal(removedTool.result.isError,true);assert.equal(await page.locator('#api-key').count(),0);checks.push('model-calling route/tool and key input removed');
    await page.reload();await page.waitForLoadState('networkidle');await page.getByRole('button',{name:/Fixture — 120 BPM/}).click();await page.getByRole('region',{name:'분석 결과'}).waitFor();checks.push('saved report reopened through UI');
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(evidence,'analysis-mobile.png'),fullPage:true});
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);assert.equal(overflow,false);checks.push('390px mobile layout has no horizontal overflow');
    await page.setViewportSize({width:1440,height:1050});
    await page.getByRole('button',{name:'새로운 분석',exact:true}).click();
    await page.getByLabel('분석 깊이', {exact:true}).selectOption('quick');
    await page.getByTestId('audio-file').setInputFiles({name:'Fixture cancel.wav',mimeType:'audio/wav',buffer:wav(120)});
    await page.getByRole('button',{name:'파일 분석',exact:true}).click();await page.getByRole('button',{name:'취소',exact:true}).click();
    await page.getByRole('status').filter({hasText:'로컬 분석을 취소'}).waitFor();await page.waitForTimeout(1800);assert.equal(await page.getByRole('region',{name:'분석 결과'}).count(),0);checks.push('cancelled decode/worker cannot publish a late report');
    assert.deepEqual(issues,[]); assert.deepEqual(externalRequests,[]); checks.push("local analysis and exports make no off-origin requests");
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(evidence,'browser-smoke.json'),JSON.stringify({checks,issues,savedIds,externalRequests},null,2));
  }
  console.log(JSON.stringify({checks,issues,savedIds,externalRequests},null,2));
})().catch(e=>{console.error(e);process.exitCode=1});
