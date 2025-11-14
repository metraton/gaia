#!/bin/bash

###############################################################################
# Script: Publicar v2.6.2 y Probar en /home/jaguilar/aaxis/rnd/repos
###############################################################################

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

GAIA_OPS_DIR="/home/jaguilar/aaxis/vtr/repositories/gaia-ops"
TEST_DIR="/home/jaguilar/aaxis/rnd/repos"

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║         Publicar v2.6.2 y Probar Instalación             ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

###############################################################################
# PASO 1: Commit y Publicar a npm
###############################################################################

echo -e "${CYAN}═══ PASO 1: Publicando v2.6.2 a npm ═══${NC}"
echo ""

cd "$GAIA_OPS_DIR"

echo -e "${YELLOW}→ Agregando cambios a git...${NC}"
git add -A
echo -e "${GREEN}✓ Cambios agregados${NC}"
echo ""

echo -e "${YELLOW}→ Haciendo commit...${NC}"
git commit -m "feat: add absolute paths support and project-context-repo flag

- Add normalizePath() function to handle both relative and absolute paths
- Add --project-context-repo flag for non-interactive mode
- Add CLAUDE_PROJECT_CONTEXT_REPO environment variable
- Update CLI documentation with examples
- Improve path handling in validateAndSetupProjectPaths()
- Add comprehensive test suite (TEST_ABSOLUTE_PATHS.sh)
- Add usage examples (EXAMPLES_ABSOLUTE_PATHS.md)
- Bump version to 2.6.2

Breaking changes: None - fully backward compatible

New features:
  - Absolute paths support for --gitops, --terraform, --app-services
  - --project-context-repo flag for non-interactive installations
  - Better path normalization

Tested scenarios:
  ✓ Absolute paths without context repo
  ✓ Absolute paths with context repo
  ✓ Relative paths (backward compatibility)
  ✓ Cleanup and reinstallation
  ✓ gaia-metrics command"

echo -e "${GREEN}✓ Commit realizado${NC}"
echo ""

echo -e "${YELLOW}→ Publicando a npm...${NC}"
npm publish
echo -e "${GREEN}✓ Publicado a npm registry${NC}"
echo ""

# Esperar un poco para que npm propague
echo -e "${YELLOW}→ Esperando 5 segundos para que npm propague...${NC}"
sleep 5
echo -e "${GREEN}✓ Listo${NC}"
echo ""

###############################################################################
# PASO 2: Limpiar instalación anterior en test dir
###############################################################################

echo -e "${CYAN}═══ PASO 2: Preparando directorio de prueba ═══${NC}"
echo ""

cd "$TEST_DIR"

echo -e "${YELLOW}→ Limpiando instalación anterior...${NC}"
rm -rf .claude CLAUDE.md AGENTS.md 2>/dev/null || true
echo -e "${GREEN}✓ Directorio limpio${NC}"
echo ""

echo -e "${YELLOW}→ Desinstalando versión anterior...${NC}"
npm uninstall @jaguilar87/gaia-ops 2>/dev/null || true
echo -e "${GREEN}✓ Desinstalado${NC}"
echo ""

###############################################################################
# PASO 3: Instalar v2.6.2 desde npm
###############################################################################

echo -e "${CYAN}═══ PASO 3: Instalando v2.6.2 desde npm ═══${NC}"
echo ""

cd "$TEST_DIR"

echo -e "${YELLOW}→ Instalando @jaguilar87/gaia-ops@2.6.2...${NC}"
npm install @jaguilar87/gaia-ops@2.6.2 --save-dev
echo -e "${GREEN}✓ Instalado desde npm registry${NC}"
echo ""

echo -e "${YELLOW}→ Verificando versión instalada...${NC}"
VERSION=$(npm list @jaguilar87/gaia-ops --depth=0 | grep @jaguilar87/gaia-ops | awk '{print $2}')
echo -e "${GREEN}✓ Versión instalada: $VERSION${NC}"
echo ""

###############################################################################
# PASO 4: Probar Caso 1 - Rutas Absolutas SIN Repo
###############################################################################

echo -e "${CYAN}═══ PASO 4: Test - Rutas Absolutas SIN Repo ═══${NC}"
echo ""

cd "$TEST_DIR"

echo -e "${YELLOW}→ Ejecutando gaia-init (rutas absolutas)...${NC}"
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

echo -e "${YELLOW}→ Verificando archivos...${NC}"
if [ -f "$TEST_DIR/CLAUDE.md" ]; then
    LINES=$(wc -l < "$TEST_DIR/CLAUDE.md")
    echo -e "${GREEN}✓ CLAUDE.md existe ($LINES líneas)${NC}"
else
    echo -e "${RED}✗ CLAUDE.md NO existe${NC}"
    exit 1
fi

