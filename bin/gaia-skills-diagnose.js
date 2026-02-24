#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Skills and Injection Diagnostic CLI
 *
 * Validates:
 * 1. Skills catalog integrity (SKILL.md + frontmatter + content quality)
 * 2. Agent -> skill wiring (frontmatter skills references)
 * 3. Injection wiring (hooks + settings + runtime paths)
 * 4. Known gap patterns (legacy tests vs current implementation)
 *
 * Usage:
 *   npx gaia-skills-diagnose
 *   npx gaia-skills-diagnose --run-tests
 *   npx gaia-skills-diagnose --json
 *   npx gaia-skills-diagnose --strict
 */

import fs from "fs";
import path from "path";
import { spawnSync } from "child_process";
import { fileURLToPath } from "url";
import chalk from "chalk";
import yargs from "yargs";
import { hideBin } from "yargs/helpers";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PACKAGE_ROOT = path.resolve(__dirname, "..");

const META_AGENTS = new Set(["gaia", "Explore", "Plan"]);
const CONTEXT_INJECTED_AGENTS = new Set([
  "terraform-architect",
  "gitops-operator",
  "cloud-troubleshooter",
  "devops-developer",
]);
const REQUIRED_PROJECT_SKILLS = ["agent-protocol", "context-updater"];

const SEVERITY_WEIGHT = {
  critical: 25,
  high: 12,
  medium: 5,
  info: 1,
};

function parseFrontmatter(markdown) {
  if (!markdown.startsWith("---")) return {};
  const endIdx = markdown.indexOf("\n---", 3);
  if (endIdx === -1) return {};

  const block = markdown.slice(3, endIdx).trim();
  const result = {};
  let currentKey = null;
  let currentList = null;

  for (const rawLine of block.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;

    if (line.startsWith("- ") && currentKey && Array.isArray(currentList)) {
      currentList.push(line.slice(2).trim());
      continue;
    }

    if (line.includes(":")) {
      if (currentKey && Array.isArray(currentList)) {
        result[currentKey] = currentList;
      }

      const [keyPart, ...rest] = line.split(":");
      const key = keyPart.trim();
      const value = rest.join(":").trim();

      if (value) {
        result[key] = value;
        currentKey = key;
        currentList = null;
      } else {
        currentKey = key;
        currentList = [];
      }
      continue;
    }

    if (currentKey && Array.isArray(currentList)) {
      result[currentKey] = currentList;
      currentKey = null;
      currentList = null;
    }
  }

  if (currentKey && Array.isArray(currentList)) {
    result[currentKey] = currentList;
  }

  return result;
}

function stripFrontmatter(markdown) {
  if (!markdown.startsWith("---")) return markdown.trim();
  const endIdx = markdown.indexOf("\n---", 3);
  if (endIdx === -1) return markdown.trim();
  return markdown.slice(endIdx + 4).trim();
}

function readText(filePath) {
  return fs.readFileSync(filePath, "utf-8");
}

function exists(filePath) {
  return fs.existsSync(filePath);
}

function listDirectories(dir) {
  if (!exists(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && !d.name.startsWith(".") && d.name !== "__pycache__")
    .map((d) => d.name);
}

function listMarkdownFiles(dir) {
  if (!exists(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isFile() && d.name.endsWith(".md") && !d.name.toUpperCase().includes("README"))
    .map((d) => path.join(dir, d.name));
}

function collectFilesRecursively(root, matcher) {
  const files = [];
  if (!exists(root)) return files;

  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });

    for (const entry of entries) {
      if (entry.name.startsWith(".") || entry.name === "__pycache__") continue;
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(absolute);
      } else if (matcher(absolute)) {
        files.push(absolute);
      }
    }
  }

  return files;
}

function addFinding(findings, finding) {
  findings.push({
    severity: finding.severity,
    code: finding.code,
    title: finding.title,
    detail: finding.detail || "",
    evidence: finding.evidence || "",
    remediation: finding.remediation || "",
  });
}

