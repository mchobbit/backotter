# NixOS, installed from another distribution's live system the way the NixOS
# manual describes: Nix itself, then nixos-install from a pinned nixpkgs, so
# the guest is that release exactly.  / on bcachefs, made by this live
# system (an Arch ISO with the 6.12 kernel, the last with bcachefs in it) in
# the on-disk version 6.12 knows, so the host's 6.12 kernel can mount it.
# The guest runs NixOS's own kernel with the out-of-tree bcachefs module
# (NixOS builds no kernel with bcachefs in it), told not to upgrade that
# version.

NIXPKGS='@NIXPKGS@'                    # a nixexprs.tar.xz of a NixOS release
NIX_VERSION='@NIX_VERSION@'
STATE_VERSION='@STATE_VERSION@'        # e.g. 26.05

mkfs_root "$ROOT_PART"
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot

# Nix, single-user as root, its store on the live system's tmpfs: only the
# installer tools go there; nixos-install builds into /mnt/nix.
curl -fsS "https://releases.nixos.org/nix/nix-$NIX_VERSION/nix-$NIX_VERSION-x86_64-linux.tar.xz" |
    tar -xJ -C /tmp
mkdir -p /etc/nix
printf 'build-users-group =\nexperimental-features = nix-command\n' >/etc/nix/nix.conf
USER=root HOME=/root /tmp/nix-"$NIX_VERSION"-x86_64-linux/install --no-daemon --no-channel-add
. /root/.nix-profile/etc/profile.d/nix.sh
export NIX_PATH=nixpkgs=$NIXPKGS
nix-env -f '<nixpkgs>' -iA nixos-install-tools

nixos-generate-config --root /mnt
cat >/mnt/etc/nixos/configuration.nix <<EOF
# A BackOtter test guest; make-bootstrap.sh wrote this.
{ config, lib, pkgs, ... }:
{
  imports = [ ./hardware-configuration.nix ];

  boot.loader.grub.enable = true;
  boot.loader.grub.device = "$DISK";
  boot.loader.timeout = 2;
  boot.supportedFilesystems = [ "$ROOT_FS" ];
  # Keep / at the on-disk version it was made with: a 6.12 kernel's.
  fileSystems."/".options = [ "version_upgrade=none" ];
  boot.kernelParams = [ "console=tty0" "console=ttyS0,115200n8" ];

  networking.hostName = "$GUEST_NAME";
  networking.useDHCP = true;
  time.timeZone = "UTC";

  services.openssh.enable = true;
  services.openssh.settings.PermitRootLogin = "prohibit-password";
  users.users.root.hashedPassword = "$ROOT_PASSWORD_HASH";
  users.users.root.openssh.authorizedKeys.keys = [ "$AUTHORIZED_KEY" ];

  # The guest's nix-* commands see the same nixpkgs it was built from.
  nix.nixPath = [ "nixpkgs=$NIXPKGS" ];
  system.stateVersion = "$STATE_VERSION";
}
EOF
nixos-install --root /mnt --no-root-passwd --no-channel-copy -I "nixpkgs=$NIXPKGS"
# The kernel must be able to mount / at boot.
# (The system's module tree links into the store by absolute paths, which
# point into the live system from out here, so look in the store itself.)
find /mnt/nix/store -maxdepth 9 -name "$ROOT_FS.ko*" | grep -q . || { echo "the guest's kernel can't mount $ROOT_FS"; false; }
finish
