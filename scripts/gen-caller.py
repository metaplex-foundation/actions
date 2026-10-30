#!/usr/bin/env python3
"""Generate a program repository's thin caller workflows for the shared CI.

Usage: python3 -I gen_ci.py <spec.json> <repo-dir>

Writes .github/programs.json and .github/workflows/{main,publish-js-client,
publish-rust-client,release-js-client,release-rust-client,create-release,
deploy-program,verify-program}.yml according to the spec. Existing files at
those paths are overwritten; nothing else is touched.
"""
import json
import sys
from pathlib import Path

WF = "metaplex-foundation/actions/.github/workflows"
SPEC = {}


def ref(name):
    return f"{WF}/{name}.yml@{SPEC['actions_sha']} # {SPEC.get('actions_tag', 'v2.0.0')}"


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
    s = SPEC
    return [
        ("apt_packages", s.get("apt_packages", "")),
        ("sbf_tools_version", s.get("sbf_tools_version", "")),
        ("client_dir", s.get("rust_client_test_dir", "")),
        ("runner", s.get("runners", {}).get("test_rust_client", "")),
    ]


def build_rust_client_opts():
    s = SPEC
    return [
        ("apt_packages", s.get("apt_packages", "")),
        ("client_matrix", f"'{json.dumps(s['rust_client_matrix'])}'" if s.get("rust_client_matrix") else ""),
        ("features", s.get("rust_client_features", "")),
    ]


