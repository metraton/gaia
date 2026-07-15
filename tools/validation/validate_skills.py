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

def analyze_injection(agents):
    """Analiza cómo se precargan las skills.

    Mecanismo real (arquitectura verificada): Gaia NO inyecta skills vía hook.
    La precarga es 100% nativa del harness de Claude Code, que lee el campo
    'skills:' del frontmatter de cada agente y presenta el contenido de cada
    SKILL.md antes de que el agente actúe -- pre_tool_use.py no contiene, ni
    debe contener, lógica de inyección de skills.

    En vez de depender de una nota de texto fija en pre_tool_use.py (frágil:
    desaparece en cualquier refactor sin que el mecanismo real cambie), este
    check confirma dos hechos observables en el estado actual del código:
      1. pre_tool_use.py sigue sin contener lógica de inyección de skills
         (ninguna referencia a leer o insertar el contenido de un SKILL.md).
      2. Existen agentes reales cuyo frontmatter declara 'skills:', que es
         la evidencia de que el mecanismo nativo del harness está en uso.
    """
    hook_path = Path("gaia/hooks/pre_tool_use.py")
    if not hook_path.exists():
        return "No se encontró gaia/hooks/pre_tool_use.py"

    content = hook_path.read_text(encoding="utf-8", errors="ignore")
    # Señales de que el hook mismo estuviera leyendo/insertando contenido de
    # un SKILL.md -- si aparecen, el hook estaría inyectando skills y el
    # mecanismo nativo del harness ya no sería la única vía.
    suspicious_injection_markers = ("SKILL.md", "skill_content", "inject_skill")
    hook_has_injection_logic = any(marker in content for marker in suspicious_injection_markers)

    agents_with_skills = sorted(
        name for name, data in agents.items() if data.get("skills_declared")
    )

    if hook_has_injection_logic:
        return (
            "ADVERTENCIA: pre_tool_use.py contiene referencias que sugieren lógica de "
            "inyección de skills. Esto contradice el mecanismo esperado (precarga nativa "
            "del harness vía frontmatter) -- revisar manualmente."
        )

    if not agents_with_skills:
        return (
            "pre_tool_use.py no contiene lógica de inyección de skills (correcto), pero no "
            "se encontró ningún agente con 'skills:' declaradas en su frontmatter -- no hay "
            "evidencia observable de que el mecanismo nativo esté en uso."
        )

    return (
        "Las skills se precargan de forma NATIVA por el harness de Claude Code a través del "
        "campo 'skills:' en el frontmatter de cada agente; Gaia no las inyecta vía hook "
        "(confirmado: pre_tool_use.py no contiene lógica de inyección de skills). Evidencia: "
        f"{len(agents_with_skills)} agente(s) con 'skills:' declaradas: "
        f"{', '.join(agents_with_skills)}."
    )

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
    # Gaia es un plugin único unificado (sin plugins legacy como
    # "conductor-orchestrator"); los agentes y skills viven en gaia/agents y
    # gaia/skills (fuente) y su copia instalada en .claude/agents, .claude/skills.
    skill_dirs = ["gaia/skills", ".claude/skills"]
    agent_dirs = ["gaia/agents", ".claude/agents"]

    print("Buscando skills...")
    skills = find_skills(skill_dirs)

    print("Validando formato...")
    validation = validate_skill_format(skills)

    print("Buscando agentes...")
    agents = find_agents(agent_dirs)

    print("Analizando inyección...")
    injection_info = analyze_injection(agents)
    
    print("Generando reporte...")
    report = generate_report(skills, validation, agents, injection_info)
    
    report_path = Path("gaia/tools/validation/skills_report.md")
    report_path.write_text(report, encoding="utf-8")
    print(f"Reporte generado en {report_path}")
    
    # Imprimir el reporte en la salida estándar para que el agente lo pueda devolver
    print("\n" + "="*50 + "\n")
    print(report)

if __name__ == "__main__":
    main()
