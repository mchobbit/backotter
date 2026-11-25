//! Times as the screens show them (rescue-screens.md, "Conventions",
//! "Times"; "4. Snapshot"): UTC until a machine is chosen, then in that
//! machine's own zone as its backup recorded it, always naming the zone:
//! `22 Nov 2026 02:00 CET`.  Logs, file names and the bug report stay
//! UTC, marked `Z`.
//!
//! Which zone, in this order (open question 6 is on these fallbacks;
//! `ORDER` says it in one place):
//!
//! 1. the `time` section's `zone`, when the image has zoneinfo for it:
//!    the TZif file's footer, its POSIX rule, worked out here;
//! 2. else its `posix_rule`, worked out here;
//! 3. else each time's own offset, as rustic-bko stored it
//!    (`2026-11-22T02:00+01:00`), shown as `02:00 UTC+01:00`.
//!
//! Before RT10l no `system.json` has a `time` section, so a machine's
//! times are its offsets (a snapshot) or UTC (system.json's `written`).
//! No libc: the rule is evaluated here, so it works the same on all
//! seven platforms, Haiku's (no zoneinfo tree) included.

use crate::json::Value;

/// The fallbacks, in order (rescue-screens.md's open question 6).
pub const ORDER: [Way; 3] = [Way::Zoneinfo, Way::Rule, Way::Offset];

/// Where zoneinfo is on this platform.
pub fn zoneinfo_dir() -> &'static str {
    if cfg!(target_os = "illumos") {
        "/usr/share/lib/zoneinfo"
    } else {
        "/usr/share/zoneinfo"
    }
}

/// The most of a TZif file read.
pub const TZIF_MAX: usize = 256 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Way {
    Zoneinfo,
    Rule,
    Offset,
}

const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/// Days since 1970-01-01 of a civil date (Howard Hinnant's algorithm).
pub fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let m = i64::from(m);
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + i64::from(d) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// The civil date of a day since 1970-01-01.
pub fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn leap(y: i64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

/// An RFC 3339 time (`2026-11-22T01:00:11Z`,
/// `2026-11-22T02:00:00.123456789+01:00`): seconds since 1970 and the
/// offset it was written with, in seconds east of UTC.
pub fn parse_rfc3339(s: &str) -> Option<(i64, i32)> {
    let b = s.as_bytes();
    if b.len() < 19 || b[4] != b'-' || b[7] != b'-' || !matches!(b[10], b'T' | b't' | b' ') || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let num = |a: usize, z: usize| -> Option<i64> { s.get(a..z)?.parse::<i64>().ok().filter(|_| s[a..z].bytes().all(|c| c.is_ascii_digit())) };
    let (y, mo, d, h, mi, sec) = (num(0, 4)?, num(5, 7)?, num(8, 10)?, num(11, 13)?, num(14, 16)?, num(17, 19)?);
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) || h > 23 || mi > 59 || sec > 60 {
        return None;
    }
    let mut i = 19;
    if b.get(i) == Some(&b'.') {
        i += 1;
        let start = i;
        while b.get(i).is_some_and(u8::is_ascii_digit) {
            i += 1;
        }
        if i == start {
            return None;
        }
    }
    let off = match b.get(i)? {
        b'Z' | b'z' if i + 1 == b.len() => 0,
        c @ (b'+' | b'-') if b.len() == i + 6 && b[i + 3] == b':' => {
            let oh = num(i + 1, i + 3)?;
            let om = num(i + 4, i + 6)?;
            if oh > 23 || om > 59 {
                return None;
            }
            let o = (oh * 3600 + om * 60) as i32;
            if *c == b'-' {
                -o
            } else {
                o
            }
        }
        _ => return None,
    };
    let days = days_from_civil(y, mo as u32, d as u32);
    let local = days * 86_400 + h * 3600 + mi * 60 + sec.min(59);
    Some((local - i64::from(off), off))
}

/// `22 Nov 2026 02:00` for a time at `offset` seconds east of UTC.
pub fn civil(t: i64, offset: i32) -> String {
    let l = t + i64::from(offset);
    let (y, m, d) = civil_from_days(l.div_euclid(86_400));
    let s = l.rem_euclid(86_400);
    format!("{d:02} {} {y} {:02}:{:02}", MONTHS[(m - 1) as usize], s / 3600, (s % 3600) / 60)
}

