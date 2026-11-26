from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import html
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from config import get_data_dir, get_storage_backend
from security import SecurityValidator

_SEC_OPS_LOCK = threading.RLock()
_VALID_COLLECTIONS = {
    "automacao_ativos",
    "cyber_auditorias",
    "cyber_achados",
    "cyber_checklists",
    "cyber_kb",
    "cyber_automacoes",
}

AUTOMACAO_PILARES = {
    "cameras": "Câmeras & CFTV",
    "infraestrutura": "Infraestrutura & Racks",
    "iot": "Aparelhos IoT & Sensores",
    "alarmes": "Alarmes & Controle de Acesso",
}

AUTOMACAO_STATUS = {"Conforme", "Alerta", "Crítico"}

CYBER_SEVERIDADES = ("Crítica", "Alta", "Média", "Baixa", "Informativa")
CYBER_ACHADO_STATUS = ("Aberto", "Em Mitigação", "Resolvido", "Risco Aceito")

_PATCHBOT_SLA_POR_SEV: dict[str, tuple[str, float]] = {
    "Crítica": ("24 horas (Emergencial)", 9.1),
    "Alta": ("7 dias", 7.8),
    "Média": ("30 dias", 5.8),
    "Baixa": ("60 dias", 3.5),
    "Informativa": ("90 dias (Melhoria contínua)", 2.0),
}

_PATCHBOT_KB_REGRAS: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (
        ("hsts", "strict-transport-security"),
        {
            "cve_cwe": "CWE-319 (Cleartext Transmission)",
            "cvss_score": 6.5,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:L/A:N",
            "owasp": "OWASP A02:2021 — Cryptographic Failures",
            "cis_control": "CIS v8 3.10 (Encrypt Sensitive Data in Transit)",
            "mitre_attack": "T1557 — Adversary-in-the-Middle (SSL Stripping)",
            "descricao_tecnica": "O servidor não envia o cabeçalho HTTP Strict-Transport-Security (HSTS). Sem HSTS, o navegador permite a primeira conexão em HTTP puro, abrindo brecha para ataques de downgrade (SSL Stripping) em redes Wi-Fi públicas ou comprometidas.",
            "impacto": "Interceptação de cookies de sessão, credenciais ou redirecionamento malicioso de usuários antes da negociação HTTPS.",
            "passos_remediacao": [
                "Garanta que todo o domínio e subdomínios possuam certificado HTTPS válido e redirecionamento 301 de HTTP para HTTPS.",
                "Adicione o header no proxy/servidor web: Strict-Transport-Security: max-age=31536000; includeSubDomains; preload.",
                "Teste inicialmente com max-age=300 e depois suba para 31536000 (1 ano).",
                "Reexecute o Byte-Scanner para confirmar presença do header.",
            ],
            "comando_validacao": "curl -sI https://SEU_DOMINIO | findstr /I strict-transport-security",
        },
    ),
    (
        ("csp", "content-security-policy"),
        {
            "cve_cwe": "CWE-79 (XSS) / CWE-1021",
            "cvss_score": 6.1,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N",
            "owasp": "OWASP A03:2021 — Injection / A05:2021 Security Misconfiguration",
            "cis_control": "CIS v8 16.11 (Application Security Hardening)",
            "mitre_attack": "T1059.007 — JavaScript Execution / Magecart",
            "descricao_tecnica": "Ausência do cabeçalho Content-Security-Policy (CSP), responsável por restringir de quais origens o navegador pode carregar scripts, estilos, imagens e frames.",
            "impacto": "Se existir qualquer ponto de injeção XSS ou script de terceiro comprometido, o navegador executará código malicioso sem barreira, permitindo roubo de tokens e alteração da página.",
            "passos_remediacao": [
                "Mapeie os domínios legítimos de scripts, fontes, estilos e APIs usados pelo sistema.",
                "Ative inicialmente em modo monitoramento: Content-Security-Policy-Report-Only: default-src 'self'; object-src 'none'; frame-ancestors 'self'.",
                "Após validar ausência de bloqueios legítimos, aplique o header Content-Security-Policy em modo enforce.",
                "Evite 'unsafe-inline' e 'unsafe-eval' sempre que possível usando nonces/hashes.",
            ],
            "comando_validacao": "curl -sI https://SEU_DOMINIO | findstr /I content-security-policy",
        },
    ),
    (
        ("x-content-type-options", "nosniff"),
        {
            "cve_cwe": "CWE-16 (Configuration) / CWE-434",
            "cvss_score": 4.3,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:N/A:N",
            "owasp": "OWASP A05:2021 — Security Misconfiguration",
            "cis_control": "CIS v8 4.1 (Secure Configuration Process)",
            "mitre_attack": "T1189 — Drive-by Compromise (MIME Sniffing)",
            "descricao_tecnica": "O cabeçalho X-Content-Type-Options: nosniff não está configurado. Navegadores podem tentar adivinhar (MIME sniffing) o tipo de conteúdo e interpretar arquivos de texto/imagem como scripts executáveis.",
            "impacto": "Facilita ataques de XSS via upload de arquivos ou respostas mal tipadas.",
            "passos_remediacao": [
                "Configure no Nginx: add_header X-Content-Type-Options \"nosniff\" always;",
                "Ou no Apache: Header always set X-Content-Type-Options \"nosniff\"",
                "Valide que arquivos estáticos (.js, .css, .png) sejam servidos com Content-Type correto.",
            ],
            "comando_validacao": "curl -sI https://SEU_DOMINIO | findstr /I x-content-type-options",
        },
    ),
    (
        ("x-frame-options", "clickjacking"),
        {
            "cve_cwe": "CWE-1021 (Improper Restriction of Rendered UI Layers)",
            "cvss_score": 4.7,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:N/I:L/A:N",
            "owasp": "OWASP A05:2021 — Security Misconfiguration",
            "cis_control": "CIS v8 16.11 (Application Hardening)",
            "mitre_attack": "T1185 — Browser Session Hijacking (Clickjacking)",
            "descricao_tecnica": "Sem X-Frame-Options (ou diretiva CSP frame-ancestors), a aplicação pode ser embutida em um <iframe> invisível dentro de um site malicioso.",
            "impacto": "Usuários autenticados podem ser induzidos a clicar em botões sensíveis (ex.: aprovar acessos, alterar senha ou excluir dados) sem perceber (Clickjacking).",
            "passos_remediacao": [
                "Adicione o header X-Frame-Options: DENY (ou SAMEORIGIN se usar iframes internos).",
                "Em conjunto, adicione frame-ancestors 'none' (ou 'self') na Content-Security-Policy.",
                "Verifique se o login e páginas administrativas não carregam dentro de frames externos.",
            ],
            "comando_validacao": "curl -sI https://SEU_DOMINIO | findstr /I x-frame-options",
        },
    ),
    (
        ("referrer-policy",),
        {
            "cve_cwe": "CWE-200 (Exposure of Sensitive Information)",
            "cvss_score": 3.1,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:N/A:N",
            "owasp": "OWASP A05:2021 — Security Misconfiguration",
            "cis_control": "CIS v8 3.3 (Configure Data Access Control Lists)",
            "mitre_attack": "T1592 — Gather Victim Host Information",
            "descricao_tecnica": "Ausência de Referrer-Policy explícita permite que URLs internas, IDs de chamados ou parâmetros de consulta vazem no cabeçalho Referer ao clicar em links externos.",
            "impacto": "Vazamento acidental de caminhos internos, tokens em URL ou dados operacionais para servidores de terceiros.",
            "passos_remediacao": [
                "Adicione o header: Referrer-Policy: strict-origin-when-cross-origin (ou no-referrer).",
                "Evite trafegar tokens ou dados sensíveis na query string da URL.",
            ],
            "comando_validacao": "curl -sI https://SEU_DOMINIO | findstr /I referrer-policy",
        },
    ),
    (
        ("permissions-policy",),
        {
            "cve_cwe": "CWE-16 (Configuration)",
            "cvss_score": 2.6,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:N/A:N",
            "owasp": "OWASP A05:2021 — Security Misconfiguration",
            "cis_control": "CIS v8 4.1 (Secure Configuration Process)",
            "mitre_attack": "T1123 / T1125 — Audio/Video Capture",
            "descricao_tecnica": "Sem Permissions-Policy, o navegador não restringe explicitamente recursos como câmera, microfone, geolocalização e USB para iframes ou scripts carregados.",
            "impacto": "Aumenta a superfície caso scripts externos ou embeds tentem requisitar APIs sensíveis do navegador.",
            "passos_remediacao": [
                "Adicione o header: Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=().",
                "Libere apenas recursos realmente usados pela aplicação (ex.: camera=(self)).",
            ],
            "comando_validacao": "curl -sI https://SEU_DOMINIO | findstr /I permissions-policy",
        },
    ),
    (
        ("certificado tls", "ssl/tls", "tls/https", "expirado", "expira em"),
        {
            "cve_cwe": "CWE-295 (Improper Certificate Validation)",
            "cvss_score": 8.1,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:L",
            "owasp": "OWASP A02:2021 — Cryptographic Failures",
            "cis_control": "CIS v8 3.10 (Encrypt Sensitive Data in Transit)",
            "mitre_attack": "T1557 — Adversary-in-the-Middle",
            "descricao_tecnica": "Certificado SSL/TLS expirado, próximo do vencimento, autoassinado ou com cadeia incompleta na porta 443.",
            "impacto": "Bloqueio de acesso pelos navegadores, perda de confiança, falha em integrações API/webhooks e exposição a ataques Man-in-the-Middle.",
            "passos_remediacao": [
                "Renove o certificado imediatamente via Certbot/ACME (Let's Encrypt) ou sua Autoridade Certificadora.",
                "Verifique se o fullchain (certificado + intermediários) está instalado corretamente no servidor web/proxy.",
                "Desative TLS 1.0 e TLS 1.1; mantenha apenas TLS 1.2 e TLS 1.3 com cifras AEAD.",
                "Configure renovação automática e alerta 30 dias antes do vencimento no Auto-Shield.",
            ],
            "comando_validacao": "openssl s_client -connect SEU_DOMINIO:443 -servername SEU_DOMINIO </dev/null 2>/dev/null | openssl x509 -noout -dates -issuer -subject",
        },
    ),
    (
        ("telnet", "porta 23"),
        {
            "cve_cwe": "CWE-319 (Cleartext Transmission of Sensitive Information)",
            "cvss_score": 9.8,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
            "owasp": "OWASP A02:2021 — Cryptographic Failures",
            "cis_control": "CIS v8 4.8 (Uninstall or Disable Unnecessary Services)",
            "mitre_attack": "T1021 — Remote Services / T1040 — Network Sniffing",
            "descricao_tecnica": "Serviço Telnet (TCP/23) detectado ativo. Todo o tráfego, incluindo usuário e senha administrativa, trafega em texto plano sem qualquer criptografia.",
            "impacto": "Captura imediata de credenciais na rede local, tomada de controle de switches/roteadores/impressoras e propagação de botnets (ex.: Mirai).",
            "passos_remediacao": [
                "Desabilite imediatamente o serviço Telnet no equipamento (switch, roteador, Linux ou IoT).",
                "Habilite SSHv2 com autenticação por chave pública e desative login root direto.",
                "Crie regra de firewall bloqueando TCP/23 em todas as interfaces.",
                "Troque todas as senhas que já foram digitadas via Telnet.",
            ],
            "comando_validacao": "Test-NetConnection -ComputerName IP_ALVO -Port 23",
        },
    ),
    (
        ("smb", "445"),
        {
            "cve_cwe": "CWE-284 (Improper Access Control)",
            "cvss_score": 8.8,
            "cvss_vetor": "CVSS:3.1/AV:A/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
            "owasp": "OWASP A01:2021 — Broken Access Control",
            "cis_control": "CIS v8 12.2 (Establish and Maintain a Secure Network Architecture)",
            "mitre_attack": "T1021.002 — SMB/Windows Admin Shares",
            "descricao_tecnica": "Serviço SMB/CIFS (TCP/445) exposto na rede. É o principal vetor de movimentação lateral de ransomware, roubo de arquivos compartilhados e exploração de SMBv1.",
            "impacto": "Criptografia em massa de servidores/estações por ransomware, enumeração de compartilhamentos e comprometimento do domínio.",
            "passos_remediacao": [
                "Desative SMBv1 imediatamente em todos os servidores e estações Windows.",
                "Ative assinatura SMB (SMB Signing) obrigatória via GPO.",
                "Restrinja TCP/445 no firewall apenas às sub-redes internas que realmente precisam acessar arquivos; jamais exponha na WAN.",
                "Revise permissões NTFS e remova compartilhamentos abertos para 'Todos (Everyone)'.",
            ],
            "comando_validacao": "Get-SmbServerConfiguration | Select EnableSMB1Protocol, RequireSecuritySignature, EncryptData",
        },
    ),
    (
        ("rdp", "3389"),
        {
            "cve_cwe": "CWE-284 / CWE-307 (Brute Force)",
            "cvss_score": 8.6,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H",
            "owasp": "OWASP A07:2021 — Identification and Authentication Failures",
            "cis_control": "CIS v8 6.4 (Require MFA for Remote Network Access)",
            "mitre_attack": "T1021.001 — Remote Desktop Protocol / T1110 — Brute Force",
            "descricao_tecnica": "Porta RDP (TCP/3389) acessível diretamente. Serviços RDP expostos são alvo constante de ataques automatizados de força bruta, credential stuffing e invasão por ransomware.",
            "impacto": "Acesso interativo completo ao servidor/estação, elevação de privilégios e comprometimento total do ambiente.",
            "passos_remediacao": [
                "Bloqueie RDP direto na borda/firewall e permita acesso exclusivamente via VPN corporativa com MFA.",
                "Ative NLA (Network Level Authentication) nas propriedades de Área de Trabalho Remota.",
                "Configure política de bloqueio de conta após tentativas inválidas (Account Lockout) e limite quem pertence ao grupo Remote Desktop Users.",
                "Monitore eventos Windows ID 4625 (falha de logon) e 4624 tipo 10.",
            ],
            "comando_validacao": "Get-NetFirewallRule -DisplayGroup 'Remote Desktop' | Select DisplayName, Enabled, Profile",
        },
    ),
    (
        ("rtsp", "554", "cftv", "câmera", "camera"),
        {
            "cve_cwe": "CWE-306 (Missing Authentication) / CWE-798 (Default Credentials)",
            "cvss_score": 8.2,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:L/A:L",
            "owasp": "OWASP IoT Top 10 — I1/I2 (Weak Passwords & Insecure Network Services)",
            "cis_control": "CIS v8 12.2 (Network Segmentation — VLAN CFTV)",
            "mitre_attack": "T1125 — Video Capture / T1190 — Exploit Public-Facing Application",
            "descricao_tecnica": "Serviço RTSP (TCP/554) de câmeras IP ou DVR/NVR acessível fora de uma VLAN isolada ou com risco de credenciais padrão/UPnP ativo.",
            "impacto": "Visualização indevida de câmeras de segurança, violação de privacidade (LGPD) e uso do DVR/câmera como pivô para invadir a rede corporativa.",
            "passos_remediacao": [
                "Isole todas as câmeras IP e NVRs em uma VLAN dedicada de CFTV sem acesso direto à internet.",
                "Troque 100% das senhas de fábrica por senhas únicas de 14+ caracteres e desative usuários convidados/RTSP anônimo.",
                "Desative UPnP e redirecionamentos de porta manuais no roteador; acesse via VPN corporativa.",
                "Atualize o firmware do DVR/NVR e das câmeras para a versão mais recente do fabricante.",
            ],
            "comando_validacao": "Test-NetConnection -ComputerName IP_CAMERA -Port 554",
        },
    ),
    (
        ("winbox", "8291", "mikrotik"),
        {
            "cve_cwe": "CWE-284 (Improper Access Control)",
            "cvss_score": 8.5,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:L",
            "owasp": "OWASP A01:2021 — Broken Access Control",
            "cis_control": "CIS v8 12.3 (Securely Manage Network Infrastructure)",
            "mitre_attack": "T1190 — Exploit Public-Facing Application",
            "descricao_tecnica": "Serviço Winbox (TCP/8291) do MikroTik RouterOS detectado aberto. Portas de gerência expostas permitem ataques de força bruta e exploração de versões desatualizadas do RouterOS.",
            "impacto": "Sequestro do roteador de borda, alteração de DNS/rotas, abertura de túneis maliciosos e indisponibilidade da rede.",
            "passos_remediacao": [
                "Restrinja o serviço Winbox apenas à VLAN/IP de gerência em IP > Services > winbox (Available From).",
                "Crie regra no firewall Filter (chain=input) bloqueando TCP/8291 vindo da WAN e de VLANs não autorizadas.",
                "Atualize o RouterOS para a branch Stable/Long-Term mais recente e desative Telnet/FTP/API não utilizados.",
            ],
            "comando_validacao": "/ip service print; /ip firewall filter print where dst-port=8291",
        },
    ),
    (
        ("mqtt", "1883"),
        {
            "cve_cwe": "CWE-306 / CWE-319 (Cleartext IoT Broker)",
            "cvss_score": 7.8,
            "cvss_vetor": "CVSS:3.1/AV:A/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:L",
            "owasp": "OWASP IoT Top 10 — I7 (Insecure Data Transfer)",
            "cis_control": "CIS v8 12.2 (IoT Network Segmentation)",
            "mitre_attack": "T1040 — Network Sniffing / T1565 — Data Manipulation",
            "descricao_tecnica": "Broker MQTT na porta 1883 sem criptografia TLS. Permite leitura e publicação de tópicos de sensores/automação por qualquer dispositivo na mesma rede.",
            "impacto": "Acionamento indevido de relés/alarmes, leitura de telemetria e sabotagem de automação.",
            "passos_remediacao": [
                "Migre o broker para MQTTS (porta 8883 com TLS 1.2+).",
                "Desative acesso anônimo (allow_anonymous false) e configure usuário/senha ou certificado por dispositivo.",
                "Isole dispositivos IoT em VLAN própria com ACLs restritas ao IP do broker.",
            ],
            "comando_validacao": "Test-NetConnection -ComputerName IP_BROKER -Port 1883",
        },
    ),
    (
        ("ssh", "porta 22"),
        {
            "cve_cwe": "CWE-287 (Improper Authentication)",
            "cvss_score": 4.3,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N",
            "owasp": "OWASP A07:2021 — Identification and Authentication Failures",
            "cis_control": "CIS v8 4.1 (Secure Configuration)",
            "mitre_attack": "T1021.004 — SSH / T1110 — Brute Force",
            "descricao_tecnica": "Serviço SSH (TCP/22) acessível. Embora criptografado, precisa estar protegido contra força bruta e login root por senha.",
            "impacto": "Acesso administrativo caso existam senhas fracas ou contas padrão habilitadas.",
            "passos_remediacao": [
                "Em /etc/ssh/sshd_config defina: PermitRootLogin no e PasswordAuthentication no (use chaves Ed25519/RSA).",
                "Restrinja TCP/22 por firewall/ACL apenas aos IPs de gerência ou VPN.",
                "Ative Fail2ban ou rate-limit para bloquear tentativas repetidas.",
            ],
            "comando_validacao": "sshd -T | grep -E 'permitrootlogin|passwordauthentication'",
        },
    ),
    (
        ("backup", "ransomware", "3-2-1-1-0"),
        {
            "cve_cwe": "CWE-693 (Protection Mechanism Failure)",
            "cvss_score": 9.0,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:N/I:H/A:H",
            "owasp": "NIST CSF 2.0 — Recover (RC.RP)",
            "cis_control": "CIS v8 11.4 (Establish and Maintain an Isolated Instance of Recovery Data)",
            "mitre_attack": "T1486 — Data Encrypted for Impact / T1490 — Inhibit System Recovery",
            "descricao_tecnica": "Ausência de validação de backup imutável/offline seguindo a regra 3-2-1-1-0.",
            "impacto": "Em caso de ransomware ou falha crítica de storage, a empresa pode perder dados permanentemente ou ficar dias parada.",
            "passos_remediacao": [
                "Configure 3 cópias dos dados em 2 mídias diferentes, sendo 1 offsite e 1 imutável (Object Lock/WORM ou offline).",
                "Use credenciais dedicadas e isoladas do Active Directory para o servidor de backup.",
                "Execute teste real de restauração mensalmente e registre evidência no checklist CIS.",
            ],
            "comando_validacao": "Verificar último job de backup imutável e registrar log do teste de restore mensal.",
        },
    ),
    (
        ("2fa", "mfa", "credenciais", "força bruta", "iam"),
        {
            "cve_cwe": "CWE-308 (Use of Single-factor Authentication)",
            "cvss_score": 8.0,
            "cvss_vetor": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:L",
            "owasp": "OWASP A07:2021 — Identification and Authentication Failures",
            "cis_control": "CIS v8 6.3 / 6.5 (Require MFA for Externally-Exposed & Admin Accounts)",
            "mitre_attack": "T1078 — Valid Accounts / T1110.004 — Credential Stuffing",
            "descricao_tecnica": "Contas ativas sem autenticação em dois fatores (2FA/TOTP) habilitada.",
            "impacto": "Se uma senha vazar em phishing ou reutilização, o invasor entra diretamente na conta sem nenhuma barreira adicional.",
            "passos_remediacao": [
                "Ative a exigência de 2FA (TOTP) na política de segurança da empresa.",
                "Oriente cada colaborador a configurar o autenticador no ícone de cadeado do sistema.",
                "Revogue sessões antigas e troque senhas provisórias ou compartilhadas.",
            ],
            "comando_validacao": "Conferir na aba Gov-Key e Gestão de Equipe se 100% dos usuários estão com selo '2FA Ativo'.",
        },
    ),
]


