use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::process;

pub struct SingleInstanceLock {
    path: PathBuf,
}

impl SingleInstanceLock {
    pub fn acquire(file_name: &str) -> Result<Self, String> {
        let path = env::temp_dir().join(file_name);
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut f) => {
                let _ = writeln!(f, "{}", process::id());
                Ok(Self { path })
            }
            Err(_) => Err(format!(
                "Another RYNA instance lock exists at {}. Bringing existing instance forward or exiting.",
                path.display()
            )),
        }
    }
}

impl Drop for SingleInstanceLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn acquires_and_releases_lock() {
        let lock_name = format!("ryna-test-{}.lock", process::id());
        {
            let _lock1 = SingleInstanceLock::acquire(&lock_name).expect("first lock should succeed");
            assert!(SingleInstanceLock::acquire(&lock_name).is_err());
        }
        let _lock2 = SingleInstanceLock::acquire(&lock_name).expect("lock should be re-acquirable after drop");
    }
}
