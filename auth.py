import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auth_security import (
    append_login_audit,
    build_login_audit_entry,
    build_otpauth_uri,
    create_mfa_challenge_token,
    create_mfa_verified_token,
    generate_qr_data_uri,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    login_rate_limiter,
    verify_and_consume_recovery_code,
    verify_mfa_challenge_token,
    verify_mfa_verified_token,
    verify_totp,
)
from config import get_data_dir, get_secret_key, get_storage_backend, get_token_ttl_hours
from security import SecurityValidator
from templates_laudo import (
    CHECKLIST_PRESETS,
    DEFAULT_LAUDO_TEMPLATES,
    normalizar_checklist_padrao,
    normalizar_modelos_laudo,
    validar_cor_hex,
    validar_imagem_base64,
)

_AUTH_LOCK = threading.RLock()
_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_LOCATIONS = [
    "Bancada TI",
    "PlayLan",
    "Central Informática",
    "Aguardando Peça",
]
ROLES_PERMITIDAS = {"admin", "tecnico"}

DEFAULT_ACTIVE_SECTORS: dict[str, bool] = {
    "ti": True,
    "loja": True,
    "estoque": True,
    "automacao": True,
    "ciberseguranca": True,
}

DEFAULT_BANCADA_FIELDS: dict[str, Any] = {
    "padrao": {
        "categoria": {"label": "Categoria TI", "visivel": True, "obrigatorio": False},
        "tombamento": {"label": "Tombamento / Serial", "visivel": True, "obrigatorio": True},
        "localizacao": {"label": "Localização Inicial", "visivel": True, "obrigatorio": True},
        "prioridade": {"label": "Prioridade / SLA", "visivel": True, "obrigatorio": False},
        "problema": {"label": "Problema Relatado", "visivel": True, "obrigatorio": False},
        "os_coleta": {"label": "O.S Coleta", "visivel": True, "obrigatorio": True},
        "origem": {"label": "Origem / Cliente", "visivel": True, "obrigatorio": True},
        "laudado": {"label": "Laudado?", "visivel": True, "obrigatorio": False},
        "garantia": {"label": "Garantia do Serviço (dias)", "visivel": True, "obrigatorio": False},
        "resolucao": {"label": "Serviço / Resolução", "visivel": True, "obrigatorio": False},
    },
    "personalizados": [],
}


def _normalizar_campos_bancada(raw: Any) -> dict[str, Any]:
    padrao_out: dict[str, dict[str, Any]] = {}
    raw_padrao = raw.get("padrao") if isinstance(raw, dict) else {}
    if not isinstance(raw_padrao, dict):
        raw_padrao = {}

    for key, cfg_def in DEFAULT_BANCADA_FIELDS["padrao"].items():
        item = raw_padrao.get(key)
        if isinstance(item, dict):
            lbl = SecurityValidator.sanitizar_texto(item.get("label"))[:50] or cfg_def["label"]
            vis = bool(item.get("visivel", cfg_def["visivel"]))
            obrig = bool(item.get("obrigatorio", cfg_def["obrigatorio"])) if vis else False
            padrao_out[key] = {"label": lbl, "visivel": vis, "obrigatorio": obrig}
        else:
            padrao_out[key] = dict(cfg_def)

    custom_out: list[dict[str, Any]] = []
    raw_custom = raw.get("personalizados") if isinstance(raw, dict) else []
    if isinstance(raw_custom, list):
        vistos: set[str] = set()
        for entry in raw_custom[:25]:
            if not isinstance(entry, dict):
                continue
            label = SecurityValidator.sanitizar_texto(entry.get("label"))[:60]
            if not label:
                continue
            cid_raw = SecurityValidator.sanitizar_texto(entry.get("id"))
            cid = re.sub(r"[^a-zA-Z0-9_]", "_", cid_raw).strip("_").lower()[:40]
            if not cid:
                cid = re.sub(r"[^a-zA-Z0-9_]", "_", label).strip("_").lower()[:32] or f"campo_{len(custom_out)+1}"
            if cid in vistos:
                cid = f"{cid[:32]}_{len(custom_out)+1}"
            vistos.add(cid)

            secao = str(entry.get("secao") or "entrada").strip().lower()
            if secao not in {"entrada", "saida"}:
                secao = "entrada"
            tipo = str(entry.get("tipo") or "texto").strip().lower()
            if tipo not in {"texto", "textarea", "numero", "select", "checkbox"}:
                tipo = "texto"
            opcoes_raw = entry.get("opcoes")
            if isinstance(opcoes_raw, str):
                opcoes = [SecurityValidator.sanitizar_texto(o)[:60] for o in opcoes_raw.split(",") if SecurityValidator.sanitizar_texto(o)]
            elif isinstance(opcoes_raw, list):
                opcoes = [SecurityValidator.sanitizar_texto(o)[:60] for o in opcoes_raw if SecurityValidator.sanitizar_texto(o)]
            else:
                opcoes = []

            custom_out.append({
                "id": cid,
                "label": label,
                "secao": secao,
                "tipo": tipo,
                "opcoes": opcoes[:20],
                "obrigatorio": bool(entry.get("obrigatorio", False)),
                "placeholder": SecurityValidator.sanitizar_texto(entry.get("placeholder"))[:80],
            })

    return {"padrao": padrao_out, "personalizados": custom_out}

PERMISSION_SECTORS = [
    {
        "key": "ti",
        "label": "🖥️ Bancada TI & Ordens de Serviço",
        "descricao": "Atendimento técnico, registro de entradas/entregas de equipamentos, SLA e dashboard da bancada.",
    },
    {
        "key": "loja",
        "label": "🏪 Loja de TI (PDV, Orçamentos & RMA)",
        "descricao": "Frente de caixa (PDV), propostas/montagem de PCs, garantia por serial, RMA, clientes B2B e fornecedores.",
    },
    {
        "key": "estoque",
        "label": "📦 Estoque & Almoxarifado TI",
        "descricao": "Peças, componentes, periféricos, garantia de itens, preços varejo/B2B e movimentação por O.S./venda.",
    },
    {
        "key": "automacao",
        "label": "🤖 Automação, CFTV & IoT",
        "descricao": "Auditoria e hardening de câmeras IP, NVR/DVR, infraestrutura de rack, sensores IoT e alarmes.",
    },
    {
        "key": "ciberseguranca",
        "label": "🛡️ Cibersegurança & Governança",
        "descricao": "Auditorias autorizadas de rede/TLS, gestão de vulnerabilidades, checklists CIS/NIST/LGPD e playbooks.",
    },
    {
        "key": "laudos",
        "label": "📄 Laudos, Pareceres & Atendimento",
        "descricao": "Emissão e edição de laudos técnicos em PDF, comprovantes, assinatura digital, link público e WhatsApp.",
    },
    {
        "key": "financeiro",
        "label": "💰 Financeiro & Painel Executivo",
        "descricao": "Dashboard executivo global, análise de custos, margens, faturamento e valores financeiros (R$).",
    },
    {
        "key": "administracao",
        "label": "🏢 Administração & Segurança",
        "descricao": "Timbre/modelos de laudos, gestão de equipe e permissões, personalização de campos e backups.",
    },
]

