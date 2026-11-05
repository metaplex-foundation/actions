# Actions

Shared CI for Metaplex Solana programs: reusable workflows that program
repositories call from a thin `.github/workflows/main.yml`, plus the
composite actions those workflows are built from.

## Reusable workflows

Every program repository used to carry its own copy of a dozen workflow
files. They now live here once, under `.github/workflows/`, and are called
with `uses: metaplex-foundation/actions/.github/workflows/<name>.yml@<sha>`.
Per-repository facts stay in the calling repository in two files:

- `.github/.env` holds toolchain pins (Rust, Solana, Node, Anchor...). The
  keys are documented in [`load-env`](./load-env/action.yml). Only those keys
  are imported, never the whole file.
- `.github/programs.json` describes the programs (see below).

| Workflow | What it does | Needs from the caller |
|---|---|---|
| `detect-changes.yml` | Runs `.github/file-filters.yml` and reduces the program list to the changed ones | file-filters.yml, programs.json |
| `build-programs.yml` | Builds every program (solana-verify or cargo build-sbf), dumps external programs, uploads `program-builds-<ref>` | programs.json, `configs/scripts/program/dump.sh` |
| `test-programs.yml` | fmt, clippy and `configs/scripts/program/test.sh` per program | test.sh |
| `coverage.yml` | `configs/scripts/program/coverage.sh` per program with a sticky PR comment | coverage.sh |
| `generate-clients.yml` | `pnpm generate` and fails on drift | root package.json `generate` script |
| `test-js-client.yml` | validator + build + test, and a lint job | `pnpm validator`, client `build`/`test`/`format`/`lint` scripts |
| `test-rust-client.yml` | clippy per client crate, then `configs/scripts/client/test-rust.sh` with the program builds | test-rust.sh |
| `benchmark.yml` | `pnpm bench` stored to gh-pages (`mode: store`) or commented on the PR (`mode: summary`) | client `bench` script |
| `publish-js-client.yml` | npm publish with provenance, then a release PR | |
| `publish-rust-client.yml` | cargo release, then a release PR | `CRATES_TOKEN` |
| `release-js-client.yml` / `release-rust-client.yml` | On merge of a release PR: tag and GitHub release | |
| `create-release.yml` | Next `release/<tag>@x.y.z` tag with the `.so` attached | program-builds artifact |
| `deploy-program.yml` | Direct or Squads deploy per cluster, with the verified-build PDA bundled on devnet/mainnet | programs.json `deploy`, secrets |
| `verify-program.yml` | Rebuild, compare the on-chain hash, submit the OtterSec job | programs.json `deploy`, secrets |

Workflows are not nested: the caller's `main.yml` wires them together so
every reference is a pinned commit. The job graph keeps the critical path
short: `detect-changes` → `build-programs` → the client tests, with
`test-programs`, `coverage` and `generate-clients` alongside rather than in
front of them (the clients under test are the committed ones;
`generate-clients` only checks them for drift). JS clients are tested on the
repository's `NODE_VERSION` alone, not on a matrix of Node releases. Inside a reusable workflow the composite
actions are taken from the same commit by checking this repository out at
`job.workflow_sha`, so a consumer that pins one SHA pins everything.

