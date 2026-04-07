// DALIOS Desktop Application — Tauri entry point
// Wraps the React frontend in a native window (~5MB binary)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running DALIOS desktop application");
}
