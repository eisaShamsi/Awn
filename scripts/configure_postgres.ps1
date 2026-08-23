[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = [System.Security.Principal.WindowsPrincipal]::new($currentIdentity)
$administratorRole = [System.Security.Principal.WindowsBuiltInRole]::Administrator
if (-not $currentPrincipal.IsInRole($administratorRole)) {
    $windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Start-Process -FilePath $windowsPowerShell -Verb RunAs -ArgumentList $arguments
    exit 0
}

$postgresRoot = "C:\Program Files\PostgreSQL\18"
$psql = Join-Path $postgresRoot "bin\psql.exe"
$createdb = Join-Path $postgresRoot "bin\createdb.exe"
$serviceName = "postgresql-x64-18"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$environmentFile = Join-Path $repositoryRoot ".env"
$statusDirectory = Join-Path $repositoryRoot "data"
$statusFile = Join-Path $statusDirectory "postgres-setup-status.txt"
$venvAlembic = Join-Path $repositoryRoot ".venv\Scripts\alembic.exe"
$phase = "initialization"

[System.IO.Directory]::CreateDirectory($statusDirectory) | Out-Null
[System.IO.File]::WriteAllText($statusFile, "RUNNING: $phase")

trap {
    $exceptionType = $_.Exception.GetType().FullName
    [System.IO.File]::WriteAllText($statusFile, "FAILED: $phase ($exceptionType)")
    Write-Host ""
    Write-Host "Setup failed during: $phase" -ForegroundColor Red
    Write-Host "No password was written to the status file."
    Read-Host "Press Enter to close this window"
    exit 1
}

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Action)

    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Psql {
    param(
        [Parameter(Mandatory)][string]$Database,
        [Parameter(Mandatory)][string]$Sql,
        [switch]$Quiet
    )

    $arguments = @(
        "--host=localhost",
        "--port=5432",
        "--username=postgres",
        "--dbname=$Database",
        "--no-psqlrc",
        "--set=ON_ERROR_STOP=1"
    )
    if ($Quiet) {
        $arguments += "--quiet"
    }

    $Sql | & $psql @arguments
    Assert-LastExitCode "PostgreSQL command"
}

if (-not (Test-Path -LiteralPath $psql)) {
    throw "PostgreSQL 18 was not found at $postgresRoot."
}

if (-not (Test-Path -LiteralPath $venvAlembic)) {
    throw "Awn virtual environment was not found at $venvAlembic."
}

Write-Host "Awn PostgreSQL secure setup" -ForegroundColor Cyan
Write-Host "The password stays inside this local PowerShell process and is never printed."

$phase = "PostgreSQL administrator authentication"
$authenticated = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    $secureAdminPassword = Read-Host "Enter the PostgreSQL 'postgres' password" -AsSecureString
    $adminCredential = [System.Net.NetworkCredential]::new("postgres", $secureAdminPassword)
    $env:PGPASSWORD = $adminCredential.Password

    & $psql --host=localhost --port=5432 --username=postgres --dbname=postgres `
        --no-psqlrc --tuples-only --no-align --command="SELECT 1" *> $null
    if ($LASTEXITCODE -eq 0) {
        $authenticated = $true
        break
    }

    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Write-Warning "Authentication failed. Try again ($attempt/3)."
}

if (-not $authenticated) {
    throw "Could not authenticate after three attempts. No Awn database changes were made."
}

try {
    $phase = "application credential generation"
    $randomBytes = [byte[]]::new(32)
    $randomGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $randomGenerator.GetBytes($randomBytes)
    }
    finally {
        $randomGenerator.Dispose()
    }
    $appPassword = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")

    $phase = "application role creation"
    $roleSql = @"
SELECT 'CREATE ROLE awn_app LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'awn_app')
\gexec
ALTER ROLE awn_app WITH
    LOGIN
    PASSWORD '$appPassword'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION;
"@
    Invoke-Psql -Database "postgres" -Sql $roleSql -Quiet

    $phase = "application database creation"
    $databaseExists = & $psql --host=localhost --port=5432 --username=postgres `
        --dbname=postgres --no-psqlrc --tuples-only --no-align `
        --command="SELECT 1 FROM pg_database WHERE datname = 'awn'"
    Assert-LastExitCode "Database existence check"

    if (($databaseExists | Out-String).Trim() -ne "1") {
        & $createdb --host=localhost --port=5432 --username=postgres `
            --owner=awn_app --encoding=UTF8 --template=template0 awn
        Assert-LastExitCode "Awn database creation"
    }

    $databaseSql = @"