/// `UTC+01:00`, `UTC-05:30`, `UTC` for 0.
pub fn offset_name(offset: i32) -> String {
    if offset == 0 {
        return "UTC".to_string();
    }
    let sign = if offset < 0 { '-' } else { '+' };
    let a = offset.unsigned_abs();
    format!("UTC{sign}{:02}:{:02}", a / 3600, (a % 3600) / 60)
}

/// `20261123-142630`, UTC, for file names (report-bug's own default).
pub fn stamp(t: i64) -> String {
    let (y, m, d) = civil_from_days(t.div_euclid(86_400));
    let s = t.rem_euclid(86_400);
    format!("{y:04}{m:02}{d:02}-{:02}{:02}{:02}", s / 3600, (s % 3600) / 60, s % 60)
}

/// `20261123T141812Z`, UTC, for restore logs (stick-config.md, "Restore
/// logs": `restore-logs/<UTC timestamp>.log.gz`).
pub fn stamp_z(t: i64) -> String {
    let (y, m, d) = civil_from_days(t.div_euclid(86_400));
    let s = t.rem_euclid(86_400);
    format!("{y:04}{m:02}{d:02}T{:02}{:02}{:02}Z", s / 3600, (s % 3600) / 60, s % 60)
}

/// When a POSIX rule's change happens in a year.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Date {
    /// `Jn`: day 1-365, February 29 never counted.
    Julian(u32),
    /// `n`: day 0-365, February 29 counted.
    Zero(u32),
    /// `Mm.w.d`: day d (0 Sunday) of week w (5 the last) of month m.
    Month(u32, u32, u32),
}

/// A POSIX TZ rule (`CET-1CEST,M3.5.0,M10.5.0/3`), as a TZif file's
/// footer and system.json's `posix_rule` hold it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rule {
    pub std: String,
    /// Seconds east of UTC (POSIX writes west).
    pub std_off: i32,
    pub dst: Option<Dst>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Dst {
    pub name: String,
    pub off: i32,
    start: (Date, i32),
    end: (Date, i32),
}

struct P<'a> {
    b: &'a [u8],
    i: usize,
}

impl P<'_> {
    fn peek(&self) -> Option<u8> {
        self.b.get(self.i).copied()
    }

    fn name(&mut self) -> Option<String> {
        let start;
        let end;
        if self.peek() == Some(b'<') {
            self.i += 1;
            start = self.i;
            while self.peek().is_some_and(|c| c.is_ascii_alphanumeric() || c == b'+' || c == b'-') {
                self.i += 1;
            }
            end = self.i;
            if self.peek() != Some(b'>') {
                return None;
            }
            self.i += 1;
        } else {
            start = self.i;
            while self.peek().is_some_and(|c| c.is_ascii_alphabetic()) {
                self.i += 1;
            }
            end = self.i;
        }
        (end - start >= 3 && end - start <= 16).then(|| String::from_utf8_lossy(&self.b[start..end]).to_string())
    }

    fn num(&mut self, max: u32) -> Option<u32> {
        let start = self.i;
        let mut n: u32 = 0;
        while let Some(c) = self.peek().filter(u8::is_ascii_digit) {
            n = n.checked_mul(10)?.checked_add(u32::from(c - b'0'))?;
            self.i += 1;
        }
        (self.i > start && n <= max).then_some(n)
    }

    /// `[+-]hh[:mm[:ss]]`, in seconds; hours up to `max_h`.
    fn time(&mut self, max_h: u32) -> Option<i32> {
        let sign = match self.peek() {
            Some(b'-') => {
                self.i += 1;
                -1
            }
            Some(b'+') => {
                self.i += 1;
                1
            }
            _ => 1,
        };
        let h = self.num(max_h)?;
        let mut s = h * 3600;
        for unit in [60, 1] {
            if self.peek() != Some(b':') {
                break;
            }
            self.i += 1;
            s += self.num(59)? * unit;
        }
        Some(sign * s as i32)
    }

    fn date(&mut self) -> Option<(Date, i32)> {
        let d = match self.peek()? {
            b'J' => {
                self.i += 1;
                Date::Julian(self.num(365).filter(|n| *n >= 1)?)
            }
            b'M' => {
                self.i += 1;
                let m = self.num(12).filter(|n| *n >= 1)?;
                if self.peek() != Some(b'.') {
                    return None;
                }
                self.i += 1;
                let w = self.num(5).filter(|n| *n >= 1)?;
                if self.peek() != Some(b'.') {
                    return None;
                }
                self.i += 1;
                Date::Month(m, w, self.num(6)?)
            }
            _ => Date::Zero(self.num(365)?),
        };
        let at = if self.peek() == Some(b'/') {
            self.i += 1;
            // RFC 8536's extension: -167 to 167 hours.
            self.time(167)?
        } else {
            7200
        };
        Some((d, at))
    }
}

