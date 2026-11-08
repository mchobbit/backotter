BackOtter rescue stick: configuration partition

The rest of this stick is the rescue system, which is read-only.  This
partition holds what the stick needs to know about the machines it can
restore.  BackOtter's backup tool updates it whenever the stick is plugged in.

  backotter/settings.toml   yours to edit with any text editor: a name for
                            this stick, and per-machine overrides
  backotter/stick.json      written by BackOtter; don't edit
  backotter/machines/       written by BackOtter; don't edit
  backotter/bin/            copies of bko-holt and rustic-bko, which
                            bko-realworld-1a runs; replace them to try other
                            versions
  backotter/realworld/      one directory of logs per bko-realworld-1a run
  backotter/authorized_keys optional, and yours: public SSH keys, one per line.
                            With it, the stick starts SSH when it boots and
                            lets those keys in as "backotter", with sudo
  backotter/ssh_host_ed25519_key
                            optional, for test benches: the host key the
                            stick presents instead of making a new one

BackOtter puts no passwords or private keys here.  A host key is here only if
someone added one on purpose, with stick/add-ssh-key.sh --host-key.
