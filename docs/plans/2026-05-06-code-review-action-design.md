# Design — `code-review` action

**Date:** 2026-05-06
**Status:** Validated, ready for implementation

## Purpose

Add a new GitHub Action, `metaplex-foundation/actions/code-review`, that runs an automated security- and quality-focused review on PRs targeting Solana programs and posts findings back on the PR. The action is consumed by Metaplex program repos to catch security regressions (missing signer/owner checks, PDA bump misuse, unchecked arithmetic, CPI safety, Anchor-constraint gaps) before merge.

## Architecture

Thin composite wrapper around `anthropics/claude-code-action@v1`. Our value-add is concentrated in three places: the curated Solana security review prompt, sensible PR defaults, and a stable entry point under the `metaplex-foundation/actions` namespace.

**Files added under `code-review/`:**

- `action.yml` — composite action definition.
- `prompt.md` — the curated baseline review prompt. Lives in a separate file so it can be edited and diffed without YAML escaping noise.
- `README.md` — usage examples (PR trigger + manual re-run), input reference, required permissions.

The root `README.md` gets a link to `code-review`. While editing it, also backfill the missing entries for `cache-crate`, `cache-crates`, and `install-cargo-release`.

## Composite steps

1. **Validate auth** — fail fast with a clear error if neither `claude_code_oauth_token` nor `anthropic_api_key` is set.
2. **Assemble prompt** — copy `${GITHUB_ACTION_PATH}/prompt.md` to `${RUNNER_TEMP}/review-prompt.md`, append the consumer's `extra_instructions` under a `## Repository-specific guidance` heading, and inject the `paths` value into the prompt so Claude knows the review scope. Output the temp path.
3. **Run Claude review** — `uses: anthropics/claude-code-action@v1` with the assembled prompt, auth, model, sticky-comment flag, and `pr_number` (for `workflow_dispatch`).

`anthropics/claude-code-action` is pinned to `@v1`. Don't track `@main`.

## Inputs

| Input | Required | Default | Purpose |
| --- | --- | --- | --- |
| `claude_code_oauth_token` | one-of-two | — | Pro/Max OAuth token, generated locally via `claude setup-token`. Preferred. |
| `anthropic_api_key` | one-of-two | — | API key fallback. Used only if OAuth token is empty. |
| `paths` | no | `programs/**/*.rs` | Glob (or newline-separated globs) restricting which changed files the review covers. |
| `extra_instructions` | no | `""` | Markdown appended to the curated baseline — repo-specific invariants. |
| `model` | no | `claude-opus-4-7` | Override if a repo wants cheaper Sonnet reviews. Opus is the default; security review benefits most from reasoning depth. |
| `pr_number` | no | — | Explicit PR number, used when triggered via `workflow_dispatch`. Falls back to the current PR context when running on `pull_request`. |

No outputs — the action's effect is the comments it posts.

## Review surface

The prompt instructs Claude to behave like a CodeRabbit-style reviewer:

- **Sticky summary comment** — one comment per PR, replaced on each push (`use_sticky_comment: true`). Contains a short walkthrough plus a findings table grouped by severity.
- **Inline review comments** — one per `medium`-or-above finding, anchored to the offending lines via the GitHub Reviews API. Each includes severity tag, the issue, the risk, and a suggested fix. Quotes 1–3 lines of context max.

Severity tiers: `critical` (exploitable / fund loss), `high` (security regression, missing required check), `medium` (correctness or DoS), `low` (quality / maintainability), `nit` (suppressed by default — listed in summary, not posted inline).

## Prompt baseline (`prompt.md`)

The curated checklist covers Solana-program security and quality:

- **Account validation** — owner checks, signer checks, writable checks, account-type confusion via raw deserialize without discriminator, expected-account-count.
- **PDA correctness** — canonical bump (`find_program_address`) vs attacker-controlled bump (`create_program_address` with passed-in bump), seeds match, PDA collision footguns.
- **CPI safety** — program ID validation before `invoke`, correct signer seeds for `invoke_signed`, no arbitrary CPI to user-controlled programs.
- **Arithmetic** — checked math on lamports / token amounts / supply, cast safety between signed and unsigned, overflow on attacker-controlled inputs.
- **Token & rent** — SPL Token / Token-2022 program ID validation, mint and decimals checks, lamport drainage via close-account, rent-exemption on new accounts.
- **Anchor-specific** — missing `#[account(...)]` constraints (`mut`, `signer`, `has_one`, `owner`, `seeds`, `bump`), `init_if_needed` re-init risk, `close = receiver` correctness.
- **Quality regressions** — new `unwrap()`/`expect()` in instruction paths, new `unsafe`, new `#[allow(...)]`, new unaudited Cargo deps, removed validation, weakened tests.

The prompt closes with a `## Repository-specific guidance` section populated from `extra_instructions`.

## Consumer workflow

```yaml
# .github/workflows/code-review.yml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths: ['programs/**/*.rs']
  workflow_dispatch:
    inputs:
      pr_number:
        description: 'PR number to review'
        required: true
        type: number

permissions:
  pull-requests: write
  contents: read
  issues: write

concurrency:
  group: code-review-pr-${{ github.event.pull_request.number || inputs.pr_number }}
  cancel-in-progress: true

jobs:
  review:
    if: ${{ github.event_name == 'workflow_dispatch' || !github.event.pull_request.draft }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          ref: ${{ github.event.pull_request.head.sha || format('refs/pull/{0}/merge', inputs.pr_number) }}
      - uses: metaplex-foundation/actions/code-review@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          pr_number: ${{ inputs.pr_number }}
          extra_instructions: |
            - The Authority PDA must use seeds [b"authority", mint.key()].
            - Use transfer_checked, not transfer.
```

Notes called out in the README:

- **`pull_request`, not `pull_request_target`** — forked PRs deliberately don't get the secret. Reviewing fork PRs requires a separate maintainer-approved flow; out of scope for v1.
- **`fetch-depth: 0`** — required so the action can compute the diff against the base.
- **Skip drafts** — saves credits on WIP work.
- **Concurrency cancel** — supersede an in-flight review when a new commit lands.
- **Workflow-level `paths`** is independent from the action's `paths` input. Workflow-level skips the run entirely; action-level narrows what Claude reviews within a run.

## Testing & rollout

1. **Self-test workflow** in this repo, `workflow_dispatch`-only, that runs `./code-review` (local action ref) against a hand-picked PR in a real Solana repo. Iterate on the prompt without moving the `v1` tag. Gated on a maintainer-only secret so randos don't burn the org's Claude quota.
2. **Pilot in one repo** — point one Metaplex program repo at `metaplex-foundation/actions/code-review@<branch>` for ~1 week before promoting `v1`. Standard branch-ref pattern from `CLAUDE.md`.
3. **Prompt-tuning loop** — when a reviewer disagrees with a finding (false positive or miss), the fix goes upstream into `prompt.md` if generalizable, otherwise into the consuming repo's `extra_instructions`. The discipline is in that line.
4. **Rollback** — consumers can pin `@<sha>` instead of `@v1` while we fix forward. No secrets need rotating.

## Out of scope for v1

- Fork PR support
- Custom severity thresholds (e.g., suppress `low` entirely)
- Failing the build on `critical` findings (advisory-only for v1)
- TS client / non-Rust review

These are the obvious next iterations once v1 is in production and we have feedback.
