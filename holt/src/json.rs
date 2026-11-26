//! A small JSON value: built by the probes, printed as the report, and parsed
//! back where the input is itself JSON (a LUKS2 header's JSON area, and the
//! tests reading the report).  Objects keep their keys in insertion order so
//! the report reads the same way every run.

use std::fmt::Write as _;

/// Equality is by value: `Int(n)` and `UInt(n)` are the same number for any
/// `n >= 0` (the reader makes `UInt` of every non-negative integer, the probes
/// often build `Int`), so a field read back from a dump compares equal to the
/// same field probed again.  `Float` equals only `Float`.
#[derive(Clone, Debug)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    UInt(u64),
    Float(f64),
    Str(String),
    Arr(Vec<Value>),
    Obj(Vec<(String, Value)>),
}

impl PartialEq for Value {
    fn eq(&self, other: &Value) -> bool {
        match (self, other) {
            (Value::Null, Value::Null) => true,
            (Value::Bool(a), Value::Bool(b)) => a == b,
            (Value::Int(a), Value::Int(b)) => a == b,
            (Value::UInt(a), Value::UInt(b)) => a == b,
            (Value::Int(a), Value::UInt(b)) | (Value::UInt(b), Value::Int(a)) => {
                u64::try_from(*a).is_ok_and(|a| a == *b)
            }
            (Value::Float(a), Value::Float(b)) => a == b,
            (Value::Str(a), Value::Str(b)) => a == b,
            (Value::Arr(a), Value::Arr(b)) => a == b,
            (Value::Obj(a), Value::Obj(b)) => a == b,
            _ => false,
        }
    }
}

impl Value {
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Obj(kv) => kv.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    /// `get` along a path of keys and array indexes ("partitions", "0", "uuid").
    pub fn at(&self, path: &[&str]) -> Option<&Value> {
        let mut v = self;
        for p in path {
            v = match v {
                Value::Arr(a) => a.get(p.parse::<usize>().ok()?)?,
                Value::Obj(_) => v.get(p)?,
                _ => return None,
            };
        }
        Some(v)
    }

