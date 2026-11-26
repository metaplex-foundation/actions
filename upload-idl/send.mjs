// Sending a transaction and following it to a known outcome, shared by the
// proposal script and the vault funding script.

import {
    AccountRole,
    appendTransactionMessageInstructions,
    createTransactionMessage,
    getBase64EncodedWireTransaction,
    getSignatureFromTransaction,
    isSolanaError,
    pipe,
    setTransactionMessageFeePayerSigner,
    setTransactionMessageLifetimeUsingBlockhash,
    signTransactionMessageWithSigners,
    SOLANA_ERROR__JSON_RPC__SERVER_ERROR_SEND_TRANSACTION_PREFLIGHT_FAILURE,
} from '@solana/kit';

import { COMPUTE_BUDGET_PROGRAM, confirmSubmission } from './squads.mjs';

// Same default as the Program Metadata CLI.
const PRIORITY_FEE_MICRO_LAMPORTS = 100_000n;

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function priorityFeeInstruction() {
    const data = new Uint8Array(9);
    data[0] = 3; // SetComputeUnitPrice
    new DataView(data.buffer).setBigUint64(1, PRIORITY_FEE_MICRO_LAMPORTS, true);
    return { programAddress: COMPUTE_BUDGET_PROGRAM, accounts: [], data };
}

// Mark the creator's account as the signer object, so the transaction is
// signed by it wherever the Squads instructions name it.
function withSigner(instruction, signer) {
    return {
        ...instruction,
        accounts: instruction.accounts.map((account) =>
            account.address === signer.address && account.role === AccountRole.WRITABLE_SIGNER ? { ...account, signer } : account,
        ),
    };
}

/**
 * The status of one signature. The transaction history is searched, not only
 * the recent status cache, so a transaction that landed is still found after
 * the cache has dropped it.
 */
export async function signatureStatus(rpc, signature) {
    return (await rpc.getSignatureStatuses([signature], { searchTransactionHistory: true }).send()).value[0];
}

export async function sendAndConfirm(rpc, signer, instructions) {
    const { value: lifetime } = await rpc.getLatestBlockhash({ commitment: 'confirmed' }).send();
    const signed = await signTransactionMessageWithSigners(
        pipe(
            createTransactionMessage({ version: 0 }),
            (message) => setTransactionMessageFeePayerSigner(signer, message),
            (message) => setTransactionMessageLifetimeUsingBlockhash(lifetime, message),
            (message) => appendTransactionMessageInstructions(instructions.map((instruction) => withSigner(instruction, signer)), message),
        ),
    );
    const signature = getSignatureFromTransaction(signed);
    // Polls instead of subscribing, so no websocket endpoint is needed.
    return confirmSubmission({
        signature,
        lastValidBlockHeight: lifetime.lastValidBlockHeight,
        send: () =>
            rpc
                .sendTransaction(getBase64EncodedWireTransaction(signed), { encoding: 'base64', preflightCommitment: 'confirmed', maxRetries: 5n })
                .send(),
        getStatus: () => signatureStatus(rpc, signature),
        getBlockHeight: () => rpc.getBlockHeight({ commitment: 'confirmed' }).send(),
        sleep,
        // The node simulated the transaction, it failed, and it was not broadcast.
        isDefinitiveSendError: (error) => isSolanaError(error, SOLANA_ERROR__JSON_RPC__SERVER_ERROR_SEND_TRANSACTION_PREFLIGHT_FAILURE),
    });
}


/** A System Program transfer of `lamports` from the signer `from` to `to`. */
export function transferSolInstruction({ from, to, lamports }) {
    // setBigUint64 wraps out-of-range values silently: check first.
    const amount = BigInt(lamports);
    if (amount <= 0n || amount > (1n << 64n) - 1n) {
        throw new Error(`lamports must be between 1 and 2^64 - 1, got ${lamports}`);
    }
    const data = new Uint8Array(12);
    const view = new DataView(data.buffer);
    view.setUint32(0, 2, true); // Transfer
    view.setBigUint64(4, amount, true);
    return {
        programAddress: '11111111111111111111111111111111',
        accounts: [
            { address: from, role: AccountRole.WRITABLE_SIGNER },
            { address: to, role: AccountRole.WRITABLE },
        ],
        data,
    };
}