PERMISSIONS_CATALOG = [
    # --- SETOR: BANCADA TI & ORDENS DE SERVIÇO ---
    {
        "key": "ver_dashboard_entradas",
        "setor": "ti",
        "label": "Ver Dashboard da Bancada TI",
        "descricao": "Exibe os indicadores de entradas, fila pendente, atrasos de SLA e distribuição por bancada.",
        "default_tecnico": True,
    },
    {
        "key": "ver_ti",
        "setor": "ti",
        "label": "Acessar Bancada TI e Fila de O.S.",
        "descricao": "Visualiza a fila de equipamentos em manutenção, histórico do ativo e ficha completa da O.S.",
        "default_tecnico": True,
    },
    {
        "key": "registrar_entrada",
        "setor": "ti",
        "label": "Registrar Entrada de Equipamentos",
        "descricao": "Permite abrir novas Ordens de Serviço (individuais ou em lote) com resumo automático de coleta.",
        "default_tecnico": True,
    },
    {
        "key": "editar_entrada",
        "setor": "ti",
        "label": "Atualizar Diagnóstico, Local e Checklist",
        "descricao": "Permite editar dados da O.S., trocar a bancada/status e marcar itens do checklist técnico.",
        "default_tecnico": True,
    },
    {
        "key": "registrar_saida",
        "setor": "ti",
        "label": "Finalizar O.S. e Registrar Entrega",
        "descricao": "Permite concluir o reparo, definir prazo de garantia e gerar o resumo automático de entrega.",
        "default_tecnico": True,
    },
    {
        "key": "excluir_registros",
        "setor": "ti",
        "label": "Excluir Registros da Bancada TI",
        "descricao": "Ação crítica: apaga permanentemente uma Ordem de Serviço e seu histórico de passagens.",
        "default_tecnico": False,
    },
    # --- SETOR: LOJA DE TI (PDV, ORÇAMENTOS, CAIXA & RMA) ---
    {
        "key": "ver_dashboard_loja",
        "setor": "loja",
        "label": "Ver Dashboard Comercial da Loja de TI",
        "descricao": "Exibe os cards de faturamento do dia/mês, ticket médio, Curva ABC e taxa de orçamentos.",
        "default_tecnico": False,
    },
    {
        "key": "ver_loja",
        "setor": "loja",
        "label": "Acessar Módulo Loja de TI",
        "descricao": "Libera a aba Loja de TI para consultar vendas, propostas, catálogo comercial e garantias.",
        "default_tecnico": True,
    },
    {
        "key": "operar_pdv_vendas",
        "setor": "loja",
        "label": "Operar PDV (Vendas, NFC-e/NF-e e Baixa Automática)",
        "descricao": "Realiza vendas no balcão/B2B com vínculo de Serial/IMEI, emissão de recibo PDF e baixa no estoque.",
        "default_tecnico": True,
    },
    {
        "key": "gerenciar_orcamentos_loja",
        "setor": "loja",
        "label": "Criar Orçamentos e Montagem de PCs",
        "descricao": "Elabora propostas comerciais em PDF e converte com 1 clique em Venda ou O.S. da Bancada.",
        "default_tecnico": True,
    },
    {
        "key": "gerenciar_caixa_loja",
        "setor": "loja",
        "label": "Abrir, Movimentar e Fechar Caixa (PDV)",
        "descricao": "Controla turnos de caixa, suprimentos, sangrias, conferência de fechamento e comissões.",
        "default_tecnico": False,
    },
    {
        "key": "gerenciar_garantias_rma",
        "setor": "loja",
        "label": "Gerenciar Garantias por Serial & RMA",
        "descricao": "Valida garantia antifraude por Serial/IMEI e controla trocas/RMA de clientes e distribuidores.",
        "default_tecnico": True,
    },
    {
        "key": "gerenciar_clientes_loja",
        "setor": "loja",
        "label": "Cadastrar Clientes PF/PJ e Fornecedores",
        "descricao": "Gerencia carteira de clientes varejo/B2B, fornecedores/distribuidores e filiais.",
        "default_tecnico": True,
    },
    # --- SETOR: ESTOQUE & ALMOXARIFADO TI ---
    {
        "key": "ver_dashboard_estoque",
        "setor": "estoque",
        "label": "Ver Dashboard do Estoque",
        "descricao": "Exibe os indicadores de total de SKUs, unidades disponíveis, itens críticos e valor em estoque.",
        "default_tecnico": True,
    },
    {
        "key": "ver_estoque",
        "setor": "estoque",
        "label": "Consultar Catálogo e Saldos de Estoque",
        "descricao": "Visualiza produtos, peças, almoxarifados, prazos de garantia e histórico de movimentações.",
        "default_tecnico": True,
    },
    {
        "key": "movimentar_estoque",
        "setor": "estoque",
        "label": "Movimentar e Dar Baixa de Peças em O.S.",
        "descricao": "Registra entradas manuais, saídas e aplicação de peças/insumos vinculados às Ordens de Serviço.",
        "default_tecnico": True,
    },
    {
        "key": "gerenciar_estoque",
        "setor": "estoque",
        "label": "Cadastrar Produtos, Preços e Excluir SKUs",
        "descricao": "Cria e edita itens de venda/peças, código de barras/NCM, preços varejo/B2B e estoque mínimo.",
        "default_tecnico": False,
    },
    # --- SETOR: AUTOMAÇÃO, CFTV & IOT ---
    {
        "key": "ver_dashboard_automacao",
        "setor": "automacao",
        "label": "Ver Dashboard de Automação & CFTV",
        "descricao": "Exibe o resumo de ativos auditados, taxa de conformidade, alertas e riscos críticos.",
        "default_tecnico": True,
    },
    {
        "key": "ver_automacao",
        "setor": "automacao",
        "label": "Consultar Inventário de CFTV, Infra & IoT",
        "descricao": "Visualiza câmeras IP, NVRs, controladores IoT, alarmes e infraestrutura predial auditados.",
        "default_tecnico": True,
    },
    {
        "key": "gerenciar_automacao",
        "setor": "automacao",
        "label": "Auditar e Cadastrar Ativos de Automação",
        "descricao": "Registra auditorias de segurança (senha, firmware, VLAN, backup) e atualiza conformidade.",
        "default_tecnico": True,
    },
    # --- SETOR: CIBERSEGURANÇA & GOVERNANÇA ---
    {
        "key": "ver_dashboard_cyber",
        "setor": "ciberseguranca",
        "label": "Ver Dashboard de Cibersegurança",
        "descricao": "Exibe o score de maturidade CIS/NIST, contagem de achados abertos/críticos e status de governança.",
        "default_tecnico": False,
    },
    {
        "key": "ver_ciberseguranca",
        "setor": "ciberseguranca",
        "label": "Acessar Painel Cyber & Base de Conhecimento",
        "descricao": "Visualiza histórico de auditorias, matriz de vulnerabilidades e playbooks defensivos.",
        "default_tecnico": False,
    },
    {
        "key": "gerenciar_auditoria_cyber",
        "setor": "ciberseguranca",
        "label": "Gerir Vulnerabilidades, Checklists CIS & Guias",
        "descricao": "Registra e mitiga achados, atualiza controles CIS/NIST/LGPD, publica playbooks e importa LAN JSON.",
        "default_tecnico": False,
    },
    {
        "key": "executar_auditoria_rede",
        "setor": "ciberseguranca",
        "label": "Executar Auditoria Ativa (c/ Autorização)",
        "descricao": "Dispara varreduras defensivas de portas e TLS apenas dentro do escopo liberado pelo gestor.",
        "default_tecnico": False,
    },
    # --- SETOR: LAUDOS, PARECERES & ATENDIMENTO ---
    {
        "key": "ver_dashboard_laudos",
        "setor": "laudos",
        "label": "Ver Dashboard de Laudos Emitidos",
        "descricao": "Exibe os cartões de resumo com total de laudos, aprovações, visibilidade pública e modelos.",
        "default_tecnico": True,
    },
    {
        "key": "acessar_criador_laudos",
        "setor": "laudos",
        "label": "Acessar Aba Dedicada Criador de Laudos",
        "descricao": "Libera a visão separada Criador de Laudos com pré-visualização em tempo real e modelos prontos.",
        "default_tecnico": True,
    },
    {
        "key": "gerenciar_laudos",
        "setor": "laudos",
        "label": "Emitir, Editar e Baixar Laudos em PDF",
        "descricao": "Permite criar, editar, imprimir e baixar laudos ou pareceres técnicos anexados às O.S.",
        "default_tecnico": True,
    },
    {
        "key": "compartilhar_os",
        "setor": "laudos",
        "label": "Comprovante PDF, Link Cliente e Assinatura",
        "descricao": "Gera comprovante de O.S., link público de acompanhamento, assinatura na tela e envio por WhatsApp.",
        "default_tecnico": True,
    },
    {
        "key": "exportar_pdf",
        "setor": "laudos",
        "label": "Exportar Relatórios Executivos em PDF",
        "descricao": "Permite gerar e baixar relatórios consolidados em PDF da bancada, loja, automação e cyber.",
        "default_tecnico": True,
    },
    # --- SETOR: FINANCEIRO & PAINEL EXECUTIVO ---
    {
        "key": "ver_dashboard",
        "setor": "financeiro",
        "label": "Ver Dashboard Executivo Global",
        "descricao": "Acessa gráficos gerenciais, ranking de técnicos e consolidado operacional completo da empresa.",
        "default_tecnico": True,
    },
    {
        "key": "ver_custos",
        "setor": "financeiro",
        "label": "Visualizar Custos, Margens e Valores (R$)",
        "descricao": "Exibe preços de custo, lucro, totais em R$, caixa e campos financeiros nas O.S., loja e estoque.",
        "default_tecnico": True,
    },
    # --- SETOR: ADMINISTRAÇÃO & SEGURANÇA ---
    {
        "key": "gerenciar_modelos",
        "setor": "administracao",
        "label": "Personalizar Timbre, Modelos e Checklist",
        "descricao": "Altera logo, CNPJ, cores dos documentos PDF, checklist padrão e modelos de laudo da empresa.",
        "default_tecnico": False,
    },
    {
        "key": "gerenciar_equipe",
        "setor": "administracao",
        "label": "Gerenciar Equipe, Permissões e Campos da O.S.",
        "descricao": "Cadastra colaboradores, define permissões, configura setores ativos e edita campos da Bancada TI.",
        "default_tecnico": False,
    },
    {
        "key": "importar_exportar_dados",
        "setor": "administracao",
        "label": "Importar Planilhas e Exportar Backups",
        "descricao": "Importa arquivos CSV/XLSX/JSON de outros sistemas e exporta cópias de segurança da empresa.",
        "default_tecnico": False,
    },
]

