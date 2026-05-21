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
    base-image: solanafoundation/solana-verifiable-build@sha256:ec2e20e1f80607150a71e4c72adfe64be24347ed0b4fb741c32b34eaf7549a25
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
  - `base-image`: Docker base image used by `solana-verify verify-from-repo`. Must be digest-pinned (`<image>@sha256:<digest>`) so a mutable upstream tag cannot change the hash the verifier computes. Pin this to match the digest used to produce the on-chain program. Leave empty to use `solana-verify`'s default image.
  - `allow-mutable-tag`: Allow `base-image` to use a mutable tag instead of a digest pin. Defaults to `false`. Mutable tags can be re-pushed by the registry owner, so the verification hash may not match the on-chain program even when the source has not changed. Only set this to `true` if you accept that risk.
  - `repo-visibility`: `public` or `private`. Defaults to `public`. The deterministic build and on-chain PDA upload always run; `private` only skips the remote OtterSec submission (which has no way to clone a private repo). The on-chain PDA still records the repo URL and commit hash so anyone with repo access can verify locally.
  - `submit-remote`: Explicit override for the remote verification job. Leave empty (the default) to derive from `repo-visibility` (`public` → `true`, `private` → `false`). Set to `true`/`false` to force a specific behavior.
  - `init-cli-config`: Whether to run `solana config set` before `solana-verify`. Defaults to `true`. Required for `solana-verify 0.4.15` — see [PR #25](https://github.com/metaplex-foundation/mpl-hybrid/pull/25) for context.
- Outputs:
  - `program-id`: The resolved program ID (pubkey) that was verified. Useful when `program-id-keypair` is used and downstream steps need the pubkey.

## Private repositories

```yaml
- uses: metaplex-foundation/actions/verify-program@v1
  with:
    program-id-keypair: ./program-id.json
    keypair: ./deployer-key.json
    rpc-url: ${{ secrets.MAINNET_RPC }}
    library-name: mpl_hybrid
    package: mpl-hybrid-program
    base-image: solanafoundation/solana-verifiable-build@sha256:ec2e20e1f80607150a71e4c72adfe64be24347ed0b4fb741c32b34eaf7549a25
    repo-visibility: private
```

For a private repo the action still runs the deterministic verified build and uploads the verified-build PDA on-chain — the PDA records the repo URL and commit hash so anyone with repo access can clone and verify locally with `solana-verify verify-from-repo`. Only the remote OtterSec submission is skipped because that service has no way to clone a private repo. The default for `submit-remote` flips to `false` automatically; pass `submit-remote: true` to override if you have a private-aware verifier.

## Pinning the base image

The action requires `base-image` to be digest-pinned because a mutable tag like `:1.18.26` can be re-pushed by the registry owner, which would silently change the verifier's hash and either falsely match or falsely diverge from the on-chain program. The deploy job that uploads the verified-build PDA runs with the deployer keypair on disk, so a swapped image is also a foothold for a supply-chain attacker — see [mpl-hybrid PR #26](https://github.com/metaplex-foundation/mpl-hybrid/pull/26) for the audit finding that originated this pattern.

Find the current digest for an image with:

```bash
docker buildx imagetools inspect solanafoundation/solana-verifiable-build:1.18.26 \
  --format '{{ .Manifest.Digest }}'
```

Hard-code the result in the caller workflow's `env:` block — **not** in `.github/.env`, since any PR can edit that file and swap the image:

```yaml
env:
  SOLANA_VERIFY_BASE_IMAGE: solanafoundation/solana-verifiable-build@sha256:ec2e20e1f80607150a71e4c72adfe64be24347ed0b4fb741c32b34eaf7549a25

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: metaplex-foundation/actions/verify-program@v1
        with:
          base-image: ${{ env.SOLANA_VERIFY_BASE_IMAGE }}
          # ...
```

If the workflow also grep-loads `.github/.env` into `$GITHUB_ENV`, exclude `SOLANA_VERIFY_BASE_IMAGE` from the allowlist so a re-introduced entry can't override the workflow-level pin (`$GITHUB_ENV` wins over `env:` for subsequent steps):

```yaml
- run: |
    grep -E '^(RUST_VERSION|DEPLOY_SOLANA_VERSION|SOLANA_VERIFY_VERSION)=' \
      .github/.env >> "$GITHUB_ENV"
```

## Notes

- The action does **not** run the deterministic build itself. Use [`verified-build`](../verified-build) to produce a matching `.so` (or rely on `solana-verify verify-from-repo` to build inside the container from the resolved `commit-hash`).
- `solana-verify 0.4.15` reads `~/.config/solana/cli/config.yml` during the PDA upload regardless of `--url`/`--keypair`. The `Initialize Solana CLI config` step writes that file before the upload to avoid `No such file or directory (os error 2)` failures on clean runners.
- `keypair` is resolved to an absolute path before being passed to `solana config set`, so the CLI config stays valid even if a later step changes the working directory.
