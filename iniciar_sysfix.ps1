param(
    [switch]$ListenOnNetwork,
    [Alias("AllowedClientCidrs")]
    [string]$AllowedClientCidr,
    [string]$DataDirectory
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$venvDirectory = Join-Path $PSScriptRoot ".venv"
$python = Join-Path $venvDirectory "Scripts\python.exe"
$venvReady = $false

if (Test-Path -LiteralPath $python) {
    try {
        & $python -c "import sys" 2>$null
        $venvReady = $LASTEXITCODE -eq 0
    }
    catch {
        $venvReady = $false
    }
}

$createdVenv = $false
if (-not $venvReady) {
    if (Test-Path -LiteralPath $venvDirectory) {
        $backupName = ".venv-anterior-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        $backupPath = Join-Path $PSScriptRoot $backupName
        Move-Item -LiteralPath $venvDirectory -Destination $backupPath
        Write-Warning "O ambiente Python copiado não funciona neste computador e foi preservado em $backupPath."
    }

    $pythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        & $pythonLauncher.Source -3 -m venv $venvDirectory
    }
    else {
        $pythonLauncher = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if (-not $pythonLauncher) {
            throw "Python não encontrado neste servidor. Instale Python 3 e habilite o inicializador 'py' no PATH antes de iniciar o SysFix."
        }
        & $pythonLauncher.Source -m venv $venvDirectory
    }

    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) {
        throw "Não foi possível criar o ambiente Python do SysFix. Verifique a instalação do Python no servidor."
    }
    $createdVenv = $true
}

$missingDependencies = $false
try {
    & $python -c "import fastapi, pydantic, reportlab, uvicorn" 2>$null
    $missingDependencies = $LASTEXITCODE -ne 0
}
catch {
    $missingDependencies = $true
}

if ($createdVenv -or $missingDependencies) {
    Write-Host "Instalando as dependências do SysFix neste servidor..."
    & $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Não foi possível instalar as dependências. Confira o acesso à internet e tente novamente."
    }
}

if ($DataDirectory) {
    $env:SYSFIX_DATA_DIR = $DataDirectory
}
elseif (-not $env:SYSFIX_DATA_DIR) {
    $env:SYSFIX_DATA_DIR = Join-Path $env:LOCALAPPDATA "SysFix\data"
}

if (-not [System.IO.Path]::IsPathRooted($env:SYSFIX_DATA_DIR)) {
    throw "A pasta de dados precisa ser um caminho absoluto, por exemplo C:\SysFix\Data."
}
if ($env:SYSFIX_DATA_DIR.StartsWith("\\")) {
    throw "Os dados devem ficar em disco local do servidor, não em compartilhamento de rede."
}
New-Item -ItemType Directory -Force -Path $env:SYSFIX_DATA_DIR | Out-Null

$env:HOST = "127.0.0.1"
$env:PORT = "8000"
$firewallRuleName = "SysFix - TCP 8000 (restricted client network)"

if ($ListenOnNetwork) {
    if (-not $AllowedClientCidr) {
        $AllowedClientCidr = Read-Host "IP/CIDRs autorizados, separados por ponto e vírgula (ex.: 177.129.124.27/32;203.0.113.10/32)"
    }

    $allowedClientCidrs = @(
        $AllowedClientCidr -split '[;,]' |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    if ($allowedClientCidrs.Count -eq 0) {
        throw "Informe pelo menos um IP/CIDR autorizado."
    }

    foreach ($cidr in $allowedClientCidrs) {
        if ($cidr -notmatch '^(?<address>(?:\d{1,3}\.){3}\d{1,3})(?:/(?<prefix>\d{1,2}))?$') {
            throw "IP/CIDR IPv4 inválido: '$cidr'. Informe IPs separados por ponto e vírgula e não use 0.0.0.0/0."
        }

        $parsedAddress = $null
        if (-not [System.Net.IPAddress]::TryParse($Matches.address, [ref]$parsedAddress)) {
            throw "Endereço IPv4 inválido: $($Matches.address)"
        }
        if ($parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
            throw "Informe endereços IPv4 para as origens autorizadas."
        }
        if ($Matches.address -eq "0.0.0.0") {
            throw "Informe o IP de uma máquina ou uma rede autorizada; 0.0.0.0 não é permitido."
        }
        if ($Matches.prefix) {
            $prefixLength = [int]$Matches.prefix
            if ($prefixLength -lt 8 -or $prefixLength -gt 32) {
                throw "A máscara CIDR precisa estar entre /8 e /32. Não use uma faixa ampla que inclua a internet."
            }
        }
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Para expor na rede, abra o PowerShell como administrador e execute novamente."
    }

    Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    New-NetFirewallRule `
        -DisplayName $firewallRuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8000 `
        -RemoteAddress $allowedClientCidrs `
        -Profile Any | Out-Null

    $env:HOST = "0.0.0.0"
    Write-Host "Acesso TCP 8000 liberado no Firewall do Windows somente para: $($allowedClientCidrs -join ', ')."
    Write-Warning "Confirme que o firewall da DMZ/NAT também permite a porta TCP 8000 somente dessa origem. O acesso usa HTTP sem criptografia."
}

$definedAdminPasswordHere = $false
if (-not $env:ADMIN_PASSWORD -or -not $env:ADMIN_PASSWORD.Trim()) {
    $adminPassword = Read-Host -AsSecureString -Prompt "Defina a senha administrativa do SysFix"
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminPassword)
    try {
        $env:ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
        $definedAdminPasswordHere = $true
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
        $adminPassword.Dispose()
    }
}

if (-not $env:ADMIN_PASSWORD -or -not $env:ADMIN_PASSWORD.Trim()) {
    Write-Warning "Nenhuma senha administrativa definida. A exclusão de registros permanecerá desabilitada nesta execução."
}

try {
    Write-Host "Dados JSON: $env:SYSFIX_DATA_DIR"
    Write-Host "SysFix disponível nesta máquina em http://localhost:8000"
    if ($ListenOnNetwork) {
        Write-Host "Acesso externo: http://ENDERECO-EXTERNO:PORTA-PUBLICADA"
    }
    else {
        Write-Host "Somente esta máquina pode acessar o servidor. Use -ListenOnNetwork para liberar uma origem específica."
    }
    Write-Host "Mantenha esta janela aberta enquanto o sistema estiver em uso."
    & $python "app.py"
    if ($LASTEXITCODE -ne 0) {
        throw "O servidor SysFix terminou com código de erro $LASTEXITCODE."
    }
}
finally {
    if ($definedAdminPasswordHere) {
        Remove-Item Env:ADMIN_PASSWORD -ErrorAction SilentlyContinue
    }
}
