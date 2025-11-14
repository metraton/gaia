#!/bin/bash

###############################################################################
# Script de Prueba Exhaustiva - Rutas Absolutas y Project Context Repo
# Versión: 2.6.2-rc
# Ubicación de prueba: /home/jaguilar/aaxis/rnd/repos/
###############################################################################

set -e  # Exit on error

# Colores para output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

TEST_DIR="/home/jaguilar/aaxis/rnd/repos"
GAIA_OPS_LOCAL="/home/jaguilar/aaxis/vtr/repositories/gaia-ops"
PROJECT_CONTEXT_REPO="git@bitbucket.org:aaxisdigital/rnd-project-context.git"

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Test Suite: Absolute Paths & Project Context Repo       ║${NC}"
echo -e "${CYAN}║  Gaia-Ops v2.6.2-rc                                       ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

###############################################################################
# TEST 1: Rutas Absolutas SIN Repo de Context
###############################################################################

echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}TEST 1: Instalación con Rutas Absolutas SIN Repo de Context${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo ""

echo -e "${YELLOW}→ Limpiando instalación anterior...${NC}"
cd "$TEST_DIR"
rm -rf .claude CLAUDE.md AGENTS.md 2>/dev/null || true
echo -e "${GREEN}✓ Directorio limpio${NC}"
echo ""

echo -e "${YELLOW}→ Desinstalando versión npm anterior...${NC}"
npm uninstall @jaguilar87/gaia-ops 2>/dev/null || true
echo -e "${GREEN}✓ Desinstalado${NC}"
echo ""

echo -e "${YELLOW}→ Instalando versión LOCAL...${NC}"
npm install "$GAIA_OPS_LOCAL" --save-dev
echo -e "${GREEN}✓ Instalado desde: $GAIA_OPS_LOCAL${NC}"
echo ""

echo -e "${YELLOW}→ Ejecutando gaia-init (rutas absolutas, SIN repo)...${NC}"
npx gaia-init --non-interactive \
  --gitops "$TEST_DIR/gitops" \
  --terraform "$TEST_DIR/terraform" \
  --app-services "$TEST_DIR/app-services" \
  --project-id aaxis-rnd \
  --region us-east-1 \
  --cluster rnd-cluster

echo ""
echo -e "${GREEN}✓ gaia-init completado${NC}"
echo ""

echo -e "${YELLOW}→ Verificando archivos creados...${NC}"

# Verificar CLAUDE.md
if [ -f "$TEST_DIR/CLAUDE.md" ]; then
    CLAUDE_SIZE=$(wc -l < "$TEST_DIR/CLAUDE.md")
    echo -e "${GREEN}✓ CLAUDE.md existe ($CLAUDE_SIZE líneas)${NC}"
else
    echo -e "${RED}✗ CLAUDE.md NO existe${NC}"
    exit 1
fi

# Verificar .claude/settings.json
if [ -f "$TEST_DIR/.claude/settings.json" ]; then
    echo -e "${GREEN}✓ .claude/settings.json existe${NC}"
else
    echo -e "${RED}✗ .claude/settings.json NO existe${NC}"
    exit 1
fi

# Verificar project-context.json (generado, NO de repo)
if [ -f "$TEST_DIR/.claude/project-context/project-context.json" ]; then
    echo -e "${GREEN}✓ .claude/project-context/project-context.json existe${NC}"
    
    # Verificar que contiene rutas absolutas
    if grep -q "$TEST_DIR/gitops" "$TEST_DIR/.claude/project-context/project-context.json"; then
        echo -e "${GREEN}✓ Contiene ruta absoluta de gitops${NC}"
    else
        echo -e "${RED}✗ NO contiene ruta absoluta de gitops${NC}"
        exit 1
    fi
else
    echo -e "${RED}✗ project-context.json NO existe${NC}"
    exit 1
fi

# Verificar que NO hay .git en project-context (porque no clonamos repo)
if [ -d "$TEST_DIR/.claude/project-context/.git" ]; then
    echo -e "${RED}✗ .git existe (no debería, porque no pasamos --project-context-repo)${NC}"
    exit 1
else
    echo -e "${GREEN}✓ .git NO existe (correcto, no clonamos repo)${NC}"
fi

# Verificar symlinks
SYMLINK_COUNT=$(find "$TEST_DIR/.claude" -type l 2>/dev/null | wc -l)
echo -e "${GREEN}✓ $SYMLINK_COUNT symlinks creados${NC}"

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}TEST 1: ✓ PASÓ - Rutas absolutas SIN repo funcionan${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
sleep 2

###############################################################################
# TEST 2: Rutas Absolutas CON Repo de Context
###############################################################################

echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}TEST 2: Instalación con Rutas Absolutas CON Repo de Context${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo ""

echo -e "${YELLOW}→ Limpiando para TEST 2...${NC}"
cd "$TEST_DIR"
rm -rf .claude CLAUDE.md AGENTS.md
echo -e "${GREEN}✓ Directorio limpio${NC}"
echo ""

echo -e "${YELLOW}→ Ejecutando gaia-init (rutas absolutas, CON repo)...${NC}"
npx gaia-init --non-interactive \
  --gitops "$TEST_DIR/gitops" \
  --terraform "$TEST_DIR/terraform" \
  --app-services "$TEST_DIR/app-services" \
  --project-id aaxis-rnd \
  --region us-east-1 \
  --cluster rnd-cluster \
  --project-context-repo "$PROJECT_CONTEXT_REPO"

echo ""
echo -e "${GREEN}✓ gaia-init completado${NC}"
echo ""

echo -e "${YELLOW}→ Verificando archivos creados...${NC}"

# Verificar CLAUDE.md
if [ -f "$TEST_DIR/CLAUDE.md" ]; then
    echo -e "${GREEN}✓ CLAUDE.md existe${NC}"
else
    echo -e "${RED}✗ CLAUDE.md NO existe${NC}"
    exit 1
fi

# Verificar .claude/settings.json
if [ -f "$TEST_DIR/.claude/settings.json" ]; then
    echo -e "${GREEN}✓ .claude/settings.json existe${NC}"
else
    echo -e "${RED}✗ .claude/settings.json NO existe${NC}"
    exit 1
fi

# Verificar project-context.json (del repo clonado)
if [ -f "$TEST_DIR/.claude/project-context/project-context.json" ]; then
    echo -e "${GREEN}✓ .claude/project-context/project-context.json existe${NC}"
else
    echo -e "${RED}✗ project-context.json NO existe${NC}"
    exit 1
fi

# Verificar que SÍ hay .git en project-context (porque clonamos repo)
if [ -d "$TEST_DIR/.claude/project-context/.git" ]; then
    echo -e "${GREEN}✓ .git existe (correcto, clonamos el repo)${NC}"
    
    # Verificar remote
    cd "$TEST_DIR/.claude/project-context"
    REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
    if [[ "$REMOTE" == *"rnd-project-context"* ]]; then
        echo -e "${GREEN}✓ Remote correcto: $REMOTE${NC}"
    else
        echo -e "${RED}✗ Remote incorrecto: $REMOTE${NC}"
        exit 1
    fi
    cd "$TEST_DIR"
else
    echo -e "${RED}✗ .git NO existe (debería existir)${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}TEST 2: ✓ PASÓ - Rutas absolutas CON repo funcionan${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
sleep 2

###############################################################################
# TEST 3: Cleanup y Reinstalación
###############################################################################

echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}TEST 3: Cleanup y Reinstalación${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo ""

echo -e "${YELLOW}→ Ejecutando gaia-cleanup...${NC}"
cd "$TEST_DIR"
npx gaia-cleanup
echo -e "${GREEN}✓ Cleanup completado${NC}"
echo ""

echo -e "${YELLOW}→ Verificando que archivos fueron eliminados...${NC}"

if [ -f "$TEST_DIR/CLAUDE.md" ]; then
    echo -e "${RED}✗ CLAUDE.md todavía existe (debería estar eliminado)${NC}"
    exit 1
else
    echo -e "${GREEN}✓ CLAUDE.md eliminado${NC}"
fi

if [ -f "$TEST_DIR/.claude/settings.json" ]; then
    echo -e "${RED}✗ settings.json todavía existe (debería estar eliminado)${NC}"
    exit 1
else
    echo -e "${GREEN}✓ settings.json eliminado${NC}"
fi

# Verificar que .git fue preservado
if [ -d "$TEST_DIR/.claude/project-context/.git" ]; then
    echo -e "${GREEN}✓ .claude/project-context/.git preservado (correcto)${NC}"
else
    echo -e "${RED}✗ .claude/project-context/.git eliminado (debería preservarse)${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}→ Reinstalando después de cleanup...${NC}"
npx gaia-init --non-interactive \
  --gitops "$TEST_DIR/gitops" \
  --terraform "$TEST_DIR/terraform" \
  --app-services "$TEST_DIR/app-services" \
  --project-id aaxis-rnd \
  --region us-east-1 \
  --cluster rnd-cluster

echo ""
echo -e "${GREEN}✓ Reinstalación completada${NC}"
echo ""

# Verificar que archivos fueron recreados
if [ -f "$TEST_DIR/CLAUDE.md" ]; then
    echo -e "${GREEN}✓ CLAUDE.md recreado${NC}"
else
    echo -e "${RED}✗ CLAUDE.md NO recreado${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}TEST 3: ✓ PASÓ - Cleanup preserva datos, reinstalación funciona${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
sleep 2

###############################################################################
# TEST 4: Rutas Relativas (para comparación)
###############################################################################

echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}TEST 4: Rutas Relativas (verificar retrocompatibilidad)${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo ""

echo -e "${YELLOW}→ Limpiando para TEST 4...${NC}"
cd "$TEST_DIR"
rm -rf .claude CLAUDE.md AGENTS.md
echo -e "${GREEN}✓ Directorio limpio${NC}"
echo ""

echo -e "${YELLOW}→ Ejecutando gaia-init (rutas RELATIVAS)...${NC}"
cd "$TEST_DIR"
npx gaia-init --non-interactive \
  --gitops ./gitops \
  --terraform ./terraform \
  --app-services ./app-services \
  --project-id aaxis-rnd \
  --region us-east-1 \
  --cluster rnd-cluster

echo ""
echo -e "${GREEN}✓ gaia-init completado${NC}"
echo ""

echo -e "${YELLOW}→ Verificando archivos creados con rutas relativas...${NC}"

if [ -f "$TEST_DIR/CLAUDE.md" ]; then
    echo -e "${GREEN}✓ CLAUDE.md existe${NC}"
else
    echo -e "${RED}✗ CLAUDE.md NO existe${NC}"
    exit 1
fi

# Verificar que project-context.json tiene rutas relativas
if grep -q "./gitops" "$TEST_DIR/.claude/project-context/project-context.json"; then
    echo -e "${GREEN}✓ Contiene ruta relativa ./gitops (correcto)${NC}"
else
    echo -e "${YELLOW}⚠ No contiene ./gitops (puede ser normalizado a ruta absoluta)${NC}"
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}TEST 4: ✓ PASÓ - Rutas relativas siguen funcionando${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""

###############################################################################
# TEST 5: Probar gaia-metrics
###############################################################################

echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}TEST 5: Comando gaia-metrics${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo ""

echo -e "${YELLOW}→ Creando logs de ejemplo...${NC}"
mkdir -p "$TEST_DIR/.claude/logs"
cat > "$TEST_DIR/.claude/logs/test.jsonl" << 'EOF'
{"event":"agent_routed","agent":"gitops-agent","success":true,"timestamp":"2025-11-14T10:00:00Z"}
{"event":"agent_invoked","agent":"gitops-agent","tier":"T1","timestamp":"2025-11-14T10:00:01Z"}
EOF
echo -e "${GREEN}✓ Logs creados${NC}"
echo ""

echo -e "${YELLOW}→ Ejecutando gaia-metrics...${NC}"
cd "$TEST_DIR"
npx gaia-metrics
echo ""
echo -e "${GREEN}✓ gaia-metrics ejecutado${NC}"
echo ""

echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}TEST 5: ✓ PASÓ - gaia-metrics funciona${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""

###############################################################################
# RESUMEN FINAL
###############################################################################

echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                   RESUMEN DE TESTS                        ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✓ TEST 1: Rutas absolutas SIN repo - PASÓ${NC}"
echo -e "${GREEN}✓ TEST 2: Rutas absolutas CON repo - PASÓ${NC}"
echo -e "${GREEN}✓ TEST 3: Cleanup y reinstalación - PASÓ${NC}"
echo -e "${GREEN}✓ TEST 4: Rutas relativas (retrocompatibilidad) - PASÓ${NC}"
echo -e "${GREEN}✓ TEST 5: gaia-metrics - PASÓ${NC}"
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✓✓✓ TODOS LOS TESTS PASARON EXITOSAMENTE ✓✓✓${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}✨ Versión 2.6.2 lista para publicar a npm! ✨${NC}"
echo ""

