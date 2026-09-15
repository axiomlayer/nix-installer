# Inert upstream workflows

These files preserve the workflows inherited from `NixOS/nix-installer` for
upstream comparison. Their `.yml.disabled` suffix and location outside
`.github/workflows` keep every schedule, publisher, signer, cache writer, and
floating action reference inert in the AxiomLayer fork.

The active integration workflow verifies this exact archive boundary. An
upstream sync that restores any additional triggering workflow fails the
AxiomLayer policy job before it can be promoted. No publisher secret belongs
in this fork.
