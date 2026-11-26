#!/usr/bin/env bash
# Prepares an IDL upload that a Squads vault has to sign.
#
# The canonical metadata account of a program can only be written by the
# program's upgrade authority. When that is a Squads vault, the submitter key:
#
#   1. writes the IDL into a Program Metadata buffer,
#   2. exports the vault's transactions (allocate, copy from the buffer,
#      initialise or update, close the buffer back to the submitter),
#   3. sends the vault the rent those transactions will move into the metadata
#      account, so the vault's own balance is left untouched,
#   4. hands the buffer to the vault,
#   5. with PROPOSE=true, creates and activates the Squads proposals. It never
#      approves them: the members still approve and execute, in order.
#
# The export has at most one 10KB extend per transaction, which is what a
# Squads v3 vault can execute through CPI. It is then regrouped so that each
# group fits one proposal (squads-proposals.mjs), and written to messages.txt
# in the form the "Import Transaction" dialog of squads-deploy-ui accepts,
# whether or not the proposals are created here.
#
# Environment:
#   RPC_URL       cluster RPC endpoint
#   PROGRAM_ID    program the IDL belongs to
#   IDL_FILE      IDL to upload (JSON)
#   MULTISIG      Squads v3 multisig
#   VAULT         its vault, the program's upgrade authority
#   BOT_KEYPAIR   path to the submitter keypair
#   PM            path to the program-metadata CLI
#   OUT_DIR       directory for messages.txt, proposals.txt, instructions.txt
#                 and result.env
#   PROPOSE       "true" to create the proposals (the submitter must be a
#                 member of the multisig); anything else only writes messages
#   ZLIB_BYTES    optional compressed IDL size, for the balance check
#
# Requires: curl, jq, node, solana, solana-keygen.

set -euo pipefail

: "${RPC_URL:?}" "${PROGRAM_ID:?}" "${IDL_FILE:?}" "${MULTISIG:?}" "${VAULT:?}" "${BOT_KEYPAIR:?}" "${PM:?}" "${OUT_DIR:?}"

TOOLS="$(cd "$(dirname "$0")" && pwd)"
PROPOSE="${PROPOSE:-false}"

export NO_COLOR=1
ADDRESS='[1-9A-HJ-NP-Za-km-z]{32,44}'
# Header that precedes the data in buffer and metadata accounts.
ACCOUNT_HEADER=96
# Transaction fees for the buffer writes, the transfer and the handover.
FEE_MARGIN=10000000

mkdir -p "$OUT_DIR"

fail() {
  echo "::error::$*"
  exit 1
}

rpc() {
  curl -sS --fail -X POST "$RPC_URL" -H 'content-type: application/json' -d "$1"
}

# The buffer is read right after it is created, so these look at confirmed
# state rather than the RPC default (finalized).

# Prints the size of an account's data, or "missing".
account_space() {
  rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getAccountInfo\",\"params\":[\"$1\",{\"encoding\":\"base64\",\"commitment\":\"confirmed\",\"dataSlice\":{\"offset\":0,\"length\":0}}]}" |
    jq -er 'if .error then error(.error.message) elif .result.value == null then "missing" else .result.value.space end'
}

rent() {
  rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getMinimumBalanceForRentExemption\",\"params\":[$1]}" |
    jq -er 'if .error then error(.error.message) else .result end'
}

balance() {
  rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getBalance\",\"params\":[\"$1\",{\"commitment\":\"confirmed\"}]}" |
    jq -er 'if .error then error(.error.message) else .result.value end'
}

sol() {
  awk -v lamports="$1" 'BEGIN { printf "%.9f", lamports / 1000000000 }'
}

# Rent the vault's transactions move into the metadata account for an IDL
# that takes $1 bytes of account space: all of it for a new account, the
# difference for one that grows, nothing for one that shrinks.
#
# Callers run this in a command substitution, where bash drops `set -e`, so
# each query is checked here: a failed one must stop the run rather than
# turn into "no funding needed".
metadata_funding() {
  local funding existing
  funding="$(rent "$1")" || return 1
  [[ "$funding" =~ ^[0-9]+$ ]] || return 1
  if [ "$METADATA_SPACE" != "missing" ]; then
    existing="$(rent "$METADATA_SPACE")" || return 1
    [[ "$existing" =~ ^[0-9]+$ ]] || return 1
    funding=$((funding - existing))
    [ "$funding" -gt 0 ] || funding=0
  fi
  echo "$funding"
}

