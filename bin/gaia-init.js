#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Installer CLI v2
 *
 * Scan-Confirm-Install flow: auto-detects everything, asks only gaps.
 *
 * Usage:
 *   npx gaia-init                              # Interactive (scan + confirm)
 *   npx gaia-init --non-interactive             # CI/CD mode (scan + auto-accept)
 *   npx gaia-init --non-interactive --cluster my-cluster  # Override detected values
 *
 * CLI Options:
 *   --non-interactive, -y     Accept detected values without confirmation
 *   --gitops <path>           Override GitOps directory
 *   --terraform <path>        Override Terraform directory
 *   --app-services <path>     Override App Services directory
 *   --project-id <id>         Override cloud project ID (GCP or AWS)
 *   --region <region>         Override primary region
 *   --cluster <name>          Override cluster name
 *   --skip-claude-install     Skip Claude Code installation (not recommended)
 *   --project-context-repo <url>  Git repository with existing project-context.json
 *
 * Environment Variables (override detected values):
 *   CLAUDE_GITOPS_DIR, CLAUDE_TERRAFORM_DIR, CLAUDE_APP_SERVICES_DIR,
 *   CLAUDE_PROJECT_ID, CLAUDE_REGION, CLAUDE_CLUSTER_NAME,
 *   CLAUDE_PROJECT_CONTEXT_REPO
 */

import { fileURLToPath } from 'url';
import { basename, dirname, join, relative, resolve, isAbsolute } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import { exec } from 'child_process';
import { promisify } from 'util';
import prompts from 'prompts';
import chalk from 'chalk';
import ora from 'ora';
import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const CWD = process.cwd();

// Package root: relative to this bin/ file
const PACKAGE_ROOT = resolve(__dirname, '..');

function getTemplatePath(name) {
  return join(PACKAGE_ROOT, 'templates', name);
}

// ============================================================================
// Phase 1: Silent Scan
// ============================================================================

/**
 * Run all detection in parallel, return unified scan result.
 */
async function runScan() {
  const spinner = ora('Scanning project structure...').start();

  try {
    const [dirs, cloud, tfvars, gitRemotes, k8s, identity, claudeCode] = await Promise.all([
      scanDirectories(),
      scanCloudProvider(),
      scanCloudValues(),
      scanGitRemotes(),
      scanKubernetesFiles(),
      scanProjectIdentity(),
      scanClaudeCode()
    ]);

    spinner.succeed('Project scanned');

    return { dirs, cloud, tfvars, gitRemotes, k8s, identity, claudeCode };
  } catch (error) {
    spinner.fail('Scan failed');
    throw error;
  }
}

/**
 * Detect directory structure by scanning CWD for known patterns.
 * Falls back to subdirectories that contain .git if not found at CWD level.
 */
async function scanDirectories() {
  const result = { gitops: null, terraform: null, appServices: null };

  let entries;
  try {
    entries = await fs.readdir(CWD);
  } catch {
    return result;
  }

  const candidates = {
    gitops: ['gitops', 'k8s', 'kubernetes', 'manifests', 'deployments'],
    terraform: ['terraform', 'tf', 'infrastructure', 'iac', 'infra'],
    appServices: ['app-services', 'services', 'apps', 'applications', 'src']
  };

  // First pass: check CWD level
  for (const [key, patterns] of Object.entries(candidates)) {
    for (const pattern of patterns) {
      if (entries.includes(pattern)) {
        result[key] = `./${pattern}`;
        break;
      }
    }
  }

  // Second pass: for any still-null paths, check subdirectories with .git
  const missingKeys = Object.keys(candidates).filter(k => result[k] === null);
  if (missingKeys.length > 0) {
    const repos = await findSubdirRepos();
    for (const repo of repos) {
      let repoEntries;
      try {
        repoEntries = await fs.readdir(repo.path);
      } catch {
        continue;
      }

      for (const key of missingKeys) {
        if (result[key] !== null) continue;
        for (const pattern of candidates[key]) {
          if (repoEntries.includes(pattern)) {
            result[key] = `./${repo.name}/${pattern}`;
            break;
          }
        }
      }

      // Stop early if all found
      if (Object.values(result).every(v => v !== null)) break;
    }
  }

  return result;
}

/**
 * Detect cloud provider, project ID, and region from terraform files.
 * Scans terraform/**\/*.tf and *.hcl for provider blocks.
 */
async function scanCloudProvider() {
  const result = { provider: null, projectId: null, region: null };

  // Find terraform directory (CWD first, then subdirs with .git)
  const tfDir = await findTerraformDir();
  if (!tfDir) return result;

  // Recursively find .tf and .hcl files (max 3 levels deep to avoid huge scans)
  const tfFiles = await findFiles(tfDir, '.tf', 3);
  const hclFiles = await findFiles(tfDir, '.hcl', 3);
  const allFiles = [...tfFiles, ...hclFiles];

  let hasGoogle = false;
  let hasAws = false;

  for (const file of allFiles) {
    try {
      const content = await fs.readFile(file, 'utf-8');

      // Detect providers
      if (/provider\s+"google(?:-beta)?"/.test(content)) hasGoogle = true;
      if (/provider\s+"aws"/.test(content)) hasAws = true;

      // Extract GCP project ID — scoped to provider block context
      if (!result.projectId && hasGoogle) {
        const providerBlock = extractProviderBlock(content, 'google');
        if (providerBlock) {
          const projectMatch = providerBlock.match(/project\s*=\s*"([^"]+)"/);
          if (projectMatch) result.projectId = projectMatch[1];
        }
      }

      // Extract AWS account (from provider block or data.aws_caller_identity)
      if (!result.projectId && hasAws) {
        const providerBlock = extractProviderBlock(content, 'aws');
        if (providerBlock) {
          const accountMatch = providerBlock.match(/account_id\s*=\s*"(\d{12})"/);
          if (accountMatch) result.projectId = accountMatch[1];
        }
      }

      // Extract region — scoped to provider block, supports multi-segment regions (us-gov-west-1)
      if (!result.region) {
        const providerName = hasGoogle ? 'google' : (hasAws ? 'aws' : null);
        const providerBlock = providerName ? extractProviderBlock(content, providerName) : null;
        const source = providerBlock || content;
        const regionMatch = source.match(/region\s*=\s*"([a-z]+-[a-z0-9]+(?:-[a-z0-9]+)*)"/);
        if (regionMatch) result.region = regionMatch[1];
      }
    } catch {
      // Skip unreadable files
    }
  }

  if (hasGoogle && hasAws) result.provider = 'multi-cloud';
  else if (hasGoogle) result.provider = 'gcp';
  else if (hasAws) result.provider = 'aws';

  return result;
}

/**
 * Global cloud value scanner. Searches ALL files recursively across the entire
 * workspace (CWD + subdirectory repos) for project ID, region, and cluster name.
 *
 * Uses four extraction strategies:
 *   A) HCL/TF key=value assignments (tfvars, terragrunt.hcl, .tf files)
 *   B) CI/CD YAML variables (.gitlab-ci*.yml, .github/workflows/*.yml)
 *   C) GCP Artifact Registry URLs (region-docker.pkg.dev/project/...)
 *   D) GKE/EKS resource path references (projects/P/locations/R/clusters/C)
 *
 * Returns the most frequently found value for each field.
 */
