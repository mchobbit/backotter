#!/bin/sh
# Record the metadata of everything below ROOT, for comparing a FreeBSD
# system before and after a restore (FreeBSD's base system has no Python for
# ../fsmeta.py).  Into OUTDIR:
#   spec           mtree(8) spec: type, owner, group, mode, file flags, link
#                  count, size, mtime, symlink target, SHA-256
#   acls.txt       getfacl(1) of every file but symlinks
#   extattrs.txt   every extended attribute, both namespaces, as an MD5 of its
#                  value
#   hardlinks.txt  the names of each file with more than one, grouped
#   sparse.txt     files whose allocated blocks are under half their size
#
# Usage: fsmeta.sh ROOT OUTDIR
# Then:  cd ROOT2 && mtree -f OUTDIR/spec -X OUTDIR/mtree-exclude
#        lists what differs in ROOT2.  UFS's journal, .sujournal, is left out:
#        it belongs to the file system, which newfs -j makes anew.

set -eu
[ $# -eq 2 ] || { echo "usage: $0 ROOT OUTDIR" >&2; exit 1; }
out=$(mkdir -p "$2" && cd "$2" && pwd)
cd "$1"

echo .sujournal >"$out/mtree-exclude"
mtree -c -n -p . -X "$out/mtree-exclude" \
    -k type,uid,gid,mode,flags,nlink,size,time,link,sha256digest >"$out/spec"

find . ! -type l -print0 | sort -z | xargs -0 getfacl >"$out/acls.txt" 2>&1 || true

: >"$out/extattrs.txt"
tab=$(printf '\t')
for ns in user system; do
    find . -print0 | sort -z | xargs -0 lsextattr -f -h $ns 2>/dev/null |
    while IFS=$tab read -r path names; do
        [ -n "$names" ] || continue
        for name in $(printf '%s\n' $names | sort); do
            echo "$ns $path $name $(getextattr -f -h -q -x $ns "$name" "$path" | md5)"
        done
    done >>"$out/extattrs.txt"
done

find . -type f -links +1 -print0 | xargs -0 stat -f '%i %N' | sort -n |
    awk '{ i = $1; n = substr($0, length($1) + 2); g[i] = (i in g) ? g[i] " | " n : n }
         END { for (i in g) print g[i] }' | sort >"$out/hardlinks.txt"

find . -type f -size +1M -print0 | xargs -0 stat -f '%b %z %N' |
    awk '$1 * 512 < $2 / 2 { print substr($0, length($1) + length($2) + 3) }' | sort >"$out/sparse.txt"
