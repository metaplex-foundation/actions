#!/usr/bin/env node
// node metadata-address.mjs <program> [seed]
// Prints the canonical metadata account of a program (seed `idl` by default).

import { findMetadataPda } from '@solana-program/program-metadata';

const [program, seed = 'idl'] = process.argv.slice(2);
if (!program) {
    console.error('usage: metadata-address.mjs <program> [seed]');
    process.exit(1);
}
const [address] = await findMetadataPda({ program, authority: null, seed });
console.log(address);
