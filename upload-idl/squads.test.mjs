import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { AccountRole, getBase58Encoder, getCompiledTransactionMessageDecoder } from '@solana/kit';

import {
    activateTransactionInstruction,
    addInstructionInstruction,
    approveTransactionInstruction,
    confirmSubmission,
    createProposals,
    createTransactionInstruction,
    findAuthorityPda,
    findInstructionPda,
    findTransactionPda,
    instructionsFromExport,
    PROPOSAL_SIZE_LIMIT,
    proposalSize,
    readMultisig,
    splitForProposals,
    toImportableMessage,
    UnconfirmedError,
} from './squads.mjs';
import { signatureStatus, transferSolInstruction } from './send.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const exported = readFileSync(join(here, 'fixtures', 'exported-transactions.txt'), 'utf8');
// Built with the official SDK (@sqds/sdk 2.0.4) for the devnet multisig.
const vectors = JSON.parse(readFileSync(join(here, 'fixtures', 'squads-vectors.json'), 'utf8'));
const { multisig, creator, vault } = vectors;

const SYSTEM = '11111111111111111111111111111111';
const COMPUTE_BUDGET = 'ComputeBudget111111111111111111111111111111';
const METADATA = 'ProgM6JCCvbYkfKqJYHePx4xxSUSqJp7rh8Lyv7nk7S';

const hex = (bytes) => Buffer.from(bytes).toString('hex');
const plain = (instruction) => ({
    program: instruction.programAddress,
    data: hex(instruction.data),
    accounts: instruction.accounts.map((account) => ({
        address: account.address,
        signer: account.role === AccountRole.READONLY_SIGNER || account.role === AccountRole.WRITABLE_SIGNER,
        writable: account.role === AccountRole.WRITABLE || account.role === AccountRole.WRITABLE_SIGNER,
    })),
});
const fromVector = (vector) => ({
    programAddress: vector.program,
    data: Buffer.from(vector.data, 'hex'),
    accounts: vector.accounts.map((account) => ({
        address: account.address,
        role: account.signer
            ? account.writable
                ? AccountRole.WRITABLE_SIGNER
                : AccountRole.READONLY_SIGNER
            : account.writable
              ? AccountRole.WRITABLE
              : AccountRole.READONLY,
    })),
});

test('the vault is authority 1 of the multisig', async () => {
    assert.equal(await findAuthorityPda(multisig, 1), vault);
});

test('transaction and instruction addresses match the SDK', async () => {
    const transaction = await findTransactionPda(multisig, vectors.transactionIndex);
    assert.equal(transaction, vectors.transactionPda);
    assert.equal(await findInstructionPda(transaction, 1), vectors.instructionPda);
});

test('the Squads instructions match the SDK byte for byte', () => {
    const { transactionPda, instructionPda } = vectors;
    assert.deepEqual(plain(createTransactionInstruction({ multisig, transaction: transactionPda, creator, authorityIndex: 1 })), vectors.createTransaction);
    assert.deepEqual(
        plain(addInstructionInstruction({ multisig, transaction: transactionPda, instruction: instructionPda, creator, inner: fromVector(vectors.inner) })),
        vectors.addInstruction,
    );
    assert.deepEqual(plain(activateTransactionInstruction({ multisig, transaction: transactionPda, creator })), vectors.activateTransaction);
    assert.deepEqual(plain(approveTransactionInstruction({ multisig, transaction: transactionPda, member: creator })), vectors.approveTransaction);
});

test('an export is read into its instructions, without the compute budget ones', () => {
    const groups = instructionsFromExport(exported, vault);
    assert.deepEqual(groups.map((group) => group.length), [3, 1, 1, 4]);
    const programs = groups.flat().map((instruction) => instruction.programAddress);
    assert.ok(!programs.includes(COMPUTE_BUDGET));
    assert.deepEqual(programs, [SYSTEM, METADATA, METADATA, METADATA, METADATA, METADATA, METADATA, METADATA, METADATA]);
    // The first instruction is the vault paying the metadata account's rent.
    assert.deepEqual(plain(groups[0][0]), vectors.inner);
});

