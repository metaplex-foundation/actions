// Squads v3 (squads-mpl) support for the upload-idl workflow.
//
// The Program Metadata CLI exports the transactions a vault has to run as full
// base58 transactions. This module turns that export into what the Squads
// tooling works with: the vault's instructions, split so that each group fits
// one proposal, either as base58 messages for the "Import Transaction" dialog
// of squads-deploy-ui or as the instructions that create the proposals.
//
// The instruction layouts mirror the squads-mpl program and are tested against
// vectors produced by the official SDK; the SDK itself is not a dependency.

import { createHash } from 'node:crypto';

import {
    AccountRole,
    appendTransactionMessageInstructions,
    compileTransactionMessage,
    createTransactionMessage,
    getAddressDecoder,
    getAddressEncoder,
    getBase58Decoder,
    getBase58Encoder,
    getCompiledTransactionMessageDecoder,
    getCompiledTransactionMessageEncoder,
    getProgramDerivedAddress,
    getTransactionDecoder,
    pipe,
    setTransactionMessageFeePayer,
    setTransactionMessageLifetimeUsingBlockhash,
} from '@solana/kit';

export const SQUADS_PROGRAM = 'SMPLecH534NA9acpos4G6x7uf3LWbCAwZQE9e8ZekMu';
export const SYSTEM_PROGRAM = '11111111111111111111111111111111';
export const COMPUTE_BUDGET_PROGRAM = 'ComputeBudget111111111111111111111111111111';
/** Largest serialized transaction the cluster accepts. */
export const PROPOSAL_SIZE_LIMIT = 1232;

// Any blockhash does for a message that is only measured or imported: the
// importer and the proposal both take the instructions, not the lifetime.
const PLACEHOLDER_LIFETIME = { blockhash: SYSTEM_PROGRAM, lastValidBlockHeight: 0n };

const addressBytes = (value) => getAddressEncoder().encode(value);
const u32 = (value) => {
    const bytes = new Uint8Array(4);
    new DataView(bytes.buffer).setUint32(0, value, true);
    return bytes;
};
const concat = (...parts) => {
    const out = new Uint8Array(parts.reduce((length, part) => length + part.length, 0));
    let offset = 0;
    for (const part of parts) {
        out.set(part, offset);
        offset += part.length;
    }
    return out;
};
const text = (value) => new TextEncoder().encode(value);
const discriminator = (name) => new Uint8Array(createHash('sha256').update(`global:${name}`).digest().subarray(0, 8));
const isSigner = (role) => role === AccountRole.READONLY_SIGNER || role === AccountRole.WRITABLE_SIGNER;
const isWritable = (role) => role === AccountRole.WRITABLE || role === AccountRole.WRITABLE_SIGNER;

async function findPda(seeds) {
    const [pda] = await getProgramDerivedAddress({ programAddress: SQUADS_PROGRAM, seeds });
    return pda;
}

/** The multisig's signing address for an authority index; index 1 is the vault. */
export function findAuthorityPda(multisig, authorityIndex) {
    return findPda([text('squad'), addressBytes(multisig), u32(authorityIndex), text('authority')]);
}

export function findTransactionPda(multisig, transactionIndex) {
    return findPda([text('squad'), addressBytes(multisig), u32(transactionIndex), text('transaction')]);
}

/** Instruction indexes start at 1. */
export function findInstructionPda(transaction, instructionIndex) {
    return findPda([text('squad'), addressBytes(transaction), new Uint8Array([instructionIndex]), text('instruction')]);
}

export function createTransactionInstruction({ multisig, transaction, creator, authorityIndex }) {
    return {
        programAddress: SQUADS_PROGRAM,
        accounts: [
            { address: multisig, role: AccountRole.WRITABLE },
            { address: transaction, role: AccountRole.WRITABLE },
            { address: creator, role: AccountRole.WRITABLE_SIGNER },
            { address: SYSTEM_PROGRAM, role: AccountRole.READONLY },
        ],
        data: concat(discriminator('create_transaction'), u32(authorityIndex)),
    };
}

