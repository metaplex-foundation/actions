# Verified build

Run a deterministic, Docker-based build of a Solana program with [`solana-verify build`](https://github.com/Ellipsis-Labs/solana-verifiable-build). The hash of the produced `.so` matches a remote verification of the same source, so the output is suitable for upload to a verified-build PDA.

Requires `solana-verify` to be installed first (see [`install-solana-verify`](../install-solana-verify)) and a working Docker daemon on the runner. The standard GitHub-hosted Ubuntu runners include Docker.

```yaml
- uses: metaplex-foundation/actions/install-solana-verify@v1

- uses: metaplex-foundation/actions/verified-build@v1
  with:
    library-name: mpl_hybrid
    package: mpl-hybrid-program
    base-image: solanafoundation/solana-verifiable-build:1.18.26
    output-dir: programs/.bin
```

- Inputs:
  - `library-name`: Cargo library name (matches `[lib].name` in the program's `Cargo.toml`). **Required**.
  - `package`: Cargo package name passed after `--`. Required when the workspace has multiple members that could emit the same library filename.
  - `base-image`: Docker base image used by `solana-verify build`. Pin this to match the Solana version that produced the on-chain program (e.g. `solanafoundation/solana-verifiable-build:1.18.26`).
  - `working-directory`: Directory to run the build from. Defaults to `.`.
  - `output-dir`: Directory (relative to `working-directory`) to copy the built `.so` into. Leave empty to keep the artifact in `target/deploy/<library-name>.so` only.
  - `extra-args`: Additional arguments forwarded verbatim to the inner `cargo build-sbf` invocation (after `--`).
- Outputs:
  - `binary-path`: Path of the produced `.so`, relative to `working-directory`. Equals `output-dir/<library-name>.so` when `output-dir` is set, otherwise `target/deploy/<library-name>.so`.
