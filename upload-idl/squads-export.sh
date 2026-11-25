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
#   4. hands the buffer to the vault.
#
# The exported transactions are legacy transactions with at most one 10KB
# extend each, which is what Squads v3 can execute through CPI.
#
# Environment:
#   RPC_URL       cluster RPC endpoint
#   PROGRAM_ID    program the IDL belongs to
#   IDL_FILE      IDL to upload (JSON)
#   VAULT         Squads vault, the program's upgrade authority
#   BOT_KEYPAIR   path to the submitter keypair
#   PM            path to the program-metadata CLI
#   OUT_DIR       directory for transactions.txt, instructions.txt, result.env
#   ZLIB_BYTES    optional compressed IDL size, for the balance check
#
# Requires: curl, jq, node, solana, solana-keygen.

set -euo pipefail

: "${RPC_URL:?}" "${PROGRAM_ID:?}" "${IDL_FILE:?}" "${VAULT:?}" "${BOT_KEYPAIR:?}" "${PM:?}" "${OUT_DIR:?}"

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
metadata_funding() {
  local funding
  funding="$(rent "$1")"
  if [ "$METADATA_SPACE" != "missing" ]; then
    funding=$((funding - $(rent "$METADATA_SPACE")))
    [ "$funding" -gt 0 ] || funding=0
  fi
  echo "$funding"
}

BOT="$(solana-keygen pubkey "$BOT_KEYPAIR")"
METADATA="$(node "$(dirname "$0")/metadata-address.mjs" "$PROGRAM_ID")"
METADATA_SPACE="$(account_space "$METADATA")"
BUFFER=""
FUNDED=0
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
  exit "$status"
}
trap cleanup EXIT

if [ -n "${ZLIB_BYTES:-}" ]; then
  ESTIMATE="$(rent $((ACCOUNT_HEADER + ZLIB_BYTES)))"
  # Rent for the buffer (returned when the vault closes it) plus the rent
  # sent to the vault for the metadata account.
  REQUIRED=$((ESTIMATE + $(metadata_funding $((ACCOUNT_HEADER + ZLIB_BYTES))) + FEE_MARGIN))
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
awk '/^\[Transaction #[0-9]+\]$/ { if ((getline tx) > 0) print tx }' "$OUT_DIR/export.log" > "$OUT_DIR/transactions.txt"
COUNT="$(wc -l < "$OUT_DIR/transactions.txt" | tr -d ' ')"
[ "$COUNT" -gt 0 ] || fail "The export produced no transactions"

# The first transaction moves this rent from the vault to the metadata account.
FUNDING="$(metadata_funding "$BUFFER_SPACE")"

if [ "$FUNDING" -gt 0 ]; then
  echo "Sending $(sol "$FUNDING") SOL to the vault for the metadata account's rent" >&2
  solana transfer --url "$RPC_URL" --keypair "$BOT_KEYPAIR" --fee-payer "$BOT_KEYPAIR" \
    --allow-unfunded-recipient "$VAULT" "$(sol "$FUNDING")" >&2
  FUNDED="$FUNDING"
fi

echo "Handing the buffer to the vault" >&2
"$PM" set-buffer-authority "$BUFFER" --new-authority "$VAULT" --keypair "$BOT_KEYPAIR" --rpc "$RPC_URL" >&2
HANDED_OVER=true

{
  echo "buffer=$BUFFER"
  echo "metadata=$METADATA"
  echo "transactions=$COUNT"
  echo "funding=$FUNDING"
  echo "submitter=$BOT"
} > "$OUT_DIR/result.env"

echo "Exported $COUNT transaction(s) to $OUT_DIR/transactions.txt" >&2
