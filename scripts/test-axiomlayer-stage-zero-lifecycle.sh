#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <nix-system> <installer-path>" >&2
  exit 64
fi

system=$1
installer=$2
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

case "$(uname -s):$(uname -m):$system" in
  Linux:x86_64:x86_64-linux | Linux:aarch64:aarch64-linux | Darwin:arm64:aarch64-darwin) ;;
  *)
    echo "error: runner does not match declared lifecycle surface $system" >&2
    exit 1
    ;;
esac

python3 "$repo_root/scripts/verify-axiomlayer-integration.py" artifact "$system" "$installer"

if [[ -e /nix/receipt.json || -e /nix/nix-installer ]]; then
  echo "error: lifecycle runner already contains a nix-installer receipt" >&2
  exit 1
fi

cleanup() {
  if [[ -x /nix/nix-installer ]]; then
    sudo env NIX_INSTALLER_NO_CONFIRM=true /nix/nix-installer uninstall || true
  fi
}
trap cleanup EXIT

sudo "$installer" \
  install \
  --no-confirm \
  --no-modify-profile \
  --enable-flakes \
  --extra-conf "flake-registry =" \
  --extra-conf "accept-flake-config = false"

installed_version=$(/nix/var/nix/profiles/default/bin/nix --version)
if [[ "$installed_version" != "nix (Nix) 2.35.2" ]]; then
  echo "error: stage zero installed unexpected Nix version: $installed_version" >&2
  exit 1
fi

grep -Fqx "flake-registry =" /etc/nix/nix.conf
grep -Fqx "accept-flake-config = false" /etc/nix/nix.conf
test -x /nix/nix-installer
test -f /nix/receipt.json

sudo env NIX_INSTALLER_NO_CONFIRM=true /nix/nix-installer uninstall
trap - EXIT

if [[ -e /nix/receipt.json || -e /nix/nix-installer ]]; then
  echo "error: uninstall left stage-zero receipt material behind" >&2
  exit 1
fi
if [[ "$(uname -s)" == "Linux" && -e /nix ]]; then
  echo "error: Linux uninstall left /nix behind" >&2
  exit 1
fi

echo "verified $system install and rollback lifecycle"
