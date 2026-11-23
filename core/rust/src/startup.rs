use std::process::Command;

/// Checks whether RYNA is registered under the current user's Windows Run registry key.
pub fn is_windows_startup_enabled() -> bool {
    #[cfg(target_os = "windows")]
    {
        let output = Command::new("reg")
            .args([
                "query",
                r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                "/v",
                "RYNA",
            ])
            .output();
        matches!(output, Ok(out) if out.status.success())
    }
    #[cfg(not(target_os = "windows"))]
    {
        false
    }
}
