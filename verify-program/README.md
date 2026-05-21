# Verify program

Upload a verified-build PDA on-chain via [`solana-verify verify-from-repo`](https://github.com/Ellipsis-Labs/solana-verifiable-build) and (optionally) submit a remote verification job to the OtterSec API. The action bakes in the `solana config set` workaround required by `solana-verify 0.4.15` and resolves sensible defaults so most callers only need to provide a program ID, an uploader keypair, an RPC URL, and the Cargo library name.

`solana-verify` and the Solana CLI must already be on `PATH` (use [`install-solana`](../install-solana) and [`install-solana-verify`](../install-solana-verify) first). A working Docker daemon is required on the runner.

```yaml
- uses: actions/checkout@v4

- uses: metaplex-foundation/actions/install-rust@v1

- uses: metaplex-foundation/actions/install-solana@v1
  with:
    version: 1.18.26

- uses: metaplex-foundation/actions/install-solana-verify@v1
  with:
    version: 0.4.15

- name: Write signer files
  env:
    PROGRAM_ID_KEYPAIR: ${{ secrets.PROGRAM_ID_KEYPAIR }}
    DEPLOYER_KEY: ${{ secrets.DEPLOYER_KEY }}
  run: |
    printf '%s' "$PROGRAM_ID_KEYPAIR" > ./program-id.json
    printf '%s' "$DEPLOYER_KEY" > ./deployer-key.json

- uses: metaplex-foundation/actions/verify-program@v1
  with:
    program-id-keypair: ./program-id.json
    keypair: ./deployer-key.json
    rpc-url: ${{ secrets.MAINNET_RPC }}
    library-name: mpl_hybrid
    package: mpl-hybrid-program
    base-image: solanafoundation/solana-verifiable-build:1.18.26
```

- Inputs:
  - `program-id`: On-chain program ID (pubkey). Either `program-id` or `program-id-keypair` must be provided.
  - `program-id-keypair`: Path to the program-id keypair file. Used to derive `program-id` when it is not provided explicitly.
  - `keypair`: Path to the uploader keypair file used to write the verified-build PDA and submit the remote verification job. Must have authority over the program. **Required**.
  - `rpc-url`: Solana RPC URL. **Required**.
  - `library-name`: Cargo library name (matches `[lib].name` in `Cargo.toml`). **Required**.
  - `package`: Cargo package name forwarded after `--`. Required when the workspace has multiple members that could emit the same library filename.
  - `repo-url`: HTTPS URL of the source repository passed to `solana-verify verify-from-repo`. Defaults to `https://github.com/${GITHUB_REPOSITORY}.git`.
  - `commit-hash`: Commit hash that produced the on-chain program. Defaults to `${GITHUB_SHA}`.
  - `mount-path`: Path inside the repository to mount into the verifier container. Defaults to `.`.
  - `working-directory`: Directory to run `solana-verify` from. Defaults to `.`.
  - `base-image`: Docker base image used by `solana-verify verify-from-repo`. Pin this to match the Solana version that produced the on-chain program (e.g. `solanafoundation/solana-verifiable-build:1.18.26`).
  - `submit-remote`: Whether to submit the remote verification job after uploading the PDA. Defaults to `true`.
  - `init-cli-config`: Whether to run `solana config set` before `solana-verify`. Defaults to `true`. Required for `solana-verify 0.4.15` — see [PR #25](https://github.com/metaplex-foundation/mpl-hybrid/pull/25) for context.
- Outputs:
  - `program-id`: The resolved program ID (pubkey) that was verified. Useful when `program-id-keypair` is used and downstream steps need the pubkey.

## Notes

- The action does **not** run the deterministic build itself. Use [`verified-build`](../verified-build) to produce a matching `.so` (or rely on `solana-verify verify-from-repo` to build inside the container from the resolved `commit-hash`).
- `solana-verify 0.4.15` reads `~/.config/solana/cli/config.yml` during the PDA upload regardless of `--url`/`--keypair`. The `Initialize Solana CLI config` step writes that file before the upload to avoid `No such file or directory (os error 2)` failures on clean runners.
- `keypair` is resolved to an absolute path before being passed to `solana config set`, so the CLI config stays valid even if a later step changes the working directory.
