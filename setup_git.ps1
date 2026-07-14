# setup_git.ps1
# Interactive one-time setup: connect this project to your personal GitHub.
# Run from this folder:
#   powershell -ExecutionPolicy Bypass -File setup_git.ps1

$ErrorActionPreference = "Stop"
$ProjectSource = "C:\Users\Smit Mehta\OneDrive - SalesOptimize\Desktop\Data\Code-r\NEwCOde\ClaudeCode\RuFlo\second-brain"

function Write-Header($text) {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Cyan
    Write-Host $text -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor Cyan
}

function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Yellow
}

function Write-OK($text)   { Write-Host "    OK: $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    WARN: $text" -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "    ERROR: $text" -ForegroundColor Red }

function Confirm($prompt, $default = "y") {
    if ($default -eq "y") { $hint = "[Y/n]" } else { $hint = "[y/N]" }
    $answer = Read-Host "$prompt $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { $answer = $default }
    return $answer -match '^[yY]'
}

function Ask($prompt, $default = "") {
    if ($default) { $hint = " [$default]" } else { $hint = "" }
    $answer = Read-Host "$prompt$hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer
}

# ============================================================================
Write-Header "GIT SETUP -- connect this project to your personal GitHub"
Write-Host ""
Write-Host "Source project:"
Write-Host "  $ProjectSource"
Write-Host ""
Write-Host "This script asks questions, then does the work."
Write-Host "Ctrl+C cancels at any time. Nothing destructive runs without a y/n."
Write-Host ""
if (-not (Confirm "Ready to begin?")) { Write-Host "Cancelled."; exit }

# ============================================================================
Write-Step "Step 1 of 7 -- checking prerequisites"

$gitOk = $false
try {
    $gitVersion = (& git --version) 2>$null
    if ($gitVersion) { Write-OK "git installed: $gitVersion"; $gitOk = $true }
} catch { }
if (-not $gitOk) {
    Write-Err "git is not on PATH. Install from https://git-scm.com/download/win and re-run."
    exit 1
}

$ghInstalled = $false
try {
    $ghVersionLine = (& gh --version 2>$null) | Select-Object -First 1
    if ($ghVersionLine) {
        Write-OK "gh CLI installed: $ghVersionLine"
        $ghInstalled = $true
    }
} catch { }

if (-not $ghInstalled) {
    Write-Warn "gh CLI not found (recommended for easy auth + repo creation)"
    if (Confirm "Install gh CLI now via winget?") {
        winget install --id GitHub.cli --silent --accept-source-agreements --accept-package-agreements
        Write-Host ""
        Write-Warn "Close this terminal and re-run the script so gh is on PATH."
        exit 0
    } else {
        Write-Warn "Continuing without gh. You will create the GitHub repo manually later."
    }
}

# ============================================================================
Write-Step "Step 2 of 7 -- choose project location"
Write-Host ""
Write-Host "Your project lives in OneDrive. OneDrive sync has already corrupted"
Write-Host "the local .git folder once today. Strongly recommended: copy the"
Write-Host "project to a path outside OneDrive (e.g. C:\Code\second-brain)."
Write-Host ""

$moveOut = Confirm "Copy the project to a non-OneDrive path?"
if ($moveOut) {
    $destDefault = "C:\Code\second-brain"
    $dest = Ask "Destination path" $destDefault
    if (Test-Path $dest) {
        Write-Warn "$dest already exists."
        if (-not (Confirm "Overwrite contents there?" "n")) {
            Write-Err "Cannot continue. Choose a different path and re-run."
            exit 1
        }
    } else {
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
    }
    Write-Host ""
    Write-Host "    Copying files (excluding broken .git)..."
    robocopy $ProjectSource $dest /E /XD ".git" "__pycache__" ".pytest_cache" /XF "*.pyc" /NFL /NDL /NJH /NJS /NP /NS | Out-Null
    if ($LASTEXITCODE -gt 7) {
        Write-Err "robocopy failed with code $LASTEXITCODE. Check the destination path."
        exit 1
    }
    Write-OK "Copied to $dest"
    Set-Location $dest
    $ProjectRoot = $dest
} else {
    Write-Warn "Staying in OneDrive. Expect .git to corrupt periodically."
    Set-Location $ProjectSource
    $ProjectRoot = $ProjectSource
}

# ============================================================================
Write-Step "Step 3 of 7 -- your git identity"
Write-Host ""

$existingName = (& git config --global user.name) 2>$null
$existingEmail = (& git config --global user.email) 2>$null

$keepIdentity = $false
if ($existingName -and $existingEmail) {
    Write-Host "    Current global git identity:"
    Write-Host "      name:  $existingName"
    Write-Host "      email: $existingEmail"
    Write-Host ""
    $keepIdentity = Confirm "Keep this identity for this project?"
}