def js_opts():
    s = SPEC
    return [
        ("client_dir", s.get("js_client_dir", "")),
        ("node_matrix", f"'{json.dumps(s['node_matrix'])}'" if s.get("node_matrix") else ""),
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
    out.append(job("build_programs", "Programs", "build-programs", needs="changes",
                   cond="needs.changes.outputs.any == 'true'", perms=["contents: read"],
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
    out.append(job("generate_clients", "Generate clients", "generate-clients", needs=["changes", "build_programs"],
                   cond="needs.changes.outputs.any == 'true'", perms=["contents: read"],
                   with_pairs=[("install_anchor", "true" if s.get("install_anchor") else ""),
                               ("runner", s.get("runners", {}).get("generate_clients", ""))]))
    if s.get("js_client", True):
        out.append(job("test_js", "JS Client", "test-js-client", needs=["changes", "generate_clients"],
                       cond="needs.changes.outputs.js_client == 'true'", perms=["contents: read"],
                       secrets_block=js_secrets(), with_pairs=js_opts()))
    if s.get("benchmark"):
        bperms = ["contents: write", "deployments: write", "pull-requests: write"]
        out.append(job("benchmark", "Benchmark", "benchmark", needs="generate_clients",
                       cond="github.event_name == 'push' && github.ref == 'refs/heads/main'",
                       perms=bperms, with_pairs=[("mode", "store")]))
        out.append(job("benchmark_summary", "Benchmark Summary", "benchmark", needs="generate_clients",
                       cond="github.event_name == 'pull_request'", perms=bperms, with_pairs=[("mode", "summary")]))
    if s.get("rust_client", True) and s.get("main_rust_client", True):
        out.append(job("build_rust_client", "Rust Client", "build-rust-client", needs=["changes", "generate_clients"],
                       cond="needs.changes.outputs.rust_client == 'true'", perms=["contents: read"],
                       with_pairs=build_rust_client_opts()))
        out.append(job("test_rust_client", "Rust Client", "test-rust-client", needs=["changes", "generate_clients"],
                       cond="needs.changes.outputs.rust_client == 'true'", perms=["contents: read"],
                       with_pairs=rust_client_opts()))
    return "\n".join(out).rstrip("\n") + "\n"


CHECK_TAG = """  check_tag:
    name: Check tag
    runs-on: ubuntu-latest
    permissions: {}
    outputs:
      type: ${{ steps.set_type.outputs.type }}
    steps:
      - name: Check tag
        id: set_type
        env:
          GIT_REF: ${{ inputs.git_ref }}
        run: |
          # %s
          if [[ "$GIT_REF" == release/*@* ]]; then
            echo "type=release" >> "$GITHUB_OUTPUT"
          else
            echo "type=ref" >> "$GITHUB_OUTPUT"
          fi
"""


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
        "jobs:",
        CHECK_TAG % "A program release tag was already built and tested when it was cut.",
    ]
    out.append(job("build_programs", "Programs", "build-programs", needs="check_tag",
                   cond="needs.check_tag.outputs.type == 'ref'", perms=["contents: read"],
                   with_pairs=[("git_ref", "${{ inputs.git_ref }}"),
                               ("apt_packages", s.get("build_apt_packages", "")),
                               ("runner", s.get("runners", {}).get("build_programs", ""))]))
    out.append(job("test_js", "JS client", "test-js-client", needs=["check_tag", "build_programs"],
                   cond="needs.check_tag.outputs.type == 'ref'", perms=["contents: read"],
                   secrets_block=js_secrets(), with_pairs=[("git_ref", "${{ inputs.git_ref }}")] + js_opts()))
    secrets = ""
    if pj.get("deploy_docs"):
        secrets = ("    secrets:\n"
                   "      VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}\n"
                   "      VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}\n"
                   "      VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}")
    out.append(job("publish", "JS client", "publish-js-client", needs=["check_tag", "test_js"],
                   cond="|\n      always()\n      && (needs.test_js.result == 'success' || needs.test_js.result == 'skipped')",
                   perms=["contents: write", "pull-requests: write", "id-token: write"], secrets_block=secrets,
                   with_pairs=[("git_ref", "${{ inputs.git_ref }}"), ("bump", "${{ inputs.bump }}"),
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
        "jobs:",
    ]
    out.append(job("build_programs", "Programs", "build-programs", perms=["contents: read"],
                   with_pairs=[("apt_packages", s.get("build_apt_packages", "")),
                               ("runner", s.get("runners", {}).get("build_programs", ""))]))
    out.append(job("build_rust_client", "Rust client", "build-rust-client", perms=["contents: read"],
                   with_pairs=build_rust_client_opts()))
    out.append(job("test_rust_client", "Rust client", "test-rust-client", needs=["build_programs", "build_rust_client"],
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
    """build + test_programs + test_js + test_rust jobs used by create-release and deploy."""
    s = SPEC
    jobs = []
    jobs.append(job("build_programs", "Programs", "build-programs", needs="check_tag" if s.get("_deploy") else None,
                    cond="needs.check_tag.outputs.type == 'ref'" if s.get("_deploy") else None,
                    perms=["contents: read"],
                    with_pairs=[("git_ref", git_ref_expr), ("apt_packages", s.get("build_apt_packages", "")),
                                ("runner", s.get("runners", {}).get("build_programs", ""))]))
    needs = ["check_tag", "build_programs"] if s.get("_deploy") else None
    cond = "needs.check_tag.outputs.type == 'ref'" if s.get("_deploy") else None
    jobs.append(job("test_programs", "Programs", "test-programs", needs=needs, cond=cond, perms=["contents: read"],
                    with_pairs=[("program_matrix", f"'[\"{program_expr}\"]'"), ("git_ref", git_ref_expr)] + program_opts()))
    if s.get("js_client", True):
        jobs.append(job("test_js", "JS client", "test-js-client",
                        needs=needs or "build_programs", cond=cond, perms=["contents: read"],
                        secrets_block=js_secrets(), with_pairs=[("git_ref", git_ref_expr)] + js_opts()))
    if rust_client_gates():
        jobs.append(job("test_rust", "Rust client", "test-rust-client",
                        needs=needs or "build_programs", cond=cond, perms=["contents: read"],
                        with_pairs=[("git_ref", git_ref_expr)] + rust_client_opts()))
    return jobs


def gating(names):
    conds = [f"(needs.{n}.result == 'success' || needs.{n}.result == 'skipped')" for n in names]
    return "|\n      always()\n      && " + "\n      && ".join(conds)


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
        "jobs:",
    ]
    SPEC["_deploy"] = False
    out += test_jobs_for_release("${{ inputs.git_ref }}", "${{ inputs.program }}")
    needs = [n for n in ["test_js", "test_rust", "test_programs"]
             if n != "test_js" or s.get("js_client", True)]
    needs = [n for n in needs if n != "test_rust" or rust_client_gates()]
    out.append(job("create_release", "Program", "create-release", needs=needs, perms=["contents: write"],
                   with_pairs=[("program", "${{ inputs.program }}"), ("bump", "${{ inputs.bump }}"),
                               ("type", "${{ inputs.type }}" if types else ""),
                               ("git_ref", "${{ inputs.git_ref }}")]))
    return "\n".join(out).rstrip("\n") + "\n"


RPC_MAP = [
    ("devnet", "DEVNET_RPC"), ("mainnet-beta", "MAINNET_RPC"), ("sonic-devnet", "SONIC_DEVNET_RPC"),
    ("sonic-testnet", "SONIC_TESTNET_RPC"), ("eclipse-devnet", "ECLIPSE_DEVNET_RPC"),
    ("eclipse-testnet", "ECLIPSE_TESTNET_RPC"), ("eclipse-mainnet", "ECLIPSE_MAINNET_RPC"),
]


def rpc_expr(clusters):
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
        "jobs:",
        CHECK_TAG % "A release tag deploys the asset attached to the GitHub release; any\n          # other ref is built and tested first.",
    ]
    SPEC["_deploy"] = True
    out += test_jobs_for_release("${{ inputs.git_ref }}", "${{ inputs.program }}")
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
    out.append(job("deploy", "Program", "deploy-program", needs=["check_tag"] + gate, cond=gating(gate),
                   perms=["contents: write"], secrets_block="\n".join(secrets),
                   with_pairs=[("program", "${{ inputs.program }}"), ("cluster", "${{ inputs.cluster }}"),
                               ("git_ref", "${{ inputs.git_ref }}"), ("dry_run", "${{ inputs.dry_run }}"),
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


def main():
    global SPEC
    spec_path, repo = sys.argv[1], Path(sys.argv[2])
    SPEC = json.loads(Path(spec_path).read_text())
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    files = {
        repo / ".github" / "programs.json": gen_programs_json(),
        wf / "main.yml": gen_main(),
        wf / "create-release.yml": gen_create_release(),
    }
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
