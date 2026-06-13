# 设置每周六 9:00 自动运行持仓分析
# 以管理员身份在 PowerShell 中运行此脚本
# 用法: powershell -ExecutionPolicy Bypass -File setup_scheduler.ps1

$TaskName = "TradingAgents 周度持仓分析"
$ProjectDir = "E:\codex-workspace\projects\TradingAgents"
$PythonExe = "$ProjectDir\.venv\Scripts\python.exe"
$ScriptPath = "$ProjectDir\weekly_analysis.py"
$WorkingDir = $ProjectDir

# 构建执行命令
$Action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $WorkingDir

# 每周六 9:00 触发
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 09:00

# 任务配置：允许在不登录时运行，最长运行 8 小时
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

# 以当前用户身份运行
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType ServiceAccount `
    -RunLevel Highest

# 注册任务
try {
    Register-ScheduledTask -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "每周六 9:00 自动运行 TradingAgents 完整分析流程，分析持仓研究目录下所有标的" `
        -Force

    Write-Host "✅ 定时任务已创建: $TaskName"
    Write-Host "   执行文件: $PythonExe"
    Write-Host "   脚本路径: $ScriptPath"
    Write-Host "   触发时间: 每周六 09:00"
    Write-Host ""
    Write-Host "查看任务: schtasks /query /tn '$TaskName' /v"
    Write-Host "删除任务: schtasks /delete /tn '$TaskName' /f"
    Write-Host "手动运行: schtasks /run /tn '$TaskName'"
} catch {
    Write-Host "❌ 创建失败: $_"
    Write-Host ""
    Write-Host "请以管理员身份运行此脚本："
    Write-Host "  右键 PowerShell → 以管理员身份运行"
    Write-Host "  cd E:\codex-workspace\projects\TradingAgents"
    Write-Host "  .\setup_scheduler.ps1"
}