LEGACY_PERMISSION_ALIASES: dict[str, tuple[str, ...]] = {
    "gerenciar_operacoes_ti": (
        "ver_estoque",
        "movimentar_estoque",
        "ver_loja",
        "gerenciar_clientes_loja",
    ),
    "ver_provedor": ("ver_loja",),
    "gerenciar_clientes_pops": ("gerenciar_clientes_loja",),
    "gerenciar_preventivas": ("gerenciar_garantias_rma",),
}

DEFAULT_TECH_PERMISSIONS: dict[str, bool] = {
    item["key"]: bool(item["default_tecnico"]) for item in PERMISSIONS_CATALOG
}
ALL_PERMISSIONS_TRUE: dict[str, bool] = {
    item["key"]: True for item in PERMISSIONS_CATALOG
}
_VALID_PERMISSION_KEYS = set(ALL_PERMISSIONS_TRUE.keys())

ROLE_PRESETS: dict[str, dict[str, Any]] = {
    "tecnico_ti": {
        "label": "🖥️ Técnico TI (Bancada)",
        "descricao": "O.S. de bancada, dashboard de entradas, laudos técnicos e comprovante (sem Caixa ou Custos).",
        "permissoes": {
            "ver_dashboard_entradas": True,
            "ver_ti": True,
            "registrar_entrada": True,
            "editar_entrada": True,
            "registrar_saida": True,
            "ver_dashboard_laudos": True,
            "acessar_criador_laudos": True,
            "gerenciar_laudos": True,
            "compartilhar_os": True,
            "exportar_pdf": True,
        },
    },
    "vendedor_loja": {
        "label": "🏪 Vendedor / Balcão Loja TI",
        "descricao": "PDV, orçamentos de PCs, cadastro de clientes, garantias/RMA e consulta de estoque (sem custos de compra).",
        "permissoes": {
            "ver_dashboard_loja": True,
            "ver_loja": True,
            "operar_pdv_vendas": True,
            "gerenciar_orcamentos_loja": True,
            "gerenciar_garantias_rma": True,
            "gerenciar_clientes_loja": True,
            "ver_dashboard_estoque": True,
            "ver_estoque": True,
            "compartilhar_os": True,
            "exportar_pdf": True,
        },
    },
    "gerente_loja": {
        "label": "🏬 Gerente Loja de TI",
        "descricao": "Acesso completo à Loja de TI (PDV, Caixa, Orçamentos, RMA, Custos/Margem) + Estoque e Dashboards.",
        "permissoes": {
            "ver_dashboard": True,
            "ver_custos": True,
            "ver_dashboard_entradas": True,
            "ver_ti": True,
            "registrar_entrada": True,
            "editar_entrada": True,
            "registrar_saida": True,
            "ver_dashboard_loja": True,
            "ver_loja": True,
            "operar_pdv_vendas": True,
            "gerenciar_orcamentos_loja": True,
            "gerenciar_caixa_loja": True,
            "gerenciar_garantias_rma": True,
            "gerenciar_clientes_loja": True,
            "ver_dashboard_estoque": True,
            "ver_estoque": True,
            "movimentar_estoque": True,
            "gerenciar_estoque": True,
            "ver_dashboard_laudos": True,
            "acessar_criador_laudos": True,
            "gerenciar_laudos": True,
            "compartilhar_os": True,
            "exportar_pdf": True,
        },
    },
    "estoquista": {
        "label": "📦 Estoquista / Almoxarifado",
        "descricao": "Acesso dedicado ao Estoque e Almoxarifado TI (cadastro de SKUs, saldos, alertas e movimentações).",
        "permissoes": {
            "ver_dashboard_estoque": True,
            "ver_estoque": True,
            "movimentar_estoque": True,
            "gerenciar_estoque": True,
        },
    },
    "financeiro_gestor": {
        "label": "💰 Gestor / Financeiro",
        "descricao": "Todos os dashboards, custos, margens, caixa e relatórios PDF (sem alterar O.S. operacionais).",
        "permissoes": {
            "ver_dashboard": True,
            "ver_custos": True,
            "ver_dashboard_entradas": True,
            "ver_ti": True,
            "ver_dashboard_loja": True,
            "ver_loja": True,
            "gerenciar_caixa_loja": True,
            "ver_dashboard_estoque": True,
            "ver_estoque": True,
            "ver_dashboard_automacao": True,
            "ver_automacao": True,
            "ver_dashboard_cyber": True,
            "ver_ciberseguranca": True,
            "ver_dashboard_laudos": True,
            "exportar_pdf": True,
        },
    },
    "operacional_sem_custos": {
        "label": "🔧 Operacional (Sem Custos)",
        "descricao": "TI, Loja de TI, Estoque (baixa), Automação e Laudos sem visualização de custos ou lucros.",
        "permissoes": {
            "ver_dashboard_entradas": True,
            "ver_ti": True,
            "registrar_entrada": True,
            "editar_entrada": True,
            "registrar_saida": True,
            "ver_loja": True,
            "operar_pdv_vendas": True,
            "gerenciar_orcamentos_loja": True,
            "gerenciar_garantias_rma": True,
            "gerenciar_clientes_loja": True,
            "ver_dashboard_estoque": True,
            "ver_estoque": True,
            "movimentar_estoque": True,
            "ver_dashboard_automacao": True,
            "ver_automacao": True,
            "gerenciar_automacao": True,
            "ver_dashboard_laudos": True,
            "acessar_criador_laudos": True,
            "gerenciar_laudos": True,
            "compartilhar_os": True,
            "exportar_pdf": True,
        },
    },
    "padrao": {
        "label": "⚙️ Técnico Padrão",
        "descricao": "Configuração padrão equilibrada entre Bancada TI, Loja de TI, Estoque, Automação e Laudos.",
        "permissoes": dict(DEFAULT_TECH_PERMISSIONS),
    },
    "tecnico_automacao": {
        "label": "🤖 Técnico Automação / CFTV",
        "descricao": "Auditoria de câmeras, infra, IoT, alarmes, O.S. de bancada e emissão de laudos.",
        "permissoes": {
            "ver_dashboard_entradas": True,
            "ver_ti": True,
            "registrar_entrada": True,
            "editar_entrada": True,
            "registrar_saida": True,
            "ver_dashboard_automacao": True,
            "ver_automacao": True,
            "gerenciar_automacao": True,
            "ver_dashboard_laudos": True,
            "acessar_criador_laudos": True,
            "gerenciar_laudos": True,
            "compartilhar_os": True,
            "exportar_pdf": True,
        },
    },
    "analista_cyber": {
        "label": "🛡️ Analista Cibersegurança",
        "descricao": "Auditorias autorizadas pelo gestor, achados, checklists CIS/NIST/LGPD e base de conhecimento.",
        "permissoes": {
            "ver_dashboard_cyber": True,
            "ver_ciberseguranca": True,
            "gerenciar_auditoria_cyber": True,
            "executar_auditoria_rede": True,
            "ver_dashboard_automacao": True,
            "ver_automacao": True,
            "ver_ti": True,
            "exportar_pdf": True,
        },
    },
}


def _expandir_permissoes_custom(
    custom: dict[str, Any], base: dict[str, bool]
) -> dict[str, bool]:
    resolved = dict(base)
    for alias_key, targets in LEGACY_PERMISSION_ALIASES.items():
        if alias_key in custom and isinstance(custom[alias_key], bool):
            alias_val = bool(custom[alias_key])
            for target_key in targets:
                if target_key not in custom:
                    resolved[target_key] = alias_val
    for key, val in custom.items():
        if key in _VALID_PERMISSION_KEYS and isinstance(val, bool):
            resolved[key] = val
    return resolved


def resolver_permissoes(
    role: str, custom: dict[str, Any] | None = None
) -> dict[str, bool]:
    if str(role or "").strip().lower() == "admin":
        return dict(ALL_PERMISSIONS_TRUE)
    if not isinstance(custom, dict):
        return dict(DEFAULT_TECH_PERMISSIONS)
    return _expandir_permissoes_custom(custom, DEFAULT_TECH_PERMISSIONS)


def normalizar_permissoes_input(
    permissoes: dict[str, Any] | None,
) -> dict[str, bool]:
    if permissoes is None:
        return dict(DEFAULT_TECH_PERMISSIONS)
    if not isinstance(permissoes, dict):
        raise ValueError("Formato de permissões inválido.")
    allowed_input = _VALID_PERMISSION_KEYS | set(LEGACY_PERMISSION_ALIASES.keys())
    unknown = set(permissoes.keys()) - allowed_input
    if unknown:
        raise ValueError(
            f"Permissões desconhecidas: {', '.join(sorted(unknown))}."
        )
    converted = {
        key: bool(val)
        for key, val in permissoes.items()
    }
    return _expandir_permissoes_custom(converted, DEFAULT_TECH_PERMISSIONS)