function resolveRuntimePaths(projectRoot) {
  const claudeDir = path.join(projectRoot, ".claude");
  const inProject = exists(claudeDir);

  const runtime = {
    projectRoot,
    packageRoot: PACKAGE_ROOT,
    claudeDir,
    inProject,
    agentsDir: inProject && exists(path.join(claudeDir, "agents"))
      ? path.join(claudeDir, "agents")
      : path.join(PACKAGE_ROOT, "agents"),
    skillsDir: inProject && exists(path.join(claudeDir, "skills"))
      ? path.join(claudeDir, "skills")
      : path.join(PACKAGE_ROOT, "skills"),
    hooksDir: inProject && exists(path.join(claudeDir, "hooks"))
      ? path.join(claudeDir, "hooks")
      : path.join(PACKAGE_ROOT, "hooks"),
    settingsPath: path.join(claudeDir, "settings.json"),
    testsDir: path.join(PACKAGE_ROOT, "tests"),
  };

  runtime.preToolUsePath = exists(path.join(runtime.hooksDir, "pre_tool_use.py"))
    ? path.join(runtime.hooksDir, "pre_tool_use.py")
    : null;

  runtime.taskValidatorPath = path.join(PACKAGE_ROOT, "hooks", "modules", "tools", "task_validator.py");
  runtime.rootClaudeMd = path.join(PACKAGE_ROOT, "CLAUDE.md");

  return runtime;
}

function validateSkillsCatalog(ctx, findings, checks) {
  const skillsDir = ctx.skillsDir;
  if (!exists(skillsDir)) {
    addFinding(findings, {
      severity: "critical",
      code: "SKILLS_DIR_MISSING",
      title: "Skills directory not found",
      detail: "No runtime skills directory was found.",
      evidence: skillsDir,
      remediation: "Ensure .claude/skills is linked or package skills/ exists.",
    });
    checks.push({ name: "skills-catalog", ok: false, detail: "skills directory missing" });
    return {
      skillNames: new Set(),
      skillBodies: new Map(),
      readmeContent: "",
    };
  }

  const skillDirs = listDirectories(skillsDir);
  const skillNames = new Set();
  const skillBodies = new Map();
  let validSkills = 0;

  for (const skillName of skillDirs) {
    skillNames.add(skillName);
    const skillMd = path.join(skillsDir, skillName, "SKILL.md");

    if (!exists(skillMd)) {
      addFinding(findings, {
        severity: "critical",
        code: "SKILL_MD_MISSING",
        title: "Skill directory missing SKILL.md",
        detail: `Skill '${skillName}' does not contain SKILL.md.`,
        evidence: skillMd,
        remediation: "Create SKILL.md in each skill directory.",
      });
      continue;
    }

    const content = readText(skillMd);
    const fm = parseFrontmatter(content);
    const stripped = stripFrontmatter(content);
    skillBodies.set(skillName, content);

    if (Object.keys(fm).length === 0) {
      addFinding(findings, {
        severity: "high",
        code: "SKILL_FRONTMATTER_MISSING",
        title: "Skill without frontmatter",
        detail: `Skill '${skillName}' has no parseable frontmatter.`,
        evidence: skillMd,
        remediation: "Add YAML frontmatter with at least name and description.",
      });
    } else {
      if (fm.name !== skillName) {
        addFinding(findings, {
          severity: "high",
          code: "SKILL_NAME_MISMATCH",
          title: "Skill frontmatter name mismatch",
          detail: `Frontmatter name '${fm.name || "undefined"}' differs from directory '${skillName}'.`,
          evidence: skillMd,
          remediation: "Align frontmatter name with the directory name.",
        });
      }

      if (!fm.description || String(fm.description).trim().length < 10) {
        addFinding(findings, {
          severity: "medium",
          code: "SKILL_DESCRIPTION_WEAK",
          title: "Skill description missing or too short",
          detail: `Skill '${skillName}' should have a meaningful description.`,
          evidence: skillMd,
          remediation: "Set description in frontmatter with >10 characters.",
        });
      }
    }

    if (stripped.length < 100) {
      addFinding(findings, {
        severity: "medium",
        code: "SKILL_CONTENT_TOO_SHORT",
        title: "Skill content too short",
        detail: `Skill '${skillName}' content is only ${stripped.length} characters after frontmatter.`,
        evidence: skillMd,
        remediation: "Expand operational instructions in SKILL.md.",
      });
    }

    validSkills += 1;
  }

  const readmePath = path.join(skillsDir, "README.md");
  const readmeContent = exists(readmePath) ? readText(readmePath) : "";

  checks.push({
    name: "skills-catalog",
    ok: skillDirs.length > 0,
    detail: `${validSkills}/${skillDirs.length} skill directories inspected`,
  });

  return { skillNames, skillBodies, readmeContent };
}

