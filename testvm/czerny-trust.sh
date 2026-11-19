#!/usr/bin/env bash
# Run on the Mac. Adds Czerny's keys to the farm, the way Cage's were added:
#  - m@czerny into m's authorized_keys on chopin, cinqcent, cage, copland, choubox and czerny itself
#  - root@czerny into root's authorized_keys on chopin
#  - Czerny's host keys into m's known_hosts on every peer (and root's on chopin)
#  - Czerny's own known_hosts gets cage's and its own host keys (it inherited Cage's file)
# Every edited file is backed up as <file>.bak-20261119-czerny first. Idempotent.
set -u
S=bak-20261119-czerny
MKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAXDsUOpoUaMCLUlWicn+fzwSBeokpWFnDTAANWQvJh3 m@czerny'
RKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBrJ+VIvV1bO/it7h6VW/nnc+V3mYUQMTzJDvWR5Lu/X root@czerny'
HK="czerny.local,czerny,192.168.68.52 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE7/zRGVYVVYlPbBrBwPOFYVe1kTFcNNO3DTnx56Qq4s
czerny.local,czerny,192.168.68.52 ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBGbhEZEuF0BiXzzAZyCH6C70WE2pxybfw4lFN4c3+AZZuYRDuUuswPSsH7suJvVOUD/FpsQ05botUFGkee2OK2I="

add() { # host user file line...
  local h=$1 u=$2 f=$3; shift 3
  local pre=""; [ "$u" = root ] && pre="sudo -n"
  printf '%s\n' "$@" | ssh -o BatchMode=yes -o ConnectTimeout=10 m@$h.local "
    f=$f; $pre test -e \$f && { $pre test -e \$f.$S || $pre cp -p \$f \$f.$S; }
    while IFS= read -r l; do $pre grep -qxF \"\$l\" \$f 2>/dev/null || echo \"\$l\" | $pre tee -a \$f >/dev/null; done
    $pre chmod 600 \$f" && echo "ok   $h $u $f" || echo "FAIL $h $u $f"
}

for h in chopin cinqcent cage copland choubox czerny; do
  add $h m /home/m/.ssh/authorized_keys "$MKEY"
  [ $h = czerny ] || add $h m /home/m/.ssh/known_hosts "$HK"
done
add chopin root /root/.ssh/authorized_keys "$RKEY"
add chopin root /root/.ssh/known_hosts "$HK"

# Czerny's own known_hosts: itself (self ssh, like the other small hosts) and Cage.
CAGEHK=$(ssh m@cage.local 'awk "{print \"cage.local,cage,192.168.68.199\", \$1, \$2}" /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub')
add czerny m /home/m/.ssh/known_hosts "$HK" "localhost $(echo "$HK" | head -1 | cut -d' ' -f2-)" "$CAGEHK"
add czerny root /root/.ssh/known_hosts "$HK"

echo; echo "== check: m@czerny -> each peer, and peers -> czerny"
for h in chopin cinqcent cage copland choubox czerny; do
  printf '%-9s ' $h; ssh m@czerny.local "ssh -o BatchMode=yes -o ConnectTimeout=8 m@$h.local hostname" 2>&1 | tail -1
done
for h in chopin cinqcent cage copland choubox; do
  printf '%-9s -> czerny: ' $h; ssh m@$h.local 'ssh -o BatchMode=yes -o ConnectTimeout=8 m@czerny.local hostname' 2>&1 | tail -1
done
printf 'root@czerny -> root@chopin: '; ssh m@czerny.local 'sudo -n ssh -o BatchMode=yes -o ConnectTimeout=8 root@chopin.local hostname' 2>&1 | tail -1
