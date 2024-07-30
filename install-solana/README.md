# Install Solana

Install Solana with optional caching and verify the installed version.

```yaml
- uses: metaplex-foundation/actions/install-solana@v1
  with:
    version: stable
    cache: true
```

- Inputs:
  - `version`: The Solana version to install. Defaults to `stable`.
  - `cache`: Whether the downloaded Solana release should be cached. Defaults to `true`.
  - `base-url`: The base URL to download the Solana release from. Defaults to `https://release.solana.com` for Solana versions below 1.18.19 and `https://release.anza.xyz` for versions 1.18.19 and above.