async function scanCloudValues() {
  const candidates = {
    projectId: new Map(),  // value -> { count, sources: Set }
    region: new Map(),
    clusterName: new Map()
  };

  /**
   * Record a candidate value with its source file.
   */
  function record(field, value, source) {
    if (!value || typeof value !== 'string') return;
    // Basic sanity: skip template/variable references
    if (value.includes('${') || value.includes('var.') || value.includes('local.')) return;
    const map = candidates[field];
    if (!map) return;
    const existing = map.get(value);
    if (existing) {
      existing.count++;
      existing.sources.add(source);
    } else {
      map.set(value, { count: 1, sources: new Set([source]) });
    }
  }

  // ---- Collect all searchable directories: CWD + subdirectory repos ----
  const searchRoots = [CWD];
  const repos = await findSubdirRepos();
  for (const repo of repos) {
    searchRoots.push(repo.path);
  }

  // ---- Gather all relevant files across every root ----
  const relevantExtensions = [
    '.tfvars', '.hcl', '.tf',
    '.yml', '.yaml'
  ];

  const skipDirs = new Set([
    'node_modules', '.git', '.terraform', 'vendor', 'dist',
    'build', '__pycache__', '.next', '.cache', '.venv', 'venv'
  ]);

  const allFiles = await findAllFiles(searchRoots, relevantExtensions, skipDirs, 8, 500);

  // ---- HCL key=value patterns ----
  const projectPatterns = ['project', 'project_id', 'gcp_project', 'google_project', 'aws_account_id'];
  const regionPatterns = ['region', 'gcp_region', 'aws_region', 'location'];
  const clusterPatterns = ['cluster_name', 'cluster', 'gke_cluster_name', 'eks_cluster_name'];

  // ---- CI/CD YAML variable patterns (case-insensitive substring match) ----
  const ciProjectPatterns = ['project_id', 'project-id', 'gcp_project', 'account_id'];
  const ciRegionPatterns = ['region'];
  const ciClusterPatterns = ['cluster_name', 'cluster-name'];

  // ---- Regex for GCP Artifact Registry URLs ----
  // Matches: us-east4-docker.pkg.dev/oci-pos-dev-471216/registry-name
  const artifactRegistryRegex = /([a-z]+-[a-z0-9]+(?:-[a-z0-9]+)*)-docker\.pkg\.dev\/([^/\s"']+)/g;

  // ---- Regex for GKE/EKS full resource paths ----
  // Matches: projects/PROJECT/locations/REGION/clusters/CLUSTER
  const gkePathRegex = /projects\/([^/\s"']+)\/locations\/([^/\s"']+)\/clusters\/([^/\s"']+)/g;

  for (const file of allFiles) {
    try {
      const content = await fs.readFile(file, 'utf-8');
      const ext = file.substring(file.lastIndexOf('.'));
      const base = basename(file);

      // ----- Strategy A: HCL/TF key=value assignments -----
      if (ext === '.tfvars' || ext === '.tf' || base === 'terragrunt.hcl') {
        const assignmentRegex = /^\s*(\w+)\s*=\s*"([^"]+)"/gm;
        let m;
        while ((m = assignmentRegex.exec(content)) !== null) {
          const keyLower = m[1].toLowerCase();
          const value = m[2];
          if (projectPatterns.includes(keyLower)) record('projectId', value, file);
          if (regionPatterns.includes(keyLower)) record('region', value, file);
          if (clusterPatterns.includes(keyLower)) record('clusterName', value, file);
        }
      }

      // ----- Strategy B: CI/CD YAML variables -----
      if (ext === '.yml' || ext === '.yaml') {
        // Match patterns like: KEY: value  or  KEY: "value"
        const yamlKvRegex = /^\s*([\w-]+)\s*:\s*["']?([^"'\s#][^"'\n#]*)["']?\s*$/gm;
        let m;
        while ((m = yamlKvRegex.exec(content)) !== null) {
          const keyLower = m[1].toLowerCase();
          const value = m[2].trim();
          // Skip template references and multiline indicators
          if (value.startsWith('{') || value.startsWith('|') || value.startsWith('>')) continue;

          if (ciProjectPatterns.some(p => keyLower.includes(p))) record('projectId', value, file);
          if (ciRegionPatterns.some(p => keyLower.includes(p))) record('region', value, file);
          if (ciClusterPatterns.some(p => keyLower.includes(p))) record('clusterName', value, file);
        }
      }

      // ----- Strategy C: GCP Artifact Registry URLs (any file type) -----
      {
        let m;
        artifactRegistryRegex.lastIndex = 0;
        while ((m = artifactRegistryRegex.exec(content)) !== null) {
          record('region', m[1], file);
          record('projectId', m[2], file);
        }
      }

      // ----- Strategy D: GKE/EKS resource path references (any file type) -----
      {
        let m;
        gkePathRegex.lastIndex = 0;
        while ((m = gkePathRegex.exec(content)) !== null) {
          record('projectId', m[1], file);
          record('region', m[2], file);
          record('clusterName', m[3], file);
        }
      }
    } catch {
      // Skip unreadable files
    }
  }

  // ---- Pick the most frequently found value for each field ----
  function pickTop(map) {
    if (map.size === 0) return null;
    let topValue = null;
    let topCount = 0;
    for (const [value, { count }] of map) {
      if (count > topCount) {
        topCount = count;
        topValue = value;
      }
    }
    return topValue;
  }

  return {
    projectId: pickTop(candidates.projectId),
    region: pickTop(candidates.region),
    clusterName: pickTop(candidates.clusterName)
  };
}

/**
 * Recursively walk multiple root directories collecting files that match
 * any of the given extensions. Respects a skip-list of directory names,
 * a maximum depth, and a maximum total file count.
 *
 * @param {string[]} roots       - Absolute paths to start searching from
 * @param {string[]} extensions  - File extensions to include (e.g. ['.tf', '.yml'])
 * @param {Set<string>} skipDirs - Directory names to skip
 * @param {number} maxDepth      - Maximum recursion depth per root
 * @param {number} maxFiles      - Stop collecting after this many files
 * @returns {Promise<string[]>}  - Array of absolute file paths
 */
async function findAllFiles(roots, extensions, skipDirs, maxDepth, maxFiles) {
  const results = [];
  const visited = new Set(); // prevent scanning the same directory twice

  async function walk(dir, depth) {
    if (depth > maxDepth || results.length >= maxFiles) return;

    const realDir = resolve(dir);
    if (visited.has(realDir)) return;
    visited.add(realDir);

    let entries;
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }

    for (const entry of entries) {
      if (results.length >= maxFiles) return;

      const name = entry.name;
      if (name.startsWith('.') && name !== '.gitlab-ci.yml' && !name.startsWith('.gitlab-ci') && !name.startsWith('.github')) {
        // Skip hidden files/dirs except CI config patterns
        if (entry.isDirectory()) continue;
        if (!name.startsWith('.gitlab-ci')) continue;
      }

      const fullPath = join(dir, name);

      if (entry.isDirectory()) {
        if (skipDirs.has(name)) continue;
        await walk(fullPath, depth + 1);
      } else {
        // Check if file is too large (skip >1MB)
        try {
          const stat = await fs.stat(fullPath);
          if (stat.size > 1048576) continue;
        } catch {
          continue;
        }

        // Check extension match
        const matches = extensions.some(ext => name.endsWith(ext));
        if (matches) {
          // Quick binary check: read first 512 bytes for null bytes
          try {
            const fd = await fs.open(fullPath, 'r');
            const buf = Buffer.alloc(512);
            await fd.read(buf, 0, 512, 0);
            await fd.close();
            if (buf.includes(0)) continue; // binary file
          } catch {
            continue;
          }

          results.push(fullPath);
        }
      }
    }
  }

  for (const root of roots) {
    if (results.length >= maxFiles) break;
    await walk(root, 0);
  }

  return results;
}

