$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dist = Join-Path $root "dist"
$bundle = Join-Path $dist "FrameTimingSkill"
$archive = Join-Path $dist "FrameTimingSkill-Windows-x64.zip"

Set-Location $root
python -m PyInstaller --noconfirm --clean packaging\windows\FrameTimingSkill.spec

Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $bundle
Copy-Item -LiteralPath (Join-Path $root "README.en.md") -Destination $bundle
Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination $bundle

if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive
}
Compress-Archive -Path (Join-Path $bundle "*") -DestinationPath $archive -CompressionLevel Optimal
Write-Output $archive