function validateAgentSkillWiring(ctx, data, findings, checks) {
  const agentsDir = ctx.agentsDir;
  if (!exists(agentsDir)) {
    addFinding(findings, {
      severity: "critical",
      code: "AGENTS_DIR_MISSING",
      title: "Agents directory not found",
      detail: "No runtime agents directory was found.",
      evidence: agentsDir,
      remediation: "Ensure .claude/agents is linked or package agents/ exists.",
    });
    checks.push({ name: "agent-skill-wiring", ok: false, detail: "agents directory missing" });
    return;
  }

  const agentFiles = listMarkdownFiles(agentsDir);
  if (agentFiles.length === 0) {
    addFinding(findings, {
      severity: "critical",
      code: "AGENT_FILES_MISSING",
      title: "No agent definition files found",
      detail: "Expected at least one agent markdown file.",
      evidence: agentsDir,
      remediation: "Restore agent definition files (*.md).",
    });
    checks.push({ name: "agent-skill-wiring", ok: false, detail: "no agent files found" });
    return;
  }

  const referenced = new Set();
  let checkedAgents = 0;

  for (const agentPath of agentFiles) {
    const content = readText(agentPath);
    const fm = parseFrontmatter(content);
    const agentName = path.basename(agentPath, ".md");
    const isMeta = META_AGENTS.has(agentName);
    const rawSkills = fm.skills;
    const skills = Array.isArray(rawSkills)
      ? rawSkills
      : typeof rawSkills === "string" && rawSkills.trim()
        ? rawSkills.split(",").map((v) => v.trim()).filter(Boolean)
        : [];

    if (Object.keys(fm).length === 0) {
      addFinding(findings, {
        severity: "high",
        code: "AGENT_FRONTMATTER_MISSING",
        title: "Agent missing parseable frontmatter",
        detail: `Agent '${agentName}' has no parseable frontmatter.`,
        evidence: agentPath,
        remediation: "Add YAML frontmatter with name, description, tools, and skills.",
      });
    }

    if (!isMeta && skills.length === 0) {
      addFinding(findings, {
        severity: "high",
        code: "AGENT_SKILLS_EMPTY",
        title: "Project agent missing skills",
        detail: `Agent '${agentName}' has an empty or missing skills list.`,
        evidence: agentPath,
        remediation: "Add required skills in frontmatter to enable runtime injection.",
      });
    }

    for (const skill of skills) {
      referenced.add(skill);
      if (!data.skillNames.has(skill)) {
        addFinding(findings, {
          severity: "critical",
          code: "AGENT_SKILL_NOT_FOUND",
          title: "Agent references non-existent skill",
          detail: `Agent '${agentName}' references missing skill '${skill}'.`,
          evidence: agentPath,
          remediation: "Create the skill directory or fix the frontmatter reference.",
        });
      }
    }

    if (!isMeta && CONTEXT_INJECTED_AGENTS.has(agentName)) {
      for (const required of REQUIRED_PROJECT_SKILLS) {
        if (!skills.includes(required)) {
          addFinding(findings, {
            severity: "high",
            code: "AGENT_REQUIRED_SKILL_MISSING",
            title: "Project agent missing required baseline skill",
            detail: `Agent '${agentName}' is missing '${required}'.`,
            evidence: agentPath,
            remediation: `Add '${required}' to the skills list.`,
          });
        }
      }
    }

    for (const skillName of data.skillNames) {
      if (content.includes(`skills/${skillName}`)) {
        referenced.add(skillName);
      }
    }

    checkedAgents += 1;
  }

  for (const [skillName, skillBody] of data.skillBodies.entries()) {
    for (const otherSkill of data.skillNames) {
      if (skillName === otherSkill) continue;
      if (skillBody.includes(`skills/${otherSkill}`) || skillBody.includes(`/${otherSkill}/`)) {
        referenced.add(otherSkill);
      }
    }
  }

  for (const skillName of data.skillNames) {
    if (data.readmeContent.includes(skillName)) {
      referenced.add(skillName);
    }
  }

  const orphanSkills = [...data.skillNames].filter((s) => !referenced.has(s));
  if (orphanSkills.length > 0) {
    addFinding(findings, {
      severity: "medium",
      code: "ORPHAN_SKILLS",
      title: "Skills not referenced by any agent or skill",
      detail: `Found ${orphanSkills.length} orphan skills.`,
      evidence: orphanSkills.join(", "),
      remediation: "Reference these skills from agents/skills README or remove deprecated skills.",
    });
  }

  checks.push({
    name: "agent-skill-wiring",
    ok: checkedAgents > 0,
    detail: `${checkedAgents} agents scanned, ${referenced.size} referenced skills`,
  });
}

