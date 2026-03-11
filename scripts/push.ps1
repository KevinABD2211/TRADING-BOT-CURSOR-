# Push all changes to GitHub (run from repo root)
param([Parameter(Mandatory=$true)] [string] $Message)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
git add -A
$status = git status --short
if (-not $status) { Write-Host "Nothing to commit."; exit 0 }
git commit -m $Message
git push origin main
