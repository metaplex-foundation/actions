#!/usr/bin/env python3
"""Generate a program repository's thin caller workflows for the shared CI.

Usage: python3 -I scripts/gen-caller.py <spec.json> <repo-dir>

Writes .github/programs.json and .github/workflows/{main,publish-js-client,
publish-rust-client,release-js-client,release-rust-client,create-release,
deploy-program,verify-program}.yml according to the spec. Existing files at
those paths are overwritten; nothing else is touched.
"""
import json
import re
import sys
from pathlib import Path

WF = "metaplex-foundation/actions/.github/workflows"
SPEC = {}


def ref(name):
    sha = SPEC["actions_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        sys.exit(f"actions_sha must be a full 40-character commit SHA, got {sha!r}")
    return f"{WF}/{name}.yml@{sha} # {SPEC.get('actions_tag', 'v2.0.0')}"


def yaml_list(items):
    return "[" + ", ".join(items) + "]"


def with_block(pairs, indent=4):
    pad = " " * indent
    lines = [f"{pad}with:"]
    for k, v in pairs:
        if v is None or v == "" or v is False:
            continue
        lines.append(f"{pad}  {k}: {v}")
    return "\n".join(lines) if len(lines) > 1 else ""


def program_opts(indent=4):
    """`with:` entries every program-test job carries."""
    s = SPEC
    tp = s.get("test_programs", {})
    return [
        ("apt_packages", s.get("apt_packages", "")),
        ("sbf_tools_version", s.get("sbf_tools_version", "")),
        ("runner", s.get("runners", {}).get("test_programs", "")),
        ("free_disk_space", "true" if tp.get("free_disk_space") else ""),
        ("clippy_deny_warnings", "true" if tp.get("clippy_deny_warnings") else ""),
    ]


def rust_client_opts():
    """`with:` entries of test-rust-client, which runs clippy on every crate in
    `rust_client_matrix` and then the client tests, sharing one dependency build."""
    s = SPEC
    return [
        ("apt_packages", s.get("apt_packages", "")),
        ("sbf_tools_version", s.get("sbf_tools_version", "")),
        ("client_dir", s.get("rust_client_test_dir", "")),
        ("client_matrix", f"'{json.dumps(s['rust_client_matrix'])}'" if s.get("rust_client_matrix") else ""),
        ("features", s.get("rust_client_features", "")),
        ("runner", s.get("runners", {}).get("test_rust_client", "")),
    ]


def js_opts():
    """JS clients are tested on NODE_VERSION from .github/.env only: one Node
    release per repository, no matrix."""
    s = SPEC
    return [
        ("client_dir", s.get("js_client_dir", "")),
        ("lint", "false" if s.get("js_lint") is False else ""),
        ("runner", s.get("runners", {}).get("test_js", "")),
    ]


def js_secrets(indent=4):
    pad = " " * indent
    if not SPEC.get("js_test_secrets"):
        return ""
    lines = [f"{pad}secrets:"]
    for name in SPEC["js_test_secrets"]:
        lines.append(f"{pad}  {name}: ${{{{ secrets.{name} }}}}")
    return "\n".join(lines)


def job(name, display, workflow, *, needs=None, cond=None, perms, with_pairs=None, secrets_block="", extra=""):
    lines = [f"  {name}:", f"    name: {display}"]
    if cond:
        lines.append(f"    if: {cond}")
    if needs:
        lines.append(f"    needs: {needs if isinstance(needs, str) else yaml_list(needs)}")
    lines.append(f"    uses: {ref(workflow)}")
    lines.append("    permissions:")
    for p in perms:
        lines.append(f"      {p}")
    if secrets_block:
        lines.append(secrets_block)
    wb = with_block(with_pairs or [])
    if wb:
        lines.append(wb)
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def gen_main():
    s = SPEC
    push = yaml_list(s["main"]["push_branches"])
    pr = yaml_list(s["main"]["pr_branches"]) if s["main"].get("pr_branches") else None
    out = [
        "name: Main\n",
        "# Thin orchestrator over the shared workflows in metaplex-foundation/actions.",
        "# Repository facts live in .github/.env (toolchain pins), .github/programs.json",
        "# (program descriptors) and .github/file-filters.yml (change detection).\n",
        "on:",
        "  push:",
        f"    branches: {push}",
        "  pull_request:" + ("" if pr else " {}"),
    ]
    if pr:
        if s["main"].get("pr_comment"):
            out.append("    " + s["main"]["pr_comment"].replace("\n", "\n    "))
        out.append(f"    branches: {pr}")
    out += ["", "permissions: {}", "", "jobs:"]
    out.append(job("changes", "Detect changes", "detect-changes", perms=["contents: read", "pull-requests: read"]))
    # Clients are generated and tested against the built programs, so a client
    # change rebuilds them; a repo without clients only builds on program changes.
    build_filter = s.get("build_programs_filter", "any")
    out.append(job("build_programs", "Programs", "build-programs", needs="changes",
                   cond=f"needs.changes.outputs.{build_filter} == 'true'", perms=["contents: read"],
                   with_pairs=[("apt_packages", s.get("build_apt_packages", "")),
                               ("runner", s.get("runners", {}).get("build_programs", ""))]))
    out.append(job("test_programs", "Programs", "test-programs", needs="changes",
                   cond="needs.changes.outputs.programs == 'true'", perms=["contents: read"],
                   with_pairs=[("program_matrix", "${{ needs.changes.outputs.program_matrix }}")] + program_opts()))
    if s.get("coverage"):
        cov = s["coverage"] if isinstance(s["coverage"], dict) else {}
        out.append(job("coverage", "Programs", "coverage", needs="changes",
                       cond="needs.changes.outputs.programs == 'true'",
                       perms=["contents: read", "pull-requests: write"],
                       with_pairs=[("program_matrix", "${{ needs.changes.outputs.program_matrix }}"),
                                   ("apt_packages", s.get("apt_packages", "")),
                                   ("pre_command", cov.get("pre_command", ""))]))
    # The client jobs test the committed clients against the program builds,
    # so they wait for build_programs only. generate_clients checks the
    # committed clients for drift from the program source and needs neither
    # the builds nor the tests: it runs alongside them and still fails the run
    # when they drift.
    if s.get("generate_clients", True):
        out.append(job("generate_clients", "Generate clients", "generate-clients", needs="changes",
                       cond="needs.changes.outputs.any == 'true'", perms=["contents: read"],
                       with_pairs=[("install_anchor", "true" if s.get("install_anchor") else ""),
                                   ("runner", s.get("runners", {}).get("generate_clients", ""))]))
    if s.get("js_client", True):
        out.append(job("test_js", "JS Client", "test-js-client", needs=["changes", "build_programs"],
                       cond="needs.changes.outputs.js_client == 'true'", perms=["contents: read"],
                       secrets_block=js_secrets(), with_pairs=js_opts()))
    if s.get("benchmark"):
        bperms = ["contents: write", "deployments: write", "pull-requests: write"]
        out.append(job("benchmark", "Benchmark", "benchmark", needs="build_programs",
                       cond="github.event_name == 'push' && github.ref == 'refs/heads/main'",
                       perms=bperms, with_pairs=[("mode", "store")]))
        out.append(job("benchmark_summary", "Benchmark Summary", "benchmark", needs="build_programs",
                       cond="github.event_name == 'pull_request'", perms=bperms, with_pairs=[("mode", "summary")]))
    if s.get("rust_client", True) and s.get("main_rust_client", True):
        out.append(job("test_rust_client", "Rust Client", "test-rust-client", needs=["changes", "build_programs"],
                       cond="needs.changes.outputs.rust_client == 'true'", perms=["contents: read"],
                       with_pairs=rust_client_opts()))
    return "\n".join(out).rstrip("\n") + "\n"


def release_tags():
    """{program name: release tag prefix}, mirroring program-info's default."""
    return {p["name"]: p.get("release_tag", p["name"]) for p in SPEC["programs"]}


SHA = "${{ needs.check_tag.outputs.sha }}"


def check_tag(comment, per_program, releases=True, reject=True):
    """The check_tag job: classifies git_ref as `release` or `ref` and
    resolves it to one commit (`sha`) that every later job checks out.

    Only this repository's own release tags count as `release`; any other
    release/*@* ref is an error rather than a reason to skip build and test.
    With `per_program` the tag must also belong to the selected program. With
    `releases=False` no ref is a release: create-release (`reject=True`)
    refuses a release/*@* ref outright and emits no `type` output, while a
    repository that cuts no program releases (`reject=False`) builds and
    tests every ref, release-looking or not, since no shared workflow has
    tested what such a tag points at.

    Resolving once matters: an unqualified name is resolved by each
    checkout separately, so a branch that moves between build and publish
    would publish untested code, and a branch named like a tag would win
    over the tag."""
    tags = release_tags()
    head = [
        "  check_tag:",
        "    name: Check tag",
        "    runs-on: ubuntu-latest",
        "    permissions:",
        ("      # Confirms the release tag exists and resolves the ref to a commit."
         if releases else "      # Resolves the ref to a commit."),
        "      contents: read",
        "    outputs:",
    ]
    if releases or not reject:
        head.append("      type: ${{ steps.set_type.outputs.type }}")
    head += [
        "      sha: ${{ steps.set_type.outputs.sha }}",
        "    steps:",
        "      - name: Check tag",
        "        id: set_type",
        "        env:",
        "          GIT_REF: ${{ inputs.git_ref }}",
        "          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    ]
    if per_program:
        head.append("          PROGRAM: ${{ inputs.program }}")
    head += ["        run: |", f"          # {comment}"]
    if not releases and reject:
        body = [
            '          if [[ "$GIT_REF" == release/*@* ]]; then',
            '            echo "::error::a release is cut from a commit or branch, not from the release tag \'$GIT_REF\'"',
            '            exit 1',
            '          fi',
        ] + tag_exists(releases=False, type_output=False)
    elif not releases:
        body = ['          TYPE=ref'] + tag_exists(releases=False)
    elif per_program:
        body = ['          case "$PROGRAM" in']
        for prog, tag in tags.items():
            body.append(f'            {prog}) TAG={tag} ;;')
        body += [
            '            *) echo "::error::unknown program \'$PROGRAM\'"; exit 1 ;;',
            '          esac',
            '          if [[ "$GIT_REF" == release/*@* ]]; then',
            '            if [[ "$GIT_REF" != "release/${TAG}@"* ]]; then',
            '              echo "::error::\'$GIT_REF\' is not a release tag of $PROGRAM (expected release/${TAG}@<version>)"',
            '              exit 1',
            '            fi',
            '            TYPE=release',
            '          else',
            '            TYPE=ref',
            '          fi',
        ] + tag_exists()
    else:
        patterns = "|".join(f"release/{t}@*" for t in dict.fromkeys(tags.values()))
        expected = ", ".join(f"release/{t}@<version>" for t in dict.fromkeys(tags.values()))
        body = [
            '          case "$GIT_REF" in',
            f'            {patterns}) TYPE=release ;;',
            f'            release/*@*) echo "::error::\'$GIT_REF\' is not a release tag of this repository (expected {expected})"; exit 1 ;;',
            '            *) TYPE=ref ;;',
            '          esac',
        ] + tag_exists()
    return "\n".join(head + body) + "\n"


def tag_exists(releases=True, type_output=True):
    """A branch named like a release tag must not skip build and test, and
    every job downstream gets the same commit. Without `releases` no ref is
    a tag to look up, so only the commit resolution is emitted."""
    lines = []
    if releases:
        lines += [
            '          if [ "$TYPE" = release ]; then',
            '            # Only an existing tag skips build and test; a branch named like one does not.',
            '            OBJ="$(gh api "repos/${GITHUB_REPOSITORY}/git/ref/tags/${GIT_REF}" --jq \'.object.type + " " + .object.sha\')" \\',
            '              || { echo "::error::\'$GIT_REF\' is not an existing tag of this repository"; exit 1; }',
            '            read -r OBJ_TYPE SHA <<< "$OBJ"',
            '            if [ "$OBJ_TYPE" = tag ]; then',
            '              # Annotated tag: follow it to the commit.',
            '              SHA="$(gh api "repos/${GITHUB_REPOSITORY}/git/tags/${SHA}" --jq .object.sha)"',
            '            fi',
            '          elif [ -z "$GIT_REF" ]; then',
        ]
    else:
        lines += ['          if [ -z "$GIT_REF" ]; then']
    lines += [
        '            SHA="$GITHUB_SHA"',
        '          else',
        '            SHA="$(gh api "repos/${GITHUB_REPOSITORY}/commits/${GIT_REF}" --jq .sha)" \\',
        '              || { echo "::error::\'$GIT_REF\' is not a commit, branch or tag of this repository"; exit 1; }',
        '          fi',
    ]
    if type_output:
        lines.append('          echo "type=$TYPE" >> "$GITHUB_OUTPUT"')
    lines.append('          echo "sha=$SHA" >> "$GITHUB_OUTPUT"')
    lines.append('          echo "$GIT_REF -> $SHA ($TYPE)"' if type_output else '          echo "$GIT_REF -> $SHA"')
    return lines


def concurrency(group, comment):
    return f"concurrency:\n  # {comment}\n  group: {group}\n  cancel-in-progress: false\n"


def gen_publish_js():
    s = SPEC
    pj = s.get("publish_js", {})
    example = s.get("release_example", "release/<program>@1.0.0")
    out = [
        "name: Publish JS Client\n",
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
        "      git_ref:",
        f"        description: Release tag ({example}) or commit to publish from",
        "        required: false",
        "        type: string",
        "        default: ''",
        "      bump:",
        "        description: Version bump",
        "        required: true",
        "        default: patch",
        "        type: choice",
        "        options: [patch, minor, major, prerelease, prepatch, preminor, premajor]",
        "      tag:",
        "        description: NPM tag (and preid for pre-releases)",
        "        required: true",
        "        type: string",
        "        default: latest",
        "      create_release:",
        "        description: Open the release pull request" + (" and deploy docs" if pj.get("deploy_docs") else ""),
        "        required: true",
        "        type: boolean",
        "        default: true",
        "",
        "permissions: {}",
        "",
        concurrency("publish-js-client", "One JS publish at a time: each bumps the version and opens a release PR."),
        "jobs:",
        (check_tag("A program release tag was already built and tested when it was cut.", per_program=False)
         if s.get("create_release", True) else
         check_tag("This repository cuts no program releases, so no tag has been\n"
                   "          # tested by CI: every ref is built and tested before publishing.",
                   per_program=False, releases=False, reject=False)),
    ]
    out.append(job("build_programs", "Programs", "build-programs", needs="check_tag",
                   cond="needs.check_tag.outputs.type == 'ref'", perms=["contents: read"],
                   with_pairs=[("git_ref", SHA),
                               ("apt_packages", s.get("build_apt_packages", "")),
                               ("runner", s.get("runners", {}).get("build_programs", ""))]))
    out.append(job("test_js", "JS client", "test-js-client", needs=["check_tag", "build_programs"],
                   cond="needs.check_tag.outputs.type == 'ref'", perms=["contents: read"],
                   secrets_block=js_secrets(), with_pairs=[("git_ref", SHA)] + js_opts()))
    secrets = ""
    if pj.get("deploy_docs"):
        secrets = ("    secrets:\n"
                   "      VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}\n"
                   "      VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}\n"
                   "      VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}")
    out.append(job("publish", "JS client", "publish-js-client", needs=["check_tag", "build_programs", "test_js"],
                   cond=gating(["test_js"]),
                   perms=["contents: write", "pull-requests: write", "id-token: write"], secrets_block=secrets,
                   with_pairs=[("git_ref", SHA), ("bump", "${{ inputs.bump }}"),
                               ("tag", "${{ inputs.tag }}"), ("create_release", "${{ inputs.create_release }}"),
                               ("client_dir", s.get("js_client_dir", "")),
                               ("deploy_docs", "true" if pj.get("deploy_docs") else "")]))
    return "\n".join(out).rstrip("\n") + "\n"


def gen_publish_rust():
    s = SPEC
    pr = s.get("publish_rust", {})
    clients = pr.get("clients")  # list of {choice, dir} for multi-client repos
    out = [
        "name: Publish Rust Client\n",
        "run-name: Publish Rust Client (${{ inputs.level }}${{ inputs.dry_run && ', dry run' || '' }})\n",
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
    ]
    if clients:
        out += ["      client:", "        description: Client to publish", "        required: true",
                "        type: choice", "        options: " + yaml_list([c["choice"] for c in clients])]
    out += [
        "      level:",
        "        description: Level",
        "        required: true",
        "        default: patch",
        "        type: choice",
        "        options: [patch, minor, major, rc, beta, alpha, release, version]",
        "      version:",
        "        description: Version (when level is `version`)",
        "        required: false",
        "        type: string",
        "      dry_run:",
        "        description: Dry run",
        "        required: true",
        "        default: true",
        "        type: boolean",
        "",
        "permissions: {}",
        "",
        concurrency("publish-rust-client" + ("-${{ inputs.client }}" if clients else ""),
                    "One Rust publish per crate at a time."),
        "jobs:",
    ]
    out.append(job("build_programs", "Programs", "build-programs", perms=["contents: read"],
                   with_pairs=[("apt_packages", s.get("build_apt_packages", "")),
                               ("runner", s.get("runners", {}).get("build_programs", ""))]))
    out.append(job("test_rust_client", "Rust client", "test-rust-client", needs="build_programs",
                   perms=["contents: read"], with_pairs=rust_client_opts()))
    if clients:
        client_dir = " || ".join(f"inputs.client == '{c['choice']}' && '{c['dir']}'" for c in clients)
        client_dir = "${{ " + client_dir + " || '' }}"
    else:
        client_dir = s.get("rust_client_dir", "")
    out.append(job("publish", "Rust client", "publish-rust-client", needs="test_rust_client",
                   perms=["contents: write", "pull-requests: write"],
                   secrets_block="    secrets:\n      CRATES_TOKEN: ${{ secrets.CRATES_TOKEN }}",
                   with_pairs=[("level", "${{ inputs.level }}"), ("version", "${{ inputs.version }}"),
                               ("dry_run", "${{ inputs.dry_run }}"), ("client_dir", client_dir)]))
    return "\n".join(out).rstrip("\n") + "\n"


def gen_release(kind):
    s = SPEC
    label = "JS client" if kind == "js" else "Rust client"
    wf = f"release-{kind}-client"
    body = [
        f"name: Release {label.replace('client', 'Client')}\n",
        f"# Tags and releases the {label} once the pull request opened by",
        f"# publish-{kind}-client is merged.\n",
        "on:",
        "  pull_request:",
        "    types: [closed]",
        f"    branches: [{s.get('base_branch', 'main')}]",
        "",
        "permissions: {}",
        "",
        "jobs:",
    ]
    pairs = [("merge_commit_sha", "${{ github.event.pull_request.merge_commit_sha }}")]
    if kind == "js" and s.get("js_client_dir"):
        pairs.append(("client_dir", s["js_client_dir"]))
    if kind == "rust":
        clients = s.get("publish_rust", {}).get("clients")
        if clients:
            pairs.append(("client_matrix", "'" + json.dumps([c["dir"] for c in clients]) + "'"))
        elif s.get("rust_client_dir"):
            pairs.append(("client_matrix", "'" + json.dumps([s["rust_client_dir"]]) + "'"))
    # The branch name carries the version publish-*-client released; the
    # release workflow tags only that version.
    pairs.append(("head_ref", "${{ github.event.pull_request.head.ref }}"))
    body.append(job("release", label, wf,
                    cond=f"|\n      github.event.pull_request.merged == true\n      && startsWith(github.event.pull_request.head.ref, 'release/{kind}-client-')\n      && github.event.pull_request.head.repo.full_name == github.repository",
                    perms=["contents: write"], with_pairs=pairs))
    return "\n".join(body).rstrip("\n") + "\n"


def rust_client_gates():
    """Whether the Rust client tests gate main, create-release and deploy.

    `rust_client: false` drops the Rust client entirely. `main_rust_client:
    false` keeps publish/release-rust-client (which test the crate themselves)
    but leaves the Rust client tests out of the CI gates, as a repo that has
    them commented out in main.yml also has them commented out in its
    create-release and deploy workflows (genesis)."""
    return SPEC.get("rust_client", True) and SPEC.get("main_rust_client", True)


def test_jobs_for_release(git_ref_expr, program_expr):
    """build + test_programs + test_js + test_rust jobs used by create-release and deploy.

    Both follow a check_tag job; only deploy skips build and test for a
    release tag, create-release never takes one."""
    s = SPEC
    jobs = []
    cond = "needs.check_tag.outputs.type == 'ref'" if s.get("_deploy") else None
    jobs.append(job("build_programs", "Programs", "build-programs", needs="check_tag", cond=cond,
                    perms=["contents: read"],
                    with_pairs=[("git_ref", git_ref_expr), ("apt_packages", s.get("build_apt_packages", "")),
                                ("runner", s.get("runners", {}).get("build_programs", ""))]))
    needs = ["check_tag", "build_programs"]
    jobs.append(job("test_programs", "Programs", "test-programs", needs=needs, cond=cond, perms=["contents: read"],
                    with_pairs=[("program_matrix", f"'[\"{program_expr}\"]'"), ("git_ref", git_ref_expr)] + program_opts()))
    if s.get("js_client", True):
        jobs.append(job("test_js", "JS client", "test-js-client", needs=needs, cond=cond, perms=["contents: read"],
                        secrets_block=js_secrets(), with_pairs=[("git_ref", git_ref_expr)] + js_opts()))
    if rust_client_gates():
        jobs.append(job("test_rust", "Rust client", "test-rust-client", needs=needs, cond=cond, perms=["contents: read"],
                        with_pairs=[("git_ref", git_ref_expr)] + rust_client_opts()))
    return jobs


def gating(names):
    """`if:` for a job that follows check_tag + build + tests.

    A release tag skips build and test on purpose, so `skipped` must be
    accepted there. On any other ref a skipped test means a dependency
    failed, so build and every test must have succeeded. `!cancelled()`
    instead of `always()` keeps the job from running after a cancel."""
    ok = "\n          && ".join(f"needs.{n}.result == 'success'" for n in ["build_programs"] + list(names))
    return ("|\n      !cancelled() && !failure()\n"
            "      && needs.check_tag.result == 'success'\n"
            "      && (needs.check_tag.outputs.type == 'release'\n"
            f"          || ({ok}))")


def gen_create_release():
    s = SPEC
    rel = s.get("release", {})
    programs = [p["name"] for p in s["programs"]]
    out = [
        "name: Create release\n",
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
        "      program:",
        "        description: Program",
        "        required: true",
        f"        default: {programs[0]}",
        "        type: choice",
        f"        options: {yaml_list(programs)}",
        "      bump:",
        "        description: Version bump",
        "        required: true",
        "        default: patch",
        "        type: choice",
        "        options: [patch, minor, major]",
    ]
    types = rel.get("types")
    if types:
        out += ["      type:", "        description: Release type", "        required: true",
                f"        default: {types[0]}", "        type: choice", f"        options: {yaml_list(types)}"]
    out += [
        "      git_ref:",
        "        description: Commit hash or branch to create the release from",
        "        required: false",
        "        type: string",
        f"        default: {rel.get('default_git_ref', s.get('base_branch', 'main'))}",
        "",
        "permissions: {}",
        "",
        concurrency("create-release-${{ inputs.program }}", "One release per program at a time."),
        "jobs:",
        check_tag("Resolve the branch to one commit: build, test and the release tag must\n          # all refer to the same source, even if the branch moves meanwhile.",
                  per_program=False, releases=False),
    ]
    SPEC["_deploy"] = False
    out += test_jobs_for_release(SHA, "${{ inputs.program }}")
    needs = [n for n in ["test_js", "test_rust", "test_programs"]
             if n != "test_js" or s.get("js_client", True)]
    needs = [n for n in needs if n != "test_rust" or rust_client_gates()]
    # create-release downloads the build artifact, so it waits for the build
    # even when no test job carries that dependency.
    needs = ["check_tag", "build_programs"] + needs
    out.append(job("create_release", "Program", "create-release", needs=needs, perms=["contents: write"],
                   with_pairs=[("program", "${{ inputs.program }}"), ("bump", "${{ inputs.bump }}"),
                               ("type", "${{ inputs.type }}" if types else ""),
                               ("git_ref", SHA)]))
    return "\n".join(out).rstrip("\n") + "\n"


RPC_MAP = [
    ("devnet", "DEVNET_RPC"), ("mainnet-beta", "MAINNET_RPC"), ("sonic-devnet", "SONIC_DEVNET_RPC"),
    ("sonic-testnet", "SONIC_TESTNET_RPC"), ("eclipse-devnet", "ECLIPSE_DEVNET_RPC"),
    ("eclipse-testnet", "ECLIPSE_TESTNET_RPC"), ("eclipse-mainnet", "ECLIPSE_MAINNET_RPC"),
]


def rpc_expr(clusters):
    unknown = [c for c in clusters if c not in dict(RPC_MAP)]
    if unknown:
        sys.exit(f"deploy.clusters {unknown} have no RPC secret in RPC_MAP; add them there first")
    parts = [f"inputs.cluster == '{c}' && secrets.{sec}" for c, sec in RPC_MAP if c in clusters]
    if len(parts) == 1:
        return "${{ " + parts[0].split(" && ")[1] + " }}"
    return ">-\n        ${{ " + "\n         || ".join(parts) + "\n         || '' }}"


def per_program_secret(mapping, fallback=""):
    """mapping: {program: SECRET}. One program -> plain reference."""
    if isinstance(mapping, str):
        return "${{ secrets." + mapping + " }}"
    if len(mapping) == 1:
        return "${{ secrets." + next(iter(mapping.values())) + " }}"
    parts = [f"inputs.program == '{p}' && secrets.{sec}" for p, sec in mapping.items()]
    return ">-\n        ${{ " + "\n         || ".join(parts) + "\n         || '' }}"


def deployer_expr(dep):
    """dep['deployer_secrets']: {prefix: SECRET} or {program: {prefix: SECRET}}."""
    ds = dep.get("deployer_secrets", {})
    if not ds:
        return ""
    def by_prefix(m):
        return [f"startsWith(inputs.cluster, '{pfx}') && secrets.{sec}" if pfx not in ("devnet", "mainnet-beta")
                else f"inputs.cluster == '{pfx}' && secrets.{sec}" for pfx, sec in m.items()]
    if all(isinstance(v, str) for v in ds.values()):
        parts = by_prefix(ds)
    else:
        parts = []
        for prog, m in ds.items():
            for clause in by_prefix(m):
                parts.append(f"inputs.program == '{prog}' && {clause}")
    return ">-\n        ${{ " + "\n         || ".join(parts) + "\n         || '' }}"


def gen_deploy():
    s = SPEC
    dep = s["deploy"]
    programs = [p["name"] for p in s["programs"]]
    clusters = dep["clusters"]
    out = [
        "name: Deploy Program\n",
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
        "      git_ref:",
        f"        description: Release tag ({s.get('release_example', 'release/<program>@1.0.0')}) or commit to deploy",
        "        required: true",
        "        type: string",
        f"        default: {dep.get('default_git_ref', s.get('base_branch', 'main'))}",
        "      program:",
        "        description: Program",
        "        required: true",
        f"        default: {programs[0]}",
        "        type: choice",
        f"        options: {yaml_list(programs)}",
        "      cluster:",
        "        description: Cluster environment",
        "        required: true",
        f"        default: {clusters[0]}",
        "        type: choice",
        "        options:",
    ] + [f"          - {c}" for c in clusters] + [
        "      dry_run:",
        "        description: Dry run",
        "        required: false",
        "        type: boolean",
        "        default: false",
        "",
        "permissions: {}",
        "",
        concurrency("deploy-${{ inputs.program }}-${{ inputs.cluster }}",
                    "One deploy per program and cluster at a time; a running deploy is never cancelled."),
        "jobs:",
        check_tag("A release tag deploys the asset attached to the GitHub release; any\n          # other ref is built and tested first.", per_program=True),
    ]
    SPEC["_deploy"] = True
    out += test_jobs_for_release(SHA, "${{ inputs.program }}")
    SPEC["_deploy"] = False
    gate = [n for n in ["test_js", "test_programs", "test_rust"]
            if (n != "test_js" or s.get("js_client", True)) and (n != "test_rust" or rust_client_gates())]
    secrets = [
        "    # GitHub Actions cannot look secrets up by a computed name, so the",
        "    # per-cluster secrets are mapped onto the shared workflow's fixed names here.",
        "    secrets:",
        f"      RPC_URL: {rpc_expr(clusters)}",
        f"      PROGRAM_ID_KEYPAIR: {per_program_secret(dep['program_id_secrets'])}",
    ]
    de = deployer_expr(dep)
    if de:
        secrets.append(f"      DEPLOYER_KEYPAIR: {de}")
    if dep.get("squads_bot_secret", "SQUADS_BOT_KEY"):
        secrets.append(f"      SQUADS_BOT_KEY: ${{{{ secrets.{dep.get('squads_bot_secret', 'SQUADS_BOT_KEY')} }}}}")
    out.append(job("deploy", "Program", "deploy-program", needs=["check_tag", "build_programs"] + gate, cond=gating(gate),
                   perms=["contents: write"], secrets_block="\n".join(secrets),
                   with_pairs=[("program", "${{ inputs.program }}"), ("cluster", "${{ inputs.cluster }}"),
                               ("git_ref", SHA),
                               ("release_tag", "${{ needs.check_tag.outputs.type == 'release' && inputs.git_ref || '' }}"),
                               ("dry_run", "${{ inputs.dry_run }}"),
                               ("runner", s.get("runners", {}).get("deploy", ""))]))
    return "\n".join(out).rstrip("\n") + "\n"


def gen_verify():
    s = SPEC
    dep = s["deploy"]
    ver = s.get("verify", {})
    programs = [p["name"] for p in s["programs"]]
    clusters = ver.get("clusters", ["mainnet-beta", "devnet"])
    out = [
        "name: Verify Program\n",
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
        "      git_ref:",
        "        description: Release tag, commit hash, or branch that was deployed",
        "        required: true",
        "        type: string",
        "      program:",
        "        description: Program",
        "        required: true",
        f"        default: {programs[0]}",
        "        type: choice",
        f"        options: {yaml_list(programs)}",
        "      cluster:",
        "        description: Cluster environment",
        "        required: true",
        f"        default: {clusters[0]}",
        "        type: choice",
        f"        options: {yaml_list(clusters)}",
        "",
        "permissions: {}",
        "",
        "jobs:",
    ]
    secrets = ["    secrets:", f"      RPC_URL: {rpc_expr(clusters)}",
               f"      PROGRAM_ID_KEYPAIR: {per_program_secret(dep['program_id_secrets'])}"]
    de = deployer_expr(dep) if ver.get("needs_deployer") else ""
    if de:
        secrets.append(f"      DEPLOYER_KEYPAIR: {de}")
    out.append(job("verify", "Program", "verify-program", perms=["contents: read"], secrets_block="\n".join(secrets),
                   with_pairs=[("program", "${{ inputs.program }}"), ("cluster", "${{ inputs.cluster }}"),
                               ("git_ref", "${{ inputs.git_ref }}"),
                               ("runner", s.get("runners", {}).get("verify", ""))]))
    return "\n".join(out).rstrip("\n") + "\n"


def gen_programs_json():
    s = SPEC
    doc = {"$comment": "Program descriptors for the shared CI in metaplex-foundation/actions. Toolchain pins live in .github/.env.",
           "programs": s["programs"]}
    if s.get("dump_scripts"):
        doc["dump_scripts"] = s["dump_scripts"]
    return json.dumps(doc, indent=2) + "\n"


def check_spec():
    """Reject combinations that generate a broken main.yml."""
    s = SPEC
    clients = [k for k, on in [("js_client", s.get("js_client", True)),
                               ("rust_client", s.get("rust_client", True) and s.get("main_rust_client", True)),
                               ("benchmark", bool(s.get("benchmark")))] if on]
    if clients and not s.get("generate_clients", True):
        sys.exit(f"generate_clients: false but {clients} need the generate_clients job; disable them too")
    if clients and s.get("build_programs_filter", "any") != "any":
        sys.exit(f"build_programs_filter must stay 'any' while {clients} are enabled: "
                 "a client-only change must still build the programs the client jobs depend on")


def main():
    global SPEC
    spec_path, repo = sys.argv[1], Path(sys.argv[2])
    SPEC = json.loads(Path(spec_path).read_text())
    check_spec()
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    files = {
        repo / ".github" / "programs.json": gen_programs_json(),
        wf / "main.yml": gen_main(),
    }
    if SPEC.get("create_release", True):
        files[wf / "create-release.yml"] = gen_create_release()
    if SPEC.get("deploy"):
        files[wf / "deploy-program.yml"] = gen_deploy()
        if SPEC.get("verify") is not False:
            files[wf / "verify-program.yml"] = gen_verify()
    if SPEC.get("js_client", True):
        files[wf / "publish-js-client.yml"] = gen_publish_js()
        files[wf / "release-js-client.yml"] = gen_release("js")
    if SPEC.get("rust_client", True):
        files[wf / "publish-rust-client.yml"] = gen_publish_rust()
        files[wf / "release-rust-client.yml"] = gen_release("rust")
    for path, content in files.items():
        path.write_text(content)
        print("wrote", path.relative_to(repo))


if __name__ == "__main__":
    main()
