# Start a local validator

Installs Solana, Node.js and downloads program builds as artifacts before starting a local validator. This assumes a previous steps builds programs and uploads their builds as artifacts.

```yaml
- uses: metaplex-foundation/actions/start-validator@v1
  with:
    artifacts: program-builds
    command: pnpm validator
    node: 18.x
    solana: stable
    cache: true
```

- Inputs:
  - `artifacts`: The name of the artifact to download program builds. Set to `false` to skip downloading. Defaults to `program-builds`.
  - `command`: The command to start the validator. Defaults to `pnpm validator`.
  - `node`: The Node.js version to install. Defaults to `18.x`.
  - `solana`: The Solana version to install. Defaults to `stable`.
  - `cache`: Whether we should use caches to speed things up. Defaults to `true`.
