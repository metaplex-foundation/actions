# Code Review

Automated security and quality review for Solana programs, powered by Claude. On each PR (or manual re-run), the action posts a sticky walkthrough/summary comment and inline review comments anchored to specific findings — the CodeRabbit pattern.

The review is opinionated: it focuses on Solana-program security (account validation, PDA correctness, CPI safety, arithmetic, token/rent handling, Anchor constraints) and quality regressions (new `unwrap()` on instruction paths, new `unsafe`, removed validation, weakened tests). The full curated checklist is in [`prompt.md`](./prompt.md). Per-repo invariants are appended via the `extra_instructions` input.

## Quick start

```yaml
- uses: metaplex-foundation/actions/code-review@v1
  with:
    claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `claude_code_oauth_token` | — | Claude Pro/Max OAuth token. Generate locally with `claude setup-token`. **Preferred.** One of this or `anthropic_api_key` is required. |
| `anthropic_api_key` | — | Anthropic API key. Used only if `claude_code_oauth_token` is empty. |
| `paths` | `programs/**/*.rs` | Newline-separated globs restricting which changed files the review covers. |
| `extra_instructions` | `""` | Markdown appended to the curated baseline under a `## Repository-specific guidance` heading. Use this to encode repo-specific invariants. |
| `model` | `claude-opus-4-7` | The Claude model. Opus is the default for review depth; `claude-sonnet-4-6` is a cheaper alternative. |
| `pr_number` | (current PR) | Explicit PR number, used when triggering via `workflow_dispatch`. Falls back to the current PR context for `pull_request` events. |

## Required permissions

The consuming workflow must grant:

```yaml
permissions:
  contents: read
  pull-requests: write
  issues: write
  id-token: write
```

## Recommended workflow

```yaml
# .github/workflows/code-review.yml
name: Code Review

on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths:
      - 'programs/**/*.rs'
  workflow_dispatch:
    inputs:
      pr_number:
        description: 'PR number to review'
        required: true
        type: number

permissions:
  contents: read
  pull-requests: write
  issues: write
  id-token: write

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
            - Token transfers must use `transfer_checked`, never `transfer`.
            - Any new instruction handler must have a corresponding test that exercises the unauthorized-caller path.
```

### Why these defaults

- **`pull_request`, not `pull_request_target`** — forked PRs deliberately don't get the secret. Reviewing fork PRs requires a separate maintainer-approved flow and is out of scope for this action.
- **`fetch-depth: 0`** — the action needs full history to compute the diff against the base.
- **Skip drafts** — saves Claude credits while a PR is still WIP.
- **`concurrency.cancel-in-progress: true`** — when a new commit lands, supersede the in-flight review rather than wasting credits on a stale diff.
- **Workflow-level `paths` filter** is independent from this action's `paths` input. The workflow filter skips the run entirely; the action input narrows what Claude reviews within a run.

## Manual re-run

To re-trigger a review without pushing a new commit, run the workflow manually with the PR number:

```bash
gh workflow run code-review.yml -f pr_number=123
```

## Authentication

The OAuth flow (`claude_code_oauth_token`) is preferred — it bills against a Claude Pro/Max subscription rather than per-call API credits. To set it up:

1. Locally, run `claude setup-token` and authenticate as the user whose subscription should pay for reviews.
2. Copy the generated token.
3. Add it to the consuming repo (or org) as the secret `CLAUDE_CODE_OAUTH_TOKEN`.

Use `anthropic_api_key` instead when the consuming repo can't sit under a personal Pro/Max subscription (e.g., partner orgs, contractor-run repos).

## Out of scope (v1)

- Reviewing PRs from forks.
- Failing the build on `critical` findings — v1 is advisory-only.
- Custom severity thresholds (suppress `low`, etc.).
- Non-Rust review (TS clients, Python tooling).
