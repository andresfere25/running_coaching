#!/bin/bash
# ──────────────────────────────────────────────────────────────
#  init_github.sh — Inicializa el repo Git y lo sube a GitHub
#
#  Uso:
#    chmod +x init_github.sh
#    ./init_github.sh TU_USUARIO_GITHUB
#
#  Prerequisitos:
#    - Git instalado (git --version)
#    - GitHub CLI instalado: https://cli.github.com/
#      o tener el repo creado manualmente en github.com
# ──────────────────────────────────────────────────────────────

GITHUB_USER=${1:-"TU_USUARIO"}
REPO_NAME="running-coaching"

echo ""
echo "🏃  Iniciando Running Coaching en GitHub"
echo "   Usuario: $GITHUB_USER"
echo "   Repo:    $REPO_NAME"
echo ""

# 1. Git init
echo "1/5  git init..."
git init
git branch -M main

# 2. Verificar .gitignore protege secretos
echo "2/5  Verificando .gitignore..."
if grep -q "secrets/" .gitignore && grep -q "^\.env" .gitignore; then
    echo "     ✅ .gitignore protege secrets/ y .env"
else
    echo "     ❌ ERROR: .gitignore no está bien configurado. Abortando."
    exit 1
fi

# 3. Primer commit
echo "3/5  Primer commit..."
git add .
git status --short
git commit -m "feat: initial commit — Running Coaching System

Pipeline completo:
- Ingesta de Google Forms y Sheets
- Sync con Strava API
- Build features (ACWR, monotonía, zonas, semáforo)  
- Plan builder semanal
- Generación de PDF
- GitHub Actions workflow (lunes 6am Bogotá)
"

# 4. Crear repo en GitHub (si tienes GitHub CLI)
echo "4/5  Creando repo en GitHub..."
if command -v gh &> /dev/null; then
    gh repo create "$REPO_NAME" --private --source=. --remote=origin --push
    echo "     ✅ Repo creado y subido: https://github.com/$GITHUB_USER/$REPO_NAME"
else
    echo "     ℹ️  GitHub CLI no encontrado. Haz esto manualmente:"
    echo ""
    echo "     a) Ve a https://github.com/new"
    echo "     b) Nombre: $REPO_NAME  |  Privado  |  SIN inicializar"
    echo "     c) Corre estos comandos:"
    echo ""
    echo "        git remote add origin https://github.com/$GITHUB_USER/$REPO_NAME.git"
    echo "        git push -u origin main"
    echo ""
fi

# 5. Instrucciones para GitHub Secrets
echo "5/5  Configura los Secrets en GitHub:"
echo ""
echo "     Ve a: https://github.com/$GITHUB_USER/$REPO_NAME/settings/secrets/actions"
echo ""
echo "     Secrets a agregar:"
echo "       SHEET_ID              → ID de tu Google Sheet"
echo "       GOOGLE_SA_JSON        → Contenido del service_account.json (todo el JSON)"
echo "       STRAVA_CLIENT_ID      → Tu Strava Client ID"
echo "       STRAVA_CLIENT_SECRET  → Tu Strava Client Secret"
echo "       ANTHROPIC_API_KEY     → (Opcional) API key de Anthropic"
echo ""
echo "✅  Setup completo."
echo ""
echo "Para correr el pipeline localmente:"
echo "    python run_pipeline.py --all"
echo ""
echo "Para disparar manualmente en GitHub:"
echo "    https://github.com/$GITHUB_USER/$REPO_NAME/actions"
echo ""