/**
 * Scan Kubernetes/GitOps manifests for cluster name references.
 * Looks at Flux Kustomization files and cluster-related YAML.
 */
async function scanKubernetesFiles() {
  const result = { clusterName: null };

  // Find gitops directory (CWD first, then subdirs with .git)
  const gitopsDir = await findGitopsDir();
  if (!gitopsDir) return result;

  try {
    // Look for cluster directories under gitops/ (e.g., gitops/clusters/<cluster-name>/)
    // Also check for directories whose name contains "cluster"
    const entries = await fs.readdir(gitopsDir, { withFileTypes: true });

    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith('.') || entry.name === 'node_modules') continue;

      // Check if directory name itself indicates a cluster
      if (entry.name === 'clusters') {
        try {
          const clusterEntries = await fs.readdir(join(gitopsDir, entry.name), { withFileTypes: true });
          for (const clusterEntry of clusterEntries) {
            if (clusterEntry.isDirectory() && !clusterEntry.name.startsWith('.')) {
              result.clusterName = clusterEntry.name;
              return result;
            }
          }
        } catch {
          // Skip unreadable
        }
      }

      // Scan YAML files for Flux Kustomization spec.path referencing cluster
      const subDir = join(gitopsDir, entry.name);
      try {
        const yamlFiles = await findFiles(subDir, '.yaml', 3);
        for (const file of yamlFiles) {
          try {
            const content = await fs.readFile(file, 'utf-8');

            // Flux gotk-sync.yaml: path: ./clusters/<cluster-name>
            const pathMatch = content.match(/path:\s*\.\/clusters\/([^\s/]+)/);
            if (pathMatch && !result.clusterName) {
              result.clusterName = pathMatch[1];
              return result;
            }

            // Look for cluster name in Kustomization metadata
            if (content.includes('kind: Kustomization') && content.includes('toolkit.fluxcd.io')) {
              const nameMatch = content.match(/name:\s+([^\s]+cluster[^\s]*)/i);
              if (nameMatch && !result.clusterName) {
                result.clusterName = nameMatch[1];
                return result;
              }
            }
          } catch {
            // Skip unreadable files
          }
        }
      } catch {
        // Skip unreadable subdirectories
      }
    }
  } catch {
    // Skip if gitops dir not readable
  }

  return result;
}

/**
 * Detect project identity: project name, git platform, and CI/CD platform.
 * Uses only local file scanning (no external CLI calls).
 */
async function scanProjectIdentity() {
  const result = { projectName: null, gitPlatform: null, ciPlatform: null };

  // Collect dirs to check: CWD first, then subdirs with .git
  const dirsToCheck = [CWD];
  const repos = await findSubdirRepos();
  for (const repo of repos) {
    dirsToCheck.push(repo.path);
  }

  // 1. Project name from package.json or directory name
  for (const dir of dirsToCheck) {
    if (result.projectName) break;
    try {
      const pkgPath = join(dir, 'package.json');
      if (existsSync(pkgPath)) {
        const pkg = JSON.parse(await fs.readFile(pkgPath, 'utf-8'));
        if (pkg.name && !pkg.name.startsWith('@') && pkg.name !== 'my-project') {
          result.projectName = pkg.name;
        } else if (pkg.name && pkg.name.startsWith('@')) {
          // Scoped package: extract the package part after /
          const parts = pkg.name.split('/');
          if (parts.length > 1 && parts[1] !== 'my-project') {
            result.projectName = parts[1];
          }
        }
      }
    } catch {
      // Skip
    }
  }

  if (!result.projectName) {
    result.projectName = basename(CWD);
  }

  // 2. Git platform from remote URLs
  for (const dir of dirsToCheck) {
    if (result.gitPlatform) break;
    try {
      if (existsSync(join(dir, '.git'))) {
        const remote = await getGitRemote(dir);
        if (remote) {
          if (remote.includes('gitlab.com') || remote.includes('gitlab.')) result.gitPlatform = 'gitlab';
          else if (remote.includes('github.com')) result.gitPlatform = 'github';
          else if (remote.includes('bitbucket.org') || remote.includes('bitbucket.')) result.gitPlatform = 'bitbucket';
        }
      }
    } catch {
      // Skip
    }
  }

  // 3. CI/CD platform from config files
  for (const dir of dirsToCheck) {
    if (result.ciPlatform) break;
    try {
      if (existsSync(join(dir, '.gitlab-ci.yml'))) {
        result.ciPlatform = 'gitlab-ci';
      } else if (existsSync(join(dir, '.github', 'workflows'))) {
        result.ciPlatform = 'github-actions';
      } else if (existsSync(join(dir, 'Jenkinsfile'))) {
        result.ciPlatform = 'jenkins';
      } else if (existsSync(join(dir, '.circleci'))) {
        result.ciPlatform = 'circleci';
      }
    } catch {
      // Skip
    }
  }

  return result;
}

/**
 * Extract the content of a provider block from HCL/TF content.
 * Handles nested braces to find the correct closing brace.
 */
function extractProviderBlock(content, providerName) {
  const pattern = new RegExp(`provider\\s+"${providerName}(?:-beta)?"\\s*\\{`);
  const match = content.match(pattern);
  if (!match) return null;

  let depth = 1;
  let start = match.index + match[0].length;

  for (let i = start; i < content.length && depth > 0; i++) {
    if (content[i] === '{') depth++;
    else if (content[i] === '}') depth--;
    if (depth === 0) return content.slice(start, i);
  }

  return null;
}

/**
 * Detect git remotes from repositories in subdirectories.
 * Returns list of {path, remote} objects.
 */
async function scanGitRemotes() {
  const remotes = [];

  // Check root
  if (existsSync(join(CWD, '.git'))) {
    const remote = await getGitRemote(CWD);
    if (remote) remotes.push({ path: '.', remote });
  }

  // Check 1 level deep
  let entries;
  try {
    entries = await fs.readdir(CWD, { withFileTypes: true });
  } catch {
    return remotes;
  }

  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith('.') || entry.name === 'node_modules') continue;

    const subDir = join(CWD, entry.name);
    if (existsSync(join(subDir, '.git'))) {
      const remote = await getGitRemote(subDir);
      if (remote) remotes.push({ path: `./${entry.name}`, remote });
      continue;
    }

    // Check 2 levels deep (e.g., gitops/qxo-monorepo/.git)
    try {
      const subEntries = await fs.readdir(subDir, { withFileTypes: true });
      for (const subEntry of subEntries) {
        if (!subEntry.isDirectory() || subEntry.name.startsWith('.') || subEntry.name === 'node_modules') continue;
        const deepDir = join(subDir, subEntry.name);
        if (existsSync(join(deepDir, '.git'))) {
          const remote = await getGitRemote(deepDir);
          if (remote) remotes.push({ path: `./${entry.name}/${subEntry.name}`, remote });
        }
      }
    } catch {
      // Skip unreadable subdirs
    }
  }

  return remotes;
}

/**
 * Check if Claude Code CLI is installed.
 */
async function scanClaudeCode() {
  try {
    const { stdout } = await execAsync('claude --version 2>/dev/null || claude-code --version 2>/dev/null');
    const version = stdout.trim().split('\n')[0];
    return { installed: true, version };
  } catch {
    return { installed: false, version: null };
  }
}

