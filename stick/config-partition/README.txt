BackOtter rescue stick: configuration partition

The rest of this stick is the rescue system, which is read-only.  This
partition holds what the stick needs to know about the machines it can
restore.  BackOtter's backup tool updates it whenever the stick is plugged in.

  backotter/settings.toml   yours to edit with any text editor: a name for
                            this stick, and per-machine overrides
  backotter/stick.json      written by BackOtter; don't edit
  backotter/machines/       written by BackOtter; don't edit

No passwords or keys are ever stored here.
