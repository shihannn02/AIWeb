@echo off
chcp 65001 >nul
echo 正在添加 Windows 防火墙规则（需管理员权限）...
netsh advfirewall firewall add rule name="AIWeb Port 8000" dir=in action=allow protocol=TCP localport=8000 profile=any
netsh advfirewall firewall add rule name="AIWeb Python" dir=in action=allow program="C:\Users\6\AppData\Local\Programs\Python\Python311\python.exe" enable=yes profile=any
echo.
echo 完成。请让同事访问: http://192.168.3.37:8000
echo （若 IP 变了，在本机运行 ipconfig 查看 IPv4）
pause