BOT="$(solana-keygen pubkey "$BOT_KEYPAIR")"
METADATA="$(node "$TOOLS/metadata-address.mjs" "$PROGRAM_ID")"
METADATA_SPACE="$(account_space "$METADATA")"
BUFFER=""
FUNDED=0
# Rent whose transfer reported an error and did not show in the vault's balance.
FUNDING_UNSEEN=0
HANDED_OVER=false

# A buffer the vault never received is of no use to anyone: give its rent
# back to the submitter. Rent already sent to the vault stays there.
cleanup() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ -n "$BUFFER" ] && [ "$HANDED_OVER" = false ]; then
    echo "Closing buffer $BUFFER after the failure" >&2
    "$PM" close-buffer "$BUFFER" --keypair "$BOT_KEYPAIR" --rpc "$RPC_URL" >&2 ||
      echo "::warning::Could not close buffer $BUFFER. Close it with: program-metadata close-buffer $BUFFER"
    if [ "$FUNDED" -gt 0 ]; then
      echo "::warning::Vault $VAULT already received $(sol "$FUNDED") SOL for the metadata account's rent. The next run sends that amount again, so the vault keeps this one as a surplus to withdraw."
    fi
  fi
  # Reported whether or not a buffer is left: this is about the vault.
  if [ "$status" -ne 0 ] && [ "$FUNDING_UNSEEN" -gt 0 ]; then
    echo "::warning::A transfer of $(sol "$FUNDING_UNSEEN") SOL to vault $VAULT reported an error and did not show in the vault's balance, but may still have been sent. Check the vault before the next run, which sends that amount again."
  fi
  exit "$status"
}
trap cleanup EXIT

# Before anything is paid for: the vault must be this multisig's, and a
# submitter that is to propose must be one of its members.
if [ "$PROPOSE" = "true" ]; then
  node "$TOOLS/squads-proposals.mjs" --preflight --multisig "$MULTISIG" --vault "$VAULT" --keypair "$BOT_KEYPAIR" --rpc "$RPC_URL"
else
  node "$TOOLS/squads-proposals.mjs" --preflight --multisig "$MULTISIG" --vault "$VAULT"
fi

if [ -n "${ZLIB_BYTES:-}" ]; then
  ESTIMATE="$(rent $((ACCOUNT_HEADER + ZLIB_BYTES)))"
  # Rent for the buffer (returned when the vault closes it) plus the rent
  # sent to the vault for the metadata account.
  ESTIMATED_FUNDING="$(metadata_funding $((ACCOUNT_HEADER + ZLIB_BYTES)))" || fail "Could not read the rent for the metadata account"
  REQUIRED=$((ESTIMATE + ESTIMATED_FUNDING + FEE_MARGIN))
  AVAILABLE="$(balance "$BOT")"
  if [ "$AVAILABLE" -lt "$REQUIRED" ]; then
    fail "Submitter $BOT holds $(sol "$AVAILABLE") SOL, the upload needs about $(sol "$REQUIRED") SOL ($(sol "$ESTIMATE") of it comes back when the vault closes the buffer)"
  fi
fi

echo "Writing the IDL to a buffer owned by $BOT" >&2
CREATE_STATUS=0
"$PM" create-buffer "$IDL_FILE" --keypair "$BOT_KEYPAIR" --rpc "$RPC_URL" > "$OUT_DIR/create-buffer.log" 2>&1 || CREATE_STATUS=$?
cat "$OUT_DIR/create-buffer.log" >&2
# Read the address even after a failure, so a half-written buffer is closed.
BUFFER="$(grep -oE "buffer: $ADDRESS" "$OUT_DIR/create-buffer.log" | head -n 1 | awk '{print $2}' || true)"
[ "$CREATE_STATUS" -eq 0 ] || fail "program-metadata create-buffer failed"
[ -n "$BUFFER" ] || fail "Could not read the buffer address from the create-buffer output"

BUFFER_SPACE="$(account_space "$BUFFER")"
[ "$BUFFER_SPACE" != "missing" ] || fail "Buffer $BUFFER was not found after it was created"

export_transactions() {
  "$PM" write idl "$PROGRAM_ID" \
    --buffer "$BUFFER" \
    --close-buffer "$BOT" \
    --format json \
    --export "$VAULT" \
    --export-encoding "$1" \
    --tx-version legacy \
    --single-extend-per-tx \
    --rpc "$RPC_URL"
}

echo "Exporting the transactions for vault $VAULT" >&2
export_transactions base58 > "$OUT_DIR/export.log" 2>&1 || { cat "$OUT_DIR/export.log" >&2; fail "program-metadata write --export failed"; }
export_transactions instruction-list > "$OUT_DIR/instructions.txt" 2>&1 || { cat "$OUT_DIR/instructions.txt" >&2; fail "program-metadata write --export failed"; }