// IncomingInstruction, Borsh: program id, then a u32-prefixed list of
// (pubkey, is_signer, is_writable), then u32-prefixed data.
function encodeInner(inner) {
    return concat(
        addressBytes(inner.programAddress),
        u32(inner.accounts.length),
        ...inner.accounts.map((account) =>
            concat(addressBytes(account.address), new Uint8Array([isSigner(account.role) ? 1 : 0, isWritable(account.role) ? 1 : 0])),
        ),
        u32(inner.data.length),
        inner.data,
    );
}

export function addInstructionInstruction({ multisig, transaction, instruction, creator, inner }) {
    return {
        programAddress: SQUADS_PROGRAM,
        accounts: [
            { address: multisig, role: AccountRole.READONLY },
            { address: transaction, role: AccountRole.WRITABLE },
            { address: instruction, role: AccountRole.WRITABLE },
            { address: creator, role: AccountRole.WRITABLE_SIGNER },
            { address: SYSTEM_PROGRAM, role: AccountRole.READONLY },
        ],
        data: concat(discriminator('add_instruction'), encodeInner(inner)),
    };
}

function memberInstruction(name, { multisig, transaction, member }) {
    return {
        programAddress: SQUADS_PROGRAM,
        accounts: [
            { address: multisig, role: AccountRole.READONLY },
            { address: transaction, role: AccountRole.WRITABLE },
            { address: member, role: AccountRole.WRITABLE_SIGNER },
        ],
        data: discriminator(name),
    };
}

export function activateTransactionInstruction({ multisig, transaction, creator }) {
    return memberInstruction('activate_transaction', { multisig, transaction, member: creator });
}

export function approveTransactionInstruction({ multisig, transaction, member }) {
    return memberInstruction('approve_transaction', { multisig, transaction, member });
}

/**
 * The instructions that create and activate one proposal: the multisig
 * transaction, one stored instruction per vault instruction, activation.
 */
export async function proposalInstructions(instructions, { multisig, creator, transactionIndex }) {
    const transaction = await findTransactionPda(multisig, transactionIndex);
    const out = [createTransactionInstruction({ multisig, transaction, creator, authorityIndex: 1 })];
    for (const [index, inner] of instructions.entries()) {
        const instruction = await findInstructionPda(transaction, index + 1);
        out.push(addInstructionInstruction({ multisig, transaction, instruction, creator, inner }));
    }
    out.push(activateTransactionInstruction({ multisig, transaction, creator }));
    return { transaction, instructions: out };
}

/** The cluster could not be asked whether a transaction landed: its outcome is unknown. */
export class UnconfirmedError extends Error {
    constructor(message) {
        super(message);
        this.name = 'UnconfirmedError';
    }
}

/**
 * Sends a signed transaction and follows its signature until it is confirmed
 * or can no longer land. A send that errors is not proof that the transaction
 * was not accepted (the response may be all that was lost), so the signature
 * is followed either way; only `isDefinitiveSendError` stops at once.
 *
 * Rejects with the on-chain or send error when the transaction did not land,
 * and with UnconfirmedError when that cannot be known: the cluster stops
 * answering, or the transaction is seen but stays unconfirmed past its
 * blockhash. Every path ends.
 */