// ============================================================================
// Phase 2: Display Results + Confirm
// ============================================================================

/**
 * Build unified config from scan results + CLI overrides + env vars.
 * Priority: CLI args > env vars > scan results > defaults
 */
function buildConfig(scan, args) {
  return {
    gitops: args.gitops || process.env.CLAUDE_GITOPS_DIR || scan.dirs.gitops || '',
    terraform: args.terraform || process.env.CLAUDE_TERRAFORM_DIR || scan.dirs.terraform || '',
    appServices: args.appServices || process.env.CLAUDE_APP_SERVICES_DIR || scan.dirs.appServices || '',
    cloudProvider: scan.cloud.provider || 'gcp',
    projectId: args.projectId || process.env.CLAUDE_PROJECT_ID || scan.cloud.projectId || scan.tfvars.projectId || '',
    region: args.region || process.env.CLAUDE_REGION || scan.cloud.region || scan.tfvars.region || '',
    clusterName: args.cluster || process.env.CLAUDE_CLUSTER_NAME || scan.tfvars.clusterName || scan.k8s.clusterName || '',
    projectName: scan.identity.projectName || basename(CWD),
    gitPlatform: scan.identity.gitPlatform || null,
    ciPlatform: scan.identity.ciPlatform || null,
    projectContextRepo: args.projectContextRepo || process.env.CLAUDE_PROJECT_CONTEXT_REPO || '',
    claudeCode: scan.claudeCode,
    gitRemotes: scan.gitRemotes
  };
}

/**
 * Display scan results and ask for confirmation.
 * Returns final config (possibly edited by user).
 */
async function displayAndConfirm(config) {
  // Banner
  console.log(chalk.cyan(`
  ██████╗  █████╗ ██╗ █████╗      ██████╗ ██████╗ ███████╗
 ██╔════╝ ██╔══██╗██║██╔══██╗    ██╔═══██╗██╔══██╗██╔════╝
 ██║  ███╗███████║██║███████║    ██║   ██║██████╔╝███████╗
 ██║   ██║██╔══██║██║██╔══██║    ██║   ██║██╔═══╝ ╚════██║
 ╚██████╔╝██║  ██║██║██║  ██║    ╚██████╔╝██║     ███████║
  ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝     ╚═════╝ ╚═╝     ╚══════╝
  `));

  // Detected config display
  console.log(chalk.cyan.bold('  Detected Configuration\n'));

  // Paths
  console.log(chalk.white('  Paths'));
  printField('GitOps', config.gitops || null, config.gitops ? existsSync(resolve(CWD, config.gitops)) : false);
  printField('Terraform', config.terraform || null, config.terraform ? existsSync(resolve(CWD, config.terraform)) : false);
  printField('App Services', config.appServices || null, config.appServices ? existsSync(resolve(CWD, config.appServices)) : false);

  // Cloud
  console.log(chalk.white('\n  Cloud'));
  printField('Provider', config.cloudProvider?.toUpperCase() || null, !!config.cloudProvider);
  printField('Project ID', config.projectId || null, !!config.projectId);
  printField('Region', config.region || null, !!config.region);
  printField('Cluster', config.clusterName || null, !!config.clusterName);

  // Identity
  console.log(chalk.white('\n  Identity'));
  printField('Project Name', config.projectName || null, !!config.projectName);
  printField('Git Platform', config.gitPlatform || null, !!config.gitPlatform);
  printField('CI/CD', config.ciPlatform || null, !!config.ciPlatform);

  // Git remotes (informational)
  if (config.gitRemotes.length > 0) {
    console.log(chalk.white('\n  Git Repositories'));
    for (const { path, remote } of config.gitRemotes.slice(0, 5)) {
      console.log(chalk.gray(`    ${path} → ${remote}`));
    }
    if (config.gitRemotes.length > 5) {
      console.log(chalk.gray(`    ... and ${config.gitRemotes.length - 5} more`));
    }
  }

  // Claude Code
  console.log(chalk.white('\n  Claude Code'));
  if (config.claudeCode.installed) {
    console.log(chalk.green(`    ${config.claudeCode.version}  ✓`));
  } else {
    console.log(chalk.yellow('    Not installed (will be installed automatically)'));
  }

  console.log('');

  // Identify gaps (items that need user input)
  const gaps = [];
  if (!config.gitops) gaps.push('gitops');
  if (!config.terraform) gaps.push('terraform');
  if (!config.appServices) gaps.push('appServices');
  if (!config.projectId) gaps.push('projectId');
  if (!config.region) gaps.push('region');
  if (!config.clusterName) gaps.push('clusterName');

  return { config, gaps };
}

function printField(label, value, found) {
  const padded = label.padEnd(14);
  if (value && found) {
    console.log(chalk.green(`    ${padded} ${value}  ✓`));
  } else if (value) {
    console.log(chalk.yellow(`    ${padded} ${value}  (will be created)`));
  } else {
    console.log(chalk.yellow(`    ${padded} (not detected)  ?`));
  }
}

/**
 * Ask user to confirm or edit the detected configuration.
 * Returns final config.
 */
async function confirmOrEdit(config, gaps) {
  // If there are gaps, ask for them first
  if (gaps.length > 0) {
    console.log(chalk.yellow(`  ${gaps.length} item(s) need your input:\n`));

    const gapQuestions = [];

    if (gaps.includes('gitops')) {
      gapQuestions.push({
        type: 'text',
        name: 'gitops',
        message: '  GitOps directory (Enter to skip):',
        initial: ''
      });
    }

    if (gaps.includes('terraform')) {
      gapQuestions.push({
        type: 'text',
        name: 'terraform',
        message: '  Terraform directory (Enter to skip):',
        initial: ''
      });
    }

    if (gaps.includes('appServices')) {
      gapQuestions.push({
        type: 'text',
        name: 'appServices',
        message: '  App Services directory (Enter to skip):',
        initial: ''
      });
    }

    if (gaps.includes('projectId')) {
      gapQuestions.push({
        type: 'text',
        name: 'projectId',
        message: config.cloudProvider === 'aws'
          ? '  AWS Account ID (Enter to skip):'
          : '  Cloud Project ID (Enter to skip):',
        initial: ''
      });
    }

    if (gaps.includes('region')) {
      gapQuestions.push({
        type: 'text',
        name: 'region',
        message: '  Primary Region (Enter to skip):',
        initial: config.cloudProvider === 'gcp' ? 'us-central1' : 'us-east-1'
      });
    }

    if (gaps.includes('clusterName')) {
      gapQuestions.push({
        type: 'text',
        name: 'clusterName',
        message: '  Cluster Name (Enter to skip):',
        initial: ''
      });
    }

    const gapAnswers = await prompts(gapQuestions, {
      onCancel: () => { console.log(chalk.yellow('\n  Installation cancelled.\n')); process.exit(0); }
    });

    // Merge gap answers into config
    if (gapAnswers.gitops) config.gitops = gapAnswers.gitops;
    if (gapAnswers.terraform) config.terraform = gapAnswers.terraform;
    if (gapAnswers.appServices) config.appServices = gapAnswers.appServices;
    if (gapAnswers.projectId) config.projectId = gapAnswers.projectId;
    if (gapAnswers.region) config.region = gapAnswers.region;
    if (gapAnswers.clusterName !== undefined) config.clusterName = gapAnswers.clusterName;
  }

  // Confirmation
  const { action } = await prompts({
    type: 'select',
    name: 'action',
    message: 'Proceed with this configuration?',
    choices: [
      { title: 'Accept and install', value: 'accept' },
      { title: 'Edit configuration', value: 'edit' },
      { title: 'Cancel', value: 'cancel' }
    ],
    initial: 0
  }, {
    onCancel: () => { console.log(chalk.yellow('\n  Installation cancelled.\n')); process.exit(0); }
  });

  if (action === 'cancel') {
    console.log(chalk.yellow('\n  Installation cancelled.\n'));
    process.exit(0);
  }

  if (action === 'edit') {
    return await editConfig(config);
  }

  return config;
}

