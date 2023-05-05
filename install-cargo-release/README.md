# Install cargo-release

Install cargo-release with optional caching and verify the installed version.

```yaml
- uses: metaplex-foundation/actions/install-cargo-release@v1
  with:
    cache: true
```

- Inputs:
  - `cache`: Whether the downloaded cargo-release should be cached. Defaults to `true`.
