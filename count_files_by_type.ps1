param (
    [string]$Path = "D:\movies"
)

if (-not (Test-Path $Path)) {
    Write-Error "Path '$Path' does not exist."
    exit 1
}

Write-Host "Scanning '$Path'... This may take a while depending on the number of files." -ForegroundColor Cyan

# Get all files recursively
$files = Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue

if ($files.Count -eq 0) {
    Write-Warning "No files found in '$Path'."
    exit
}

# Group by extension and calculate stats
$stats = $files | Group-Object Extension | Select-Object `
    @{N='Extension';E={if ([string]::IsNullOrWhiteSpace($_.Name)) { "<No Extension>" } else { $_.Name }}}, `
    Count, `
    @{N='TotalBytes';E={($_.Group | Measure-Object -Property Length -Sum).Sum}}

# Sort by size descending and format output
$stats | Sort-Object TotalBytes -Descending | Select-Object `
    Extension, `
    Count, `
    @{N='Total Size';E={
        $bytes = $_.TotalBytes
        if ($bytes -gt 1TB) { "{0:N2} TB" -f ($bytes / 1TB) }
        elseif ($bytes -gt 1GB) { "{0:N2} GB" -f ($bytes / 1GB) }
        elseif ($bytes -gt 1MB) { "{0:N2} MB" -f ($bytes / 1MB) }
        elseif ($bytes -gt 1KB) { "{0:N2} KB" -f ($bytes / 1KB) }
        else { "{0:N0} Bytes" -f $bytes }
    }} | Format-Table -AutoSize

# Print total summary
$totalCount = $files.Count
$totalSize = ($files | Measure-Object -Property Length -Sum).Sum
$formattedTotalSize = if ($totalSize -gt 1TB) { "{0:N2} TB" -f ($totalSize / 1TB) }
                      elseif ($totalSize -gt 1GB) { "{0:N2} GB" -f ($totalSize / 1GB) }
                      else { "{0:N2} MB" -f ($totalSize / 1MB) }

Write-Host "`nTotal: $totalCount files, $formattedTotalSize" -ForegroundColor Green