ALTER DATABASE awn OWNER TO awn_app;
REVOKE ALL ON DATABASE awn FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE awn TO awn_app;
"@
    Invoke-Psql -Database "postgres" -Sql $databaseSql -Quiet

    $schemaSql = @"
ALTER SCHEMA public OWNER TO awn_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO awn_app;
"@
    Invoke-Psql -Database "awn" -Sql $schemaSql -Quiet

    $phase = "local network hardening"
    $hardeningSql = @"
ALTER SYSTEM SET listen_addresses = 'localhost';
ALTER SYSTEM SET password_encryption = 'scram-sha-256';
"@
    Invoke-Psql -Database "postgres" -Sql $hardeningSql -Quiet

    Restart-Service -Name $serviceName
    $service = Get-Service -Name $serviceName
    $service.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(30))

    $phase = "local environment configuration"
    $databaseUrl = "postgresql+psycopg://awn_app:$appPassword@localhost:5432/awn"
    $environmentLines = @(
        "AWN_ENVIRONMENT=local",
        "AWN_MODEL_PROVIDER=fake",
        "AWN_OPENAI_MODEL=",
        "AWN_OPENAI_API_KEY=",
        "AWN_DATABASE_URL=$databaseUrl",
        "AWN_TEST_POSTGRES_URL=$databaseUrl"
    )
    [System.IO.File]::WriteAllLines($environmentFile, $environmentLines, [System.Text.UTF8Encoding]::new($false))

    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $environmentFile /inheritance:r /grant:r `
        "*$($currentSid):(R,W)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" *> $null
    Assert-LastExitCode "Environment file permission update"

    $phase = "Alembic migrations"
    $env:AWN_DATABASE_URL = $databaseUrl
    Push-Location $repositoryRoot
    try {
        & $venvAlembic upgrade head
        Assert-LastExitCode "Database migration"
        & $venvAlembic check
        Assert-LastExitCode "Database migration check"
    }
    finally {
        Pop-Location
    }

    $env:PGPASSWORD = $appPassword
    & $psql --host=localhost --port=5432 --username=awn_app --dbname=awn `
        --no-psqlrc --tuples-only --no-align --command="SELECT current_user" | Out-Null
    Assert-LastExitCode "Awn application connection check"

    $phase = "complete"
    [System.IO.File]::WriteAllText($statusFile, "SUCCESS")
    Write-Host ""
    Write-Host "PostgreSQL is configured securely for Awn." -ForegroundColor Green
    Write-Host "- Network access: localhost only"
    Write-Host "- Authentication: SCRAM-SHA-256"
    Write-Host "- Application role: awn_app (limited privileges)"
    Write-Host "- Database: awn"
    Write-Host "- Local configuration: $environmentFile (Git-ignored, restricted ACL)"
    Write-Host "- Alembic migrations: current"
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:AWN_DATABASE_URL -ErrorAction SilentlyContinue
    if (Get-Variable -Name adminCredential -ErrorAction SilentlyContinue) {
        $adminCredential.Password = ""
    }
    if (Get-Variable -Name appPassword -ErrorAction SilentlyContinue) {
        $appPassword = ""
    }
}

Write-Host ""
Read-Host "Press Enter to close this window"
