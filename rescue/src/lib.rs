//! bko-rescue: BackOtter's restore program for the rescue stick.  One
//! Whiptail-style 80x24 program on all seven rescue platforms, with its own
//! small terminal layer and no crates (docs/design/rescue-tui.md).
//!
//! Built so far: RT1 and RT2 (the crate, the FFI and its header test, the
//! console spike), RT4 (the terminal layer proper and the sanitiser) and
//! RT5 (the screen buffer with diffed redraw, and the golden-screen and
//! byte-stream harnesses).

/// holt's JSON reader and writer, shared as source (D2).
#[path = "../../holt/src/json.rs"]
pub mod json;
pub mod screen;
pub mod spike;
pub mod term;
#[doc(hidden)]
pub mod testkit;
pub mod text;
