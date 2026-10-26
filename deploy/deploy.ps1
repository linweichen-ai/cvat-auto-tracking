<#
.SYNOPSIS
    Deploy a CVAT serverless tracker function to the remote Nuclio host.
    This is the Windows-side entrypoint: it uploads the function dir over SCP
    and then runs deploy.sh on the server over SSH.

.PARAMETER ServerHost
    Remote CVAT/Nuclio host (e.g. 172.16.88.68).

.PARAMETER SshUser
    SSH login user on the remote host.

.PARAMETER FunctionDir
    Local path to the tracker function directory (contains function.yaml + main.py).

.PARAMETER RemoteWorkDir
    Where to stage the function on the remote host. Default: ~/cvat-auto-tracking/deploy.

.PARAMETER SshKey
    Optional path to a private key. If omitted, ssh-agent / default keys / password prompt are used.
#>

param(
    [Parameter(Mandatory = $true)][string]$ServerHost,
    [Parameter(Mandatory = $true)][string]$SshUser,
    [Parameter(Mandatory = $true)][string]$FunctionDir,
    [string]$RemoteWorkDir = "~/cvat-auto-tracking/deploy",
    [string]$SshKey = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $FunctionDir)) {
    Write-Error "[deploy] FunctionDir not found: $FunctionDir"
    exit 1
}
if (-not (Test-Path (Join-Path $FunctionDir "function.yaml"))) {
    Write-Warning "[deploy] function.yaml not found under $FunctionDir - continue anyway?"
}

$FunctionDir = (Resolve-Path $FunctionDir).Path
$funcName = Split-Path $FunctionDir -Leaf
$sshTarget = "$SshUser@$ServerHost"

function Invoke-Ssh {
    param([string]$Cmd)
    $sshArgs = @()
    if ($SshKey) { $sshArgs += @("-i", $SshKey) }
    $sshArgs += @("-o", "StrictHostKeyChecking=accept-new", $sshTarget, $Cmd)
    & ssh @sshArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[deploy] ssh command failed (exit $LASTEXITCODE): $Cmd"
        exit $LASTEXITCODE
    }
}

Write-Host "[deploy] 1/4  Ensuring remote work dir ..."
Invoke-Ssh "mkdir -p $RemoteWorkDir"

Write-Host "[deploy] 2/4  Uploading $funcName ..."
$scpArgs = @("-r")
if ($SshKey) { $scpArgs += @("-i", $SshKey) }
$scpArgs += @("-o", "StrictHostKeyChecking=accept-new", $FunctionDir, "${sshTarget}:$RemoteWorkDir/")
& scp @scpArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "[deploy] scp failed (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Host "[deploy] 3/4  Uploading deploy.sh ..."
$deploySh = Join-Path $PSScriptRoot "deploy.sh"
if (Test-Path $deploySh) {
    $scpArgs2 = @("-o", "StrictHostKeyChecking=accept-new")
    if ($SshKey) { $scpArgs2 += @("-i", $SshKey) }
    $scpArgs2 += @($deploySh, "${sshTarget}:$RemoteWorkDir/")
    & scp @scpArgs2
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[deploy] scp deploy.sh failed (exit $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
} else {
    Write-Warning "[deploy] deploy.sh not found next to this script; skipping upload."
}

Write-Host "[deploy] 4/4  Running remote deploy.sh ..."
Invoke-Ssh "bash $RemoteWorkDir/deploy.sh --function-dir $RemoteWorkDir/$funcName --function-name $funcName"

Write-Host "[deploy] Done. Check CVAT UI -> Models / or run: nuctl get function $funcName"
