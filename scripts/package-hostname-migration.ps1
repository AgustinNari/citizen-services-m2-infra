param(
    [Parameter(Mandatory = $true)][string]$Commit,
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resolvedCommit = (& git -C $repositoryRoot rev-parse --verify "$Commit^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or $resolvedCommit -notmatch '^[0-9a-f]{40}$') { throw 'Invalid commit' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repositoryRoot '.https-validation/hostname-package' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path
$archive = Join-Path $outputRoot "hostname-$resolvedCommit.tar"
if (Test-Path -LiteralPath $archive) { throw 'Archive already exists; use another output directory' }
$stage = Join-Path $outputRoot ('stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
$paths = @('scripts/migrate-https-hostname.sh', 'scripts/migrate_https_hostname.py', 'scripts/smoke-https.sh', 'docs/dynu-migration.md')
foreach ($path in $paths) {
    $entry = (& git -C $repositoryRoot ls-tree $resolvedCommit -- $path | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($entry) -or $entry -notmatch '^100(644|755) blob ') { throw "Missing or nonregular committed file: $path" }
}
$sourceArchive = Join-Path $stage 'source.tar'
# Keep archive bytes identical to committed blobs even on Windows with autocrlf enabled.
& git -C $repositoryRoot -c core.autocrlf=false -c core.eol=lf archive --format=tar "--output=$sourceArchive" $resolvedCommit -- @paths
if ($LASTEXITCODE -ne 0) { throw 'git archive failed' }
& tar -xf $sourceArchive -C $stage
if ($LASTEXITCODE -ne 0) { throw 'Cannot extract the committed sources' }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$manifest = @()
foreach ($path in $paths) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $stage $path) -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest += "$hash  $path"
}
[System.IO.File]::WriteAllText((Join-Path $stage 'COMMIT'), "$resolvedCommit`n", $utf8)
$manifest += ((Get-FileHash -LiteralPath (Join-Path $stage 'COMMIT') -Algorithm SHA256).Hash.ToLowerInvariant() + '  COMMIT')
[System.IO.File]::WriteAllText((Join-Path $stage 'SHA256SUMS'), (($manifest -join "`n") + "`n"), $utf8)
& tar -cf $archive -C $stage @paths COMMIT SHA256SUMS
if ($LASTEXITCODE -ne 0) { throw 'Cannot create the final package' }
$digest = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText("$archive.sha256", "$digest  $([System.IO.Path]::GetFileName($archive))`n", $utf8)
Write-Output "Commit: $resolvedCommit"
Write-Output "Archive: $archive"
Write-Output "SHA256: $digest"
Write-Output 'No files were copied to a server. Transfer the archive and its .sha256 file after CI success.'