The reference caller lives in
[solana-project-template](https://github.com/metaplex-foundation/solana-project-template);
`scripts/gen-caller.py` generates a repository's caller workflows and
`programs.json` from a short spec (see its docstring). The specs of the
consuming repositories live in `scripts/specs/`; bump `actions_sha` and
`actions_tag` there and regenerate to roll a release out.

### programs.json

Every shared workflow that takes a `git_ref` (`build-programs`, `test-programs`, `create-release`, `deploy-program`, `verify-program`) reads `programs.json` from the revision the run is for (`github.sha`), not from `git_ref`, so a release tag cut before the file existed still builds and deploys with today's cluster configuration. `.github/.env` is read from `git_ref`, because toolchain pins belong with the code they build; the one exception is the host tools (`DEPLOY_SOLANA_VERSION`, `SOLANA_VERIFY_VERSION`, `SOLANA_VERIFY_RUST_VERSION`), which `build-programs`, `deploy-program` and `verify-program` take from `github.sha` so that an old release can still be built and verified with a verifier that compiles today. The Squads helper crate (`configs/scripts/program/squads-verified-upgrade`) is deploy tooling and comes from `github.sha` as well.

The generated dispatch workflows resolve the `git_ref` they were given to one commit in their `check_tag` job and pass that commit to every build, test, publish, release and deploy job, so a branch that moves during the run, or a branch named like a tag, cannot make one job work on different source than another. `deploy-program` additionally takes the release tag the commit was resolved from (`release_tag`) to download that release's asset instead of the build artifact.

```json
{
  "programs": [
    {
      "name": "mpl-core",
      "package": "mpl-core-program",
      "library": "mpl_core_program",
      "manifest": "programs/mpl-core/Cargo.toml",
      "workspace": ".",
      "build": "verified",
      "release_tag": "core",
      "deploy": {
        "program_id": "CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d",
        "spill_address": "botTxAkJhuCtNNn9xsH8fHJjzTkcN6XD4dR3R5hkzV2",
        "repo_visibility": "public",
        "clusters": {
          "devnet": { "type": "squads", "multisig": "...", "vault": "...", "program_index": 1 },
          "mainnet-beta": { "type": "squads", "multisig": "...", "vault": "...", "program_index": 1 },
          "sonic-devnet": { "type": "direct" }
        }
      }
    }
  ],
  "dump_scripts": ["configs/scripts/program/dump.sh"]
}
```

Only `name` is required. Defaults follow the template layout:

| Field | Default | Meaning |
|---|---|---|
| `package` | `<name>-program` | Cargo package |
| `library` | `<name_with_underscores>_program` | Cargo `[lib].name`; the artifact is `<library>.so` |
| `manifest` | `programs/<name>/Cargo.toml` | Where fmt, clippy and build-sbf run. A root `Cargo.toml` means "workspace member `package`" |
| `workspace` | `.` | Directory solana-verify runs from (the Cargo workspace containing the program) |
| `build` | `verified` | `verified` (solana-verify, digest-pinned image) or `sbf` (cargo build-sbf) |
| `release_tag` | `<name>` | Tag prefix: `release/<release_tag>@x.y.z`, env tag `<release_tag>-<cluster>` |
| `deploy.program_id` | | Expected pubkey; deploy fails if the keypair secret derives another one |
| `deploy.clusters.<c>.type` | | `direct` (DEPLOYER_KEYPAIR signs), `squads` (buffer + proposal) or `squads-buffer` (buffer only, upgrade proposed by hand from the job summary) |
| `dump_scripts` (top level) | `["configs/scripts/program/dump.sh"]` | Scripts run after the build with the output directory as `$1` |

### Pinning and upgrades

Consumers reference a full commit SHA with the release tag in a trailing
comment, and let Dependabot's `github-actions` ecosystem bump it:

```yaml
uses: metaplex-foundation/actions/.github/workflows/build-programs.yml@<sha> # v2.0.0
```

Third-party actions used here are SHA-pinned the same way. `v1` remains for
the composite actions as they were before the reusable workflows; it is not
moved.

### Linting

```bash
actionlint -ignore 'property "workflow_(sha|repository)" is not defined' -ignore SC2016 -ignore SC2129
```

actionlint 1.7.x does not know the `job.workflow_sha` and
`job.workflow_repository` properties yet; the two shellcheck codes are style
notes about markdown backticks and grouped redirects.

## Composite actions

- [Load CI environment](./load-env): allowlisted import of `.github/.env`
- [Sanitize git ref](./sanitize-ref): artifact-safe ref names
- [Program info](./program-info): one entry of `programs.json` as outputs
- [Cache IDL generators](./cache-idl-generators)
- [Cache crate dependencies](./cache-crate): the registry and compiled
  dependencies of the Cargo workspace containing a crate, via
  Swatinem/rust-cache; used by the test and coverage workflows
- [Cache all programs dependencies](./cache-programs): plain `actions/cache`
  over `programs/**/target`, used by the build and generate workflows
- [Cache program dependencies](./cache-program): legacy v1 action, superseded
  by `cache-crate`
- [Filter matrix](./filter-matrix)
- [Install Anchor CLI](./install-anchor-cli)
- [Install cargo-release](./install-cargo-release)
- [Install Node dependencies](./install-node-dependencies)
- [Install Node with PNPM](./install-node-with-pnpm)
- [Install Rust](./install-rust)
- [Install SBF test tools](./install-sbf-tools): pinned, cached
  `cargo-build-sbf` / `cargo-test-sbf` in front of the Solana CLI's copies
- [Install Solana](./install-solana)
- [Install solana-verify](./install-solana-verify)
- [Start a local validator](./start-validator)
- [Verified build](./verified-build)
- [Verify program](./verify-program)

### Known gaps

- `install-solana` still runs the upstream install script without a
  checksum. A per-version SHA-256 table like `solana-verify-base-images.json`
  would close that.
- `start-validator` and `install-node-with-pnpm`'s nested references still
  point at `@v1`; the reusable workflows do not use them and compose the
  primitives directly instead.
