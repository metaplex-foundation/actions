# Verified build

Run a deterministic, Docker-based build of a Solana program with [`solana-verify build`](https://github.com/Ellipsis-Labs/solana-verifiable-build). The hash of the produced `.so` matches a remote verification of the same source, so the output is suitable for upload to a verified-build PDA.

Requires `solana-verify` to be installed first (see [`install-solana-verify`](../install-solana-verify)) and a working Docker daemon on the runner. The standard GitHub-hosted Ubuntu runners include Docker.

```yaml
- uses: metaplex-foundation/actions/install-solana-verify@v1

- uses: metaplex-foundation/actions/verified-build@v1
  with:
    library-name: mpl_hybrid
    package: mpl-hybrid-program
    base-image: solanafoundation/solana-verifiable-build@sha256:ec2e20e1f80607150a71e4c72adfe64be24347ed0b4fb741c32b34eaf7549a25
    output-dir: programs/.bin
```

- Inputs:
  - `library-name`: Cargo library name (matches `[lib].name` in the program's `Cargo.toml`). **Required**.
  - `package`: Cargo package name passed after `--`. Required when the workspace has multiple members that could emit the same library filename.
  - `base-image`: Docker base image used by `solana-verify build`. Must be digest-pinned (`<image>@sha256:<digest>`) so a mutable upstream tag cannot change the resulting `.so` hash between runs. Pin this to match the Solana version that produced the on-chain program. Leave empty to use `solana-verify`'s default image.
  - `allow-mutable-tag`: Allow `base-image` to use a mutable tag instead of a digest pin. Defaults to `false`. Mutable tags can be re-pushed by the registry owner, so two builds at the same git commit may produce different binaries. Only set this to `true` if you accept that risk.
  - `working-directory`: Directory to run the build from. Defaults to `.`.
  - `output-dir`: Directory (relative to `working-directory`) to copy the built `.so` into. Leave empty to keep the artifact in `target/deploy/<library-name>.so` only.
  - `extra-args`: Additional arguments forwarded verbatim to the inner `cargo build-sbf` invocation (after `--`).
- Outputs:
  - `binary-path`: Path of the produced `.so`, relative to `working-directory`. Equals `output-dir/<library-name>.so` when `output-dir` is set, otherwise `target/deploy/<library-name>.so`.

## Pinning the base image

The action requires `base-image` to be digest-pinned because [`solana-verify build`](https://github.com/Ellipsis-Labs/solana-verifiable-build) builds the program inside the chosen image — a mutable tag like `:1.18.26` can be re-pushed by the registry owner, so two CI runs at the same git commit can produce different `.so` files. Find the current digest for an image with:

```bash
docker buildx imagetools inspect solanafoundation/solana-verifiable-build:1.18.26 \
  --format '{{ .Manifest.Digest }}'
```

Hard-code the result in the caller workflow's `env:` block — **not** in `.github/.env` — and treat updates to it as security-relevant review items. Loading the digest from `.github/.env` is unsafe because any PR can edit that file and silently swap the verifier image; see [mpl-hybrid PR #26](https://github.com/metaplex-foundation/mpl-hybrid/pull/26) for the audit finding that originated this pattern.

```yaml
env:
  SOLANA_VERIFY_BASE_IMAGE: solanafoundation/solana-verifiable-build@sha256:ec2e20e1f80607150a71e4c72adfe64be24347ed0b4fb741c32b34eaf7549a25

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: metaplex-foundation/actions/verified-build@v1
        with:
          library-name: mpl_hybrid
          package: mpl-hybrid-program
          base-image: ${{ env.SOLANA_VERIFY_BASE_IMAGE }}
```

If the workflow also grep-loads `.github/.env` into `$GITHUB_ENV`, use an allowlist that excludes `SOLANA_VERIFY_BASE_IMAGE` so a future re-introduction can't override the workflow-level pin:

```yaml
- run: |
    grep -E '^(CARGO_TERM_COLOR|RUST_VERSION|SOLANA_VERSION|SOLANA_VERIFY_VERSION|PROGRAMS)=' \
      .github/.env >> "$GITHUB_ENV"
```
