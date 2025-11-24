param (
    [string]$Source = "D:\movies",
    [string]$Destination = "E:\movies",
    [switch]$Force
)

# Define allowed extensions
# Video formats requested: mkv, mp4, avi, mpeg, divx, m4v
# Subtitle formats requested: srt and related
$AllowedExtensions = @(
    # Video
    ".mkv", ".mp4", ".avi", ".mpeg", ".mpg", ".divx", ".m4v",
    # Subtitles
    ".srt", ".sub", ".idx", ".ass", ".ssa", ".smi", ".vtt"
)

Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "Source:      $Source"
Write-Host "Destination: $Destination"
Write-Host "Extensions:  $($AllowedExtensions -join ', ')"
Write-Host ""

if (-not (Test-Path $Source)) {
    Write-Error "Source path '$Source' does not exist."
    exit 1
}

# Get all files recursively that match the extensions
Write-Host "Scanning source directory..." -ForegroundColor Cyan
$allFiles = Get-ChildItem -Path $Source -Recurse -File | Where-Object {
    $AllowedExtensions -contains $_.Extension.ToLower()
}

if ($allFiles.Count -eq 0) {
    Write-Warning "No files matching the criteria found in '$Source'."
    exit
}

Write-Host "Found $($allFiles.Count) files. Checking for existing files..." -ForegroundColor Cyan

# Filter files that actually need copying
$filesToCopy = @()
$skippedCount = 0
$totalBytesToCopy = 0

foreach ($file in $allFiles) {
    $escapedSource = [regex]::Escape($Source)
    $relativePath = $file.FullName -replace "^$escapedSource\\", ""
    $destPath = Join-Path -Path $Destination -ChildPath $relativePath
    
    $shouldCopy = $true
    
    if ((Test-Path $destPath) -and (-not $Force)) {
        $srcInfo = $file
        $destInfo = Get-Item $destPath
        
        # Resume Logic: Skip if size and modify time match
        # We check Length first (fastest), then LastWriteTime
        if ($srcInfo.Length -eq $destInfo.Length -and 
            $srcInfo.LastWriteTime.ToString('yyyyMMddHHmmss') -eq $destInfo.LastWriteTime.ToString('yyyyMMddHHmmss')) {
            $shouldCopy = $false
            $skippedCount++
        }
    }
    
    if ($shouldCopy) {
        # Using -LiteralPath here just to be safe when checking existence later
        $filesToCopy += @{
            Source = $file
            DestPath = $destPath
            RelativePath = $relativePath
        }
        $totalBytesToCopy += $file.Length
    } else {
        # Even if we don't copy, we track that we skipped it for summary
        $skippedCount++
    }
}

$filesToCopyCount = $filesToCopy.Count

if ($filesToCopyCount -eq 0) {
    Write-Host "All $skippedCount files already exist at destination. Nothing to do!" -ForegroundColor Green
    exit
}

Write-Host "Resuming: Skipped $skippedCount files." -ForegroundColor Gray
Write-Host "Remaining: $filesToCopyCount files to copy ($("{0:N2} GB" -f ($totalBytesToCopy / 1GB)))." -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop at any time. The script can be resumed later." -ForegroundColor Gray

# Process only the needed files
$currentFileIndex = 0
$totalBytesCopied = 0
$failedFiles = 0
$startTime = Get-Date

foreach ($item in $filesToCopy) {
    $currentFileIndex++
    $file = $item.Source
    $destPath = $item.DestPath
    $relativePath = $item.RelativePath
    $destDir = Split-Path -Path $destPath -Parent
    $fileSize = $file.Length

    # Calculate time stats
    # We only count time elapsed since we started THIS batch of actual copies
    # This avoids skewing speed with the fast "skipping" phase
    $timeElapsed = (Get-Date) - $startTime
    $secondsElapsed = $timeElapsed.TotalSeconds
    
    # Calculate speed (Bytes/sec) - avoid division by zero
    $speed = if ($secondsElapsed -gt 0) { $totalBytesCopied / $secondsElapsed } else { 0 }
    
    # Format Speed
    $speedStr = if ($speed -gt 1MB) { "{0:N2} MB/s" -f ($speed / 1MB) }
                elseif ($speed -gt 1KB) { "{0:N2} KB/s" -f ($speed / 1KB) }
                else { "{0:N0} B/s" -f $speed }

    # Calculate remaining bytes and time
    $bytesRemaining = $totalBytesToCopy - $totalBytesCopied
    $secondsRemaining = if ($speed -gt 0) { $bytesRemaining / $speed } else { 0 }
    $timeRemaining = New-TimeSpan -Seconds $secondsRemaining
    $timeRemainingStr = "{0:dd\.hh\:mm\:ss}" -f $timeRemaining

    # Calculate total progress percentage based on BYTES of PLANNED COPIES only
    $percentBytes = if ($totalBytesToCopy -gt 0) { [math]::Min(100, [math]::Round(($totalBytesCopied / $totalBytesToCopy) * 100, 1)) } else { 0 }
    
    # Format totals for display
    $totalCopiedStr = if ($totalBytesCopied -gt 1GB) { "{0:N2} GB" -f ($totalBytesCopied / 1GB) } else { "{0:N2} MB" -f ($totalBytesCopied / 1MB) }
    $totalTotalStr = if ($totalBytesToCopy -gt 1GB) { "{0:N2} GB" -f ($totalBytesToCopy / 1GB) } else { "{0:N2} MB" -f ($totalBytesToCopy / 1MB) }

    # Update Progress
    $statusMsg = "$percentBytes% Complete | Speed: $speedStr | ETA: $timeRemainingStr | $totalCopiedStr / $totalTotalStr"
    $currentOp = "File $currentFileIndex of $filesToCopyCount : $relativePath"
    
    Write-Progress -Activity "Copying Files" -Status $statusMsg -CurrentOperation $currentOp -PercentComplete $percentBytes

    try {
        # Create destination directory if it doesn't exist
        if (-not (Test-Path -LiteralPath $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }

        # Copy the file
        Copy-Item -LiteralPath $file.FullName -Destination $destPath -Force
        
        # Update accumulated bytes ONLY after successful copy
        $totalBytesCopied += $fileSize
    }
    catch {
        Write-Error "Failed to copy '$($file.FullName)': $_"
        $failedFiles++
    }
}

Write-Host "`nCopy Complete." -ForegroundColor Green
Write-Host "Total processed: $totalFiles"
Write-Host "Skipped (already exists): $skippedFiles"
if ($failedFiles -gt 0) {
    Write-Host "Failed: $failedFiles" -ForegroundColor Red
}

