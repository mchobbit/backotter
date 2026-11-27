# Sourced after audit-lib.sh by rust-lint.sh and stock-baseline.sh (on the
# Mac): the audits' hosts from testvm/hosts.json, probed and ranked (F10 of
# docs/design/img-speed.md: they took Chopin and Cinqcent by name).  Apart
# from audit-lib.sh so that fork-tests.sh, a gate layer's harness, keeps its
# inputs.  Not run by itself.

# shellcheck source=../hosts.sh
source "$repo/testvm/hosts.sh"
dest_of() {  # NAME -> its ssh destination: the host table's for a row, else host_of's
    local d
    if [[ $1 != *[@.]* ]] && d=$(hosts_get "$1" ssh 2>/dev/null) && [[ -n $d ]]; then echo "$d"; else host_of "$1"; fi
}

# probe_hosts DIR NAME...: audit-hosts.py's probe on each host, in parallel,
# into DIR/NAME.txt (an empty or cut-short file: no answer).
probe_hosts() {
    local d=$1 c; shift
    mkdir -p "$d"
    for c in "$@"; do
        python3 -I -B "$here/audit-hosts.py" probe | "${SSH[@]}" "$(dest_of "$c")" sh -s >"$d/$c.txt" 2>/dev/null &
    done
    wait
}

# rank_hosts NAME...: those of the host table's rows best first, by pick-host's
# room for RESERVE (it leaves out a host that can never hold it), then the
# others in the order given.  Exit 1 when none is left.
rank_hosts() {
    local c intable= r out=
    for c in "$@"; do hosts_get "$c" name >/dev/null 2>&1 && intable+="${intable:+,}$c"; done
    if [[ $# -gt 1 && -n $intable ]]; then
        r=$("$repo/testvm/pick-host" --hosts "$intable" --reserve "$RESERVE_DISK" "$RESERVE_MEM" --all --field name) || return 1
        out=$(echo $r)
    elif [[ -n $intable ]]; then
        out=$intable
    fi
    for c in "$@"; do [[ ,$intable, == *",$c,"* ]] || out+="${out:+ }$c"; done
    [[ -n $out ]] && echo "$out"
}
