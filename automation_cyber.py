from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
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

# Portas avaliadas em auditorias defensivas internas (somente detecção de exposição de serviços)
_PORTAS_AUDITORIA_DEFENSIVA: dict[int, tuple[str, str, str]] = {
    22: ("SSH", "Baixa", "Manter autenticação por chave SSH e desabilitar login root por senha."),
    23: ("Telnet", "Crítica", "Protocolo em texto claro: desativar Telnet imediatamente e migrar para SSH."),
    80: ("HTTP", "Média", "Redirecionar interface administrativa para HTTPS/TLS com certificado válido."),
    443: ("HTTPS", "Informativa", "Verificar validade do certificado TLS, cifras fortes e headers de segurança."),
    445: ("SMB", "Alta", "Restringir SMB/CIFS à rede interna isolada e bloquear SMBv1 para prevenir ransomware."),
    554: ("RTSP (CFTV)", "Alta", "Segmentar câmeras em VLAN isolada, exigir autenticação forte e usar túnel/VPN."),
    1883: ("MQTT (IoT)", "Alta", "Ativar autenticação e TLS (MQTTS porta 8883) para dispositivos IoT."),
    3389: ("RDP", "Alta", "Nunca expor RDP diretamente; exigir VPN + MFA + NLA."),
    8080: ("HTTP Alt / Painel", "Média", "Validar se painel web possui autenticação forte e acesso restrito por firewall."),
    8291: ("Winbox (MikroTik)", "Alta", "Restringir Winbox apenas à VLAN/IP de gerência (Management) e desativar na WAN."),
    8443: ("HTTPS Alt", "Informativa", "Conferir certificado TLS e controle de acesso ao painel administrativo."),
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


def _host_matches_domain_scope(host: str, escopo_norm: list[str]) -> bool:
    host_l = host.lower().strip(".")
    for entry in escopo_norm:
        item = entry.lower().strip().lstrip("*.")
        if not item or "/" in item:
            continue
        try:
            ipaddress.ip_address(item)
            if item == host_l:
                return True
            continue
        except ValueError:
            pass
        if host_l == item or host_l.endswith(f".{item}"):
            return True
    return False


class NetworkAuditEngine:
    """Motor defensivo e autorizado para auditoria de postura de rede e Web/TLS."""

    @staticmethod
    def validar_alvo_autorizado(
        alvo: str, tipo_auditoria: str, escopo_autorizado: list[str]
    ) -> tuple[str, str]:
        raw = SecurityValidator.sanitizar_texto(alvo)
        if not raw:
            raise ValueError("Informe o alvo da auditoria (IP/CIDR privado ou domínio/URL autorizado).")
        if not escopo_autorizado:
            raise PermissionError(
                "Nenhum escopo de rede/domínio foi autorizado pelo gestor da empresa."
            )

        escopo_limpo = [SecurityValidator.sanitizar_texto(e) for e in escopo_autorizado if SecurityValidator.sanitizar_texto(e)]

        if tipo_auditoria == "web_tls":
            url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").strip().lower()
            if not host or host in {"localhost", "metadata.google.internal"}:
                raise ValueError("Host inválido ou proibido para auditoria externa.")
            try:
                ip_obj = ipaddress.ip_address(host)
                if _is_forbidden_ip(ip_obj) or ip_obj.is_private:
                    raise PermissionError("Auditorias Web/TLS públicas exigem domínio corporativo autorizado (não IPs locais/metadata).")
            except ValueError:
                pass
            if not _host_matches_domain_scope(host, escopo_limpo):
                raise PermissionError(
                    f"Domínio '{host}' fora do escopo autorizado pelo gestor."
                )
            return url, host

        # tipo_auditoria == "rede_privada"
        host_limpo = raw.strip().lower()
        if host_limpo in {"localhost", "metadata.google.internal"}:
            raise PermissionError("Alvo proibido pela política anti-SSRF.")

        try:
            net = ipaddress.ip_network(host_limpo, strict=False)
        except ValueError as exc:
            raise ValueError("Para auditoria de rede interna, informe um IP ou sub-rede CIDR privada válida (ex.: 192.168.1.0/24).") from exc

        if net.version != 4 or not net.is_private:
            raise PermissionError("Auditoria de rede aceita apenas faixas privadas IPv4 (RFC1918: 10.x, 172.16-31.x, 192.168.x).")
        if any(_is_forbidden_ip(addr) for addr in (net.network_address, net.broadcast_address)):
            raise PermissionError("Endereço loopback, link-local ou reservado não é permitido.")
        if net.prefixlen < 24:
            raise ValueError("Para segurança e agilidade, use no máximo blocos /24 (até 254 IPs por varredura).")

        autorizado = False
        for entry in escopo_limpo:
            try:
                allowed_net = ipaddress.ip_network(entry, strict=False)
                if allowed_net.version == 4 and net.subnet_of(allowed_net):
                    autorizado = True
                    break
            except ValueError:
                continue

        if not autorizado:
            raise PermissionError(
                f"Sub-rede '{net}' não está incluída nos CIDRs autorizados pelo gestor."
            )
        return str(net), str(net)

    @staticmethod
    def auditar_web_tls(url: str, host: str) -> dict[str, Any]:
        achados: list[dict[str, Any]] = []
        detalhes: dict[str, Any] = {"host": host, "url": url, "tls": {}, "headers": {}}
        score = 100

        # 1. Verificação passiva de certificado SSL/TLS
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=4.0) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert() or {}
                    versao_tls = ssock.version() or ""
                    detalhes["tls"]["versao"] = versao_tls
                    not_after = cert.get("notAfter")
                    if not_after:
                        exp_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                        dias_rest = (exp_dt - datetime.utcnow()).days
                        detalhes["tls"]["dias_para_expirar"] = dias_rest
                        if dias_rest < 0:
                            score -= 35
                            achados.append({
                                "titulo": f"Certificado TLS expirado em {host}",
                                "severidade": "Crítica",
                                "categoria": "TLS / Criptografia",
                                "recomendacao": "Renovar imediatamente o certificado SSL/TLS no servidor ou proxy reverso.",
                            })
                        elif dias_rest <= 15:
                            score -= 15
                            achados.append({
                                "titulo": f"Certificado TLS expira em {dias_rest} dia(s)",
                                "severidade": "Alta",
                                "categoria": "TLS / Criptografia",
                                "recomendacao": "Verificar automação ACME / Let's Encrypt antes do vencimento.",
                            })
        except Exception as exc:
            score -= 30
            detalhes["tls"]["erro"] = str(exc)[:180]
            achados.append({
                "titulo": f"Falha ao validar TLS/HTTPS em {host}",
                "severidade": "Alta",
                "categoria": "TLS / Criptografia",
                "recomendacao": "Confirmar porta 443 ativa e cadeia de certificação válida.",
            })

        # 2. Cabeçalhos HTTP defensivos (requisição única sem seguir redirects para fora do escopo)
        try:
            req = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "SysFix-SecurityAuditor/2.3"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                detalhes["http_status"] = getattr(resp, "status", 200)
                obrigatorios = [
                    ("strict-transport-security", "HSTS ausente (Strict-Transport-Security)", "Média", 10, "Ativar header HSTS para forçar conexões HTTPS."),
                    ("content-security-policy", "CSP ausente (Content-Security-Policy)", "Média", 10, "Definir Content-Security-Policy para mitigar XSS."),
                    ("x-content-type-options", "X-Content-Type-Options ausente", "Baixa", 6, "Definir X-Content-Type-Options: nosniff."),
                    ("x-frame-options", "X-Frame-Options ausente", "Baixa", 6, "Definir X-Frame-Options: DENY ou SAMEORIGIN contra clickjacking."),
                    ("referrer-policy", "Referrer-Policy ausente", "Informativa", 4, "Definir Referrer-Policy: strict-origin-when-cross-origin."),
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
                            "recomendacao": rec,
                        })
        except Exception as exc:
            detalhes["http_erro"] = str(exc)[:180]
            score -= 15
            achados.append({
                "titulo": f"Endpoint HTTP/HTTPS inacessível em {host}",
                "severidade": "Média",
                "categoria": "Disponibilidade / Web",
                "recomendacao": "Verificar se o site está online e acessível publicamente.",
            })

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
            for p in portas:
                try:
                    if cls._sondar_porta(ip_str, p):
                        servico, sev, _ = _PORTAS_AUDITORIA_DEFENSIVA[p]
                        abertas.append({"porta": p, "servico": servico, "severidade": sev})
                except OSError:
                    continue
            if not abertas:
                return None
            return {"ip": ip_str, "portas": abertas}

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
                    achados.append({
                        "titulo": f"Serviço {servico} (porta {porta}) exposto em {host_info['ip']}",
                        "severidade": sev,
                        "categoria": "Superfície de Ataque / Rede",
                        "recomendacao": rec,
                    })

        score = max(0, min(100, 100 - min(85, penalidade)))
        return {
            "score": score,
            "hosts_ativos": len(descobertos),
            "portas_expostas": total_portas,
            "detalhes": {"cidr": cidr, "hosts_verificados": len(hosts), "hosts": descobertos},
            "achados_detectados": achados,
        }

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

    def executar_auditoria_rede(
        self,
        tenant: dict,
        alvo: str,
        tipo_auditoria: str = "web_tls",
        registrar_achados: bool = True,
        actor: str = "",
    ) -> dict[str, Any]:
        gov = tenant.get("cyber_governanca") or {}
        if not gov.get("permitir_auditoria_rede"):
            raise PermissionError(
                "Auditoria de rede bloqueada: o gestor precisa habilitar e autorizar o escopo em Governança."
            )

        tipo_norm = SecurityValidator.sanitizar_texto(tipo_auditoria).lower() or "web_tls"
        if tipo_norm not in {"web_tls", "rede_privada"}:
            raise ValueError("Tipo de auditoria inválido. Use 'web_tls' ou 'rede_privada'.")

        escopo = list(gov.get("escopo_autorizado") or [])
        alvo_norm, _ = NetworkAuditEngine.validar_alvo_autorizado(alvo, tipo_norm, escopo)

        if tipo_norm == "web_tls":
            resultado = NetworkAuditEngine.auditar_web_tls(alvo_norm, urllib.parse.urlparse(alvo_norm).hostname or alvo_norm)
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
            "detalhes": {"hosts": hosts[:100]},
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
        return sorted(
            rows,
            key=lambda r: (
                1 if r.get("status") == "Resolvido" else 0,
                sev_order.get(r.get("severidade"), 99),
                str(r.get("criado_em") or ""),
            ),
        )

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
            "auditoria_id": SecurityValidator.sanitizar_texto(payload.get("auditoria_id"))[:40],
            "criado_por": SecurityValidator.sanitizar_texto(actor) or "Sistema",
            "criado_em": agora,
            "atualizado_em": agora,
        }
        return self.store.upsert_item("cyber_achados", item, tenant_id)

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
        if "recomendacao" in payload and payload["recomendacao"] is not None:
            updates["recomendacao"] = SecurityValidator.sanitizar_texto(payload["recomendacao"])[:600]
        return self.store.update_item("cyber_achados", item_id, updates, tenant_id)

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
            "governanca": tenant.get("cyber_governanca") or {
                "permitir_auditoria_rede": False,
                "escopo_autorizado": [],
                "observacao_termo": "",
                "autorizado_por": "",
                "autorizado_em": "",
            },
            "ultimas_auditorias": auditorias[:5],
        }