def usuario_tem_permissao(user: dict[str, Any], perm_key: str) -> bool:
    if not user:
        return False
    if str(user.get("role") or "").strip().lower() == "admin":
        return True
    perms = resolver_permissoes(
        user.get("role", "tecnico"), user.get("permissoes")
    )
    if perm_key in LEGACY_PERMISSION_ALIASES:
        return any(bool(perms.get(k, False)) for k in LEGACY_PERMISSION_ALIASES[perm_key])
    return bool(perms.get(perm_key, False))


class AuthenticationError(Exception):
    """Token ausente, inválido ou expirado."""


class MfaRequiredError(AuthenticationError):
    """Autenticação primária concluída; exige segundo fator (2FA TOTP)."""

    def __init__(self, email: str = "", mode: str = "firebase", challenge_token: str = ""):
        super().__init__("Verificação em duas etapas (2FA) necessária para concluir o acesso.")
        self.email = email
        self.mode = mode
        self.challenge_token = challenge_token


class RateLimitError(AuthenticationError):
    """Excesso de tentativas inválidas; conta/IP temporariamente bloqueado."""

    def __init__(self, retry_after: int = 300):
        minutos = max(1, (int(retry_after) + 59) // 60)
        super().__init__(
            f"Muitas tentativas inválidas. Por segurança, aguarde cerca de {minutos} minuto(s) e tente novamente."
        )
        self.retry_after = max(1, int(retry_after))


class AuthorizationError(Exception):
    """Usuário sem permissão ou conta desativada."""


class OnboardingRequiredError(Exception):
    """Usuário autenticado no Firebase ainda sem empresa vinculada."""

    def __init__(self, identity: dict[str, Any]):
        super().__init__("Finalize o cadastro da sua empresa para continuar.")
        self.identity = identity


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validar_email(email: str) -> str:
    clean = SecurityValidator.sanitizar_texto(email).lower()
    if not clean or not _EMAIL_REGEX.match(clean):
        raise ValueError("Informe um endereço de e-mail válido.")
    return clean


def _validar_senha(senha: str) -> str:
    if not isinstance(senha, str) or len(senha) < 6:
        raise ValueError("A senha deve ter pelo menos 6 caracteres.")
    return senha


def _validar_role(role: str) -> str:
    clean = SecurityValidator.sanitizar_texto(role).lower() or "tecnico"
    if clean not in ROLES_PERMITIDAS:
        raise ValueError("Papel inválido. Use 'admin' ou 'tecnico'.")
    return clean


def _normalizar_localizacoes(localizacoes: list[str] | None) -> list[str]:
    if not localizacoes:
        return list(DEFAULT_LOCATIONS)
    limpas: list[str] = []
    vistas: set[str] = set()
    for item in localizacoes:
        texto = SecurityValidator.sanitizar_texto(item)
        if texto and texto.lower() not in vistas:
            vistas.add(texto.lower())
            limpas.append(texto)
    if not limpas:
        raise ValueError("Informe pelo menos uma localização válida.")
    return limpas


def sanitizar_usuario(user: dict) -> dict:
    """Remove dados sensíveis (hash de senha, segredo 2FA e códigos) antes de expor na API."""
    role = user.get("role", "tecnico")
    ultimos = user.get("ultimos_acessos")
    return {
        "uid": user.get("uid", ""),
        "email": user.get("email", ""),
        "nome": user.get("nome", ""),
        "role": role,
        "tenant_id": user.get("tenant_id", ""),
        "ativo": bool(user.get("ativo", True)),
        "permissoes": resolver_permissoes(role, user.get("permissoes")),
        "mfa_enabled": bool(user.get("mfa_enabled", False)),
        "mfa_ativado_em": str(user.get("mfa_ativado_em") or ""),
        "must_change_password": bool(user.get("must_change_password", False)),
        "password_changed_at": str(user.get("password_changed_at") or ""),
        "session_version": int(user.get("session_version") or 1),
        "ultimos_acessos": list(ultimos[:10]) if isinstance(ultimos, list) else [],
        "criado_em": user.get("criado_em", ""),
    }


def _normalizar_setores_ativos(setores: dict[str, Any] | None) -> dict[str, bool]:
    base = dict(DEFAULT_ACTIVE_SECTORS)
    if isinstance(setores, dict):
        if "loja" not in setores and "provedor" in setores:
            base["loja"] = bool(setores["provedor"])
        for key in DEFAULT_ACTIVE_SECTORS:
            if key in setores:
                base[key] = bool(setores[key])
    if not any(base.values()):
        raise ValueError("Mantenha pelo menos um setor ativo na empresa.")
    return base


def sanitizar_tenant(tenant: dict) -> dict:
    checklist = tenant.get("checklist_padrao")
    if not isinstance(checklist, list) or not checklist:
        checklist = list(CHECKLIST_PRESETS["padrao"]["itens"])
    modelos = tenant.get("modelos_laudo")
    if not isinstance(modelos, list) or not modelos:
        modelos = [dict(tpl) for tpl in DEFAULT_LAUDO_TEMPLATES]
    setores_raw = tenant.get("setores_ativos")
    setores = dict(DEFAULT_ACTIVE_SECTORS)
    if isinstance(setores_raw, dict):
        if "loja" not in setores_raw and "provedor" in setores_raw:
            setores["loja"] = bool(setores_raw["provedor"])
        for key in DEFAULT_ACTIVE_SECTORS:
            if key in setores_raw:
                setores[key] = bool(setores_raw[key])
    gov_raw = tenant.get("cyber_governanca")
    gov = {
        "permitir_auditoria_rede": bool(gov_raw.get("permitir_auditoria_rede")) if isinstance(gov_raw, dict) else False,
        "usuarios_autorizados": list(gov_raw.get("usuarios_autorizados") or []) if isinstance(gov_raw, dict) else [],
        "restringir_usuarios": bool(gov_raw.get("restringir_usuarios")) if isinstance(gov_raw, dict) else False,
        "escopo_autorizado": list(gov_raw.get("escopo_autorizado") or []) if isinstance(gov_raw, dict) else [],
        "observacao_termo": str(gov_raw.get("observacao_termo") or "") if isinstance(gov_raw, dict) else "",
        "autorizado_por": str(gov_raw.get("autorizado_por") or "") if isinstance(gov_raw, dict) else "",
        "autorizado_em": str(gov_raw.get("autorizado_em") or "") if isinstance(gov_raw, dict) else "",
    }
    return {
        "id": tenant.get("id", ""),
        "nome": tenant.get("nome", "SysFix"),
        "plano": tenant.get("plano", "pro"),
        "ativo": bool(tenant.get("ativo", True)),
        "sla_dias": int(tenant.get("sla_dias") or 3),
        "localizacoes": list(tenant.get("localizacoes") or DEFAULT_LOCATIONS),
        "setores_ativos": setores,
        "exigir_mfa_equipe": bool(tenant.get("exigir_mfa_equipe", False)),
        "campos_bancada": _normalizar_campos_bancada(tenant.get("campos_bancada")),
        "cyber_governanca": gov,
        "cnpj": tenant.get("cnpj", ""),
        "telefone": tenant.get("telefone", ""),
        "endereco": tenant.get("endereco", ""),
        "rodape_documento": tenant.get("rodape_documento", ""),
        "cor_documento": tenant.get("cor_documento") or "#4C1D95",
        "logo_base64": tenant.get("logo_base64", ""),
        "checklist_padrao": list(checklist),
        "modelos_laudo": [dict(m) for m in modelos],
        "criado_em": tenant.get("criado_em", ""),
    }


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iter_str, salt_hex, hash_hex = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False

    computed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(computed, expected)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def create_local_token(user: dict) -> str:
    now = int(time.time())
    ttl_seconds = get_token_ttl_hours() * 3600
    payload = {
        "purpose": "session",
        "uid": user["uid"],
        "tenant_id": user["tenant_id"],
        "role": user["role"],
        "email": user["email"],
        "sv": int(user.get("session_version") or 1),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    sig = hmac.new(
        get_secret_key().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_local_token(token: str) -> dict[str, Any]:
    if not token or "." not in token:
        raise AuthenticationError("Token de sessão inválido.")
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(
            get_secret_key().encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise AuthenticationError("Assinatura do token inválida.")
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError("Token de sessão malformado.") from exc

    purpose = payload.get("purpose")
    if purpose and purpose != "session":
        raise AuthenticationError("Token inadequado para sessão autenticada.")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise AuthenticationError("Sessão expirada. Faça login novamente.")
    if not payload.get("uid"):
        raise AuthenticationError("Token sem identificador de usuário.")
    return payload


class LocalTenantUserStore:
    """Armazenamento local em JSON de empresas (tenants) e usuários."""

    def __init__(self, path: str | Path | None = None):
        self._custom_path = Path(path) if path else None

    @property
    def path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "auth_db.json"

    @staticmethod
    def _empty_data() -> dict:
        return {"version": 1, "tenants": {}, "users": {}}

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return self._empty_data()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Não foi possível ler o arquivo de autenticação {self.path}: {exc}"
            ) from exc
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("tenants"), dict)
            or not isinstance(data.get("users"), dict)
        ):
            raise RuntimeError(f"Formato inválido em {self.path}")
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
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(
                    data,
                    temp_file,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                )
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def ensure(self) -> None:
        with _AUTH_LOCK:
            if not self.path.exists():
                self._write_unlocked(self._empty_data())

    def count_tenants(self) -> int:
        with _AUTH_LOCK:
            return len(self._read_unlocked()["tenants"])

    def get_tenant(self, tenant_id: str) -> dict | None:
        with _AUTH_LOCK:
            tenant = self._read_unlocked()["tenants"].get(tenant_id)
            return dict(tenant) if tenant else None

    def create_tenant(self, tenant_dict: dict) -> dict:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            tenant = dict(tenant_dict)
            data["tenants"][tenant["id"]] = tenant
            self._write_unlocked(data)
            return dict(tenant)

    def update_tenant(self, tenant_id: str, updates: dict) -> dict | None:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            tenant = data["tenants"].get(tenant_id)
            if not tenant:
                return None
            tenant.update(updates)
            self._write_unlocked(data)
            return dict(tenant)

    def get_user(self, uid: str) -> dict | None:
        with _AUTH_LOCK:
            user = self._read_unlocked()["users"].get(uid)
            return dict(user) if user else None

    def get_user_by_email(self, email: str) -> dict | None:
        email_norm = email.strip().lower()
        with _AUTH_LOCK:
            for user in self._read_unlocked()["users"].values():
                if str(user.get("email") or "").strip().lower() == email_norm:
                    return dict(user)
        return None

    def list_users_by_tenant(self, tenant_id: str) -> list[dict]:
        with _AUTH_LOCK:
            users = [
                dict(user)
                for user in self._read_unlocked()["users"].values()
                if user.get("tenant_id") == tenant_id
            ]
        return sorted(users, key=lambda u: str(u.get("nome") or "").lower())

    def create_user(self, user_dict: dict) -> dict:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            user = dict(user_dict)
            user["email"] = str(user.get("email") or "").strip().lower()
            data["users"][user["uid"]] = user
            self._write_unlocked(data)
            return dict(user)

    def update_user(self, uid: str, updates: dict) -> dict | None:
        with _AUTH_LOCK:
            data = self._read_unlocked()
            user = data["users"].get(uid)
            if not user:
                return None
            user.update(updates)
            self._write_unlocked(data)
            return dict(user)


_local_auth_store = LocalTenantUserStore()


def get_tenant_user_store():
    if get_storage_backend() == "firebase":
        from firebase_backend import FirestoreTenantUserStore

        return FirestoreTenantUserStore()
    return _local_auth_store


class AuthService:
    """Orquestra cadastro, autenticação, RBAC e gestão de empresa em modo Local ou Firebase."""

    def __init__(
        self,
        user_store=None,
        firebase_auth_provider=None,
        backend_override: str | None = None,
    ):
        self._user_store = user_store
        self._firebase_auth = firebase_auth_provider
        self._backend_override = backend_override

    @property
    def backend(self) -> str:
        return self._backend_override or get_storage_backend()

    @property
    def user_store(self):
        if self._user_store is not None:
            return self._user_store
        return get_tenant_user_store()

    @property
    def firebase_auth(self):
        if self._firebase_auth is None:
            from firebase_backend import FirebaseAuthProvider

            self._firebase_auth = FirebaseAuthProvider()
        return self._firebase_auth

    def _next_tenant_id(self) -> str:
        if self.backend == "local" and self.user_store.count_tenants() == 0:
            from storage import JsonStore

            if JsonStore().has_legacy_default_records():
                return "default"
        return f"emp_{uuid.uuid4().hex[:10]}"

    def _build_tenant(self, nome_empresa: str, tenant_id: str | None = None) -> dict:
        nome = SecurityValidator.sanitizar_texto(nome_empresa)
        if not nome:
            raise ValueError("O nome da empresa é obrigatório.")
        return {
            "id": tenant_id or self._next_tenant_id(),
            "nome": nome,
            "plano": "pro",
            "ativo": True,
            "sla_dias": 3,
            "localizacoes": list(DEFAULT_LOCATIONS),
            "setores_ativos": dict(DEFAULT_ACTIVE_SECTORS),
            "exigir_mfa_equipe": False,
            "campos_bancada": _normalizar_campos_bancada(None),
            "cyber_governanca": {
                "permitir_auditoria_rede": False,
                "escopo_autorizado": [],
                "observacao_termo": "",
                "autorizado_por": "",
                "autorizado_em": "",
            },
            "cnpj": "",
            "telefone": "",
            "endereco": "",
            "rodape_documento": "",
            "cor_documento": "#4C1D95",
            "logo_base64": "",
            "checklist_padrao": list(CHECKLIST_PRESETS["padrao"]["itens"]),
            "modelos_laudo": [dict(tpl) for tpl in DEFAULT_LAUDO_TEMPLATES],
            "criado_em": _now_iso(),
        }

    def register_local(
        self,
        nome_empresa: str,
        nome_usuario: str,
        email: str,
        senha: str,
    ) -> dict[str, Any]:
        if self.backend == "firebase":
            raise ValueError(
                "O cadastro em modo Firebase deve ser feito pelo fluxo de onboarding autenticado."
            )

        nome_resp = SecurityValidator.sanitizar_texto(nome_usuario)
        if not nome_resp:
            raise ValueError("O nome do responsável é obrigatório.")
        email_norm = _validar_email(email)
        senha_valida = _validar_senha(senha)

        if self.user_store.get_user_by_email(email_norm):
            raise ValueError("Já existe uma conta cadastrada com este e-mail.")

        tenant = self.user_store.create_tenant(self._build_tenant(nome_empresa))
        user = self.user_store.create_user(
            {
                "uid": f"usr_{uuid.uuid4().hex[:12]}",
                "email": email_norm,
                "nome": nome_resp,
                "role": "admin",
                "tenant_id": tenant["id"],
                "ativo": True,
                "permissoes": dict(ALL_PERMISSIONS_TRUE),
                "password_hash": hash_password(senha_valida),
                "must_change_password": False,
                "password_changed_at": _now_iso(),
                "criado_em": _now_iso(),
            }
        )
        token = create_local_token(user)
        return {
            "token": token,
            "user": sanitizar_usuario(user),
            "tenant": sanitizar_tenant(tenant),
        }

    def login_local(
        self,
        email: str,
        senha: str,
        client_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        if self.backend == "firebase":
            raise ValueError(
                "O login em modo Firebase é realizado pelo SDK do Firebase no cliente."
            )

        email_norm = _validar_email(email)
        if not isinstance(senha, str) or not senha:
            raise ValueError("Informe a senha.")

        rate_keys = [f"email:{email_norm}"]
        if client_ip and client_ip not in {"desconhecido", ""}:
            rate_keys.append(f"ip:{client_ip}")
        for rk in rate_keys:
            allowed, retry_after = login_rate_limiter.check_allowed(rk)
            if not allowed:
                raise RateLimitError(retry_after)

        user = self.user_store.get_user_by_email(email_norm)
        if not user or not verify_password(senha, str(user.get("password_hash") or "")):
            for rk in rate_keys:
                login_rate_limiter.record_failure(rk)
            if user:
                try:
                    aud_err = append_login_audit(
                        user.get("ultimos_acessos"),
                        build_login_audit_entry("Falha de senha", ip=client_ip, user_agent=user_agent, metodo="senha"),
                    )
                    self.user_store.update_user(user["uid"], {"ultimos_acessos": aud_err})
                except Exception:
                    pass
            raise AuthenticationError("E-mail ou senha inválidos.")

        for rk in rate_keys:
            login_rate_limiter.reset(rk)

        if not user.get("ativo", True):
            raise AuthorizationError("Sua conta está desativada. Procure o administrador da empresa.")

        tenant = self.user_store.get_tenant(user["tenant_id"])
        if not tenant or not tenant.get("ativo", True):
            raise AuthorizationError("Esta empresa está inativa ou indisponível.")

        if user.get("mfa_enabled"):
            return {
                "mfa_required": True,
                "challenge_token": create_mfa_challenge_token(user),
                "email": user["email"],
                "nome": user.get("nome", ""),
            }

        auditoria = append_login_audit(
            user.get("ultimos_acessos"),
            build_login_audit_entry("Sucesso", ip=client_ip, user_agent=user_agent, metodo="senha"),
        )
        updated_user = self.user_store.update_user(user["uid"], {"ultimos_acessos": auditoria}) or user
        token = create_local_token(updated_user)
        return {
            "mfa_required": False,
            "token": token,
            "user": sanitizar_usuario(updated_user),
            "tenant": sanitizar_tenant(tenant),
        }

    def verify_mfa_login_local(
        self,
        challenge_token: str,
        code: str,
        client_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        if self.backend == "firebase":
            raise ValueError("Use o endpoint de sessão MFA para autenticação Firebase.")
        try:
            payload = verify_mfa_challenge_token(challenge_token)
        except ValueError as exc:
            raise AuthenticationError(str(exc)) from exc

        uid = str(payload.get("uid") or "")
        rate_key = f"mfa:{uid}"
        allowed, retry_after = login_rate_limiter.check_allowed(rate_key)
        if not allowed:
            raise RateLimitError(retry_after)

        user = self.user_store.get_user(uid)
        if not user or not user.get("ativo", True):
            raise AuthenticationError("Usuário não encontrado ou inativo.")
        if int(payload.get("sv") or 1) != int(user.get("session_version") or 1):
            raise AuthenticationError("Desafio expirado pela revogação de sessão.")

        ok, remaining_hashes, usado_recovery = self._verify_user_mfa_code(user, code)
        if not ok:
            login_rate_limiter.record_failure(rate_key)
            raise AuthenticationError("Código 2FA inválido ou expirado.")

        login_rate_limiter.reset(rate_key)
        tenant = self.user_store.get_tenant(user["tenant_id"])
        if not tenant or not tenant.get("ativo", True):
            raise AuthorizationError("Empresa suspensa ou indisponível.")

        auditoria = append_login_audit(
            user.get("ultimos_acessos"),
            build_login_audit_entry(
                "Sucesso",
                ip=client_ip,
                user_agent=user_agent,
                metodo="senha+backup" if usado_recovery else "senha+2fa",
            ),
        )
        updates: dict[str, Any] = {"ultimos_acessos": auditoria}
        if usado_recovery:
            updates["mfa_recovery_hashes"] = remaining_hashes
        updated_user = self.user_store.update_user(user["uid"], updates) or user

        return {
            "token": create_local_token(updated_user),
            "user": sanitizar_usuario(updated_user),
            "tenant": sanitizar_tenant(tenant),
        }

    @staticmethod
    def _verify_user_mfa_code(user: dict, code: str) -> tuple[bool, list[str], bool]:
        secret = str(user.get("mfa_secret") or "")
        if secret and verify_totp(secret, code):
            return True, list(user.get("mfa_recovery_hashes") or []), False
        ok_rec, remaining = verify_and_consume_recovery_code(
            user.get("mfa_recovery_hashes"), code
        )
        if ok_rec:
            return True, remaining, True
        return False, list(user.get("mfa_recovery_hashes") or []), False

    def onboard_firebase_user(
        self, token: str, nome_empresa: str, nome_usuario: str = ""
    ) -> dict[str, Any]:
        if self.backend != "firebase":
            raise ValueError("Onboarding Firebase disponível apenas quando SYSFIX_BACKEND=firebase.")

        try:
            identity = self.firebase_auth.verify_id_token(token)
        except Exception as exc:
            raise AuthenticationError("Token Firebase inválido ou expirado.") from exc

        uid = identity["uid"]
        if self.user_store.get_user(uid):
            raise ValueError("Este usuário já possui uma empresa configurada.")

        email_norm = _validar_email(identity.get("email") or "")
        nome_final = SecurityValidator.sanitizar_texto(
            nome_usuario or identity.get("name") or email_norm.split("@")[0]
        )
        if not nome_final:
            raise ValueError("O nome do responsável é obrigatório.")

        tenant_id = (
            "default"
            if self.user_store.count_tenants() == 0 and not self.user_store.get_tenant("default")
            else f"emp_{uuid.uuid4().hex[:10]}"
        )
        tenant = self.user_store.create_tenant(
            self._build_tenant(nome_empresa, tenant_id=tenant_id)
        )
        user = self.user_store.create_user(
            {
                "uid": uid,
                "email": email_norm,
                "nome": nome_final,
                "role": "admin",
                "tenant_id": tenant["id"],
                "ativo": True,
                "permissoes": dict(ALL_PERMISSIONS_TRUE),
                "must_change_password": False,
                "password_changed_at": _now_iso(),
                "criado_em": _now_iso(),
            }
        )
        return {
            "user": sanitizar_usuario(user),
            "tenant": sanitizar_tenant(tenant),
        }

    def authenticate_token(
        self,
        token: str,
        mfa_token: str | None = None,
        allow_unverified_mfa: bool = False,
    ) -> dict[str, Any]:
        if not token:
            raise AuthenticationError("Token de autenticação não fornecido.")

        if self.backend == "firebase":
            try:
                identity = self.firebase_auth.verify_id_token(token)
            except Exception as exc:
                raise AuthenticationError("Token Firebase inválido ou expirado.") from exc

            user = self.user_store.get_user(identity["uid"])
            if not user:
                raise OnboardingRequiredError(identity)
        else:
            payload = verify_local_token(token)
            user = self.user_store.get_user(payload["uid"])
            if not user:
                raise AuthenticationError("Sessão inválida ou usuário removido.")
            token_sv = int(payload.get("sv") or 1)
            user_sv = int(user.get("session_version") or 1)
            if token_sv != user_sv:
                raise AuthenticationError("Sessão revogada. Faça login novamente.")

        if not user.get("ativo", True):
            raise AuthorizationError("Sua conta foi desativada pelo administrador.")

        if self.backend == "firebase" and user.get("mfa_enabled") and not allow_unverified_mfa:
            user_sv = int(user.get("session_version") or 1)
            if not verify_mfa_verified_token(
                mfa_token or "", expected_uid=user["uid"], expected_sv=user_sv
            ):
                raise MfaRequiredError(email=user.get("email", ""), mode="firebase")

        tenant = self.user_store.get_tenant(user["tenant_id"])
        if not tenant or not tenant.get("ativo", True):
            raise AuthorizationError("Empresa suspensa ou não encontrada.")

        ctx = sanitizar_usuario(user)
        ctx["tenant"] = sanitizar_tenant(tenant)
        return ctx

    def verify_mfa_firebase_session(
        self,
        firebase_token: str,
        code: str,
        client_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        ctx = self.authenticate_token(firebase_token, allow_unverified_mfa=True)
        uid = ctx["uid"]
        user = self.user_store.get_user(uid)
        if not user:
            raise AuthenticationError("Usuário não encontrado.")

        rate_key = f"mfa:{uid}"
        allowed, retry_after = login_rate_limiter.check_allowed(rate_key)
        if not allowed:
            raise RateLimitError(retry_after)

        ok, remaining_hashes, usado_recovery = self._verify_user_mfa_code(user, code)
        if not ok:
            login_rate_limiter.record_failure(rate_key)
            raise AuthenticationError("Código 2FA inválido ou expirado.")

        login_rate_limiter.reset(rate_key)
        auditoria = append_login_audit(
            user.get("ultimos_acessos"),
            build_login_audit_entry(
                "Sucesso",
                ip=client_ip,
                user_agent=user_agent,
                metodo="firebase+backup" if usado_recovery else "firebase+2fa",
            ),
        )
        updates: dict[str, Any] = {"ultimos_acessos": auditoria}
        if usado_recovery:
            updates["mfa_recovery_hashes"] = remaining_hashes
        updated_user = self.user_store.update_user(uid, updates) or user

        return {
            "mfa_token": create_mfa_verified_token(updated_user),
            "user": sanitizar_usuario(updated_user),
            "tenant": ctx["tenant"],
        }

    def get_user_security_status(self, user_ctx: dict[str, Any]) -> dict[str, Any]:
        user = self.user_store.get_user(user_ctx["uid"]) or user_ctx
        tenant = self.user_store.get_tenant(user_ctx["tenant_id"]) or user_ctx.get("tenant") or {}
        sanitized = sanitizar_usuario(user)
        return {
            "auth_mode": self.backend,
            "mfa_enabled": sanitized["mfa_enabled"],
            "mfa_ativado_em": sanitized["mfa_ativado_em"],
            "session_version": sanitized["session_version"],
            "ultimos_acessos": sanitized["ultimos_acessos"],
            "exigir_mfa_equipe": bool(tenant.get("exigir_mfa_equipe", False)),
            "recovery_remaining": len(user.get("mfa_recovery_hashes") or []),
        }

    def start_mfa_setup(self, user_ctx: dict[str, Any]) -> dict[str, Any]:
        user = self.user_store.get_user(user_ctx["uid"])
        if not user:
            raise LookupError("Usuário não encontrado.")
        secret = generate_totp_secret()
        self.user_store.update_user(user["uid"], {"mfa_pending_secret": secret})
        otpauth_uri = build_otpauth_uri(secret, user.get("email", ""), issuer="SysFix")
        return {
            "secret": secret,
            "otpauth_uri": otpauth_uri,
            "qr_svg_data_uri": generate_qr_data_uri(otpauth_uri),
            "mfa_enabled": bool(user.get("mfa_enabled", False)),
        }

    def confirm_mfa_setup(self, user_ctx: dict[str, Any], code: str) -> dict[str, Any]:
        user = self.user_store.get_user(user_ctx["uid"])
        if not user:
            raise LookupError("Usuário não encontrado.")
        pending = str(user.get("mfa_pending_secret") or "")
        if not pending:
            raise ValueError("Nenhuma configuração 2FA em andamento. Clique em Gerar QR Code primeiro.")
        if not verify_totp(pending, code):
            raise AuthenticationError("Código de confirmação inválido. Confira o horário do dispositivo e tente novamente.")

        recovery_codes = generate_recovery_codes(8)
        recovery_hashes = [hash_recovery_code(c) for c in recovery_codes]
        updated = self.user_store.update_user(
            user["uid"],
            {
                "mfa_enabled": True,
                "mfa_secret": pending,
                "mfa_pending_secret": "",
                "mfa_recovery_hashes": recovery_hashes,
                "mfa_ativado_em": _now_iso(),
            },
        ) or user

        return {
            "mfa_enabled": True,
            "recovery_codes": recovery_codes,
            "mfa_token": create_mfa_verified_token(updated) if self.backend == "firebase" else "",
            "user": sanitizar_usuario(updated),
        }

    def disable_mfa(self, user_ctx: dict[str, Any], code: str) -> dict[str, Any]:
        user = self.user_store.get_user(user_ctx["uid"])
        if not user:
            raise LookupError("Usuário não encontrado.")
        if not user.get("mfa_enabled"):
            return {"mfa_enabled": False, "user": sanitizar_usuario(user)}

        ok, _, _ = self._verify_user_mfa_code(user, code)
        if not ok:
            raise AuthenticationError("Informe um código TOTP atual ou código de recuperação válido para desativar o 2FA.")

        updated = self.user_store.update_user(
            user["uid"],
            {
                "mfa_enabled": False,
                "mfa_secret": "",
                "mfa_pending_secret": "",
                "mfa_recovery_hashes": [],
                "mfa_ativado_em": "",
            },
        ) or user
        return {"mfa_enabled": False, "user": sanitizar_usuario(updated)}

    def revoke_all_sessions(self, user_ctx: dict[str, Any]) -> dict[str, Any]:
        user = self.user_store.get_user(user_ctx["uid"])
        if not user:
            raise LookupError("Usuário não encontrado.")
        nova_versao = int(user.get("session_version") or 1) + 1
        updated = self.user_store.update_user(
            user["uid"], {"session_version": nova_versao}
        ) or user
        return {
            "session_version": nova_versao,
            "token": create_local_token(updated) if self.backend != "firebase" else "",
            "mfa_token": create_mfa_verified_token(updated)
            if (self.backend == "firebase" and updated.get("mfa_enabled"))
            else "",
            "user": sanitizar_usuario(updated),
        }

    def change_local_password(
        self, user_ctx: dict[str, Any], senha_atual: str, nova_senha: str
    ) -> dict[str, Any]:
        if self.backend == "firebase":
            raise ValueError("No modo nuvem, use a redefinição de senha por e-mail.")
        user = self.user_store.get_user(user_ctx["uid"])
        if not user:
            raise LookupError("Usuário não encontrado.")
        if not verify_password(senha_atual, str(user.get("password_hash") or "")):
            raise AuthenticationError("Senha atual incorreta.")
        nova_valida = _validar_senha(nova_senha)
        if senha_atual == nova_valida:
            raise ValueError("A nova senha deve ser diferente da senha atual.")

        nova_versao = int(user.get("session_version") or 1) + 1
        updated = self.user_store.update_user(
            user["uid"],
            {
                "password_hash": hash_password(nova_valida),
                "must_change_password": False,
                "password_changed_at": _now_iso(),
                "session_version": nova_versao,
            },
        ) or user
        return {
            "token": create_local_token(updated),
            "session_version": nova_versao,
            "user": sanitizar_usuario(updated),
        }

    def complete_first_login_password_change(
        self,
        user_ctx: dict[str, Any],
        nova_senha: str,
        confirmar_senha: str | None = None,
        client_ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        user = self.user_store.get_user(user_ctx["uid"])
        if not user:
            raise LookupError("Usuário não encontrado.")
        nova_valida = _validar_senha(nova_senha)
        if confirmar_senha is not None and nova_valida != confirmar_senha:
            raise ValueError("A confirmação da nova senha não confere.")

        if self.backend != "firebase":
            if verify_password(nova_valida, str(user.get("password_hash") or "")):
                raise ValueError("A nova senha deve ser diferente da senha temporária/atual.")

        nova_versao = int(user.get("session_version") or 1) + 1
        auditoria = append_login_audit(
            user.get("ultimos_acessos"),
            build_login_audit_entry(
                "Senha pessoal definida (1º acesso)",
                ip=client_ip,
                user_agent=user_agent,
                metodo=self.backend,
            ),
        )
        updates: dict[str, Any] = {
            "must_change_password": False,
            "password_changed_at": _now_iso(),
            "session_version": nova_versao,
            "ultimos_acessos": auditoria,
        }
        if self.backend == "firebase":
            self.firebase_auth.update_user_password(user["uid"], nova_valida)
        else:
            updates["password_hash"] = hash_password(nova_valida)

        updated = self.user_store.update_user(user["uid"], updates) or user
        tenant = self.user_store.get_tenant(user["tenant_id"])
        return {
            "sucesso": True,
            "token": create_local_token(updated) if self.backend != "firebase" else "",
            "mfa_token": create_mfa_verified_token(updated)
            if (self.backend == "firebase" and updated.get("mfa_enabled"))
            else "",
            "session_version": nova_versao,
            "user": sanitizar_usuario(updated),
            "tenant": sanitizar_tenant(tenant) if tenant else user_ctx.get("tenant"),
        }

    def create_team_member(
        self,
        admin_ctx: dict[str, Any],
        nome: str,
        email: str,
        senha: str,
        role: str = "tecnico",
        permissoes: dict[str, Any] | None = None,
        exigir_troca_senha: bool = True,
    ) -> dict[str, Any]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError("Você não tem permissão para adicionar membros.")

        nome_limpo = SecurityValidator.sanitizar_texto(nome)
        if not nome_limpo:
            raise ValueError("O nome do colaborador é obrigatório.")
        email_norm = _validar_email(email)
        senha_valida = _validar_senha(senha)
        role_valida = _validar_role(role)

        is_caller_admin = admin_ctx.get("role") == "admin"
        if role_valida == "admin" and not is_caller_admin:
            raise AuthorizationError("Apenas administradores podem criar outros administradores.")

        if role_valida == "admin":
            perms_finais = dict(ALL_PERMISSIONS_TRUE)
        else:
            perms_finais = normalizar_permissoes_input(permissoes)
            if not is_caller_admin:
                for k, v in perms_finais.items():
                    if v and not usuario_tem_permissao(admin_ctx, k):
                        raise AuthorizationError(
                            f"Você não pode conceder a permissão '{k}' sem possuí-la."
                        )

        if self.user_store.get_user_by_email(email_norm):
            raise ValueError("Já existe um usuário cadastrado com este e-mail.")

        tenant_id = admin_ctx["tenant_id"]
        user_record = {
            "email": email_norm,
            "nome": nome_limpo,
            "role": role_valida,
            "tenant_id": tenant_id,
            "ativo": True,
            "permissoes": perms_finais,
            "must_change_password": bool(exigir_troca_senha),
            "password_changed_at": "",
            "criado_em": _now_iso(),
        }

        if self.backend == "firebase":
            uid = self.firebase_auth.create_auth_user(
                email=email_norm, password=senha_valida, display_name=nome_limpo
            )
            user_record["uid"] = uid
        else:
            user_record["uid"] = f"usr_{uuid.uuid4().hex[:12]}"
            user_record["password_hash"] = hash_password(senha_valida)

        created = self.user_store.create_user(user_record)
        return sanitizar_usuario(created)

    def update_team_member(
        self,
        admin_ctx: dict[str, Any],
        target_uid: str,
        nome: str | None = None,
        role: str | None = None,
        ativo: bool | None = None,
        permissoes: dict[str, Any] | None = None,
        must_change_password: bool | None = None,
    ) -> dict[str, Any]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError("Você não tem permissão para editar membros.")

        target = self.user_store.get_user(target_uid)
        if not target or target.get("tenant_id") != admin_ctx["tenant_id"]:
            raise LookupError("Colaborador não encontrado nesta empresa.")

        is_caller_admin = admin_ctx.get("role") == "admin"
        if target.get("role") == "admin" and not is_caller_admin:
            raise AuthorizationError("Apenas administradores podem alterar outros administradores.")

        updates: dict[str, Any] = {}
        if nome is not None:
            nome_limpo = SecurityValidator.sanitizar_texto(nome)
            if not nome_limpo:
                raise ValueError("O nome do colaborador não pode ficar vazio.")
            updates["nome"] = nome_limpo

        if role is not None:
            nova_role = _validar_role(role)
            if nova_role == "admin" and not is_caller_admin:
                raise AuthorizationError("Apenas administradores podem promover usuários a administrador.")
            updates["role"] = nova_role

        if ativo is not None:
            updates["ativo"] = bool(ativo)

        if must_change_password is not None:
            updates["must_change_password"] = bool(must_change_password)

        novo_role = updates.get("role", target.get("role"))
        novo_ativo = updates.get("ativo", target.get("ativo", True))
        if target.get("role") == "admin" and (novo_role != "admin" or not novo_ativo):
            membros = self.user_store.list_users_by_tenant(admin_ctx["tenant_id"])
            outros_admins_ativos = [
                m
                for m in membros
                if m.get("uid") != target_uid
                and m.get("role") == "admin"
                and m.get("ativo", True)
            ]
            if not outros_admins_ativos:
                raise ValueError(
                    "A empresa precisa manter pelo menos um administrador ativo."
                )

        if novo_role == "admin":
            if role == "admin" or permissoes is not None:
                updates["permissoes"] = dict(ALL_PERMISSIONS_TRUE)
        elif permissoes is not None:
            perms_norm = normalizar_permissoes_input(permissoes)
            if not is_caller_admin:
                for k, v in perms_norm.items():
                    if v and not usuario_tem_permissao(admin_ctx, k):
                        raise AuthorizationError(
                            f"Você não pode conceder a permissão '{k}' sem possuí-la."
                        )
            updates["permissoes"] = perms_norm
        elif role is not None and novo_role == "tecnico":
            updates["permissoes"] = resolver_permissoes(
                "tecnico", target.get("permissoes")
            )

        if not updates:
            return sanitizar_usuario(target)

        updated = self.user_store.update_user(target_uid, updates)
        if self.backend == "firebase" and ativo is not None:
            self.firebase_auth.set_user_disabled(target_uid, disabled=not bool(ativo))
        return sanitizar_usuario(updated)

    def list_team_members(self, admin_ctx: dict[str, Any]) -> list[dict[str, Any]]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError("Você não tem permissão para visualizar a equipe.")
        membros = self.user_store.list_users_by_tenant(admin_ctx["tenant_id"])
        return [sanitizar_usuario(m) for m in membros]

    def update_tenant_settings(
        self,
        admin_ctx: dict[str, Any],
        nome: str | None = None,
        sla_dias: int | None = None,
        localizacoes: list[str] | None = None,
        setores_ativos: dict[str, Any] | None = None,
        exigir_mfa_equipe: bool | None = None,
        campos_bancada: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not usuario_tem_permissao(admin_ctx, "gerenciar_equipe"):
            raise AuthorizationError(
                "Você não tem permissão para alterar configurações da empresa."
            )

        updates: dict[str, Any] = {}
        if nome is not None:
            nome_limpo = SecurityValidator.sanitizar_texto(nome)
            if not nome_limpo:
                raise ValueError("O nome da empresa não pode ficar vazio.")
            updates["nome"] = nome_limpo

        if sla_dias is not None:
            if isinstance(sla_dias, bool) or not isinstance(sla_dias, int) or not (1 <= sla_dias <= 60):
                raise ValueError("O SLA em dias deve ser um número inteiro entre 1 e 60.")
            updates["sla_dias"] = sla_dias

        if localizacoes is not None:
            updates["localizacoes"] = _normalizar_localizacoes(localizacoes)

        if setores_ativos is not None:
            updates["setores_ativos"] = _normalizar_setores_ativos(setores_ativos)

        if exigir_mfa_equipe is not None:
            updates["exigir_mfa_equipe"] = bool(exigir_mfa_equipe)

        if campos_bancada is not None:
            updates["campos_bancada"] = _normalizar_campos_bancada(campos_bancada)

        if not updates:
            tenant = self.user_store.get_tenant(admin_ctx["tenant_id"])
            return sanitizar_tenant(tenant)

        updated = self.user_store.update_tenant(admin_ctx["tenant_id"], updates)
        if not updated:
            raise LookupError("Empresa não encontrada.")
        return sanitizar_tenant(updated)

    def update_tenant_branding_and_templates(
        self,
        user_ctx: dict[str, Any],
        cnpj: str | None = None,
        telefone: str | None = None,
        endereco: str | None = None,
        rodape_documento: str | None = None,
        cor_documento: str | None = None,
        logo_base64: str | None = None,
        checklist_padrao: list[str] | None = None,
        modelos_laudo: list[dict] | None = None,
    ) -> dict[str, Any]:
        if not (
            usuario_tem_permissao(user_ctx, "gerenciar_modelos")
            or usuario_tem_permissao(user_ctx, "gerenciar_equipe")
        ):
            raise AuthorizationError(
                "Você não tem permissão para personalizar modelos, checklists e timbre."
            )

        updates: dict[str, Any] = {}
        if cnpj is not None:
            updates["cnpj"] = SecurityValidator.sanitizar_texto(cnpj)[:32]
        if telefone is not None:
            updates["telefone"] = SecurityValidator.sanitizar_texto(telefone)[:40]
        if endereco is not None:
            updates["endereco"] = SecurityValidator.sanitizar_texto(endereco)[:200]
        if rodape_documento is not None:
            updates["rodape_documento"] = SecurityValidator.sanitizar_texto(rodape_documento)[:260]
        if cor_documento is not None:
            updates["cor_documento"] = validar_cor_hex(cor_documento)
        if logo_base64 is not None:
            updates["logo_base64"] = validar_imagem_base64(logo_base64)
        if checklist_padrao is not None:
            updates["checklist_padrao"] = normalizar_checklist_padrao(checklist_padrao)
        if modelos_laudo is not None:
            updates["modelos_laudo"] = normalizar_modelos_laudo(modelos_laudo)

        if not updates:
            tenant = self.user_store.get_tenant(user_ctx["tenant_id"])
            return sanitizar_tenant(tenant)

        updated = self.user_store.update_tenant(user_ctx["tenant_id"], updates)
        if not updated:
            raise LookupError("Empresa não encontrada.")
        return sanitizar_tenant(updated)

    def update_cyber_governance(
        self,
        admin_ctx: dict[str, Any],
        permitir_auditoria_rede: bool,
        escopo_autorizado: list[str] | None = None,
        observacao_termo: str = "",
        usuarios_autorizados: list[str] | None = None,
        restringir_usuarios: bool | None = None,
    ) -> dict[str, Any]:
        if admin_ctx.get("role") != "admin":
            raise AuthorizationError(
                "Apenas administradores/gestores podem definir quem está autorizado no Gov-Key."
            )

        escopo_limpo: list[str] = []
        vistos_escopo: set[str] = set()
        for item in escopo_autorizado or []:
            txt = SecurityValidator.sanitizar_texto(item)
            if txt and txt.lower() not in vistos_escopo:
                vistos_escopo.add(txt.lower())
                escopo_limpo.append(txt[:120])

        usuarios_limpos: list[str] = []
        vistos_usr: set[str] = set()
        for u in usuarios_autorizados or []:
            txt = SecurityValidator.sanitizar_texto(u).strip()
            chave = txt.lower()
            if txt and chave not in vistos_usr:
                vistos_usr.add(chave)
                usuarios_limpos.append(txt[:120])

        permitir = bool(permitir_auditoria_rede)
        restringir = (
            bool(restringir_usuarios)
            if restringir_usuarios is not None
            else bool(usuarios_limpos)
        )

        autor = admin_ctx.get("nome") or admin_ctx.get("email") or "Gestor"
        gov = {
            "permitir_auditoria_rede": permitir,
            "usuarios_autorizados": usuarios_limpos[:100],
            "restringir_usuarios": restringir,
            "escopo_autorizado": escopo_limpo[:30],
            "observacao_termo": SecurityValidator.sanitizar_texto(observacao_termo)[:300],
            "autorizado_por": SecurityValidator.sanitizar_texto(autor) if permitir else "",
            "autorizado_em": _now_iso() if permitir else "",
        }
        updated = self.user_store.update_tenant(
            admin_ctx["tenant_id"], {"cyber_governanca": gov}
        )
        if not updated:
            raise LookupError("Empresa não encontrada.")
        return sanitizar_tenant(updated)
