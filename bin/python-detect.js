/**
 * Cross-platform Python 3 detection.
 *
 * On Linux/macOS the binary is typically `python3`.
 * On Windows it is often just `python` (the Microsoft Store alias
 * or the official installer both register `python`).
 *
 * This module tries each candidate in order and returns the first
 * one that reports Python 3.x.  The result is cached for the
 * lifetime of the process so repeated calls are free.
 */

import { execSync, execFileSync } from 'child_process';

/** @type {string | null | undefined} */
let _cached;

/**
 * Detect a working Python 3 command.
 *
 * @returns {string | null} The command name ('python3' or 'python'),
 *   or null if no Python 3 was found.
 */
export function findPython() {
  if (_cached !== undefined) return _cached;

  for (const cmd of ['python3', 'python']) {
    try {
      const version = execFileSync(cmd, ['--version'], {
        encoding: 'utf8',
        stdio: ['pipe', 'pipe', 'pipe'],
        timeout: 5000,
      }).trim();
      if (version.startsWith('Python 3.')) {
        _cached = cmd;
        return _cached;
      }
    } catch {
      // Not found or not Python 3 -- try next candidate
    }
  }

  _cached = null;
  return null;
}

/**
 * Return the Python command or throw with a helpful message.
 *
 * @returns {string}
 */
export function requirePython() {
  const cmd = findPython();
  if (!cmd) {
    throw new Error(
      'Python 3 not found. Install Python 3.9+ and ensure "python3" or "python" is on PATH.'
    );
  }
  return cmd;
}
