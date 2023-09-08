# Cache crate dependencies

Cache all folders related to the given folder. This includes Rust global folders and its `target` folder.

```yaml
- uses: metaplex-foundation/actions/cache-crate@v1
  with:
    folder: ./programs/mpl-token-metadata
    key: program-token-metadata
```

- Inputs:
  - `folder`: Path to the folder containing the crate (without trailing slash). **Required**.
  - `key`: A unique key prefix for the cache identifying the crate. **Required**.
