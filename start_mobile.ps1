# 考研打卡 · 手机访问一键启动（单隧道版）
# 后端已顺带托管前端（同源），所以只需一个 cpolar 隧道到 8000，手机开一个地址即可，不用填后端地址。

$Root = 'D:\XX'
$TunnelLog = Join-Path $env:TEMP 'kaoyan_tunnel.log'

Write-Host '==============================================' -ForegroundColor Cyan
Write-Host '  考研打卡 - 手机访问一键启动' -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host ''

# 1) 后端 FastAPI（8000，前端后端同源）
Write-Host '[1/2] 启动后端 FastAPI（端口 8000，已顺带托管前端）...'
Start-Process cmd -ArgumentList '/k', 'py -m uvicorn server:app --host 0.0.0.0 --port 8000' -WorkingDirectory "$Root\agent"

# 2) 隧道（cpolar http 8000），日志写文件以便读取地址
Write-Host '[2/2] 启动隧道（cpolar http 8000）...'
if (Test-Path $TunnelLog) { Remove-Item $TunnelLog -Force }
Start-Process cmd -ArgumentList '/k', "cpolar http 8000 -log $TunnelLog -log-level info"

Write-Host ''
Write-Host '正在等隧道建立（约 8 秒）...'
Start-Sleep -Seconds 8

# 3) 读取公网地址
$Url = ''
if (Test-Path $TunnelLog) {
    $m = Select-String -Path $TunnelLog -Pattern 'Tunnel established at (https?://[a-zA-Z0-9.-]+\.cpolar\.[a-z]+)' | Select-Object -Last 1
    if ($m) { $Url = $m.Matches[0].Groups[1].Value }
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host '  完成！' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
Write-Host ''

if ($Url) {
    Write-Host '  [手机直接打开这个地址，不用填后端地址]' -ForegroundColor Yellow
    Write-Host "  $Url" -ForegroundColor White
    Write-Host ''
    Write-Host '  打开后点「我的 → 导出并发送 Agent」，状态栏变绿即打通。' -ForegroundColor White
} else {
    Write-Host '  [地址读取失败] 请打开 "cpolar" 窗口，' -ForegroundColor Red
    Write-Host '  找到 "Tunnel established at https://..." 那一行，就是手机要打开的地址。' -ForegroundColor Red
}

Write-Host ''
Write-Host '  后端 + cpolar 两个窗口请保留，电脑保持开机。' -ForegroundColor White
Write-Host ''
Write-Host '按回车关闭本窗口（后端 + cpolar 窗口请保留）...'
Read-Host | Out-Null
