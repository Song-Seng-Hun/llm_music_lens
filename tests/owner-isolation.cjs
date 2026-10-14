// Actual local D1 queries must exclude another owner, even for a known report ID.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { DatabaseSync } = require('node:sqlite');
const root = path.resolve(__dirname, '..');
const evidenceDir = path.join(root, 'evidence', 'keyless');
const folder = path.join(root, '.wrangler', 'state', 'v3', 'd1', 'miniflare-D1DatabaseObject');
const candidates = fs.readdirSync(folder).filter(p => /^[a-f0-9]{64}\.sqlite$/.test(p));
assert.equal(candidates.length, 1, 'Use only this project local D1 database');
const db = new DatabaseSync(path.join(folder, candidates[0])); db.exec('PRAGMA busy_timeout = 5000');
const fixture = JSON.parse(fs.readFileSync(path.join(evidenceDir, 'browser-report.json'), 'utf8'));
fixture.id = crypto.randomUUID(); fixture.source.title = 'Foreign owner fixture';
db.prepare('INSERT INTO reports (id, owner_id, title, source_type, created_at, body) VALUES (?, ?, ?, ?, ?, ?)').run(fixture.id, 'isolation_fixture_other_user', fixture.source.title, 'synthetic', fixture.createdAt, JSON.stringify(fixture));
const base = 'http://127.0.0.1:5173';
const headers = {Cookie:'__sites_local_auth=1'};
(async()=>{
  try {
    const list = await (await fetch(base+'/api/reports',{headers})).json();assert.ok(!list.reports.some(r=>r.id===fixture.id));
    assert.equal((await fetch(base+'/api/reports/'+fixture.id,{headers})).status,404);
    const mcp=await (await fetch(base+'/mcp',{method:'POST',headers:{...headers,'content-type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'get_music_report',arguments:{id:fixture.id}}})})).json();
    assert.equal(mcp.result.isError,true);assert.ok(!JSON.stringify(mcp).includes('Foreign owner fixture'));
    const result={listExcludesForeign:true,getForeign404:true,mcpForeignError:true,providerCalls:0};
    fs.writeFileSync(path.join(evidenceDir,'owner-isolation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  } finally {
    db.prepare('DELETE FROM reports WHERE owner_id = ? AND id = ?').run('isolation_fixture_other_user',fixture.id);
    // Clean only the exact synthetic records saved by this test session.
    const evidence=JSON.parse(fs.readFileSync(path.join(evidenceDir,'browser-smoke.json'),'utf8'));
    for(const id of evidence.savedIds) db.prepare('DELETE FROM reports WHERE owner_id = ? AND id = ? AND title = ?').run('local_seedy',id,'Fixture — 120 BPM + A4');
    db.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1});
