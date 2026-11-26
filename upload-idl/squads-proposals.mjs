#!/usr/bin/env node
// Turns the Program Metadata CLI's exported vault transactions into Squads v3
// proposals.
//
//   node squads-proposals.mjs --transactions <file> --multisig <address>
//     --vault <address> --out-dir <dir> [--propose --keypair <path> --rpc <url>]
//   node squads-proposals.mjs --preflight --multisig <address> --vault <address>
//     [--keypair <path> --rpc <url>]
//
// --preflight changes nothing: it checks that the vault belongs to the
// multisig and, given a keypair, that the keypair may create proposals.
//
// Always writes <out-dir>/messages.txt: one base58 legacy message per
// proposal, for the "Import Transaction" dialog of squads-deploy-ui.
// With --propose it also creates and activates the proposals as the keypair
// (a member of the multisig) and writes <out-dir>/proposals.txt, one
// "<transaction index> <transaction account>" line per proposal, appended as
// each one is created: after a failure the file lists the proposals that
// exist. The keypair never approves: the members still approve and execute.
//
// Fails with a workflow error annotation.

import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { parseArgs } from 'node:util';

import {
    AccountRole,
    appendTransactionMessageInstructions,
    createKeyPairSignerFromBytes,
    createSolanaRpc,
    createTransactionMessage,
    getBase64EncodedWireTransaction,
    getBase64Encoder,
    getSignatureFromTransaction,
    pipe,
    setTransactionMessageFeePayerSigner,
    setTransactionMessageLifetimeUsingBlockhash,
    signTransactionMessageWithSigners,
} from '@solana/kit';

import {
    COMPUTE_BUDGET_PROGRAM,
    createProposals,
    findAuthorityPda,
    instructionsFromExport,
    PROPOSAL_SIZE_LIMIT,
    readMultisig,
    splitForProposals,
    SQUADS_PROGRAM,
    toImportableMessage,
    transactionSize,
} from './squads.mjs';

// Same default as the Program Metadata CLI.
const PRIORITY_FEE_MICRO_LAMPORTS = 100_000n;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function priorityFeeInstruction() {
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

async function sendAndConfirm(rpc, signer, instructions) {
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
    await rpc
        .sendTransaction(getBase64EncodedWireTransaction(signed), { encoding: 'base64', preflightCommitment: 'confirmed', maxRetries: 5n })
        .send();
    // Poll instead of subscribing: no websocket endpoint is needed.
    for (;;) {
        const { value } = await rpc.getSignatureStatuses([signature]).send();
        const status = value[0];
        if (status?.err) throw new Error(`transaction ${signature} failed: ${JSON.stringify(status.err, (_, v) => (typeof v === 'bigint' ? v.toString() : v))}`);
        if (status?.confirmationStatus === 'confirmed' || status?.confirmationStatus === 'finalized') return signature;
        const height = await rpc.getBlockHeight({ commitment: 'confirmed' }).send();
        if (height > lifetime.lastValidBlockHeight) throw new Error(`transaction ${signature} expired before it was confirmed`);
        await sleep(1000);
    }
}

// One atomic transaction when it fits, which the split guarantees in
// practice; otherwise the same instructions in order over several.
function packTransactions(instructions, feePayer) {
    const fee = priorityFeeInstruction();
    const batches = [];
    let current = [];
    for (const instruction of instructions) {
        if (current.length > 0 && transactionSize([fee, ...current, instruction], feePayer) > PROPOSAL_SIZE_LIMIT) {
            batches.push(current);
            current = [];
        }
        current.push(instruction);
    }
    if (current.length > 0) batches.push(current);
    return batches.map((batch) => [fee, ...batch]);
}

async function fetchMultisig(rpc, multisig) {
    const { value } = await rpc.getAccountInfo(multisig, { encoding: 'base64', commitment: 'confirmed' }).send();
    if (!value) throw new Error(`multisig ${multisig} was not found`);
    if (value.owner !== SQUADS_PROGRAM) throw new Error(`multisig ${multisig} is owned by ${value.owner}, not the Squads v3 program ${SQUADS_PROGRAM}`);
    return readMultisig(getBase64Encoder().encode(value.data[0]));
}

async function loadProposer({ multisig, keypair, rpcUrl }) {
    const rpc = createSolanaRpc(rpcUrl);
    const signer = await createKeyPairSignerFromBytes(new Uint8Array(JSON.parse(readFileSync(keypair, 'utf8'))));
    const { transactionIndex, members } = await fetchMultisig(rpc, multisig);
    if (!members.includes(signer.address)) {
        throw new Error(`${signer.address} is not a member of multisig ${multisig}, so it cannot create proposals`);
    }
    return { rpc, signer, transactionIndex };
}

async function propose(proposals, { multisig, keypair, rpcUrl, record }) {
    const { rpc, signer, transactionIndex } = await loadProposer({ multisig, keypair, rpcUrl });
    // The file exists from here on, and is empty until a proposal does.
    writeFileSync(record, '');
    return createProposals(proposals, {
        multisig,
        creator: signer.address,
        firstIndex: transactionIndex + 1,
        send: async (instructions) => {
            for (const batch of packTransactions(instructions, signer.address)) {
                await sendAndConfirm(rpc, signer, batch);
            }
        },
        onCreated: ({ index, transaction }) => {
            appendFileSync(record, `${index} ${transaction}\n`);
            console.error(`Created proposal ${index} (${transaction})`);
        },
    });
}

try {
    const { values } = parseArgs({
        options: {
            keypair: { type: 'string' },
            multisig: { type: 'string' },
            'out-dir': { type: 'string' },
            preflight: { type: 'boolean', default: false },
            propose: { type: 'boolean', default: false },
            rpc: { type: 'string' },
            transactions: { type: 'string' },
            vault: { type: 'string' },
        },
    });
    for (const name of values.preflight ? ['multisig', 'vault'] : ['transactions', 'multisig', 'vault', 'out-dir']) {
        if (!values[name]) throw new Error(`--${name} is required`);
    }
    const { multisig, vault } = values;
    const derived = await findAuthorityPda(multisig, 1);
    if (derived !== vault) throw new Error(`vault ${vault} is not the vault of multisig ${multisig} (${derived})`);

    if (values.preflight) {
        if (values.keypair) {
            if (!values.rpc) throw new Error('--keypair needs --rpc');
            await loadProposer({ multisig, keypair: values.keypair, rpcUrl: values.rpc });
        }
        process.exit(0);
    }

    const groups = instructionsFromExport(readFileSync(values.transactions, 'utf8'), vault);
    // Sizes do not depend on who proposes, so the vault stands in for the member.
    const proposals = await splitForProposals(groups, { multisig, member: vault });
    writeFileSync(join(values['out-dir'], 'messages.txt'), proposals.map((instructions) => toImportableMessage(instructions, vault)).join('\n') + '\n');
    console.error(`Wrote ${proposals.length} importable message(s)`);

    if (values.propose) {
        if (!values.keypair || !values.rpc) throw new Error('--propose needs --keypair and --rpc');
        await propose(proposals, { multisig, keypair: values.keypair, rpcUrl: values.rpc, record: join(values['out-dir'], 'proposals.txt') });
    }
} catch (error) {
    console.log(`::error::${String(error.message ?? error).replace(/\r?\n/g, ' ')}`);
    process.exit(1);
}
