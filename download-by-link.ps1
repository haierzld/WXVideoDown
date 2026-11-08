param(
    [string]$Url,
    [string]$Config = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Config) { $Config = Join-Path $root 'config.api.yaml' }
$exe  = Join-Path $root 'wx_video_download_debug.exe'
$api  = 'http://127.0.0.1:2022'

if (-not $Url) { $Url = Read-Host 'Paste weixin.qq.com/sph/ share link' }
if ($Url -notmatch 'weixin\.qq\.com/sph/') { throw 'Only weixin.qq.com/sph/ share links are supported' }

$listening = Get-NetTCPConnection -LocalPort 2022 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath $exe -ArgumentList @('-c', $Config) -WorkingDirectory $root `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $env:TEMP 'wx_api_stdout.log') `
        -RedirectStandardError (Join-Path $env:TEMP 'wx_api_stderr.log') | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Get-NetTCPConnection -LocalPort 2022 -State Listen -ErrorAction SilentlyContinue) { break }
    }
}
if (-not (Get-NetTCPConnection -LocalPort 2022 -State Listen -ErrorAction SilentlyContinue)) {
    throw 'API server did not start. Check config.api.yaml and the log in %TEMP%.'
}

$enc = [uri]::EscapeDataString($Url)
$tmp = Join-Path $env:TEMP 'wx_parse_result.json'
curl.exe -q -sS --noproxy '*' --connect-timeout 5 --max-time 40 -o $tmp "$api/api/channels/parse_sph?url=$enc"
if ($LASTEXITCODE -ne 0) { throw 'parse_sph request failed' }
$r = Get-Content $tmp -Raw -Encoding UTF8 | ConvertFrom-Json
if ($r.code -ne 0) { throw "parse failed: $($r.msg)" }

$fi = $r.data.data.feedInfo
$author = $r.data.data.authorInfo.nickname
if (-not $fi.videoUrl) { throw 'No videoUrl in parse result (cookie may have expired)' }

$name = (($fi.description -replace '[#"\\/:*?<>|\r\n]', '') -replace '\s+', '_')
if ($name.Length -gt 40) { $name = $name.Substring(0, 40) }
$dir = Join-Path $root 'downloads'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$out = Join-Path $dir ($name + '_' + $author + '.mp4')

$probeHeader = Join-Path $env:TEMP 'wx_video_probe.txt'
$probeBody = Join-Path $env:TEMP 'wx_video_probe.bin'
curl.exe -q -sS --noproxy '*' --connect-timeout 6 --max-time 20 -r 0-0 -D $probeHeader -o $probeBody $fi.videoUrl | Out-Null
$expected = 0
if (Test-Path $probeHeader) {
    $hit = Select-String -Path $probeHeader -Pattern 'Content-Range: bytes 0-0/(\d+)' | Select-Object -First 1
    if ($hit) { $expected = [int64]$hit.Matches[0].Groups[1].Value }
}
$size = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
if ($expected -gt 0 -and $size -ge $expected) {
    Write-Host 'Already complete, skip download.'
} else {
    for ($i = 0; $i -lt 8; $i++) {
        curl.exe -q -sS --noproxy '*' --connect-timeout 6 --max-time 300 -C - -o $out -w 'HTTP=%{http_code} CHUNK=%{size_download}' $fi.videoUrl
        $code = $LASTEXITCODE
        $size = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
        if ($code -eq 0 -and ($expected -eq 0 -or $size -ge $expected)) { break }
        Start-Sleep -Seconds 2
    }
    if ($expected -gt 0 -and $size -lt $expected) { throw "Incomplete download: $size / $expected bytes" }
    if ($code -ne 0 -and $expected -eq 0) { throw 'Video download failed' }
}
Get-Item $out | Select-Object FullName, Length, LastWriteTime | Format-List
