//! The keyboard (rescue-screens.md, "The keyboard offer", "Keyboard
//! layout"): US English until changed; the machine's own layout offered
//! once before the first password, never applied by itself.
//!
//! - Layouts are XKB names (layout and variant), as the dump records
//!   them (`keyboard.canonical`, RT10l: null until the backup tool
//!   writes it, so until then there is no offer and the keyboard line
//!   says the backup doesn't say).
//! - Changing it: on Linux, `ckbcomp -layout L [-variant V]` compiles the
//!   layout to a console keymap and `loadkeys` loads it.  The BSDs' and
//!   illumos's tools need the mapping table (open question 11, `NATIVE`
//!   below, empty until it is decided), so there, as on Haiku, the stick
//!   can't change it: no offer, no < Keyboard >, and the keyboard line
//!   names the backup's layout so it can be set by hand.
//! - Never over ssh (the layout is the client's) or on a serial console
//!   (the terminal's).

use crate::json::Value;

/// One layout the Keyboard screen lists.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Layout {
    pub layout: &'static str,
    pub variant: Option<&'static str>,
    pub name: &'static str,
}

const fn l(layout: &'static str, variant: Option<&'static str>, name: &'static str) -> Layout {
    Layout { layout, variant, name }
}

/// The stick's default (open question 5, decided 2026-11-24).
pub const US: Layout = l("us", None, "English (US)");

/// The layouts listed, in this order after the one set now and the
/// machine's own.  XKB's names: the UK's is `gb`.
pub const LAYOUTS: &[Layout] = &[
    US,
    l("de", Some("nodeadkeys"), "German, no dead keys"),
    l("de", None, "German"),
    l("gb", None, "English (UK)"),
    l("fr", None, "French (AZERTY)"),
    l("be", None, "Belgian"),
    l("es", None, "Spanish"),
    l("it", None, "Italian"),
    l("ch", None, "Swiss German"),
    l("ch", Some("fr"), "Swiss French"),
    l("pt", None, "Portuguese"),
    l("br", None, "Portuguese (Brazil)"),
    l("nl", None, "Dutch"),
    l("se", None, "Swedish"),
    l("no", None, "Norwegian"),
    l("dk", None, "Danish"),
    l("fi", None, "Finnish"),
    l("pl", None, "Polish"),
    l("cz", None, "Czech"),
    l("hu", None, "Hungarian"),
    l("ru", None, "Russian"),
    l("us", Some("intl"), "English (US, international)"),
    l("us", Some("dvorak"), "English (Dvorak)"),
    l("ca", None, "French (Canada)"),
    l("latam", None, "Spanish (Latin America)"),
    l("tr", None, "Turkish"),
];

/// XKB names to the BSDs' and illumos's own (open question 11): who
/// writes the table, where it lives and what a variant with no
/// counterpart does is undecided, so it is empty, and those sticks don't
/// change the layout.
pub const NATIVE: &[(&str, Option<&str>, &str, &str)] = &[];

/// Where the layout set now came from, for the keyboard line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum From {
    Default,
    Backup(String),
    Chosen,
}

/// An XKB layout and variant as the dump gives it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Xkb {
    pub layout: String,
    pub variant: Option<String>,
}

impl Xkb {
    pub fn of(l: &Layout) -> Xkb {
        Xkb { layout: l.layout.to_string(), variant: l.variant.map(str::to_string) }
    }

    /// The table's row, if it has one.
    pub fn row(&self) -> Option<&'static Layout> {
        LAYOUTS.iter().find(|r| r.layout == self.layout && r.variant == self.variant.as_deref())
    }

    /// `German (de, nodeadkeys)`, `English (US)`, or the XKB names alone
    /// for a layout the table hasn't.
    pub fn name(&self) -> String {
        let names = match &self.variant {
            Some(v) => format!("{}, {v}", self.layout),
            None => self.layout.clone(),
        };
        if *self == Xkb::of(&US) {
            return US.name.to_string();
        }
        match LAYOUTS.iter().find(|r| r.layout == self.layout && r.variant.is_none()) {
            Some(base) => format!("{} ({names})", base.name.split(',').next().unwrap_or(base.name)),
            None => names,
        }
    }

    /// The base layout's name alone: `German`.
    pub fn short(&self) -> String {
        LAYOUTS
            .iter()
            .find(|r| r.layout == self.layout && r.variant.is_none())
            .map(|r| r.name.to_string())
            .unwrap_or_else(|| self.layout.clone())
    }
}

fn xkb_name(s: &str) -> bool {
    !s.is_empty() && s.len() <= 32 && s.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'_' | b'-'))
}

/// The layout a machine's backup names (`keyboard.canonical`), when it
/// names one in XKB's form.
pub fn canonical(kb: Option<&Value>) -> Option<Xkb> {
    let c = kb?.get("canonical")?;
    let layout = c.get_str("layout").filter(|l| xkb_name(l))?.to_string();
    let variant = c.get_str("variant").filter(|v| xkb_name(v)).map(str::to_string);
    Some(Xkb { layout, variant })
}

