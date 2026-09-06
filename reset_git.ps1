# Ejecutar con: .\reset_git.ps1
# Reinicia el repositorio git desde cero en esta carpeta, de forma segura.

Write-Host "Borrando repositorio git anterior (si existe)..."
Remove-Item -Recurse -Force ".git" -ErrorAction SilentlyContinue

Write-Host "Inicializando git..."
git init

Write-Host "Agregando archivos..."
git add .

Write-Host ""
Write-Host "=== REVISA ESTA LISTA ANTES DE CONTINUAR ==="
Write-Host "NO deben aparecer: .env, credentials/, .venv/, venv/"
Write-Host ""
git status
