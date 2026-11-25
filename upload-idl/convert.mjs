// Converts a Shank IDL to a Codama root node, the format the Solana Explorer
// needs to decode a Shank program's instructions. IDLs of any other origin
// (Anchor) are returned unchanged: the Explorer reads those natively.

import { defaultVisitor, rootNodeFromAnchorWithoutDefaultVisitor, typeNodeFromAnchorV00 } from '@codama/nodes-from-anchor';
import {
    bottomUpTransformerVisitor,
    bytesTypeNode,
    bytesValueNode,
    camelCase,
    constantValueNode,
    createFromRoot,
    visit,
    zeroableOptionTypeNode,
} from 'codama';

const PLACEHOLDER_PREFIX = 'idlToCodamaFixedSizeOption';

function isFixedSizeOption(value) {
    return typeof value === 'object' && value !== null && !Array.isArray(value) && 'fixedSizeOption' in value;
}

/**
 * Shank's `fixedSizeOption` (an option encoded as a sentinel value instead of
 * a prefix) is unknown to the Anchor converter. Swap each one for a link to a
 * placeholder type and remember what it stood for; inner types are handled
 * first so a nested option is registered before the one that contains it.
 */
function extractFixedSizeOptions(value, placeholders) {
    if (Array.isArray(value)) return value.map((item) => extractFixedSizeOptions(item, placeholders));
    if (typeof value !== 'object' || value === null) return value;
    if (isFixedSizeOption(value)) {
        const { inner, sentinel } = value.fixedSizeOption;
        if (!Array.isArray(sentinel) || !sentinel.every((byte) => Number.isInteger(byte) && byte >= 0 && byte <= 255)) {
            throw new Error(`fixedSizeOption has an invalid sentinel: ${JSON.stringify(sentinel)}`);
        }
        const name = `${PLACEHOLDER_PREFIX}${placeholders.length}`;
        placeholders.push({ name, inner: extractFixedSizeOptions(inner, placeholders), sentinel });
        return { defined: name };
    }
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, extractFixedSizeOptions(item, placeholders)]));
}

function zeroValueNode(sentinel) {
    // All zeroes is Codama's default zero value.
    if (sentinel.every((byte) => byte === 0)) return undefined;
    const hex = sentinel.map((byte) => byte.toString(16).padStart(2, '0')).join('');
    return constantValueNode(bytesTypeNode(), bytesValueNode('base16', hex));
}

function restoreFixedSizeOptions(root, placeholders) {
    if (placeholders.length === 0) return root;
    const resolved = new Map();
    const transformer = () =>
        bottomUpTransformerVisitor([
            {
                select: '[definedTypeLinkNode]',
                transform: (node) => resolved.get(node.name) ?? node,
            },
        ]);
    for (const { name, inner, sentinel } of placeholders) {
        const item = visit(typeNodeFromAnchorV00(inner), transformer());
        resolved.set(camelCase(name), zeroableOptionTypeNode(item, zeroValueNode(sentinel)));
    }
    return visit(root, transformer());
}

function shankToCodama(idl) {
    const placeholders = [];
    const normalised = extractFixedSizeOptions(
        {
            ...idl,
            // Shank leaves out sections that would be empty.
            accounts: idl.accounts ?? [],
            errors: idl.errors ?? [],
            instructions: (idl.instructions ?? []).map((instruction) => ({
                ...instruction,
                accounts: instruction.accounts ?? [],
                args: instruction.args ?? [],
            })),
            types: idl.types ?? [],
        },
        placeholders,
    );
    const bare = rootNodeFromAnchorWithoutDefaultVisitor(normalised);
    // The default visitor computes account sizes, so it must see real types.
    const root = visit(restoreFixedSizeOptions(bare, placeholders), defaultVisitor());
    return JSON.parse(createFromRoot(root).getJson());
}

/**
 * @param {object} idl Parsed IDL.
 * @param {{ programId?: string }} [options] Program id the IDL must describe.
 * @returns {{ format: 'codama' | 'anchor', address: string, content: object }}
 */
export function convertIdl(idl, { programId } = {}) {
    const address = idl?.metadata?.address ?? idl?.address;
    if (typeof address !== 'string' || address === '') {
        throw new Error('the IDL has no program address (metadata.address or address)');
    }
    if (programId !== undefined && programId !== address) {
        throw new Error(`the IDL describes program ${address}, expected ${programId}`);
    }
    if (idl.metadata?.origin !== 'shank') {
        return { format: 'anchor', address, content: idl };
    }
    const content = shankToCodama(idl);
    if (content.program?.publicKey !== address) {
        throw new Error(`the converted IDL has program ${content.program?.publicKey}, expected ${address}`);
    }
    return { format: 'codama', address, content };
}
