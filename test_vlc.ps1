Get-CimInstance -ClassName Win32_Process -Filter "name = 'vlc.exe'" | Select-Object CommandLine, ProcessId | ConvertTo-Json -Compress

