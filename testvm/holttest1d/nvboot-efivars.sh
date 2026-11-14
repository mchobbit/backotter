# Sourced into ht1.sh's NVBOOT, run in a guest without efibootmgr (Guix
# System): the entry the firmware booted through, read from efivarfs, as one
# line in efibootmgr -v's shape: "BootXXXX* DESCRIPTION<TAB>PATH", PATH's
# nodes HD(PARTITION) for a hard-drive node and File(PATH) for a file path
# node (others as Node(TYPE,SUBTYPE)).  POSIX sh, od and awk only.
v=${NVBOOT_EFIVARS:-/sys/firmware/efi/efivars}   # the variable for its test only
g=8be4df61-93ca-11d2-aa0d-00e098032b8c
[ -r "$v/BootCurrent-$g" ] || { echo "no efibootmgr, no BootCurrent in efivarfs"; exit 0; }
c=$(od -An -tx1 -v -j4 -N2 "$v/BootCurrent-$g" | awk '{ printf "%s%s", toupper($2), toupper($1) }')
f=$v/Boot$c-$g
[ -r "$f" ] || { echo "BootCurrent $c, no such entry"; exit 0; }
od -An -tx1 -v -j4 "$f" | awk -v n="$c" '
function hex(s,   i, r) { r = 0; s = tolower(s); for (i = 1; i <= length(s); i++) r = r * 16 + index("0123456789abcdef", substr(s, i, 1)) - 1; return r }
function u16(i) { return hex(b[i + 1] b[i]) }
function u32(i) { return hex(b[i + 3] b[i + 2] b[i + 1] b[i]) }
function ucs2(i, end,   s, ch) { s = ""; for (; i + 1 <= end; i += 2) { ch = u16(i); if (ch == 0) break; s = s sprintf("%c", ch < 128 ? ch : 63) } return s }
{ for (i = 1; i <= NF; i++) b[++k] = $i }
END {
    attr = u32(1); fpl = u16(5)
    i = 7; d = ""
    while (i + 1 <= k) { ch = u16(i); i += 2; if (ch == 0) break; d = d sprintf("%c", ch < 128 ? ch : 63) }
    p = ""; end = i + fpl
    while (i + 3 < end) {
        t = hex(b[i]); s = hex(b[i + 1]); len = u16(i + 2)
        if (len < 4) break
        if (t == 127) break
        if (t == 4 && s == 1) node = "HD(" u32(i + 4) ")"
        else if (t == 4 && s == 4) node = "File(" ucs2(i + 4, i + len - 1) ")"
        else node = "Node(" t "," s ")"
        p = p (p == "" ? "" : "/") node
        i += len
    }
    printf "Boot%s%s %s\t%s\n", n, (attr % 2 ? "*" : ""), d, p
}'
exit 0