export async function confirmSubmission({
    signature,
    lastValidBlockHeight,
    send,
    getStatus,
    getBlockHeight,
    sleep,
    isDefinitiveSendError = () => false,
    maxFailures = 30,
    maxPollsAfterExpiry = 30,
}) {
    let sendError = null;
    try {
        await send();
    } catch (error) {
        if (isDefinitiveSendError(error)) throw error;
        sendError = error;
    }
    const isConfirmed = (status) => status?.confirmationStatus === 'confirmed' || status?.confirmationStatus === 'finalized';
    let failures = 0;
    let expired = false;
    let pollsAfterExpiry = 0;
    for (;;) {
        let status;
        let height;
        try {
            status = await getStatus();
            // Read on every poll that settles nothing, whatever the status:
            // a transaction that stays "processed" must not hide the expiry.
            if (!expired && !status?.err && !isConfirmed(status)) height = await getBlockHeight();
        } catch (error) {
            failures += 1;
            if (failures >= maxFailures) {
                throw new UnconfirmedError(`could not ask the cluster about transaction ${signature}: ${error.message}`);
            }
            await sleep(1000);
            continue;
        }
        failures = 0;
        if (status?.err) {
            const detail = JSON.stringify(status.err, (_, value) => (typeof value === 'bigint' ? value.toString() : value));
            throw new Error(`transaction ${signature} failed: ${detail}`);
        }
        if (isConfirmed(status)) return signature;
        if (expired) {
            // No status, read once more after the expiry: it cannot land any more.
            if (!status) throw sendError ?? new Error(`transaction ${signature} expired before it was confirmed`);
            // Seen but not confirmed. It should settle within a few slots;
            // if it does not, stop waiting and say the outcome is unknown.
            pollsAfterExpiry += 1;
            if (pollsAfterExpiry >= maxPollsAfterExpiry) {
                throw new UnconfirmedError(`transaction ${signature} was processed but not confirmed after its blockhash expired`);
            }
        } else if (height !== undefined && height > lastValidBlockHeight) {
            expired = true;
            continue;
        }
        await sleep(1000);
    }
}

/**
 * Creates the proposals one after the other, numbered from `firstIndex`.
 * `send` submits and confirms one proposal's instructions; `onCreated` is
 * called as soon as a proposal exists, before the next one is attempted, so
 * that a failure partway leaves a record of what was created. When `send`
 * rejects with UnconfirmedError the proposal may exist, and the error thrown
 * carries `unconfirmed` (its index, account and position) to report that.
 */
export async function createProposals(proposals, { multisig, creator, firstIndex, send, onCreated }) {
    const created = [];
    for (const [offset, instructions] of proposals.entries()) {
        const index = firstIndex + offset;
        const proposal = await proposalInstructions(instructions, { multisig, creator, transactionIndex: index });
        try {
            await send(proposal.instructions);
        } catch (error) {
            const done = created.length > 0 ? ` Proposals ${created.map((p) => p.index).join(', ')} were created before the failure.` : '';
            const place = `proposal ${index} (${offset + 1} of ${proposals.length})`;
            if (error instanceof UnconfirmedError) {
                // It may exist: say so, instead of sending the reader to create it again.
                const unknown = new Error(
                    `could not confirm whether ${place} was created: ${error.message}. Check the multisig for proposal ${index} before importing message ${offset + 1}.${done}`,
                );
                unknown.unconfirmed = { index, transaction: proposal.transaction, position: offset + 1 };
                throw unknown;
            }
            throw new Error(`could not create ${place}: ${error.message}.${done}`);
        }
        const record = { index, transaction: proposal.transaction };
        created.push(record);
        onCreated?.(record);
    }
    return created;
}

function compile(instructions, feePayer, version) {
    return compileTransactionMessage(
        pipe(
            createTransactionMessage({ version }),
            (message) => setTransactionMessageFeePayer(feePayer, message),
            (message) => setTransactionMessageLifetimeUsingBlockhash(PLACEHOLDER_LIFETIME, message),
            (message) => appendTransactionMessageInstructions(instructions, message),
        ),
    );
}

/** Serialized size of a transaction with these instructions and one signer. */
export function transactionSize(instructions, feePayer) {
    const message = getCompiledTransactionMessageEncoder().encode(compile(instructions, feePayer, 0));
    return 1 + 64 + message.length;
}

/**
 * Size of the single transaction that proposes these vault instructions the
 * way squads-deploy-ui does on import: create, add each instruction, activate
 * and approve. A group that fits here can be imported, and can be created by
 * CI in one atomic transaction.
 */
export async function proposalSize(instructions, { multisig, member }) {
    const proposal = await proposalInstructions(instructions, { multisig, creator: member, transactionIndex: 1 });
    return transactionSize(
        [...proposal.instructions, approveTransactionInstruction({ multisig, transaction: proposal.transaction, member })],
        member,
    );
}