test('an export whose fee payer is not the vault is rejected', () => {
    assert.throws(() => instructionsFromExport(exported, creator), /fee payer/);
});

test('an empty export is rejected', () => {
    assert.throws(() => instructionsFromExport('\n', vault), /no transactions/);
});

test('instructions are split so every proposal fits one transaction', async () => {
    const groups = instructionsFromExport(exported, vault);
    // As exported, the last transaction is too large for a single proposal.
    assert.ok((await proposalSize(groups[3], { multisig, member: creator })) > PROPOSAL_SIZE_LIMIT);
    const proposals = await splitForProposals(groups, { multisig, member: creator });
    assert.deepEqual(proposals.map((proposal) => proposal.length), [3, 1, 1, 3, 1]);
    // Order is preserved.
    assert.deepEqual(proposals.flat(), groups.flat());
    // Sizes of the transaction squads-deploy-ui builds on import, as measured with the SDK.
    const sizes = await Promise.all(proposals.map((proposal) => proposalSize(proposal, { multisig, member: creator })));
    assert.deepEqual(sizes, [984, 539, 539, 1015, 571]);
});

test('an importable message is a legacy message paid by the vault with the same instructions', async () => {
    const groups = instructionsFromExport(exported, vault);
    const [first] = await splitForProposals(groups, { multisig, member: creator });
    const encoded = toImportableMessage(first, vault);
    const message = getCompiledTransactionMessageDecoder().decode(getBase58Encoder().encode(encoded));
    assert.equal(message.version, 'legacy');
    assert.equal(message.staticAccounts[0], vault);
    assert.equal(message.header.numSignerAccounts, 1);
    assert.equal(message.instructions.length, first.length);
    message.instructions.forEach((compiled, index) => {
        assert.equal(message.staticAccounts[compiled.programAddressIndex], first[index].programAddress);
        assert.deepEqual(
            (compiled.accountIndices ?? []).map((accountIndex) => message.staticAccounts[accountIndex]),
            first[index].accounts.map((account) => account.address),
        );
        assert.equal(hex(compiled.data ?? []), hex(first[index].data));
    });
});

test('a multisig account is read for its next index and its members', () => {
    const data = Buffer.alloc(8 + 2 + 2 + 4 + 4 + 1 + 32 + 1 + 4 + 64);
    data.writeUInt16LE(2, 8); // threshold
    data.writeUInt32LE(74, 12); // transaction index
    data.writeUInt32LE(2, 54); // member count
    Buffer.from(getBase58Encoder().encode(creator)).copy(data, 58);
    Buffer.from(getBase58Encoder().encode(vault)).copy(data, 90);
    assert.deepEqual(readMultisig(data), { threshold: 2, transactionIndex: 74, members: [creator, vault] });
});

test('a truncated multisig account is rejected', () => {
    assert.throws(() => readMultisig(Buffer.alloc(40)), /multisig account/);
});

test('each proposal is recorded as soon as it exists, so a later failure does not lose it', async () => {
    const proposals = await splitForProposals(instructionsFromExport(exported, vault), { multisig, member: creator });
    const recorded = [];
    const sent = [];
    let calls = 0;
    await assert.rejects(
        createProposals(proposals, {
            multisig,
            creator,
            firstIndex: 75,
            send: async (instructions) => {
                calls += 1;
                // The third proposal cannot be sent.
                if (calls === 3) throw new Error('blockhash expired');
                sent.push(instructions);
            },
            onCreated: (proposal) => recorded.push(proposal),
        }),
        /could not create proposal 77 \(3 of 5\): blockhash expired\. Proposals 75, 76 were created before the failure\./,
    );
    assert.deepEqual(recorded, [
        { index: 75, transaction: vectors.transactionPda },
        { index: 76, transaction: await findTransactionPda(multisig, 76) },
    ]);
    // Create, three stored instructions, activate.
    assert.equal(sent[0].length, 5);
});

