#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Auto-update script
 *
 * Runs automatically on npm install/update (postinstall hook)
 *
 * Purpose:
 * - Regenerate CLAUDE.md from template (preserving existing values)
 * - Regenerate settings.json from template
 * - Only runs if CLAUDE.md already exists (skip first-time install, let gaia-init handle it)
 *
 * Usage: Automatic (npm postinstall hook)
 */

import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import chalk from 'chalk';
import ora from 'ora';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CWD = process.cwd();

/**
 * Extract placeholder values from existing CLAUDE.md
 */
async function extractExistingValues() {
  const claudeMdPath = join(CWD, 'CLAUDE.md');

  if (!existsSync(claudeMdPath)) {
    return null; // First time install, skip
  }

  try {
    const content = await fs.readFile(claudeMdPath, 'utf-8');

    // Extract values from CLAUDE.md (simple pattern matching)
    const gitopsMatch = content.match(/- \*\*GitOps Path:\*\* (.+)/);
    const terraformMatch = content.match(/- \*\*Terraform Path:\*\* (.+)/);
    const appServicesMatch = content.match(/- \*\*App Services Path:\*\* (.+)/);
    const projectConfigMatch = content.match(/\*\*This project:\*\*\s+([\s\S]+?)## System Paths/);

    return {
      gitops: gitopsMatch ? gitopsMatch[1].trim() : './gitops',
      terraform: terraformMatch ? terraformMatch[1].trim() : './terraform',
      appServices: appServicesMatch ? appServicesMatch[1].trim() : './app-services',
      projectConfig: projectConfigMatch ? projectConfigMatch[1].trim() : ''
    };
  } catch (error) {
    console.warn(chalk.yellow(`⚠️  Could not extract existing values: ${error.message}`));
    return null;
  }
}

/**
 * Regenerate CLAUDE.md from template
 */
async function updateClaudeMd() {
  const spinner = ora('Updating CLAUDE.md...').start();

  try {
    // Get template path from gaia-ops package
    const templatePath = join(__dirname, '../templates/CLAUDE.template.md');

    if (!existsSync(templatePath)) {
      spinner.warn('Template not found, skipping CLAUDE.md update');
      return false;
    }

    let template = await fs.readFile(templatePath, 'utf-8');

    // Extract existing values if CLAUDE.md exists
    const existingValues = await extractExistingValues();

    if (!existingValues) {
      // First time install - don't auto-generate, gaia-init handles it
      spinner.info('First-time installation detected - skipping auto-update');
      return false;
    }

    // Replace placeholders with extracted values
    template = template.replace(/{{GITOPS_PATH}}/g, existingValues.gitops);
    template = template.replace(/{{TERRAFORM_PATH}}/g, existingValues.terraform);
    template = template.replace(/{{APP_SERVICES_PATH}}/g, existingValues.appServices);

    if (existingValues.projectConfig) {
      template = template.replace(/{{PROJECT_CONFIG}}/g, existingValues.projectConfig);
    }

    // Write updated CLAUDE.md
    const claudeMdPath = join(CWD, 'CLAUDE.md');
    await fs.writeFile(claudeMdPath, template, 'utf-8');

    spinner.succeed('CLAUDE.md updated successfully');
    return true;
  } catch (error) {
    spinner.fail(`Failed to update CLAUDE.md: ${error.message}`);
    return false;
  }
}

/**
 * Regenerate settings.json from template
 */
async function updateSettingsJson() {
  const spinner = ora('Updating settings.json...').start();

  try {
    const templatePath = join(__dirname, '../templates/settings.template.json');
    const settingsPath = join(CWD, '.claude', 'settings.json');

    if (!existsSync(templatePath)) {
      spinner.warn('Settings template not found, skipping');
      return false;
    }

    if (!existsSync(join(CWD, '.claude'))) {
      spinner.info('First-time installation detected - skipping auto-update');
      return false;
    }

    // Copy template to settings.json (overwrite to get latest)
    const template = await fs.readFile(templatePath, 'utf-8');
    await fs.writeFile(settingsPath, template, 'utf-8');

    spinner.succeed('settings.json updated successfully');
    return true;
  } catch (error) {
    spinner.fail(`Failed to update settings.json: ${error.message}`);
    return false;
  }
}

/**
 * Main function
 */
async function main() {
  console.log(chalk.cyan('\n🔄 @jaguilar87/gaia-ops auto-update\n'));

  try {
    const claudeUpdated = await updateClaudeMd();
    const settingsUpdated = await updateSettingsJson();

    if (claudeUpdated || settingsUpdated) {
      console.log(chalk.green('\n✅ Auto-update completed\n'));
      console.log(chalk.gray('Next steps:'));
      if (claudeUpdated) {
        console.log(chalk.gray('  • Review CLAUDE.md for any needed adjustments'));
      }
      if (settingsUpdated) {
        console.log(chalk.gray('  • Review settings.json for security rules'));
      }
    } else {
      // Silent exit on first-time install (gaia-init will handle it)
      process.exit(0);
    }
  } catch (error) {
    console.error(chalk.red(`\n❌ Auto-update failed: ${error.message}\n`));
    // Don't fail npm install, just warn
    process.exit(0);
  }
}

main();
