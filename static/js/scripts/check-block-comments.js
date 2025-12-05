#!/usr/bin/env node
// Check for block comments (/** */ or /* */) in JavaScript files
// This project prefers // comments to discourage verbose documentation
// that restates what function names already say clearly.

import { readFileSync, readdirSync, statSync } from 'fs';
import { join, extname } from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const jsDir = join(__dirname, '..');

function findJsFiles(dir, files = []) {
    const entries = readdirSync(dir);
    for (const entry of entries) {
        if (entry === 'node_modules' || entry === 'scripts') continue;
        const fullPath = join(dir, entry);
        const stat = statSync(fullPath);
        if (stat.isDirectory()) {
            findJsFiles(fullPath, files);
        } else if (extname(entry) === '.js') {
            files.push(fullPath);
        }
    }
    return files;
}

function checkFile(filePath) {
    const content = readFileSync(filePath, 'utf-8');
    const lines = content.split('\n');
    const violations = [];
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        // Match start of block comment (/** or /*)
        if (/^\s*\/\*\*/.test(line) || /^\s*\/\*[^*]/.test(line)) {
            violations.push({
                line: i + 1,
                content: line.trim()
            });
        }
    }
    
    return violations;
}

const files = findJsFiles(jsDir);
let totalViolations = 0;

for (const file of files) {
    const violations = checkFile(file);
    if (violations.length > 0) {
        const relativePath = file.replace(jsDir + '\\', '').replace(jsDir + '/', '');
        console.log(`\n${relativePath}:`);
        for (const v of violations) {
            console.log(`  Line ${v.line}: ${v.content}`);
            totalViolations++;
        }
    }
}

if (totalViolations > 0) {
    console.log(`\n✖ Found ${totalViolations} block comment(s). Use // comments instead.`);
    process.exit(1);
} else {
    console.log('✓ No block comments found.');
    process.exit(0);
}

