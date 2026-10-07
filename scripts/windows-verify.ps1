#requires -Version 5.1
<#
Read-only Windows verification of files created by Nativol's Mac card test.
No repair, format, mount, installation, or network operations are requested.
Only the JSON result is written, to a local fixed disk outside the tested volume.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z]:\\?$')]
    [string]$Drive,

    [string]$ManifestPath = '',

    [ValidatePattern('^([0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4})?$')]
    [string]$ExpectedVolumeSerial = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Drive = $Drive.TrimEnd('\').ToUpperInvariant()
$driveRoot = $Drive + '\'
$maxManifestBytes = 65536
$report = [ordered]@{
    schemaVersion = 1
    kind = 'nativol-windows-physical-ntfs-verification'
    status = 'failed'
    startedAt = [DateTime]::UtcNow.ToString('o')
    drive = $Drive
    testedVolumeWritesRequested = $false
    repairsRequested = $false
    filesystemCheckPerformed = $false
    certifiesFilesystemSafety = $false
    files = @()
    absent = @()
}
$disk = $null

function Get-LocalLogicalDisk([string]$DeviceID) {
    if ($DeviceID -notmatch '^[A-Z]:$') { throw 'A drive letter is required.' }
    $diskMatches = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$DeviceID'")
    if ($diskMatches.Count -ne 1) { throw "Cannot identify drive $DeviceID." }
    return $diskMatches[0]
}

function Assert-Component([string]$Name) {
    if ([string]::IsNullOrEmpty($Name) -or $Name -in @('.', '..') -or
        $Name -match '[\x00-\x1f<>:"|?*\\/]' -or $Name -match '[ .]$' -or
        $Name -match '^(?i:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\.|$)') {
        throw 'A path contains an unsafe or ambiguous Windows component.'
    }
}

function Get-AbsoluteLocalPath([string]$Path) {
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Length -gt 1024) {
        throw 'Only absolute local drive-letter paths are accepted.'
    }
    $tail = $Path.Substring(3).TrimEnd('\')
    if ($tail.Length -gt 0) {
        foreach ($part in $tail.Split('\')) { Assert-Component $part }
    }
    return [IO.Path]::GetFullPath($Path)
}

function Assert-PlainPath([string]$Path, [bool]$Directory, [bool]$AllowMissing = $false) {
    # Check every existing component, including the drive and test root. This
    # rejects junctions and other reparse points as well as symbolic links.
    $full = Get-AbsoluteLocalPath $Path
    $root = [IO.Path]::GetPathRoot($full)
    $parts = @($full.Substring($root.Length).TrimEnd('\').Split('\') | Where-Object { $_ -ne '' })
    $cursor = $root
    for ($index = -1; $index -lt $parts.Count; $index++) {
        if ($index -ge 0) { $cursor = [IO.Path]::Combine($cursor, $parts[$index]) }
        try {
            $attributes = [IO.File]::GetAttributes($cursor)
        } catch [IO.FileNotFoundException] {
            if ($AllowMissing -and $index -ge 0) { return $false }
            throw
        } catch [IO.DirectoryNotFoundException] {
            if ($AllowMissing -and $index -ge 0) { return $false }
            throw
        }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse point or symbolic link refused: $cursor"
        }
        $isDirectory = ($attributes -band [IO.FileAttributes]::Directory) -ne 0
        if ($index -lt $parts.Count - 1 -and -not $isDirectory) {
            throw "An ancestor is not a directory: $cursor"
        }
        if ($index -eq $parts.Count - 1 -and $isDirectory -ne $Directory) {
            throw "Unexpected file or directory type: $cursor"
        }
    }
    return $true
}

function Get-SafeRelativePath([object]$Value) {
    if ($Value -isnot [string] -or $Value.Length -eq 0 -or $Value.Length -gt 200 -or
        $Value.Contains('\') -or $Value.StartsWith('/') -or $Value.EndsWith('/')) {
        throw 'Manifest paths must be bounded relative paths using forward slashes.'
    }
    foreach ($part in $Value.Split('/')) { Assert-Component $part }
    return $Value.Replace('/', '\')
}

function Get-Hex([byte[]]$Bytes) {
    return [BitConverter]::ToString($Bytes).Replace('-', '').ToLowerInvariant()
}

function Get-Serial([string]$Value) {
    if ($Value -notmatch '^[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}$') {
        throw 'Expected volume serial must contain eight hexadecimal digits.'
    }
    return $Value.Replace('-', '').ToUpperInvariant()
}

function Save-LocalReport([string]$Json) {
    $places = @([Environment]::GetFolderPath('Desktop'), [IO.Path]::GetTempPath())
    foreach ($place in $places) {
        try {
            $folder = Get-AbsoluteLocalPath $place
            [void](Assert-PlainPath $folder $true)
            $outputDrive = [IO.Path]::GetPathRoot($folder).Substring(0, 2).ToUpperInvariant()
            $localDisk = Get-LocalLogicalDisk $outputDrive
            if ($outputDrive -eq $Drive -or $localDisk.DriveType -ne 3 -or
                ($null -ne $disk -and $localDisk.VolumeSerialNumber -eq $disk.VolumeSerialNumber)) {
                continue
            }
            $name = 'Nativol-Windows-Result-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') +
                '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8) + '.json'
            $destination = [IO.Path]::Combine($folder, $name)
            $stream = [IO.File]::Open($destination, [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Json + [Environment]::NewLine)
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush()
            } finally { $stream.Dispose() }
            return $destination
        } catch {
            # Try the existing local temp directory if Desktop is unavailable,
            # redirected to a share, or passes through a reparse point.
            continue
        }
    }
    throw 'Could not save a report on an existing local fixed disk outside the tested volume.'
}

try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this script on Windows.' }
    $disk = Get-LocalLogicalDisk $Drive
    if ($disk.DriveType -notin @(2, 3) -or $disk.FileSystem -cne 'NTFS') {
        throw 'The selected drive must be a local NTFS volume.'
    }
    [void](Assert-PlainPath $driveRoot $true)
    $report.fileSystem = $disk.FileSystem
    $report.volumeSerial = Get-Serial ([string]$disk.VolumeSerialNumber)
    $report.volumeLabel = [string]$disk.VolumeName
    if ([string]::IsNullOrEmpty($ManifestPath)) {
        $ManifestPath = [IO.Path]::Combine($driveRoot, 'Nativol-Windows-Check.json')
    }
    $ManifestPath = Get-AbsoluteLocalPath $ManifestPath
    $manifestDisk = Get-LocalLogicalDisk ([IO.Path]::GetPathRoot($ManifestPath).Substring(0, 2).ToUpperInvariant())
    if ($manifestDisk.DriveType -notin @(2, 3)) { throw 'Manifest must be on a local disk.' }
    [void](Assert-PlainPath $ManifestPath $false)
    $stream = [IO.File]::Open($ManifestPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($stream.Length -le 0 -or $stream.Length -gt $maxManifestBytes) {
            throw 'Manifest must contain between 1 and 65536 bytes.'
        }
        $length = [int]$stream.Length
        $bytes = New-Object byte[] $length
        $offset = 0
        while ($offset -lt $length) {
            $count = $stream.Read($bytes, $offset, $length - $offset)
            if ($count -le 0) { throw 'Manifest ended unexpectedly.' }
            $offset += $count
        }
        if ($stream.ReadByte() -ne -1 -or $stream.Length -ne $length) { throw 'Manifest changed while reading.' }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $report.manifestSHA256 = Get-Hex ($sha.ComputeHash($bytes)) } finally { $sha.Dispose() }
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes).TrimStart([char]0xFEFF)
        $manifest = ConvertFrom-Json -InputObject $text
    } finally { $stream.Dispose() }
    [void](Assert-PlainPath $ManifestPath $false)
    if ($null -eq $manifest -or $manifest -is [array]) { throw 'Manifest must be one JSON object.' }
    foreach ($required in @('schemaVersion', 'targetKind', 'testRoot', 'files', 'absent')) {
        if ($manifest.PSObject.Properties.Name -notcontains $required) { throw "Missing manifest field: $required" }
    }
    if (($manifest.schemaVersion -isnot [int] -and $manifest.schemaVersion -isnot [long]) -or
        $manifest.schemaVersion -ne 1 -or $manifest.targetKind -cne 'physical-ntfs-validation' -or
        $manifest.testRoot -isnot [string] -or $manifest.testRoot -cnotmatch '^Nativol-Check-[0-9a-f]{64}$') {
        throw 'Manifest schema, target kind, or unique test root is invalid.'
    }
    if ($manifest.files -isnot [array] -or $manifest.files.Count -lt 1 -or $manifest.files.Count -gt 128 -or
        $manifest.absent -isnot [array] -or $manifest.absent.Count -gt 128) {
        throw 'Manifest must contain 1-128 files and an absence array of at most 128 paths.'
    }
    $serialExpected = ''
    if ($manifest.PSObject.Properties.Name -contains 'expectedVolumeSerial') {
        $serialExpected = Get-Serial ([string]$manifest.expectedVolumeSerial)
    }
    if (-not [string]::IsNullOrEmpty($ExpectedVolumeSerial)) {
        $parameterSerial = Get-Serial $ExpectedVolumeSerial
        if ($serialExpected -ne '' -and $parameterSerial -ne $serialExpected) {
            throw 'Parameter and manifest expected volume serials disagree.'
        }
        $serialExpected = $parameterSerial
    }
    $report.volumeSerialCompared = ($serialExpected -ne '')
    if ($serialExpected -ne '' -and $report.volumeSerial -ne $serialExpected) {
        throw 'The NTFS volume serial does not match the expected card.'
    }
    $testRoot = [IO.Path]::Combine($driveRoot, $manifest.testRoot)
    [void](Assert-PlainPath $testRoot $true)
    $report.testRoot = $manifest.testRoot
    $paths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $plannedFiles = @()
    $plannedAbsent = @()
    [long]$totalBytes = 0
    foreach ($file in $manifest.files) {
        foreach ($required in @('path', 'size', 'sha256')) {
            if ($null -eq $file -or $file.PSObject.Properties.Name -notcontains $required) {
                throw "Missing file field: $required"
            }
        }
        $relative = Get-SafeRelativePath $file.path
        if (-not $paths.Add($relative)) { throw 'Manifest contains case-insensitive duplicate paths.' }
        if (($file.size -isnot [int] -and $file.size -isnot [long]) -or $file.size -lt 0 -or
            $file.size -gt 67108864 -or $file.sha256 -isnot [string] -or $file.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
            throw 'A file size or SHA-256 digest is invalid.'
        }
        $totalBytes += [long]$file.size
        if ($totalBytes -gt 134217728) { throw 'Manifest exceeds the 128 MiB fixture limit.' }
        $plannedFiles += [pscustomobject]@{ relative = $relative; record = $file }
    }
    foreach ($name in $manifest.absent) {
        $relative = Get-SafeRelativePath $name
        if (-not $paths.Add($relative)) { throw 'Manifest contains duplicate file or absence paths.' }
        $plannedAbsent += [pscustomobject]@{ relative = $relative; original = $name }
    }
    foreach ($entry in $plannedFiles) {
        $file = $entry.record
        $path = [IO.Path]::Combine($testRoot, $entry.relative)
        [void](Assert-PlainPath $path $false)
        $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            if ($stream.Length -ne [long]$file.size) { throw "File size mismatch: $($file.path)" }
            [void](Assert-PlainPath $path $false)
            $actual = (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($stream.Length -ne [long]$file.size -or $actual -cne $file.sha256.ToLowerInvariant()) {
                throw "File checksum mismatch: $($file.path)"
            }
            [void](Assert-PlainPath $path $false)
        } finally { $stream.Dispose() }
        $report.files += [ordered]@{ path = $file.path; size = [long]$file.size; sha256 = $actual; status = 'passed' }
    }
    foreach ($entry in $plannedAbsent) {
        $path = [IO.Path]::Combine($testRoot, $entry.relative)
        # Existing files and directories both fail an expected-absence check.
        # A type mismatch also fails instead of being treated as absence.
        if (Assert-PlainPath $path $false $true) { throw "Expected absent path still exists: $($entry.original)" }
        $report.absent += [ordered]@{ path = $entry.original; status = 'passed' }
    }
    [void](Assert-PlainPath $testRoot $true)
    $after = Get-LocalLogicalDisk $Drive
    if ($after.FileSystem -cne 'NTFS' -or (Get-Serial ([string]$after.VolumeSerialNumber)) -ne $report.volumeSerial) {
        throw 'The drive identity changed during verification.'
    }
    $report.filesVerified = $report.files.Count
    $report.absencesVerified = $report.absent.Count
    $report.status = 'passed'
} catch {
    $report.error = $_.Exception.Message
}
$report.completedAt = [DateTime]::UtcNow.ToString('o')
$json = $report | ConvertTo-Json -Depth 8
try {
    $saved = Save-LocalReport $json
    Write-Host "Saved local result: $saved"
} catch {
    $report.status = 'failed'
    $report.reportSaveError = $_.Exception.Message
    $json = $report | ConvertTo-Json -Depth 8
}
Write-Output $json
if ($report.status -eq 'passed') { exit 0 }
exit 1