function hookMatcherContains(hooksArray, matcher, fragment) {
  if (!Array.isArray(hooksArray)) return false;
  for (const entry of hooksArray) {
    if (!entry || entry.matcher !== matcher || !Array.isArray(entry.hooks)) continue;
    for (const hook of entry.hooks) {
      if (hook?.type === "command" && String(hook.command || "").includes(fragment)) {
        return true;
      }
    }
  }
  return false;
}

function validateInjectionWiring(ctx, findings, checks) {
  let ok = true;
  const preToolUsePath = ctx.preToolUsePath;

  if (!preToolUsePath) {
    addFinding(findings, {
      severity: "critical",
      code: "PRE_TOOL_USE_MISSING",
      title: "pre_tool_use hook not found",
      detail: "Task context injection hook is missing.",
      evidence: path.join(ctx.hooksDir, "pre_tool_use.py"),
      remediation: "Restore pre_tool_use.py in hooks directory.",
    });
    ok = false;
  } else {
    const content = readText(preToolUsePath);

    if (!content.includes("def _inject_project_context")) {
      addFinding(findings, {
        severity: "high",
        code: "CONTEXT_INJECTION_FUNCTION_MISSING",
        title: "Context injection function missing",
        detail: "_inject_project_context() not found in pre_tool_use.py.",
        evidence: preToolUsePath,
        remediation: "Reintroduce or repair context injection handler.",
      });
      ok = false;
    }

    if (!content.includes("updatedInput")) {
      addFinding(findings, {
        severity: "high",
        code: "UPDATED_INPUT_NOT_EMITTED",
        title: "Hook may not return updated input",
        detail: "updatedInput marker not found in pre_tool_use.py.",
        evidence: preToolUsePath,
        remediation: "Ensure modified Task prompts are returned with updatedInput.",
      });
      ok = false;
    }

    if (!content.includes("skills:' field in the agent's frontmatter")) {
      addFinding(findings, {
        severity: "info",
        code: "SKILLS_INJECTION_DOC_MISSING",
        title: "No explicit inline note about native skills injection",
        detail: "Could not find an explicit comment clarifying frontmatter-based skill injection.",
        evidence: preToolUsePath,
        remediation: "Keep an inline note to reduce ambiguity for future maintainers.",
      });
    }
  }

  if (ctx.inProject) {
    for (const name of ["agents", "skills", "hooks"]) {
      const target = path.join(ctx.claudeDir, name);
      if (!exists(target)) {
        addFinding(findings, {
          severity: "high",
          code: "RUNTIME_DIR_MISSING",
          title: "Missing runtime .claude directory",
          detail: `Missing .claude/${name}, runtime injection may fail.`,
          evidence: target,
          remediation: "Run gaia-init to recreate runtime links/directories.",
        });
        ok = false;
      }
    }
  }

  if (exists(ctx.settingsPath)) {
    try {
      const settings = JSON.parse(readText(ctx.settingsPath));

      if (!hookMatcherContains(settings?.hooks?.PreToolUse, "Task", "pre_tool_use.py")) {
        addFinding(findings, {
          severity: "high",
          code: "SETTINGS_PRE_TOOL_TASK_HOOK_MISSING",
          title: "Task PreToolUse hook not configured",
          detail: "settings.json lacks Task -> pre_tool_use.py mapping.",
          evidence: ctx.settingsPath,
          remediation: "Add PreToolUse matcher for Task using .claude/hooks/pre_tool_use.py.",
        });
        ok = false;
      }

      if (!hookMatcherContains(settings?.hooks?.PostToolUse, "Bash", "post_tool_use.py")) {
        addFinding(findings, {
          severity: "medium",
          code: "SETTINGS_POST_TOOL_BASH_HOOK_MISSING",
          title: "Bash PostToolUse hook not configured",
          detail: "settings.json lacks Bash -> post_tool_use.py mapping.",
          evidence: ctx.settingsPath,
          remediation: "Add PostToolUse matcher for Bash using .claude/hooks/post_tool_use.py.",
        });
      }

      if (!hookMatcherContains(settings?.hooks?.SubagentStop, "*", "subagent_stop.py")) {
        addFinding(findings, {
          severity: "medium",
          code: "SETTINGS_SUBAGENT_STOP_HOOK_MISSING",
          title: "SubagentStop hook not configured",
          detail: "settings.json lacks SubagentStop -> subagent_stop.py mapping.",
          evidence: ctx.settingsPath,
          remediation: "Add SubagentStop hook in settings.json.",
        });
      }
    } catch (error) {
      addFinding(findings, {
        severity: "high",
        code: "SETTINGS_JSON_INVALID",
        title: "Invalid settings.json",
        detail: `Failed to parse settings.json: ${error.message}`,
        evidence: ctx.settingsPath,
        remediation: "Fix JSON syntax or regenerate settings via gaia-init.",
      });
      ok = false;
    }
  } else {
    addFinding(findings, {
      severity: ctx.inProject ? "high" : "info",
      code: "SETTINGS_JSON_NOT_FOUND",
      title: "settings.json not found",
      detail: ctx.inProject
        ? "Project appears installed but .claude/settings.json is missing."
        : "Running in package mode; runtime settings validation skipped.",
      evidence: ctx.settingsPath,
      remediation: "Generate settings with gaia-init in project context.",
    });
    if (ctx.inProject) ok = false;
  }

  checks.push({
    name: "injection-wiring",
    ok,
    detail: preToolUsePath
      ? `using ${path.relative(ctx.projectRoot, preToolUsePath)}`
      : "missing pre_tool_use.py",
  });
}

