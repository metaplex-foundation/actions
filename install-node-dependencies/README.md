# Install Node dependencies

Install Node.js dependencies with optional caching.

```yaml
- uses: metaplex-foundation/actions/install-node-dependencies@v1
  with:
    folder: .
    command: pnpm install --frozen-lockfile
    cache: true
    key: node
```

- Inputs:
  - `folder`: Path to the folder containing the desired package.json (without trailing slash). Defaults to `.`.
  - `command`: The command to install dependencies. Defaults to `pnpm install --frozen-lockfile`.
  - `cache`: Whether installed dependencies should be cached. Defaults to `true`.
  - `key`: A unique key prefix for the cache. Defaults to `node`.