impl Rule {
    pub fn parse(s: &str) -> Option<Rule> {
        let s = s.trim();
        if s.len() > 128 {
            return None;
        }
        let mut p = P { b: s.as_bytes(), i: 0 };
        let std = p.name()?;
        let std_off = -p.time(24)?;
        if p.peek().is_none() {
            return Some(Rule { std, std_off, dst: None });
        }
        let name = p.name()?;
        let off = if matches!(p.peek(), Some(b',') | None) { std_off + 3600 } else { -p.time(24)? };
        // No rule given: the US's, as POSIX says the default is
        // implementation-defined; glibc uses M3.2.0,M11.1.0.
        let (start, end) = if p.peek() == Some(b',') {
            p.i += 1;
            let a = p.date()?;
            if p.peek() != Some(b',') {
                return None;
            }
            p.i += 1;
            (a, p.date()?)
        } else {
            ((Date::Month(3, 2, 0), 7200), (Date::Month(11, 1, 0), 7200))
        };
        if p.peek().is_some() {
            return None;
        }
        Some(Rule { std, std_off, dst: Some(Dst { name, off, start, end }) })
    }

    /// The offset and the zone's abbreviation at time `t`.
    pub fn at(&self, t: i64) -> (i32, &str) {
        let Some(d) = &self.dst else { return (self.std_off, &self.std) };
        let local_std = t + i64::from(self.std_off);
        let (y, _, _) = civil_from_days(local_std.div_euclid(86_400));
        // The changes in this year, in UTC: the start is given in
        // standard time, the end in summer time.
        let start = day_of(y, d.start.0) * 86_400 + i64::from(d.start.1) - i64::from(self.std_off);
        let end = day_of(y, d.end.0) * 86_400 + i64::from(d.end.1) - i64::from(d.off);
        let summer = if start < end { t >= start && t < end } else { !(t >= end && t < start) };
        if summer {
            (d.off, &d.name)
        } else {
            (self.std_off, &self.std)
        }
    }

    /// `CET in winter, CEST in summer`, or `CET` alone.
    pub fn names(&self) -> (String, Option<String>) {
        (self.std.clone(), self.dst.as_ref().map(|d| d.name.clone()))
    }
}

/// The day since 1970 of a rule's date in year `y`.
fn day_of(y: i64, d: Date) -> i64 {
    let jan1 = days_from_civil(y, 1, 1);
    match d {
        Date::Julian(n) => {
            let n = i64::from(n) - 1;
            // February 29 isn't counted: from March on, a leap year's day
            // is one later.
            jan1 + n + i64::from(leap(y) && n >= 59)
        }
        Date::Zero(n) => jan1 + i64::from(n),
        Date::Month(m, w, wd) => {
            let first = days_from_civil(y, m, 1);
            // 1970-01-01 was a Thursday (4).
            let first_wd = (first + 4).rem_euclid(7);
            let mut day = first + (i64::from(wd) - first_wd).rem_euclid(7) + (i64::from(w) - 1) * 7;
            let next = if m == 12 { days_from_civil(y + 1, 1, 1) } else { days_from_civil(y, m + 1, 1) };
            while day >= next {
                day -= 7;
            }
            day
        }
    }
}

