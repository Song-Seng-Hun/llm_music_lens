import './sites-env.mjs';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';

if (!existsSync('dist/server/wrangler.json')) throw new Error('Run the project build first.');
const journal = JSON.parse(readFileSync('drizzle/meta/_journal.json', 'utf8'));
const args = ['--import', './scripts/sites-env.mjs', './node_modules/wrangler/bin/wrangler.js', 'd1', 'execute', 'DB', '--local', '--config', 'dist/server/wrangler.json', '--persist-to', '.wrangler/state'];
for (const entry of journal.entries) {
  const source = `drizzle/${entry.tag}.sql`;
  // This initial schema may already exist after restarting the local program.
  // Future migrations need normal ordered migration tracking, not this initializer.
  if (journal.entries.length !== 1 || entry.idx !== 0) throw new Error('Use the documented migration procedure for a later schema version.');
  const sql = readFileSync(source, 'utf8').replaceAll('CREATE TABLE `', 'CREATE TABLE IF NOT EXISTS `').replaceAll('CREATE INDEX `', 'CREATE INDEX IF NOT EXISTS `');
  const result = spawnSync(process.execPath, [...args, '--command', sql], { stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