function validateRoutingContract(ctx, findings, checks) {
  const agentsOnDisk = new Set(
    listMarkdownFiles(ctx.agentsDir).map((file) => path.basename(file, ".md"))
  );
  const validatorPath = ctx.taskValidatorPath;

  if (!exists(validatorPath)) {
    checks.push({
      name: "routing-contract",
      ok: true,
      detail: "task_validator.py not found; skipped",
    });
    return;
  }

  const content = readText(validatorPath);
  const match = content.match(/AVAILABLE_AGENTS\s*=\s*\[(.*?)\]/s);
  if (!match) {
    addFinding(findings, {
      severity: "medium",
      code: "AVAILABLE_AGENTS_PARSE_FAILED",
      title: "Could not parse AVAILABLE_AGENTS",
      detail: "Routing contract check skipped.",
      evidence: validatorPath,
      remediation: "Keep AVAILABLE_AGENTS as a simple static list for validation tooling.",
    });
    checks.push({ name: "routing-contract", ok: false, detail: "could not parse AVAILABLE_AGENTS" });
    return;
  }

  const availableAgents = new Set(
    [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1])
  );

  const missingOnDisk = [...availableAgents].filter((agent) => !META_AGENTS.has(agent) && !agentsOnDisk.has(agent));
  const missingInValidator = [...agentsOnDisk].filter((agent) => !availableAgents.has(agent));

  if (missingOnDisk.length > 0) {
    addFinding(findings, {
      severity: "high",
      code: "AVAILABLE_AGENT_MISSING_FILE",
      title: "AVAILABLE_AGENTS entry has no agent file",
      detail: `${missingOnDisk.length} agent names are not present on disk.`,
      evidence: missingOnDisk.join(", "),
      remediation: "Create missing agent files or remove stale names from AVAILABLE_AGENTS.",
    });
  }

  if (missingInValidator.length > 0) {
    addFinding(findings, {
      severity: "high",
      code: "AGENT_FILE_NOT_IN_AVAILABLE_AGENTS",
      title: "Agent file not listed in AVAILABLE_AGENTS",
      detail: `${missingInValidator.length} agent files are not routable by validator.`,
      evidence: missingInValidator.join(", "),
      remediation: "Add these agents to AVAILABLE_AGENTS or remove deprecated files.",
    });
  }

  checks.push({
    name: "routing-contract",
    ok: missingOnDisk.length === 0 && missingInValidator.length === 0,
    detail: `${availableAgents.size} available agents vs ${agentsOnDisk.size} agent files`,
  });
}

