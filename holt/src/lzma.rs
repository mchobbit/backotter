//! A raw LZMA (LZMA1) decoder, for what GRUB compresses with it: the
//! i386-pc core.img's kernel and modules (load/grubprefix.rs reads the
//! prefix from them).  No header: the caller gives the properties (lc, lp,
//! pb) and the uncompressed size, as GRUB's `lzma_decompress.img` has them.
//! The dictionary is the output itself (it is all kept), so no dictionary
//! size is needed.  It follows the LZMA specification's reference decoder
//! (the LZMA SDK's LzmaSpec.cpp): a range decoder, the literal, match and
//! rep coders, and the 12-state machine.  A stream that is corrupt, or
//! ends before SIZE bytes (without an end marker), is an Err.

const PROB_INIT: u16 = 1024;
const NUM_BIT_MODEL_TOTAL_BITS: u32 = 11;
const NUM_MOVE_BITS: u32 = 5;
const TOP: u32 = 1 << 24;
const NUM_STATES: usize = 12;
const POS_BITS_MAX: usize = 4;
const NUM_LEN_TO_POS_STATES: usize = 4;
const NUM_ALIGN_BITS: usize = 4;
const START_POS_MODEL_INDEX: u32 = 4;
const END_POS_MODEL_INDEX: u32 = 14;
const NUM_FULL_DISTANCES: usize = 1 << (END_POS_MODEL_INDEX >> 1);
const MATCH_MIN_LEN: usize = 2;

struct Range<'a> {
    b: &'a [u8],
    at: usize,
    range: u32,
    code: u32,
}

impl<'a> Range<'a> {
    fn new(b: &'a [u8]) -> Result<Self, String> {
        if b.len() < 5 {
            return Err("an LZMA stream of under 5 bytes".into());
        }
        if b[0] != 0 {
            return Err("an LZMA stream whose first byte isn't 0".into());
        }
        let code = u32::from_be_bytes([b[1], b[2], b[3], b[4]]);
        if code == u32::MAX {
            return Err("a corrupt LZMA stream (its first code is all ones)".into());
        }
        Ok(Range {
            b,
            at: 5,
            range: u32::MAX,
            code,
        })
    }

    fn byte(&mut self) -> Result<u8, String> {
        let c = *self.b.get(self.at).ok_or("the LZMA stream ends early")?;
        self.at += 1;
        Ok(c)
    }

    fn normalize(&mut self) -> Result<(), String> {
        if self.range < TOP {
            self.range <<= 8;
            self.code = (self.code << 8) | u32::from(self.byte()?);
        }
        Ok(())
    }

    fn direct(&mut self, mut n: u32) -> Result<u32, String> {
        let mut res = 0u32;
        while n > 0 {
            self.range >>= 1;
            self.code = self.code.wrapping_sub(self.range);
            let t = 0u32.wrapping_sub(self.code >> 31);
            self.code = self.code.wrapping_add(self.range & t);
            if self.code == self.range {
                return Err("a corrupt LZMA stream (direct bits)".into());
            }
            self.normalize()?;
            res = (res << 1).wrapping_add(t.wrapping_add(1));
            n -= 1;
        }
        Ok(res)
    }

    fn bit(&mut self, p: &mut u16) -> Result<u32, String> {
        let v = u32::from(*p);
        let bound = (self.range >> NUM_BIT_MODEL_TOTAL_BITS) * v;
        let sym = if self.code < bound {
            *p = (v + (((1 << NUM_BIT_MODEL_TOTAL_BITS) - v) >> NUM_MOVE_BITS)) as u16;
            self.range = bound;
            0
        } else {
            *p = (v - (v >> NUM_MOVE_BITS)) as u16;
            self.code -= bound;
            self.range -= bound;
            1
        };
        self.normalize()?;
        Ok(sym)
    }

    fn tree(&mut self, probs: &mut [u16], bits: u32) -> Result<u32, String> {
        let mut m = 1usize;
        for _ in 0..bits {
            m = (m << 1) + self.bit(&mut probs[m])? as usize;
        }
        Ok(m as u32 - (1 << bits))
    }

    fn reverse(&mut self, probs: &mut [u16], bits: u32) -> Result<u32, String> {
        let (mut m, mut sym) = (1usize, 0u32);
        for i in 0..bits {
            let b = self.bit(&mut probs[m])?;
            m = (m << 1) + b as usize;
            sym |= b << i;
        }
        Ok(sym)
    }
}

struct Len {
    choice: u16,
    choice2: u16,
    low: Vec<[u16; 1 << 3]>,
    mid: Vec<[u16; 1 << 3]>,
    high: [u16; 1 << 8],
}

impl Len {
    fn new() -> Self {
        Len {
            choice: PROB_INIT,
            choice2: PROB_INIT,
            low: vec![[PROB_INIT; 8]; 1 << POS_BITS_MAX],
            mid: vec![[PROB_INIT; 8]; 1 << POS_BITS_MAX],
            high: [PROB_INIT; 256],
        }
    }