/**
 * Let user edit all config fields.
 */
async function editConfig(config) {
  console.log(chalk.gray('\n  Edit any field (press Enter to keep current value):\n'));

  const answers = await prompts([
    { type: 'text', name: 'gitops', message: 'GitOps directory:', initial: config.gitops },
    { type: 'text', name: 'terraform', message: 'Terraform directory:', initial: config.terraform },
    { type: 'text', name: 'appServices', message: 'App Services directory:', initial: config.appServices },
    {
      type: 'select', name: 'cloudProvider', message: 'Cloud provider:',
      choices: [
        { title: 'GCP', value: 'gcp' },
        { title: 'AWS', value: 'aws' },
        { title: 'Multi-cloud', value: 'multi-cloud' }
      ],
      initial: config.cloudProvider === 'aws' ? 1 : config.cloudProvider === 'multi-cloud' ? 2 : 0
    },
    { type: 'text', name: 'projectId', message: 'Project/Account ID:', initial: config.projectId },
    { type: 'text', name: 'region', message: 'Primary region:', initial: config.region },
    { type: 'text', name: 'clusterName', message: 'Cluster name:', initial: config.clusterName },
    { type: 'text', name: 'projectContextRepo', message: 'Project context repo (empty to skip):', initial: config.projectContextRepo }
  ], {
    onCancel: () => { console.log(chalk.yellow('\n  Installation cancelled.\n')); process.exit(0); }
  });

  return {
    ...config,
    ...answers
  };
}

// ============================================================================
// Phase 3 & 4: Install
// ============================================================================

/**
 * Install Claude Code if not present (mandatory).
 */
async function ensureClaudeCode(claudeCodeStatus, skipInstall = false) {
  if (claudeCodeStatus.installed) return;

  if (skipInstall) {
    console.log(chalk.yellow('\n  ⚠️  Claude Code not installed (--skip-claude-install used)'));
    console.log(chalk.gray('  Install manually: npm install -g @anthropic-ai/claude-code\n'));
    return;
  }

  const spinner = ora('Installing Claude Code (mandatory)...').start();

  try {
    await execAsync('npm install -g @anthropic-ai/claude-code', { timeout: 120000 });
    spinner.succeed('Claude Code installed');
  } catch (error) {
    spinner.fail('Failed to install Claude Code');
    console.log(chalk.red(`\n  Error: ${error.message}`));
    console.log(chalk.yellow('  Install manually: npm install -g @anthropic-ai/claude-code\n'));
    // Don't throw - continue installation
  }
}

/**
 * Ensure @jaguilar87/gaia-ops is installed as npm dependency.
 */
async function ensureGaiaOpsPackage() {
  const spinner = ora('Setting up @jaguilar87/gaia-ops...').start();

  try {
    const packageJsonPath = join(CWD, 'package.json');

    // Check if already installed in node_modules
    const pkgPath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops', 'package.json');
    if (existsSync(pkgPath)) {
      spinner.succeed('@jaguilar87/gaia-ops already installed');
      return;
    }

    // Create package.json if missing
    if (!existsSync(packageJsonPath)) {
      await fs.writeFile(packageJsonPath, JSON.stringify({
        name: 'my-project',
        version: '1.0.0',
        private: true,
        dependencies: {}
      }, null, 2), 'utf-8');
    }

    await execAsync('npm install @jaguilar87/gaia-ops', { timeout: 120000 });
    spinner.succeed('@jaguilar87/gaia-ops installed');
  } catch (error) {
    spinner.fail(`Failed to install package: ${error.message}`);
    throw error;
  }
}

/**
 * Create .claude/ directory with symlinks to npm package.
 */
async function createClaudeDirectory() {
  const spinner = ora('Creating .claude/ directory...').start();

  try {
    const claudeDir = join(CWD, '.claude');
    await fs.mkdir(claudeDir, { recursive: true });

    const packagePath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops');
    const relativePath = relative(claudeDir, packagePath);

    const symlinks = [
      'agents', 'tools', 'hooks', 'commands',
      'templates', 'config', 'speckit', 'skills'
    ];

    for (const name of symlinks) {
      const link = join(claudeDir, name);
      if (existsSync(link)) await fs.unlink(link);
      await fs.symlink(join(relativePath, name), link);
    }

    // CHANGELOG.md symlink
    const changelogLink = join(claudeDir, 'CHANGELOG.md');
    if (existsSync(changelogLink)) await fs.unlink(changelogLink);
    await fs.symlink(join(relativePath, 'CHANGELOG.md'), changelogLink);

    // Project-specific directories (NOT symlinked)
    await fs.mkdir(join(claudeDir, 'logs'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'tests'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'project-context'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'project-context', 'workflow-episodic-memory'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'approvals'), { recursive: true });

    spinner.succeed('.claude/ directory created');
  } catch (error) {
    spinner.fail('Failed to create .claude/');
    throw error;
  }
}

/**
 * Install native git hooks (commit-msg) to all detected git repositories.
 *
 * These hooks run at the git level, catching commits made outside Claude Code
 * (manual terminal, IDE, etc.). Complements the Claude Code PreToolUse hook
 * which only intercepts commits made through Claude Code's Bash tool.
 */
async function installGitHooks() {
  const spinner = ora('Installing git hooks...').start();

  try {
    const hookSource = join(PACKAGE_ROOT, 'git-hooks', 'commit-msg');
    if (!existsSync(hookSource)) {
      spinner.warn('git-hooks/commit-msg not found in package, skipping');
      return;
    }

    // Find git repos: check CWD and common subdirectories
    const candidates = [CWD];
    const entries = await fs.readdir(CWD, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory() && !entry.name.startsWith('.') && entry.name !== 'node_modules') {
        candidates.push(join(CWD, entry.name));
      }
    }

    let installed = 0;
    for (const dir of candidates) {
      const gitHooksDir = join(dir, '.git', 'hooks');
      if (!existsSync(gitHooksDir)) continue;

      const dest = join(gitHooksDir, 'commit-msg');
      await fs.copyFile(hookSource, dest);
      await fs.chmod(dest, 0o755);
      installed++;
    }

    if (installed > 0) {
      spinner.succeed(`Git commit-msg hook installed in ${installed} repo(s)`);
    } else {
      spinner.info('No git repositories found, skipping git hooks');
    }
  } catch (error) {
    spinner.warn(`Git hooks installation failed (non-fatal): ${error.message}`);
  }
}

/**
 * Copy static CLAUDE.md from template (no placeholders to replace).
 * Always overwrites — CLAUDE.md is core orchestrator config and must stay
 * in sync with the installed package version.
 */
async function copyClaudeMd() {
  const spinner = ora('Generating CLAUDE.md...').start();

  try {
    const templatePath = getTemplatePath('CLAUDE.template.md');
    const destPath = join(CWD, 'CLAUDE.md');
    await fs.copyFile(templatePath, destPath);
    spinner.succeed('CLAUDE.md synced from package');
  } catch (error) {
    spinner.fail('Failed to generate CLAUDE.md');
    throw error;
  }
}

