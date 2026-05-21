# Install solana-verify

Install the [`solana-verify`](https://github.com/Ellipsis-Labs/solana-verifiable-build) CLI with optional caching and verify the installed version. The action also installs the Linux build dependencies (`libudev-dev`, `pkg-config`) that the `solana-verify` build requires.

`solana-verify` is installed via `cargo install`, so Rust must already be on `PATH` (use [`install-rust`](../install-rust) first) unless the cache hits.

```yaml
- uses: metaplex-foundation/actions/install-rust@v1

- uses: metaplex-foundation/actions/install-solana-verify@v1
  with:
    version: 0.4.15
    cache: true
```

- Inputs:
  - `version`: The `solana-verify` version to install. Defaults to `0.4.15`.
  - `cache`: Whether the installed `solana-verify` binary should be cached. Defaults to `true`.
  - `install-system-deps`: Whether to install the Linux build dependencies (`libudev-dev`, `pkg-config`). Defaults to `true`. Has no effect on non-Linux runners.

Pair this with [`verified-build`](../verified-build) to produce deterministic program binaries and [`verify-program`](../verify-program) to upload the verified-build PDA on-chain.
