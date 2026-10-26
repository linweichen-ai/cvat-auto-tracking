# 构建脚本：把 bridge + panel-core 拼装成最终交付物
#   userscript/cvat-auto-annotation.user.js = header + bridge-userscript + panel-core
#   extension/content.js                     = bridge-extension + panel-core
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$core = Get-Content (Join-Path $root 'extension\panel-core.js') -Raw -Encoding UTF8
$bridgeUserscript = Get-Content (Join-Path $root 'userscript\bridge-userscript.js') -Raw -Encoding UTF8
$header = Get-Content (Join-Path $root 'userscript\userscript-header.txt') -Raw -Encoding UTF8
$bridgeExt = Get-Content (Join-Path $root 'extension\bridge-extension.js') -Raw -Encoding UTF8

$nl = "`r`n"
$userjs = $header.TrimEnd() + $nl + $nl + $bridgeUserscript.TrimEnd() + $nl + $nl + $core.TrimEnd() + $nl
[System.IO.File]::WriteAllText((Join-Path $root 'userscript\cvat-auto-annotation.user.js'), $userjs, (New-Object System.Text.UTF8Encoding($false)))

$contentJs = $bridgeExt.TrimEnd() + $nl + $nl + $core.TrimEnd() + $nl
[System.IO.File]::WriteAllText((Join-Path $root 'extension\content.js'), $contentJs, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "build OK:"
Write-Host ("  userscript\cvat-auto-annotation.user.js  " + ((Get-Item (Join-Path $root 'userscript\cvat-auto-annotation.user.js')).Length) + " bytes")
Write-Host ("  extension\content.js                     " + ((Get-Item (Join-Path $root 'extension\content.js')).Length) + " bytes")
