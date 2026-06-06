# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

Collection of reusable **composite GitHub Actions** for Solana development at Metaplex. There is no source code, no build, and no test suite — each top-level directory is one published action consumed by other repos as `metaplex-foundation/actions/<action-name>@v1`.

## Layout convention

Every action is a directory containing at minimum:
- `action.yml` — composite action definition (`runs.using: "composite"`)
- `README.md` — usage example and input/output reference

Some actions also ship supporting assets (e.g., `code-review/prompt.md` is a multi-hundred-line prompt template kept out of `action.yml` for readability). When an action's logic or content would bloat `action.yml`, prefer a sibling file loaded at runtime via `${{ github.action_path }}` over embedding it in YAML.

When adding a new action, follow that structure and add a link to the new directory in the root `README.md`.

## Cross-action dependencies

Some actions wrap others in this repo via `metaplex-foundation/actions/<name>@v1`. When editing one, check who calls it:
- `start-validator` → uses `install-solana` and `install-node-with-pnpm`
- `install-node-with-pnpm` → uses `install-node-dependencies`

Because callers reference the `@v1` tag (not a local path), changes only take effect for downstream repos after the `v1` tag is moved. Local cross-references in this repo also resolve via the published tag, not the working tree.

## Recurring patterns to preserve

- **Cache toggle:** any action that installs or downloads something exposes a `cache: "true"` input gating an `actions/cache@v4` step. Cache keys follow `${{ runner.os }}-<name>-v<version>` or `${{ runner.os }}-<key>-${{ hashFiles(...) }}` with a fallback `restore-keys`. Match this when adding new install/cache actions.
- **PATH export:** install actions append the tool's bin directory to `$GITHUB_PATH` and finish with a `--version` verification step.
- **Inputs are always strings.** Booleans are passed as `"true"` / `"false"` and compared as strings (`if: inputs.cache == 'true'`).

## `install-solana` base URL logic

`install-solana/action.yml` selects the download host by version: `https://release.solana.com` for versions strictly below `1.18.19`, `https://release.anza.xyz` for `1.18.19` and above. The comparison uses `sort -V`. A `base_url` input overrides this. Don't simplify this branching without considering older Solana versions still in use.

## `filter-matrix` quirks

`filter-matrix/action.yml` does string munging on JSON: it replaces `-` with `_` in change keys to match `dorny/paths-filter` filter names (which are typically snake_case), then uses `jq` to intersect with the input matrix. The `prefix`/`suffix` inputs are concatenated onto each matrix entry before lookup. Be careful editing the `sed` pipeline — entries containing literal quotes or hyphens are sensitive to it.

## Testing changes

There is no automated test harness. Validate changes by:
1. Reading the action's `README.md` example to confirm the input/output contract still holds.
2. Running the action from a consuming repo's workflow against a branch ref (e.g. `metaplex-foundation/actions/install-solana@my-branch`) before moving the `v1` tag.
