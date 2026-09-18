# 考研打卡 · 手机访问一键启动
# 启动：后端 FastAPI(8000) + 前端静态服务器(8080) + 前端隧道(cpolar 8080)
# 后端隧道走 cpolar 服务里已配置好的 xx 隧道（服务自动运行），脚本从服务日志读出它当前的公网地址。

$Root = 'D:\XX'
$FrontLog = Join-Path $env:TEMP 'kaoyan_front_tunnel.log'

Write-Host '==============================================' -ForegroundColor Cyan
Write-Host '  考研打卡 - 手机访问一键启动' -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host ''

# 1) 后端 FastAPI（8000）
Write-Host '[1/4] 启动后端 FastAPI（端口 8000）...'
Start-Process cmd -ArgumentList '/k', 'py -m uvicorn server:app --host 0.0.0.0 --port 8000' -WorkingDirectory "$Root\agent"

# 2) 前端静态服务器（8080）
Write-Host '[2/4] 启动前端静态服务器（端口 8080）...'
Start-Process cmd -ArgumentList '/k', 'py -m http.server 8080' -WorkingDirectory $Root

# 3) 前端隧道（cpolar 8080），日志写文件以便读取地址
Write-Host '[3/4] 启动前端隧道（cpolar http 8080）...'
if (Test-Path $FrontLog) { Remove-Item $FrontLog -Force }
Start-Process cmd -ArgumentList '/k', "cpolar http 8080 -log $FrontLog -log-level info"

Write-Host ''
Write-Host '正在等隧道建立（约 8 秒）...'
Start-Sleep -Seconds 8

# 4) 提取两个公网地址
Write-Host '[4/4] 读取隧道地址...'

$FrontUrl = ''
if (Test-Path $FrontLog) {
    $m = Select-String -Path $FrontLog -Pattern 'Tunnel established at (https?://[a-zA-Z0-9.-]+\.cpolar\.[a-z]+)' | Select-Object -Last 1
    if ($m) { $FrontUrl = $m.Matches[0].Groups[1].Value }
}

$BackUrl = ''
$SvcLog = Join-Path $env:USERPROFILE '.cpolar\logs\cpolar_service.log'
if (Test-Path $SvcLog) {
    $line = Select-String -Path $SvcLog -Pattern 'NewTunnel' |
        Where-Object { $_.Line -match 'localhost:8000' -and $_.Line -match 'https://' } |
        Select-Object -Last 1
    if ($line) { $BackUrl = [regex]::Match($line.Line, 'https://[a-zA-Z0-9.-]+\.cpolar\.[a-z]+').Value }
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host '  完成！请按下面操作' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
Write-Host ''

if ($FrontUrl) {
    Write-Host '  [手机打开的前端地址]' -ForegroundColor Yellow
    Write-Host "  $FrontUrl/index.html" -ForegroundColor White
    Write-Host ''
} else {
    Write-Host '  [前端地址] 自动读取失败：请打开"前端隧道-cpolar"窗口，' -ForegroundColor Red
    Write-Host '  找到 "Tunnel established at https://..." 那一行，即前端地址。' -ForegroundColor Red
    Write-Host ''
}

if ($BackUrl) {
    Write-Host '  [后端地址，填到"我的"页]' -ForegroundColor Yellow
    Write-Host "  $BackUrl" -ForegroundColor White
    Write-Host ''
} else {
    Write-Host '  [后端地址] 自动读取失败：请到 https://dashboard.cpolar.com 登录，' -ForegroundColor Red
    Write-Host '  查看 xx 隧道的当前 https 地址；或确认 cpolar 服务正在运行。' -ForegroundColor Red
    Write-Host ''
}

Write-Host '  手机操作：'
Write-Host '   1. 手机打开上面的前端地址'
Write-Host '   2. "我的"页 -> 后端地址输入框，填后端地址（不带末尾 /）'
Write-Host '   3. 点"保存"，页面刷新'
Write-Host '   4. 点"导出并发送 Agent"测试，状态栏变绿即打通'
Write-Host ''
Write-Host '按回车关闭本窗口（3 个服务窗口请保留）...'
Read-Host | Out-Null
