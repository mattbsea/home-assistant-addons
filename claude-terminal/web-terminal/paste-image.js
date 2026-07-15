'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const MIME_TO_EXT = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
};

function resolveImagesDir(home) {
    return path.join(home, '.claude-terminal', 'pasted-images');
}

function pruneToSizeLimit(dir, maxBytes, skipPath) {
    let entries;
    try {
        entries = fs.readdirSync(dir);
    } catch (e) {
        return; // directory doesn't exist yet -- nothing to prune
    }

    const files = entries.map(function(name) {
        const full = path.join(dir, name);
        const stat = fs.statSync(full);
        return { full: full, size: stat.size, mtimeMs: stat.mtimeMs };
    });

    let total = files.reduce(function(sum, f) { return sum + f.size; }, 0);
    if (total <= maxBytes) return;

    files.sort(function(a, b) { return a.mtimeMs - b.mtimeMs; }); // oldest first

    for (const f of files) {
        if (total <= maxBytes) break;
        if (skipPath && f.full === skipPath) continue;
        fs.unlinkSync(f.full);
        total -= f.size;
    }
}

function saveImage(options) {
    const dir = options.dir;
    const mimeType = options.mimeType;
    const base64Data = options.base64Data;
    const maxBytes = options.maxBytes;

    const ext = MIME_TO_EXT[mimeType];
    if (!ext) {
        throw new Error('Unsupported image type: ' + mimeType);
    }

    fs.mkdirSync(dir, { recursive: true });

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const suffix = crypto.randomBytes(2).toString('hex');
    const filename = 'paste-' + timestamp + '-' + suffix + '.' + ext;
    const fullPath = path.join(dir, filename);

    fs.writeFileSync(fullPath, Buffer.from(base64Data, 'base64'));

    pruneToSizeLimit(dir, maxBytes, fullPath);

    return fullPath;
}

module.exports = {
    MIME_TO_EXT: MIME_TO_EXT,
    resolveImagesDir: resolveImagesDir,
    pruneToSizeLimit: pruneToSizeLimit,
    saveImage: saveImage,
};
