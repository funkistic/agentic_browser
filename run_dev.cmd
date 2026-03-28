@echo off
echo Starting Tauri development server...

rem Add Cargo to PATH in case the terminal hasn't been restarted since installing Rust
set PATH=%PATH%;%USERPROFILE%\.cargo\bin

rem Run the app via npm
npm run tauri dev