function detectLegacyGapPatterns(ctx, findings, checks) {
  const testsDir = ctx.testsDir;
  if (!exists(testsDir)) {
    checks.push({ name: "gap-patterns", ok: true, detail: "tests directory not found; skipped" });
    return;
  }

  const preToolUse = ctx.preToolUsePath && exists(ctx.preToolUsePath) ? readText(ctx.preToolUsePath) : "";
  const hasRuntimeLoadFn = preToolUse.includes("def _load_agent_skills");
  const testFiles = collectFilesRecursively(testsDir, (f) => f.endsWith(".py"));

  const legacyRefs = [];
  for (const file of testFiles) {
    const content = readText(file);
    if (content.includes("_load_agent_skills(")) {
      legacyRefs.push(path.relative(PACKAGE_ROOT, file));
    }
  }

  if (legacyRefs.length > 0 && !hasRuntimeLoadFn) {
    addFinding(findings, {
      severity: "critical",
      code: "LEGACY_TEST_EXPECTS_LOAD_AGENT_SKILLS",
      title: "Tests still expect deprecated _load_agent_skills API",
      detail: `${legacyRefs.length} test file(s) reference _load_agent_skills(), but runtime hook does not expose it.`,
      evidence: legacyRefs.join(", "),
      remediation: "Update tests to validate frontmatter-based native skills injection instead of hook-based loading.",
    });
  }

  const conftestPath = path.join(testsDir, "conftest.py");
  if (exists(conftestPath)) {
    const content = readText(conftestPath);
    const expectsRootClaudeMd = content.includes('(package_root / "CLAUDE.md").read_text()');
    const hasRootClaudeMd = exists(ctx.rootClaudeMd);

    if (expectsRootClaudeMd && !hasRootClaudeMd) {
      addFinding(findings, {
        severity: "high",
        code: "TEST_EXPECTS_MISSING_CLAUDE_MD",
        title: "Tests require package-root CLAUDE.md that does not exist",
        detail: "tests/conftest.py fixture reads package_root/CLAUDE.md, causing routing-table tests to error.",
        evidence: `${path.relative(PACKAGE_ROOT, conftestPath)} + missing ${path.relative(PACKAGE_ROOT, ctx.rootClaudeMd)}`,
        remediation: "Either include CLAUDE.md in package root or make fixture robust to installed-project layout.",
      });
    }
  }

  checks.push({
    name: "gap-patterns",
    ok: true,
    detail: `${legacyRefs.length} legacy pattern reference(s) scanned`,
  });
}

