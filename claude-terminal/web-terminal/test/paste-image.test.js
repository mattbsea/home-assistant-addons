'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const pasteImage = require('../paste-image');

function makeTempDir() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'paste-image-test-'));
}

test('saveImage writes a png with the correct extension', () => {
    const dir = makeTempDir();
    const base64 = Buffer.from('fake-png-bytes').toString('base64');
    const savedPath = pasteImage.saveImage({ dir, mimeType: 'image/png', base64Data: base64, maxBytes: 1024 * 1024 });
    assert.equal(path.dirname(savedPath), dir);
    assert.match(path.basename(savedPath), /^paste-.*\.png$/);
    assert.equal(fs.readFileSync(savedPath, 'utf8'), 'fake-png-bytes');
});

test('saveImage writes a jpeg with the correct extension', () => {
    const dir = makeTempDir();
    const base64 = Buffer.from('fake-jpeg-bytes').toString('base64');
    const savedPath = pasteImage.saveImage({ dir, mimeType: 'image/jpeg', base64Data: base64, maxBytes: 1024 * 1024 });
    assert.match(path.basename(savedPath), /^paste-.*\.jpg$/);
});

test('saveImage creates the directory if it does not exist yet', () => {
    const parent = makeTempDir();
    const dir = path.join(parent, 'nested', 'pasted-images');
    const base64 = Buffer.from('x').toString('base64');
    const savedPath = pasteImage.saveImage({ dir, mimeType: 'image/webp', base64Data: base64, maxBytes: 1024 });
    assert.ok(fs.existsSync(savedPath));
});

test('saveImage rejects an unsupported mime type', () => {
    const dir = makeTempDir();
    assert.throws(() => {
        pasteImage.saveImage({ dir, mimeType: 'application/pdf', base64Data: 'AA==', maxBytes: 1024 });
    }, /Unsupported image type/);
});

test('pruneToSizeLimit deletes oldest files first until back under the cap', () => {
    const dir = makeTempDir();
    // Three 100-byte files, oldest -> newest: a, b, c. Cap = 250 bytes, so
    // exactly the oldest ("a") must go to get down to 200.
    const names = ['a', 'b', 'c'];
    const now = Date.now();
    names.forEach((name, i) => {
        const full = path.join(dir, name);
        fs.writeFileSync(full, Buffer.alloc(100, 65));
        const mtime = new Date(now - (names.length - i) * 60000);
        fs.utimesSync(full, mtime, mtime);
    });

    pasteImage.pruneToSizeLimit(dir, 250);

    assert.deepEqual(fs.readdirSync(dir).sort(), ['b', 'c']);
});

test('pruneToSizeLimit never deletes the just-written file even if it alone exceeds the cap', () => {
    const dir = makeTempDir();
    const oldFile = path.join(dir, 'old.png');
    fs.writeFileSync(oldFile, Buffer.alloc(50, 65));
    const oldMtime = new Date(Date.now() - 60000);
    fs.utimesSync(oldFile, oldMtime, oldMtime);

    const skipPath = path.join(dir, 'new.png');
    fs.writeFileSync(skipPath, Buffer.alloc(500, 66)); // alone bigger than maxBytes

    pasteImage.pruneToSizeLimit(dir, 100, skipPath);

    assert.ok(fs.existsSync(skipPath), 'the just-written file must survive pruning');
    assert.ok(!fs.existsSync(oldFile), 'the older file should have been pruned');
});

test('pruneToSizeLimit no-ops when the directory does not exist', () => {
    const dir = path.join(os.tmpdir(), 'paste-image-test-does-not-exist-' + Date.now());
    assert.doesNotThrow(() => pasteImage.pruneToSizeLimit(dir, 1024));
});

test('resolveImagesDir joins home with the scratch folder path', () => {
    assert.equal(
        pasteImage.resolveImagesDir('/home/claude'),
        path.join('/home/claude', '.claude-terminal', 'pasted-images')
    );
});