if (-not $keepIdentity) {
    $name = Ask "Your name (as you want it on commits)" $existingName
    $email = Ask "Your GitHub email" $existingEmail
    if (-not $name -or -not $email) {
        Write-Err "Name and email both required."
        exit 1
    }
    & git config --global user.name $name
    & git config --global user.email $email
    Write-OK "Set global identity: $name <$email>"
} else {
    Write-OK "Keeping $existingName <$existingEmail>"
}

# ============================================================================
Write-Step "Step 4 of 7 -- GitHub authentication"

$ghAuthed = $false
if ($ghInstalled) {
    & gh auth status 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Already authenticated with gh"
        $ghAuthed = $true
    } else {
        Write-Host ""
        Write-Host "    Opening the gh auth login flow."
        Write-Host "    Pick: GitHub.com -> HTTPS -> Login with a web browser"
        Write-Host ""
        if (Confirm "Run 'gh auth login' now?") {
            & gh auth login
            if ($LASTEXITCODE -ne 0) {
                Write-Err "gh auth login failed. Re-run the script after sorting it."
                exit 1
            }
            Write-OK "Authenticated"
            $ghAuthed = $true
        } else {
            Write-Warn "Skipping. You will need to push manually with a token later."
        }
    }
} else {
    Write-Warn "No gh CLI. You will need a Personal Access Token or SSH key for push."
}

# ============================================================================
Write-Step "Step 5 of 7 -- initialize the repo"

if (Test-Path ".git") {
    Write-Host ""
    Write-Warn ".git already exists in $ProjectRoot"
    Write-Host "    (Source had a corrupt .git which we excluded during copy. If a"
    Write-Host "     .git already exists here, it is likely safe to remove and re-init.)"
    Write-Host ""
    if (Confirm "Remove existing .git and re-init fresh?" "n") {
        Remove-Item -Recurse -Force ".git"
        Write-OK "Removed .git"
    } else {
        Write-Err "Cannot continue with a potentially corrupt .git. Exiting."
        exit 1
    }
}

& git init -b main | Out-Null
Write-OK "Initialized fresh repo on branch 'main'"

# ============================================================================
Write-Step "Step 6 of 7 -- write .gitignore"

Write-Host ""
Write-Host "    Some data files contain your actual notes or secrets."
Write-Host "    Pick what to ignore (y to ignore, n to commit):"
Write-Host ""

$ignoreList = New-Object System.Collections.Generic.List[string]
$ignoreList.Add("# secrets")
$ignoreList.Add(".env")
$ignoreList.Add(".env.*")
$ignoreList.Add("!.env.example")
$ignoreList.Add("notes.txt")
$ignoreList.Add("")
$ignoreList.Add("# local databases / agent artifacts")
$ignoreList.Add("*.db")
$ignoreList.Add("*.db-journal")
$ignoreList.Add("*.db-wal")
$ignoreList.Add("*.db-shm")
$ignoreList.Add(".swarm/")
$ignoreList.Add(".claude-flow/")
$ignoreList.Add("")
$ignoreList.Add("# python")
$ignoreList.Add("__pycache__/")
$ignoreList.Add("*.pyc")
$ignoreList.Add("*.pyo")
$ignoreList.Add("*.pyd")
$ignoreList.Add(".pytest_cache/")
$ignoreList.Add(".mypy_cache/")
$ignoreList.Add(".ruff_cache/")
$ignoreList.Add("*.egg-info/")
$ignoreList.Add(".venv/")
$ignoreList.Add("venv/")
$ignoreList.Add("")
$ignoreList.Add("# IDE")
$ignoreList.Add(".vscode/")
$ignoreList.Add(".idea/")
$ignoreList.Add("*.swp")
$ignoreList.Add("")
$ignoreList.Add("# OS")
$ignoreList.Add("Thumbs.db")
$ignoreList.Add(".DS_Store")
$ignoreList.Add("")
$ignoreList.Add("# data (chosen during setup)")

if (Confirm "    Ignore data/secondbrain.db (your SQLite notes DB)?" "y") {
    $ignoreList.Add("data/*.db")
    $ignoreList.Add("data/*.db-journal")
    $ignoreList.Add("data/*.db-wal")
    $ignoreList.Add("data/*.db-shm")
}

if (Confirm "    Ignore docs/notes.json (your notes export)?" "y") {
    $ignoreList.Add("docs/notes.json")
}

if (Confirm "    Ignore data/custom_buckets.json (your custom buckets)?" "n") {
    $ignoreList.Add("data/custom_buckets.json")
}

if (Confirm "    Ignore data/notion_reconcile_state.json (sync state)?" "y") {
    $ignoreList.Add("data/notion_reconcile_state.json")
}

