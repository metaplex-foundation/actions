#!/usr/bin/env node
// node to-codama.mjs --idl <path> --out <path> [--program-id <address>]
// Prints `format=codama|anchor` and `zlib_bytes=<n>` (the size the IDL takes
// on chain); fails with a workflow error annotation.

import { readFileSync, writeFileSync } from 'node:fs';
import { parseArgs } from 'node:util';
import { deflateSync } from 'node:zlib';

import { convertIdl } from './convert.mjs';

try {
    const { values } = parseArgs({
        options: {
            idl: { type: 'string' },
            out: { type: 'string' },
            'program-id': { type: 'string' },
        },
    });
    if (!values.idl || !values.out) throw new Error('usage: to-codama.mjs --idl <path> --out <path> [--program-id <address>]');
    const idl = JSON.parse(readFileSync(values.idl, 'utf8'));
    const { format, content } = convertIdl(idl, { programId: values['program-id'] });
    // Compact: every byte of the IDL is paid for in rent.
    const json = JSON.stringify(content);
    writeFileSync(values.out, json);
    console.log(`format=${format}`);
    console.log(`zlib_bytes=${deflateSync(json).length}`);
} catch (error) {
    console.log(`::error::${String(error.message ?? error).replace(/\r?\n/g, ' ')}`);
    process.exit(1);
}
