<#
.SYNOPSIS
    Empaqueta la extensión ViReal en un .zip listo para publicar.

.DESCRIPTION
    Genera dist/ingetrazo_vireal-<version>.zip con vireal.py, README.md,
    CHANGELOG.md, LICENSE y el HDR del horizonte. El usuario final sólo
    necesita copiar vireal.py y el HDR a su carpeta de plugins de IngeTrazo.

.PARAMETER Version
    Versión a empaquetar. Si se omite, se toma del ``__version__`` de
    vireal.py (así no se desincronizan).

.PARAMETER Install
    Además de empaquetar, copia vireal.py y el HDR a la carpeta de plugins de IngeTrazo
    del usuario actual (%APPDATA%\IngeTrazo\plugins) y borra el antiguo
    mundo.py si siguiera ahí.

.EXAMPLE
    ./build_zip.ps1

.EXAMPLE
    ./build_zip.ps1 -Version 0.2.0 -Install
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$src = Join-Path $root "vireal.py"
if (-not (Test-Path $src)) {
    throw "No encuentro vireal.py en $root"
}

if (-not $Version) {
    $m = Select-String -Path $src -Pattern '__version__\s*=\s*"([^"]+)"'
    if ($m) { $Version = $m.Matches[0].Groups[1].Value }
    else { $Version = "0.0.0" }
    Write-Host "Versión detectada en vireal.py: $Version"
}

$dist = Join-Path $root "dist"
$stage = Join-Path $dist "ingetrazo_vireal"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

Copy-Item $src (Join-Path $stage "vireal.py")
foreach ($f in @("README.md", "LICENSE", "CHANGELOG.md",
                "grasslands_sunset_2k.hdr")) {
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
        $hdr = Join-Path $root "grasslands_sunset_2k.hdr"
        if (Test-Path $hdr) {
            Copy-Item $hdr (Join-Path $plugins "grasslands_sunset_2k.hdr") -Force
            Write-Host "Instalado HDR en: $plugins\grasslands_sunset_2k.hdr" -ForegroundColor Green
        }
        Write-Host "Instalado en: $plugins\vireal.py" -ForegroundColor Green
        Write-Host "Reinicia IngeTrazo para cargarlo."
    }
}
