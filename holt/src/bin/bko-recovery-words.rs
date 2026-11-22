//! bko-recovery-words: BackOtter's recovery keys (bko_holt::recovery) by
//! hand: make one, or check one as someone would type it back.  Built with
//! bko-holt but not shipped: build-holt.sh and the images take bko-holt
//! alone.

use bko_holt::recovery::{self, Words};
use std::io::{IsTerminal, Read, Write};
use std::process::ExitCode;

const USAGE: &str = "Usage: bko-recovery-words new
       bko-recovery-words check [WORD...]
       bko-recovery-words -h | --help | -V | --version

A BackOtter recovery key is 12 words from BIP39's English list, chosen at
random: 128 bits from the system's random source and a 4-bit checksum, as
BIP39 makes them.  The words are the password of a second key of the
repository, which opens it without the passphrase.

  new     prints a new recovery key: its 12 words on one line, then numbered
  check   reads a recovery key as it would be typed back: in any case and
          spacing, each word in full or its first 4 letters or more (\"aban\"
          for abandon; the 3-letter words, such as act, in full), numbers
          before the words left out.  It prints OK: and the 12 words in full,
          which are the repository key's password; or what's wrong, with the
          word's place among those typed.  With no WORD it reads them from
          standard input, which keeps them out of the shell's history and
          `ps` (on a terminal, lines until there are 12 words, or an empty
          one).

Exit status: 0 for a new key or one that checks out, 1 when the words
aren't a recovery key (or there's no random source), 2 for bad arguments.
";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("new") if args.len() == 1 => new(),
        Some("new" | "check") if matches!(args.get(1).map(String::as_str), Some("-h" | "--help")) => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        Some("check") => check(&args[1..]),
        Some("-h" | "--help") if args.len() == 1 => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        Some("-V" | "--version") if args.len() == 1 => {
            println!("bko-recovery-words (bko-holt {})", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        Some("new") => usage_error("new takes no arguments"),
        // Not echoed: it may be the words, given without "check".
        Some(_) => usage_error("unknown arguments: say new or check"),
        None => usage_error("say new or check"),
    }
}

fn usage_error(why: &str) -> ExitCode {
    eprint!("bko-recovery-words: {why}\n\n{USAGE}");
    ExitCode::from(2)
}

/// `new`: the words on one line, then numbered, four to a row.
fn new() -> ExitCode {
    let w = match recovery::generate() {
        Ok(w) => w,
        Err(e) => {
            eprintln!("bko-recovery-words: {e}");
            return ExitCode::from(1);
        }
    };
    let mut shown = show_new(&w);
    print!("{shown}");
    recovery::zeroize_string(&mut shown);
    ExitCode::SUCCESS
}

fn show_new(w: &Words) -> String {
    let words = w.words();
    let mut pw = recovery::password(w);
    let mut s = format!("Recovery key: {pw}\n\n");
    recovery::zeroize_string(&mut pw);
    for row in words.chunks(4).enumerate() {
        let mut line: String = row.1.iter().enumerate().map(|(i, x)| format!("{:>4} {:<10}", 4 * row.0 + i + 1, x)).collect();
        s += line.trim_end();
        s.push('\n');
        recovery::zeroize_string(&mut line);
    }
    s += "\nWrite them down in this order.  Typed back, the first 4 letters of each\nword are enough.  Keep them as safe as the passphrase: they open the\nrepository without it.\n";
    s
}

/// `check [WORD...]`: the words from the arguments, or else from standard
/// input (on a terminal, lines until there are 12 words or one is empty).
fn check(args: &[String]) -> ExitCode {
    let mut text = String::new();
    if !args.is_empty() {
        text = args.join(" ");
    } else if std::io::stdin().is_terminal() {
        eprint!("Recovery key (12 words, on one line or more): ");
        let _ = std::io::stderr().flush();
        loop {
            let mut line = String::new();
            match std::io::stdin().read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {}
                Err(_) => {
                    recovery::zeroize_string(&mut line);
                    recovery::zeroize_string(&mut text);
                    eprintln!("bko-recovery-words: couldn't read the words");
                    return ExitCode::from(1);
                }
            }
            text += &line;
            let stop = line.trim().is_empty() || recovery::typed_count(&text) >= recovery::COUNT;
            recovery::zeroize_string(&mut line);
            if stop {
                break;
            }
        }
    } else if std::io::stdin().take(64 * 1024).read_to_string(&mut text).is_err() {
        recovery::zeroize_string(&mut text);
        eprintln!("bko-recovery-words: couldn't read the words from standard input");
        return ExitCode::from(1);
    }
    let res = recovery::check(&text);
    recovery::zeroize_string(&mut text);
    match res {
        Ok(w) => {
            let mut pw = recovery::password(&w);
            println!("OK: {pw}");
            recovery::zeroize_string(&mut pw);
            ExitCode::SUCCESS
        }
        Err(p) => {
            for line in p.to_string().lines() {
                eprintln!("bko-recovery-words: {line}");
            }
            ExitCode::from(1)
        }
    }
}
