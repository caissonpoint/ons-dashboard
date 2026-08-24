#Requires -Version 5.1
<#
.SYNOPSIS
    One-shot GitHub setup for the ONS balances dashboard (Windows / PowerShell).

.DESCRIPTION
    Creates the repository, pushes this folder, turns on GitHub Pages with
    Actions as the build source, starts the first build, and prints the URLs.

    Safe to re-run: against an existing repo it just commits and pushes.

.PARAMETER Name
    Repository name. Defaults to ons-balances.

.PARAMETER Private
    Create a private repo. Note that GitHub Pages will not serve a private
    repo unless you are on Enterprise Cloud.

.EXAMPLE
    .\setup_github.ps1
    .\setup_github.ps1 -Name ons-dashboard

.NOTES
    Requires git and the GitHub CLI (https://cli.github.com), with
    'gh auth login' already done.

    If PowerShell refuses to run this ("running scripts is disabled"), use:
        powershell -ExecutionPolicy Bypass -File .\setup_github.ps1

    Tested against Windows PowerShell 5.1 and PowerShell 7.
#>

[CmdletBinding()]
param(
    [string]$Name = "ons-balances_6",
    [switch]$Private
)

# git and gh write progress and status to stderr as a matter of course, even on
# success -- 'gh auth status' is the obvious example. On Windows PowerShell 5.1,
# stderr from a native command raises NativeCommandError when
# $ErrorActionPreference is 'Stop', whatever the exit code was. So leave the
# preference alone and judge success by $LASTEXITCODE, which is what actually
# means something. Every native call below goes through the helpers.
$ErrorActionPreference = "Continue"

# PowerShell 7.3+ can also be configured to turn a nonzero native exit into a
# terminating error. Same reasoning: switch it off. Absent on 5.1.
if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Write-Step { param($m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Warn { param($m) Write-Host "    $m" -ForegroundColor Yellow }
function Write-Ok   { param($m) Write-Host "    $m" -ForegroundColor Green }

# A plain one-line failure reads better than a PowerShell stack trace for what
# is almost always a missing tool or a missing login.
function Fail {
    param($m, $hint)
    Write-Host ""
    Write-Host "Stopped: $m" -ForegroundColor Red
    if ($hint) { Write-Host "         $hint" -ForegroundColor Yellow }
    Write-Host ""
    exit 1
}

# The three helpers below all wrap the call in try/catch as well as setting the
# preference. Belt and braces: if some host or profile still manages to turn
# stderr into a NativeCommandError, we swallow it and fall back to
# $LASTEXITCODE, which is set correctly either way.

# Run a native command silently; return only its exit code.
function Invoke-Quiet {
    param([string]$Exe, [string[]]$CmdArgs)
    $global:LASTEXITCODE = 0
    try { & $Exe @CmdArgs 2>$null 1>$null } catch { }
    return $LASTEXITCODE
}

# Run a native command and capture stdout; returns .Out and .Code.
function Invoke-Capture {
    param([string]$Exe, [string[]]$CmdArgs)
    $global:LASTEXITCODE = 0
    $text = ""
    try { $text = & $Exe @CmdArgs 2>$null } catch { }
    return [pscustomobject]@{
        Out  = (($text | Out-String) -replace "`r?`n$", "").Trim()
        Code = $LASTEXITCODE
    }
}

# Run a native command and let the user watch it (push, repo create).
function Invoke-Loud {
    param([string]$Exe, [string[]]$CmdArgs)
    $global:LASTEXITCODE = 0
    try { & $Exe @CmdArgs } catch { Write-Host $_.Exception.Message }
    return $LASTEXITCODE
}

function Assert-Command {
    param($Cmd, $Url)
    if (-not (Get-Command $Cmd -ErrorAction SilentlyContinue)) {
        Fail "$Cmd is not installed, or not on your PATH." "Get it from $Url"
    }
}

# ----------------------------------------------------------------- preflight
Assert-Command git "https://git-scm.com/download/win"
Assert-Command gh  "https://cli.github.com"

# Don't gate on `gh auth status`: it reports on every configured host and exits
# non-zero if ANY of them is broken - a stray/empty host entry is enough - even
# when github.com is signed in perfectly well. The real test is whether an
# authenticated API call works.
$who = Invoke-Capture gh @("api", "user", "--jq", ".login")
if ($who.Code -ne 0 -or -not $who.Out) {
    Fail "Could not reach GitHub as an authenticated user." `
         "Run 'gh auth login' (choose GitHub.com), then 'gh api user' to confirm."
}
$owner = $who.Out
Write-Step "Account: $owner"

if ($Private) {
    $visibility = "--private"
    Write-Warn "Private repo: GitHub Pages will not serve the site unless you are"
    Write-Warn "on Enterprise Cloud. See the README for the Cloudflare alternative."
} else {
    $visibility = "--public"
}

# ---------------------------------------------------------------------- git
if (-not (Test-Path ".git")) {
    Write-Step "Initialising repository"
    Invoke-Quiet git @("init", "-q")            | Out-Null
    Invoke-Quiet git @("branch", "-M", "main")  | Out-Null
}

# The Actions runner is Linux; commit LF regardless of this machine's setting.
Invoke-Quiet git @("config", "core.autocrlf", "false") | Out-Null

Invoke-Quiet git @("add", "-A") | Out-Null
if ((Invoke-Quiet git @("diff", "--staged", "--quiet")) -ne 0) {
    $msg = "ONS balances dashboard: pipeline, dashboard, daily refresh workflow"
    if ((Invoke-Quiet git @("commit", "-q", "-m", $msg)) -ne 0) {
        Fail "git commit failed." "Set your identity: git config --global user.email you@example.com"
    }
    Write-Ok "Committed"
} else {
    Write-Ok "Nothing new to commit"
}

# --------------------------------------------------------------------- repo
$remoteUrl = "https://github.com/$owner/$Name.git"

# Set origin ourselves rather than letting `gh repo create --source=.` do it:
# that flag fails outright if this folder already has an origin, which it does
# after any earlier run.
function Set-Origin {
    param([string]$Url)
    if ((Invoke-Quiet git @("remote", "get-url", "origin")) -eq 0) {
        Invoke-Quiet git @("remote", "set-url", "origin", $Url) | Out-Null
        Write-Ok "Pointed existing 'origin' at $Url"
    } else {
        Invoke-Quiet git @("remote", "add", "origin", $Url) | Out-Null
        Write-Ok "Added remote 'origin'"
    }
}

if ((Invoke-Quiet gh @("repo", "view", "$owner/$Name")) -eq 0) {
    Write-Step "Repo $owner/$Name already exists - reusing it"
} else {
    Write-Step "Creating $owner/$Name"
    $code = Invoke-Loud gh @("repo", "create", $Name, $visibility,
        "--description=Brazilian grid balances from ONS open data, refreshed daily")
    if ($code -ne 0) {
        Fail "Could not create the repository." `
             "Pick another name with -Name, or delete the existing one on GitHub."
    }
}
Set-Origin $remoteUrl

Write-Step "Pushing"
if ((Invoke-Loud git @("push", "-u", "origin", "main")) -ne 0) {
    Fail "Push failed." "See the git message above - usually auth or a non-empty remote."
}

# -------------------------------------------------------------------- Pages
Write-Step "Enabling Pages (source: GitHub Actions)"
$pagesPath = "repos/$owner/$Name/pages"
if ((Invoke-Quiet gh @("api", $pagesPath)) -eq 0) {
    if ((Invoke-Quiet gh @("api", "-X", "PUT", $pagesPath,
                           "-f", "build_type=workflow")) -eq 0) {
        Write-Ok "Updated existing Pages config"
    } else {
        Write-Warn "Could not update Pages. Settings -> Pages -> Source: GitHub Actions"
    }
} else {
    if ((Invoke-Quiet gh @("api", "-X", "POST", $pagesPath,
                           "-f", "build_type=workflow")) -eq 0) {
        Write-Ok "Pages enabled"
    } else {
        Write-Warn "Could not enable Pages automatically. Do it by hand:"
        Write-Warn "Settings -> Pages -> Build and deployment -> Source: GitHub Actions"
    }
}

# -------------------------------------------------------------- first build
Write-Step "Starting the first build (this one takes 10-20 minutes)"
Start-Sleep -Seconds 3
if ((Invoke-Quiet gh @("workflow", "run", "refresh.yml",
                       "--repo", "$owner/$Name")) -eq 0) {
    Write-Ok "Started"
} else {
    Write-Warn "Start it by hand: Actions tab -> Refresh ONS dashboard -> Run workflow"
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "  Repo      https://github.com/$owner/$Name"
Write-Host "  Actions   https://github.com/$owner/$Name/actions"
Write-Host "  Site      https://$owner.github.io/$Name/     (live once the first run finishes)"
Write-Host ""
Write-Host "Watch the first run with:  gh run watch --repo $owner/$Name"
