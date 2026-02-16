/**
 * @jaguilar87/gaia-ops
 *
 * Multi-agent orchestration system for Claude Code - DevOps automation toolkit
 *
 * Usage:
 *   import { getAgentPath, getToolPath, getConfigPath } from '@jaguilar87/gaia-ops';
 *   const agentPath = getAgentPath('gitops-operator');
 *   const toolPath = getToolPath('context_provider.py');
 *   const configPath = getConfigPath('git_standards.json');
 */

import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export const PACKAGE_ROOT = __dirname;

/**
 * Get absolute path to an agent definition
 * @param {string} agentName - Name of the agent (e.g., 'gitops-operator')
 * @returns {string} Absolute path to agent file
 */
export function getAgentPath(agentName) {
  return join(PACKAGE_ROOT, 'agents', `${agentName}.md`);
}

/**
 * Get absolute path to a tool
 * @param {string} toolName - Name of the tool (e.g., 'context_provider.py')
 * @returns {string} Absolute path to tool file
 */
export function getToolPath(toolName) {
  return join(PACKAGE_ROOT, 'tools', toolName);
}

/**
 * Get absolute path to a hook
 * @param {string} hookName - Name of the hook (e.g., 'pre-commit')
 * @returns {string} Absolute path to hook file
 */
export function getHookPath(hookName) {
  return join(PACKAGE_ROOT, 'hooks', hookName);
}

/**
 * Get absolute path to a command
 * @param {string} commandName - Name of the command (e.g., 'architect.md')
 * @returns {string} Absolute path to command file
 */
export function getCommandPath(commandName) {
  return join(PACKAGE_ROOT, 'commands', commandName);
}

/**
 * Get absolute path to a template
 * @param {string} templateName - Name of the template (e.g., 'CLAUDE.template.md')
 * @returns {string} Absolute path to template file
 */
export function getTemplatePath(templateName) {
  return join(PACKAGE_ROOT, 'templates', templateName);
}

/**
 * Get absolute path to config file
 * @param {string} configName - Name of the config (e.g., 'git_standards.json')
 * @returns {string} Absolute path to config file
 */
export function getConfigPath(configName) {
  return join(PACKAGE_ROOT, 'config', configName);
}

export default {
  PACKAGE_ROOT,
  getAgentPath,
  getToolPath,
  getHookPath,
  getCommandPath,
  getTemplatePath,
  getConfigPath
};