/// The other layouts its users' desktops had, in order (`desktop[].layouts`).
pub fn desktop(kb: Option<&Value>) -> Vec<Xkb> {
    let mut v: Vec<Xkb> = Vec::new();
    for d in kb.map(|k| k.arr_at(&["desktop"])).unwrap_or(&[]) {
        for x in d.arr_at(&["layouts"]) {
            if let Some(l) = x.get_str("layout").filter(|l| xkb_name(l)) {
                let k = Xkb { layout: l.to_string(), variant: x.get_str("variant").filter(|v| xkb_name(v)).map(str::to_string) };
                if !v.contains(&k) {
                    v.push(k);
                }
            }
        }
    }
    v
}

/// The layout the machine's start-up prompt takes the disk passphrase in
/// (`keyboard.boot_prompt`, RT10l; rescue-screens.md, "The keyboard at
/// start-up"): US where no map is loaded there (`us_fallback`, and
/// FreeBSD's loader and OpenBSD's boot, which are US by nature), the
/// console's own layout where the prompt loads one (`native` named);
/// None where the dump doesn't say, as every dump before RT10l.
pub fn boot_prompt(kb: Option<&Value>) -> Option<Xkb> {
    let b = kb?.get("boot_prompt").filter(|b| !b.is_null())?;
    if b.get_bool("us_fallback") == Some(true) || matches!(b.get_str("where"), Some("loader" | "boot")) {
        return Some(Xkb::of(&US));
    }
    if b.get("native").is_some_and(|n| !n.is_null()) {
        return canonical(kb);
    }
    None
}

/// The keyboard as it is now.
#[derive(Clone, Debug)]
pub struct Keyboard {
    pub set: Xkb,
    pub from: From,
    /// This stick can change the layout here (a screen console, with the
    /// tools).
    pub can_change: bool,
    /// The machines (install IDs) the offer was made for: once each.
    pub offered: Vec<String>,
}

impl Keyboard {
    pub fn new(can_change: bool) -> Keyboard {
        Keyboard { set: Xkb::of(&US), from: From::Default, can_change, offered: Vec::new() }
    }
}

/// The commands that set a layout: Linux's `ckbcomp`, whose keymap then
/// goes to `loadkeys` (its path given after it is written).
pub const CKBCOMP: &str = "ckbcomp";
pub const LOADKEYS: &str = "loadkeys";

pub fn ckbcomp_args(x: &Xkb) -> Vec<String> {
    let mut a = vec!["-layout".to_string(), x.layout.clone()];
    if let Some(v) = &x.variant {
        a.push("-variant".to_string());
        a.push(v.clone());
    }
    a
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_the_dump() {
        assert_eq!(Xkb::of(&US).name(), "English (US)");
        let de = Xkb { layout: "de".into(), variant: Some("nodeadkeys".into()) };
        assert_eq!(de.name(), "German (de, nodeadkeys)");
        assert_eq!(de.short(), "German");
        assert_eq!(Xkb { layout: "xx".into(), variant: None }.name(), "xx");
        let kb = crate::json::parse(
            r#"{"canonical": {"layout": "de", "variant": "nodeadkeys", "from": "xkb"},
                "desktop": [{"source": "kxkbrc", "layouts": [{"layout": "de", "variant": "nodeadkeys"}, {"layout": "us", "variant": null}]}]}"#,
        )
        .unwrap();
        assert_eq!(canonical(Some(&kb)), Some(de.clone()));
        assert_eq!(desktop(Some(&kb)), vec![de, Xkb::of(&US)]);
        let bad = crate::json::parse(r#"{"canonical": {"layout": "de; rm -rf /", "variant": null}}"#).unwrap();
        assert_eq!(canonical(Some(&bad)), None);
        assert_eq!(canonical(None), None);
        assert!(LAYOUTS.iter().all(|l| xkb_name(l.layout) && l.variant.is_none_or(xkb_name)));
        assert!(NATIVE.is_empty(), "open question 11 decides the table");
    }

    #[test]
    fn the_start_up_prompts_layout() {
        let kb = |t: &str| crate::json::parse(t).unwrap();
        let de = r#""canonical": {"layout": "de", "variant": null}"#;
        // Debian's initramfs without KEYMAP=y: US whatever the console has.
        assert_eq!(boot_prompt(Some(&kb(&format!(r#"{{{de}, "boot_prompt": {{"where": "initramfs", "us_fallback": true, "native": null}}}}"#)))), Some(Xkb::of(&US)));
        // FreeBSD's loader and OpenBSD's boot are US by nature.
        for w in ["loader", "boot"] {
            assert_eq!(boot_prompt(Some(&kb(&format!(r#"{{{de}, "boot_prompt": {{"where": "{w}", "us_fallback": false, "native": null}}}}"#)))), Some(Xkb::of(&US)));
        }
        // A prompt that loads the console's map: the backup's layout.
        let native = kb(&format!(r#"{{{de}, "boot_prompt": {{"where": "initramfs", "us_fallback": false, "native": "de-latin1"}}}}"#));
        assert_eq!(boot_prompt(Some(&native)), Some(Xkb { layout: "de".into(), variant: None }));
        // The dump doesn't say (every dump before RT10l): not known.
        assert_eq!(boot_prompt(Some(&kb(&format!("{{{de}}}")))), None);
        assert_eq!(boot_prompt(Some(&kb(&format!(r#"{{{de}, "boot_prompt": null}}"#)))), None);
        assert_eq!(boot_prompt(Some(&kb(r#"{"boot_prompt": {"where": "initramfs", "us_fallback": false, "native": null}}"#))), None);
        assert_eq!(boot_prompt(None), None);
    }
}
