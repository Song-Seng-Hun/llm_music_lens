$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$nodeCommand = Get-Command node -ErrorAction Stop
if (-not (Test-Path -LiteralPath 'node_modules/vinext/dist/cli.js')) {
    throw 'Dependencies missing. Follow README.md setup first.'
}
if (-not (Test-Path -LiteralPath 'dist/server/wrangler.json')) {
    & $nodeCommand.Source --import ./scripts/sites-env.mjs scripts/run-framework.mjs build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $nodeCommand.Source scripts/local-db.mjs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Music Lens: open http://127.0.0.1:5173/ when the Local address appears. Ctrl+C stops it.'
& $nodeCommand.Source --import ./scripts/sites-env.mjs scripts/run-framework.mjs dev --host 127.0.0.1
exit $LASTEXITCODE