/// A TZif file's footer, its POSIX rule (version 2 and later): the text
/// between the file's last two newlines.
pub fn tzif_rule(b: &[u8]) -> Option<String> {
    if !b.starts_with(b"TZif") || b.get(4).is_none_or(|v| *v < b'2') || b.last() != Some(&b'\n') {
        return None;
    }
    let body = &b[..b.len() - 1];
    let nl = body.iter().rposition(|c| *c == b'\n')?;
    let r = std::str::from_utf8(&body[nl + 1..]).ok()?;
    (!r.is_empty() && r.is_ascii()).then(|| r.to_string())
}

/// How one machine's times are shown.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Zone {
    /// No machine chosen yet, or none of its zone is known and the times
    /// have no offset of their own (system.json's `written`).
    Utc,
    /// The zone's rule: its name (`Europe/Berlin`) when known.
    Rule { name: Option<String>, rule: Rule, way: Way },
    /// Each time's own offset.
    Offset,
}

/// A valid Olson name: letters, digits, `/`, `_`, `-`, `+`, no `..`.
fn olson(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && !name.starts_with('/')
        && !name.split('/').any(|p| p.is_empty() || p == "." || p == "..")
        && name.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'/' | b'_' | b'-' | b'+'))
}

impl Zone {
    /// From a machine's `time` section (null before RT10l), reading
    /// zoneinfo through `read`.
    pub fn of(time: Option<&Value>, read: &dyn Fn(&str) -> Option<Vec<u8>>) -> Zone {
        let Some(t) = time.filter(|v| v.as_object().is_some()) else { return Zone::Offset };
        let name = t.get("zone").and_then(Value::as_str).filter(|n| olson(n)).map(str::to_string);
        for way in ORDER {
            match way {
                Way::Zoneinfo => {
                    if let Some(n) = &name {
                        let path = format!("{}/{n}", zoneinfo_dir());
                        if let Some(rule) = read(&path).as_deref().and_then(tzif_rule).as_deref().and_then(Rule::parse) {
                            return Zone::Rule { name: Some(n.clone()), rule, way };
                        }
                    }
                }
                Way::Rule => {
                    if let Some(rule) = t.get("posix_rule").and_then(Value::as_str).and_then(Rule::parse) {
                        return Zone::Rule { name: name.clone(), rule, way };
                    }
                }
                Way::Offset => return Zone::Offset,
            }
        }
        Zone::Offset
    }