/**
 * Generate or merge settings.json from template.
 *
 * Behavior:
 * - If settings.json does NOT exist: copy template as-is.
 * - If settings.json EXISTS: deep-merge template into existing, preserving
 *   user customizations (permissions, allowed tools, etc.).
 */
async function copySettingsJson() {
  const spinner = ora('Generating settings.json...').start();

  try {
    const templatePath = getTemplatePath('settings.template.json');
    const destPath = join(CWD, '.claude', 'settings.json');

    if (!existsSync(destPath)) {
      await fs.copyFile(templatePath, destPath);
      spinner.succeed('settings.json generated');
      return;
    }

    // File exists — merge: template is the base, existing values win on conflict
    let templateData, existingData;
    try {
      templateData = JSON.parse(await fs.readFile(templatePath, 'utf-8'));
      existingData = JSON.parse(await fs.readFile(destPath, 'utf-8'));
    } catch {
      // Corrupt existing — overwrite safely
      await fs.copyFile(templatePath, destPath);
      spinner.succeed('settings.json regenerated (previous file was invalid)');
      return;
    }

    // Shallow merge: template keys as base, existing keys override
    // For array keys (permissions, allowedTools), union both sets
    const merged = { ...templateData };
    for (const [key, val] of Object.entries(existingData)) {
      if (Array.isArray(val) && Array.isArray(merged[key])) {
        // Union arrays, preserving order (existing first)
        const combined = [...val];
        for (const item of merged[key]) {
          if (!combined.includes(item)) combined.push(item);
        }
        merged[key] = combined;
      } else {
        merged[key] = val;
      }
    }

    await fs.writeFile(destPath, JSON.stringify(merged, null, 2), 'utf-8');
    spinner.succeed('settings.json updated (template merged, existing preserved)');
  } catch (error) {
    spinner.fail('Failed to generate settings.json');
    throw error;
  }
}

/**
 * Generate governance.md from governance.template.md + project context.
 *
 * Behavior:
 * - If governance.md does NOT exist: write the full interpolated template.
 * - If governance.md EXISTS: skip — managed by speckit.init (runs every agent session).
 */
async function generateGovernanceMd(config, speckitRoot) {
  const spinner = ora('Checking governance.md...').start();

  try {
    // governance.md lives in speckit-root (resolved from project-context.json paths.speckit_root)
    const resolvedRoot = isAbsolute(speckitRoot)
      ? speckitRoot
      : join(CWD, speckitRoot);
    await fs.mkdir(resolvedRoot, { recursive: true });

    const destPath = join(resolvedRoot, 'governance.md');

    if (existsSync(destPath)) {
      spinner.info('governance.md already exists — skipping (managed by speckit.init)');
      return true;
    }

    const templatePath = getTemplatePath('governance.template.md');
    if (!existsSync(templatePath)) {
      spinner.warn('governance.template.md not found — skipping');
      return false;
    }

    const template = await fs.readFile(templatePath, 'utf-8');

    // Derive k8s platform from cloud provider
    const k8sPlatform = config.cloudProvider === 'aws' ? 'EKS'
      : config.cloudProvider === 'gcp' ? 'GKE'
      : 'Kubernetes';

    // Interpolate all placeholders
    const today = new Date().toISOString().split('T')[0];
    const interpolated = template
      .replace(/\[CLOUD_PROVIDER\]/g, (config.cloudProvider || 'gcp').toUpperCase())
      .replace(/\[PRIMARY_REGION\]/g, config.region || 'N/A')
      .replace(/\[PROJECT_ID\]/g, config.projectId || 'N/A')
      .replace(/\[CLUSTER_NAME\]/g, config.clusterName || 'N/A')
      .replace(/\[GITOPS_PATH\]/g, config.gitops || 'N/A')
      .replace(/\[TERRAFORM_PATH\]/g, config.terraform || 'N/A')
      .replace(/\[POSTGRES_INSTANCE\]/g, 'N/A')
      .replace(/\[CONTAINER_REGISTRY\]/g, 'N/A')
      .replace(/\[K8S_PLATFORM\]/g, k8sPlatform)
      .replace(/\[DATE\]/g, today);

    await fs.writeFile(destPath, interpolated, 'utf-8');
    spinner.succeed(`governance.md created at ${join(speckitRoot, 'governance.md')}`);

    return true;
  } catch (error) {
    spinner.fail(`governance.md: ${error.message}`);
    return false;
  }
}

/**
 * Generate project-context.json from detected + user config.
 */