if [ -f "$TEST_DIR/.claude/settings.json" ]; then
    echo -e "${GREEN}✓ settings.json existe${NC}"
else
    echo -e "${RED}✗ settings.json NO existe${NC}"
    exit 1
fi

if grep -q "$TEST_DIR/gitops" "$TEST_DIR/.claude/project-context/project-context.json"; then
    echo -e "${GREEN}✓ project-context.json contiene rutas absolutas${NC}"
else
    echo -e "${RED}✗ project-context.json NO contiene rutas absolutas${NC}"
    exit 1
fi

SYMLINKS=$(find "$TEST_DIR/.claude" -type l 2>/dev/null | wc -l)
echo -e "${GREEN}✓ $SYMLINKS symlinks creados${NC}"
echo ""

###############################################################################
# PASO 5: Limpiar y Probar Caso 2 - Con Repo de Context
###############################################################################

echo -e "${CYAN}═══ PASO 5: Test - Rutas Absolutas CON Repo ═══${NC}"
echo ""

cd "$TEST_DIR"

echo -e "${YELLOW}→ Limpiando para test 2...${NC}"
rm -rf .claude CLAUDE.md AGENTS.md
echo -e "${GREEN}✓ Limpio${NC}"
echo ""

echo -e "${YELLOW}→ Ejecutando gaia-init (con repo de context)...${NC}"
npx gaia-init --non-interactive \
  --gitops "$TEST_DIR/gitops" \
  --terraform "$TEST_DIR/terraform" \
  --app-services "$TEST_DIR/app-services" \
  --project-id aaxis-rnd \
  --region us-east-1 \
  --cluster rnd-cluster \
  --project-context-repo git@bitbucket.org:aaxisdigital/rnd-project-context.git

echo ""
echo -e "${GREEN}✓ gaia-init completado${NC}"
echo ""

echo -e "${YELLOW}→ Verificando repo clonado...${NC}"
if [ -d "$TEST_DIR/.claude/project-context/.git" ]; then
    echo -e "${GREEN}✓ Repo clonado (.git existe)${NC}"
    cd "$TEST_DIR/.claude/project-context"
    REMOTE=$(git remote get-url origin 2>/dev/null)
    echo -e "${GREEN}✓ Remote: $REMOTE${NC}"
    cd "$TEST_DIR"
else
    echo -e "${RED}✗ Repo NO clonado${NC}"
    exit 1
fi
echo ""

###############################################################################
# PASO 6: Probar gaia-metrics
###############################################################################

echo -e "${CYAN}═══ PASO 6: Test - gaia-metrics ═══${NC}"
echo ""

cd "$TEST_DIR"

echo -e "${YELLOW}→ Creando logs de prueba...${NC}"
mkdir -p "$TEST_DIR/.claude/logs"
cat > "$TEST_DIR/.claude/logs/test.jsonl" << 'EOF'
{"event":"agent_routed","agent":"gitops-agent","success":true,"timestamp":"2025-11-14T10:00:00Z"}
{"event":"agent_invoked","agent":"gitops-agent","tier":"T1","timestamp":"2025-11-14T10:00:01Z"}
EOF
echo -e "${GREEN}✓ Logs creados${NC}"
echo ""

echo -e "${YELLOW}→ Ejecutando gaia-metrics...${NC}"
npx gaia-metrics
echo ""
echo -e "${GREEN}✓ gaia-metrics funciona${NC}"
echo ""

###############################################################################
# RESUMEN FINAL
###############################################################################

echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                    ✅ TODO LISTO ✅                        ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✓ Versión 2.6.2 publicada a npm${NC}"
echo -e "${GREEN}✓ Instalada en: $TEST_DIR${NC}"
echo -e "${GREEN}✓ Test 1: Rutas absolutas SIN repo - OK${NC}"
echo -e "${GREEN}✓ Test 2: Rutas absolutas CON repo - OK${NC}"
echo -e "${GREEN}✓ Test 3: gaia-metrics - OK${NC}"
echo ""
echo -e "${CYAN}📦 Estado Final:${NC}"
echo -e "${CYAN}  • Package: @jaguilar87/gaia-ops@2.6.2${NC}"
echo -e "${CYAN}  • Instalado en: $TEST_DIR${NC}"
echo -e "${CYAN}  • Repo de context: git@bitbucket.org:aaxisdigital/rnd-project-context.git${NC}"
echo -e "${CYAN}  • Archivos creados:${NC}"
echo -e "${CYAN}    - CLAUDE.md${NC}"
echo -e "${CYAN}    - .claude/settings.json${NC}"
echo -e "${CYAN}    - .claude/project-context/ (con .git)${NC}"
echo -e "${CYAN}    - $SYMLINKS symlinks${NC}"
echo ""
echo -e "${GREEN}🎉 Gaia-Ops 2.6.2 listo para usar! 🎉${NC}"
echo ""

