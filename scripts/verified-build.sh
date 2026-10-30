#!/usr/bin/env bash
# Deterministic program build through `solana-verify build`.
#
# Inputs are environment variables so the same script serves the
# `verified-build` composite action and the reusable build-programs workflow:
#
#   LIBRARY_NAME      (required) Cargo [lib] name; the artifact is <LIBRARY_NAME>.so
#   PACKAGE_NAME      Cargo package passed after `--` (needed in multi-member workspaces)
#   SOLANA_VERSION    resolved to a digest-pinned image via solana-verify-base-images.json
#   BASE_IMAGE        explicit image; takes precedence over SOLANA_VERSION
#   ALLOW_MUTABLE_TAG "true" to accept a non-digest BASE_IMAGE (not recommended)
#   WORKING_DIR       directory to run from (default .)
#   OUTPUT_DIR        where to copy the .so, relative to WORKING_DIR (optional)
#   EXTRA_ARGS        extra args forwarded to the inner cargo build-sbf
#   LOOKUP_FILE       path to solana-verify-base-images.json
#
# Prints `binary-path=<path>` on stdout as its last line.
set -euo pipefail

: "${LIBRARY_NAME:?LIBRARY_NAME is required}"
WORKING_DIR="${WORKING_DIR:-.}"
LOOKUP_FILE="${LOOKUP_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/solana-verify-base-images.json}"

cd "$WORKING_DIR"

# Resolve base-image: explicit input wins, otherwise look up by Solana
# version against the digest table.
if [ -z "${BASE_IMAGE:-}" ] && [ -n "${SOLANA_VERSION:-}" ]; then
  if [ ! -f "${LOOKUP_FILE}" ]; then
    echo "::error::solana-version lookup table not found at ${LOOKUP_FILE}"
    exit 1
  fi
  BASE_IMAGE=$(jq -r --arg v "${SOLANA_VERSION}" '.images[$v] // empty' "${LOOKUP_FILE}")
  if [ -z "${BASE_IMAGE}" ]; then
    KNOWN=$(jq -r '.images | keys | join(", ")' "${LOOKUP_FILE}")
    echo "::error::no known digest for solana-version '${SOLANA_VERSION}'. Pass 'base-image' explicitly or add an entry to solana-verify-base-images.json. Known versions: ${KNOWN}"
    exit 1
  fi
fi

# Reject mutable Docker tags by default. A re-pushed upstream tag can swap the
# verified-build image and silently change the .so hash; a digest pins one
# specific image manifest.
if [ -n "${BASE_IMAGE:-}" ] && [[ ! "${BASE_IMAGE}" =~ ^[^@[:space:]]+@sha256:[a-f0-9]{64}$ ]]; then
  if [ "${ALLOW_MUTABLE_TAG:-false}" = "true" ]; then
    echo "::warning::base-image '${BASE_IMAGE}' is not digest-pinned (allow-mutable-tag=true). Builds may not be reproducible."
  else
    echo "::error::base-image '${BASE_IMAGE}' must be digest-pinned (e.g. <image>@sha256:<digest>). Pass allow-mutable-tag: true to bypass."
    exit 1
  fi
fi

ARGS=(solana-verify build)
if [ -n "${BASE_IMAGE:-}" ]; then
  ARGS+=(--base-image "${BASE_IMAGE}")
fi
ARGS+=(--library-name "${LIBRARY_NAME}")
ARGS+=(--)
if [ -n "${PACKAGE_NAME:-}" ]; then
  ARGS+=(--package "${PACKAGE_NAME}")
fi
if [ -n "${EXTRA_ARGS:-}" ]; then
  # shellcheck disable=SC2206
  EXTRA=(${EXTRA_ARGS})
  ARGS+=("${EXTRA[@]}")
fi

echo "+ ${ARGS[*]}"
"${ARGS[@]}"

SOURCE="target/deploy/${LIBRARY_NAME}.so"
if [ ! -f "${SOURCE}" ]; then
  echo "::error::expected build output ${SOURCE} not found"
  exit 1
fi

BINARY_PATH="${SOURCE}"
if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "${OUTPUT_DIR}"
  DEST="${OUTPUT_DIR%/}/${LIBRARY_NAME}.so"
  cp "${SOURCE}" "${DEST}"
  BINARY_PATH="${DEST}"
fi

printf 'binary-path=%s\n' "${BINARY_PATH}"
