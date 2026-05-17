param(
    [Parameter(Mandatory = $true)]
    [string]$ModelFile,

    [Parameter(Mandatory = $true)]
    [string]$ModelName,

    [string]$TabularEditorPath = $env:TE2_PATH,
    [string]$XmlaServer = $env:PBI_XMLA_SERVER,
    [string]$TenantId = $env:PBI_TENANT_ID,
    [string]$ClientId = $env:PBI_CLIENT_ID,
    [string]$ClientSecret = $env:PBI_CLIENT_SECRET,

    [switch]$DeployConnections,
    [switch]$DeployRoleMembers
)

$ErrorActionPreference = "Stop"

function Require-Value {
    param(
        [string]$Name,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing required value: $Name"
    }
}

Require-Value "ModelFile" $ModelFile
Require-Value "ModelName" $ModelName
Require-Value "TE2_PATH or -TabularEditorPath" $TabularEditorPath
Require-Value "PBI_XMLA_SERVER or -XmlaServer" $XmlaServer
Require-Value "PBI_TENANT_ID or -TenantId" $TenantId
Require-Value "PBI_CLIENT_ID or -ClientId" $ClientId
Require-Value "PBI_CLIENT_SECRET or -ClientSecret" $ClientSecret

$resolvedModelFile = Resolve-Path -LiteralPath $ModelFile
if (-not (Test-Path -LiteralPath $TabularEditorPath -PathType Leaf)) {
    throw "Tabular Editor was not found at: $TabularEditorPath"
}

$spUser = "app:$ClientId@$TenantId"
$deployOptions = @("-O", "-SHARED", "-R", "-E", "-W")

if ($DeployConnections) {
    $deployOptions += "-C"
}

if ($DeployRoleMembers) {
    $deployOptions += "-M"
}

$arguments = @(
    $resolvedModelFile.Path,
    "-D",
    $XmlaServer,
    $ModelName
) + $deployOptions + @(
    "-L",
    $spUser,
    $ClientSecret
)

Write-Host "Deploying BIM model with Tabular Editor 2..."
Write-Host "Model file: $($resolvedModelFile.Path)"
Write-Host "XMLA server: $XmlaServer"
Write-Host "Target model: $ModelName"
Write-Host "Deploy options: $($deployOptions -join ' ')"

$process = Start-Process `
    -FilePath $TabularEditorPath `
    -ArgumentList $arguments `
    -Wait `
    -NoNewWindow `
    -PassThru

if ($process.ExitCode -ne 0) {
    throw "Tabular Editor deployment failed. ExitCode=$($process.ExitCode)"
}

Write-Host "Tabular Editor deployment completed."

