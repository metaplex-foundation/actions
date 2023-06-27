# Install cargo-release

Install cargo-release with optional caching and verify the installed version.

```yaml
- uses: metaplex-foundation/actions/install-cargo-release@v1
  with:
    cache: true
    version: "0.24.10"
```

- Inputs:
  - `cache`: Whether the downloaded cargo-release should be cached. Defaults to `true`.
  - `version`: version of the cargo-release to install. Defaults to `"0.24.10"`.
