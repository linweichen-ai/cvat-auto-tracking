<#
.SYNOPSIS
    One-click sync: append changelog -> git add -> commit (timestamped) -> push.
    Idempotent: exits cleanly when there is nothing to commit. Safe to re-run.

.PARAMETER Message
    Optional short description. The script appends a local timestamp automatically.

.EXAMPLE
    .\sync-to-github.ps1 -Message "add NanoTrack function.yaml"
#>

param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Refresh PATH so git/gh work even right after winget install in a new shell.
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "[sync] git not found on PATH."
    exit 1
}

# Make sure we are inside a git repo.
if (-not (Test-Path ".git")) {
    Write-Host "[sync] git repo not initialized; running 'git init'."
    git init
    if ($LASTEXITCODE -ne 0) { Write-Error "[sync] git init failed."; exit 1 }
}

# Show non-ASCII paths (e.g. Chinese filenames) as UTF-8, not octal escapes.
git config core.quotepath false

# Snapshot pending changes BEFORE we touch CHANGELOG.
$pending = git status --porcelain
if (-not $pending) {
    Write-Host "[sync] Working tree clean. Nothing to do."
    exit 0
}

$now      = Get-Date
$stamp    = $now.ToString("yyyy-MM-dd HH:mm:ss zzz")
$isoDay   = $now.ToString("yyyy-MM-dd")
if ([string]::IsNullOrWhiteSpace($Message)) {
    $subject = "sync: update at $stamp"
} else {
    $subject = "$Message ($stamp)"
}

# Summarize changed files. Porcelain v1 format: XY<filename> where XY occupies
# columns 0-1, column 2 is a space, filename starts at column 3.
$summary = ($pending | ForEach-Object {
    $statusChar = $_.Substring(0,2)
    $file = $_.Substring(3).Trim('"')
    switch -Wildcard ($statusChar) {
        "A*" { "[add]    $file" }
        "M*" { "[mod]    $file" }
        "D*" { "[del]    $file" }
        "??" { "[new]    $file" }
        default { "      $file" }
    }
}) -join "`n"

# Append a changelog entry to docs/CHANGELOG.md (created if missing).
$changelogDir = "docs"
if (-not (Test-Path $changelogDir)) { New-Item -ItemType Directory -Path $changelogDir | Out-Null }
$changelog = Join-Path $changelogDir "CHANGELOG.md"
if (-not (Test-Path $changelog)) {
    @"
# Changelog

本文件记录每次同步到 GitHub 的提交内容与时间。由 ``sync-to-github.ps1`` 自动追加。

"@ | Set-Content -Path $changelog -Encoding UTF8
}

$entry = @"
## $stamp

- commit message: ``$subject``
- changed files:

``````
$summary
``````

"@
Add-Content -Path $changelog -Value $entry -Encoding UTF8

# Stage everything (including the new changelog entry).
git add -A
if ($LASTEXITCODE -ne 0) { Write-Error "[sync] git add failed."; exit 1 }

git commit -m "$subject"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[sync] git commit exited with $LASTEXITCODE (nothing to commit?)."
    exit 0
}

# Push if a remote exists; otherwise stop after local commit.
$remotes = git remote
if (-not $remotes) {
    Write-Host "[sync] No remote configured. Local commit kept only."
    exit 0
}

git push 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[sync] git push failed (exit $LASTEXITCODE). Local commit is preserved."
    exit $LASTEXITCODE
}

Write-Host "[sync] OK: $subject"
