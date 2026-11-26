#!/usr/bin/env node
// node fund-vault.mjs --keypair <path> --rpc <url> --to <address> --lamports <n>
//
// Sends lamports from the keypair to an address and follows the transaction
// by its signature, so the outcome is the bot's own transfer, not a change in
// the recipient's balance that anyone could have caused.
//
// Exit codes:
//   0  the transfer is confirmed; prints "signature=<signature>"
//   1  it did not land (failed, rejected, or expired unsent)
//   2  it was sent but its outcome could not be determined: it may have landed

import { readFileSync } from 'node:fs';
import { parseArgs } from 'node:util';

import { createKeyPairSignerFromBytes, createSolanaRpc } from '@solana/kit';

import { priorityFeeInstruction, sendAndConfirm, transferSolInstruction } from './send.mjs';
import { UnconfirmedError } from './squads.mjs';

const oneLine = (error) => String(error?.message ?? error).replace(/\r?\n/g, ' ');

try {
    const { values } = parseArgs({
        options: {
            keypair: { type: 'string' },
            lamports: { type: 'string' },
            rpc: { type: 'string' },
            to: { type: 'string' },
        },
    });
    for (const name of ['keypair', 'rpc', 'to', 'lamports']) {
        if (!values[name]) throw new Error(`--${name} is required`);
    }
    if (!/^[1-9][0-9]*$/.test(values.lamports)) throw new Error(`--lamports must be a positive integer, got ${values.lamports}`);
    const signer = await createKeyPairSignerFromBytes(new Uint8Array(JSON.parse(readFileSync(values.keypair, 'utf8'))));
    const signature = await sendAndConfirm(createSolanaRpc(values.rpc), signer, [
        priorityFeeInstruction(),
        transferSolInstruction({ from: signer.address, to: values.to, lamports: values.lamports }),
    ]);
    console.log(`signature=${signature}`);
} catch (error) {
    if (error instanceof UnconfirmedError) {
        console.log(`::warning::${oneLine(error)}`);
        process.exit(2);
    }
    console.log(`::error::${oneLine(error)}`);
    process.exit(1);
}
