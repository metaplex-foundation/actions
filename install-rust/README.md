# Install Rust

Install Rust with components, verify the installed versions and set the rustc hash as a `RUSTC_HASH` environment variable.

```yaml
- uses: metaplex-foundation/actions/install-rust@v1
  with:
    toolchain: stable
    components: clippy,rustfmt
```

- Inputs:
  - `toolchain`: The Rust toolchain to install. Defaults to `stable`.
  - `components`: A comma-separated list of Rust components to install. Defaults to `clippy,rustfmt`.
- Outputs:
  - `cachekey`: The hash of the installed rustc.
  - `toolchain`: The name of the installed toolchain.
