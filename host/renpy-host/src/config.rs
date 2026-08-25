//! Centralized host env reads (Phase 1 closeout).
//! Single call-site for `RENPY_HOST_*` env vars; typed bool/Path outputs.

use std::path::PathBuf;

/// Typed host configuration from env.
pub struct HostConfig {
    pub base: PathBuf,
    pub game: Option<PathBuf>,
    pub phase0_signals: bool,
    pub ui_trace: bool,
    pub draw_raise: bool,
}

impl HostConfig {
    /// Read all `RENPY_HOST_*` vars from process env.
    /// Keeps compatibility with prior `std::env::var` call-sites; only centralizes.
    pub fn from_env() -> Self {
        let base = std::env::var("RENPY_HOST_BASE")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
        let game = std::env::var("RENPY_HOST_GAME")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from);
        Self {
            base,
            game,
            phase0_signals: env_bool("RENPY_HOST_PHASE0_SIGNALS"),
            ui_trace: env_bool("RENPY_HOST_UI_TRACE"),
            draw_raise: env_bool("RENPY_HOST_DRAW_RAISE"),
        }
    }
}

fn env_bool(name: &str) -> bool {
    matches!(
        std::env::var(name).map(|v| v.to_lowercase()).as_deref(),
        Ok("1") | Ok("true") | Ok("yes")
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn with_env<F: FnOnce()>(k: &str, v: Option<&str>, f: F) {
        let prev = env::var(k).ok();
        match v {
            Some(val) => unsafe { env::set_var(k, val) },
            None => unsafe { env::remove_var(k) },
        }
        f();
        match prev {
            Some(val) => unsafe { env::set_var(k, val) },
            None => unsafe { env::remove_var(k) },
        }
    }

    #[test]
    fn env_bool_truthy_variants() {
        let _g = ENV_LOCK.lock().unwrap();
        for truthy in ["1", "true", "TRUE", "True", "yes", "YES", "Yes"] {
            with_env("RENPY_HOST_UI_TRACE", Some(truthy), || {
                assert!(
                    env_bool("RENPY_HOST_UI_TRACE"),
                    "should be true for {truthy}"
                )
            });
        }
        for falsy in ["0", "false", "", "no", "2", "1 "] {
            with_env("RENPY_HOST_UI_TRACE", Some(falsy), || {
                assert!(
                    !env_bool("RENPY_HOST_UI_TRACE"),
                    "should be false for {falsy:?}"
                )
            });
        }
        with_env("RENPY_HOST_UI_TRACE", None, || {
            assert!(!env_bool("RENPY_HOST_UI_TRACE"))
        });
    }

    #[test]
    fn from_env_reads_typed_fields() {
        let _g = ENV_LOCK.lock().unwrap();
        with_env("RENPY_HOST_BASE", Some("/tmp/base"), || {
            with_env("RENPY_HOST_GAME", Some("/tmp/game"), || {
                with_env("RENPY_HOST_PHASE0_SIGNALS", Some("1"), || {
                    with_env("RENPY_HOST_UI_TRACE", Some("yes"), || {
                        with_env("RENPY_HOST_DRAW_RAISE", Some("true"), || {
                            let c = HostConfig::from_env();
                            assert_eq!(c.base, PathBuf::from("/tmp/base"));
                            assert_eq!(c.game, Some(PathBuf::from("/tmp/game")));
                            assert!(c.phase0_signals);
                            assert!(c.ui_trace);
                            assert!(c.draw_raise);
                        })
                    })
                })
            })
        });
        with_env("RENPY_HOST_GAME", None, || {
            let c = HostConfig::from_env();
            assert!(c.game.is_none());
        });
    }
}