    fn decode(&mut self, rc: &mut Range<'_>, pos_state: usize) -> Result<usize, String> {
        if rc.bit(&mut self.choice)? == 0 {
            return Ok(rc.tree(&mut self.low[pos_state], 3)? as usize);
        }
        if rc.bit(&mut self.choice2)? == 0 {
            return Ok(8 + rc.tree(&mut self.mid[pos_state], 3)? as usize);
        }
        Ok(16 + rc.tree(&mut self.high, 8)? as usize)
    }
}

/// SIZE bytes decoded from the raw LZMA stream B with properties LC, LP and
/// PB (GRUB's i386-pc core: 3, 0, 2).  An end marker before SIZE bytes, or
/// past them, ends the output there (Err when short).  SIZE bytes are
/// reserved up front, so the caller bounds it (`core_prefix` caps it at
/// `MAX_CORE`, 16 MiB).
///
/// # Errors
///
/// When LC, LP or PB is out of LZMA's range, when B is under 5 bytes or
/// doesn't start as a range coder's stream does, when the stream is
/// corrupt (a match reaching back before the output's start, bad direct
/// bits), and when it ends before SIZE bytes.
pub fn decode(b: &[u8], lc: u32, lp: u32, pb: u32, size: usize) -> Result<Vec<u8>, String> {
    if lc > 8 || lp > 4 || pb > 4 {
        return Err(format!(
            "LZMA properties lc={lc} lp={lp} pb={pb} out of range"
        ));
    }
    let mut rc = Range::new(b)?;
    let mut out: Vec<u8> = Vec::with_capacity(size);
    let mut lit = vec![PROB_INIT; 0x300usize << (lc + lp)];
    let mut pos_slot = vec![[PROB_INIT; 1 << 6]; NUM_LEN_TO_POS_STATES];
    let mut pos_dec = vec![PROB_INIT; 1 + NUM_FULL_DISTANCES - END_POS_MODEL_INDEX as usize];
    let mut align = [PROB_INIT; 1 << NUM_ALIGN_BITS];
    let mut is_match = vec![PROB_INIT; NUM_STATES << POS_BITS_MAX];
    let mut is_rep = [PROB_INIT; NUM_STATES];
    let mut is_rep_g0 = [PROB_INIT; NUM_STATES];
    let mut is_rep_g1 = [PROB_INIT; NUM_STATES];
    let mut is_rep_g2 = [PROB_INIT; NUM_STATES];
    let mut is_rep0_long = vec![PROB_INIT; NUM_STATES << POS_BITS_MAX];
    let (mut len_dec, mut rep_len_dec) = (Len::new(), Len::new());
    let (mut rep0, mut rep1, mut rep2, mut rep3) = (0usize, 0usize, 0usize, 0usize);
    let mut state = 0usize;
    let (pb_mask, lp_mask) = ((1usize << pb) - 1, (1usize << lp) - 1);
    while out.len() < size {
        let pos_state = out.len() & pb_mask;
        if rc.bit(&mut is_match[(state << POS_BITS_MAX) + pos_state])? == 0 {
            let prev = out.last().copied().unwrap_or(0) as usize;
            let ls = ((out.len() & lp_mask) << lc) + (prev >> (8 - lc));
            let probs = &mut lit[0x300 * ls..0x300 * (ls + 1)];
            let mut sym = 1usize;
            if state >= 7 {
                let mut mb = *out
                    .get(out.len().wrapping_sub(rep0 + 1))
                    .ok_or("a corrupt LZMA stream (a literal's match byte before the start)")?
                    as usize;
                while sym < 0x100 {
                    let m = (mb >> 7) & 1;
                    mb <<= 1;
                    let bit = rc.bit(&mut probs[((1 + m) << 8) + sym])? as usize;
                    sym = (sym << 1) | bit;
                    if m != bit {
                        break;
                    }
                }
            }
            while sym < 0x100 {
                sym = (sym << 1) | rc.bit(&mut probs[sym])? as usize;
            }
            out.push((sym - 0x100) as u8);
            state = if state < 4 {
                0
            } else if state < 10 {
                state - 3
            } else {
                state - 6
            };
            continue;
        }
        let len;
        if rc.bit(&mut is_rep[state])? != 0 {
            if out.is_empty() {
                return Err("a corrupt LZMA stream (a rep match at the start)".into());
            }
            if rc.bit(&mut is_rep_g0[state])? == 0 {
                if rc.bit(&mut is_rep0_long[(state << POS_BITS_MAX) + pos_state])? == 0 {
                    state = if state < 7 { 9 } else { 11 };
                    let c = *out
                        .get(out.len().wrapping_sub(rep0 + 1))
                        .ok_or("a corrupt LZMA stream (a short rep before the start)")?;
                    out.push(c);
                    continue;
                }
            } else {
                let dist;
                if rc.bit(&mut is_rep_g1[state])? == 0 {
                    dist = rep1;
                } else {
                    if rc.bit(&mut is_rep_g2[state])? == 0 {
                        dist = rep2;
                    } else {
                        dist = rep3;
                        rep3 = rep2;
                    }
                    rep2 = rep1;
                }
                rep1 = rep0;
                rep0 = dist;
            }
            len = rep_len_dec.decode(&mut rc, pos_state)?;
            state = if state < 7 { 8 } else { 11 };
        } else {
            rep3 = rep2;
            rep2 = rep1;
            rep1 = rep0;
            len = len_dec.decode(&mut rc, pos_state)?;
            state = if state < 7 { 7 } else { 10 };
            // The distance.
            let ls = len.min(NUM_LEN_TO_POS_STATES - 1);
            let slot = rc.tree(&mut pos_slot[ls], 6)?;
            let dist: u32 = if slot < START_POS_MODEL_INDEX {
                slot
            } else {
                let nd = (slot >> 1) - 1;
                let base = (2 | (slot & 1)) << nd;
                if slot < END_POS_MODEL_INDEX {
                    let off = (base - slot) as usize;
                    base + rc.reverse(&mut pos_dec[off..], nd)?
                } else {
                    base.wrapping_add(rc.direct(nd - NUM_ALIGN_BITS as u32)? << NUM_ALIGN_BITS)
                        .wrapping_add(rc.reverse(&mut align, NUM_ALIGN_BITS as u32)?)
                }
            };
            if dist == u32::MAX {
                break; // the end marker
            }
            rep0 = dist as usize;
            if rep0 >= out.len() {
                return Err(format!(
                    "a corrupt LZMA stream (a match {} back at byte {})",
                    rep0 + 1,
                    out.len()
                ));
            }
        }
        let n = (len + MATCH_MIN_LEN).min(size - out.len());
        let from = out.len() - rep0 - 1;
        for i in 0..n {
            let c = out[from + i];
            out.push(c);
        }
    }
    if out.len() < size {
        return Err(format!(
            "the LZMA stream ends after {} of {size} bytes",
            out.len()
        ));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The plain text the fixtures hold compressed: text with repeats near
    /// and far (literals, matches, reps, long distances), from a fixed LCG.
    pub(crate) fn plain() -> Vec<u8> {
        let mut x: u32 = 12345;
        let mut next = || {
            x = x.wrapping_mul(1103515245).wrapping_add(12345);
            (x >> 16) & 0x7fff
        };
        let words = [
            "grub",
            "prefix",
            "(,gpt3)",
            "/@/boot/grub",
            "normal.mod",
            "biosdisk",
            "part_gpt",
            "btrfs",
            "\0\0\0\0",
            "mimg",
        ];
        let mut out = Vec::new();
        while out.len() < 70000 {
            let r = next();
            match r % 7 {
                0..=2 => out.extend_from_slice(words[(r as usize / 7) % words.len()].as_bytes()),
                3 => out.push((r >> 3) as u8),
                4 if out.len() > 5000 => {
                    let back = 1 + (next() as usize * 3) % (out.len() - 1).min(60000);
                    let n = 2 + (next() as usize % 300);
                    let from = out.len() - back;
                    for i in 0..n {
                        let c = out[from + i % back];
                        out.push(c);
                    }
                }
                _ => out.push(b' '),
            }
        }
        out.truncate(70000);
        out
    }

    /// `python3 -c 'import lzma; ...'` (tests/fixtures/lzma/README.md): liblzma's raw
    /// LZMA1, lc=3 lp=0 pb=2, a 64 KiB dictionary (GRUB's mkimage settings),
    /// with its end marker.
    const RAW: &[u8] = include_bytes!("../tests/fixtures/lzma/plain.lzma1");

    #[test]
    fn decodes_liblzma_raw_lzma1() {
        let p = plain();
        assert_eq!(decode(RAW, 3, 0, 2, p.len()).unwrap(), p);
        // Fewer bytes asked: the first ones.
        assert_eq!(decode(RAW, 3, 0, 2, 1000).unwrap(), p[..1000]);
        // More: the end marker ends it, short.
        assert!(
            decode(RAW, 3, 0, 2, p.len() + 1)
                .unwrap_err()
                .contains("ends after 70000")
        );
    }

    #[test]
    fn refuses_what_is_cut_or_corrupt() {
        let p = plain();
        assert!(decode(&RAW[..RAW.len() / 2], 3, 0, 2, p.len()).is_err());
        assert!(
            decode(&[1, 0, 0, 0, 0, 0], 3, 0, 2, 1)
                .unwrap_err()
                .contains("first byte")
        );
        assert!(decode(&[0, 0, 0], 3, 0, 2, 1).is_err());
        assert!(decode(RAW, 9, 0, 2, 1).is_err());
        // Bytes flipped: an Err or other bytes, never a panic.
        for at in [7usize, 100, 1000, RAW.len() - 10] {
            let mut b = RAW.to_vec();
            b[at] ^= 0x5a;
            if let Ok(o) = decode(&b, 3, 0, 2, p.len()) {
                assert_ne!(o, p, "{at}");
            }
        }
        assert_eq!(decode(RAW, 3, 0, 2, 0).unwrap(), Vec::<u8>::new());
    }
}
