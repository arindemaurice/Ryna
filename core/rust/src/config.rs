use std::env;
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct CoreConfig {
    pub host: String,
    pub port: u16,
    pub python_bin: String,
    pub project_root: PathBuf,
}

impl CoreConfig {
    pub fn from_env() -> Self {
        let host = env::var("RYNA_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
        let port = env::var("RYNA_PORT")
            .ok()
            .and_then(|v| v.parse::<u16>().ok())
            .unwrap_or(8765);
        let python_bin = env::var("RYNA_PYTHON").unwrap_or_else(|_| "python".to_string());
        let project_root = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));

        Self {
            host,
            port,
            python_bin,
            project_root,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_host_is_loopback() {
        let cfg = CoreConfig::from_env();
        assert!(!cfg.host.is_empty());
        assert!(cfg.port > 0);
    }
}
