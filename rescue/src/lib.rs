//! bko-rescue: BackOtter's restore program for the rescue stick.  One
//! Whiptail-style 80x24 program on all seven rescue platforms, with its own
//! small terminal layer and no crates (docs/design/rescue-tui.md).
//!
//! Built so far: RT1 and RT2 (the crate, the FFI and its header test, the
//! console spike), RT4 (the terminal layer proper and the sanitiser),
//! RT5 (the screen buffer with diffed redraw, and the golden-screen and
//! byte-stream harnesses), RT6 (the widgets, the `Ui` trait and the
//! full-screen runner, `Secret`) and RT7 (plain mode).

/// holt's JSON reader and writer, shared as source (D2).
#[path = "../../holt/src/json.rs"]
pub mod json;
pub mod demo;
pub mod plain;
pub mod screen;
pub mod secret;
pub mod spike;
pub mod term;
#[doc(hidden)]
pub mod testkit;
pub mod text;
pub mod ui;
pub mod widgets;
