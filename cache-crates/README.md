# Cache dependencies of multiple crates

Cache all crate related folders. This includes Rust global folders and all `target` folders under a given folder.

```yaml
- uses: metaplex-foundation/actions/cache-crates@v1
  with:
    folder: ./programs
    key: programs
```

- Inputs:
  - `folder`: Path to the folder containing the crates (without trailing slash). Defaults to `./programs`.
  - `key`: A unique key prefix for the cache. Defaults to `programs`.