    /// A time as shown: `22 Nov 2026 02:00 CET`; `offset` is the one it
    /// was written with (0 for a `Z` time).
    pub fn show(&self, t: i64, offset: i32) -> String {
        match self {
            Zone::Utc => format!("{} UTC", civil(t, 0)),
            Zone::Rule { rule, .. } => {
                let (o, name) = rule.at(t);
                format!("{} {name}", civil(t, o))
            }
            Zone::Offset => format!("{} {}", civil(t, offset), offset_name(offset)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(s: &str) -> i64 {
        parse_rfc3339(s).unwrap().0
    }

    #[test]
    fn dates_both_ways() {
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(2000, 3, 1), 11_017);
        for z in [-800_000i64, -1, 0, 1, 11_016, 20_000, 2_932_896] {
            let (y, m, d) = civil_from_days(z);
            assert_eq!(days_from_civil(y, m, d), z);
        }
        assert_eq!(civil_from_days(days_from_civil(2024, 2, 29)), (2024, 2, 29));
    }

    #[test]
    fn rfc3339() {
        assert_eq!(parse_rfc3339("2026-11-22T01:00:11Z"), Some((1_795_309_211, 0)));
        assert_eq!(parse_rfc3339("2026-11-22T02:00:11.123456789+01:00"), Some((1_795_309_211, 3600)));
        assert_eq!(parse_rfc3339("2026-11-21T20:00:11-05:00"), Some((1_795_309_211, -18_000)));
        for bad in ["", "2026-11-22", "2026-13-01T00:00:00Z", "2026-11-22T01:00:11", "2026-11-22T01:00:11+0100", "2026-11-22T01:00:11Zjunk", "2026-11-22T01:00:11.Z", "x026-11-22T01:00:11Z"] {
            assert_eq!(parse_rfc3339(bad), None, "{bad}");
        }
    }

    #[test]
    fn shown_in_each_way() {
        let x = t("2026-11-22T01:00:11Z");
        assert_eq!(Zone::Utc.show(x, 0), "22 Nov 2026 01:00 UTC");
        assert_eq!(Zone::Offset.show(x, 3600), "22 Nov 2026 02:00 UTC+01:00");
        assert_eq!(Zone::Offset.show(x, -19_800), "21 Nov 2026 19:30 UTC-05:30");
        let berlin = Rule::parse("CET-1CEST,M3.5.0,M10.5.0/3").unwrap();
        let z = Zone::Rule { name: Some("Europe/Berlin".into()), rule: berlin.clone(), way: Way::Rule };
        assert_eq!(z.show(x, 0), "22 Nov 2026 02:00 CET");
        assert_eq!(z.show(t("2026-10-01T00:00:00Z"), 0), "01 Oct 2026 02:00 CEST");
        // The changes themselves: 2026-03-29 01:00Z and 2026-10-25 01:00Z.
        assert_eq!(berlin.at(t("2026-03-29T00:59:59Z")), (3600, "CET"));
        assert_eq!(berlin.at(t("2026-03-29T01:00:00Z")), (7200, "CEST"));
        assert_eq!(berlin.at(t("2026-10-25T00:59:59Z")), (7200, "CEST"));
        assert_eq!(berlin.at(t("2026-10-25T01:00:00Z")), (3600, "CET"));
        // The south: summer across the new year.
        let sydney = Rule::parse("AEST-10AEDT,M10.1.0,M4.1.0/3").unwrap();
        assert_eq!(sydney.at(t("2026-01-15T00:00:00Z")).1, "AEDT");
        assert_eq!(sydney.at(t("2026-06-15T00:00:00Z")).1, "AEST");
        let ny = Rule::parse("EST5EDT,M3.2.0,M11.1.0").unwrap();
        assert_eq!(Zone::Rule { name: None, rule: ny, way: Way::Rule }.show(t("2026-11-21T08:00:00Z"), 0), "21 Nov 2026 03:00 EST");
        let ist = Rule::parse("IST-5:30").unwrap();
        assert_eq!(ist.at(0), (19_800, "IST"));
        let quoted = Rule::parse("<+0330>-3:30").unwrap();
        assert_eq!(quoted.at(0), (12_600, "+0330"));
        for bad in ["", "C", "CET", "CET-1CEST,M3.5.0", "CET-1CEST,M13.5.0,M10.5.0", "CET-1CEST,M3.5.0,M10.5.0junk", "CET-99"] {
            assert_eq!(Rule::parse(bad), None, "{bad}");
        }
    }

    #[test]
    fn zoneinfo_then_the_rule_then_offsets() {
        let time = crate::json::parse(r#"{"zone": "Europe/Berlin", "posix_rule": "CET-1CEST,M3.5.0,M10.5.0/3"}"#).unwrap();
        let tzif = b"TZif2\0\0\0junk\nCET-1CEST,M3.5.0,M10.5.0/3\n".to_vec();
        let seen = std::cell::RefCell::new(Vec::new());
        let read = |p: &str| {
            seen.borrow_mut().push(p.to_string());
            Some(tzif.clone())
        };
        match Zone::of(Some(&time), &read) {
            Zone::Rule { name, way, .. } => assert_eq!((name.as_deref(), way), (Some("Europe/Berlin"), Way::Zoneinfo)),
            z => panic!("{z:?}"),
        }
        assert_eq!(seen.borrow()[0], format!("{}/Europe/Berlin", zoneinfo_dir()));
        let none = |_: &str| None;
        assert!(matches!(Zone::of(Some(&time), &none), Zone::Rule { way: Way::Rule, .. }));
        let bad = crate::json::parse(r#"{"zone": "../../etc/passwd", "posix_rule": null}"#).unwrap();
        let never = |p: &str| -> Option<Vec<u8>> { panic!("read {p}") };
        assert_eq!(Zone::of(Some(&bad), &never), Zone::Offset);
        assert_eq!(Zone::of(None, &never), Zone::Offset);
        assert_eq!(tzif_rule(b"TZif1...\nX\n"), None, "version 1 has no footer");
    }

    #[test]
    fn stamps() {
        assert_eq!(stamp(t("2026-11-23T14:26:30Z")), "20261123-142630");
    }
}