async function generateProjectContext(config) {
  const spinner = ora('Generating project-context.json...').start();

  try {
    const metadata = {
      version: '1.0',
      last_updated: new Date().toISOString(),
      project_name: config.projectName || basename(CWD),
      project_root: '.',
      created_by: 'gaia-init',
      cloud_provider: config.cloudProvider,
      environment: 'non-prod',
      primary_region: config.region
    };

    // Cloud-specific metadata
    if (['gcp', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      metadata.project_id = config.projectId;
    }
    if (['aws', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      metadata.aws_account = config.projectId;
    }

    const projectDetails = {
      region: config.region,
      environment: 'non-prod',
      cluster_name: config.clusterName,
      cloud_provider: config.cloudProvider
    };

    if (config.cloudProvider === 'gcp' || config.cloudProvider === 'multi-cloud') {
      projectDetails.project_id = config.projectId;
    }
    if (config.cloudProvider === 'aws' || config.cloudProvider === 'multi-cloud') {
      projectDetails.account_id = config.projectId;
    }
    if (config.gitPlatform) {
      projectDetails.git_platform = config.gitPlatform;
    }
    if (config.ciPlatform) {
      projectDetails.ci_platform = config.ciPlatform;
    }
    projectDetails.speckit_root = config.speckitRoot || '.claude/project-context/speckit-project-specs';

    // Build provider credentials
    const providerCredentials = {};
    if (['gcp', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      providerCredentials.gcp = { project: config.projectId, region: config.region };
    }
    if (['aws', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      providerCredentials.aws = { account_id: config.projectId, region: config.region };
    }

    // Detect gitops platform from files
    const gitopsPlatform = await detectGitopsPlatform(config.gitops);

    const projectContext = {
      metadata,
      paths: {
        gitops: config.gitops,
        terraform: config.terraform,
        app_services: config.appServices
      },
      sections: {
        project_details: projectDetails,
        terraform_infrastructure: {
          layout: {
            base_path: config.terraform,
            module_structure: 'terragrunt'
          },
          provider_credentials: providerCredentials
        },
        gitops_configuration: {
          repository: {
            path: config.gitops,
            platform: gitopsPlatform
          }
        },
        application_services: {
          base_path: config.appServices,
          services: []
        },
        operational_guidelines: {
          commit_standards: {
            format: 'conventional_commits',
            validation_required: true,
            config_path: '.claude/config/git_standards.json'
          }
        }
      }
    };

    // Enrich sections from contract file (progressive context enrichment)
    try {
      const provider = config.cloudProvider === 'multi-cloud' ? 'gcp' : config.cloudProvider;
      const contractPath = join(PACKAGE_ROOT, 'config', `context-contracts.${provider}.json`);
      if (existsSync(contractPath)) {
        const contracts = JSON.parse(await fs.readFile(contractPath, 'utf-8'));
        const contractSections = new Set();
        for (const agent of Object.values(contracts.agents || {})) {
          for (const s of (agent.read || [])) contractSections.add(s);
          for (const s of (agent.write || [])) contractSections.add(s);
        }
        for (const section of contractSections) {
          if (!projectContext.sections[section]) {
            projectContext.sections[section] = {};
          }
        }
      }
    } catch (err) {
      // Fall back to hardcoded sections — contract file not required
    }

    const destPath = join(CWD, '.claude', 'project-context', 'project-context.json');

    if (!existsSync(destPath)) {
      // First-time install: write the full generated context
      await fs.writeFile(destPath, JSON.stringify(projectContext, null, 2), 'utf-8');
      spinner.succeed('project-context.json generated');
    } else {
      // File already exists — merge to preserve agent-enriched sections
      let existing;
      try {
        existing = JSON.parse(await fs.readFile(destPath, 'utf-8'));
      } catch {
        // Existing file is corrupt — overwrite safely
        await fs.writeFile(destPath, JSON.stringify(projectContext, null, 2), 'utf-8');
        spinner.succeed('project-context.json regenerated (previous file was invalid)');
        return;
      }

      // Merge strategy:
      // - metadata.*  : replace field-by-field from new scan (always reflects current state)
      // - paths.*     : replace field-by-field from new scan (filesystem-derived, always current)
      // - sections.*  : preserve existing content; add new sections from scan if absent
      const merged = {
        metadata: {
          ...existing.metadata,
          ...projectContext.metadata,
          last_updated: new Date().toISOString()
        },
        paths: {
          ...existing.paths,
          ...projectContext.paths
        },
        sections: {
          // Start from new scan's sections as the schema base,
          // then override with existing sections that have content
          ...projectContext.sections,
          ...Object.fromEntries(
            Object.entries(existing.sections || {}).filter(([, v]) => {
              // Preserve existing section if it has any content
              return v !== null && typeof v === 'object' && Object.keys(v).length > 0;
            })
          )
        }
      };

      await fs.writeFile(destPath, JSON.stringify(merged, null, 2), 'utf-8');
      spinner.succeed('project-context.json updated (metadata + paths synced, sections preserved)');
    }
  } catch (error) {
    spinner.fail('Failed to generate project-context.json');
    throw error;
  }
}

// ensureProjectDirs removed: gaia-init detects existing directories, it does not create them.

/**
 * Clone project context repo if provided.
 */
async function cloneProjectContextRepo(repoUrl) {
  if (!repoUrl || repoUrl.trim() === '') return;

  const sanitized = sanitizeGitUrl(repoUrl);
  const spinner = ora('Cloning project context repository...').start();

  try {
    const contextDir = join(CWD, '.claude', 'project-context');
    const tempDir = `${contextDir}-temp`;

    if (existsSync(tempDir)) await fs.rm(tempDir, { recursive: true, force: true });

    await execAsync(`git clone ${sanitized} ${tempDir}`, { timeout: 30000 });

    // Move contents to project-context/
    const files = await fs.readdir(tempDir);
    for (const file of files) {
      await fs.rename(join(tempDir, file), join(contextDir, file));
    }
    await fs.rm(tempDir, { recursive: true, force: true });

    spinner.succeed('Project context cloned');
  } catch (error) {
    spinner.fail(`Failed to clone: ${error.message}`);
    console.log(chalk.gray(`  Clone manually: cd .claude && git clone ${sanitized} project-context\n`));
  }
}

// ============================================================================
// Verification (inline gaia-doctor)
// ============================================================================

/**
 * Run post-install verification checks.
 */
async function runVerification() {
  console.log(chalk.cyan('\n  Verifying installation...\n'));

  const checks = [
    { name: 'Symlinks', fn: checkSymlinks },
    { name: 'CLAUDE.md', fn: checkClaudeMd },
    { name: 'settings.json', fn: checkSettingsJson },
    { name: 'project-context', fn: checkProjectContext },
    { name: 'Python', fn: checkPython },
    { name: 'Hooks', fn: checkHooks }
  ];

  let allPassed = true;

  for (const { name, fn } of checks) {
    try {
      const result = await fn();
      if (result.ok) {
        console.log(chalk.green(`    ✓ ${name.padEnd(18)} ${result.detail || ''}`));
      } else {
        console.log(chalk.yellow(`    ⚠ ${name.padEnd(18)} ${result.detail}`));
        if (result.fix) console.log(chalk.gray(`      Fix: ${result.fix}`));
        allPassed = false;
      }
    } catch {
      console.log(chalk.red(`    ✗ ${name.padEnd(18)} Check failed`));
      allPassed = false;
    }
  }

  console.log('');
  return allPassed;
}

async function checkSymlinks() {
  const names = ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'speckit', 'CHANGELOG.md'];
  let valid = 0;
  for (const name of names) {
    if (existsSync(join(CWD, '.claude', name))) valid++;
  }
  return { ok: valid === names.length, detail: `${valid}/${names.length} valid` };
}

async function checkClaudeMd() {
  const path = join(CWD, 'CLAUDE.md');
  if (!existsSync(path)) return { ok: false, detail: 'Missing', fix: 'Run gaia-init' };
  const content = await fs.readFile(path, 'utf-8');
  if (content.includes('{{')) return { ok: false, detail: 'Contains raw placeholders', fix: 'Run gaia-init to regenerate' };
  return { ok: true, detail: 'Valid' };
}

async function checkSettingsJson() {
  const path = join(CWD, '.claude', 'settings.json');
  if (!existsSync(path)) return { ok: false, detail: 'Missing', fix: 'Run gaia-init' };
  try {
    JSON.parse(await fs.readFile(path, 'utf-8'));
    return { ok: true, detail: 'Valid JSON' };
  } catch {
    return { ok: false, detail: 'Invalid JSON', fix: 'Delete and run gaia-init' };
  }
}

async function checkProjectContext() {
  const path = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (!existsSync(path)) return { ok: false, detail: 'Missing', fix: 'Run gaia-init or /speckit.init' };
  try {
    const data = JSON.parse(await fs.readFile(path, 'utf-8'));
    const sections = Object.keys(data.sections || {}).length;
    return { ok: sections >= 3, detail: `${sections} sections` };
  } catch {
    return { ok: false, detail: 'Invalid JSON', fix: 'Regenerate with /speckit.init' };
  }
}

async function checkPython() {
  try {
    const { stdout } = await execAsync('python3 --version');
    const version = stdout.trim();
    return { ok: true, detail: version };
  } catch {
    return { ok: false, detail: 'Not found', fix: 'Install Python 3.9+' };
  }
}

async function checkHooks() {
  const hookPath = join(CWD, '.claude', 'hooks', 'pre_tool_use.py');
  if (!existsSync(hookPath)) return { ok: false, detail: 'pre_tool_use.py missing' };
  return { ok: true, detail: 'pre_tool_use.py found' };
}

// ============================================================================
// Utility functions
// ============================================================================

/**
 * Find subdirectories of CWD that contain .git (they are repos).
 * Returns array of {name, path} where path is the absolute path.
 */
async function findSubdirRepos() {
  const repos = [];
  try {
    const entries = await fs.readdir(CWD, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith('.') || entry.name === 'node_modules') continue;
      const subDir = join(CWD, entry.name);
      if (existsSync(join(subDir, '.git'))) {
        repos.push({ name: entry.name, path: subDir });
      }
    }
  } catch { /* skip */ }
  return repos;
}

/**
 * Find the terraform directory, checking CWD then subdirectories with .git.
 * Returns absolute path or null.
 */
async function findTerraformDir() {
  const candidates = ['terraform', 'tf', 'infrastructure', 'iac', 'infra'];

  // Check CWD first
  for (const c of candidates) {
    const p = join(CWD, c);
    if (existsSync(p)) return p;
  }

  // Check subdirectories that have .git (repos)
  const repos = await findSubdirRepos();
  for (const repo of repos) {
    for (const c of candidates) {
      const p = join(repo.path, c);
      if (existsSync(p)) return p;
    }
  }

  return null;
}

/**
 * Find the gitops directory, checking CWD then subdirectories with .git.
 * Returns absolute path or null.
 */
async function findGitopsDir() {
  const candidates = ['gitops', 'k8s', 'kubernetes', 'manifests', 'deployments'];

  // Check CWD first
  for (const c of candidates) {
    const p = join(CWD, c);
    if (existsSync(p)) return p;
  }

  // Check subdirectories that have .git (repos)
  const repos = await findSubdirRepos();
  for (const repo of repos) {
    for (const c of candidates) {
      const p = join(repo.path, c);
      if (existsSync(p)) return p;
    }
  }

  return null;
}

/**
 * Convert an absolute path to a CWD-relative path prefixed with ./
 */
function toRelativePath(absPath) {
  if (!absPath) return null;
  const rel = relative(CWD, absPath);
  if (!rel) return '.';
  return rel.startsWith('.') ? rel : `./${rel}`;
}

async function getGitRemote(dir) {
  try {
    const { stdout } = await execAsync('git remote get-url origin', { cwd: dir, timeout: 5000 });
    return stdout.trim();
  } catch {
    return null;
  }
}

async function findFiles(dir, extension, maxDepth, currentDepth = 0) {
  if (currentDepth >= maxDepth) return [];

  const results = [];
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
      const fullPath = join(dir, entry.name);
      if (entry.isDirectory()) {
        const nested = await findFiles(fullPath, extension, maxDepth, currentDepth + 1);
        results.push(...nested);
      } else if (entry.name.endsWith(extension)) {
        results.push(fullPath);
      }
    }
  } catch {
    // Skip unreadable directories
  }
  return results;
}

