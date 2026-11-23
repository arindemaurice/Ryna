use crate::config::CoreConfig;
use std::net::TcpStream;
use std::process::{Command, Stdio};
use std::time::Duration;

pub fn check_local_port(host: &str, port: u16) -> bool {
    let addr = format!("{}:{}", host, port);
    addr.parse()
        .ok()
        .and_then(|sock| TcpStream::connect_timeout(&sock, Duration::from_millis(500)).ok())
        .is_some()
}

pub fn run_python_engine(
    cfg: &CoreConfig,
    companion: bool,
    open_ui: bool,
) -> Result<i32, String> {
    if check_local_port(&cfg.host, cfg.port) {
        return Err(format!(
            "Local port {}:{} is already in use by an active service.",
            cfg.host, cfg.port
        ));
    }

    let mut cmd = Command::new(&cfg.python_bin);
    cmd.current_dir(&cfg.project_root)
        .args(["-m", "core.python.main", "--host", &cfg.host, "--port", &cfg.port.to_string(), "--allow-multiple"])
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    if companion {
        cmd.arg("--companion");
    }
    if open_ui {
        cmd.arg("--open-ui");
    }

    let status = cmd
        .status()
        .map_err(|e| format!("Failed to spawn Python engine '{}': {}", cfg.python_bin, e))?;

    Ok(status.code().unwrap_or(1))
}