EXPORTED_METADATA="$(grep -oE "metadata: $ADDRESS" "$OUT_DIR/export.log" | head -n 1 | awk '{print $2}' || true)"
[ "$EXPORTED_METADATA" = "$METADATA" ] ||
  fail "The export writes metadata account '$EXPORTED_METADATA', expected the canonical account $METADATA"
# Each transaction is printed on the line after its "[Transaction #n]" header.
awk '/^\[Transaction #[0-9]+\]$/ { if ((getline tx) > 0) print tx }' "$OUT_DIR/export.log" > "$OUT_DIR/exported.txt"
[ -s "$OUT_DIR/exported.txt" ] || fail "The export produced no transactions"

# Regroup the vault's instructions into proposals and write them as messages.
node "$TOOLS/squads-proposals.mjs" --transactions "$OUT_DIR/exported.txt" --multisig "$MULTISIG" --vault "$VAULT" --out-dir "$OUT_DIR" >&2
COUNT="$(wc -l < "$OUT_DIR/messages.txt" | tr -d ' ')"
[ "$COUNT" -gt 0 ] || fail "The export produced no proposals"

# The export sized its rent transfer from the metadata account as it was then.
# If the account changed since this script first read it, the amount computed
# below would not match that transfer: stop before the vault gets anything.
# (A change after this point still needs a new run; the summary says so.)
CURRENT_SPACE="$(account_space "$METADATA")"
[ "$CURRENT_SPACE" = "$METADATA_SPACE" ] ||
  fail "Metadata account $METADATA changed while the upload was prepared (size $METADATA_SPACE, now $CURRENT_SPACE). Run the workflow again."

# The first transaction moves this rent from the vault to the metadata account.
FUNDING="$(metadata_funding "$BUFFER_SPACE")" || fail "Could not read the rent for the metadata account"

if [ "$FUNDING" -gt 0 ]; then
  echo "Sending $(sol "$FUNDING") SOL to the vault for the metadata account's rent" >&2
  VAULT_BEFORE="$(balance "$VAULT")" || fail "Could not read the vault's balance"
  if solana transfer --url "$RPC_URL" --keypair "$BOT_KEYPAIR" --fee-payer "$BOT_KEYPAIR" \
    --allow-unfunded-recipient "$VAULT" "$(sol "$FUNDING")" >&2; then
    FUNDED="$FUNDING"
  else
    # The CLI can report an error for a transfer that landed, when it could
    # not confirm it. The vault's balance says which: a rise of at least the
    # amount means the rent is there, and the upload goes on.
    sleep "${FUNDING_RECHECK_SECONDS:-15}"
    VAULT_AFTER="$(balance "$VAULT")" || VAULT_AFTER="$VAULT_BEFORE"
    if [ $((VAULT_AFTER - VAULT_BEFORE)) -ge "$FUNDING" ]; then
      echo "::warning::solana transfer reported an error, but the vault's balance rose by the rent: continuing."
      FUNDED="$FUNDING"
    else
      FUNDING_UNSEEN="$FUNDING"
      fail "Could not send the vault the rent for the metadata account"
    fi
  fi
fi

echo "Handing the buffer to the vault" >&2
"$PM" set-buffer-authority "$BUFFER" --new-authority "$VAULT" --keypair "$BOT_KEYPAIR" --rpc "$RPC_URL" >&2
HANDED_OVER=true

# Written before proposing: from here on the messages are a way to finish the
# upload by hand, whatever happens to the proposals.
write_result() {
  {
    echo "buffer=$BUFFER"
    echo "metadata=$METADATA"
    echo "transactions=$COUNT"
    echo "funding=$FUNDING"
    echo "submitter=$BOT"
    echo "proposed=$1"
  } > "$OUT_DIR/result.env"
}
write_result false
echo "Wrote $COUNT message(s) to $OUT_DIR/messages.txt" >&2

if [ "$PROPOSE" = "true" ]; then
  echo "Creating $COUNT Squads proposal(s) in multisig $MULTISIG" >&2
  node "$TOOLS/squads-proposals.mjs" --transactions "$OUT_DIR/exported.txt" --multisig "$MULTISIG" --vault "$VAULT" \
    --out-dir "$OUT_DIR" --propose --keypair "$BOT_KEYPAIR" --rpc "$RPC_URL" >&2
  write_result true
fi
