# One-time: give the DEMEX server read-only access to the company inboxes (Fly secret DEMEX_MAIL_ACCOUNTS).
# Uses app passwords, never normal passwords. Run:  powershell -NoProfile -ExecutionPolicy Bypass -File server\set_mail_accounts.ps1
$ErrorActionPreference = 'Stop'
$fly = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\flyctl.exe'

function Read-Secret($prompt) {
  $s = Read-Host -AsSecureString -Prompt $prompt
  $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($p)).Trim() -replace '\s', '' } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p) }
}

$list = @()
function Add-Account($org, $email, $hostName, $label) {
  Write-Host ""
  Write-Host "$label  ($email)" -ForegroundColor Cyan
  $pw = Read-Secret '  App password (hidden; press Enter to skip this inbox)'
  if (-not $pw) { Write-Host '  skipped'; return }
  $script:list += @{ org = $org; user = $email; app_password = $pw; host = $hostName }
  Write-Host '  added' -ForegroundColor Green
}

Write-Host "Inboxes for the DEMEX server (read-only)" -ForegroundColor Cyan
Write-Host "If estimates@ or admin@ are aliases that already deliver into shawn@, skip them: they have no password of their own."
Add-Account 'ddd' 'shawn@disassembledynamics.com' 'imap.gmail.com' 'Disassemble Dynamics -> DEMEX'
Add-Account 'ddd' 'estimates@disassembledynamics.com' 'imap.gmail.com' 'Disassemble Dynamics -> DEMEX'
Add-Account 'ddd' 'admin@disassembledynamics.com' 'imap.gmail.com' 'Disassemble Dynamics -> DEMEX'
Write-Host ""
$y = Read-Host "Wayne's Carpet Plus Yahoo address for PAVORA (press Enter to skip)"
if ($y) { Add-Account 'wcp' $y.Trim() 'imap.mail.yahoo.com' "Wayne's Carpet Plus -> PAVORA" }

if (-not $list.Count) { Write-Host "No inboxes entered; nothing changed."; exit 0 }
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
@('DEMEX_MAIL_ACCOUNTS=' + (ConvertTo-Json -InputObject @($list) -Compress)) | & $fly secrets import --app demex-ddd --stage
if ($LASTEXITCODE -ne 0) { throw "flyctl could not set the secret." }
Write-Host ""
Write-Host "Done. $($list.Count) inbox(es) saved; they take effect on the next deploy." -ForegroundColor Green
