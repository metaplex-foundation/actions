# Install SBF test tools

Install `cargo-build-sbf` and `cargo-test-sbf` at a pinned crate version (`solana-cargo-build-sbf` / `solana-cargo-test-sbf`) in front of the copies that ship with the Solana CLI, with caching. Programs whose platform-tools need a newer `cargo build-sbf` than their Solana release provides use this.

Rust must be on `PATH` (use [`install-rust`](../install-rust) first) and the action must run after [`install-solana`](../install-solana), since `GITHUB_PATH` prepends and the pinned tools have to come first.

```yaml
- uses: metaplex-foundation/actions/install-rust@v2
- uses: metaplex-foundation/actions/install-solana@v2
  with:
    version: 2.3.5

- uses: metaplex-foundation/actions/install-sbf-tools@v2
  with:
    version: 4.0.0
```

- Inputs:
  - `version`: The crate version to install, e.g. `4.0.0`. **Required**.
  - `cache`: Whether the installed binaries should be cached. Defaults to `true`.
