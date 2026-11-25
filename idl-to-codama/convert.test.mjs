import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { convertIdl } from './convert.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const fixturePath = (name) => join(here, 'fixtures', name);
const fixture = (name) => JSON.parse(readFileSync(fixturePath(name), 'utf8'));
const field = (struct, name) => struct.fields.find((f) => f.name === name).type;

test('a Shank IDL without accounts, types or errors becomes a Codama root node', () => {
    const { format, address, content } = convertIdl(fixture('shank-minimal.json'));
    assert.equal(format, 'codama');
    assert.equal(address, 'mnoopTCrg4p8ry25e4bcWA9XZjbNjMTfgYVGGEdRsf3');
    assert.equal(content.kind, 'rootNode');
    assert.equal(content.standard, 'codama');
    assert.equal(content.program.publicKey, 'mnoopTCrg4p8ry25e4bcWA9XZjbNjMTfgYVGGEdRsf3');
    assert.equal(content.program.origin, 'shank');
    // Codama 1.11 leaves empty sections out of the tree.
    assert.equal(content.program.accounts, undefined);
    assert.equal(content.program.instructions.length, 1);
});

test('Shank instructions keep their u8 discriminant', () => {
    const { content } = convertIdl(fixture('shank-fixed-size-option.json'));
    const [instruction] = content.program.instructions;
    const discriminator = instruction.arguments.find((a) => a.name === 'discriminator');
    assert.equal(discriminator.type.format, 'u8');
    assert.equal(discriminator.defaultValue.number, 3);
});

test('fixedSizeOption with a zero sentinel becomes a zeroable option without a zero value', () => {
    const { content } = convertIdl(fixture('shank-fixed-size-option.json'));
    const record = content.program.accounts.find((a) => a.name === 'record');
    assert.deepEqual(field(record.data, 'delegate'), {
        kind: 'zeroableOptionTypeNode',
        item: { kind: 'publicKeyTypeNode' },
    });
    const [instruction] = content.program.instructions;
    const delegate = instruction.arguments.find((a) => a.name === 'delegate');
    assert.equal(delegate.type.kind, 'zeroableOptionTypeNode');
    assert.equal(delegate.type.item.kind, 'publicKeyTypeNode');
});

test('fixedSizeOption with a non-zero sentinel carries the sentinel as its zero value', () => {
    const { content } = convertIdl(fixture('shank-fixed-size-option.json'));
    const record = content.program.accounts.find((a) => a.name === 'record');
    const expiry = field(record.data, 'expiry');
    assert.equal(expiry.kind, 'zeroableOptionTypeNode');
    assert.deepEqual(expiry.item, { kind: 'numberTypeNode', format: 'u64', endian: 'le' });
    assert.deepEqual(expiry.zeroValue, {
        kind: 'constantValueNode',
        type: { kind: 'bytesTypeNode' },
        value: { kind: 'bytesValueNode', encoding: 'base16', data: 'ffffffffffffffff' },
    });
});

test('fixedSizeOption nested in another type is converted too', () => {
    const { content } = convertIdl(fixture('shank-fixed-size-option.json'));
    const record = content.program.accounts.find((a) => a.name === 'record');
    const history = field(record.data, 'history');
    assert.equal(history.kind, 'arrayTypeNode');
    assert.equal(history.item.kind, 'zeroableOptionTypeNode');
    assert.equal(history.item.item.format, 'u16');
});

test('no placeholder survives the conversion', () => {
    const { content } = convertIdl(fixture('shank-fixed-size-option.json'));
    const json = JSON.stringify(content);
    assert.doesNotMatch(json, /fixedSizeOption/i);
    assert.doesNotMatch(json, /definedTypeLinkNode/);
});

test('the input IDL is not modified', () => {
    const idl = fixture('shank-fixed-size-option.json');
    const before = JSON.stringify(idl);
    convertIdl(idl);
    assert.equal(JSON.stringify(idl), before);
});

test('an Anchor IDL is passed through unchanged', () => {
    const idl = fixture('anchor-legacy.json');
    const { format, address, content } = convertIdl(idl);
    assert.equal(format, 'anchor');
    assert.equal(address, 'MPL4o4wMzndgh8T1NVDxELQCj5UQfYTYEkabX3wNKtb');
    assert.deepEqual(content, idl);
});

test('a modern Anchor IDL reports its top-level address', () => {
    const idl = { address: 'mgrfTeJh5VgHt67LQQVZ7U2gPY88En94QMWz64cV2AY', metadata: { spec: '0.1.0' }, instructions: [] };
    const { format, address } = convertIdl(idl);
    assert.equal(format, 'anchor');
    assert.equal(address, 'mgrfTeJh5VgHt67LQQVZ7U2gPY88En94QMWz64cV2AY');
});

test('a program id that differs from the IDL address is rejected', () => {
    assert.throws(
        () => convertIdl(fixture('shank-minimal.json'), { programId: 'CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d' }),
        /mnoopTCrg4p8ry25e4bcWA9XZjbNjMTfgYVGGEdRsf3.*CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d/s,
    );
});

test('an IDL without a program address is rejected', () => {
    const idl = fixture('shank-minimal.json');
    delete idl.metadata.address;
    assert.throws(() => convertIdl(idl), /no program address/);
});

test('the CLI writes the converted IDL and prints its format', () => {
    const out = join(mkdtempSync(join(tmpdir(), 'idl-to-codama-')), 'idl.json');
    const stdout = execFileSync(
        process.execPath,
        [join(here, 'cli.mjs'), '--idl', fixturePath('shank-minimal.json'), '--program-id', 'mnoopTCrg4p8ry25e4bcWA9XZjbNjMTfgYVGGEdRsf3', '--out', out],
        { encoding: 'utf8' },
    );
    assert.match(stdout, /^format=codama$/m);
    assert.equal(JSON.parse(readFileSync(out, 'utf8')).kind, 'rootNode');
});

test('the CLI fails with a workflow error annotation on a mismatch', () => {
    const out = join(mkdtempSync(join(tmpdir(), 'idl-to-codama-')), 'idl.json');
    const result = spawnSync(
        process.execPath,
        [join(here, 'cli.mjs'), '--idl', fixturePath('shank-minimal.json'), '--program-id', 'CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d', '--out', out],
        { encoding: 'utf8' },
    );
    assert.equal(result.status, 1);
    assert.match(result.stdout + result.stderr, /^::error::/m);
});