    /// `get`, for writing: the value of KEY (its first, as `get` reads)
    /// when this is an object that has it.
    pub fn get_mut(&mut self, key: &str) -> Option<&mut Value> {
        match self {
            Value::Obj(kv) => kv.iter_mut().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    /// `at`, for writing: the value along PATH (keys and array indexes).
    pub fn at_mut(&mut self, path: &[&str]) -> Option<&mut Value> {
        let mut v = self;
        for p in path {
            v = match v {
                Value::Arr(a) => a.get_mut(p.parse::<usize>().ok()?)?,
                Value::Obj(_) => v.get_mut(p)?,
                _ => return None,
            };
        }
        Some(v)
    }

    /// Sets KEY of an object as `Obj::put` does: an earlier value of KEY is
    /// replaced in place, else KEY is added at the end.  Nothing happens
    /// to a value that isn't an object.
    pub fn put(&mut self, key: &str, v: impl Into<Value>) {
        if let Value::Obj(kv) = self {
            let v = v.into();
            match kv.iter_mut().find(|(k, _)| k == key) {
                Some(slot) => slot.1 = v,
                None => kv.push((key.to_string(), v)),
            }
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_u64(&self) -> Option<u64> {
        match *self {
            Value::UInt(n) => Some(n),
            Value::Int(n) if n >= 0 => Some(n as u64),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match *self {
            Value::Int(n) => Some(n),
            Value::UInt(n) if n <= i64::MAX as u64 => Some(n as i64),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match *self {
            Value::Bool(b) => Some(b),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&Vec<Value>> {
        match self {
            Value::Arr(a) => Some(a),
            _ => None,
        }
    }

    pub fn as_object(&self) -> Option<&Vec<(String, Value)>> {
        match self {
            Value::Obj(o) => Some(o),
            _ => None,
        }
    }

    pub fn as_array_mut(&mut self) -> Option<&mut Vec<Value>> {
        match self {
            Value::Arr(a) => Some(a),
            _ => None,
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Value::Null)
    }

    pub fn get_str(&self, key: &str) -> Option<&str> {
        self.get(key).and_then(Value::as_str)
    }

    pub fn get_u64(&self, key: &str) -> Option<u64> {
        self.get(key).and_then(Value::as_u64)
    }

    /// KEY's number as load reads a dump's numbers (layout, fit, sizes,
    /// write-table: RustAudit1 H7-09), in every spelling dump writes one:
    /// a JSON number (not negative, not a fraction), decimal text as u64's
    /// FromStr reads it (an optional '+'; LUKS2's JSON keeps its offsets
    /// and sizes as strings), or "0x" and hex digits only.  Nothing else
    /// (no "0X", no sign after "0x", no spaces).  get_u64 is the strict
    /// one: numbers only.
    pub fn get_u64_lenient(&self, key: &str) -> Option<u64> {
        match self.get(key)? {
            // from_str_radix takes a '+' too: "0x+1f" isn't a spelling.
            Value::Str(t) => t.strip_prefix("0x").map_or_else(
                || t.parse().ok(),
                |h| {
                    if h.bytes().all(|c| c.is_ascii_hexdigit()) {
                        u64::from_str_radix(h, 16).ok()
                    } else {
                        None
                    }
                },
            ),
            x => x.as_u64(),
        }
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        self.get(key).and_then(Value::as_bool)
    }

    pub fn get_arr(&self, key: &str) -> Option<&Vec<Value>> {
        self.get(key).and_then(Value::as_array)
    }

    pub fn str_at(&self, path: &[&str]) -> Option<&str> {
        self.at(path).and_then(Value::as_str)
    }

    pub fn u64_at(&self, path: &[&str]) -> Option<u64> {
        self.at(path).and_then(Value::as_u64)
    }

    pub fn bool_at(&self, path: &[&str]) -> Option<bool> {
        self.at(path).and_then(Value::as_bool)
    }

    pub fn arr_at(&self, path: &[&str]) -> &[Value] {
        self.at(path)
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }

    /// The report's layout: two-space indents, and arrays of scalars that fit
    /// on one line kept on one line.
    pub fn pretty(&self) -> String {
        let mut out = String::new();
        write_value(self, 0, &mut out);
        out.push('\n');
        out
    }

    pub fn compact(&self) -> String {
        let mut out = String::new();
        write_compact(self, &mut out);
        out
    }
}

macro_rules! from_uint {
    ($($t:ty),*) => {$(
        impl From<$t> for Value {
            fn from(n: $t) -> Value { Value::UInt(n as u64) }
        }
    )*};
}
macro_rules! from_int {
    ($($t:ty),*) => {$(
        impl From<$t> for Value {
            fn from(n: $t) -> Value { Value::Int(n as i64) }
        }
    )*};
}
from_uint!(u8, u16, u32, u64, usize);
from_int!(i8, i16, i32, i64);

impl From<bool> for Value {
    fn from(b: bool) -> Value {
        Value::Bool(b)
    }
}
impl From<f64> for Value {
    fn from(f: f64) -> Value {
        Value::Float(f)
    }
}
impl From<&str> for Value {
    fn from(s: &str) -> Value {
        Value::Str(s.to_string())
    }
}
impl From<String> for Value {
    fn from(s: String) -> Value {
        Value::Str(s)
    }
}
impl From<&String> for Value {
    fn from(s: &String) -> Value {
        Value::Str(s.clone())
    }
}
impl<T: Into<Value>> From<Option<T>> for Value {
    fn from(o: Option<T>) -> Value {
        o.map_or(Value::Null, Into::into)
    }
}
impl<T: Into<Value>> From<Vec<T>> for Value {
    fn from(v: Vec<T>) -> Value {
        Value::Arr(v.into_iter().map(Into::into).collect())
    }
}
impl From<Obj> for Value {
    fn from(o: Obj) -> Value {
        Value::Obj(o.0)
    }
}

/// Builds a JSON object in order.
#[derive(Default, Debug, Clone)]
pub struct Obj(pub Vec<(String, Value)>);

impl Obj {
    pub fn new() -> Obj {
        Obj(Vec::new())
    }
    /// Adds `key`, replacing an earlier value of the same key in place.
    pub fn set(mut self, key: &str, v: impl Into<Value>) -> Obj {
        self.put(key, v);
        self
    }
    pub fn put(&mut self, key: &str, v: impl Into<Value>) {
        let v = v.into();
        if let Some(slot) = self.0.iter_mut().find(|(k, _)| k == key) {
            slot.1 = v;
        } else {
            self.0.push((key.to_string(), v));
        }
    }
    /// Adds `key` only when `v` is `Some`.
    pub fn set_some<T: Into<Value>>(mut self, key: &str, v: Option<T>) -> Obj {
        if let Some(v) = v {
            self.put(key, v);
        }
        self
    }
    pub fn put_some<T: Into<Value>>(&mut self, key: &str, v: Option<T>) {
        if let Some(v) = v {
            self.put(key, v);
        }
    }
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
    pub fn build(self) -> Value {
        Value::Obj(self.0)
    }
}

pub fn escape(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || c == '\u{7f}' => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_scalar(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Int(n) => {
            let _ = write!(out, "{n}");
        }
        Value::UInt(n) => {
            let _ = write!(out, "{n}");
        }
        Value::Float(f) => {
            if f.is_finite() {
                let s = format!("{f}");
                out.push_str(&s);
                if !s.contains(['.', 'e', 'E']) {
                    out.push_str(".0");
                }
            } else {
                out.push_str("null");
            }
        }
        Value::Str(s) => escape(s, out),
        Value::Arr(_) | Value::Obj(_) => write_compact(v, out),
    }
}

fn is_scalar(v: &Value) -> bool {
    !matches!(v, Value::Arr(_) | Value::Obj(_))
}

const INLINE_WIDTH: usize = 100;

fn write_value(v: &Value, indent: usize, out: &mut String) {
    match v {
        Value::Arr(a) if a.is_empty() => out.push_str("[]"),
        Value::Obj(o) if o.is_empty() => out.push_str("{}"),
        Value::Arr(a) => {
            if a.iter().all(is_scalar) {
                let mut line = String::new();
                write_compact(v, &mut line);
                if line.len() + indent <= INLINE_WIDTH {
                    // write_compact has no spaces; add one after each comma
                    // between elements for readability.
                    out.push('[');
                    for (i, e) in a.iter().enumerate() {
                        if i > 0 {
                            out.push_str(", ");
                        }
                        write_scalar(e, out);
                    }
                    out.push(']');
                    return;
                }
            }
            out.push_str("[\n");
            for (i, e) in a.iter().enumerate() {
                pad(indent + 2, out);
                write_value(e, indent + 2, out);
                if i + 1 < a.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            pad(indent, out);
            out.push(']');
        }
        Value::Obj(o) => {
            out.push_str("{\n");
            for (i, (k, e)) in o.iter().enumerate() {
                pad(indent + 2, out);
                escape(k, out);
                out.push_str(": ");
                write_value(e, indent + 2, out);
                if i + 1 < o.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            pad(indent, out);
            out.push('}');
        }
        _ => write_scalar(v, out),
    }
}

fn write_compact(v: &Value, out: &mut String) {
    match v {
        Value::Arr(a) => {
            out.push('[');
            for (i, e) in a.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_compact(e, out);
            }
            out.push(']');
        }
        Value::Obj(o) => {
            out.push('{');
            for (i, (k, e)) in o.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                escape(k, out);
                out.push(':');
                write_compact(e, out);
            }
            out.push('}');
        }
        _ => write_scalar(v, out),
    }
}

fn pad(n: usize, out: &mut String) {
    for _ in 0..n {
        out.push(' ');
    }
}

// --- Parsing ------------------------------------------------------------------

/// Why TEXT isn't JSON, and where.  Its Display is "MESSAGE at byte OFFSET".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ParseError {
    pub offset: usize,
    pub message: &'static str,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} at byte {}", self.message, self.offset)
    }
}

impl std::error::Error for ParseError {}

const MAX_DEPTH: usize = 256;

pub fn parse(text: &str) -> Result<Value, ParseError> {
    let mut p = Parser {
        s: text.as_bytes(),
        i: 0,
        depth: 0,
    };
    p.ws();
    let v = p.value()?;
    p.ws();
    if p.i != p.s.len() {
        return Err(p.err("trailing characters"));
    }
    Ok(v)
}

struct Parser<'a> {
    s: &'a [u8],
    i: usize,
    depth: usize,
}

impl Parser<'_> {
    fn err(&self, message: &'static str) -> ParseError {
        ParseError {
            offset: self.i,
            message,
        }
    }
    fn ws(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.s.get(self.i) {
            self.i += 1;
        }
    }
    fn eat(&mut self, lit: &str) -> bool {
        if self.s[self.i..].starts_with(lit.as_bytes()) {
            self.i += lit.len();
            true
        } else {
            false
        }
    }
    fn value(&mut self) -> Result<Value, ParseError> {
        match self.s.get(self.i) {
            None => Err(self.err("unexpected end")),
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => Ok(Value::Str(self.string()?)),
            Some(b't') if self.eat("true") => Ok(Value::Bool(true)),
            Some(b'f') if self.eat("false") => Ok(Value::Bool(false)),
            Some(b'n') if self.eat("null") => Ok(Value::Null),
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(_) => Err(self.err("unexpected character")),
        }
    }
    fn enter(&mut self) -> Result<(), ParseError> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err(self.err("nested too deeply"));
        }
        Ok(())
    }
    fn object(&mut self) -> Result<Value, ParseError> {
        self.enter()?;
        self.i += 1;
        let mut kv = Vec::new();
        self.ws();
        if self.eat("}") {
            self.depth -= 1;
            return Ok(Value::Obj(kv));
        }
        loop {
            self.ws();
            if self.s.get(self.i) != Some(&b'"') {
                return Err(self.err("expected a key"));
            }
            let k = self.string()?;
            self.ws();
            if !self.eat(":") {
                return Err(self.err("expected ':'"));
            }
            self.ws();
            let v = self.value()?;
            kv.push((k, v));
            self.ws();
            if self.eat(",") {
                continue;
            }
            if self.eat("}") {
                break;
            }
            return Err(self.err("expected ',' or '}'"));
        }
        self.depth -= 1;
        Ok(Value::Obj(kv))
    }
    fn array(&mut self) -> Result<Value, ParseError> {
        self.enter()?;
        self.i += 1;
        let mut a = Vec::new();
        self.ws();
        if self.eat("]") {
            self.depth -= 1;
            return Ok(Value::Arr(a));
        }
        loop {
            self.ws();
            a.push(self.value()?);
            self.ws();
            if self.eat(",") {
                continue;
            }
            if self.eat("]") {
                break;
            }
            return Err(self.err("expected ',' or ']'"));
        }
        self.depth -= 1;
        Ok(Value::Arr(a))
    }
    fn hex4(&mut self) -> Result<u32, ParseError> {
        let h = self
            .s
            .get(self.i..self.i + 4)
            .ok_or_else(|| self.err("short \\u escape"))?;
        let h = std::str::from_utf8(h).map_err(|_| self.err("bad \\u escape"))?;
        let n = u32::from_str_radix(h, 16).map_err(|_| self.err("bad \\u escape"))?;
        self.i += 4;
        Ok(n)
    }
    fn string(&mut self) -> Result<String, ParseError> {
        self.i += 1; // opening quote
        let mut out: Vec<u8> = Vec::new();
        loop {
            let c = *self
                .s
                .get(self.i)
                .ok_or_else(|| self.err("unterminated string"))?;
            self.i += 1;
            match c {
                b'"' => break,
                b'\\' => {
                    let e = *self
                        .s
                        .get(self.i)
                        .ok_or_else(|| self.err("unterminated escape"))?;
                    self.i += 1;
                    let ch = match e {
                        b'"' => '"',
                        b'\\' => '\\',
                        b'/' => '/',
                        b'b' => '\u{8}',
                        b'f' => '\u{c}',
                        b'n' => '\n',
                        b'r' => '\r',
                        b't' => '\t',
                        b'u' => {
                            let hi = self.hex4()?;
                            let cp = if (0xd800..0xdc00).contains(&hi) {
                                if !self.eat("\\u") {
                                    return Err(self.err("lone surrogate"));
                                }
                                let lo = self.hex4()?;
                                if !(0xdc00..0xe000).contains(&lo) {
                                    return Err(self.err("bad surrogate pair"));
                                }
                                0x10000 + ((hi - 0xd800) << 10) + (lo - 0xdc00)
                            } else if (0xdc00..0xe000).contains(&hi) {
                                return Err(self.err("lone surrogate"));
                            } else {
                                hi
                            };
                            char::from_u32(cp).ok_or_else(|| self.err("bad code point"))?
                        }
                        _ => return Err(self.err("bad escape")),
                    };
                    let mut buf = [0u8; 4];
                    out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                }
                c if c < 0x20 => return Err(self.err("control character in string")),
                c => out.push(c),
            }
        }
        String::from_utf8(out).map_err(|_| self.err("invalid UTF-8"))
    }
    fn number(&mut self) -> Result<Value, ParseError> {
        let start = self.i;
        if self.s.get(self.i) == Some(&b'-') {
            self.i += 1;
        }
        let digits = |p: &mut Parser<'_>| {
            let s = p.i;
            while let Some(b'0'..=b'9') = p.s.get(p.i) {
                p.i += 1;
            }
            p.i - s
        };
        let int_start = self.i;
        if digits(self) == 0 {
            return Err(self.err("expected digits"));
        }
        if self.s[int_start] == b'0' && self.i - int_start > 1 {
            return Err(self.err("leading zero"));
        }
        let mut float = false;
        if self.s.get(self.i) == Some(&b'.') {
            self.i += 1;
            float = true;
            if digits(self) == 0 {
                return Err(self.err("expected fraction digits"));
            }
        }
        if let Some(b'e' | b'E') = self.s.get(self.i) {
            self.i += 1;
            float = true;
            if let Some(b'+' | b'-') = self.s.get(self.i) {
                self.i += 1;
            }
            if digits(self) == 0 {
                return Err(self.err("expected exponent digits"));
            }
        }
        let text =
            std::str::from_utf8(&self.s[start..self.i]).map_err(|_| self.err("bad number"))?;
        if !float {
            if let Ok(n) = text.parse::<u64>() {
                return Ok(Value::UInt(n));
            }
            if let Ok(n) = text.parse::<i64>() {
                return Ok(Value::Int(n));
            }
        }
        text.parse::<f64>()
            .map(Value::Float)
            .map_err(|_| self.err("bad number"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_error_display() {
        let e = parse("[1,").unwrap_err();
        assert_eq!(e.to_string(), format!("{} at byte 3", e.message));
        let d: &dyn std::error::Error = &e;
        assert_eq!(d.to_string(), e.to_string());
    }

    #[test]
    fn scalars_print() {
        assert_eq!(Value::Null.compact(), "null");
        assert_eq!(Value::Bool(true).compact(), "true");
        assert_eq!(Value::Bool(false).compact(), "false");
        assert_eq!(Value::Int(-5).compact(), "-5");
        assert_eq!(Value::UInt(u64::MAX).compact(), "18446744073709551615");
        assert_eq!(Value::Float(1.5).compact(), "1.5");
        assert_eq!(Value::Float(2.0).compact(), "2.0");
        assert_eq!(Value::Float(f64::NAN).compact(), "null");
        assert_eq!(Value::Float(f64::INFINITY).compact(), "null");
    }

    #[test]
    fn numbers_compare_by_value() {
        // RustAudit1 H1-03: the reader's UInt and a probe's Int are one number.
        assert_eq!(parse("5").unwrap(), Value::from(5i32));
        assert_eq!(Value::Int(0), Value::UInt(0));
        assert_eq!(Value::UInt(7), Value::Int(7));
        assert_eq!(
            parse("[1,{\"a\":2}]").unwrap(),
            Value::Arr(vec![
                Value::Int(1),
                Value::Obj(vec![("a".into(), Value::Int(2))])
            ])
        );
        assert_ne!(Value::Int(-1), Value::UInt(u64::MAX));
        assert_ne!(Value::Int(5), Value::UInt(6));
        assert_ne!(Value::Int(5), Value::Float(5.0));
        assert_ne!(Value::Int(0), Value::Null);
        assert_ne!(Value::Float(f64::NAN), Value::Float(f64::NAN));
        assert_eq!(Value::Str("x".into()), Value::from("x"));
    }

    #[test]
    fn strings_escape() {
        let v = Value::from("a\"b\\c\nd\re\tf\u{8}\u{c}\u{1}\u{7f}é😀");
        assert_eq!(
            v.compact(),
            "\"a\\\"b\\\\c\\nd\\re\\tf\\b\\f\\u0001\\u007fé😀\""
        );
        assert_eq!(parse(&v.compact()).unwrap(), v);
    }

    #[test]
    fn pretty_layout() {
        let v = Obj::new()
            .set("a", 1u32)
            .set("list", vec!["x", "y"])
            .set("empty", Value::Arr(vec![]))
            .set("eo", Obj::new())
            .set("nested", Obj::new().set("k", Value::Null))
            .set("objs", vec![Obj::new().set("n", 1u8).build()])
            .build();
        let expected = "{\n  \"a\": 1,\n  \"list\": [\"x\", \"y\"],\n  \"empty\": [],\n  \"eo\": {},\n  \"nested\": {\n    \"k\": null\n  },\n  \"objs\": [\n    {\n      \"n\": 1\n    }\n  ]\n}\n";
        assert_eq!(v.pretty(), expected);
        assert_eq!(parse(&v.pretty()).unwrap(), v);
    }

    #[test]
    fn long_scalar_arrays_break_lines() {
        let words: Vec<String> = (0..40).map(|i| format!("feature{i}")).collect();
        let v = Value::from(words.clone());
        let p = v.pretty();
        assert!(p.starts_with("[\n  \"feature0\",\n"));
        assert_eq!(parse(&p).unwrap(), v);
    }

    #[test]
    fn u64_readers() {
        let v = parse(r#"{"n": 7, "neg": -1, "dec": "2048", "plus": "+5", "hex": "0x1f", "HEX": "0X1f", "bad": "12a", "sp": " 1", "f": 1.5, "b": true, "tneg": "-1", "junk": "12abc", "hplus": "0x+1f", "hneg": "0x-1", "h0": "0x", "e": "", "big": "18446744073709551616"}"#).unwrap();
        let all = |f: fn(&Value, &str) -> Option<u64>| {
            [
                "n", "neg", "dec", "plus", "hex", "HEX", "bad", "sp", "f", "b", "none", "tneg",
                "junk", "hplus", "hneg", "h0", "e", "big",
            ]
            .map(|k| f(&v, k))
        };
        let no = [None; 7];
        assert_eq!(
            all(Value::get_u64).to_vec(),
            [
                [
                    Some(7),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None
                ]
                .as_slice(),
                &no
            ]
            .concat()
        );
        assert_eq!(
            all(Value::get_u64_lenient).to_vec(),
            [
                [
                    Some(7),
                    None,
                    Some(2048),
                    Some(5),
                    Some(31),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None
                ]
                .as_slice(),
                &no
            ]
            .concat()
        );
    }

    #[test]
    fn mutable_accessors() {
        let mut v = parse(r#"{"a": {"b": [1, {"c": "x"}]}, "d": 2}"#).unwrap();
        *v.at_mut(&["a", "b", "1", "c"]).unwrap() = Value::from("y");
        assert_eq!(
            v.at(&["a", "b", "1", "c"]).and_then(Value::as_str),
            Some("y")
        );
        assert!(v.at_mut(&["a", "b", "2"]).is_none());
        assert!(v.at_mut(&["a", "b", "x"]).is_none());
        assert!(v.at_mut(&["d", "e"]).is_none());
        assert_eq!(v.at_mut(&[]).map(|x| x.compact()), Some(v.compact()));
        v.get_mut("a")
            .and_then(|a| a.get_mut("b"))
            .and_then(Value::as_array_mut)
            .unwrap()
            .push(Value::Null);
        assert_eq!(v.arr_at(&["a", "b"]).len(), 3);
        // put replaces in place, else adds at the end; not an object: nothing.
        v.put("a", 1u64);
        v.put("e", "z");
        assert_eq!(v.compact(), r#"{"a":1,"d":2,"e":"z"}"#);
        let mut n = Value::from(3u64);
        n.put("a", 1u64);
        assert_eq!(n, Value::from(3u64));
        assert!(n.get_mut("a").is_none() && n.as_array_mut().is_none());
    }

    #[test]
    fn obj_set_replaces_in_place() {
        let o = Obj::new().set("a", 1u8).set("b", 2u8).set("a", 3u8);
        assert_eq!(o.build().compact(), "{\"a\":3,\"b\":2}");
        let o = Obj::new()
            .set_some("x", None::<u8>)
            .set_some("y", Some("z"));
        assert_eq!(o.build().compact(), "{\"y\":\"z\"}");
    }

    #[test]
    fn option_and_vec_into() {
        assert_eq!(Value::from(None::<u32>), Value::Null);
        assert_eq!(Value::from(Some(7u32)), Value::UInt(7));
        assert_eq!(
            Value::from(vec![1u8, 2]),
            Value::Arr(vec![Value::UInt(1), Value::UInt(2)])
        );
        assert_eq!(Value::from(-3i32), Value::Int(-3));
    }

    #[test]
    fn parse_basics() {
        let v = parse(r#" { "a" : [1, -2, 3.5, 1e3, true, false, null, "s"], "b": {} } "#).unwrap();
        assert_eq!(v.at(&["a", "0"]), Some(&Value::UInt(1)));
        assert_eq!(v.at(&["a", "1"]), Some(&Value::Int(-2)));
        assert_eq!(v.at(&["a", "2"]), Some(&Value::Float(3.5)));
        assert_eq!(v.at(&["a", "3"]), Some(&Value::Float(1000.0)));
        assert_eq!(v.at(&["a", "4"]).and_then(Value::as_bool), Some(true));
        assert!(v.at(&["a", "6"]).unwrap().is_null());
        assert_eq!(v.at(&["a", "7"]).and_then(Value::as_str), Some("s"));
        assert_eq!(v.get("b"), Some(&Value::Obj(vec![])));
        assert_eq!(v.at(&["a", "99"]), None);
        assert_eq!(v.at(&["a", "x"]), None);
    }

    #[test]
    fn parse_big_numbers() {
        assert_eq!(
            parse("18446744073709551615").unwrap(),
            Value::UInt(u64::MAX)
        );
        assert_eq!(parse("-9223372036854775808").unwrap(), Value::Int(i64::MIN));
        assert_eq!(
            parse("18446744073709551616").unwrap(),
            Value::Float(18446744073709551616.0)
        );
    }

    #[test]
    fn parse_unicode_escapes() {
        assert_eq!(
            parse(r#""\u00e9\ud83d\ude00\/""#).unwrap(),
            Value::from("é😀/")
        );
        assert!(parse(r#""\ud83d""#).is_err());
        assert!(parse(r#""\ude00""#).is_err());
        assert!(parse(r#""\ud83d\u0041""#).is_err());
        assert!(parse(r#""\u12""#).is_err());
        assert!(parse(r#""\uzzzz""#).is_err());
    }

    #[test]
    fn parse_rejects_bad_input() {
        for bad in [
            "",
            "{",
            "[1,]",
            "{\"a\" 1}",
            "{\"a\":1,}",
            "01",
            "1.",
            "1e",
            "-",
            "tru",
            "\"abc",
            "\"a\u{1}\"",
            "[1 2]",
            "{1:2}",
            "\"\\x\"",
            "1 2",
            "nul",
            ".5",
        ] {
            assert!(parse(bad).is_err(), "accepted {bad:?}");
        }
    }

    #[test]
    fn parse_depth_limit() {
        let deep = "[".repeat(MAX_DEPTH + 1) + &"]".repeat(MAX_DEPTH + 1);
        assert_eq!(parse(&deep).unwrap_err().message, "nested too deeply");
        let ok = "[".repeat(MAX_DEPTH) + &"]".repeat(MAX_DEPTH);
        assert!(parse(&ok).is_ok());
    }

    #[test]
    fn accessors() {
        assert_eq!(Value::Int(4).as_u64(), Some(4));
        assert_eq!(Value::Int(-4).as_u64(), None);
        assert_eq!(Value::UInt(4).as_i64(), Some(4));
        assert_eq!(Value::UInt(u64::MAX).as_i64(), None);
        assert_eq!(Value::Null.as_str(), None);
        assert_eq!(Value::Null.as_array(), None);
        assert_eq!(Value::Null.as_object(), None);
        assert_eq!(Value::Null.get("x"), None);
    }
}
