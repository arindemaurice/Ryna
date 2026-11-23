//! RYNA 0.1.0 Core Engine (Rust)
//!
//! Responsibilities:
//! - Application lifecycle & single-instance locking
//! - Supervised launch and restart of the Python AI/Agent service
//! - Localhost loopback health verification
//! - Safe Windows startup status inspection

mod config;
mod single_instance;
mod startup;
mod supervisor;

use config::CoreConfig;
use single_instance::SingleInstanceLock;
use std::env;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    let cfg = CoreConfig::from_env();

    if args.iter().any(|a| a == "--version" || a == "-V") {
        println!("ryna-core 0.1.0");
        return ExitCode::SUCCESS;
    }

    if args.iter().any(|a| a == "--startup-status") {
        println!("Windows startup enabled: {}", startup::is_windows_startup_enabled());
        return ExitCode::SUCCESS;
    }

    let _lock = match SingleInstanceLock::acquire("ryna-core.lock") {
        Ok(lock) => lock,
        Err(msg) => {
            eprintln!("[RYNA Core] {}", msg);
            return ExitCode::SUCCESS;
        }
    };

    println!(
        "[RYNA Core 0.1.0] Starting supervised local engine on {}:{}",
        cfg.host, cfg.port
    );

    let launch_companion = args.iter().any(|a| a == "--companion");
    let open_ui = args.iter().any(|a| a == "--open-ui");

    match supervisor::run_python_engine(&cfg, launch_companion, open_ui) {
        Ok(code) => {
            println!("[RYNA Core] Engine exited cleanly with code {}", code);
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("[RYNA Core] Supervision error: {}", err);
            ExitCode::FAILURE
        }
    }
}