def _enriquecer_achado_patchbot(item: dict[str, Any]) -> dict[str, Any]:
    """Enriquece um achado com dossiê técnico completo do Patch-Bot (CVSS, CWE, OWASP, CIS, MITRE, SLA, passos e comando)."""
    enr = dict(item)
    sev = str(enr.get("severidade") or "Média")
    sla_padrao, cvss_padrao = _PATCHBOT_SLA_POR_SEV.get(sev, ("30 dias", 5.0))
    texto_busca = " ".join([
        str(enr.get("titulo") or ""),
        str(enr.get("categoria") or ""),
        str(enr.get("recomendacao") or ""),
        str(enr.get("evidencia") or ""),
    ]).lower()

    kb_match: dict[str, Any] | None = None
    for keywords, meta in _PATCHBOT_KB_REGRAS:
        if any(k in texto_busca for k in keywords):
            kb_match = meta
            break

    alvo_txt = str(enr.get("alvo") or "alvo-afetado").strip()
    if kb_match:
        enr.setdefault("cve_cwe", "")
        if not enr["cve_cwe"]:
            enr["cve_cwe"] = kb_match["cve_cwe"]
        if not enr.get("cvss_score"):
            enr["cvss_score"] = kb_match["cvss_score"]
        enr["cvss_vetor"] = enr.get("cvss_vetor") or kb_match["cvss_vetor"]
        enr["owasp"] = enr.get("owasp") or kb_match["owasp"]
        enr["cis_control"] = enr.get("cis_control") or kb_match["cis_control"]
        enr["mitre_attack"] = enr.get("mitre_attack") or kb_match["mitre_attack"]
        enr["descricao_tecnica"] = enr.get("descricao_tecnica") or kb_match["descricao_tecnica"]
        enr["impacto"] = enr.get("impacto") or kb_match["impacto"]
        enr["passos_remediacao"] = enr.get("passos_remediacao") or list(kb_match["passos_remediacao"])
        cmd = str(enr.get("comando_validacao") or kb_match["comando_validacao"])
        if alvo_txt and alvo_txt not in {"-", "Ambiente Interno", "Ambiente da Empresa", "Verificar Parque / Rede"}:
            alvo_host = _normalizar_dominio_escopo(alvo_txt) or alvo_txt
            cmd = cmd.replace("SEU_DOMINIO", alvo_host).replace("IP_ALVO", alvo_host).replace("IP_CAMERA", alvo_host).replace("IP_BROKER", alvo_host)
        enr["comando_validacao"] = cmd
    else:
        enr["cve_cwe"] = enr.get("cve_cwe") or "CWE-693 (Revisão de Segurança)"
        enr["cvss_score"] = enr.get("cvss_score") or cvss_padrao
        enr["cvss_vetor"] = enr.get("cvss_vetor") or "Estimativa baseada na severidade classificada"
        enr["owasp"] = enr.get("owasp") or "OWASP / Boas Práticas de Hardening"
        enr["cis_control"] = enr.get("cis_control") or "CIS v8 7.1 (Continuous Vulnerability Management)"
        enr["mitre_attack"] = enr.get("mitre_attack") or "Superfície de Ataque / Gestão de Exposição"
        enr["descricao_tecnica"] = enr.get("descricao_tecnica") or (
            f"Achado de severidade {sev} identificado na categoria '{enr.get('categoria') or 'Geral'}' afetando '{alvo_txt}'."
        )
        enr["impacto"] = enr.get("impacto") or (
            "Exposição operacional ou de segurança que pode comprometer confidencialidade, integridade ou disponibilidade caso não seja tratada dentro do SLA."
        )
        rec = str(enr.get("recomendacao") or "").strip()
        enr["passos_remediacao"] = enr.get("passos_remediacao") or [
            rec or "Avaliar o ativo afetado e aplicar a correção/patch recomendado pelo fabricante.",
            "Validar que não houve impacto colateral após aplicar a mitigação.",
            "Reexecutar a auditoria no Byte-Scanner ou Auto-Shield e anexar evidência nas notas de correção.",
        ]
        enr["comando_validacao"] = enr.get("comando_validacao") or f"Reauditar alvo '{alvo_txt}' no Byte-Scanner após aplicar a mitigação."

    enr["sla_recomendado"] = enr.get("prazo_sla") or enr.get("sla_recomendado") or sla_padrao
    enr["origem"] = enr.get("origem") or (
        "Byte-Scanner / Varredura" if enr.get("auditoria_id") else (
            "Purple-Bot (Simulação)" if "[Simulação" in str(enr.get("titulo") or "") else (
                "Wiki-Core (Threat Intel)" if "[Radar Cyber]" in str(enr.get("titulo") or "") else "Registro Manual"
            )
        )
    )
    enr.setdefault("evidencia", "")
    enr.setdefault("responsavel", "")
    enr.setdefault("prazo_sla", "")
    enr.setdefault("notas_remediacao", "")
    return enr

# Portas avaliadas em auditorias defensivas internas (somente detecção de exposição de serviços e protocolos)
_PORTAS_AUDITORIA_DEFENSIVA: dict[int, tuple[str, str, str]] = {
    21: ("FTP", "Alta", "Protocolo sem criptografia: migrar para SFTP/FTPS e desativar acesso anônimo."),
    22: ("SSH", "Baixa", "Manter autenticação por chave SSH e desabilitar login root por senha."),
    23: ("Telnet", "Crítica", "Protocolo em texto claro: desativar Telnet imediatamente e migrar para SSH."),
    25: ("SMTP", "Média", "Restringir relay aberto, exigir TLS (STARTTLS) e configurar SPF/DKIM/DMARC."),
    53: ("DNS", "Média", "Restringir recursão DNS apenas à rede interna e bloquear transferência de zona (AXFR)."),
    80: ("HTTP", "Média", "Redirecionar interface administrativa para HTTPS/TLS com certificado válido."),
    110: ("POP3", "Alta", "Protocolo de e-mail em texto claro: migrar para POP3S (995) ou IMAPS (993)."),
    135: ("MSRPC", "Alta", "Restringir RPC do Windows apenas à LAN/VLAN corporativa via firewall."),
    139: ("NetBIOS", "Alta", "Desativar NetBIOS sobre TCP/IP quando não necessário e isolar na LAN."),
    143: ("IMAP", "Média", "Exigir STARTTLS ou migrar para IMAPS (porta 993)."),
    443: ("HTTPS", "Informativa", "Verificar validade do certificado TLS, cifras fortes e headers de segurança."),
    445: ("SMB", "Alta", "Restringir SMB/CIFS à rede interna isolada e bloquear SMBv1 para prevenir ransomware."),
    465: ("SMTPS", "Informativa", "Verificar certificado TLS válido e autenticação SMTP obrigatória."),
    554: ("RTSP (CFTV)", "Alta", "Segmentar câmeras em VLAN isolada, exigir autenticação forte e usar túnel/VPN."),
    587: ("SMTP Submission", "Informativa", "Exigir STARTTLS e autenticação forte para envio de e-mails."),
    631: ("IPP (Impressora)", "Baixa", "Restringir painel de impressão à VLAN de escritório e exigir senha admin."),
    993: ("IMAPS", "Informativa", "Verificar validade do certificado TLS e ativar 2FA nas contas."),
    995: ("POP3S", "Informativa", "Verificar certificado TLS e preferir IMAPS com MFA."),
    1433: ("MSSQL", "Crítica", "Nunca expor banco de dados diretamente; restringir apenas aos servidores de aplicação."),
    1883: ("MQTT (IoT)", "Alta", "Ativar autenticação e TLS (MQTTS porta 8883) para dispositivos IoT."),
    3306: ("MySQL / MariaDB", "Crítica", "Bloquear acesso externo ao banco; permitir apenas localhost/VLAN de aplicação com TLS."),
    3389: ("RDP", "Alta", "Nunca expor RDP diretamente; exigir VPN + MFA + NLA."),
    5432: ("PostgreSQL", "Crítica", "Restringir pg_hba.conf apenas aos IPs autorizados e exigir SSL."),
    5900: ("VNC", "Alta", "Nunca expor VNC sem túnel SSH/VPN e senha forte."),
    6379: ("Redis", "Crítica", "Restringir bind ao localhost/rede privada e exigir ACL/senha forte."),
    8080: ("HTTP Alt / Painel", "Média", "Validar se painel web possui autenticação forte e acesso restrito por firewall."),
    8291: ("Winbox (MikroTik)", "Alta", "Restringir Winbox apenas à VLAN/IP de gerência (Management) e desativar na WAN."),
    8443: ("HTTPS Alt", "Informativa", "Conferir certificado TLS e controle de acesso ao painel administrativo."),
    8883: ("MQTTS (IoT TLS)", "Informativa", "Verificar certificado TLS e credenciais individuais por dispositivo IoT."),
    9100: ("JetDirect (Impressora)", "Média", "Isolar impressoras de rede em VLAN própria para evitar abuso de spooler."),
}

CYBER_KNOWLEDGE_BASE: list[dict[str, Any]] = [
    {
        "id": "kb_zerotrust",
        "categoria": "Arquitetura & Rede",
        "nivel": "Essencial",
        "atualizado_em": "2026-10",
        "titulo": "Zero Trust & Microssegmentação por VLANs (TI, CFTV, IoT e Gerência)",
        "resumo": "Nunca misture câmeras IP, sensores IoT, visitantes e servidores na mesma VLAN.",
        "conteudo": (
            "1. Crie VLANs dedicadas: VLAN Gerência/NOC, VLAN Corporativa/TI, VLAN CFTV/Alarmes, VLAN IoT e VLAN Visitantes.\n"
            "2. Aplique firewall inter-VLAN com bloqueio padrão (Deny-All) liberando apenas portas estritamente necessárias (ex.: NVR -> Câmeras na 554).\n"
            "3. Bloqueie saída direta à internet de DVRs, câmeras e sensores IoT; prefira acesso remoto via VPN corporativa com MFA."
        ),
        "tags": ["VLAN", "Zero Trust", "Segmentação", "Firewall"],
    },
    {
        "id": "kb_cftv_iot",
        "categoria": "Automação & IoT",
        "nivel": "Crítico",
        "atualizado_em": "2026-10",
        "titulo": "Hardening de Câmeras IP, DVRs/NVRs, Alarmes e Dispositivos IoT",
        "resumo": "Elimine senhas de fábrica, UPnP, RTSP anônimo e portas abertas via P2P inseguro.",
        "conteudo": (
            "1. Altere imediatamente senhas padrão (admin/12345, etc.) usando senhas únicas de 14+ caracteres.\n"
            "2. Desative UPnP no roteador e no DVR/NVR para evitar exposição automática de portas na internet.\n"
            "3. Desabilite serviços não utilizados (Telnet, SSH aberto, ONVIF sem autenticação, cloud P2P quando houver VPN).\n"
            "4. Para centrais de alarme e controle de acesso: monitore queda de energia/bateria e falha de enlace Ethernet/GPRS."
        ),
        "tags": ["CFTV", "IoT", "RTSP", "ONVIF", "Alarmes"],
    },
    {
        "id": "kb_ransomware",
        "categoria": "Defesa & Continuidade",
        "nivel": "Crítico",
        "atualizado_em": "2026-10",
        "titulo": "Defesa Anti-Ransomware & Estratégia de Backup 3-2-1-1-0",
        "resumo": "Backups imutáveis/offline e bloqueio de SMB/RDP expostos são a principal barreira.",
        "conteudo": (
            "1. Regra 3-2-1-1-0: 3 cópias dos dados, 2 mídias diferentes, 1 fora do local (nuvem/offsite), 1 cópia IMUTÁVEL/air-gapped e 0 erros em testes de restauração.\n"
            "2. Desative SMBv1, restrinja porta 445 e jamais exponha RDP (3389) diretamente na internet.\n"
            "3. Implemente EDR/Antivírus gerenciado, MFA em todos os acessos administrativos e princípio do menor privilégio."
        ),
        "tags": ["Ransomware", "Backup", "Imutabilidade", "EDR"],
    },
    {
        "id": "kb_isp_noc",
        "categoria": "Provedores (ISP)",
        "nivel": "Avançado",
        "atualizado_em": "2026-10",
        "titulo": "Segurança para Provedores: BCP38, RPKI, MikroTik/OLT & Proteção CGNAT",
        "resumo": "Boas práticas de borda, gerência OOB e proteção contra amplificação DDoS em ISPs.",
        "conteudo": (
            "1. Aplique BCP38/uRPF na borda e nos concentradores PPPoE/IPoE para impedir spoofing de IP.\n"
            "2. Valide rotas BGP com RPKI (ROA) e filtros IRR nos upstreams/IX.br.\n"
            "3. Isole portas de gerência (Winbox 8291, SSH, TR-069/ACS, SNMP, OLT Web) apenas para a rede de gerência/VPN NOC.\n"
            "4. Guarde logs de CGNAT conforme Marco Civil da Internet em servidor syslog seguro e sincronizado via NTP."
        ),
        "tags": ["ISP", "BCP38", "RPKI", "MikroTik", "OLT"],
    },
    {
        "id": "kb_owasp_web",
        "categoria": "Web & APIs",
        "nivel": "Essencial",
        "atualizado_em": "2026-10",
        "titulo": "OWASP Top 10, TLS 1.3 & Cabeçalhos de Segurança HTTP",
        "resumo": "Como auditar certificados SSL/TLS e headers defensivos de sistemas web e painéis.",
        "conteudo": (
            "1. Exija HTTPS (TLS 1.2/1.3) com renovação automática antes de expirar e redirecionamento 301 de HTTP.\n"
            "2. Configure headers obrigatórios: Strict-Transport-Security (HSTS), Content-Security-Policy (CSP), X-Content-Type-Options: nosniff, X-Frame-Options e Referrer-Policy.\n"
            "3. Valide autenticação forte, controle de acesso por função (RBAC) e proteção contra SSRF/injeções."
        ),
        "tags": ["OWASP", "TLS", "HSTS", "CSP", "API"],
    },
    {
        "id": "kb_cis_nist",
        "categoria": "Governança & Normas",
        "nivel": "Estratégico",
        "atualizado_em": "2026-10",
        "titulo": "CIS Controls v8 & NIST Cybersecurity Framework 2.0",
        "resumo": "Os 6 pilares (Govern, Identify, Protect, Detect, Respond, Recover) aplicados a PMEs e ISPs.",
        "conteudo": (
            "1. Govern: política de segurança aprovada pela direção e escopo formal de auditorias.\n"
            "2. Identify: inventário contínuo de ativos TI, rede, CFTV e IoT.\n"
            "3. Protect: MFA, patching de firmware, gestão de privilégios e backups.\n"
            "4. Detect/Respond/Recover: logs centralizados, monitoramento de alertas e planos de recuperação testados."
        ),
        "tags": ["CIS v8", "NIST CSF 2.0", "Governança"],
    },
    {
        "id": "kb_lgpd",
        "categoria": "Conformidade (LGPD)",
        "nivel": "Essencial",
        "atualizado_em": "2026-10",
        "titulo": "LGPD na Prática para TI, Assistências e Provedores",
        "resumo": "Segregação de dados pessoais, minimização, anonimização de relatórios e trilha de auditoria.",
        "conteudo": (
            "1. Aplique menor privilégio: técnicos sem função financeira não devem ver valores ou dados fiscais desnecessários.\n"
            "2. Utilize anonimização em relatórios compartilhados externamente e controle links públicos sem expor dados sensíveis.\n"
            "3. Registre evidências de movimentações, laudos e autorizações formais de acesso/auditoria."
        ),
        "tags": ["LGPD", "Privacidade", "RBAC", "Auditoria"],
    },
    {
        "id": "kb_ir_playbook",
        "categoria": "Resposta a Incidentes",
        "nivel": "Operacional",
        "atualizado_em": "2026-10",
        "titulo": "Playbook Rápido de Resposta a Incidentes (Contenção -> Erradicação -> Recuperação)",
        "resumo": "Passo a passo para agir em caso de invasão de câmera/roteador, phishing ou ransomware.",
        "conteudo": (
            "1. Contenção: isole a VLAN ou desconecte o cabo/Wi-Fi do host comprometido sem desligá-lo se precisar preservar logs.\n"
            "2. Credenciais: revogue sessões ativas, troque senhas administrativas e chaves de API.\n"
            "3. Erradicação & Recuperação: atualize firmware/SO, restaure backup limpo validado e registre o laudo/lições aprendidas."
        ),
        "tags": ["Playbook", "SOC", "Incidente", "Contenção"],
    },
]

