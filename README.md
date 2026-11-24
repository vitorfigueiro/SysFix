# SysFix SaaS 2.0

Sistema web multi-empresa para gestão de coletas, ordens de serviço, entregas de equipamentos, indicadores gerenciais e relatórios em PDF.

Funciona tanto em **Modo Local** (armazenamento em JSON sem depender de banco externo) quanto em **Modo Firebase Cloud** (**Firebase Authentication + Cloud Firestore**), pronto para uso interno ou comercialização como SaaS.

## Principais Recursos

- **Login & Multi-empresa (SaaS):** cada empresa possui seus próprios registros, localizações configuráveis, prazo de SLA e equipe com isolamento estrito (`tenant_id`).
- **Controle de Acesso (RBAC):** perfis `Administrador` (configurações da empresa, gestão de colaboradores e exclusão de registros) e `Técnico` (registro de entradas, atualizações e saídas).
- **Dashboard Gerencial:** KPIs em tempo real (pendentes, atrasados acima do SLA, entregues, taxa de resolução, tempo médio de reparo e custos), evolução dos últimos 6 meses, distribuição por localização, ranking de técnicos, principais origens e alertas clicáveis de atraso.
- **Operação Rápida:** cadastro em lote atômico, busca em tempo real por ID/equipamento/tombamento/O.S./origem/técnico, autocompletar de técnicos e origens, ficha técnica detalhada e geração de relatórios PDF (mensal, pendentes e geral) com opção de anonimização LGPD.
- **Pronto para Firebase:** suporte completo ao Firebase Auth (incluindo redefinição de senha) e Cloud Firestore com transações atômicas de ID sequencial por empresa.

## Como Executar Localmente (Windows)

Com Python 3.10+ instalado, execute:

```powershell
.\iniciar_sysfix.ps1
```

Ou diretamente pela `.venv`:

```powershell
.\.venv\Scripts\python.exe app.py
```

Acesse **`http://localhost:8000`** no navegador.

- Sem arquivo `.env` configurado, o sistema inicia automaticamente em **Modo Local** (`coletas.json` e `auth_db.json` na pasta de dados).
- Se já existir um `coletas.json` legado sem `tenant_id`, a primeira empresa cadastrada no modo local herda automaticamente esses registros.

## Ativar Modo Firebase (Nuvem / Comercialização)

Consulte o passo a passo completo em [`FIREBASE_SETUP.md`](FIREBASE_SETUP.md) e use [`SysFix/.env.example`](.env.example) como base para criar o arquivo `.env`.

## Executar Testes Automatizados

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

## Exposição em Rede Local no Windows

Para liberar o acesso somente a endereços IPv4 específicos via Firewall do Windows, execute o PowerShell como administrador:

```powershell
.\iniciar_sysfix.ps1 -ListenOnNetwork -AllowedClientCidrs "177.129.124.27/32" -DataDirectory "C:\SysFix\Data"
```

> **Importante:** em ambiente comercial na internet, publique o sistema atrás de HTTPS (Cloud Run, Render, Railway ou proxy reverso Nginx/Caddy com TLS).