function decodeExportedTransaction(line, vault) {
    const { messageBytes } = getTransactionDecoder().decode(getBase58Encoder().encode(line));
    const message = getCompiledTransactionMessageDecoder().decode(messageBytes);
    const accounts = message.staticAccounts;
    if (accounts[0] !== vault) {
        throw new Error(`the exported transaction's fee payer is ${accounts[0]}, expected the vault ${vault}`);
    }
    const { numSignerAccounts, numReadonlySignerAccounts, numReadonlyNonSignerAccounts } = message.header;
    const role = (index) => {
        const signer = index < numSignerAccounts;
        const writable = signer ? index < numSignerAccounts - numReadonlySignerAccounts : index < accounts.length - numReadonlyNonSignerAccounts;
        if (signer) return writable ? AccountRole.WRITABLE_SIGNER : AccountRole.READONLY_SIGNER;
        return writable ? AccountRole.WRITABLE : AccountRole.READONLY;
    };
    return message.instructions.map((instruction) => ({
        programAddress: accounts[instruction.programAddressIndex],
        accounts: (instruction.accountIndices ?? []).map((index) => ({ address: accounts[index], role: role(index) })),
        data: new Uint8Array(instruction.data ?? []),
    }));
}

/**
 * Reads the CLI's export (one base58 transaction per line) into the vault's
 * instructions, one group per exported transaction. Compute budget
 * instructions are dropped: they do nothing when the vault runs them.
 */
export function instructionsFromExport(exported, vault) {
    const lines = exported
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
    if (lines.length === 0) throw new Error('the export contains no transactions');
    return lines
        .map((line) => decodeExportedTransaction(line, vault).filter((instruction) => instruction.programAddress !== COMPUTE_BUDGET_PROGRAM))
        .filter((group) => group.length > 0);
}

/**
 * Splits each group, in order, into runs that fit one proposal transaction.
 * Instructions never move between exported transactions, so the CLI's own
 * boundaries (one 10KB extend per transaction) are kept.
 */
export async function splitForProposals(groups, { multisig, member }) {
    const fits = async (instructions) => (await proposalSize(instructions, { multisig, member })) <= PROPOSAL_SIZE_LIMIT;
    const proposals = [];
    for (const group of groups) {
        let current = [];
        for (const instruction of group) {
            if (!(await fits([instruction]))) throw new Error('an exported instruction is too large for a Squads proposal');
            if (current.length > 0 && !(await fits([...current, instruction]))) {
                proposals.push(current);
                current = [];
            }
            current.push(instruction);
        }
        if (current.length > 0) proposals.push(current);
    }
    return proposals;
}

/** A base58 legacy message paid by the vault, for "Import Transaction". */
export function toImportableMessage(instructions, vault) {
    return getBase58Decoder().decode(getCompiledTransactionMessageEncoder().encode(compile(instructions, vault, 'legacy')));
}

/**
 * Reads the fields of a squads-mpl multisig account that proposing needs.
 * Layout after the 8-byte discriminator: threshold u16, authority index u16,
 * transaction index u32, change index u32, bump u8, create key (32), external
 * execute flag u8, then the u32-prefixed member list.
 */
export function readMultisig(data) {
    const MEMBERS_OFFSET = 54;
    const bytes = new Uint8Array(data);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (bytes.length < MEMBERS_OFFSET + 4) throw new Error('the multisig account is too short to be a Squads v3 multisig');
    const count = view.getUint32(MEMBERS_OFFSET, true);
    if (bytes.length < MEMBERS_OFFSET + 4 + 32 * count) throw new Error('the multisig account ends before its member list does');
    const members = [];
    for (let index = 0; index < count; index++) {
        const start = MEMBERS_OFFSET + 4 + 32 * index;
        members.push(getAddressDecoder().decode(bytes.subarray(start, start + 32)));
    }
    return { threshold: view.getUint16(8, true), transactionIndex: view.getUint32(12, true), members };
}
