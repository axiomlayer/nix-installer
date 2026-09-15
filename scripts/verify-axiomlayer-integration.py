#!/usr/bin/env python3
"""Validate the immutable AxiomLayer stage-zero contract and its CI boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "axiomlayer" / "stage-zero-policy.json"
ACTIVE_WORKFLOW = "axiomlayer-integration.yml"
ARCHIVED_WORKFLOWS = {
    "act-test.yml.disabled",
    "bump-nix-version.yml.disabled",
    "ci.yml.disabled",
    "hestia-gc.yml.disabled",
    "release-script.yml.disabled",
    "update.yml.disabled",
}
EXPECTED_SOURCE = {
    "upstream": "NixOS/nix-installer",
    "fork": "AxiomLayer/nix-installer",
    "version": "2.35.2",
    "tag": "2.35.2",
    "commit": "b687af918ee7cb78be861542137395bb482111f3",
    "embeddedNixVersion": "2.35.2",
}
EXPECTED_SURFACES = {
    "x86_64-linux": (
        "ubuntu-24.04",
        "nix-installer-static",
        "nix-installer-x86_64-linux",
        "5448a1cd70ad945cb4d36365defbaf3731eba38e23859f3dc8bd7418e1946acc",
        34320320,
    ),
    "aarch64-linux": (
        "ubuntu-24.04-arm",
        "nix-installer-static",
        "nix-installer-aarch64-linux",
        "a1b35e56da5adadbc117c3cf17b83948ac657f3c0bd79d47bbe0aa70832b5c8e",
        32467873,
    ),
    "aarch64-darwin": (
        "macos-15",
        "nix-installer",
        "nix-installer-aarch64-darwin",
        "6314b195321b3acc6826b1c5d66bb9cf9306c8231c6dbb745f51a04c3bcee235",
        25205264,
    ),
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def load_policy() -> dict[str, object]:
    with POLICY_PATH.open("rb") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        fail("stage-zero policy must be a JSON object")
    return value


def verify_workflow_boundary(policy: dict[str, object]) -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    active = {
        path.name
        for path in workflow_dir.iterdir()
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    }
    if active != {ACTIVE_WORKFLOW}:
        fail(f"unexpected active workflow set: {sorted(active)}")

    archive_dir = ROOT / ".github" / "upstream-workflows"
    archived = {
        path.name
        for path in archive_dir.iterdir()
        if path.is_file() and path.name.endswith(".disabled")
    }
    if archived != ARCHIVED_WORKFLOWS:
        fail(f"unexpected archived upstream workflow set: {sorted(archived)}")

    text = (workflow_dir / ACTIVE_WORKFLOW).read_text()
    required_triggers = ("pull_request:", "push:", "schedule:", "workflow_dispatch:")
    for trigger in required_triggers:
        if trigger not in text:
            fail(f"active integration workflow is missing trigger {trigger}")

    forbidden = {
        "pull_request_target": "privileged pull-request trigger",
        "repository_dispatch": "external mutation trigger",
        "environment:": "deployment environment",
        "codex_security_gate": "retired security gate",
        "${{ secrets.": "repository or organization secret",
        "contents: write": "contents write permission",
        "actions: write": "actions write permission",
        "packages: write": "packages write permission",
        "id-token: write": "OIDC write permission",
        "attestations: write": "attestation write permission",
        "gh release": "release publication command",
        "cargo publish": "crate publication command",
        "nix flake update": "lock mutation command",
        "cachix": "external cache mutation",
    }
    for needle, description in forbidden.items():
        if needle in text:
            fail(f"active workflow contains {description}: {needle}")

    action_references = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", text, re.MULTILINE)
    if not action_references:
        fail("active integration workflow must declare pinned actions")
    for reference in action_references:
        if reference.startswith("./"):
            continue
        if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference):
            fail(f"action is not pinned to a full commit SHA: {reference}")

    consumer = policy.get("consumerContract")
    if not isinstance(consumer, dict) or consumer.get("shaPinningRequired") is not True:
        fail("consumer contract must require full action SHA pinning")


def verify_policy() -> None:
    policy = load_policy()
    if policy.get("schema") != "axiomlayer-nix-installer-integration-v1":
        fail("unsupported stage-zero policy schema")
    if policy.get("source") != EXPECTED_SOURCE:
        fail("source identity drifted from the reviewed Dotfiles #49 contract")

    consumer = policy.get("consumerContract")
    if not isinstance(consumer, dict):
        fail("consumer contract is missing")
    expected_consumer = {
        "repository": "AxiomLayer/dotfiles",
        "pullRequest": 49,
        "headCommit": "d7a9c4afc1083c17c06a2f82beb63a5d6292dca0",
        "policyPath": "config/upstream-promotion-policy.json",
        "bootstrapPath": "config/nix-bootstrap.json",
        "shaPinningRequired": True,
    }
    if consumer != expected_consumer:
        fail("consumer snapshot drifted from the reviewed Dotfiles #49 files")

    install_args = policy.get("installArguments")
    if install_args != [
        "install",
        "--no-confirm",
        "--no-modify-profile",
        "--enable-flakes",
        "--extra-conf",
        "flake-registry =",
        "--extra-conf",
        "accept-flake-config = false",
    ]:
        fail("stage-zero install arguments do not match Dotfiles #49")

    surfaces = policy.get("supportedSurfaces")
    if not isinstance(surfaces, dict) or set(surfaces) != set(EXPECTED_SURFACES):
        fail("supported surface set drifted")
    for system, expected in EXPECTED_SURFACES.items():
        surface = surfaces.get(system)
        if not isinstance(surface, dict):
            fail(f"surface {system} is invalid")
        actual = (
            surface.get("runner"),
            surface.get("package"),
            surface.get("asset"),
            surface.get("sha256"),
            surface.get("size"),
        )
        if actual != expected:
            fail(f"surface {system} drifted from the reviewed release asset")
        expected_suffix = f"/2.35.2/{surface['asset']}"
        if not str(surface.get("releaseUrl", "")).endswith(expected_suffix):
            fail(f"surface {system} release URL is not version-addressed")
        if not re.fullmatch(r"[0-9a-f]{64}", str(surface.get("sha256", ""))):
            fail(f"surface {system} digest is invalid")

    delegated = policy.get("delegatedSurfaces")
    if delegated != {
        "x86_64-darwin": {
            "repository": "AxiomLayer/nix",
            "reason": "nix-installer 2.35.2 does not support x86_64-darwin",
        }
    }:
        fail("the x86_64-darwin delegation changed without review")

    source_commit = EXPECTED_SOURCE["commit"]
    tag_commit = run_git("rev-parse", f"{EXPECTED_SOURCE['tag']}^{{commit}}")
    if tag_commit != source_commit:
        fail(f"tag {EXPECTED_SOURCE['tag']} resolves to {tag_commit}, not {source_commit}")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_commit, "HEAD"],
        cwd=ROOT,
        check=False,
    )
    if ancestry.returncode != 0:
        fail("reviewed source commit is not an ancestor of the integration candidate")

    cargo_toml = tomllib.loads(run_git("show", f"{source_commit}:Cargo.toml"))
    if cargo_toml.get("package", {}).get("version") != "2.35.2":
        fail("reviewed source commit does not declare installer version 2.35.2")
    flake_text = run_git("show", f"{source_commit}:flake.nix")
    if 'nix.url = "github:NixOS/nix/2.35.2";' not in flake_text:
        fail("reviewed source commit does not embed Nix 2.35.2")
    systems_match = re.search(r"supportedSystems\s*=\s*\[(.*?)\];", flake_text, re.DOTALL)
    if systems_match is None:
        fail("could not read supportedSystems from the reviewed flake")
    flake_systems = set(re.findall(r'"([^"]+)"', systems_match.group(1)))
    if flake_systems != set(EXPECTED_SURFACES):
        fail(f"reviewed flake surface set drifted: {sorted(flake_systems)}")

    verify_workflow_boundary(policy)
    print(f"verified AxiomLayer stage zero at {source_commit}")


def surface_for(system: str) -> dict[str, object]:
    policy = load_policy()
    surfaces = policy.get("supportedSurfaces")
    if not isinstance(surfaces, dict) or system not in surfaces:
        fail(f"unsupported stage-zero surface: {system}")
    surface = surfaces[system]
    if not isinstance(surface, dict):
        fail(f"invalid stage-zero surface: {system}")
    return surface


def verify_artifact(system: str, artifact: Path, execute: bool = True) -> None:
    surface = surface_for(system)
    if not artifact.is_file():
        fail(f"stage-zero artifact is missing: {artifact}")
    actual_size = artifact.stat().st_size
    if actual_size != surface["size"]:
        fail(
            f"stage-zero artifact size mismatch for {system}: "
            f"expected {surface['size']}, got {actual_size}"
        )
    digest = hashlib.sha256()
    with artifact.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_digest = digest.hexdigest()
    if actual_digest != surface["sha256"]:
        fail(
            f"stage-zero artifact digest mismatch for {system}: "
            f"expected {surface['sha256']}, got {actual_digest}"
        )
    artifact.chmod(artifact.stat().st_mode | stat.S_IXUSR)
    if execute:
        version = subprocess.run(
            [str(artifact), "--version"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        if version != "nix-installer 2.35.2":
            fail(f"unexpected installer version for {system}: {version}")
    print(f"verified {system} stage-zero artifact {actual_digest}")


def fetch_artifact(system: str, destination: Path) -> None:
    surface = surface_for(system)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        str(surface["releaseUrl"]),
        headers={"User-Agent": "AxiomLayer-nix-installer-integration"},
    )
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                while chunk := response.read(1024 * 1024):
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
    temporary_path.replace(destination)
    verify_artifact(system, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("policy")
    fetch = subcommands.add_parser("fetch")
    fetch.add_argument("system")
    fetch.add_argument("destination", type=Path)
    artifact = subcommands.add_parser("artifact")
    artifact.add_argument("system")
    artifact.add_argument("path", type=Path)
    artifact.add_argument("--no-execute", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "policy":
            verify_policy()
        elif args.command == "fetch":
            fetch_artifact(args.system, args.destination)
        elif args.command == "artifact":
            verify_artifact(args.system, args.path, not args.no_execute)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
