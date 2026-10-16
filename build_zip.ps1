<#
.SYNOPSIS
    Empaqueta la extensión ViReal en un .zip listo para publicar.

.DESCRIPTION
    Genera dist/ingetrazo_vireal-<version>.zip con vireal.py, README.md y
    LICENSE. El usuario final sólo necesita copiar vireal.py a su carpeta de
    plugins de IngeTrazo.

.PARAMETER Version
    Versión a empaquetar. Por defecto 0.1.0.

.PARAMETER Install
    Además de empaquetar, copia vireal.py a la carpeta de plugins de IngeTrazo
    del usuario actual (%APPDATA%\IngeTrazo\plugins) y borra el antiguo
    mundo.py si siguiera ahí.

.EXAMPLE
    ./build_zip.ps1

.EXAMPLE
    ./build_zip.ps1 -Version 0.2.0 -Install
#>
[CmdletBinding()]
param(
    [string]$Version = "0.1.0",
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$src = Join-Path $root "vireal.py"
if (-not (Test-Path $src)) {
    throw "No encuentro vireal.py en $root"
}

$dist = Join-Path $root "dist"
$stage = Join-Path $dist "ingetrazo_vireal"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

Copy-Item $src (Join-Path $stage "vireal.py")
foreach ($f in @("README.md", "LICENSE", "CHANGELOG.md")) {
    $p = Join-Path $root $f
    if (Test-Path $p) { Copy-Item $p (Join-Path $stage $f) }
}

$zip = Join-Path $dist "ingetrazo_vireal-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force

Write-Host ""
Write-Host "Paquete creado:" -ForegroundColor Green
Write-Host "  $zip"
Write-Host ""
Get-ChildItem $stage | Select-Object Name, Length | Format-Table -AutoSize

if ($Install) {
    $plugins = Join-Path $env:APPDATA "IngeTrazo\plugins"
    if (-not (Test-Path $plugins)) {
        Write-Warning "No encuentro la carpeta de plugins: $plugins"
    }
    else {
        $old = Join-Path $plugins "mundo.py"
        if (Test-Path $old) {
            Remove-Item $old -Force
            Write-Host "Eliminado antiguo: $old"
        }
        Copy-Item $src (Join-Path $plugins "vireal.py") -Force
        Write-Host "Instalado en: $plugins\vireal.py" -ForegroundColor Green
        Write-Host "Reinicia IngeTrazo para cargarlo."
    }
}
