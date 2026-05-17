param(
    [Parameter(Mandatory = $true)]
    [string]$EventName,

    [string]$ProjectName = "all",
    [string]$BeforeSha = "",
    [string]$CurrentSha = "",
    [string]$CatalogPath = "projects.json"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CatalogPath -PathType Leaf)) {
    throw "Project catalog not found: $CatalogPath"
}

$projects = @(Get-Content -LiteralPath $CatalogPath -Raw | ConvertFrom-Json)
if ($projects.Count -eq 0) {
    throw "Project catalog is empty: $CatalogPath"
}

function Select-ProjectByName {
    param(
        [object[]]$Catalog,
        [string]$Name
    )

    $matched = @($Catalog | Where-Object { $_.name -eq $Name -or $_.folder -eq $Name })
    if ($matched.Count -eq 0) {
        $available = ($Catalog | ForEach-Object { $_.name }) -join ", "
        throw "Unknown project '$Name'. Available projects: $available"
    }

    return $matched
}

if ($EventName -eq "workflow_dispatch") {
    if ([string]::IsNullOrWhiteSpace($ProjectName) -or $ProjectName -eq "all") {
        $selectedProjects = $projects
    }
    else {
        $selectedProjects = Select-ProjectByName -Catalog $projects -Name $ProjectName
    }
}
else {
    if ([string]::IsNullOrWhiteSpace($CurrentSha)) {
        throw "CurrentSha is required for event '$EventName'"
    }

    if (-not [string]::IsNullOrWhiteSpace($BeforeSha) -and $BeforeSha -notmatch "^0+$") {
        $changedFiles = @(git -c core.quotePath=false diff --name-only $BeforeSha $CurrentSha)
    }
    else {
        $changedFiles = @(git -c core.quotePath=false diff-tree --no-commit-id --name-only -r $CurrentSha)
    }

    $changedFolders = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $deployAll = $false
    foreach ($file in $changedFiles) {
        $normalized = $file -replace "\\", "/"
        $folder = ($normalized -split "/")[0]
        if (-not [string]::IsNullOrWhiteSpace($folder)) {
            [void]$changedFolders.Add($folder)
        }

        if (
            $normalized -eq "projects.json" -or
            $normalized.StartsWith(".github/", [System.StringComparison]::OrdinalIgnoreCase) -or
            $normalized.StartsWith("scripts/", [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            $deployAll = $true
        }
    }

    if ($deployAll) {
        $selectedProjects = $projects
    }
    else {
        $selectedProjects = @(
            $projects | Where-Object {
                $changedFolders.Contains([string]$_.folder)
            }
        )
    }

    Write-Host "Changed files:"
    $changedFiles | ForEach-Object { Write-Host " - $_" }

    if ($deployAll) {
        Write-Host "Deployment config changed; all projects will be deployed."
    }
    else {
        Write-Host "Changed top-level folders:"
        $changedFolders | ForEach-Object { Write-Host " - $_" }
    }
}

$projectList = @(
    $selectedProjects | ForEach-Object {
        [pscustomobject]@{
            name = $_.name
            folder = $_.folder
            modelFile = $_.modelFile
            pbixFile = $_.pbixFile
            modelName = $_.modelName
            reportDisplayName = $_.reportDisplayName
        }
    }
)

if ($projectList.Count -eq 0) {
    Write-Output "[]"
    exit 0
}

ConvertTo-Json -InputObject $projectList -Compress -Depth 5
