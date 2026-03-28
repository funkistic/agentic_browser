Write-Host "Ensuring Rust is installed..."
$rust_installed = Get-Command rustc -ErrorAction SilentlyContinue
if (-not $rust_installed) {
    Write-Host "Rust not found. Downloading and installing rustup..."
    Invoke-WebRequest -Uri "https://win.rustup.rs" -OutFile "rustup-init.exe"
    .\rustup-init.exe -y --quiet
    $env:Path += ";$env:USERPROFILE\.cargo\bin"
    Write-Host "Rust installation completed."
} else {
    Write-Host "Rust already installed."
}

Write-Host "Installing Node.js dependencies..."
cmd /c "npm install"

Write-Host "Stopping any running Node.js processes holding a lock..."
Stop-Process -Name "node" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host "Cleaning Vite cache to prevent lockfile issues..."
if (Test-Path "node_modules\.vite") {
    Remove-Item -Recurse -Force "node_modules\.vite" -ErrorAction SilentlyContinue
}

Write-Host "Starting the Tauri app in development mode..."
cmd /c "npm run tauri dev"