test('every proposal is recorded in order when all are created', async () => {
    const proposals = await splitForProposals(instructionsFromExport(exported, vault), { multisig, member: creator });
    const recorded = [];
    const created = await createProposals(proposals, { multisig, creator, firstIndex: 75, send: async () => {}, onCreated: (proposal) => recorded.push(proposal) });
    assert.deepEqual(created.map((proposal) => proposal.index), [75, 76, 77, 78, 79]);
    assert.deepEqual(recorded, created);
});

const confirmed = { confirmationStatus: 'confirmed', err: null };
const submission = (overrides) => ({
    signature: 'sig',
    lastValidBlockHeight: 100n,
    send: async () => {},
    getStatus: async () => confirmed,
    getBlockHeight: async () => 50n,
    sleep: async () => {},
    ...overrides,
});

test('a submission is confirmed once its signature is', async () => {
    const statuses = [null, { confirmationStatus: 'processed', err: null }, confirmed];
    assert.equal(await confirmSubmission(submission({ getStatus: async () => statuses.shift() })), 'sig');
    assert.equal(statuses.length, 0);
});

test('a send that errors is still followed, and counts when the transaction lands', async () => {
    const statuses = [null, null, confirmed];
    const result = await confirmSubmission(
        submission({
            send: async () => {
                throw new Error('socket hang up');
            },
            getStatus: async () => statuses.shift(),
        }),
    );
    assert.equal(result, 'sig');
});

test('a send that errors and is never seen is unknown once the blockhash has expired', async () => {
    const heights = [50n, 50n, 101n];
    let statusReads = 0;
    const failure = await confirmSubmission(
        submission({
            send: async () => {
                throw new Error('socket hang up');
            },
            getStatus: async () => {
                statusReads += 1;
                return null;
            },
            getBlockHeight: async () => heights.shift(),
        }),
    ).catch((error) => error);
    // The error does not prove the transaction was not broadcast.
    assert.ok(failure instanceof UnconfirmedError);
    assert.match(failure.message, /socket hang up/);
    // Three reads while the blockhash was valid, one more after it expired.
    assert.equal(statusReads, 4);
});

test('a transaction that lands in the last valid block is still found after expiry', async () => {
    const statuses = [null, confirmed];
    assert.equal(await confirmSubmission(submission({ getStatus: async () => statuses.shift(), getBlockHeight: async () => 101n })), 'sig');
});

test('a transaction that expires without a send error is reported as expired', async () => {
    await assert.rejects(confirmSubmission(submission({ getStatus: async () => null, getBlockHeight: async () => 101n })), /expired before it was confirmed/);
});

test('a transaction that fails on chain is rejected with its error', async () => {
    await assert.rejects(
        confirmSubmission(submission({ getStatus: async () => ({ confirmationStatus: 'confirmed', err: { InstructionError: [1, { Custom: 6000n }] } }) })),
        /transaction sig failed: .*InstructionError.*6000/,
    );
});

test('a send error that proves the transaction was never broadcast stops at once', async () => {
    let asked = false;
    await assert.rejects(
        confirmSubmission(
            submission({
                send: async () => {
                    throw new Error('simulation failed');
                },
                isDefinitiveSendError: () => true,
                getStatus: async () => {
                    asked = true;
                    return null;
                },
            }),
        ),
        /simulation failed/,
    );
    assert.equal(asked, false);
});

test('status errors are retried', async () => {
    let calls = 0;
    const result = await confirmSubmission(
        submission({
            getStatus: async () => {
                calls += 1;
                if (calls < 3) throw new Error('429');
                return confirmed;
            },
        }),
    );
    assert.equal(result, 'sig');
});

test('when the cluster cannot be asked, the outcome is unknown rather than a failure', async () => {
    let calls = 0;
    const failure = await confirmSubmission(
        submission({
            getStatus: async () => {
                calls += 1;
                throw new Error('fetch failed');
            },
            maxFailures: 4,
        }),
    ).catch((error) => error);
    assert.ok(failure instanceof UnconfirmedError);
    assert.match(failure.message, /sig.*fetch failed/);
    assert.equal(calls, 4);
});

