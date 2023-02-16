# Install Node with PNPM

Install Node.js and PNPM. Optionally download and cache the dependencies in the root folder.

```yaml
- uses: metaplex-foundation/actions/install-node-with-pnpm@v1
  with:
    version: 18.x
    dependencies: false
    cache: true
```

- Inputs:
  - `version`: The Node.js version to install. Defaults to `18.x`.
  - `dependencies`: whether to install dependencies on the root folder. Defaults to `false`.
  - `cache`: Whether installed dependencies should be cached. Defaults to `true`.