async function detectGitopsPlatform(gitopsPath) {
  if (!gitopsPath) return 'flux';
  const absPath = isAbsolute(gitopsPath) ? gitopsPath : resolve(CWD, gitopsPath);
  if (!existsSync(absPath)) return 'flux';

  try {
    const files = await findFiles(absPath, '.yaml', 3);
    for (const file of files) {
      try {
        const content = await fs.readFile(file, 'utf-8');
        if (content.includes('kind: Kustomization') && content.includes('toolkit.fluxcd.io')) return 'flux';
        if (content.includes('kind: Application') && content.includes('argoproj.io')) return 'argocd';
      } catch {
        continue;
      }
    }
  } catch {
    // Default
  }
  return 'flux';
}

function sanitizeGitUrl(input) {
  if (!input || typeof input !== 'string') return '';
  let sanitized = input.trim();
  if (sanitized.toLowerCase().startsWith('git clone ')) {
    sanitized = sanitized.substring('git clone '.length).trim();
  }
  return sanitized.replace(/^["']|["']$/g, '');
}

// ============================================================================
// CLI Argument Parsing
// ============================================================================

function parseCliArguments() {
  return yargs(hideBin(process.argv))
    .usage('Usage: $0 [options]')
    .option('non-interactive', { alias: 'y', type: 'boolean', description: 'Accept detected values', default: false })
    .option('gitops', { type: 'string', description: 'GitOps directory path' })
    .option('terraform', { type: 'string', description: 'Terraform directory path' })
    .option('app-services', { type: 'string', description: 'App services directory path' })
    .option('project-id', { type: 'string', description: 'Cloud project/account ID' })
    .option('region', { type: 'string', description: 'Primary region' })
    .option('cluster', { type: 'string', description: 'Cluster name' })
    .option('skip-claude-install', { type: 'boolean', description: 'Skip Claude Code install', default: false })
    .option('project-context-repo', { type: 'string', description: 'Git repo with project context' })
    .help('h').alias('h', 'help')
    .version('2.0.0').alias('v', 'version')
    .parse();
}

// ============================================================================
// Main
// ============================================================================

async function main() {
  try {
    const args = parseCliArguments();

    // Phase 1: Scan
    const scan = await runScan();

    // Build config from scan + overrides
    let config = buildConfig(scan, args);

    if (args.nonInteractive) {
      // Non-interactive: show what was detected and proceed
      console.log(chalk.cyan('\n  Configuration (auto-detected + overrides):\n'));
      console.log(chalk.gray(`    GitOps:       ${config.gitops || '(not detected)'}`));
      console.log(chalk.gray(`    Terraform:    ${config.terraform || '(not detected)'}`));
      console.log(chalk.gray(`    App Services: ${config.appServices || '(not detected)'}`));
      console.log(chalk.gray(`    Cloud:        ${config.cloudProvider?.toUpperCase()}`));
      console.log(chalk.gray(`    Project ID:   ${config.projectId || '(none)'}`));
      console.log(chalk.gray(`    Region:       ${config.region || '(none)'}`));
      console.log(chalk.gray(`    Cluster:      ${config.clusterName || '(none)'}`));
      console.log(chalk.gray(`    Project Name: ${config.projectName || '(none)'}`));
      console.log(chalk.gray(`    Git Platform: ${config.gitPlatform || '(none)'}`));
      console.log(chalk.gray(`    CI/CD:        ${config.ciPlatform || '(none)'}\n`));
    } else {
      // Phase 2: Display + Confirm
      const { gaps } = await displayAndConfirm(config);

      // Phase 3: Fill gaps + get confirmation
      config = await confirmOrEdit(config, gaps);
    }

    // Phase 4: Install
    console.log(chalk.cyan('\n  Installing...\n'));

    // 4.1 Claude Code (mandatory)
    await ensureClaudeCode(config.claudeCode, args.skipClaudeInstall);

    // 4.2 npm package
    await ensureGaiaOpsPackage();

    // 4.3 (removed) — gaia-init detects directories, does not create them

    // 4.4 .claude/ directory with symlinks
    await createClaudeDirectory();

    // 4.5 Static CLAUDE.md
    await copyClaudeMd();

    // 4.6 Settings.json
    await copySettingsJson();

    // 4.6.1 Git commit-msg hook (strips Claude Code footers at git level)
    await installGitHooks();

    // 4.7 Project context
    await generateProjectContext(config);

    // 4.8 Governance.md (always sync from project-context)
    const speckitRoot = config.speckitRoot || '.claude/project-context/speckit-project-specs';
    await generateGovernanceMd(config, speckitRoot);

    // 4.9 Clone context repo (optional)
    if (config.projectContextRepo) {
      await cloneProjectContextRepo(config.projectContextRepo);
    }

    // Verification
    const healthy = await runVerification();

    // Success
    console.log(chalk.green.bold('  ✓ Installation complete!\n'));

    if (!healthy) {
      console.log(chalk.yellow('  Some checks have warnings. Run `npx gaia-doctor` for details.\n'));
    }

    console.log(chalk.gray('  Next steps:'));
    console.log(chalk.gray('    1. Start Claude Code: claude'));
    console.log(chalk.gray('    2. Enrich context:    /speckit.init'));
    console.log(chalk.gray('    3. Verify health:     npx gaia-doctor\n'));

  } catch (error) {
    console.error(chalk.red(`\n  ✗ Installation failed: ${error.message}\n`));
    process.exit(1);
  }
}

main();