CYBER_COMPLIANCE_TEMPLATE: list[dict[str, Any]] = [
    {"id": "chk_mfa", "framework": "CIS v8 / NIST", "controle": "MFA obrigatório para administradores, VPN e e-mail corporativo", "status": "Pendente", "responsavel": ""},
    {"id": "chk_backup", "framework": "CIS v8 / NIST", "controle": "Backup 3-2-1-1-0 com cópia imutável/offline e teste mensal de restore", "status": "Pendente", "responsavel": ""},
    {"id": "chk_vlan_iot", "framework": "CFTV & IoT", "controle": "Câmeras IP, DVRs, alarmes e IoT isolados em VLAN dedicada sem acesso direto externo", "status": "Pendente", "responsavel": ""},
    {"id": "chk_senhas_padrao", "framework": "CFTV & IoT", "controle": "100% das senhas padrão de fábrica trocadas em roteadores, ONUs, câmeras e nobreaks", "status": "Pendente", "responsavel": ""},
    {"id": "chk_tls_headers", "framework": "OWASP / Web", "controle": "Certificados HTTPS válidos e cabeçalhos HSTS/CSP/X-Frame ativos", "status": "Pendente", "responsavel": ""},
    {"id": "chk_patching", "framework": "CIS v8", "controle": "Rotina mensal de atualização de firmware (OLT, firewall, NVR) e patches críticos", "status": "Pendente", "responsavel": ""},
    {"id": "chk_logs_cgnat", "framework": "LGPD / ISP", "controle": "Logs de acesso, firewall e CGNAT centralizados com sincronismo NTP", "status": "Pendente", "responsavel": ""},
    {"id": "chk_termo_auditoria", "framework": "Governança", "controle": "Termo de autorização formal do gestor registrado antes de auditorias ativas de rede", "status": "Pendente", "responsavel": ""},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calcular_status_automacao(checks: dict[str, bool], status_manual: str | None = None) -> str:
    if status_manual in AUTOMACAO_STATUS:
        return status_manual
    positivos = sum(1 for v in checks.values() if bool(v))
    if positivos >= 4:
        return "Conforme"
    if positivos >= 2:
        return "Alerta"
    return "Crítico"


class LocalSecurityOpsStore:
    """Armazenamento atômico local para os setores de Automação e Cibersegurança."""

    def __init__(self, path: str | Path | None = None):
        self._custom_path = Path(path) if path else None

    @property
    def path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "security_ops_db.json"

    @staticmethod
    def _empty() -> dict:
        return {
            "version": 1,
            "automacao_ativos": [],
            "cyber_auditorias": [],
            "cyber_achados": [],
            "cyber_checklists": [],
            "cyber_kb": [],
            "cyber_automacoes": [],
        }

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Erro ao ler {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            return self._empty()
        for col in _VALID_COLLECTIONS:
            if not isinstance(data.get(col), list):
                data[col] = []
        return data

    def _write_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tf:
                temp_path = Path(tf.name)
                json.dump(data, tf, ensure_ascii=False, allow_nan=False, indent=2)
                tf.write("\n")
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def ensure(self) -> None:
        with _SEC_OPS_LOCK:
            if not self.path.exists():
                self._write_unlocked(self._empty())

    def list_items(self, collection: str, tenant_id: str) -> list[dict]:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _SEC_OPS_LOCK:
            return [
                dict(row)
                for row in self._read_unlocked()[collection]
                if row.get("tenant_id") == tenant_id
            ]

    def get_item(self, collection: str, item_id: str, tenant_id: str) -> dict | None:
        for row in self.list_items(collection, tenant_id):
            if row.get("id") == item_id:
                return row
        return None

    def upsert_item(self, collection: str, item: dict, tenant_id: str) -> dict:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _SEC_OPS_LOCK:
            data = self._read_unlocked()
            row = dict(item)
            row["tenant_id"] = tenant_id
            rows = data[collection]
            for idx, existing in enumerate(rows):
                if existing.get("id") == row["id"] and existing.get("tenant_id") == tenant_id:
                    rows[idx] = row
                    self._write_unlocked(data)
                    return dict(row)
            rows.append(row)
            self._write_unlocked(data)
            return dict(row)

    def update_item(
        self, collection: str, item_id: str, updates: dict, tenant_id: str
    ) -> dict | None:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _SEC_OPS_LOCK:
            data = self._read_unlocked()
            for row in data[collection]:
                if row.get("id") == item_id and row.get("tenant_id") == tenant_id:
                    row.update(updates)
                    row["tenant_id"] = tenant_id
                    self._write_unlocked(data)
                    return dict(row)
        return None

    def delete_item(self, collection: str, item_id: str, tenant_id: str) -> bool:
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        with _SEC_OPS_LOCK:
            data = self._read_unlocked()
            antes = len(data[collection])
            data[collection] = [
                row
                for row in data[collection]
                if not (row.get("id") == item_id and row.get("tenant_id") == tenant_id)
            ]
            if len(data[collection]) < antes:
                self._write_unlocked(data)
                return True
            return False


class FirestoreSecurityOpsStore:
    """Armazenamento Cloud Firestore para Automação e Cibersegurança."""

    def __init__(self, db: Any = None):
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from firebase_backend import get_firestore_client

            self._db = get_firestore_client()
        return self._db

    def _col_ref(self, tenant_id: str, collection: str):
        if collection not in _VALID_COLLECTIONS:
            raise ValueError("Coleção inválida.")
        return self.db.collection("tenants").document(tenant_id).collection(collection)

    def list_items(self, collection: str, tenant_id: str) -> list[dict]:
        rows: list[dict] = []
        for doc in self._col_ref(tenant_id, collection).stream():
            data = dict(doc.to_dict() or {})
            data.setdefault("id", doc.id)
            data["tenant_id"] = tenant_id
            rows.append(data)
        return rows

    def get_item(self, collection: str, item_id: str, tenant_id: str) -> dict | None:
        snap = self._col_ref(tenant_id, collection).document(item_id).get()
        if not snap.exists:
            return None
        data = dict(snap.to_dict() or {})
        data.setdefault("id", item_id)
        data["tenant_id"] = tenant_id
        return data

    def upsert_item(self, collection: str, item: dict, tenant_id: str) -> dict:
        row = dict(item)
        row["tenant_id"] = tenant_id
        self._col_ref(tenant_id, collection).document(row["id"]).set(row)
        return dict(row)

    def update_item(
        self, collection: str, item_id: str, updates: dict, tenant_id: str
    ) -> dict | None:
        ref = self._col_ref(tenant_id, collection).document(item_id)
        snap = ref.get()
        if not snap.exists:
            return None
        payload = dict(updates)
        payload["tenant_id"] = tenant_id
        ref.update(payload)
        updated = dict(ref.get().to_dict() or {})
        updated.setdefault("id", item_id)
        updated["tenant_id"] = tenant_id
        return updated

    def delete_item(self, collection: str, item_id: str, tenant_id: str) -> bool:
        ref = self._col_ref(tenant_id, collection).document(item_id)
        snap = ref.get()
        if not snap.exists:
            return False
        ref.delete()
        return True


_local_sec_store = LocalSecurityOpsStore()


def get_security_ops_store():
    if get_storage_backend() == "firebase":
        return FirestoreSecurityOpsStore()
    return _local_sec_store


def _is_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
        or str(ip_obj) == "169.254.169.254"
    )


def _normalizar_dominio_escopo(entry: str) -> str:
    """Extrai o hostname limpo mesmo quando o usuário digita https://dominio.com.br/caminho:443."""
    bruto = (entry or "").strip().lower()
    if not bruto:
        return ""
    if "://" not in bruto and "/" in bruto:
        try:
            ipaddress.ip_network(bruto, strict=False)
            return ""
        except ValueError:
            pass
    candidato = bruto if "://" in bruto else f"https://{bruto}"
    parsed = urllib.parse.urlparse(candidato)
    host = (parsed.hostname or "").strip().strip(".").lstrip("*.")
    return host


def _host_matches_domain_scope(host: str, escopo_norm: list[str]) -> bool:
    host_l = host.lower().strip(".")
    host_sem_www = host_l[4:] if host_l.startswith("www.") else host_l
    for entry in escopo_norm:
        item = _normalizar_dominio_escopo(entry)
        if not item:
            continue
        try:
            ipaddress.ip_address(item)
            if item == host_l:
                return True
            continue
        except ValueError:
            pass
        item_sem_www = item[4:] if item.startswith("www.") else item
        if (
            host_l == item
            or host_l.endswith(f".{item}")
            or host_sem_www == item_sem_www
            or host_l.endswith(f".{item_sem_www}")
        ):
            return True
    return False


def verificar_operador_autorizado_govkey(
    tenant: dict,
    user_uid: str = "",
    user_email: str = "",
    is_admin: bool = False,
) -> None:
    """Valida se o colaborador atual foi autorizado pelo gestor no Gov-Key."""
    if is_admin:
        return
    gov = tenant.get("cyber_governanca") or {}
    autorizados_raw = gov.get("usuarios_autorizados") or []
    restringir = bool(gov.get("restringir_usuarios")) or bool(autorizados_raw)
    if not restringir:
        return
    autorizados = {str(u).strip().lower() for u in autorizados_raw if str(u).strip()}
    uid_norm = str(user_uid or "").strip().lower()
    email_norm = str(user_email or "").strip().lower()
    if (uid_norm and uid_norm in autorizados) or (email_norm and email_norm in autorizados):
        return
    raise PermissionError(
        "Acesso bloqueado pelo Gov-Key: seu usuário não está na lista de pessoas autorizadas pelo gestor para executar tarefas de cibersegurança."
    )


class NetworkAuditEngine:
    """Motor defensivo e autorizado para auditoria de postura de rede e Web/TLS."""

    @staticmethod
    def validar_alvo_autorizado(
        alvo: str, tipo_auditoria: str, escopo_autorizado: list[str] | None = None
    ) -> tuple[str, str]:
        raw = SecurityValidator.sanitizar_texto(alvo)
        if not raw:
            raise ValueError("Informe o alvo da auditoria (domínio/URL para Web/TLS ou IP/CIDR privado para Rede Local).")

        escopo_limpo = [
            SecurityValidator.sanitizar_texto(e)
            for e in (escopo_autorizado or [])
            if SecurityValidator.sanitizar_texto(e)
        ]

        if tipo_auditoria == "web_tls":
            url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").strip().lower().strip(".")
            if not host or host in {"localhost", "metadata.google.internal"} or host.endswith(".internal"):
                raise ValueError("Host inválido ou bloqueado pela proteção anti-SSRF.")
            try:
                ip_obj = ipaddress.ip_address(host)
                if _is_forbidden_ip(ip_obj) or ip_obj.is_private:
                    raise PermissionError("Auditorias Web/TLS exigem um domínio válido (IPs internos/loopback/metadata são bloqueados no modo Web).")
            except ValueError:
                if "." not in host:
                    raise ValueError("Informe um domínio válido (ex.: suaempresa.com.br).")
            if escopo_limpo and not _host_matches_domain_scope(host, escopo_limpo):
                raise PermissionError(
                    f"Domínio '{host}' não consta na restrição opcional de escopo definida pelo gestor."
                )
            return url, host

        if tipo_auditoria == "protocolos_pacotes":
            host_limpo = raw.strip().lower()
            if host_limpo in {"localhost", "metadata.google.internal"} or host_limpo.endswith(".internal"):
                raise PermissionError("Alvo proibido pela política anti-SSRF.")
            try:
                net = ipaddress.ip_network(host_limpo, strict=False)
                if net.version != 4 or not net.is_private:
                    raise PermissionError(
                        "Para inspeção de portas por IP/CIDR, utilize apenas faixas privadas IPv4 (RFC1918: 10.x, 172.16-31.x, 192.168.x)."
                    )
                if any(_is_forbidden_ip(addr) for addr in (net.network_address, net.broadcast_address)):
                    raise PermissionError("Endereço loopback, link-local ou reservado não é permitido.")
                if net.prefixlen < 24:
                    raise ValueError("Use no máximo blocos /24 (até 254 IPs por varredura).")
                if escopo_limpo:
                    redes_escopo = []
                    for entry in escopo_limpo:
                        try:
                            allowed_net = ipaddress.ip_network(entry, strict=False)
                            if allowed_net.version == 4:
                                redes_escopo.append(allowed_net)
                        except ValueError:
                            continue
                    if not redes_escopo or not any(net.subnet_of(allowed_net) for allowed_net in redes_escopo):
                        raise PermissionError(f"Sub-rede '{net}' fora da restrição de escopo configurada.")
                return str(net), "cidr"
            except ValueError:
                url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
                parsed = urllib.parse.urlparse(url)
                host = (parsed.hostname or "").strip().lower().strip(".")
                if not host or "." not in host or host in {"localhost", "metadata.google.internal"}:
                    raise ValueError("Informe um IP/CIDR privado (ex: 192.168.1.1) ou domínio válido (ex: empresa.com.br).")
                if escopo_limpo and not _host_matches_domain_scope(host, escopo_limpo):
                    raise PermissionError(f"Domínio '{host}' não consta na restrição de escopo definida pelo gestor.")
                return host, "host"

        # tipo_auditoria == "rede_privada"
        host_limpo = raw.strip().lower()
        if host_limpo in {"localhost", "metadata.google.internal"}:
            raise PermissionError("Alvo proibido pela política anti-SSRF.")

        try:
            net = ipaddress.ip_network(host_limpo, strict=False)
        except ValueError as exc:
            if "." in host_limpo or host_limpo.startswith(("http://", "https://")):
                raise ValueError(
                    f"Você informou um domínio/URL ('{raw}'), mas selecionou o modo 'Varredura de Rede Privada (IP/CIDR)'. Altere o modo para 'Web / TLS / Certificados & Headers OWASP' para auditar domínios!"
                ) from exc
            raise ValueError("Para auditoria de rede interna, informe um IP ou sub-rede CIDR privada válida (ex.: 192.168.1.0/24).") from exc

        if net.version != 4 or not net.is_private:
            raise PermissionError("Auditoria de rede aceita apenas faixas privadas IPv4 (RFC1918: 10.x, 172.16-31.x, 192.168.x).")
        if any(_is_forbidden_ip(addr) for addr in (net.network_address, net.broadcast_address)):
            raise PermissionError("Endereço loopback, link-local ou reservado não é permitido.")
        if net.prefixlen < 24:
            raise ValueError("Para segurança e agilidade, use no máximo blocos /24 (até 254 IPs por varredura).")

        if escopo_limpo:
            redes_escopo = []
            for entry in escopo_limpo:
                try:
                    allowed_net = ipaddress.ip_network(entry, strict=False)
                    if allowed_net.version == 4:
                        redes_escopo.append(allowed_net)
                except ValueError:
                    continue
            if redes_escopo and not any(net.subnet_of(allowed_net) for allowed_net in redes_escopo):
                raise PermissionError(
                    f"Sub-rede '{net}' não está incluída nos CIDRs restritos pelo gestor."
                )
            if not redes_escopo:
                raise PermissionError(
                    f"Sub-rede '{net}' fora da restrição de escopo configurada."
                )
        return str(net), str(net)

    @staticmethod
    def _validar_dns_anti_ssrf(host: str) -> None:
        """Impede que domínios externos apontem para IPs internos/loopback/metadata (DNS Rebinding / SSRF)."""
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        except OSError:
            return
        for info in infos:
            ip_txt = info[4][0]
            try:
                ip_obj = ipaddress.ip_address(ip_txt)
                if _is_forbidden_ip(ip_obj) or ip_obj.is_private:
                    raise PermissionError(
                        f"Bloqueio Anti-SSRF: o domínio '{host}' resolve para endereço interno/reservado ({ip_txt})."
                    )
            except ValueError:
                continue

    @staticmethod
    def auditar_web_tls(url: str, host: str) -> dict[str, Any]:
        NetworkAuditEngine._validar_dns_anti_ssrf(host)
        achados: list[dict[str, Any]] = []
        ips_resolvidos: list[str] = []
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            seen_ip: set[str] = set()
            for info in infos:
                ip_txt = str(info[4][0])
                if ip_txt not in seen_ip:
                    seen_ip.add(ip_txt)
                    ips_resolvidos.append(ip_txt)
        except OSError:
            pass

        detalhes: dict[str, Any] = {
            "host": host,
            "url": url,
            "ips_resolvidos": ips_resolvidos[:10],
            "tls": {},
            "headers": {},
            "todos_headers": {},
        }
        score = 100

        # 1. Verificação passiva de certificado SSL/TLS
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=4.0) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert() or {}
                    versao_tls = ssock.version() or ""
                    cifra_info = ssock.cipher()
                    detalhes["tls"]["versao"] = versao_tls
                    if cifra_info:
                        detalhes["tls"]["cifra"] = str(cifra_info[0])
                        detalhes["tls"]["bits"] = int(cifra_info[2]) if len(cifra_info) > 2 and cifra_info[2] else 0
                    subj_map = {k: v for pair in cert.get("subject", ()) for k, v in pair}
                    iss_map = {k: v for pair in cert.get("issuer", ()) for k, v in pair}
                    detalhes["tls"]["titular_cn"] = str(subj_map.get("commonName") or "")[:120]
                    detalhes["tls"]["emissor"] = str(
                        iss_map.get("organizationName") or iss_map.get("commonName") or ""
                    )[:120]
                    sans = [str(v) for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
                    detalhes["tls"]["sans"] = sans[:15]
                    if cert.get("notBefore"):
                        detalhes["tls"]["valido_de"] = str(cert.get("notBefore"))
                    not_after = cert.get("notAfter")
                    if not_after:
                        detalhes["tls"]["valido_ate"] = str(not_after)
                        exp_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                        dias_rest = (exp_dt - datetime.utcnow()).days
                        detalhes["tls"]["dias_para_expirar"] = dias_rest
                        if dias_rest < 0:
                            score -= 35
                            achados.append({
                                "titulo": f"Certificado TLS expirado em {host}",
                                "severidade": "Crítica",
                                "categoria": "TLS / Criptografia",
                                "evidencia": f"Host: {host} • Válido até: {not_after} ({dias_rest} dia(s)) • Emissor: {detalhes['tls'].get('emissor') or '-'} • Protocolo: {versao_tls or '-'}",
                                "recomendacao": "Renovar imediatamente o certificado SSL/TLS no servidor ou proxy reverso.",
                            })
                        elif dias_rest <= 15:
                            score -= 15
                            achados.append({
                                "titulo": f"Certificado TLS expira em {dias_rest} dia(s)",
                                "severidade": "Alta",
                                "categoria": "TLS / Criptografia",
                                "evidencia": f"Host: {host} • Válido até: {not_after} ({dias_rest} dia(s) restantes) • Emissor: {detalhes['tls'].get('emissor') or '-'} • Protocolo: {versao_tls or '-'}",
                                "recomendacao": "Verificar automação ACME / Let's Encrypt antes do vencimento.",
                            })
        except Exception as exc:
            score -= 30
            detalhes["tls"]["erro"] = str(exc)[:180]
            achados.append({
                "titulo": f"Falha ao validar TLS/HTTPS em {host}",
                "severidade": "Alta",
                "categoria": "TLS / Criptografia",
                "evidencia": f"Host: {host}:443 • IPs: {', '.join(ips_resolvidos[:3]) or '-'} • Erro TLS: {str(exc)[:140]}",
                "recomendacao": "Confirmar porta 443 ativa e cadeia de certificação válida.",
            })

        # 2. Cabeçalhos HTTP defensivos (requisição única)
        try:
            t0 = time.monotonic()
            req = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "SysFix-SecurityAuditor/2.8"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                detalhes["tempo_resposta_ms"] = int((time.monotonic() - t0) * 1000)
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                detalhes["http_status"] = getattr(resp, "status", 200)
                detalhes["url_final"] = str(getattr(resp, "geturl", lambda: url)() or url)[:200]
                detalhes["servidor"] = str(hdrs.get("server") or "")[:120]
                detalhes["x_powered_by"] = str(hdrs.get("x-powered-by") or "")[:120]
                detalhes["todos_headers"] = {
                    k[:60]: str(v)[:240] for k, v in list(hdrs.items())[:30]
                }
                obrigatorios = [
                    ("strict-transport-security", "HSTS ausente (Strict-Transport-Security)", "Média", 10, "Ativar header HSTS para forçar conexões HTTPS."),
                    ("content-security-policy", "CSP ausente (Content-Security-Policy)", "Média", 10, "Definir Content-Security-Policy para mitigar XSS."),
                    ("x-content-type-options", "X-Content-Type-Options ausente", "Baixa", 6, "Definir X-Content-Type-Options: nosniff."),
                    ("x-frame-options", "X-Frame-Options ausente", "Baixa", 6, "Definir X-Frame-Options: DENY ou SAMEORIGIN contra clickjacking."),
                    ("referrer-policy", "Referrer-Policy ausente", "Informativa", 4, "Definir Referrer-Policy: strict-origin-when-cross-origin."),
                    ("permissions-policy", "Permissions-Policy ausente", "Informativa", 2, "Definir Permissions-Policy para restringir câmera/microfone/geolocalização."),
                ]
                for h_key, msg, sev, pen, rec in obrigatorios:
                    presente = h_key in hdrs
                    detalhes["headers"][h_key] = hdrs.get(h_key, "Ausente")
                    if not presente:
                        score -= pen
                        achados.append({
                            "titulo": f"{msg} em {host}",
                            "severidade": sev,
                            "categoria": "Headers Web / OWASP",
                            "evidencia": f"URL: {detalhes.get('url_final') or url} • HTTP {detalhes.get('http_status')} ({detalhes.get('tempo_resposta_ms', 0)} ms) • Header '{h_key}' ausente na resposta • Server: {detalhes.get('servidor') or 'Oculto'}",
                            "recomendacao": rec,
                        })
        except Exception as exc:
            detalhes["http_erro"] = str(exc)[:180]
            score -= 15
            achados.append({
                "titulo": f"Endpoint HTTP/HTTPS inacessível em {host}",
                "severidade": "Média",
                "categoria": "Disponibilidade / Web",
                "evidencia": f"URL: {url} • IPs DNS: {', '.join(ips_resolvidos[:3]) or '-'} • Erro HTTP: {str(exc)[:140]}",
                "recomendacao": "Verificar se o site está online e acessível publicamente.",
            })

        detalhes["achados"] = achados
        return {
            "score": max(0, min(100, score)),
            "hosts_ativos": 1,
            "portas_expostas": 1 if "http_status" in detalhes else 0,
            "detalhes": detalhes,
            "achados_detectados": achados,
        }

    @staticmethod
    def _sondar_porta(ip_str: str, porta: int, timeout: float = 0.35) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip_str, porta)) == 0

    @staticmethod
    def _sondar_porta_detalhada(ip_str: str, porta: int, timeout: float = 0.35) -> tuple[bool, int, str]:
        t0 = time.monotonic()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            if s.connect_ex((ip_str, porta)) != 0:
                return False, 0, ""
            lat_ms = max(1, int((time.monotonic() - t0) * 1000))
            banner = ""
            try:
                s.settimeout(0.25)
                raw = s.recv(160)
                if raw:
                    banner = SecurityValidator.sanitizar_texto(
                        raw.decode("utf-8", errors="ignore")
                    )[:120]
            except OSError:
                pass
            return True, lat_ms, banner

    @classmethod
    def auditar_rede_privada(cls, cidr: str) -> dict[str, Any]:
        net = ipaddress.ip_network(cidr, strict=False)
        hosts = [str(h) for h in net.hosts()] if net.num_addresses > 1 else [str(net.network_address)]
        if len(hosts) > 64:
            hosts = hosts[:64]

        portas = list(_PORTAS_AUDITORIA_DEFENSIVA.keys())
        descobertos: list[dict[str, Any]] = []
        achados: list[dict[str, Any]] = []
        penalidade = 0

        def _scan_host(ip_str: str) -> dict[str, Any] | None:
            abertas = []
            lat_min = 0
            for p in portas:
                try:
                    ok, lat_ms, banner = cls._sondar_porta_detalhada(ip_str, p)
                    if ok:
                        servico, sev, rec = _PORTAS_AUDITORIA_DEFENSIVA[p]
                        if not lat_min or lat_ms < lat_min:
                            lat_min = lat_ms
                        abertas.append({
                            "porta": p,
                            "servico": servico,
                            "severidade": sev,
                            "banner": banner,
                            "latencia_ms": lat_ms,
                            "recomendacao": rec,
                        })
                except OSError:
                    continue
            if not abertas:
                return None
            hostname = ""
            try:
                hostname = socket.gethostbyaddr(ip_str)[0][:120]
            except OSError:
                pass
            return {
                "ip": ip_str,
                "hostname": hostname,
                "latencia_ms": lat_min,
                "portas": abertas,
            }

        with ThreadPoolExecutor(max_workers=16) as pool:
            futs = [pool.submit(_scan_host, ip_str) for ip_str in hosts]
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    descobertos.append(res)

        descobertos.sort(key=lambda h: tuple(int(x) for x in h["ip"].split(".")))
        total_portas = sum(len(h["portas"]) for h in descobertos)

        for host_info in descobertos:
            for p_info in host_info["portas"]:
                porta = p_info["porta"]
                servico, sev, rec = _PORTAS_AUDITORIA_DEFENSIVA[porta]
                if sev in {"Crítica", "Alta", "Média"}:
                    penalidade += 20 if sev == "Crítica" else (12 if sev == "Alta" else 5)
                    hn = f" ({host_info['hostname']})" if host_info.get("hostname") else ""
                    bn = f" • Banner: {p_info['banner']}" if p_info.get("banner") else ""
                    achados.append({
                        "titulo": f"Serviço {servico} (porta {porta}) exposto em {host_info['ip']}",
                        "severidade": sev,
                        "categoria": "Superfície de Ataque / Rede",
                        "evidencia": f"Host: {host_info['ip']}{hn} • Porta TCP/{porta} ({servico}) aberta • Latência: {p_info.get('latencia_ms', 0)} ms{bn}",
                        "recomendacao": rec,
                    })

        score = max(0, min(100, 100 - min(85, penalidade)))
        return {
            "score": score,
            "hosts_ativos": len(descobertos),
            "portas_expostas": total_portas,
            "detalhes": {
                "cidr": cidr,
                "hosts_verificados": len(hosts),
                "hosts": descobertos,
                "achados": achados,
            },
            "achados_detectados": achados,
        }

    @staticmethod
    def _parse_portas_custom(portas_custom: str | None) -> list[int]:
        raw = str(portas_custom or "").strip()
        if not raw:
            return list(_PORTAS_AUDITORIA_DEFENSIVA.keys())
        portas: list[int] = []
        vistos: set[int] = set()
        for token in re.split(r"[\s,;]+", raw):
            if not token:
                continue
            if "-" in token:
                partes = token.split("-", 1)
                if partes[0].isdigit() and partes[1].isdigit():
                    ini, fim = int(partes[0]), int(partes[1])
                    if 1 <= ini <= fim <= 65535 and (fim - ini) <= 100:
                        for p in range(ini, fim + 1):
                            if p not in vistos and len(portas) < 100:
                                vistos.add(p)
                                portas.append(p)
            elif token.isdigit():
                p = int(token)
                if 1 <= p <= 65535 and p not in vistos and len(portas) < 100:
                    vistos.add(p)
                    portas.append(p)
        if not portas:
            raise ValueError("Lista de portas inválida. Use portas entre 1 e 65535 (ex: 22,80,443,3389 ou 8000-8020).")
        return portas

    @staticmethod
    def _inspecionar_protocolo_porta(
        alvo_ip: str, porta: int, server_hostname: str = "", timeout: float = 0.55
    ) -> dict[str, Any] | None:
        """Descobre se a porta está aberta e inspeciona respostas/pacotes somente-leitura do protocolo."""
        t0 = time.monotonic()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            if s.connect_ex((alvo_ip, porta)) != 0:
                return None
            lat_ms = max(1, int((time.monotonic() - t0) * 1000))
            raw_bytes = b""
            protocolo_detectado = ""
            tls_resumo = ""
            try:
                if porta in {443, 8443, 465, 993, 995, 8883}:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    s.settimeout(1.2)
                    with ctx.wrap_socket(s, server_hostname=server_hostname or alvo_ip) as ssock:
                        versao = ssock.version() or "TLS"
                        cifra = ssock.cipher()
                        cifra_txt = f"{cifra[0]} ({cifra[2]} bits)" if cifra else ""
                        tls_resumo = f"{versao} {cifra_txt}".strip()
                        protocolo_detectado = f"TLS/{versao}"
                        if porta in {443, 8443}:
                            host_hdr = server_hostname or alvo_ip
                            ssock.sendall(f"HEAD / HTTP/1.1\r\nHost: {host_hdr}\r\nUser-Agent: SysFix-Inspector/2.9\r\nConnection: close\r\n\r\n".encode("ascii", errors="ignore"))
                            raw_bytes = ssock.recv(1024)
                        else:
                            raw_bytes = ssock.recv(512)
                elif porta in {80, 8080, 8000, 8008, 8888}:
                    host_hdr = server_hostname or alvo_ip
                    s.settimeout(0.8)
                    s.sendall(f"HEAD / HTTP/1.1\r\nHost: {host_hdr}\r\nUser-Agent: SysFix-Inspector/2.9\r\nConnection: close\r\n\r\n".encode("ascii", errors="ignore"))
                    raw_bytes = s.recv(1024)
                    protocolo_detectado = "HTTP"
                elif porta == 554:
                    s.settimeout(0.6)
                    s.sendall(b"OPTIONS * RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: SysFix-Inspector\r\n\r\n")
                    raw_bytes = s.recv(512)
                    protocolo_detectado = "RTSP"
                else:
                    s.settimeout(0.45)
                    raw_bytes = s.recv(512)
            except OSError:
                pass

        info_def = _PORTAS_AUDITORIA_DEFENSIVA.get(
            porta, (f"Porta TCP/{porta}", "Informativa", "Verificar se o serviço exposto nesta porta é necessário e restringir acesso por firewall.")
        )
        servico, sev, rec = info_def
        texto_decod = ""
        if raw_bytes:
            texto_decod = raw_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()[:600]
            if not protocolo_detectado:
                up = texto_decod.upper()
                if up.startswith("SSH-"):
                    protocolo_detectado = "SSH"
                elif up.startswith("HTTP/"):
                    protocolo_detectado = "HTTP"
                elif up.startswith("220 ") and "FTP" in up:
                    protocolo_detectado = "FTP"
                elif up.startswith("220 "):
                    protocolo_detectado = "SMTP"
                elif up.startswith("+OK"):
                    protocolo_detectado = "POP3"
                elif up.startswith("* OK"):
                    protocolo_detectado = "IMAP"
                elif up.startswith("RFB "):
                    protocolo_detectado = "VNC/RFB"
                else:
                    protocolo_detectado = servico

        hex_preview = " ".join(f"{b:02X}" for b in raw_bytes[:48]) if raw_bytes else ""
        banner_curto = SecurityValidator.sanitizar_texto(
            (texto_decod.splitlines()[0] if texto_decod else tls_resumo) or ""
        )[:140]

        return {
            "porta": porta,
            "transporte": "TCP",
            "servico": servico,
            "protocolo_detectado": protocolo_detectado or servico,
            "severidade": sev,
            "latencia_ms": lat_ms,
            "bytes_recebidos": len(raw_bytes),
            "banner": banner_curto,
            "tls_resumo": tls_resumo,
            "pacote_texto": texto_decod,
            "pacote_hex": hex_preview,
            "recomendacao": rec,
        }

    @classmethod
    def auditar_protocolos_e_portas(
        cls, alvo: str, alvo_tipo: str, portas_custom: str | None = None
    ) -> dict[str, Any]:
        """Descobre portas abertas e inspeciona respostas/pacotes de protocolos de forma defensiva."""
        portas = cls._parse_portas_custom(portas_custom)
        alvos_ips: list[tuple[str, str]] = []
        if alvo_tipo == "cidr":
            net = ipaddress.ip_network(alvo, strict=False)
            hosts = [str(h) for h in net.hosts()] if net.num_addresses > 1 else [str(net.network_address)]
            alvos_ips = [(ip, "") for ip in hosts[:32]]
        else:
            cls._validar_dns_anti_ssrf(alvo)
            try:
                ip_res = socket.gethostbyname(alvo)
                alvos_ips = [(ip_res, alvo)]
            except OSError as exc:
                raise ValueError(f"Não foi possível resolver o host '{alvo}': {exc}") from exc

        descobertos: list[dict[str, Any]] = []
        achados: list[dict[str, Any]] = []
        penalidade = 0

        def _scan_one_host(ip_str: str, host_ref: str) -> dict[str, Any] | None:
            abertas: list[dict[str, Any]] = []
            lat_min = 0
            with ThreadPoolExecutor(max_workers=12) as subpool:
                futs_p = [
                    subpool.submit(cls._inspecionar_protocolo_porta, ip_str, p, host_ref)
                    for p in portas
                ]
                for fp in as_completed(futs_p):
                    info_p = fp.result()
                    if info_p:
                        abertas.append(info_p)
                        if not lat_min or info_p["latencia_ms"] < lat_min:
                            lat_min = info_p["latencia_ms"]
            if not abertas:
                return None
            abertas.sort(key=lambda x: x["porta"])
            hostname = host_ref
            if not hostname:
                try:
                    hostname = socket.gethostbyaddr(ip_str)[0][:120]
                except OSError:
                    hostname = ""
            return {
                "ip": ip_str,
                "hostname": hostname,
                "latencia_ms": lat_min,
                "portas": abertas,
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(_scan_one_host, ip_s, h_ref) for ip_s, h_ref in alvos_ips]
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    descobertos.append(res)

        descobertos.sort(key=lambda h: tuple(int(x) for x in h["ip"].split(".")))
        total_portas = sum(len(h["portas"]) for h in descobertos)

        for host_info in descobertos:
            hn = f" ({host_info['hostname']})" if host_info.get("hostname") else ""
            for p_info in host_info["portas"]:
                sev = p_info["severidade"]
                if sev in {"Crítica", "Alta", "Média"}:
                    penalidade += 20 if sev == "Crítica" else (12 if sev == "Alta" else 5)
                    bn = f" • Resposta: {p_info['banner']}" if p_info.get("banner") else ""
                    achados.append({
                        "titulo": f"Serviço {p_info['servico']} (porta {p_info['porta']}/{p_info['transporte']}) aberta em {host_info['ip']}",
                        "severidade": sev,
                        "categoria": "Portas & Protocolos",
                        "evidencia": f"Host: {host_info['ip']}{hn} • Porta {p_info['transporte']}/{p_info['porta']} • Protocolo: {p_info['protocolo_detectado']} • Latência: {p_info['latencia_ms']} ms • Bytes: {p_info['bytes_recebidos']}{bn}",
                        "recomendacao": p_info["recomendacao"],
                    })

        score = max(0, min(100, 100 - min(85, penalidade)))
        return {
            "score": score,
            "hosts_ativos": len(descobertos),
            "portas_expostas": total_portas,
            "detalhes": {
                "alvo": alvo,
                "portas_verificadas": portas,
                "hosts_verificados": len(alvos_ips),
                "hosts": descobertos,
                "achados": achados,
            },
            "achados_detectados": achados,
        }

    @staticmethod
    def gerar_documento_auditoria(auditoria: dict[str, Any], formato: str = "txt") -> tuple[bytes, str, str]:
        """Gera documento completo (.txt, .md ou .json) com todo o conteúdo encontrado na varredura."""
        fmt = (formato or "txt").strip().lower()
        aud_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(auditoria.get("id") or "auditoria"))
        if fmt == "json":
            return (
                json.dumps(auditoria, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json; charset=utf-8",
                f"relatorio_{aud_id}.json",
            )

        det = auditoria.get("detalhes") or {}
        achados = auditoria.get("achados") or det.get("achados") or []
        linhas: list[str] = [
            "========================================================================",
            "SYSFIX CIBERSEGURANÇA — RELATÓRIO COMPLETO DE PORTAS, PROTOCOLOS E PACOTES",
            "========================================================================",
            f"ID da Auditoria : {auditoria.get('id', '-')}",
            f"Alvo Auditado   : {auditoria.get('alvo', '-')}",
            f"Modo            : {auditoria.get('tipo', '-')}",
            f"Score           : {auditoria.get('score', 0)}/100",
            f"Hosts Ativos    : {auditoria.get('hosts_ativos', 0)}",
            f"Portas Abertas  : {auditoria.get('portas_expostas', 0)}",
            f"Executado por   : {auditoria.get('executado_por', 'Sistema')}",
            f"Data / Hora     : {auditoria.get('executado_em', '-')}",
            "",
        ]

        if auditoria.get("tipo") == "web_tls":
            tls = det.get("tls") or {}
            linhas.append("--- DNS & HTTP ---")
            linhas.append(f"IPs Resolvidos  : {', '.join(det.get('ips_resolvidos') or []) or '-'}")
            linhas.append(f"HTTP Status     : {det.get('http_status', det.get('http_erro', '-'))} ({det.get('tempo_resposta_ms', 0)} ms)")
            linhas.append(f"URL Final       : {det.get('url_final', det.get('url', '-'))}")
            linhas.append(f"Servidor        : {det.get('servidor') or 'Oculto'}")
            linhas.append("")
            linhas.append("--- CERTIFICADO SSL/TLS ---")
            for k, v in tls.items():
                linhas.append(f"  {k}: {v}")
            linhas.append("")
            linhas.append("--- HEADERS OWASP ---")
            for k, v in (det.get("headers") or {}).items():
                linhas.append(f"  {k}: {v}")
            linhas.append("")
            linhas.append("--- TODOS OS HEADERS HTTP RECEBIDOS ---")
            for k, v in (det.get("todos_headers") or {}).items():
                linhas.append(f"  {k}: {v}")
            linhas.append("")
        else:
            portas_verif = det.get("portas_verificadas") or []
            if portas_verif:
                linhas.append(f"Portas verificadas ({len(portas_verif)}): {', '.join(str(p) for p in portas_verif)}")
                linhas.append("")
            hosts = det.get("hosts") or []
            linhas.append(f"--- HOSTS, PORTAS ABERTAS & CONTEÚDO DOS PROTOCOLOS ({len(hosts)} host(s)) ---")
            if not hosts:
                linhas.append("Nenhum host com porta aberta encontrado na faixa/alvo verificado.")
            for h in hosts:
                hn = f" ({h.get('hostname')})" if h.get("hostname") else ""
                linhas.append("")
                linhas.append(f"HOST: {h.get('ip', '-')}{hn} | Latência mín.: {h.get('latencia_ms', 0)} ms")
                for p in h.get("portas") or []:
                    if not isinstance(p, dict):
                        linhas.append(f"  - Porta {p} aberta")
                        continue
                    linhas.append(
                        f"  [PORTA {p.get('porta')}/{p.get('transporte', 'TCP')}] "
                        f"Serviço: {p.get('servico', '-')} | Protocolo: {p.get('protocolo_detectado', p.get('servico', '-'))} "
                        f"| Risco: {p.get('severidade', '-')} | Latência: {p.get('latencia_ms', 0)} ms "
                        f"| Bytes: {p.get('bytes_recebidos', 0)}"
                    )
                    if p.get("tls_resumo"):
                        linhas.append(f"    TLS/Cifra      : {p.get('tls_resumo')}")
                    if p.get("banner"):
                        linhas.append(f"    Banner/Resumo  : {p.get('banner')}")
                    if p.get("pacote_texto"):
                        linhas.append("    Conteúdo do Pacote / Resposta do Protocolo:")
                        for ln_p in str(p.get("pacote_texto")).splitlines()[:25]:
                            linhas.append(f"      | {ln_p}")
                    if p.get("pacote_hex"):
                        linhas.append(f"    Hex (primeiros bytes): {p.get('pacote_hex')}")
                    if p.get("recomendacao"):
                        linhas.append(f"    Mitigação      : {p.get('recomendacao')}")
            linhas.append("")

        linhas.append(f"--- ACHADOS E VULNERABILIDADES ({len(achados)}) ---")
        if not achados:
            linhas.append("Nenhuma vulnerabilidade crítica ou pendência registrada.")
        for idx, ach in enumerate(achados, start=1):
            linhas.append(f"{idx}. [{ach.get('severidade', 'Média')}] {ach.get('titulo', '-')}")
            if ach.get("evidencia"):
                linhas.append(f"   Evidência   : {ach.get('evidencia')}")
            if ach.get("recomendacao"):
                linhas.append(f"   Recomendação: {ach.get('recomendacao')}")

        texto = "\n".join(linhas) + "\n"
        ext = "md" if fmt == "md" else "txt"
        mime = "text/markdown; charset=utf-8" if ext == "md" else "text/plain; charset=utf-8"
        return texto.encode("utf-8"), mime, f"relatorio_portas_protocolos_{aud_id}.{ext}"

    @staticmethod
    def gerar_script_coletor_local(formato: str, escopo: list[str]) -> tuple[bytes, str, str]:
        escopo_str = ", ".join(escopo) if escopo else "192.168.1.0/24"
        fmt = (formato or "ps1").strip().lower()
        if fmt == "ps1":
            conteudo = (
                "# SysFix Security Collector (Somente-Leitura / LAN Local)\n"
                f"# Escopo autorizado: {escopo_str}\n"
                "param([string]$Subrede = '192.168.1')\n"
                "$portas = @(22, 23, 80, 443, 445, 554, 1883, 3389, 8080, 8291)\n"
                "$hosts = @()\n"
                "1..254 | ForEach-Object {\n"
                "  $ip = \"$Subrede.$_\"\n"
                "  $abertas = @()\n"
                "  foreach ($p in $portas) {\n"
                "    $c = New-Object System.Net.Sockets.TcpClient\n"
                "    $ar = $c.BeginConnect($ip, $p, $null, $null)\n"
                "    if ($ar.AsyncWaitHandle.WaitOne(180, $false) -and $c.Connected) {\n"
                "      $abertas += @{ porta = $p }\n"
                "    }\n"
                "    $c.Close()\n"
                "  }\n"
                "  if ($abertas.Count -gt 0) { $hosts += @{ ip = $ip; portas = $abertas } }\n"
                "}\n"
                "$out = @{ tipo = 'rede_privada'; alvo = \"$Subrede.0/24\"; hosts = $hosts; gerado_em = (Get-Date -Format o) }\n"
                "$out | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 'auditoria_sysfix_lan.json'\n"
                "Write-Host 'Relatorio salvo em auditoria_sysfix_lan.json'\n"
            ).encode("utf-8")
            return conteudo, "text/plain; charset=utf-8", "sysfix_coletor_lan.ps1"

        if fmt == "py":
            conteudo = (
                "#!/usr/bin/env python3\n"
                f"# SysFix Security Collector (Somente-Leitura) - Escopo: {escopo_str}\n"
                "import ipaddress, json, socket, sys\n"
                "from datetime import datetime, timezone\n"
                "cidr = sys.argv[1] if len(sys.argv) > 1 else '192.168.1.0/24'\n"
                "portas = [22, 23, 80, 443, 445, 554, 1883, 3389, 8080, 8291, 8443]\n"
                "hosts = []\n"
                "for ip in ipaddress.ip_network(cidr, strict=False).hosts():\n"
                "    abertas = []\n"
                "    for p in portas:\n"
                "        with socket.socket() as s:\n"
                "            s.settimeout(0.2)\n"
                "            if s.connect_ex((str(ip), p)) == 0:\n"
                "                abertas.append({'porta': p})\n"
                "    if abertas:\n"
                "        hosts.append({'ip': str(ip), 'portas': abertas})\n"
                "payload = {'tipo': 'rede_privada', 'alvo': cidr, 'hosts': hosts, 'gerado_em': datetime.now(timezone.utc).isoformat()}\n"
                "with open('auditoria_sysfix_lan.json', 'w', encoding='utf-8') as f:\n"
                "    json.dump(payload, f, indent=2)\n"
                "print('Concluido: auditoria_sysfix_lan.json')\n"
            ).encode("utf-8")
            return conteudo, "text/x-python; charset=utf-8", "sysfix_coletor_lan.py"

        raise ValueError("Formato de coletor inválido. Use 'ps1' ou 'py'.")


class AutomationCyberService:
    """Serviços multi-tenant dos setores Automação (CFTV/Infra/IoT/Alarmes) e Cibersegurança."""

    def __init__(self, store=None):
        self._store = store

    @property
    def store(self):
        return self._store if self._store is not None else get_security_ops_store()

    # ==================================================================
    # SETOR 1: AUTOMAÇÃO (CÂMERAS, INFRAESTRUTURA, IOT & ALARMES)
    # ==================================================================

    def listar_automacao(self, tenant_id: str, pilar: str | None = None) -> list[dict]:
        itens = self.store.list_items("automacao_ativos", tenant_id)
        if pilar:
            p_norm = SecurityValidator.sanitizar_texto(pilar).lower()
            itens = [i for i in itens if str(i.get("pilar") or "").lower() == p_norm]
        return sorted(itens, key=lambda i: (str(i.get("pilar") or ""), str(i.get("nome") or "").lower()))

    def resumo_automacao(self, tenant_id: str) -> dict[str, Any]:
        ativos = self.listar_automacao(tenant_id)
        por_pilar = {k: 0 for k in AUTOMACAO_PILARES}
        conformes = 0
        alertas = 0
        criticos = 0
        for item in ativos:
            p = item.get("pilar")
            if p in por_pilar:
                por_pilar[p] += 1
            st = item.get("status_auditoria") or "Alerta"
            if st == "Conforme":
                conformes += 1
            elif st == "Crítico":
                criticos += 1
            else:
                alertas += 1
        total = len(ativos)
        taxa = round((conformes / total) * 100, 1) if total > 0 else 100.0
        return {
            "total_ativos": total,
            "conformes": conformes,
            "alertas": alertas,
            "criticos": criticos,
            "taxa_conformidade_percent": taxa,
            "por_pilar": por_pilar,
        }

    def criar_ativo_automacao(self, tenant_id: str, payload: dict, actor: str = "") -> dict:
        pilar = SecurityValidator.sanitizar_texto(payload.get("pilar")).lower()
        if pilar not in AUTOMACAO_PILARES:
            raise ValueError("Pilar de automação inválido. Use: cameras, infraestrutura, iot ou alarmes.")
        nome = SecurityValidator.sanitizar_texto(payload.get("nome"))
        if not nome:
            raise ValueError("Informe o nome ou identificação do dispositivo/ativo.")

        checks = {
            "senha_padrao_alterada": bool(payload.get("senha_padrao_alterada")),
            "firmware_atualizado": bool(payload.get("firmware_atualizado")),
            "rede_segmentada": bool(payload.get("rede_segmentada")),
            "energia_backup_ok": bool(payload.get("energia_backup_ok")),
            "monitoramento_ativo": bool(payload.get("monitoramento_ativo")),
        }
        status_manual = SecurityValidator.sanitizar_texto(payload.get("status_auditoria"))
        status_final = _calcular_status_automacao(checks, status_manual)
        agora = _now_iso()
        item = {
            "id": f"aut_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "pilar": pilar,
            "nome": nome[:140],
            "subtipo": SecurityValidator.sanitizar_texto(payload.get("subtipo"))[:80],
            "localizacao": SecurityValidator.sanitizar_texto(payload.get("localizacao"))[:120],
            "ip_mac": SecurityValidator.sanitizar_texto(payload.get("ip_mac"))[:100],
            "vlan": SecurityValidator.sanitizar_texto(payload.get("vlan"))[:40],
            "fabricante_modelo": SecurityValidator.sanitizar_texto(payload.get("fabricante_modelo"))[:100],
            "firmware": SecurityValidator.sanitizar_texto(payload.get("firmware"))[:60],
            "checks": checks,
            "status_auditoria": status_final,
            "observacoes": SecurityValidator.sanitizar_texto(payload.get("observacoes"))[:500],
            "ultima_auditoria": SecurityValidator.validar_data(payload.get("ultima_auditoria")) if payload.get("ultima_auditoria") else agora[:10],
            "auditado_por": SecurityValidator.sanitizar_texto(actor) or "Sistema",
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self.store.upsert_item("automacao_ativos", item, tenant_id)

    def atualizar_ativo_automacao(
        self, tenant_id: str, item_id: str, payload: dict, actor: str = ""
    ) -> dict | None:
        existente = self.store.get_item("automacao_ativos", item_id, tenant_id)
        if not existente:
            return None
        checks = dict(existente.get("checks") or {})
        for c_key in (
            "senha_padrao_alterada",
            "firmware_atualizado",
            "rede_segmentada",
            "energia_backup_ok",
            "monitoramento_ativo",
        ):
            if c_key in payload and payload[c_key] is not None:
                checks[c_key] = bool(payload[c_key])

        status_manual = SecurityValidator.sanitizar_texto(payload.get("status_auditoria"))
        updates: dict[str, Any] = {
            "checks": checks,
            "status_auditoria": _calcular_status_automacao(checks, status_manual or None),
            "atualizado_em": _now_iso(),
            "auditado_por": SecurityValidator.sanitizar_texto(actor) or existente.get("auditado_por", ""),
        }
        for campo, limite in (
            ("nome", 140),
            ("subtipo", 80),
            ("localizacao", 120),
            ("ip_mac", 100),
            ("vlan", 40),
            ("fabricante_modelo", 100),
            ("firmware", 60),
            ("observacoes", 500),
        ):
            if campo in payload and payload[campo] is not None:
                val = SecurityValidator.sanitizar_texto(payload[campo])[:limite]
                if campo == "nome" and not val:
                    raise ValueError("O nome do ativo não pode ficar vazio.")
                updates[campo] = val
        return self.store.update_item("automacao_ativos", item_id, updates, tenant_id)

    def excluir_ativo_automacao(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("automacao_ativos", item_id, tenant_id)

    # ==================================================================
    # SETOR 2: CIBERSEGURANÇA (AUDITORIAS, ACHADOS, CHECKLISTS & KB)
    # ==================================================================

    def listar_auditorias(self, tenant_id: str) -> list[dict]:
        rows = self.store.list_items("cyber_auditorias", tenant_id)
        return sorted(rows, key=lambda r: str(r.get("executado_em") or ""), reverse=True)

    def obter_auditoria(self, tenant_id: str, auditoria_id: str) -> dict | None:
        return self.store.get_item("cyber_auditorias", auditoria_id, tenant_id)

    def executar_auditoria_rede(
        self,
        tenant: dict,
        alvo: str,
        tipo_auditoria: str = "web_tls",
        registrar_achados: bool = True,
        actor: str = "",
        user_uid: str = "",
        user_email: str = "",
        is_admin: bool = False,
        portas_custom: str = "",
    ) -> dict[str, Any]:
        gov = tenant.get("cyber_governanca") or {}
        if not gov.get("permitir_auditoria_rede"):
            raise PermissionError(
                "Auditoria de rede bloqueada: o gestor precisa habilitar a autorização de tarefas na aba Gov-Key."
            )
        verificar_operador_autorizado_govkey(
            tenant, user_uid=user_uid, user_email=user_email, is_admin=is_admin
        )

        tipo_norm = SecurityValidator.sanitizar_texto(tipo_auditoria).lower() or "web_tls"
        if tipo_norm not in {"web_tls", "rede_privada", "protocolos_pacotes"}:
            raise ValueError("Tipo de auditoria inválido. Use 'web_tls', 'rede_privada' ou 'protocolos_pacotes'.")

        escopo = list(gov.get("escopo_autorizado") or [])
        alvo_norm, alvo_subtipo = NetworkAuditEngine.validar_alvo_autorizado(alvo, tipo_norm, escopo)

        if tipo_norm == "web_tls":
            resultado = NetworkAuditEngine.auditar_web_tls(alvo_norm, urllib.parse.urlparse(alvo_norm).hostname or alvo_norm)
        elif tipo_norm == "protocolos_pacotes":
            resultado = NetworkAuditEngine.auditar_protocolos_e_portas(
                alvo_norm, alvo_subtipo, portas_custom=portas_custom
            )
        else:
            resultado = NetworkAuditEngine.auditar_rede_privada(alvo_norm)

        agora = _now_iso()
        auditoria = {
            "id": f"aud_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant["id"],
            "tipo": tipo_norm,
            "alvo": alvo_norm,
            "origem": "execucao_remota_autorizada",
            "score": resultado["score"],
            "hosts_ativos": resultado["hosts_ativos"],
            "portas_expostas": resultado["portas_expostas"],
            "achados_count": len(resultado["achados_detectados"]),
            "achados": resultado["achados_detectados"],
            "detalhes": resultado["detalhes"],
            "executado_por": SecurityValidator.sanitizar_texto(actor) or "Sistema",
            "executado_em": agora,
        }
        salva = self.store.upsert_item("cyber_auditorias", auditoria, tenant["id"])

        achados_criados = 0
        if registrar_achados:
            for item in resultado["achados_detectados"][:25]:
                self.criar_achado(
                    tenant["id"],
                    {
                        "titulo": item["titulo"],
                        "severidade": item["severidade"],
                        "categoria": item["categoria"],
                        "alvo": alvo_norm,
                        "status": "Aberto",
                        "evidencia": item.get("evidencia", ""),
                        "origem": f"Byte-Scanner ({tipo_norm})",
                        "recomendacao": item["recomendacao"],
                        "auditoria_id": salva["id"],
                    },
                    actor=actor,
                )
                achados_criados += 1

        return {"auditoria": salva, "achados_criados": achados_criados}

    def importar_auditoria_json(
        self, tenant_id: str, payload: dict, actor: str = ""
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Payload JSON de auditoria inválido.")
        hosts = payload.get("hosts")
        if not isinstance(hosts, list):
            raise ValueError("O JSON importado precisa conter a lista 'hosts'.")
        alvo = SecurityValidator.sanitizar_texto(payload.get("alvo")) or "LAN Local"

        total_portas = 0
        achados_detectados: list[dict[str, Any]] = []
        penalidade = 0
        for h in hosts[:254]:
            if not isinstance(h, dict):
                continue
            ip_str = SecurityValidator.sanitizar_texto(h.get("ip")) or "Host"
            for p_obj in h.get("portas") or []:
                porta = int(p_obj.get("porta") if isinstance(p_obj, dict) else p_obj)
                total_portas += 1
                info = _PORTAS_AUDITORIA_DEFENSIVA.get(porta)
                if info:
                    servico, sev, rec = info
                    if sev in {"Crítica", "Alta", "Média"}:
                        penalidade += 18 if sev == "Crítica" else (10 if sev == "Alta" else 4)
                        achados_detectados.append({
                            "titulo": f"Serviço {servico} (porta {porta}) em {ip_str}",
                            "severidade": sev,
                            "categoria": "Coletor LAN Local",
                            "recomendacao": rec,
                        })

        score = max(0, min(100, 100 - min(85, penalidade)))
        agora = _now_iso()
        auditoria = {
            "id": f"aud_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "tipo": "coletor_lan_json",
            "alvo": alvo,
            "origem": "coletor_local_importado",
            "score": score,
            "hosts_ativos": len(hosts),
            "portas_expostas": total_portas,
            "achados_count": len(achados_detectados),
            "achados": achados_detectados,
            "detalhes": {"hosts": hosts[:100], "achados": achados_detectados},
            "executado_por": SecurityValidator.sanitizar_texto(actor) or "Sistema",
            "executado_em": agora,
        }
        salva = self.store.upsert_item("cyber_auditorias", auditoria, tenant_id)
        for item in achados_detectados[:30]:
            self.criar_achado(
                tenant_id,
                {
                    **item,
                    "alvo": alvo,
                    "status": "Aberto",
                    "auditoria_id": salva["id"],
                },
                actor=actor,
            )
        return {"auditoria": salva, "achados_criados": len(achados_detectados[:30])}

    def listar_achados(self, tenant_id: str) -> list[dict]:
        sev_order = {s: idx for idx, s in enumerate(CYBER_SEVERIDADES)}
        rows = self.store.list_items("cyber_achados", tenant_id)
        ordenados = sorted(
            rows,
            key=lambda r: (
                1 if r.get("status") == "Resolvido" else 0,
                sev_order.get(r.get("severidade"), 99),
                str(r.get("criado_em") or ""),
            ),
        )
        return [_enriquecer_achado_patchbot(r) for r in ordenados]

    def criar_achado(self, tenant_id: str, payload: dict, actor: str = "") -> dict:
        titulo = SecurityValidator.sanitizar_texto(payload.get("titulo"))
        if not titulo:
            raise ValueError("Informe o título do achado ou vulnerabilidade.")
        sev = SecurityValidator.sanitizar_texto(payload.get("severidade"))
        if sev not in CYBER_SEVERIDADES:
            sev = "Média"
        st = SecurityValidator.sanitizar_texto(payload.get("status"))
        if st not in CYBER_ACHADO_STATUS:
            st = "Aberto"
        cvss_val = None
        if payload.get("cvss_score") not in (None, ""):
            try:
                cvss_val = round(max(0.0, min(10.0, float(payload.get("cvss_score")))), 1)
            except (TypeError, ValueError):
                cvss_val = None
        agora = _now_iso()
        item = {
            "id": f"ach_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "titulo": titulo[:180],
            "severidade": sev,
            "categoria": SecurityValidator.sanitizar_texto(payload.get("categoria"))[:80] or "Geral",
            "alvo": SecurityValidator.sanitizar_texto(payload.get("alvo"))[:120],
            "status": st,
            "recomendacao": SecurityValidator.sanitizar_texto(payload.get("recomendacao"))[:600],
            "cve_cwe": SecurityValidator.sanitizar_texto(payload.get("cve_cwe"))[:100],
            "cvss_score": cvss_val,
            "evidencia": SecurityValidator.sanitizar_texto(payload.get("evidencia"))[:800],
            "impacto": SecurityValidator.sanitizar_texto(payload.get("impacto"))[:500],
            "responsavel": SecurityValidator.sanitizar_texto(payload.get("responsavel"))[:80],
            "prazo_sla": SecurityValidator.sanitizar_texto(payload.get("prazo_sla"))[:40],
            "notas_remediacao": SecurityValidator.sanitizar_texto(payload.get("notas_remediacao"))[:800],
            "origem": SecurityValidator.sanitizar_texto(payload.get("origem"))[:80],
            "auditoria_id": SecurityValidator.sanitizar_texto(payload.get("auditoria_id"))[:40],
            "criado_por": SecurityValidator.sanitizar_texto(actor) or "Sistema",
            "criado_em": agora,
            "atualizado_em": agora,
        }
        salvo = self.store.upsert_item("cyber_achados", item, tenant_id)
        return _enriquecer_achado_patchbot(salvo)

    def atualizar_achado(self, tenant_id: str, item_id: str, payload: dict) -> dict | None:
        existente = self.store.get_item("cyber_achados", item_id, tenant_id)
        if not existente:
            return None
        updates: dict[str, Any] = {"atualizado_em": _now_iso()}
        if "status" in payload and payload["status"] is not None:
            st = SecurityValidator.sanitizar_texto(payload["status"])
            if st not in CYBER_ACHADO_STATUS:
                raise ValueError("Status de achado inválido.")
            updates["status"] = st
        if "severidade" in payload and payload["severidade"] is not None:
            sev = SecurityValidator.sanitizar_texto(payload["severidade"])
            if sev not in CYBER_SEVERIDADES:
                raise ValueError("Severidade inválida.")
            updates["severidade"] = sev
        if "cvss_score" in payload and payload["cvss_score"] not in (None, ""):
            try:
                updates["cvss_score"] = round(max(0.0, min(10.0, float(payload["cvss_score"]))), 1)
            except (TypeError, ValueError):
                pass
        for campo, limite in (
            ("recomendacao", 600),
            ("cve_cwe", 100),
            ("evidencia", 800),
            ("impacto", 500),
            ("responsavel", 80),
            ("prazo_sla", 40),
            ("notas_remediacao", 800),
        ):
            if campo in payload and payload[campo] is not None:
                updates[campo] = SecurityValidator.sanitizar_texto(payload[campo])[:limite]
        atualizado = self.store.update_item("cyber_achados", item_id, updates, tenant_id)
        return _enriquecer_achado_patchbot(atualizado) if atualizado else None

    def excluir_achado(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("cyber_achados", item_id, tenant_id)

    def obter_checklist_cyber(self, tenant_id: str) -> list[dict]:
        salvos = self.store.list_items("cyber_checklists", tenant_id)
        if salvos:
            doc = next((s for s in salvos if s.get("id") == "principal"), salvos[0])
            itens = doc.get("itens")
            if isinstance(itens, list) and itens:
                return itens
        return [dict(item) for item in CYBER_COMPLIANCE_TEMPLATE]

    def salvar_checklist_cyber(
        self, tenant_id: str, itens: list[dict], actor: str = ""
    ) -> list[dict]:
        if not isinstance(itens, list) or not itens:
            raise ValueError("Informe uma lista válida de controles de conformidade.")
        limpos: list[dict] = []
        valid_status = {"Conforme", "Parcial", "Pendente", "Não Aplicável"}
        for idx, entry in enumerate(itens[:40], start=1):
            if not isinstance(entry, dict):
                continue
            controle = SecurityValidator.sanitizar_texto(entry.get("controle"))
            if not controle:
                continue
            st = SecurityValidator.sanitizar_texto(entry.get("status"))
            if st not in valid_status:
                st = "Pendente"
            limpos.append({
                "id": SecurityValidator.sanitizar_texto(entry.get("id")) or f"chk_{idx}",
                "framework": SecurityValidator.sanitizar_texto(entry.get("framework")) or "CIS v8",
                "controle": controle[:220],
                "status": st,
                "responsavel": SecurityValidator.sanitizar_texto(entry.get("responsavel"))[:80],
            })
        if not limpos:
            raise ValueError("Nenhum item válido no checklist de cibersegurança.")
        self.store.upsert_item(
            "cyber_checklists",
            {
                "id": "principal",
                "tenant_id": tenant_id,
                "itens": limpos,
                "atualizado_por": SecurityValidator.sanitizar_texto(actor),
                "atualizado_em": _now_iso(),
            },
            tenant_id,
        )
        return limpos

    def listar_conhecimento(self, tenant_id: str, busca: str = "") -> list[dict]:
        custom = self.store.list_items("cyber_kb", tenant_id)
        todos = [{**item, "padrao_sistema": True} for item in CYBER_KNOWLEDGE_BASE] + [
            {**item, "padrao_sistema": False} for item in custom
        ]
        termo = (busca or "").strip().lower()
        if termo:
            todos = [
                art
                for art in todos
                if termo in str(art.get("titulo") or "").lower()
                or termo in str(art.get("categoria") or "").lower()
                or termo in str(art.get("resumo") or "").lower()
                or termo in str(art.get("conteudo") or "").lower()
                or any(termo in str(t).lower() for t in (art.get("tags") or []))
            ]
        return todos

    def criar_artigo_conhecimento(
        self, tenant_id: str, payload: dict, actor: str = ""
    ) -> dict:
        titulo = SecurityValidator.sanitizar_texto(payload.get("titulo"))
        conteudo = str(payload.get("conteudo") or "").strip()
        if not titulo or not conteudo:
            raise ValueError("Informe título e conteúdo do guia/playbook.")
        tags_raw = payload.get("tags")
        if isinstance(tags_raw, str):
            tags = [SecurityValidator.sanitizar_texto(t) for t in tags_raw.split(",") if SecurityValidator.sanitizar_texto(t)]
        elif isinstance(tags_raw, list):
            tags = [SecurityValidator.sanitizar_texto(t) for t in tags_raw if SecurityValidator.sanitizar_texto(t)]
        else:
            tags = []
        agora = _now_iso()
        artigo = {
            "id": f"kb_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "categoria": SecurityValidator.sanitizar_texto(payload.get("categoria"))[:60] or "Playbook Interno",
            "nivel": SecurityValidator.sanitizar_texto(payload.get("nivel"))[:30] or "Operacional",
            "atualizado_em": agora[:7],
            "titulo": titulo[:160],
            "resumo": SecurityValidator.sanitizar_texto(payload.get("resumo"))[:240] or titulo[:160],
            "conteudo": conteudo[:6000],
            "tags": tags[:10],
            "criado_por": SecurityValidator.sanitizar_texto(actor) or "Equipe",
        }
        return self.store.upsert_item("cyber_kb", artigo, tenant_id)

    def excluir_artigo_conhecimento(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("cyber_kb", item_id, tenant_id)

    def resumo_cyber(self, tenant: dict) -> dict[str, Any]:
        tenant_id = tenant["id"]
        auditorias = self.listar_auditorias(tenant_id)
        achados = self.listar_achados(tenant_id)
        checklist = self.obter_checklist_cyber(tenant_id)

        abertos = [a for a in achados if a.get("status") in {"Aberto", "Em Mitigação"}]
        criticos_abertos = sum(1 for a in abertos if a.get("severidade") == "Crítica")
        altos_abertos = sum(1 for a in abertos if a.get("severidade") == "Alta")

        total_chk = len(checklist)
        conformes_chk = sum(1 for c in checklist if c.get("status") == "Conforme")
        parciais_chk = sum(1 for c in checklist if c.get("status") == "Parcial")
        maturidade_chk = (
            round(((conformes_chk + 0.5 * parciais_chk) / total_chk) * 100, 1)
            if total_chk > 0
            else 0.0
        )
        penalidade_achados = min(45, criticos_abertos * 15 + altos_abertos * 7)
        score_maturidade = max(0, round(maturidade_chk - penalidade_achados, 1))

        return {
            "maturidade_percent": score_maturidade,
            "total_auditorias": len(auditorias),
            "achados_abertos": len(abertos),
            "achados_criticos": criticos_abertos,
            "checklist_conformes": conformes_chk,
            "checklist_total": total_chk,
            "total_automacoes": len(self.listar_automacoes_defesa(tenant_id)),
            "governanca": tenant.get("cyber_governanca") or {
                "permitir_auditoria_rede": False,
                "usuarios_autorizados": [],
                "restringir_usuarios": False,
                "escopo_autorizado": [],
                "observacao_termo": "",
                "autorizado_por": "",
                "autorizado_em": "",
            },
            "ultimas_auditorias": auditorias[:5],
        }

    # ==================================================================
    # WIKI-CORE: NOTÍCIAS REAIS EXTERNAS SEPARADAS DOS PLAYBOOKS INTERNOS
    # ==================================================================

    def listar_wiki_separado(
        self, tenant_id: str, busca: str = "", atualizar_noticias: bool = False
    ) -> dict[str, Any]:
        internos_raw = self.store.list_items("cyber_kb", tenant_id)
        internos = sorted(
            [{**item, "padrao_sistema": False, "origem_tipo": "interno"} for item in internos_raw],
            key=lambda x: str(x.get("atualizado_em") or ""),
            reverse=True,
        )
        guias_base = [
            {**item, "padrao_sistema": True, "origem_tipo": "referencia"}
            for item in CYBER_KNOWLEDGE_BASE
        ]
        noticias_bundle = CyberNewsFeedService.obter_noticias(forcar=atualizar_noticias)
        noticias = list(noticias_bundle.get("itens") or [])

        termo = (busca or "").strip().lower()
        if termo:
            def _match_doc(art: dict) -> bool:
                return (
                    termo in str(art.get("titulo") or "").lower()
                    or termo in str(art.get("categoria") or "").lower()
                    or termo in str(art.get("resumo") or "").lower()
                    or termo in str(art.get("conteudo") or "").lower()
                    or any(termo in str(t).lower() for t in (art.get("tags") or []))
                )

            internos = [a for a in internos if _match_doc(a)]
            guias_base = [a for a in guias_base if _match_doc(a)]
            noticias = [
                n
                for n in noticias
                if termo in str(n.get("titulo") or "").lower()
                or termo in str(n.get("resumo") or "").lower()
                or termo in str(n.get("fonte") or "").lower()
                or termo in str(n.get("categoria") or "").lower()
            ]

        return {
            "noticias_mundo_cyber": {
                "ao_vivo": bool(noticias_bundle.get("ao_vivo")),
                "atualizado_em": noticias_bundle.get("atualizado_em", ""),
                "fontes_ativas": noticias_bundle.get("fontes_ativas", []),
                "itens": noticias,
            },
            "publicados_internos": internos,
            "guias_referencia": guias_base,
        }

    # ==================================================================
    # LABORATÓRIO DIDÁTICO ATAQUE × DEFESA (SIMULAÇÃO PURPLE-TEAM SEGURA)
    # ==================================================================

    def listar_cenarios_didaticos(self) -> list[dict[str, Any]]:
        return [dict(c) for c in CYBER_DIDACTIC_SCENARIOS]

    def simular_cenario_didatico(
        self,
        tenant: dict,
        cenario_id: str,
        users_list: list[dict] | None = None,
        registrar_plano: bool = False,
        actor: str = "",
    ) -> dict[str, Any]:
        cenario = next((c for c in CYBER_DIDACTIC_SCENARIOS if c["id"] == cenario_id), None)
        if not cenario:
            raise ValueError("Cenário didático não encontrado.")

        tid = tenant["id"]
        achados = self.listar_achados(tid)
        abertos = [a for a in achados if a.get("status") in {"Aberto", "Em Mitigação"}]
        checklist = {c.get("id"): c.get("status") for c in self.obter_checklist_cyber(tid)}
        ativos_aut = self.listar_automacao(tid)
        usuarios = users_list or []

        verificacoes: list[dict[str, Any]] = []
        falhas_detectadas: list[dict[str, str]] = []

        if cenario_id == "ransomware_smb_rdp":
            portas_crit = [
                a for a in abertos
                if any(k in str(a.get("titulo") or "").lower() for k in ("445", "smb", "3389", "rdp", "telnet"))
            ]
            verificacoes.append({
                "item": "Exposição de portas críticas (SMB 445 / RDP 3389 / Telnet 23)",
                "ok": len(portas_crit) == 0,
                "detalhe": "Nenhuma porta crítica aberta registrada." if not portas_crit else f"{len(portas_crit)} alerta(s) aberto(s) na fila.",
            })
            bkp_ok = checklist.get("chk_backup") == "Conforme"
            verificacoes.append({
                "item": "Backup imutável/offline 3-2-1-1-0 validado no checklist CIS",
                "ok": bkp_ok,
                "detalhe": "Controle de backup marcado como Conforme." if bkp_ok else "Backup 3-2-1-1-0 ainda não está Conforme.",
            })
            if not bkp_ok:
                falhas_detectadas.append({
                    "titulo": "[Simulação Ransomware] Backup 3-2-1-1-0 pendente de validação",
                    "severidade": "Crítica",
                    "categoria": "Defesa Anti-Ransomware",
                    "recomendacao": "Configurar cópia offline/imutável e testar restauração mensalmente.",
                })

        elif cenario_id == "bruteforce_credenciais":
            total_u = len(usuarios)
            sem_mfa = [u for u in usuarios if u.get("ativo", True) and not u.get("mfa_enabled")]
            mfa_pol = bool(tenant.get("exigir_mfa_equipe")) or checklist.get("chk_mfa") == "Conforme"
            verificacoes.append({
                "item": "Política de 2FA (TOTP) habilitada na empresa",
                "ok": mfa_pol,
                "detalhe": "Política 2FA ativa." if mfa_pol else "Recomendação/exigência de 2FA desmarcada nas configurações.",
            })
            verificacoes.append({
                "item": "Colaboradores ativos com 2FA configurado",
                "ok": total_u > 0 and len(sem_mfa) == 0,
                "detalhe": f"{total_u - len(sem_mfa)}/{total_u} usuários com 2FA ativo." if total_u else "Verifique em Gestão de Equipe.",
            })
            if sem_mfa or not mfa_pol:
                falhas_detectadas.append({
                    "titulo": f"[Simulação Credenciais] {len(sem_mfa)} conta(s) sem segundo fator 2FA ativo",
                    "severidade": "Alta",
                    "categoria": "Identidade & Acesso (IAM)",
                    "recomendacao": "Exigir 2FA TOTP para toda a equipe e revisar senhas provisórias.",
                })

        elif cenario_id == "iot_cftv_botnet":
            inseguros = [
                a for a in ativos_aut
                if not (a.get("checks") or {}).get("senha_padrao_alterada")
                or not (a.get("checks") or {}).get("rede_segmentada")
            ]
            verificacoes.append({
                "item": "Inventário de Câmeras, NVR, Alarmes e IoT auditado",
                "ok": len(ativos_aut) > 0 and len(inseguros) == 0,
                "detalhe": (
                    f"{len(ativos_aut)} dispositivos auditados sem senha padrão/VLAN exposta."
                    if ativos_aut and not inseguros
                    else (f"{len(inseguros)} dispositivo(s) com senha padrão ou sem VLAN isolada!" if ativos_aut else "Nenhum dispositivo CFTV/IoT cadastrado ainda.")
                ),
            })
            if inseguros:
                falhas_detectadas.append({
                    "titulo": f"[Simulação IoT/CFTV] {len(inseguros)} equipamento(s) com risco de invasão RTSP/UPnP",
                    "severidade": "Alta",
                    "categoria": "Automação & CFTV",
                    "recomendacao": "Trocar senhas de fábrica, desativar UPnP e isolar CFTV/IoT em VLAN dedicada.",
                })

        elif cenario_id == "owasp_web_headers":
            tls_ok = checklist.get("chk_tls_headers") == "Conforme"
            ach_web = [a for a in abertos if "owasp" in str(a.get("categoria") or "").lower() or "tls" in str(a.get("categoria") or "").lower()]
            verificacoes.append({
                "item": "Cabeçalhos HTTP defensivos (HSTS, CSP, X-Frame-Options) e TLS 1.3",
                "ok": tls_ok and len(ach_web) == 0,
                "detalhe": "Sem falhas TLS/Headers pendentes." if (tls_ok and not ach_web) else f"{len(ach_web)} achado(s) web aberto(s) / controle OWASP pendente.",
            })
            if not tls_ok or ach_web:
                falhas_detectadas.append({
                    "titulo": "[Simulação Web OWASP] Revisar headers HSTS/CSP e renovação TLS nos portais",
                    "severidade": "Média",
                    "categoria": "Headers Web / OWASP",
                    "recomendacao": "Executar Byte-Scanner no modo Web/TLS nos domínios oficiais e ativar HSTS/CSP.",
                })

        else:
            vlan_ok = checklist.get("chk_vlan_iot") == "Conforme"
            logs_ok = checklist.get("chk_logs_cgnat") == "Conforme"
            verificacoes.append({
                "item": "Segmentação de rede (VLANs) e logs sincronizados (NTP/Syslog)",
                "ok": vlan_ok and logs_ok,
                "detalhe": "Controles de segmentação e logs em conformidade." if (vlan_ok and logs_ok) else "Controles de VLAN ou Logs ainda pendentes no CIS/NIST.",
            })
            if not (vlan_ok and logs_ok):
                falhas_detectadas.append({
                    "titulo": f"[Simulação {cenario['nome']}] Reforçar proteção de rede/domínio",
                    "severidade": "Média",
                    "categoria": cenario["categoria"],
                    "recomendacao": cenario["passo_a_passo_mitigacao"][0],
                })

        criados = 0
        if registrar_plano and falhas_detectadas:
            for f in falhas_detectadas:
                self.criar_achado(
                    tid,
                    {
                        "titulo": f["titulo"],
                        "severidade": f["severidade"],
                        "categoria": f["categoria"],
                        "alvo": "Ambiente da Empresa",
                        "status": "Aberto",
                        "recomendacao": f["recomendacao"],
                    },
                    actor=actor,
                )
                criados += 1

        total_v = max(1, len(verificacoes))
        ok_v = sum(1 for v in verificacoes if v["ok"])
        nivel_defesa = round((ok_v / total_v) * 100)
        return {
            "cenario": cenario,
            "nivel_defesa_percent": nivel_defesa,
            "verificacoes": verificacoes,
            "falhas_detectadas": falhas_detectadas,
            "achados_gerados": criados,
        }

    # ==================================================================
    # AUTOMAÇÕES DEFENSIVAS DE REDE & CAÇA AUTOMÁTICA POR FALHAS
    # ==================================================================

    def listar_automacoes_defesa(self, tenant_id: str) -> list[dict]:
        rows = self.store.list_items("cyber_automacoes", tenant_id)
        return sorted(rows, key=lambda r: str(r.get("criado_em") or ""), reverse=True)

    def criar_automacao_defesa(
        self,
        tenant: dict,
        payload: dict,
        actor: str = "",
        user_uid: str = "",
        user_email: str = "",
        is_admin: bool = False,
    ) -> dict:
        verificar_operador_autorizado_govkey(
            tenant, user_uid=user_uid, user_email=user_email, is_admin=is_admin
        )
        nome = SecurityValidator.sanitizar_texto(payload.get("nome"))
        if not nome:
            raise ValueError("Informe o nome da tarefa automatizada de defesa.")
        tipo = SecurityValidator.sanitizar_texto(payload.get("tipo")).lower()
        if tipo not in AUTOMACAO_DEFESA_TIPOS:
            raise ValueError("Tipo de automação defensiva inválido.")
        freq = SecurityValidator.sanitizar_texto(payload.get("frequencia")) or "Diária"
        if freq not in {"A cada 6h", "Diária", "Semanal", "Mensal", "Sob Demanda"}:
            freq = "Diária"
        alvo = SecurityValidator.sanitizar_texto(payload.get("alvo"))[:120] or "Ambiente Interno"

        # Se a tarefa envolver rede/domínio, valida formato com segurança antecipadamente
        escopo = list((tenant.get("cyber_governanca") or {}).get("escopo_autorizado") or [])
        if tipo == "monitor_ssl_headers":
            _, host_limpo = NetworkAuditEngine.validar_alvo_autorizado(alvo, "web_tls", escopo)
            alvo = host_limpo
        elif tipo == "varredura_portas_criticas" and alvo != "Ambiente Interno":
            alvo, _ = NetworkAuditEngine.validar_alvo_autorizado(alvo, "rede_privada", escopo)

        agora = _now_iso()
        item = {
            "id": f"caut_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant["id"],
            "nome": nome[:140],
            "tipo": tipo,
            "tipo_label": AUTOMACAO_DEFESA_TIPOS[tipo],
            "alvo": alvo,
            "frequencia": freq,
            "auto_registrar_achados": bool(payload.get("auto_registrar_achados", True)),
            "ativo": bool(payload.get("ativo", True)),
            "ultima_execucao": "",
            "ultimo_status": "Aguardando 1ª execução",
            "ultimo_resumo": "",
            "ultimo_score": 100,
            "criado_por": SecurityValidator.sanitizar_texto(actor) or "Equipe",
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self.store.upsert_item("cyber_automacoes", item, tenant["id"])

    def executar_automacao_defesa(
        self,
        tenant: dict,
        item_id: str,
        actor: str = "",
        user_uid: str = "",
        user_email: str = "",
        is_admin: bool = False,
        users_list: list[dict] | None = None,
    ) -> dict[str, Any]:
        verificar_operador_autorizado_govkey(
            tenant, user_uid=user_uid, user_email=user_email, is_admin=is_admin
        )
        tid = tenant["id"]
        tarefa = self.store.get_item("cyber_automacoes", item_id, tid)
        if not tarefa:
            raise LookupError("Automação de segurança não encontrada.")

        tipo = tarefa.get("tipo")
        alvo = tarefa.get("alvo") or "Ambiente Interno"
        auto_achados = bool(tarefa.get("auto_registrar_achados", True))
        gov = tenant.get("cyber_governanca") or {}

        achados_novos: list[dict[str, str]] = []
        score = 100
        resumo_exec = ""

        if tipo in {"monitor_ssl_headers", "varredura_portas_criticas"} and alvo != "Ambiente Interno":
            if not gov.get("permitir_auditoria_rede"):
                raise PermissionError(
                    "Para rodar varreduras ativas de rede/SSL, o gestor precisa ativar a chave geral no Gov-Key."
                )
            modo = "web_tls" if tipo == "monitor_ssl_headers" else "rede_privada"
            exec_aud = self.executar_auditoria_rede(
                tenant=tenant,
                alvo=alvo,
                tipo_auditoria=modo,
                registrar_achados=auto_achados,
                actor=actor,
                user_uid=user_uid,
                user_email=user_email,
                is_admin=is_admin,
            )
            score = exec_aud["auditoria"]["score"]
            qtd = exec_aud["achados_criados"]
            resumo_exec = f"Auditoria automática concluída em {alvo}: Score {score}/100 ({qtd} falha(s) registrada(s))."
            atualizada = self.store.update_item(
                "cyber_automacoes",
                item_id,
                {
                    "ultima_execucao": _now_iso(),
                    "ultimo_status": "Alerta" if qtd > 0 else "Protegido",
                    "ultimo_resumo": resumo_exec,
                    "ultimo_score": score,
                    "atualizado_em": _now_iso(),
                },
                tid,
            )
            return {"automacao": atualizada, "achados_criados": qtd, "resumo": resumo_exec}

        if tipo == "auditoria_contas_2fa_senhas":
            usuarios = users_list or []
            ativos_u = [u for u in usuarios if u.get("ativo", True)]
            sem_2fa = [u for u in ativos_u if not u.get("mfa_enabled")]
            senha_prov = [u for u in ativos_u if u.get("must_change_password")]
            if sem_2fa:
                score -= min(45, len(sem_2fa) * 15)
                achados_novos.append({
                    "titulo": f"[Auto-Shield] {len(sem_2fa)} colaborador(es) ativo(s) sem 2FA habilitado",
                    "severidade": "Alta",
                    "categoria": "Automação IAM / 2FA",
                    "alvo": "Contas da Equipe",
                    "recomendacao": "Solicitar ativação do 2FA TOTP no menu Minha Segurança de cada colaborador.",
                })
            if senha_prov:
                score -= min(30, len(senha_prov) * 10)
                achados_novos.append({
                    "titulo": f"[Auto-Shield] {len(senha_prov)} conta(s) ainda com senha provisória de 1º acesso",
                    "severidade": "Média",
                    "categoria": "Automação IAM / Senhas",
                    "alvo": "Contas da Equipe",
                    "recomendacao": "Confirmar conclusão do primeiro login ou revogar contas inativas.",
                })
            resumo_exec = (
                f"Contas verificadas: {len(ativos_u)} ativas • {len(sem_2fa)} sem 2FA • {len(senha_prov)} com senha provisória."
            )

        elif tipo == "auditoria_iot_cftv_hardening":
            ativos = self.listar_automacao(tid)
            falhos = [
                a for a in ativos
                if a.get("status_auditoria") in {"Crítico", "Alerta"}
                or not (a.get("checks") or {}).get("senha_padrao_alterada")
                or not (a.get("checks") or {}).get("rede_segmentada")
            ]
            if not ativos:
                score = 85
                resumo_exec = "Nenhum dispositivo CFTV/IoT cadastrado ainda para auditoria automática."
            else:
                score = max(15, 100 - len(falhos) * 18)
                if falhos:
                    nomes = ", ".join(str(x.get("nome") or "Ativo") for x in falhos[:4])
                    achados_novos.append({
                        "titulo": f"[Auto-Shield] {len(falhos)} ativo(s) de CFTV/IoT com falha de hardening ({nomes})",
                        "severidade": "Alta",
                        "categoria": "Automação CFTV & IoT",
                        "alvo": "Parque de Automação",
                        "recomendacao": "Alterar senhas padrão, atualizar firmware e isolar em VLAN de CFTV/IoT.",
                    })
                resumo_exec = f"{len(ativos)} ativo(s) CFTV/IoT inspecionados: {len(falhos)} com falha de segurança."

        else:
            # conformidade_backup_cis ou varredura interna sem CIDR externo
            chk = self.obter_checklist_cyber(tid)
            pendentes = [c for c in chk if c.get("status") in {"Pendente", "Parcial"}]
            score = max(10, round(((len(chk) - len(pendentes)) / max(1, len(chk))) * 100))
            if pendentes:
                achados_novos.append({
                    "titulo": f"[Auto-Shield] {len(pendentes)} controle(s) CIS/NIST/LGPD pendentes de conformidade",
                    "severidade": "Média",
                    "categoria": "Automação de Conformidade",
                    "alvo": "Controles Internos",
                    "recomendacao": f"Priorizar regularização de: {pendentes[0].get('controle', 'controles pendentes')}.",
                })
            resumo_exec = f"Checklist CIS/NIST auditado: {len(chk) - len(pendentes)}/{len(chk)} controles Conformes."

        criados = 0
        if auto_achados:
            for ach in achados_novos:
                self.criar_achado(tid, {**ach, "status": "Aberto"}, actor=actor)
                criados += 1

        atualizada = self.store.update_item(
            "cyber_automacoes",
            item_id,
            {
                "ultima_execucao": _now_iso(),
                "ultimo_status": "Alerta" if achados_novos else "Protegido",
                "ultimo_resumo": resumo_exec,
                "ultimo_score": max(0, min(100, score)),
                "atualizado_em": _now_iso(),
            },
            tid,
        )
        return {"automacao": atualizada, "achados_criados": criados, "resumo": resumo_exec}

    def excluir_automacao_defesa(self, tenant_id: str, item_id: str) -> bool:
        return self.store.delete_item("cyber_automacoes", item_id, tenant_id)

    @staticmethod
    def gerar_script_protecao_rede(tipo_script: str) -> tuple[bytes, str, str]:
        t = (tipo_script or "firewall_windows_ps1").strip().lower()
        if t == "firewall_windows_ps1":
            conteudo = (
                "# SysFix Auto-Shield - Script Defensivo de Blindagem Windows (Blue Team)\n"
                "# Execute como Administrador em estacoes/servidores Windows para reduzir superficie de ataque.\n"
                "Write-Host '1. Desativando protocolo legado SMBv1 (Anti-WannaCry/Ransomware)...'\n"
                "Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force -ErrorAction SilentlyContinue\n"
                "Write-Host '2. Bloqueando portas inseguras de entrada (Telnet 23, TFTP 69)...'\n"
                "New-NetFirewallRule -DisplayName 'SysFix-Block-Telnet-In' -Direction Inbound -Protocol TCP -LocalPort 23 -Action Block -ErrorAction SilentlyContinue\n"
                "Write-Host '3. Listando portas TCP em escuta para auditoria local...'\n"
                "Get-NetTCPConnection -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess | Sort-Object LocalPort | Format-Table -AutoSize\n"
            ).encode("utf-8")
            return conteudo, "text/plain; charset=utf-8", "sysfix_blindagem_windows.ps1"

        if t == "hardening_linux_ufw_sh":
            conteudo = (
                "#!/usr/bin/env bash\n"
                "# SysFix Auto-Shield - Hardening Defensivo Linux (UFW + Auditoria de Portas)\n"
                "set -e\n"
                "echo '[SysFix] Aplicando politica padrao Deny-Incoming no UFW...'\n"
                "sudo ufw default deny incoming\n"
                "sudo ufw default allow outgoing\n"
                "echo '[SysFix] Aplicando limitacao anti-bruteforce no SSH (porta 22/tcp)...'\n"
                "sudo ufw limit 22/tcp\n"
                "echo '[SysFix] Servicos expostos atualmente neste servidor:'\n"
                "ss -tulnp\n"
            ).encode("utf-8")
            return conteudo, "text/x-shellscript; charset=utf-8", "sysfix_hardening_linux.sh"

        if t == "tarefa_agendada_monitor_ps1":
            conteudo = (
                "# SysFix Auto-Shield - Monitor Diário de Portas & Certificado HTTPS\n"
                "param([string]$Dominio = 'suaempresa.com.br', [string]$Subrede = '192.168.1')\n"
                "$relatorio = @{ dominio = $Dominio; verificado_em = (Get-Date -Format o); alertas = @() }\n"
                "try {\n"
                "  $tcp = New-Object System.Net.Sockets.TcpClient($Dominio, 443)\n"
                "  $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false)\n"
                "  $ssl.AuthenticateAsClient($Dominio)\n"
                "  $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)\n"
                "  $dias = ($cert.NotAfter - (Get-Date)).Days\n"
                "  $relatorio['ssl_dias_restantes'] = $dias\n"
                "  if ($dias -le 15) { $relatorio.alertas += \"Certificado SSL expira em $dias dias\" }\n"
                "  $tcp.Close()\n"
                "} catch { $relatorio.alertas += \"Falha ao validar HTTPS em $Dominio\" }\n"
                "$relatorio | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 'monitor_diario_sysfix.json'\n"
                "Write-Host 'Monitoramento concluido: monitor_diario_sysfix.json'\n"
            ).encode("utf-8")
            return conteudo, "text/plain; charset=utf-8", "sysfix_monitor_diario.ps1"

        raise ValueError("Tipo de script defensivo inválido.")


AUTOMACAO_DEFESA_TIPOS: dict[str, str] = {
    "auditoria_contas_2fa_senhas": "🔐 Caça-Falhas de Identidade (2FA Ausente & Senhas Provisórias)",
    "auditoria_iot_cftv_hardening": "📷 Busca de Falhas em Câmeras IP, DVRs, Alarmes & IoT",
    "conformidade_backup_cis": "🛡️ Verificação Automática de Backup 3-2-1-1-0 & CIS/LGPD",
    "monitor_ssl_headers": "🌐 Vigia de Certificado HTTPS & Headers OWASP de Domínio",
    "varredura_portas_criticas": "📡 Busca de Portas Críticas Expostas na Rede Interna (RFC1918)",
}

CYBER_DIDACTIC_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "ransomware_smb_rdp",
        "nome": "Ransomware & Movimentação Lateral (SMB / RDP)",
        "categoria": "Continuidade & Rede",
        "risco": "Crítica",
        "icone": "🦠",
        "anatomia_ataque": (
            "1. Entrada Inicial: O invasor obtém acesso via e-mail de phishing ou porta RDP (3389) exposta na internet.\n"
            "2. Movimentação Lateral: Usa protocolo SMB (porta 445) e credenciais administrativas repetidas para alcançar servidores e NAS.\n"
            "3. Impacto: Criptografa pastas compartilhadas e tenta apagar backups conectados na mesma rede."
        ),
        "como_buscar_falha": (
            "• Rode o Byte-Scanner na sub-rede local (ex.: 192.168.1.0/24) procurando portas 445 (SMB), 3389 (RDP) e 23 (Telnet).\n"
            "• Verifique se o servidor de backup está na mesma VLAN ou domínio Windows das estações comuns."
        ),
        "passo_a_passo_mitigacao": [
            "Jamais exponha RDP (3389) ou SMB (445) na internet; exija VPN WireGuard/IPsec + 2FA.",
            "Desative SMBv1 em todas as máquinas e separe senhas de administrador local (LAPS).",
            "Mantenha Backup 3-2-1-1-0 com pelo menos 1 cópia imutável (Object Lock) ou desconectada.",
        ],
        "comando_diagnostico": "Get-SmbServerConfiguration | Select EnableSMB1Protocol, EnableSMB2Protocol",
    },
    {
        "id": "bruteforce_credenciais",
        "nome": "Força Bruta, Credential Stuffing & Sequestro de Sessão",
        "categoria": "Identidade & Acesso (2FA)",
        "risco": "Alta",
        "icone": "🔑",
        "anatomia_ataque": (
            "1. Coleta: Vazamentos externos expõem combinações de e-mail e senha reutilizadas por colaboradores.\n"
            "2. Automação Ofensiva: Bots testam milhares de senhas por minuto (Credential Stuffing) em painéis sem rate-limit ou sem 2FA.\n"
            "3. Impacto: Acesso indevido a O.S., dados de clientes (LGPD) e configurações financeiras."
        ),
        "como_buscar_falha": (
            "• Execute a automação 'Caça-Falhas de Identidade' para listar contas sem 2FA TOTP ou com senha provisória.\n"
            "• Audite em 'Minha Segurança' os últimos IPs de acesso e sessões antigas abertas."
        ),
        "passo_a_passo_mitigacao": [
            "Habilite 2FA TOTP (Google/Microsoft Authenticator) para 100% dos usuários.",
            "Mantenha troca obrigatória de senha no 1º login ao cadastrar novos técnicos.",
            "Aplique princípio do menor privilégio nas permissões por setor.",
        ],
        "comando_diagnostico": "SysFix > Minha Segurança > Ativar 2FA TOTP + Revogar Sessões Antigas",
    },
    {
        "id": "owasp_web_headers",
        "nome": "Ataques Web: XSS, Clickjacking & Interceptação SSL/TLS",
        "categoria": "Segurança Web / OWASP",
        "risco": "Alta",
        "icone": "🌐",
        "anatomia_ataque": (
            "1. Downgrade/SSL Expirado: Sem HSTS ou com TLS vencido, conexões podem sofrer interceptação (Man-in-the-Middle) em redes Wi-Fi públicas.\n"
            "2. Clickjacking & XSS: Sem X-Frame-Options e Content-Security-Policy (CSP), páginas legítimas podem ser embutidas em iframes falsos ou executar scripts injetados."
        ),
        "como_buscar_falha": (
            "• No Byte-Scanner, selecione o modo 'Web / TLS / Certificados & Headers OWASP' e informe o domínio da empresa.\n"
            "• Confira se os 5 cabeçalhos (HSTS, CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy) retornam presentes."
        ),
        "passo_a_passo_mitigacao": [
            "Ative HTTPS obrigatório com TLS 1.2/1.3 e renovação automática Let's Encrypt / ACME.",
            "Adicione Strict-Transport-Security, Content-Security-Policy e X-Frame-Options: DENY no Nginx/Cloudflare.",
            "Crie uma automação 'Vigia de Certificado HTTPS' para alertar 15 dias antes do vencimento.",
        ],
        "comando_diagnostico": "curl -I https://seudominio.com.br",
    },
    {
        "id": "iot_cftv_botnet",
        "nome": "Invasão de Câmeras IP, DVRs, Alarmes & Botnets IoT",
        "categoria": "Automação, CFTV & IoT",
        "risco": "Crítica",
        "icone": "📷",
        "anatomia_ataque": (
            "1. Descoberta: Bots varrem a internet buscando portas 554 (RTSP), 8000/37777 (DVR) e 23 (Telnet) abertas automaticamente via UPnP.\n"
            "2. Exploração: Testam credenciais de fábrica (admin/admin, admin/12345) ou falhas de firmware antigo.\n"
            "3. Impacto: Vazamento de imagens internas e uso do DVR como pivô para invadir a rede da empresa."
        ),
        "como_buscar_falha": (
            "• Revise o setor Automação & IoT e rode a automação 'Busca de Falhas em Câmeras IP & IoT'.\n"
            "• Verifique no roteador de borda se o UPnP está ativo abrindo portas sem autorização."
        ),
        "passo_a_passo_mitigacao": [
            "Isole todas as câmeras, NVRs e sensores IoT em uma VLAN exclusiva sem acesso à VLAN do Financeiro/TI.",
            "Desative UPnP no roteador/firewall e substitua redirecionamento de portas por VPN.",
            "Troque 100% das senhas padrão e atualize o firmware dos gravadores.",
        ],
        "comando_diagnostico": "nmap -sT -p 23,80,554,1883,8000,37777 192.168.10.0/24",
    },
    {
        "id": "mitm_arp_vlan",
        "nome": "Interceptação Local (ARP Spoofing & Rede Plana sem VLAN)",
        "categoria": "Infraestrutura & Switches",
        "risco": "Média",
        "icone": "🔌",
        "anatomia_ataque": (
            "1. Rede Plana: Quando Wi-Fi de visitantes, bancada de testes de clientes e servidores usam a mesma faixa IP (ex.: tudo em 192.168.0.x).\n"
            "2. Envenenamento ARP / DHCP Rogue: Um notebook infectado na bancada anuncia-se como gateway e captura tráfego não criptografado."
        ),
        "como_buscar_falha": (
            "• Confirme se equipamentos de clientes em manutenção na Bancada TI estão em uma rede/VLAN separada dos computadores da loja/administração."
        ),
        "passo_a_passo_mitigacao": [
            "Crie uma VLAN exclusiva 'Bancada / Quarentena' com Client Isolation para testar PCs de clientes com vírus.",
            "Ative DHCP Snooping e Dynamic ARP Inspection (DAI) nos switches gerenciáveis.",
            "Separe Wi-Fi Visitantes do Wi-Fi Corporativo.",
        ],
        "comando_diagnostico": "arp -a  # Verifique se dois IPs diferentes compartilham o mesmo MAC do Gateway",
    },
    {
        "id": "dns_email_spoofing",
        "nome": "Falsificação de E-mail Corporativo (Phishing / BEC sem DMARC)",
        "categoria": "Domínio & E-mail",
        "risco": "Alta",
        "icone": "✉️",
        "anatomia_ataque": (
            "1. Spoofing: Criminosos enviam boletos ou ordens falsas fingindo ser `@suaempresa.com.br`.\n"
            "2. Falha: Se o DNS do domínio não possui registros SPF, DKIM e DMARC com política `p=quarantine` ou `p=reject`, provedores entregam o e-mail falso na caixa de entrada do cliente."
        ),
        "como_buscar_falha": (
            "• Consulte os registros TXT do seu domínio (`_dmarc.suaempresa.com.br` e registro `v=spf1`)."
        ),
        "passo_a_passo_mitigacao": [
            "Configure o registro TXT SPF listando apenas os servidores oficiais (Google Workspace / Microsoft 365).",
            "Ative assinatura criptográfica DKIM no painel do provedor de e-mail.",
            "Publique registro `_dmarc` iniciando em `p=quarantine` e evoluindo para `p=reject`.",
        ],
        "comando_diagnostico": "nslookup -type=TXT _dmarc.suaempresa.com.br",
    },
]


_NEWS_CACHE_LOCK = threading.Lock()
_NEWS_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}

_FALLBACK_CYBER_NEWS: list[dict[str, Any]] = [
    {
        "titulo": "CISA KEV: Alerta global para atualização imediata de VPNs, Firewalls de borda e hipervisores expostos",
        "fonte": "CISA KEV (Catálogo Oficial)",
        "categoria": "Vulnerabilidade Explorada",
        "data": "Recente",
        "resumo": "Agência de cibersegurança destaca exploração ativa de falhas de autenticação e injeção em appliances de borda sem patch. Recomendado desativar gerência WAN e aplicar correções.",
        "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
        "destaque": "Crítica",
    },
    {
        "titulo": "CERT.br: Recomendações contra campanhas de phishing financeiro, QR Codes maliciosos e sequestro de contas sem MFA",
        "fonte": "CERT.br / NIC.br",
        "categoria": "Alerta Brasil",
        "data": "Recente",
        "resumo": "Crescimento de golpes direcionados a empresas brasileiras explorando engenharia social e boletos adulterados. Uso de 2FA baseado em aplicativo (TOTP) e validação DMARC bloqueiam a maioria dos vetores.",
        "url": "https://www.cert.br/",
        "destaque": "Alta",
    },
    {
        "titulo": "Botnets IoT miram DVRs, câmeras IP com RTSP exposto e roteadores com UPnP/Telnet habilitado",
        "fonte": "Threat Intel / BleepingComputer",
        "categoria": "IoT & CFTV",
        "data": "Recente",
        "resumo": "Variantes recentes de botnets automatizam varreduras em portas 23, 554, 8000 e 8291 para recrutar equipamentos não segmentados em ataques DDoS.",
        "url": "https://www.bleepingcomputer.com/",
        "destaque": "Alta",
    },
    {
        "titulo": "Ransomware moderno prioriza roubo de credenciais de backup e desativação de agentes EDR via RDP/SMB",
        "fonte": "The Hacker News",
        "categoria": "Ransomware & Defesa",
        "data": "Recente",
        "resumo": "Grupos de extorsão dupla focam em servidores de backup acessíveis pela mesma rede local. Isolamento de VLAN e imutabilidade (3-2-1-1-0) seguem como defesa essencial.",
        "url": "https://thehackernews.com/",
        "destaque": "Crítica",
    },
]


def _limpar_html_curto(texto: str, limite: int = 260) -> str:
    sem_tags = re.sub(r"<[^>]+>", " ", html.unescape(str(texto or "")))
    compacto = re.sub(r"\s+", " ", sem_tags).strip()
    if len(compacto) > limite:
        return compacto[: limite - 1].rstrip() + "…"
    return compacto


def _url_externa_segura(url: str) -> str:
    u = str(url or "").strip()
    if u.startswith(("https://", "http://")):
        return u[:400]
    return ""


class CyberNewsFeedService:
    """Coleta notícias reais de fontes públicas confiáveis de cibersegurança com cache e fallback offline."""

    CACHE_TTL_SECONDS = 900  # 15 minutos

    @classmethod
    def _fetch_cisa_kev(cls) -> list[dict[str, Any]]:
        req = urllib.request.Request(
            "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
            headers={"User-Agent": "SysFix-CyberRadar/2.9"},
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        vulns = list(raw.get("vulnerabilities") or [])
        vulns.sort(key=lambda v: str(v.get("dateAdded") or ""), reverse=True)
        itens: list[dict[str, Any]] = []
        for v in vulns[:5]:
            cve = SecurityValidator.sanitizar_texto(v.get("cveID"))
            vendor = SecurityValidator.sanitizar_texto(v.get("vendorProject"))
            prod = SecurityValidator.sanitizar_texto(v.get("product"))
            nome = SecurityValidator.sanitizar_texto(v.get("vulnerabilityName"))
            itens.append({
                "titulo": f"{cve} — {vendor} {prod}: {nome}"[:180],
                "fonte": "CISA KEV (EUA)",
                "categoria": "CVE Explorada Ativamente",
                "data": str(v.get("dateAdded") or "")[:10],
                "resumo": _limpar_html_curto(v.get("shortDescription") or v.get("requiredAction") or ""),
                "url": f"https://nvd.nist.gov/vuln/detail/{cve}" if cve.startswith("CVE-") else "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
                "destaque": "Crítica",
            })
        return itens

    @classmethod
    def _fetch_rss(cls, url: str, fonte_nome: str, categoria: str, max_items: int = 5) -> list[dict[str, Any]]:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "SysFix-CyberRadar/2.9", "Accept": "application/rss+xml, application/xml, text/xml"},
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            xml_bytes = resp.read(350_000)
        root = ET.fromstring(xml_bytes)
        itens: list[dict[str, Any]] = []
        nodes = root.findall(".//item")
        if not nodes:
            nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for node in nodes[:max_items]:
            titulo = (
                node.findtext("title")
                or node.findtext("{http://www.w3.org/2005/Atom}title")
                or ""
            )
            link = node.findtext("link") or ""
            if not link:
                atom_link = node.find("{http://www.w3.org/2005/Atom}link")
                if atom_link is not None:
                    link = atom_link.attrib.get("href", "")
            desc = (
                node.findtext("description")
                or node.findtext("{http://www.w3.org/2005/Atom}summary")
                or ""
            )
            pub = (
                node.findtext("pubDate")
                or node.findtext("{http://purl.org/dc/elements/1.1/}date")
                or node.findtext("{http://www.w3.org/2005/Atom}updated")
                or ""
            )
            titulo_limpo = _limpar_html_curto(titulo, 170)
            if not titulo_limpo:
                continue
            itens.append({
                "titulo": titulo_limpo,
                "fonte": fonte_nome,
                "categoria": categoria,
                "data": _limpar_html_curto(pub, 25),
                "resumo": _limpar_html_curto(desc, 250),
                "url": _url_externa_segura(link),
                "destaque": "Notícia Real",
            })
        return itens

    @classmethod
    def obter_noticias(cls, forcar: bool = False) -> dict[str, Any]:
        agora_ts = time.time()
        with _NEWS_CACHE_LOCK:
            if (
                not forcar
                and _NEWS_CACHE["data"] is not None
                and (agora_ts - _NEWS_CACHE["ts"]) < cls.CACHE_TTL_SECONDS
            ):
                return dict(_NEWS_CACHE["data"])

        coletadas: list[dict[str, Any]] = []
        fontes_ativas: list[str] = []
        tarefas = [
            ("CISA KEV", cls._fetch_cisa_kev),
            (
                "The Hacker News",
                lambda: cls._fetch_rss(
                    "https://feeds.feedburner.com/TheHackersNews",
                    "The Hacker News",
                    "Mundo Cyber",
                    5,
                ),
            ),
            (
                "BleepingComputer",
                lambda: cls._fetch_rss(
                    "https://www.bleepingcomputer.com/feed/",
                    "BleepingComputer",
                    "Ameaças & Zero-Day",
                    4,
                ),
            ),
            (
                "CERT.br",
                lambda: cls._fetch_rss(
                    "https://www.cert.br/rss/certbr-rss.xml",
                    "CERT.br",
                    "Avisos Brasil",
                    4,
                ),
            ),
        ]

        with ThreadPoolExecutor(max_workers=4) as pool:
            fut_map = {pool.submit(fn): nome for nome, fn in tarefas}
            for fut in as_completed(fut_map):
                nome_f = fut_map[fut]
                try:
                    parcial = fut.result()
                    if parcial:
                        coletadas.extend(parcial)
                        fontes_ativas.append(nome_f)
                except Exception:
                    continue

        ao_vivo = len(coletadas) > 0
        if not coletadas:
            coletadas = [dict(x) for x in _FALLBACK_CYBER_NEWS]
            fontes_ativas = ["Modo Contingência (Sem Internet no Servidor)"]

        resultado = {
            "ao_vivo": ao_vivo,
            "atualizado_em": _now_iso(),
            "fontes_ativas": sorted(fontes_ativas),
            "itens": coletadas[:18],
        }
        with _NEWS_CACHE_LOCK:
            _NEWS_CACHE["ts"] = agora_ts
            _NEWS_CACHE["data"] = resultado
        return dict(resultado)
