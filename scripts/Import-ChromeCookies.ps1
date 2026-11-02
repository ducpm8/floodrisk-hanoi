<#
.SYNOPSIS
    Import Facebook/Instagram cookies (EditThisCookie export) into crawler_sessions DB.

.DESCRIPTION
    HOW TO EXPORT COOKIES FROM CHROME:
      1. Install "EditThisCookie" extension in Chrome
      2. Go to facebook.com (make sure you are logged in)
      3. Click the EditThisCookie icon -> click the Export button (clipboard icon)
      4. Paste into a file, e.g. C:\firstproject\fb_cookies.json
      5. Run this script

    Install packages (one-time):
        pip install psycopg2-binary

.PARAMETER File
    Path to the exported cookies JSON file (relative to project root or absolute)

.PARAMETER Source
    facebook | instagram

.PARAMETER DbUrl
    Override DATABASE_URL from .env

.EXAMPLE
    .\Import-ChromeCookies.ps1 -File fb_cookies.json -Source facebook
    .\Import-ChromeCookies.ps1 -File C:\Downloads\ig_cookies.json -Source instagram

.NOTES
    Jira: FIR-49
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$File,

    [Parameter(Mandatory)]
    [ValidateSet("facebook", "instagram")]
    [string]$Source,

    [string]$DbUrl = ""
)

$ErrorActionPreference = "Stop"

$scriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyScript    = Join-Path $scriptDir "import_chrome_cookies.py"

$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    Write-Host "[FAIL] Python not found in PATH." -ForegroundColor Red
    exit 1
}
$pythonExe = $pyCmd.Source

$pyArgs = [System.Collections.ArrayList]@($pyScript, "--file", $File, "--source", $Source)
if ($DbUrl) {
    $null = $pyArgs.Add("--db-url")
    $null = $pyArgs.Add($DbUrl)
}

Write-Host ""
Write-Host "  Importing $Source cookies from: $File" -ForegroundColor Cyan
Write-Host ""
& $pythonExe @pyArgs
exit $LASTEXITCODE
