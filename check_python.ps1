$p = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\python3.exe"
Write-Host "Python path: $p"
& $p --version
