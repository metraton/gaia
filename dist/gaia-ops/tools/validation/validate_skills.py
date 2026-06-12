#!/usr/bin/env python3
import os
import re
import yaml
from pathlib import Path
from collections import defaultdict

def find_skills(base_dirs):
    """Encuentra todas las skills en los directorios base."""
    skills = {}
    for base_dir in base_dirs:
        path = Path(base_dir)
        if not path.exists():
            continue
        for skill_file in path.rglob("SKILL.md"):
            skill_name = skill_file.parent.name
            skills[skill_name] = {
                "path": str(skill_file),
                "content": skill_file.read_text(encoding="utf-8", errors="ignore")
            }
    return skills

def validate_skill_format(skills):
    """Valida el formato de cada skill."""
    validation_results = {}
    for name, data in skills.items():
        content = data["content"]
        has_title = bool(re.search(r'^#\s+.+', content, re.MULTILINE))
        validation_results[name] = {
            "has_title": has_title,
            "is_empty": len(content.strip()) == 0,
            "path": data["path"]
        }
    return validation_results

def find_agents(base_dirs):
    """Encuentra las definiciones de los agentes."""
    agents = {}
    for base_dir in base_dirs:
        path = Path(base_dir)
        if not path.exists():
            continue
        for agent_file in path.rglob("*.md"):
            if agent_file.name == "README.md":
                continue
            content = agent_file.read_text(encoding="utf-8", errors="ignore")
            # Extraer frontmatter YAML
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
            if match:
                try:
                    frontmatter = yaml.safe_load(match.group(1))
                    if isinstance(frontmatter, dict) and "name" in frontmatter:
                        agents[frontmatter["name"]] = {
                            "path": str(agent_file),
                            "skills_declared": frontmatter.get("skills", []),
                            "body": match.group(2)
                        }
                except yaml.YAMLError:
                    pass
    return agents

def analyze_injection():
    """Analiza cómo se inyectan las skills (revisando pre_tool_use.py)."""
    hook_path = Path("gaia-ops/hooks/pre_tool_use.py")
    if not hook_path.exists():
        return "No se encontró gaia-ops/hooks/pre_tool_use.py"
    
    content = hook_path.read_text(encoding="utf-8", errors="ignore")
    if "skills are injected natively by Claude Code" in content:
        return "Las skills se inyectan de forma nativa por Claude Code a través del campo 'skills:' en el frontmatter del agente (según pre_tool_use.py)."
    return "Mecanismo de inyección en pre_tool_use.py analizado, pero no se encontró la nota estándar sobre inyección nativa."

def generate_report(skills, validation, agents, injection_info):
    """Genera el reporte en formato Markdown."""
    report = ["# Reporte de Validación de Skills\n"]
    
    report.append("## 1. Análisis de Inyección")
    report.append(f"{injection_info}\n")
    
    report.append(f"## 2. Skills Encontradas ({len(skills)})")
    for name, val in validation.items():
        status = "✅ OK" if val["has_title"] and not val["is_empty"] else "❌ PROBLEMA"
        issues = []
        if not val["has_title"]: issues.append("Falta título (# Título)")
        if val["is_empty"]: issues.append("Archivo vacío")
        issue_str = f" - Detalles: {', '.join(issues)}" if issues else ""
        report.append(f"- **{name}** ({val['path']}): {status}{issue_str}")
    report.append("")
    
    # Analizar uso de skills
    used_skills = defaultdict(list)
    missing_skills = defaultdict(list)
    body_mentions = defaultdict(list)
    
    for agent_name, agent_data in agents.items():
        declared = agent_data["skills_declared"] or []
        body = agent_data["body"]
        for skill in declared:
            if skill in skills:
                used_skills[skill].append(agent_name)
            else:
                missing_skills[agent_name].append(skill)
        
        # Check for skills mentioned in the body but not declared
        for skill in skills:
            if skill not in declared and skill in body:
                body_mentions[agent_name].append(skill)
                
    report.append("## 3. Uso de Skills por Agentes")
    if not agents:
        report.append("No se encontraron definiciones de agentes con frontmatter YAML válido.\n")
    else:
        for agent_name, agent_data in agents.items():
            declared = agent_data["skills_declared"] or []
            mentions = body_mentions[agent_name]
            mention_str = f" (Menciona en texto sin declarar: {', '.join(mentions)})" if mentions else ""
            report.append(f"- **{agent_name}**: {len(declared)} skills declaradas.{mention_str}")
        report.append("")
        
    report.append("## 4. Gaps Identificados")
    
    # Skills no utilizadas
    # Consideramos una skill como utilizada si está declarada o si se menciona explícitamente en el cuerpo
    all_used_skills = set(used_skills.keys())
    for mentions in body_mentions.values():
        all_used_skills.update(mentions)
        
    unused_skills = set(skills.keys()) - all_used_skills
    if unused_skills:
        report.append("### Skills no utilizadas (Huérfanas)")
        for skill in sorted(unused_skills):
            report.append(f"- {skill}")
    else:
        report.append("### Skills no utilizadas (Huérfanas)")
        report.append("- Ninguna. Todas las skills encontradas están asignadas a al menos un agente.")
    report.append("")
    
    # Skills declaradas pero inexistentes
    if missing_skills:
        report.append("### Skills declaradas pero no encontradas (Faltantes)")
        for agent, missing in missing_skills.items():
            for m in missing:
                report.append(f"- El agente **{agent}** declara la skill '{m}', pero no se encontró el archivo SKILL.md correspondiente.")
    else:
        report.append("### Skills declaradas pero no encontradas (Faltantes)")
        report.append("- Ninguna. Todas las skills declaradas por los agentes existen.")
    report.append("")
    
    # Skills mencionadas en el texto pero no inyectadas formalmente
    report.append("### Skills mencionadas en el texto pero NO declaradas en 'skills:'")
    if body_mentions:
        for agent, mentions in body_mentions.items():
            for m in mentions:
                report.append(f"- **{agent}** menciona '{m}' en su cuerpo pero no está en la lista de inyección.")
    else:
        report.append("- Ninguna.")
    report.append("")
    
    return "\n".join(report)

def main():
    skill_dirs = ["gaia-ops/skills", ".claude/skills", "conductor-orchestrator/skills"]
    agent_dirs = ["gaia-ops/agents", ".claude/agents", "conductor-orchestrator/agents"]
    
    print("Buscando skills...")
    skills = find_skills(skill_dirs)
    
    print("Validando formato...")
    validation = validate_skill_format(skills)
    
    print("Buscando agentes...")
    agents = find_agents(agent_dirs)
    
    print("Analizando inyección...")
    injection_info = analyze_injection()
    
    print("Generando reporte...")
    report = generate_report(skills, validation, agents, injection_info)
    
    report_path = Path("gaia-ops/tools/validation/skills_report.md")
    report_path.write_text(report, encoding="utf-8")
    print(f"Reporte generado en {report_path}")
    
    # Imprimir el reporte en la salida estándar para que el agente lo pueda devolver
    print("\n" + "="*50 + "\n")
    print(report)

if __name__ == "__main__":
    main()