# Write with UTF-8 BOM so any tool reads it correctly
$gitignorePath = Join-Path $ProjectRoot ".gitignore"
$content = ($ignoreList -join "`r`n") + "`r`n"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($gitignorePath, $content, $utf8Bom)
Write-OK "Wrote .gitignore"

# ============================================================================
Write-Step "Step 7 of 7 -- create remote + first commit + push"

$repoName = Ask "GitHub repo name" "second-brain"
$visibility = Ask "Visibility (private/public)" "private"
if ($visibility -ne "public") { $visibility = "private" }

Write-Host ""
Write-Host "    Staging all files (respecting .gitignore)..."
& git add -A
$stagedFiles = & git diff --cached --name-only
$stagedCount = ($stagedFiles | Measure-Object).Count
Write-OK "$stagedCount files staged"

$envStaged = $stagedFiles | Where-Object { $_ -eq ".env" -or ($_ -like ".env.*" -and $_ -ne ".env.example") -or $_ -eq "notes.txt" -or $_ -like "*.db" }
if ($envStaged) {
    Write-Err "SAFETY CHECK FAILED: $($envStaged -join ', ') would be committed. Aborting."
    Write-Host "    Fix .gitignore and re-run the script."
    exit 1
}

# Content scan: abort if any staged file contains an API-key-shaped string.
# Patterns: Google (AIza...), Groq (gsk_...), Notion (secret_/ntn_...),
# Anthropic/OpenAI (sk-...), Telegram bot token (digits:AA...).
$keyPatterns = "AIza[0-9A-Za-z_\-]{30,}|gsk_[0-9A-Za-z]{20,}|secret_[0-9A-Za-z]{20,}|ntn_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z_\-]{20,}|[0-9]{8,10}:AA[0-9A-Za-z_\-]{30,}"
$leaks = & git grep --cached -I -l -E $keyPatterns 2>$null
if ($leaks) {
    Write-Err "SAFETY CHECK FAILED: staged file(s) contain API-key-shaped strings:"
    $leaks | ForEach-Object { Write-Host "      $_" }
    Write-Host "    Remove the secrets (or gitignore the files) and re-run the script."
    exit 1
}
Write-OK "Safety check passed (no .env/notes.txt/db staged, no key-shaped strings in staged content)"

$commitMsgLines = @(
    "Initial commit: Mind Palace / second-brain",
    "",
    "Snapshot of 2026-05-23 work:",
    "- Buckets as primary categorization axis (8 canonical + user-creatable custom)",
    "- Multi-bucket model: notes can belong to multiple buckets",
    "- New dashboard pages: bucket, review, atlas, mindmap (+ index landing)",
    "- Shared theme.css with Warm Dim Dark palette + WCAG AA contrast",
    "- Telegram-bot ingestion -> Jina extraction -> Gemini/Groq categorization -> Notion + SQLite",
    "- Bidirectional Notion archive sync (5-min poll)",
    "- notion-client 3.x migration (databases.query -> data_sources.query)"
)
$commitMessage = $commitMsgLines -join "`n"
& git commit -m $commitMessage | Out-Null
Write-OK "First commit created"

if ($ghAuthed) {
    Write-Host ""
    Write-Host ("    Creating " + $visibility + " repo '" + $repoName + "' on GitHub...")
    $vFlag = "--$visibility"
    & gh repo create $repoName $vFlag "--source=." "--remote=origin" "--push"
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Repo created and pushed."
        $url = (& gh repo view --json url -q .url) 2>$null
        if ($url) {
            Write-Host ""
            Write-Host ("    URL: " + $url) -ForegroundColor Cyan
        }
    } else {
        Write-Err "gh repo create failed. Run manually:"
        Write-Host ("    gh repo create " + $repoName + " " + $vFlag + " --source=. --remote=origin --push")
    }
} else {
    Write-Host ""
    Write-Warn "No gh auth -- finish manually:"
    Write-Host ("    1. Create the repo at https://github.com/new (name: " + $repoName + ", " + $visibility + ")")
    Write-Host ("    2. git remote add origin https://github.com/<your-username>/" + $repoName + ".git")
    Write-Host "    3. git push -u origin main"
}

# ============================================================================
Write-Header "DONE"
Write-Host ""
Write-Host "Project root: $ProjectRoot"
Write-Host ""
if ($moveOut) {
    Write-Host "Original (corrupt-git, OneDrive) source is untouched at:"
    Write-Host "  $ProjectSource"
    Write-Host ""
    Write-Host "You can delete it once the new location works for you."
}
Write-Host ""
Write-Host "Next time you make changes:"
Write-Host "  cd $ProjectRoot"
Write-Host "  git add -A"
Write-Host "  git commit -m 'what changed'"
Write-Host "  git push"
Write-Host ""
