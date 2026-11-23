//! RYNA Core Supervisor (Rust)
//! Manages single-instance lock, Windows startup status, and supervised launch of the Python AI core.

use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, ExitCode, Stdio};

struct InstanceLock(PathBuf);

impl InstanceLock {
    fn acquire(name: &str) -> Result<Self, String> {
        let p = env::temp_dir().join(name);
        let mut f = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&p)
            .map_err(|_| format!("RYNA instance already active ({})", p.display()))?;
        let _ = writeln!(f, "{}", std::process::id());
        Ok(Self(p))
    }
}

impl Drop for InstanceLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.iter().any(|a| a == "--version" || a == "-V") {
        println!("ryna-core 0.1.0");
        return ExitCode::SUCCESS;
    }

    let _lock = match InstanceLock::acquire("ryna-core-v2.lock") {
        Ok(l) => l,
        Err(msg) => {
            eprintln!("[RYNA Core] {}", msg);
            return ExitCode::SUCCESS;
        }
    };

    let py = env::var("RYNA_PYTHON").unwrap_or_else(|_| "python".to_string());
    let mut cmd = Command::new(&py);
    cmd.args(["-m", "ai.orchestration.main", "--allow-multiple"])
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    for flag in ["--open-ui", "--robot"] {
        if args.iter().any(|a| a == flag) {
            cmd.arg(flag);
        }
    }

    match cmd.status() {
        Ok(st) if st.success() => ExitCode::SUCCESS,
        Ok(st) => {
            eprintln!("[RYNA Core] Engine exited with code {:?}", st.code());
            ExitCode::FAILURE
        }
        Err(e) => {
            eprintln!("[RYNA Core] Failed to launch Python core '{}': {}", py, e);
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn single_instance_lock_roundtrip() {
        let name = format!("ryna-v2-test-{}.lock", std::process::id());
        {
            let _a = InstanceLock::acquire(&name).expect("first acquire");
            assert!(InstanceLock::acquire(&name).is_err());
        }
        let _b = InstanceLock::acquire(&name).expect("re-acquire after drop");
    }
}
