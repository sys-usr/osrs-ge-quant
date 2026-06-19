# setup_maven.ps1
$workingDir = "c:\Users\londo\OneDrive\Desktop\osrs-ge-quant"
Set-Location $workingDir

$mavenDir = Join-Path $workingDir "maven"

# Clean up any failed run
if (Test-Path $mavenDir) {
    # If already exists, do nothing
    Write-Host "[Maven Setup] Portable Maven already exists at $mavenDir"
    exit 0
}

Write-Host "[Maven Setup] Downloading Apache Maven 3.9.6..."
$url = "https://archive.apache.org/dist/maven/maven-3/3.9.6/binaries/apache-maven-3.9.6-bin.zip"
$zipPath = Join-Path $workingDir "maven.zip"

try {
    Invoke-WebRequest -Uri $url -OutFile $zipPath
    Write-Host "[Maven Setup] Unzipping Apache Maven..."
    Expand-Archive -Path $zipPath -DestinationPath $workingDir
    
    $extractedDir = Join-Path $workingDir "apache-maven-3.9.6"
    if (Test-Path $extractedDir) {
        Rename-Item $extractedDir "maven"
        Write-Host "[Maven Setup] Portable Maven installed successfully!"
    } else {
        Write-Host "[Maven Setup] Error: Extracted directory not found."
    }
} catch {
    Write-Host "[Maven Setup] Exception occurred during download/unzip: $_"
} finally {
    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force
    }
}