test('a transaction that stays processed is given up on as unknown once its blockhash has expired', async () => {
    let reads = 0;
    const failure = await confirmSubmission(
        submission({
            getStatus: async () => {
                reads += 1;
                return { confirmationStatus: 'processed', err: null };
            },
            getBlockHeight: async () => 101n,
            maxPollsAfterExpiry: 3,
        }),
    ).catch((error) => error);
    assert.ok(failure instanceof UnconfirmedError);
    assert.match(failure.message, /sig.*not confirmed/);
    // One read that finds the blockhash expired, then three more.
    assert.equal(reads, 4);
});

test('a transaction that stays processed is still followed while its blockhash is valid, then bounded', async () => {
    const heights = [50n, 50n, 50n, 101n];
    let reads = 0;
    const failure = await confirmSubmission(
        submission({
            getStatus: async () => {
                reads += 1;
                return { confirmationStatus: 'processed', err: null };
            },
            getBlockHeight: async () => heights.shift() ?? 101n,
            maxPollsAfterExpiry: 2,
        }),
    ).catch((error) => error);
    assert.ok(failure instanceof UnconfirmedError);
    assert.equal(reads, 6);
});

test('a processed transaction that is confirmed after the blockhash expired still counts', async () => {
    const processed = { confirmationStatus: 'processed', err: null };
    const statuses = [processed, processed, confirmed];
    assert.equal(await confirmSubmission(submission({ getStatus: async () => statuses.shift(), getBlockHeight: async () => 101n })), 'sig');
});

test('an unconfirmed proposal is reported as unknown, with what to check', async () => {
    const proposals = await splitForProposals(instructionsFromExport(exported, vault), { multisig, member: creator });
    const recorded = [];
    let calls = 0;
    const failure = await createProposals(proposals, {
        multisig,
        creator,
        firstIndex: 75,
        send: async () => {
            calls += 1;
            if (calls === 3) throw new UnconfirmedError('could not ask the cluster about transaction sig: fetch failed');
        },
        onCreated: (proposal) => recorded.push(proposal),
    }).catch((error) => error);
    assert.match(failure.message, /could not confirm whether proposal 77 \(3 of 5\) was created/);
    assert.match(failure.message, /Proposals 75, 76 were created before the failure\./);
    assert.deepEqual(failure.unconfirmed, { index: 77, transaction: await findTransactionPda(multisig, 77), position: 3 });
    assert.deepEqual(recorded.map((proposal) => proposal.index), [75, 76]);
});

test('the vault funding transfer matches the System Program transfer the CLI builds', () => {
    // The first exported instruction is the vault paying 162570160 lamports
    // of rent into the metadata account.
    const [transfer] = instructionsFromExport(exported, vault)[0];
    const metadata = transfer.accounts[1].address;
    const ours = transferSolInstruction({ from: vault, to: metadata, lamports: '162570160' });
    assert.deepEqual(plain(ours), plain(transfer));
});

test('the transfer amount must fit an unsigned 64-bit integer', () => {
    const max = (1n << 64n) - 1n;
    const data = transferSolInstruction({ from: vault, to: creator, lamports: max.toString() }).data;
    assert.equal(new DataView(data.buffer).getBigUint64(4, true), max);
    for (const lamports of ['18446744073709551616', '18446744074709551616', '0', '-1']) {
        assert.throws(() => transferSolInstruction({ from: vault, to: creator, lamports }), /lamports/);
    }
});

test('signature statuses are looked up in the transaction history, not just the recent cache', async () => {
    const calls = [];
    const rpc = {
        getSignatureStatuses: (signatures, config) => {
            calls.push({ signatures, config });
            return { send: async () => ({ value: [{ confirmationStatus: 'confirmed', err: null }] }) };
        },
    };
    assert.deepEqual(await signatureStatus(rpc, 'sig'), { confirmationStatus: 'confirmed', err: null });
    assert.deepEqual(calls, [{ signatures: ['sig'], config: { searchTransactionHistory: true } }]);
});
