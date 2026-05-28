# Verify program

Run one of three [`solana-verify`](https://github.com/Ellipsis-Labs/solana-verifiable-build) flows for a Solana program against a deployed artifact. The `mode` input selects the flow so a single action can cover direct (single-keypair) deployments and multisig (e.g. Squads) deployments without forking the action.

| `mode` | Underlying `solana-verify` call(s) | Required mode-specific inputs | Side effects |
|---|---|---|---|
| `verify-from-repo` (default) | `verify-from-repo --keypair ...` (+ optional `remote submit-job`) | `keypair` | Uploads the verified-build PDA from the runner. Optionally notifies OtterSec. |
| `export-pda-tx` | `export-pda-tx --uploader ...` | `uploader` (multisig pubkey) | Writes the base58 PDA-upload transaction to `output-file` and appends it to `$GITHUB_STEP_SUMMARY`. No keypair on the runner, no on-chain side effect. |
| `submit-remote-job` | `build` + `get-executable-hash` + `get-program-hash` + diff (+ optional `remote submit-job`) | `uploader` if `submit-remote` resolves to `true` | Rebuilds deterministically, fails the step if the local hash does not match on-chain, then optionally notifies OtterSec. |

`solana-verify` and the Solana CLI must already be on `PATH` (use [`install-solana`](../install-solana) and [`install-solana-verify`](../install-solana-verify) first). A working Docker daemon is required on the runner.

## Mode: `verify-from-repo` (direct deployment)

```yaml
- uses: actions/checkout@v4
- uses: metaplex-foundation/actions/install-rust@v1
- uses: metaplex-foundation/actions/install-solana@v1
  with: { version: 1.18.26 }
- uses: metaplex-foundation/actions/install-solana-verify@v1
  with: { version: 0.4.15 }

- name: Write signer files
  env:
    PROGRAM_ID_KEYPAIR: ${{ secrets.PROGRAM_ID_KEYPAIR }}
    DEPLOYER_KEY: ${{ secrets.DEPLOYER_KEY }}
  run: |
    printf '%s' "$PROGRAM_ID_KEYPAIR" > ./program-id.json
    printf '%s' "$DEPLOYER_KEY"       > ./deployer-key.json

- uses: metaplex-foundation/actions/verify-program@v1
  with:
    # mode: verify-from-repo  (default)
    program-id-keypair: ./program-id.json
    keypair: ./deployer-key.json
    rpc-url: ${{ secrets.MAINNET_RPC }}
    library-name: mpl_hybrid
    package: mpl-hybrid-program
    solana-version: 1.18.26
```

## Mode: `export-pda-tx` (Squads / multisig — phase 1)

Run during the deploy. Generates the base58 PDA-upload transaction for the multisig vault to execute. No keypair is required on the runner; only the vault pubkey.

```yaml
- uses: metaplex-foundation/actions/verify-program@v1
  with:
    mode: export-pda-tx
    program-id: BGUMAp9Gq7iTEuizy4pqaxsTyUCBK68MDfK752saRPUY
    uploader: bfQVv6niKVgEURYqQ1beJmiEQQN7MrvLRvk3mZGFubb  # Squads vault
    rpc-url: ${{ secrets.MAINNET_RPC }}
    library-name: bubblegum
    package: bubblegum
    mount-path: programs/bubblegum
    solana-version: 1.18.26
    output-file: verify-pda-tx.b58

- uses: actions/upload-artifact@v4
  with:
    name: verify-pda-tx
    path: verify-pda-tx.b58
    if-no-files-found: error
```

The action writes the base58 transaction to `output-file`, appends a `### Verified-build PDA transaction` block to `$GITHUB_STEP_SUMMARY`, and surfaces the transaction as both an `outputs.tx-file` path and an `outputs.tx` raw string for downstream steps.

## Mode: `submit-remote-job` (Squads / multisig — phase 2)

Run after the multisig executes the transaction from phase 1. Rebuilds the program deterministically, fails if the local hash differs from the on-chain hash, and (when `submit-remote` resolves to `true`) notifies OtterSec to do the same comparison.

```yaml
- uses: metaplex-foundation/actions/verify-program@v1
  with:
    mode: submit-remote-job
    program-id: BGUMAp9Gq7iTEuizy4pqaxsTyUCBK68MDfK752saRPUY
    uploader: bfQVv6niKVgEURYqQ1beJmiEQQN7MrvLRvk3mZGFubb  # required if submit-remote=true
    rpc-url: ${{ secrets.MAINNET_RPC }}
    library-name: bubblegum
    package: bubblegum
    mount-path: programs/bubblegum
    solana-version: 1.18.26
    submit-remote: true
```

Setting `submit-remote: false` makes this mode a pre-flight check only: it builds and compares hashes without contacting OtterSec.

## Bubblegum-style operator workflow (both phases, same action)

A workflow that lets an operator pick a phase from `workflow_dispatch.inputs.mode` and pipes it straight into the action:

```yaml
on:
  workflow_dispatch:
    inputs:
      git_ref: { type: string, required: true }
      mode:
        type: choice
        required: true
        default: submit-remote-job
        options: [export-pda-tx, submit-remote-job]

jobs:
  verify:
    runs-on: ubuntu-latest-16-cores
    steps:
      - uses: actions/checkout@v4
        with: { ref: "${{ inputs.git_ref }}" }
      - uses: metaplex-foundation/actions/install-rust@v1
      - uses: metaplex-foundation/actions/install-solana@v1
        with: { version: 1.18.26 }
      - uses: metaplex-foundation/actions/install-solana-verify@v1
        with: { version: 0.4.15 }

      - uses: metaplex-foundation/actions/verify-program@v1
        with:
          mode: ${{ inputs.mode }}
          program-id: BGUMAp9Gq7iTEuizy4pqaxsTyUCBK68MDfK752saRPUY
          uploader: bfQVv6niKVgEURYqQ1beJmiEQQN7MrvLRvk3mZGFubb
          rpc-url: ${{ secrets.MAINNET_RPC }}
          library-name: bubblegum
          package: bubblegum
          mount-path: programs/bubblegum
          solana-version: 1.18.26
          submit-remote: true

      - if: inputs.mode == 'export-pda-tx'
        uses: actions/upload-artifact@v4
        with:
          name: verify-pda-tx
          path: verify-pda-tx.b58
```

## Inputs

Inputs are organized by which mode(s) they apply to. The mode is validated in the resolve step and the action fails fast with a clear error when a mode-required input is missing.

### All modes

| Input | Required | Default | Notes |
|---|---|---|---|
| `mode` | yes | `verify-from-repo` | One of `verify-from-repo`, `export-pda-tx`, `submit-remote-job`. |
| `program-id` | one of these | — | Pubkey of the deployed program. |
| `program-id-keypair` | one of these | — | Path to the program-id keypair file; the action derives `program-id` via `solana-keygen pubkey`. |
| `rpc-url` | yes | — | Solana RPC URL. |
| `library-name` | yes | — | Cargo library name (`[lib].name`). |
| `package` | no | — | Cargo package name forwarded after `--`. Required when the workspace has multiple members that emit the same library filename. |
| `mount-path` | yes | `.` | Path inside the repo to mount into the verifier container. Use e.g. `programs/bubblegum` when the Cargo workspace is nested. |
| `working-directory` | yes | `.` | Directory `solana-verify` runs from. |
| `solana-version` | no | — | Solana version that produced the on-chain program. Resolved against [`solana-verify-base-images.json`](../solana-verify-base-images.json) to a digest-pinned image. Use this for any version present in the table; otherwise pass `base-image`. |
| `base-image` | no | derived from `solana-version` if set, else `solana-verify`'s default | Direct digest-pinned image. Takes precedence over `solana-version`. Must match `<image>@sha256:<64 hex>` unless `allow-mutable-tag: true`. |
| `allow-mutable-tag` | yes | `false` | Allow a mutable tag for `base-image` (not recommended). |
| `init-cli-config` | yes | `true` | Whether to run `solana config set` before `solana-verify`. Required for `solana-verify 0.4.15`'s PDA upload path. |

### `verify-from-repo` mode

| Input | Required | Default | Notes |
|---|---|---|---|
| `keypair` | yes | — | Uploader keypair file. The action derives the `uploader` pubkey via `solana-keygen pubkey`. |
| `repo-url` | no | `https://github.com/${GITHUB_REPOSITORY}.git` | HTTPS URL passed to `verify-from-repo`. |
| `commit-hash` | no | `${GITHUB_SHA}` | Commit that produced the on-chain program. |

### `export-pda-tx` mode

| Input | Required | Default | Notes |
|---|---|---|---|
| `uploader` | yes | — | Pubkey that will execute the PDA-upload transaction (multisig vault). Must be the program's upgrade authority for Solana Explorer to show the verified badge. |
| `repo-url` | no | `https://github.com/${GITHUB_REPOSITORY}.git` | HTTPS URL passed to `export-pda-tx`. |
| `commit-hash` | no | `${GITHUB_SHA}` | Commit that produced the on-chain program. |
| `output-file` | yes | `verify-pda-tx.b58` | Path (relative to `working-directory`) for the base58 transaction file. |
| `emit-summary` | yes | `true` | Whether to append a `### Verified-build PDA transaction` block to `$GITHUB_STEP_SUMMARY`. |
| `encoding` | yes | `base58` | Forwarded to `export-pda-tx --encoding`. |
| `compute-unit-price` | yes | `0` | Forwarded to `export-pda-tx --compute-unit-price`. |

### `submit-remote-job` mode

| Input | Required | Default | Notes |
|---|---|---|---|
| `uploader` | yes if `submit-remote` resolves to `true` | — | Pubkey passed to `solana-verify remote submit-job --uploader`. Should match whoever uploaded the verified-build PDA (e.g. the multisig vault). |

### Submit-remote handling (verify-from-repo and submit-remote-job)

| Input | Required | Default | Notes |
|---|---|---|---|
| `repo-visibility` | yes | `public` | `public` or `private`. Drives the default of `submit-remote`. Auto-promoted to `private` when `github.event.repository.private == true` and `repo-url` resolves to the workflow repository. |
| `submit-remote` | no | derived from `repo-visibility` (`public` → `true`, `private` → `false`) | Explicit `true`/`false` overrides. Setting `true` in `export-pda-tx` mode is rejected — the PDA has not been written on-chain yet, so re-run with `mode: submit-remote-job` after the multisig executes. |

## Outputs

| Output | Modes | Notes |
|---|---|---|
| `program-id` | all | Resolved program ID (pubkey). |
| `uploader` | all | Resolved uploader pubkey. Useful when the action derived it from `keypair`. |
| `base-image` | all | Resolved digest-pinned base image (from `base-image` input or `solana-version` lookup). Empty when neither was set. |
| `tx-file` | `export-pda-tx` | Path of the base58 PDA-upload transaction file. |
| `tx` | `export-pda-tx` | The base58 PDA-upload transaction itself, suitable for piping into downstream steps. |
| `local-hash` | `submit-remote-job` | Local executable hash from the deterministic rebuild. |
| `onchain-hash` | `submit-remote-job` | On-chain program hash from `solana-verify get-program-hash`. |

## Private repositories

When `repo-visibility: private`, `submit-remote` defaults to `false` because the OtterSec verifier has no way to clone a private repo. The on-chain side effects of each mode (PDA upload in `verify-from-repo`, transaction generation in `export-pda-tx`, hash compare in `submit-remote-job`) still run, so anyone with repo access can verify locally with `solana-verify verify-from-repo`. Override with `submit-remote: true` if your setup has a private-aware verifier.

For `verify-from-repo` and `export-pda-tx` modes, private repos need one extra piece: `solana-verify` performs an internal `git clone` of the repository URL before building/exporting. When the effective repository visibility is private, this action scopes a temporary git URL rewrite to that single `solana-verify` invocation via `GIT_CONFIG_GLOBAL` and authenticates the clone with the workflow's `github.token`. The token is not written to the runner's global git config and the temporary config is deleted when the step exits. The action auto-promotes `repo-visibility` to `private` only when `github.event.repository.private == true` and `repo-url` resolves to the workflow repository, so same-repo private callers do not have to set the flag manually. The calling job must declare `permissions: contents: read` (or higher) so `github.token` can authenticate the clone.

## Pinning the base image

The action requires `base-image` to be digest-pinned because a mutable tag like `:1.18.26` can be re-pushed by the registry owner, which would silently change the verifier's hash and either falsely match or falsely diverge from the on-chain program. The deploy job that uploads the verified-build PDA runs with the deployer keypair on disk, so a swapped image is also a foothold for a supply-chain attacker — see [mpl-hybrid PR #26](https://github.com/metaplex-foundation/mpl-hybrid/pull/26) for the audit finding that originated this pattern.

For callers on a Solana version listed in [`solana-verify-base-images.json`](../solana-verify-base-images.json), the simplest pattern is `solana-version: <X.Y.Z>` and let the action pick the right digest. For other versions or to override the lookup, find the digest with:

```bash
docker buildx imagetools inspect solanafoundation/solana-verifiable-build:1.18.30 \
  --format '{{ .Manifest.Digest }}'
```

and pass `base-image` directly. Hard-code that value in the caller workflow's `env:` block — **not** in `.github/.env`, since any PR can edit that file and swap the image. If the workflow grep-loads `.github/.env` into `$GITHUB_ENV`, exclude any image-related key from the allowlist:

```yaml
- run: |
    grep -E '^(RUST_VERSION|DEPLOY_SOLANA_VERSION|SOLANA_VERIFY_VERSION)=' \
      .github/.env >> "$GITHUB_ENV"
```

### Adding a new Solana version to the lookup

Update [`solana-verify-base-images.json`](../solana-verify-base-images.json) with the digest from `docker buildx imagetools inspect`. The action's resolution error message lists the currently known versions, so an actionable failure is the signal to add an entry.

## Notes

- The action does **not** run a standalone deterministic build in `verify-from-repo` or `export-pda-tx` modes — `solana-verify` does that internally inside the verifier container. `submit-remote-job` mode runs `solana-verify build` on the runner so the local hash can be compared to on-chain.
- `solana-verify 0.4.15` reads `~/.config/solana/cli/config.yml` during the PDA upload regardless of `--url`/`--keypair`. The `Initialize Solana CLI config` step writes that file before the upload to avoid `No such file or directory (os error 2)` failures on clean runners. The step runs in all modes; the `--keypair` flag is omitted when no keypair was provided.
- `keypair` is resolved to an absolute path before being passed to `solana config set`, so the CLI config stays valid even if a later step changes the working directory.
- `export-pda-tx`'s stdout interleaves docker progress with the encoded transaction. The action extracts the longest pure-base58 line of at least 100 characters; if `solana-verify`'s output format ever changes this heuristic may need updating.