function runTestProbe(ctx, findings, checks) {
  if (!exists(ctx.testsDir)) {
    checks.push({ name: "test-probe", ok: true, detail: "tests directory missing; skipped" });
    return;
  }

  const args = [
    "-m",
    "pytest",
    "-q",
    "tests/layer1_prompt_regression/test_skills_cross_reference.py",
    "tests/layer1_prompt_regression/test_agent_frontmatter.py",
    "tests/layer1_prompt_regression/test_agent_prompt_content.py",
    "tests/layer1_prompt_regression/test_routing_table.py",
    "tests/integration/test_subagent_lifecycle.py",
    "-k",
    "Phase1SkillsInjection or load_skills",
  ];

  const res = spawnSync("python3", args, {
    cwd: ctx.packageRoot,
    encoding: "utf-8",
  });

  // In some sandboxed environments spawnSync can return a non-fatal EPERM
  // while still providing a valid exit status and output.
  if (res.error && typeof res.status !== "number") {
    addFinding(findings, {
      severity: "high",
      code: "PYTEST_PROBE_EXEC_FAILED",
      title: "Unable to execute pytest probe",
      detail: res.error.message,
      evidence: `python3 ${args.join(" ")}`,
      remediation: "Install pytest and Python dependencies to run the probe.",
    });
    checks.push({ name: "test-probe", ok: false, detail: "execution failed" });
    return;
  }

  const combined = `${res.stdout || ""}\n${res.stderr || ""}`;
  const status = typeof res.status === "number" ? res.status : 1;
  const failed = status !== 0;

  if (failed) {
    if (combined.includes("has no attribute '_load_agent_skills'")) {
      addFinding(findings, {
        severity: "critical",
        code: "PYTEST_FAILS_ON_LOAD_AGENT_SKILLS",
        title: "Integration tests fail due to removed _load_agent_skills API",
        detail: "test_subagent_lifecycle.py still validates legacy hook behavior.",
        evidence: "AttributeError: module 'pre_tool_use_*' has no attribute '_load_agent_skills'",
        remediation: "Rewrite Phase 1 tests to validate frontmatter-based injection contract.",
      });
    }

    if (combined.includes("No such file or directory") && combined.includes("CLAUDE.md")) {
      addFinding(findings, {
        severity: "high",
        code: "PYTEST_FAILS_ON_MISSING_CLAUDE_MD",
        title: "Routing-table tests fail because package-root CLAUDE.md is missing",
        detail: "tests/conftest.py fixture assumes package_root/CLAUDE.md exists.",
        evidence: "FileNotFoundError: .../gaia-ops/CLAUDE.md",
        remediation: "Provide CLAUDE.md or adjust fixture to locate generated CLAUDE.md in project context.",
      });
    }
  }

  checks.push({
    name: "test-probe",
    ok: !failed,
    detail: failed ? `pytest exited with status ${status}` : "pytest probe passed",
  });
}

function summarizeFindings(findings) {
  const counts = { critical: 0, high: 0, medium: 0, info: 0 };
  let score = 100;

  for (const finding of findings) {
    counts[finding.severity] = (counts[finding.severity] || 0) + 1;
    score -= SEVERITY_WEIGHT[finding.severity] || 0;
  }

  if (score < 0) score = 0;

  let status = "healthy";
  if (counts.critical > 0 || score < 70) {
    status = "at-risk";
  } else if (counts.high > 0 || score < 90) {
    status = "degraded";
  }

  return { counts, score, status };
}

