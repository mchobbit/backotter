//! bko-rescue's command line.  The restore screens come in RT11 and RT12;
//! until then the hidden options are what it does.

use bko_rescue::spike;
use bko_rescue::term::ffi;
use bko_rescue::term::out::Lines;
use bko_rescue::term::profile::{self, Facts};
use bko_rescue::term::tty;
use bko_rescue::text;

const USAGE: &str = "\
usage: bko-rescue [--plain] [--lines utf8|dec|ascii] [--colour on|off]
                  [--holt PATH] [--rustic PATH] [--dry-run]
       bko-rescue --version | --help

  --plain          numbered questions, one line at a time
  --lines STYLE    box lines: utf8, dec or ascii (else BKO_RESCUE_LINES,
                   else the console's own)
  --colour on|off  colour or none (else BKO_RESCUE_COLOUR, else the console's)
  --holt PATH      the bko-holt to run (default: bko-holt on PATH)
  --rustic PATH    the rustic-bko to run (default: rustic-bko on PATH)
  --dry-run        go through the restore without writing to any disk
";

#[derive(Debug, PartialEq, Eq)]
enum Mode {
    App,
    Abi,
    CheckTty,
    Profile,
    Spike { panic: bool },
    Demo,
    Version,
    Help,
}

#[derive(Debug, PartialEq, Eq)]
struct Opts {
    mode: Mode,
    plain: bool,
    lines: Option<Lines>,
    colour: Option<bool>,
    holt: String,
    rustic: String,
    dry_run: bool,
}

/// The options that take no value: `--plain=no` is an error, not plain.
const NO_VALUE: &[&str] =
    &["--plain", "--dry-run", "--abi", "--check-tty", "--profile", "--demo", "--version", "--help"];

fn parse(args: &[String]) -> Result<Opts, String> {
    let mut o = Opts {
        mode: Mode::App,
        plain: false,
        lines: None,
        colour: None,
        holt: "bko-holt".to_string(),
        rustic: "rustic-bko".to_string(),
        dry_run: false,
    };
    let mut modes = 0;
    let mut it = args.iter().peekable();
    while let Some(a) = it.next() {
        let (name, inline) = match a.split_once('=') {
            Some((n, v)) if n.starts_with("--") => (n, Some(v.to_string())),
            _ => (a.as_str(), None),
        };
        let mut value = |what: &str| -> Result<String, String> {
            match inline.clone() {
                Some(v) => Ok(v),
                None => it.next().cloned().ok_or_else(|| format!("{name} needs {what}")),
            }
        };
        if inline.is_some() && NO_VALUE.contains(&name) {
            return Err(format!("{name} takes no value"));
        }
        let mut mode = |m: Mode, o: &mut Opts| {
            o.mode = m;
            modes += 1;
        };
        match name {
            "--plain" => o.plain = true,
            "--dry-run" => o.dry_run = true,
            "--lines" => {
                let v = value("utf8, dec or ascii")?;
                o.lines = Some(Lines::parse(&v).ok_or_else(|| format!("--lines {v}: utf8, dec or ascii"))?);
            }
            "--colour" | "--color" => {
                let v = value("on or off")?;
                o.colour = Some(profile::parse_colour(&v).ok_or_else(|| format!("--colour {v}: on or off"))?);
            }
            "--holt" => o.holt = value("a path")?,
            "--rustic" => o.rustic = value("a path")?,
            "--abi" => mode(Mode::Abi, &mut o),
            "--check-tty" => mode(Mode::CheckTty, &mut o),
            "--profile" => mode(Mode::Profile, &mut o),
            "--demo" => mode(Mode::Demo, &mut o),
            "--version" => mode(Mode::Version, &mut o),
            "--help" | "-h" => mode(Mode::Help, &mut o),
            "--spike" => {
                let panic = match inline.as_deref() {
                    Some("panic") => true,
                    Some(v) => return Err(format!("--spike={v}: only 'panic'")),
                    None => it.next_if(|n| *n == "panic").is_some(),
                };
                mode(Mode::Spike { panic }, &mut o);
            }
            _ => return Err(format!("unknown option {a}")),
        }
    }
    if modes > 1 {
        return Err("give one of --abi, --check-tty, --profile, --spike, --demo".to_string());
    }
    Ok(o)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let o = match parse(&args) {
        Ok(o) => o,
        Err(e) => {
            // The error quotes argv, which may hold anything.
            eprintln!("bko-rescue: {}\n{USAGE}", text::ascii(&e));
            std::process::exit(2);
        }
    };
    let code = match o.mode {
        Mode::Help => {
            print!("{USAGE}");
            0
        }
        Mode::Version => {
            println!("bko-rescue {}", env!("CARGO_PKG_VERSION"));
            0
        }
        Mode::Abi => {
            for l in ffi::abi_lines() {
                println!("{l}");
            }
            0
        }
        Mode::CheckTty => {
            let (lines, ok) = tty::check_tty();
            for l in lines {
                println!("{l}");
            }
            if ok {
                0
            } else {
                1
            }
        }
        Mode::Profile => {
            let f = Facts::gather(o.plain, o.lines, o.colour);
            let p = profile::decide(&f);
            for l in profile::describe(&f, &p, tty::size()) {
                println!("{l}");
            }
            0
        }
        Mode::Spike { panic } => {
            let f = Facts::gather(o.plain, o.lines, o.colour);
            let p = profile::decide(&f);
            match &p.plain {
                Some(why) => {
                    eprintln!("bko-rescue --spike needs a full-screen terminal, and this is plain mode: {why}");
                    2
                }
                None => spike::run(&p, panic),
            }
        }
        Mode::Demo => {
            eprintln!("bko-rescue: --demo isn't built yet (RT8)");
            2
        }
        Mode::App => {
            let _ = (&o.holt, &o.rustic, o.dry_run);
            eprintln!(
                "bko-rescue: the restore screens aren't built yet (RT11, RT12){}; \
                 --spike shows the terminal layer",
                if o.dry_run { ", nor --dry-run" } else { "" }
            );
            2
        }
    };
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(args: &[&str]) -> Result<Opts, String> {
        parse(&args.iter().map(|s| s.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn options() {
        let o = p(&["--plain", "--lines", "dec", "--colour=off", "--holt", "/x/bko-holt", "--dry-run"]).unwrap();
        assert!(o.plain && o.dry_run);
        assert_eq!((o.lines, o.colour, o.holt.as_str()), (Some(Lines::Dec), Some(false), "/x/bko-holt"));
        assert_eq!(o.mode, Mode::App);
        assert_eq!(p(&["--spike", "panic"]).unwrap().mode, Mode::Spike { panic: true });
        assert_eq!(p(&["--spike=panic"]).unwrap().mode, Mode::Spike { panic: true });
        assert_eq!(p(&["--spike", "--lines=ascii"]).unwrap().mode, Mode::Spike { panic: false });
        assert_eq!(p(&["--check-tty"]).unwrap().mode, Mode::CheckTty);
        assert_eq!(p(&["--rustic=/r"]).unwrap().rustic, "/r");
    }

    #[test]
    fn bad_options() {
        assert!(p(&["--lines", "fancy"]).is_err());
        assert!(p(&["--lines"]).is_err());
        assert!(p(&["--colour", "maybe"]).is_err());
        assert!(p(&["--abi", "--profile"]).is_err());
        assert!(p(&["--spike=twice"]).is_err());
        assert!(p(&["-x"]).is_err());
        assert_eq!(p(&["--plain=no"]), Err("--plain takes no value".to_string()));
        assert_eq!(p(&["--dry-run=false"]), Err("--dry-run takes no value".to_string()));
        assert!(p(&["--abi=x"]).is_err());
        assert!(p(&["--check-tty=1"]).is_err());
        assert!(p(&["--help="]).is_err());
    }
}
