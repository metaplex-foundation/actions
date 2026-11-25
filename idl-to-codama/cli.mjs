#!/usr/bin/env node
// node cli.mjs --idl <path> --program-id <address> --out <path>
// Prints `format=codama|anchor`; fails with a workflow error annotation.

import { readFileSync, writeFileSync } from 'node:fs';
import { parseArgs } from 'node:util';

import { convertIdl } from './convert.mjs';

try {
    const { values } = parseArgs({
        options: {
            idl: { type: 'string' },
            out: { type: 'string' },
            'program-id': { type: 'string' },
        },
    });
    if (!values.idl || !values.out) throw new Error('usage: cli.mjs --idl <path> --out <path> [--program-id <address>]');
    const idl = JSON.parse(readFileSync(values.idl, 'utf8'));
    const { format, content } = convertIdl(idl, { programId: values['program-id'] });
    // Compact: every byte of the IDL is paid for in rent.
    writeFileSync(values.out, JSON.stringify(content));
    console.log(`format=${format}`);
} catch (error) {
    console.log(`::error::${String(error.message ?? error).replace(/\r?\n/g, ' ')}`);
    process.exit(1);
}
