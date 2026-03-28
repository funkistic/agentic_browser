use std::sync::{Arc, Mutex};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // We keep a strong reference to the Python server child process.
    // When the Tauri window is destroyed, we kill the whole process tree.
    let child_handle: Arc<Mutex<Option<std::process::Child>>> = Arc::new(Mutex::new(None));
    let child_for_event = child_handle.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .setup(move |_app| {
            #[cfg(target_os = "windows")]
            let python_bin = "agent-env2\\Scripts\\python.exe";

            #[cfg(not(target_os = "windows"))]
            let python_bin = "agent-env2/bin/python";

            match std::process::Command::new(python_bin)
                .arg("agent-engine/server.py")
                .spawn()
            {
                Ok(child) => {
                    *child_handle.lock().unwrap() = Some(child);
                    println!("[Nexus] Agent Engine started successfully.");
                }
                Err(e) => println!("[Nexus] Failed to spawn Agent Engine server: {}", e),
            }

            Ok(())
        })
        .on_window_event(move |_window, event| {
            // When the main window is destroyed (user closes the app),
            // kill the Python FastAPI server (and everything IT spawned, including Chrome).
            if let tauri::WindowEvent::Destroyed = event {
                let mut guard = child_for_event.lock().unwrap();
                if let Some(ref mut child) = *guard {
                    let pid = child.id();

                    // On Windows: use taskkill /F /T to kill the entire process TREE
                    // (this takes down uvicorn → chromedriver → chrome.exe)
                    #[cfg(target_os = "windows")]
                    {
                        let _ = std::process::Command::new("taskkill")
                            .args(["/F", "/T", "/PID", &pid.to_string()])
                            .spawn();
                    }

                    // On other platforms: just kill the direct child; rely on SIGTERM propagation
                    #[cfg(not(target_os = "windows"))]
                    {
                        let _ = child.kill();
                    }

                    println!("[Nexus] Agent Engine (PID {}) killed on window close.", pid);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

