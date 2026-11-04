# Cache crate dependencies

Cache the cargo registry and the compiled dependencies of the Cargo workspace that contains `folder`, via [Swatinem/rust-cache](https://github.com/Swatinem/rust-cache).

The workspace root and its `target/` directory are resolved with cargo, so this works for a crate that is a member of a root workspace (the usual `programs/<name>` with `Cargo.lock` and `target/` at the repository root) as well as for a crate with its own lock file. The cache is keyed on the lock files, manifests and toolchain; the workspace's own build artifacts are dropped before saving so only dependencies are kept.

Rust must already be installed (use [`install-rust`](../install-rust) first).

```yaml
- uses: metaplex-foundation/actions/cache-crate@v2
  with:
    folder: ./programs/mpl-core
    key: program-mpl-core
```

- Inputs:
  - `folder`: Path to the crate, or to any directory inside its Cargo workspace (without trailing slash). **Required**.
  - `key`: A unique key for the cache; jobs with the same key share a cache. **Required**.
  - `cache-on-failure`: Save the cache even when a later step fails. Defaults to `true`.
