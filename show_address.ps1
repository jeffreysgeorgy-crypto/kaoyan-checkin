# 考研打卡 · 查当前手机访问地址（cpolar 免费版地址会变，双击 show_address.bat 查最新地址）
$Log = Join-Path $env:USERPROFILE '.cpolar\logs\cpolar_service.log'

Write-Host '==============================================' -ForegroundColor Cyan
Write-Host '  考研打卡 - 查当前手机访问地址' -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $Log)) {
    Write-Host '  [错误] 找不到 cpolar 日志文件：' -ForegroundColor Red
    Write-Host "  $Log" -ForegroundColor Red
    Write-Host '  请确认 cpolar 服务已安装并正在运行。' -ForegroundColor Red
}
else {
    $m = Select-String -Path $Log -Pattern 'Tunnel established at (https?://[a-zA-Z0-9.-]+\.cpolar\.[a-z]+)' | Select-Object -Last 1
    if ($m) {
        $Url = $m.Matches[0].Groups[1].Value
        Write-Host '  [手机直接打开这个地址]' -ForegroundColor Yellow
        Write-Host ''
        Write-Host "  $Url" -ForegroundColor Green
        Write-Host ''
        Write-Host '  打开后点「我的 → 导出并发送 Agent」验证。' -ForegroundColor White
    }
    else {
        Write-Host '  [没找到地址] cpolar 可能还没连上隧道，稍等再试。' -ForegroundColor Red
    }
}

Write-Host ''
