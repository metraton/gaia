#!/usr/bin/env node

/**
 * Install git hooks for commit message validation
 * Runs automatically during postinstall
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const hooksDir = path.join(__dirname, '..', '.git', 'hooks');
const commitMsgHook = path.join(hooksDir, 'commit-msg');

try {
  // Only proceed if .git exists (not during npm publish)
  if (!fs.existsSync(path.join(__dirname, '..', '.git'))) {
    console.log('ℹ️  Skipping git hooks installation (not in git repository)');
    process.exit(0);
  }

  // Copy commit-msg hook from templates if it exists
  const templateHook = path.join(__dirname, '..', 'templates', 'hooks', 'commit-msg');
  if (fs.existsSync(templateHook) && !fs.existsSync(commitMsgHook)) {
    fs.copyFileSync(templateHook, commitMsgHook);
    fs.chmodSync(commitMsgHook, 0o755);
    console.log('✅ Git hooks installed');
  } else if (fs.existsSync(commitMsgHook)) {
    console.log('ℹ️  Git hooks already installed');
  }

  // Make sure hook is executable
  if (fs.existsSync(commitMsgHook)) {
    fs.chmodSync(commitMsgHook, 0o755);
  }
} catch (err) {
  console.warn('⚠️  Failed to install git hooks:', err.message);
  // Don't fail installation if hooks setup fails
  process.exit(0);
}