function printHumanReport(report) {
  console.log(chalk.cyan("\n  Gaia-Ops Skills Injection Diagnostic\n"));
  console.log(chalk.gray(`  Project root: ${report.roots.projectRoot}`));
  console.log(chalk.gray(`  Runtime skills: ${report.roots.skillsDir}`));
  console.log(chalk.gray(`  Runtime agents: ${report.roots.agentsDir}\n`));

  for (const check of report.checks) {
    const icon = check.ok ? chalk.green("✓") : chalk.yellow("⚠");
    const detail = check.ok ? chalk.gray(check.detail) : chalk.yellow(check.detail);
    console.log(`    ${icon} ${check.name.padEnd(20)} ${detail}`);
  }

  const { counts, score, status } = report.summary;
  console.log("");
  console.log(`  Score: ${score}/100`);
  console.log(`  Status: ${status.toUpperCase()}`);
  console.log(
    `  Findings: critical=${counts.critical}, high=${counts.high}, medium=${counts.medium}, info=${counts.info}\n`
  );

  if (report.findings.length === 0) {
    console.log(chalk.green("  No gaps detected.\n"));
    return;
  }

  const severityOrder = { critical: 0, high: 1, medium: 2, info: 3 };
  const sorted = [...report.findings].sort(
    (a, b) => severityOrder[a.severity] - severityOrder[b.severity]
  );

  console.log(chalk.cyan("  Gap diagnosis:\n"));
  for (const finding of sorted) {
    const sevColor =
      finding.severity === "critical"
        ? chalk.red.bold
        : finding.severity === "high"
          ? chalk.yellow.bold
          : finding.severity === "medium"
            ? chalk.blue.bold
            : chalk.gray;
    console.log(`  [${sevColor(finding.severity.toUpperCase())}] ${finding.code} - ${finding.title}`);
    if (finding.detail) console.log(`    ${finding.detail}`);
    if (finding.evidence) console.log(chalk.gray(`    Evidence: ${finding.evidence}`));
    if (finding.remediation) console.log(chalk.gray(`    Remediation: ${finding.remediation}`));
  }
  console.log("");
}

function buildReport(args) {
  const ctx = resolveRuntimePaths(path.resolve(args.projectRoot));
  const findings = [];
  const checks = [];

  const skillData = validateSkillsCatalog(ctx, findings, checks);
  validateAgentSkillWiring(ctx, skillData, findings, checks);
  validateInjectionWiring(ctx, findings, checks);
  validateRoutingContract(ctx, findings, checks);
  detectLegacyGapPatterns(ctx, findings, checks);

  if (args.runTests) {
    runTestProbe(ctx, findings, checks);
  }

  const summary = summarizeFindings(findings);
  return {
    generatedAt: new Date().toISOString(),
    roots: {
      projectRoot: ctx.projectRoot,
      packageRoot: ctx.packageRoot,
      skillsDir: ctx.skillsDir,
      agentsDir: ctx.agentsDir,
      hooksDir: ctx.hooksDir,
      settingsPath: ctx.settingsPath,
      preToolUsePath: ctx.preToolUsePath || "",
    },
    checks,
    findings,
    summary,
  };
}

function main() {
  const args = yargs(hideBin(process.argv))
    .usage("Usage: $0 [options]")
    .option("project-root", {
      type: "string",
      default: process.cwd(),
      description: "Project root to inspect (.claude/ expected here in project mode)",
    })
    .option("run-tests", {
      type: "boolean",
      default: false,
      description: "Run focused pytest probe for skills/injection regressions",
    })
    .option("json", {
      type: "boolean",
      default: false,
      description: "Output JSON report",
    })
    .option("strict", {
      type: "boolean",
      default: false,
      description: "Exit non-zero for HIGH findings too (CI mode)",
    })
    .help("h")
    .alias("h", "help")
    .version(false)
    .parse();

  const report = buildReport(args);

  if (args.json) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    printHumanReport(report);
  }

  const { critical, high } = report.summary.counts;
  let exitCode = 0;
  if (critical > 0) {
    exitCode = 2;
  } else if (args.strict && high > 0) {
    exitCode = 1;
  }

  process.exit(exitCode);
}

main();
