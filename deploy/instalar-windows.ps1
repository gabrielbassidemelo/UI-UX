# Windows: registra o healthmon para iniciar no logon (Agendador de Tarefas).
# Execute no PowerShell, dentro da pasta do projeto, após criar o venv e instalar.
$exe = Join-Path $PSScriptRoot "..\.venv\Scripts\healthmon.exe" | Resolve-Path
$action = New-ScheduledTaskAction -Execute $exe -Argument "executar"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "healthmon" -Action $action -Trigger $trigger -Settings $settings -Description "Monitoramento pessoal de saude (Garmin)"
Write-Host "Tarefa 'healthmon' registrada. Inicie agora com: Start-ScheduledTask healthmon"
