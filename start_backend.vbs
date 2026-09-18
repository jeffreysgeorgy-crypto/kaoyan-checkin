' 考研打卡 · 后端开机自启（隐藏窗口，日志写到 agent/backend_boot.log）
' 放在「启动」文件夹后，每次登录电脑会自动拉起后端（端口 8000）。
' 手机访问地址：https://693555c6.r24.cpolar.top （cpolar 服务已开机自启，固定隧道，无需再跑脚本）

Set ws = CreateObject("Wscript.Shell")
ws.Run "cmd /c cd /d D:\XX\agent && py -m uvicorn server:app --host 0.0.0.0 --port 8000 >> D:\XX\agent\backend_boot.log 2>&1", 0, False
