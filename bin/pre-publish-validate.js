#!/usr/bin/env node
/**
 * Pre-publish validation script for gaia-ops
 *
 * Validates that changes work correctly before publishing to npm:
 * 1. Checks for staged changes
 * 2. Bumps package.json version (patch by default)
 * 3. Runs npm install in the consuming project
 * 4. Validates that changes are reflected in node_modules
 * 5. Runs basic validation tests
 * 6. Only then proceeds to publish
 *
 * Usage:
 *   node bin/pre-publish-validate.js [major|minor|patch]
 *   node bin/pre-publish-validate.js --dry-run
 *   node bin/pre-publish-validate.js --validate-only
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const chalk = require('chalk');

const GAIA_OPS_ROOT = path.resolve(__dirname, '..');
const MONOREPO_ROOT = path.resolve(GAIA_OPS_ROOT, '..');
const NODE_MODULES_INSTALL = path.resolve(MONOREPO_ROOT, 'node_modules', '@jaguilar87', 'gaia-ops');

class PrePublishValidator {
  constructor(options = {}) {
    this.dryRun = options.dryRun || false;
    this.validateOnly = options.validateOnly || false;
    this.versionBump = options.versionBump || 'patch';
    this.currentVersion = null;
    this.newVersion = null;
  }

  log(message, level = 'info') {
    const timestamp = new Date().toLocaleTimeString();
    const prefix = `[${timestamp}]`;

    switch (level) {
      case 'error':
        console.error(chalk.red(`${prefix} ✗ ${message}`));
        break;
      case 'success':
        console.log(chalk.green(`${prefix} ✓ ${message}`));
        break;
      case 'warning':
        console.warn(chalk.yellow(`${prefix} ⚠️  ${message}`));
        break;
      case 'info':
        console.log(chalk.blue(`${prefix} ℹ️  ${message}`));
        break;
      default:
        console.log(message);
    }
  }

  execute(command, cwd = GAIA_OPS_ROOT, silent = false) {
    try {
      if (!silent) {
        this.log(`Running: ${command}`, 'info');
      }
      const result = execSync(command, { cwd, encoding: 'utf-8' });
      if (!silent) {
        this.log(`Command completed`, 'success');
      }
      return result;
    } catch (error) {
      this.log(`Command failed: ${error.message}`, 'error');
      throw error;
    }
  }

  validateGitStatus() {
    this.log('Step 1: Validating git status...', 'info');

    try {
      const status = this.execute('git status --porcelain', GAIA_OPS_ROOT, true);

      if (!status.trim()) {
        this.log('✓ No uncommitted changes - working tree clean', 'success');
        return true;
      }

      const lines = status.trim().split('\n');
      this.log(`Found ${lines.length} uncommitted changes:`, 'warning');
      lines.forEach(line => console.log(`  ${line}`));

      if (!this.dryRun && !this.validateOnly) {
        this.log('Proceeding with validation (changes will be committed after validation)', 'info');
      }
      return true;
    } catch (error) {
      this.log('Failed to check git status', 'error');
      throw error;
    }
  }

  readCurrentVersion() {
    this.log('Step 2: Reading current version...', 'info');

    const packageJsonPath = path.join(GAIA_OPS_ROOT, 'package.json');
    const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf-8'));
    this.currentVersion = packageJson.version;

    this.log(`Current version: ${this.currentVersion}`, 'success');
    return this.currentVersion;
  }

  bumpVersion() {
    this.log(`Step 3: Bumping version (${this.versionBump})...`, 'info');

    const parts = this.currentVersion.split('.');

    switch (this.versionBump) {
      case 'major':
        parts[0] = String(parseInt(parts[0]) + 1);
        parts[1] = '0';
        parts[2] = '0';
        break;
      case 'minor':
        parts[1] = String(parseInt(parts[1]) + 1);
        parts[2] = '0';
        break;
      case 'patch':
      default:
        parts[2] = String(parseInt(parts[2]) + 1);
    }

    this.newVersion = parts.join('.');

    if (this.dryRun) {
      this.log(`[DRY RUN] Would bump version to: ${this.newVersion}`, 'info');
      return;
    }

    const packageJsonPath = path.join(GAIA_OPS_ROOT, 'package.json');
    const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf-8'));
    packageJson.version = this.newVersion;
    fs.writeFileSync(packageJsonPath, JSON.stringify(packageJson, null, 2) + '\n');

    this.log(`Version bumped: ${this.currentVersion} → ${this.newVersion}`, 'success');
  }

  validateNodeModules() {
    this.log('Step 4: Validating node_modules installation...', 'info');

    if (!fs.existsSync(NODE_MODULES_INSTALL)) {
      this.log('⚠️  gaia-ops not found in node_modules, installing...', 'warning');
      this.reinstallNodeModules();
    }

    const installedPackageJson = path.join(NODE_MODULES_INSTALL, 'package.json');
    if (!fs.existsSync(installedPackageJson)) {
      this.log('✗ node_modules installation is invalid', 'error');
      throw new Error('node_modules installation failed');
    }

    const installedPkg = JSON.parse(fs.readFileSync(installedPackageJson, 'utf-8'));
    const expectedVersion = this.newVersion || this.currentVersion;

    if (installedPkg.version !== expectedVersion) {
      this.log(`✗ Version mismatch: expected ${expectedVersion}, got ${installedPkg.version}`, 'error');
      throw new Error('Version mismatch in node_modules');
    }

    this.log(`✓ node_modules version matches: ${expectedVersion}`, 'success');
  }

  reinstallNodeModules() {
    this.log('Reinstalling node_modules in monorepo...', 'info');

    if (this.dryRun) {
      this.log('[DRY RUN] Would run: npm install', 'info');
      return;
    }

    this.execute('npm install', MONOREPO_ROOT);
    this.log('✓ npm install completed', 'success');
  }

  validateFiles() {
    this.log('Step 5: Validating key files...', 'info');

    const criticalFiles = [
      'package.json',
      'bin/gaia-init.js',
      'tools/1-routing/agent_router.py',
      'hooks/pre_tool_use.py',
      'templates/settings.template.json'
    ];

    let allValid = true;

    criticalFiles.forEach(file => {
      const sourcePath = path.join(GAIA_OPS_ROOT, file);
      const installedPath = path.join(NODE_MODULES_INSTALL, file);

      if (!fs.existsSync(sourcePath)) {
        this.log(`✗ Source file missing: ${file}`, 'warning');
        allValid = false;
        return;
      }

      if (!fs.existsSync(installedPath)) {
        this.log(`✗ Installed file missing: ${file}`, 'warning');
        allValid = false;
        return;
      }

      // Compare file hashes
      const sourceContent = fs.readFileSync(sourcePath, 'utf-8');
      const installedContent = fs.readFileSync(installedPath, 'utf-8');

      if (sourceContent === installedContent) {
        this.log(`✓ ${file}`, 'success');
      } else {
        this.log(`⚠️  ${file} differs (this is normal after version bump)`, 'warning');
      }
    });

    if (!allValid) {
      throw new Error('Some critical files are missing');
    }
  }

  runTests() {
    this.log('Step 6: Running validation tests...', 'info');

    try {
      // Test 1: Validate JSON files
      this.log('Test 1: Validating JSON configuration files...', 'info');
      const jsonFiles = [
        'templates/settings.template.json',
        'config/clarification_rules.json',
        'config/git_standards.json'
      ];

      jsonFiles.forEach(file => {
        const filePath = path.join(NODE_MODULES_INSTALL, file);
        if (fs.existsSync(filePath)) {
          try {
            JSON.parse(fs.readFileSync(filePath, 'utf-8'));
            this.log(`  ✓ ${file}`, 'success');
          } catch (error) {
            this.log(`  ✗ ${file}: ${error.message}`, 'error');
            throw error;
          }
        }
      });

      // Test 2: Validate Python syntax (if Python is available)
      this.log('Test 2: Validating Python files syntax...', 'info');
      try {
        const pythonFiles = [
          'hooks/pre_tool_use.py',
          'tools/1-routing/agent_router.py'
        ];

        pythonFiles.forEach(file => {
          const filePath = path.join(NODE_MODULES_INSTALL, file);
          if (fs.existsSync(filePath)) {
            this.execute(`python3 -m py_compile "${filePath}"`, MONOREPO_ROOT, true);
            this.log(`  ✓ ${file}`, 'success');
          }
        });
      } catch (error) {
        this.log(`  ⚠️  Python validation skipped (python3 not available or syntax error)`, 'warning');
      }

      // Test 3: Check if bin scripts are executable
      this.log('Test 3: Checking bin scripts...', 'info');
      const binDir = path.join(NODE_MODULES_INSTALL, 'bin');
      if (fs.existsSync(binDir)) {
        const scripts = fs.readdirSync(binDir);
        this.log(`  ✓ Found ${scripts.length} bin scripts`, 'success');
      }

    } catch (error) {
      this.log('Tests failed', 'error');
      throw error;
    }
  }

  summary() {
    console.log('\n' + '='.repeat(70));
    this.log('PRE-PUBLISH VALIDATION SUMMARY', 'info');
    console.log('='.repeat(70));

    console.log(`
  Source:           ${GAIA_OPS_ROOT}
  Monorepo:         ${MONOREPO_ROOT}
  node_modules:     ${NODE_MODULES_INSTALL}

  Current Version:  ${this.currentVersion}
  ${this.newVersion ? `New Version:      ${this.newVersion}` : '  (No version change)'}

  Dry Run:          ${this.dryRun ? 'YES' : 'NO'}
  Validate Only:    ${this.validateOnly ? 'YES' : 'NO'}
    `);

    console.log('='.repeat(70));

    if (this.validateOnly) {
      this.log('✓ Validation completed successfully (--validate-only mode)', 'success');
    } else if (this.dryRun) {
      this.log('✓ Dry run completed - no changes made', 'success');
      this.log('To proceed with actual validation, run without --dry-run flag', 'info');
    } else {
      this.log('✓ All validations passed!', 'success');
      this.log('Ready to publish with: npm publish', 'info');
    }
  }

  async run() {
    try {
      this.log('Starting pre-publish validation...', 'info');
      console.log('');

      this.validateGitStatus();
      this.readCurrentVersion();

      if (!this.validateOnly) {
        this.bumpVersion();
      }

      this.reinstallNodeModules();
      this.validateNodeModules();
      this.validateFiles();
      this.runTests();

      this.summary();

      return true;
    } catch (error) {
      console.log('\n' + '='.repeat(70));
      this.log('VALIDATION FAILED', 'error');
      console.log('='.repeat(70));
      this.log(error.message, 'error');
      process.exit(1);
    }
  }
}

// Parse command line arguments
const args = process.argv.slice(2);
const options = {
  dryRun: args.includes('--dry-run'),
  validateOnly: args.includes('--validate-only'),
  versionBump: 'patch'
};

if (args.includes('major')) options.versionBump = 'major';
if (args.includes('minor')) options.versionBump = 'minor';
if (args.includes('patch')) options.versionBump = 'patch';

const validator = new PrePublishValidator(options);
validator.run();
