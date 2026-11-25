import base64
import binascii
from datetime import datetime
import html
import io
import json
import re
import unicodedata
from typing import Any

from PIL import Image

from security import SecurityValidator

MAX_TEMPLATE_UPLOAD_BYTES = 512 * 1024  # 512 KB
MAX_IMAGE_BYTES = 300 * 1024  # 300 KB

CATEGORIAS_EQUIPAMENTO = [
    "ONU / ONT (Fibra)",
    "Roteador / Wi-Fi",
    "Switch / OLT",
    "Nobreak / Fonte",
    "Notebook",
    "Desktop / PC",
    "Impressora",
    "Servidor / Rack",
    "CFTV / Câmera IP",
    "DVR / NVR",
    "Central de Alarme",
    "Controle de Acesso / Biometria",
    "Dispositivo IoT / Sensor",
    "Automação Predial / CLP",
    "Rádio / Antena PTP",
    "Outro",
]

TIPOS_FLUXO = [
    "Manutenção",
    "Comodato (Cliente)",
    "RMA / Garantia",
    "Recondicionamento / Triagem",
    "Preventiva",
]

PRIORIDADES = [
    "Normal",
    "Baixa",
    "Alta",
    "Crítica",
]

CONCLUSOES_LAUDO = [
    "Reparado / Aprovado",
    "Aguardando Aprovação de Orçamento",
    "Aguardando Peça / RMA",
    "Condenado / Inviável",
    "Sem Defeito Constatado",
    "Troca em Garantia / Comodato",
]

CHECKLIST_PRESETS: dict[str, dict[str, Any]] = {
    "isp": {
        "nome": "Provedor de Internet (ISP / FTTH)",
        "itens": [
            "Reset de fábrica e limpeza de configurações",
            "Medição de sinal óptico RX/TX (dBm) na porta PON",
            "Teste de portas LAN / WAN e negociação Gigabit",
            "Teste de sinal Wi-Fi 2.4 GHz e 5 GHz",
            "Firmware atualizado e homologado",
            "Fonte de alimentação original testada sob carga",
            "Higienização física e etiqueta de MAC/Serial conferida",
        ],
    },
    "ti": {
        "nome": "Assistência Técnica & TI Corporativa",
        "itens": [
            "Conferência de avarias físicas, parafusos e carregador",
            "Verificação de integridade de dados / backup preventivo",
            "Teste de saúde do SSD / HD (SMART)",
            "Teste de memória RAM e estresse térmico de CPU",
            "Limpeza interna, coolers e troca de pasta térmica",
            "Sistema operacional, drivers e rede validados",
            "Teste de carga e autonomia de bateria",
        ],
    },
    "padrao": {
        "nome": "Bancada Mista (TI + Provedores)",
        "itens": [
            "Inspeção física e conferência de fonte/acessórios",
            "Validação de Serial / MAC / Tombamento",
            "Teste funcional de conectividade e portas",
            "Limpeza preventiva e atualização de firmware/sistema",
            "Teste final de estabilidade antes da entrega",
        ],
    },
}

DEFAULT_LAUDO_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "tpl_inviabilidade",
        "nome": "Laudo de Inviabilidade / Condenação",
        "categoria_sugerida": "Geral",
        "titulo": "LAUDO TÉCNICO DE INVIABILIDADE E CONDENAÇÃO DE ATIVO",
        "conclusao_padrao": "Condenado / Inviável",
        "diagnostico": (
            "Equipamento {{equipamento}} (Tombamento {{tombamento}} | Serial/MAC: {{serial_mac}}) "
            "apresentou falha crítica compatível com o sintoma relatado ({{problema}})."
        ),
        "corpo": (
            "Após inspeção visual, medições elétricas em bancada e testes funcionais realizados pela equipe da "
            "{{empresa}}, constatou-se dano irreversível / desgaste avançado nos componentes principais do equipamento.\n\n"
            "Considerando o custo de reposição de peças, indisponibilidade operacional e valor residual do ativo, "
            "o reparo mostra-se técnica e economicamente inviável.\n\n"
            "Emitido em {{data_atual}} referente à O.S. #{{id}} ({{origem}})."
        ),
        "recomendacoes": (
            "Recomenda-se a baixa patrimonial / substituição definitiva do equipamento e descarte ecológico adequado."
        ),
    },
    {
        "id": "tpl_reparo_garantia",
        "nome": "Parecer Técnico de Reparo & Garantia",
        "categoria_sugerida": "TI / Corporativo",
        "titulo": "PARECER TÉCNICO DE MANUTENÇÃO E LIBERAÇÃO",
        "conclusao_padrao": "Reparado / Aprovado",
        "diagnostico": (
            "Atendimento realizado para o equipamento {{equipamento}} (Tombamento {{tombamento}}, Serial/MAC {{serial_mac}}), "
            "originário de {{origem}}, com relato inicial: {{problema}}."
        ),
        "corpo": (
            "Durante a análise em bancada ({{localizacao}}), foram executados os ensaios de diagnóstico, limpeza técnica "
            "e procedimentos corretivos descritos na resolução da O.S.: {{resolucao}}.\n\n"
            "O equipamento permaneceu em teste de estabilidade após a intervenção, apresentando funcionamento regular "
            "dentro dos parâmetros esperados pelo fabricante. Data da coleta: {{data_coleta}} | Data de entrega: {{data_entrega}}."
        ),
        "recomendacoes": (
            "Manter o equipamento em local ventilado, utilizar proteção contra surtos elétricos/nobreak e acionar a "
            "garantia do serviço ({{garantia_ate}}) mediante apresentação deste parecer caso ocorra reincidência."
        ),
    },
    {
        "id": "tpl_isp_ftth",
        "nome": "Laudo ISP / Comodato (ONU / Roteador)",
        "categoria_sugerida": "ONU / ONT (Fibra)",
        "titulo": "LAUDO DE TRIAGEM E CERTIFICAÇÃO DE EQUIPAMENTO DE REDE",
        "conclusao_padrao": "Reparado / Aprovado",
        "diagnostico": (
            "Triagem técnica executada no ativo de rede {{equipamento}} | Patrimônio {{tombamento}} | "
            "GPON SN / MAC: {{serial_mac}} | Fluxo: {{tipo_fluxo}}."
        ),
        "corpo": (
            "O equipamento passou por bateria de testes de bancada FTTH/Wi-Fi na {{empresa}}, incluindo verificação "
            "de fonte de alimentação, integridade da porta óptica/WAN, portas LAN Gigabit, estabilidade wireless em 2.4/5 GHz "
            "e regravação/validação de firmware homologado.\n\n"
            "Ocorrência original ({{origem}}): {{problema}}. Intervenção realizada: {{resolucao}}."
        ),
        "recomendacoes": (
            "Equipamento apto para retorno ao estoque de campo / reinstalação em comodato ou encaminhamento conforme conclusão acima."
        ),
    },
]

LAUDO_VARIABLES_HELP = [
    {"variavel": "{{empresa}}", "descricao": "Nome da empresa emissora"},
    {"variavel": "{{id}}", "descricao": "Número/ID da O.S."},
    {"variavel": "{{equipamento}}", "descricao": "Nome/modelo do equipamento"},
    {"variavel": "{{tombamento}}", "descricao": "Tombamento ou patrimônio"},
    {"variavel": "{{serial_mac}}", "descricao": "Número de série / MAC / GPON SN"},
    {"variavel": "{{categoria}}", "descricao": "Categoria TI/ISP"},
    {"variavel": "{{tipo_fluxo}}", "descricao": "Manutenção, Comodato, RMA..."},
    {"variavel": "{{origem}}", "descricao": "Cliente, setor ou POP"},
    {"variavel": "{{localizacao}}", "descricao": "Localização atual"},
    {"variavel": "{{problema}}", "descricao": "Defeito relatado"},
    {"variavel": "{{resolucao}}", "descricao": "Serviço executado"},
    {"variavel": "{{tecnico}}", "descricao": "Técnico responsável"},
    {"variavel": "{{data_coleta}}", "descricao": "Data de entrada (DD/MM/AAAA)"},
    {"variavel": "{{data_entrega}}", "descricao": "Data de entrega (DD/MM/AAAA)"},
    {"variavel": "{{garantia_ate}}", "descricao": "Validade da garantia"},
    {"variavel": "{{data_atual}}", "descricao": "Data de emissão do laudo"},
]

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def renderizar_template_laudo(texto: str, contexto: dict[str, Any]) -> str:
    if not texto:
        return ""

    def _subst(match: re.Match[str]) -> str:
        chave = match.group(1).strip().lower()
        val = contexto.get(chave)
        if val is None or str(val).strip() == "":
            return "-"
        return str(val).strip()

    return _PLACEHOLDER_RE.sub(_subst, str(texto))


def validar_cor_hex(cor: str | None) -> str:
    limpa = str(cor or "").strip()
    if not limpa:
        return "#4C1D95"
    if not limpa.startswith("#"):
        limpa = f"#{limpa}"
    if not _HEX_COLOR_RE.match(limpa):
        raise ValueError("Cor do documento inválida. Use código hexadecimal (#RRGGBB), ex.: #4C1D95.")
    return limpa.upper()


def validar_imagem_base64(
    data_url: str | None, max_bytes: int = MAX_IMAGE_BYTES
) -> str:
    """Valida data URL / base64 de imagem (PNG/JPEG/WebP) usando Pillow e normaliza para PNG base64."""
    bruto = str(data_url or "").strip()
    if not bruto:
        return ""

    b64_part = bruto.split(",", 1)[1].strip() if "," in bruto else bruto
    try:
        raw_bytes = base64.b64decode(b64_part, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Imagem em base64 inválida.") from exc

    if not raw_bytes:
        return ""
    if len(raw_bytes) > max_bytes:
        raise ValueError(
            f"A imagem excede o limite máximo permitido ({max_bytes // 1024} KB)."
        )

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            fmt = (img.format or "").upper()
            if fmt not in {"PNG", "JPEG", "JPG", "WEBP"}:
                raise ValueError("Formato de imagem não suportado. Envie PNG, JPEG ou WebP.")
            img_conv = img.convert("RGBA" if fmt in {"PNG", "WEBP"} else "RGB")
            img_conv.thumbnail((900, 450))
            out = io.BytesIO()
            img_conv.save(out, format="PNG", optimize=True)
            png_bytes = out.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Não foi possível decodificar a imagem enviada.") from exc

    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def normalizar_checklist_padrao(itens: list[str] | None) -> list[str]:
    if not itens:
        return list(CHECKLIST_PRESETS["padrao"]["itens"])
    limpos: list[str] = []
    vistos: set[str] = set()
    for item in itens:
        texto = SecurityValidator.sanitizar_texto(item)
        if texto and texto.lower() not in vistos:
            vistos.add(texto.lower())
            limpos.append(texto[:160])
    if not limpos:
        raise ValueError("Informe pelo menos um item válido no checklist.")
    return limpos[:30]


def normalizar_modelos_laudo(modelos: list[dict] | None) -> list[dict[str, Any]]:
    if not modelos:
        return [dict(tpl) for tpl in DEFAULT_LAUDO_TEMPLATES]
    if not isinstance(modelos, list):
        raise ValueError("A lista de modelos de laudo é inválida.")

    limpos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for idx, item in enumerate(modelos[:20], start=1):
        if not isinstance(item, dict):
            continue
        nome = SecurityValidator.sanitizar_texto(item.get("nome"))
        titulo = SecurityValidator.sanitizar_texto(item.get("titulo")) or nome.upper()
        corpo = str(item.get("corpo") or "").strip()
        if not nome or not corpo:
            raise ValueError(f"Modelo #{idx}: informe pelo menos o nome e o texto do parecer/laudo.")
        tpl_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(item.get("id") or f"tpl_{idx}")).strip("_")
        if not tpl_id or tpl_id in vistos:
            tpl_id = f"tpl_{idx}"
        vistos.add(tpl_id)
        conclusao = SecurityValidator.sanitizar_texto(item.get("conclusao_padrao"))
        if conclusao not in CONCLUSOES_LAUDO:
            conclusao = CONCLUSOES_LAUDO[0]
        limpos.append(
            {
                "id": tpl_id,
                "nome": nome[:100],
                "categoria_sugerida": SecurityValidator.sanitizar_texto(item.get("categoria_sugerida")) or "Geral",
                "titulo": titulo[:140],
                "conclusao_padrao": conclusao,
                "diagnostico": str(item.get("diagnostico") or "").strip()[:2000],
                "corpo": corpo[:8000],
                "recomendacoes": str(item.get("recomendacoes") or "").strip()[:2000],
            }
        )

    if not limpos:
        raise ValueError("Mantenha pelo menos um modelo de laudo cadastrado.")
    return limpos


def _decode_template_text(raw_bytes: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Não foi possível ler o conteúdo textual do modelo enviado.")


def _strip_html_for_template(raw_html: str) -> tuple[str, str]:
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    title_text = ""
    if title_match:
        title_text = SecurityValidator.sanitizar_texto(
            html.unescape(re.sub(r"<[^>]+>", " ", title_match.group(1)))
        )

    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<(br|hr|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = html.unescape(cleaned)
    linhas = [ln.strip() for ln in cleaned.splitlines()]
    texto = re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()
    return title_text, texto


def parse_uploaded_laudo_template(filename: str, raw_bytes: bytes) -> dict[str, Any]:
    """Lê um arquivo pronto de modelo de laudo (.json, .txt, .md, .html) e converte em modelo SysFix."""
    if not raw_bytes:
        raise ValueError("O arquivo de modelo enviado está vazio.")
    if len(raw_bytes) > MAX_TEMPLATE_UPLOAD_BYTES:
        raise ValueError("O modelo excede o limite de 512 KB.")

    nome_arq = (filename or "modelo.txt").strip()
    base_name = nome_arq.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = base_name.rsplit(".", 1)[0] if "." in base_name else base_name
    ext = base_name.rsplit(".", 1)[-1].lower() if "." in base_name else "txt"

    if ext not in {"json", "txt", "md", "html", "htm"}:
        raise ValueError("Formato inválido. Envie um modelo pronto em .txt, .md, .html ou .json.")

    texto = _decode_template_text(raw_bytes).strip()
    if not texto:
        raise ValueError("O arquivo de modelo não possui conteúdo.")

    if ext == "json":
        try:
            payload = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON do modelo inválido: {exc}") from exc
        if isinstance(payload, list) and payload:
            payload = payload[0]
        if not isinstance(payload, dict):
            raise ValueError("O JSON de modelo precisa ser um objeto com 'nome' e 'corpo'.")
        normalizado = normalizar_modelos_laudo(
            [
                {
                    "id": payload.get("id") or f"tpl_imp_{re.sub(r'[^a-zA-Z0-9]+', '_', stem.lower())[:20]}",
                    "nome": payload.get("nome") or stem,
                    "categoria_sugerida": payload.get("categoria_sugerida") or "Importado",
                    "titulo": payload.get("titulo") or (payload.get("nome") or stem).upper(),
                    "conclusao_padrao": payload.get("conclusao_padrao") or CONCLUSOES_LAUDO[0],
                    "diagnostico": payload.get("diagnostico") or "",
                    "corpo": payload.get("corpo") or payload.get("parecer") or payload.get("texto") or "",
                    "recomendacoes": payload.get("recomendacoes") or "",
                }
            ]
        )[0]
        return normalizado

    titulo_extraido = ""
    if ext in {"html", "htm"}:
        titulo_extraido, corpo_limpo = _strip_html_for_template(texto)
    else:
        linhas = [ln.rstrip() for ln in texto.splitlines()]
        if linhas and linhas[0].lstrip().startswith("#"):
            titulo_extraido = SecurityValidator.sanitizar_texto(linhas[0].lstrip("#").strip())
            linhas = linhas[1:]
        corpo_limpo = re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()

    if not corpo_limpo:
        raise ValueError("Não foi possível extrair texto do modelo enviado.")

    nome_modelo = SecurityValidator.sanitizar_texto(stem.replace("_", " ").replace("-", " ")) or "Modelo Importado"
    return normalizar_modelos_laudo(
        [
            {
                "id": f"tpl_imp_{re.sub(r'[^a-zA-Z0-9]+', '_', stem.lower())[:24]}",
                "nome": nome_modelo[:100],
                "categoria_sugerida": "Importado",
                "titulo": (titulo_extraido or nome_modelo).upper()[:140],
                "conclusao_padrao": CONCLUSOES_LAUDO[0],
                "diagnostico": "",
                "corpo": corpo_limpo[:8000],
                "recomendacoes": "",
            }
        ]
    )[0]


# ==============================================================================
# CONFIGURAÇÃO EDITÁVEL DE CAMPOS DA BANCADA TI & GERADOR DE RESUMO PARA O.S.
# ==============================================================================

TOMBAMENTO_TIPOS = ("tombamento", "sem_tombamento", "serial")
BANCADA_FIELD_SECTIONS = ("entrada", "item", "saida")
BANCADA_FIELD_TYPES = ("texto", "textarea", "numero", "selecao", "data", "tombamento")

DEFAULT_BANCADA_FIELDS: list[dict[str, Any]] = [
    {"id": "tecnico_coleta", "label": "Técnico de Coleta", "tipo": "texto", "secao": "entrada", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "Quem recolheu", "opcoes": []},
    {"id": "data_coleta", "label": "Data Coleta", "tipo": "data", "secao": "entrada", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "origem", "label": "Origem / Cliente", "tipo": "texto", "secao": "entrada", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "Ex: Setor / Assinante", "opcoes": []},
    {"id": "os_coleta", "label": "O.S. Coleta", "tipo": "texto", "secao": "entrada", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "Nº da O.S.", "opcoes": []},
    {"id": "localizacao", "label": "Localização Inicial", "tipo": "selecao", "secao": "entrada", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "tipo_fluxo", "label": "Fluxo (Provedor/TI)", "tipo": "selecao", "secao": "entrada", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "prioridade", "label": "Prioridade / SLA", "tipo": "selecao", "secao": "entrada", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "equipamento", "label": "Equipamento", "tipo": "texto", "secao": "item", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "Ex: ONU Huawei HG8145V5 / Notebook", "opcoes": []},
    {"id": "categoria", "label": "Categoria TI/ISP", "tipo": "selecao", "secao": "item", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "tombamento", "label": "Tombamento / Serial", "tipo": "tombamento", "secao": "item", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "Ex: 123456 ou S/N", "opcoes": []},
    {"id": "serial_mac", "label": "Serial / MAC / GPON SN", "tipo": "texto", "secao": "item", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "Ex: 48575443A1B2C3D4 ou AA:BB:CC:DD:EE:FF", "opcoes": []},
    {"id": "problema", "label": "Problema Relatado", "tipo": "textarea", "secao": "item", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "Descreva a falha...", "opcoes": []},
    {"id": "tecnico_entrega", "label": "Técnico de Saída", "tipo": "texto", "secao": "saida", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "Quem entregou", "opcoes": []},
    {"id": "os_entrega", "label": "O.S. de Saída", "tipo": "texto", "secao": "saida", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "Nº da O.S.", "opcoes": []},
    {"id": "data_entrega", "label": "Data de Saída", "tipo": "data", "secao": "saida", "ativo": True, "obrigatorio": True, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "laudado", "label": "Laudado?", "tipo": "selecao", "secao": "saida", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "status_custo", "label": "Status Custo & Valor", "tipo": "selecao", "secao": "saida", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "garantia_dias", "label": "Garantia do Serviço (dias)", "tipo": "numero", "secao": "saida", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "", "opcoes": []},
    {"id": "resolucao", "label": "Serviço / Resolução", "tipo": "textarea", "secao": "saida", "ativo": True, "obrigatorio": False, "sistema": True, "placeholder": "Detalhes do reparo efetuado...", "opcoes": []},
]

_DEFAULT_BANCADA_BY_ID: dict[str, dict[str, Any]] = {
    f["id"]: f for f in DEFAULT_BANCADA_FIELDS
}


def _slugificar_campo_id(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", norm.lower()).strip("_")
    return slug[:40]


def normalizar_config_campos_bancada(campos: list[dict] | None) -> list[dict[str, Any]]:
    """Normaliza a configuração de campos da Bancada TI mantendo integridade dos campos nativos."""
    if not campos:
        return [dict(item, opcoes=list(item.get("opcoes") or [])) for item in DEFAULT_BANCADA_FIELDS]
    if not isinstance(campos, list):
        raise ValueError("A configuração de campos da bancada deve ser uma lista.")

    resultado: list[dict[str, Any]] = []
    vistos: set[str] = set()

    for idx, raw in enumerate(campos[:50], start=1):
        if not isinstance(raw, dict):
            continue
        raw_id = _slugificar_campo_id(raw.get("id") or "")
        base_def = _DEFAULT_BANCADA_BY_ID.get(raw_id)
        is_sistema = base_def is not None

        label = SecurityValidator.sanitizar_texto(raw.get("label")) or (
            base_def["label"] if base_def else ""
        )
        if not label:
            raise ValueError(f"Campo #{idx}: informe o nome/rótulo do campo.")

        field_id = raw_id if is_sistema else (raw_id or _slugificar_campo_id(label) or f"campo_{idx}")
        if not is_sistema:
            base_slug = field_id
            sufixo = 2
            while field_id in vistos or field_id in _DEFAULT_BANCADA_BY_ID:
                field_id = f"{base_slug}_{sufixo}"
                sufixo += 1
        elif field_id in vistos:
            continue
        vistos.add(field_id)

        if is_sistema:
            tipo = base_def["tipo"]
            secao = base_def["secao"]
            ativo = True if field_id == "equipamento" else bool(raw.get("ativo", base_def["ativo"]))
            obrigatorio = (
                True
                if field_id == "equipamento"
                else (bool(raw.get("obrigatorio", base_def["obrigatorio"])) if ativo else False)
            )
            opcoes: list[str] = []
        else:
            tipo_raw = str(raw.get("tipo") or "texto").strip().lower()
            tipo = tipo_raw if tipo_raw in {"texto", "textarea", "numero", "selecao", "data"} else "texto"
            secao_raw = str(raw.get("secao") or "item").strip().lower()
            secao = secao_raw if secao_raw in BANCADA_FIELD_SECTIONS else "item"
            ativo = bool(raw.get("ativo", True))
            obrigatorio = bool(raw.get("obrigatorio", False)) if ativo else False
            raw_opts = raw.get("opcoes") or []
            if isinstance(raw_opts, str):
                raw_opts = [o.strip() for o in raw_opts.replace("\n", ",").split(",")]
            opcoes = []
            seen_opts: set[str] = set()
            if isinstance(raw_opts, list):
                for opt in raw_opts[:30]:
                    clean_opt = SecurityValidator.sanitizar_texto(opt)[:80]
                    if clean_opt and clean_opt.lower() not in seen_opts:
                        seen_opts.add(clean_opt.lower())
                        opcoes.append(clean_opt)
            if tipo == "selecao" and not opcoes:
                raise ValueError(f"Campo '{label}': informe pelo menos uma opção para lista de seleção.")

        resultado.append(
            {
                "id": field_id,
                "label": label[:80],
                "tipo": tipo,
                "secao": secao,
                "ativo": ativo,
                "obrigatorio": obrigatorio,
                "sistema": is_sistema,
                "placeholder": SecurityValidator.sanitizar_texto(raw.get("placeholder"))[:120],
                "opcoes": opcoes,
            }
        )

    for base in DEFAULT_BANCADA_FIELDS:
        if base["id"] not in vistos:
            resultado.append(dict(base, opcoes=list(base.get("opcoes") or [])))

    if not resultado:
        return [dict(item, opcoes=list(item.get("opcoes") or [])) for item in DEFAULT_BANCADA_FIELDS]
    return resultado


def obter_mapa_campos_bancada(config_campos: list[dict] | None) -> dict[str, dict[str, Any]]:
    norm = normalizar_config_campos_bancada(config_campos)
    return {item["id"]: item for item in norm}


def formatar_data_br_texto(valor: Any) -> str:
    raw = str(valor or "").strip()
    if not raw:
        return "---"
    chunk = raw.replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(chunk, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return raw


def formatar_identificacao_tombamento(tombamento: Any, tombamento_tipo: Any = "") -> tuple[str, str]:
    tipo = str(tombamento_tipo or "").strip().lower()
    valor = SecurityValidator.sanitizar_texto(tombamento)
    if tipo == "sem_tombamento" or (not tipo and valor.lower() in {"sem tombamento", "s/t", "sem_tombamento"}):
        return "Identificação", "Sem Tombamento"
    if tipo == "serial":
        return "Serial", valor or "---"
    return "Tombamento", valor or "---"


def _linhas_campos_customizados(
    registro: dict[str, Any],
    config_norm: list[dict[str, Any]],
    secoes: tuple[str, ...],
) -> list[str]:
    custom_vals = registro.get("campos_customizados")
    if not isinstance(custom_vals, dict) or not custom_vals:
        return []
    linhas: list[str] = []
    mapa_cfg = {c["id"]: c for c in config_norm}
    usados: set[str] = set()
    for cfg in config_norm:
        if cfg.get("sistema") or cfg.get("secao") not in secoes or not cfg.get("ativo", True):
            continue
        cid = cfg["id"]
        val = custom_vals.get(cid)
        if val not in (None, ""):
            usados.add(cid)
            val_fmt = formatar_data_br_texto(val) if cfg.get("tipo") == "data" else str(val)
            linhas.append(f"• {cfg['label']}: {val_fmt}")
    for cid, val in custom_vals.items():
        if cid in usados or val in (None, ""):
            continue
        cfg = mapa_cfg.get(cid)
        if cfg:
            if cfg.get("secao") not in secoes or not cfg.get("ativo", True):
                continue
            label = cfg["label"]
        elif "entrada" in secoes:
            label = cid.replace("_", " ").strip().title()
        else:
            continue
        linhas.append(f"• {label}: {val}")
    return linhas


def gerar_resumo_os_texto(
    registro: dict[str, Any],
    config_campos: list[dict] | None = None,
    incluir_custos: bool = True,
    empresa_nome: str = "SysFix",
) -> dict[str, str]:
    """Gera textos estruturados de Coleta, Entrega e Completo prontos para anexar em O.S."""
    config_norm = normalizar_config_campos_bancada(config_campos)
    mapa = {c["id"]: c for c in config_norm}

    def ativo(fid: str) -> bool:
        return bool(mapa.get(fid, {}).get("ativo", True))

    def rotulo(fid: str, default: str) -> str:
        return str(mapa.get(fid, {}).get("label") or default)

    reg_id = registro.get("id") or "---"
    os_col = registro.get("os_coleta") or "---"
    os_ent = registro.get("os_entrega") or os_col
    equip = registro.get("equipamento") or "---"
    ident_lbl, ident_val = formatar_identificacao_tombamento(
        registro.get("tombamento"), registro.get("tombamento_tipo")
    )

    coleta_linhas = [
        f"=== RESUMO DA COLETA / ENTRADA (O.S. {os_col} | Reg. #{reg_id}) ===",
        f"Empresa: {empresa_nome}",
        f"• {rotulo('equipamento', 'Equipamento')}: {equip}",
    ]
    if ativo("tombamento"):
        coleta_linhas.append(f"• {ident_lbl}: {ident_val}")
    if ativo("categoria") and registro.get("categoria"):
        coleta_linhas.append(f"• {rotulo('categoria', 'Categoria')}: {registro.get('categoria')}")
    if ativo("serial_mac") and registro.get("serial_mac"):
        coleta_linhas.append(f"• {rotulo('serial_mac', 'Serial / MAC / GPON SN')}: {registro.get('serial_mac')}")
    if ativo("data_coleta"):
        coleta_linhas.append(f"• {rotulo('data_coleta', 'Data Coleta')}: {formatar_data_br_texto(registro.get('data_coleta'))}")
    if ativo("tecnico_coleta") and (registro.get("tecnico_coleta") or registro.get("tecnico")):
        coleta_linhas.append(f"• {rotulo('tecnico_coleta', 'Técnico de Coleta')}: {registro.get('tecnico_coleta') or registro.get('tecnico')}")
    if ativo("origem") and registro.get("origem"):
        coleta_linhas.append(f"• {rotulo('origem', 'Origem / Cliente')}: {registro.get('origem')}")
    if ativo("os_coleta") and registro.get("os_coleta"):
        coleta_linhas.append(f"• {rotulo('os_coleta', 'O.S. Coleta')}: {registro.get('os_coleta')}")
    if ativo("localizacao") and registro.get("localizacao"):
        coleta_linhas.append(f"• {rotulo('localizacao', 'Localização')}: {registro.get('localizacao')}")
    if ativo("tipo_fluxo") and registro.get("tipo_fluxo"):
        coleta_linhas.append(f"• {rotulo('tipo_fluxo', 'Fluxo')}: {registro.get('tipo_fluxo')}")
    if ativo("prioridade") and registro.get("prioridade"):
        coleta_linhas.append(f"• {rotulo('prioridade', 'Prioridade')}: {registro.get('prioridade')}")
    if registro.get("codigo_acompanhamento"):
        coleta_linhas.append(f"• Cód. Acompanhamento: {registro.get('codigo_acompanhamento')}")
    coleta_linhas.extend(_linhas_campos_customizados(registro, config_norm, ("entrada", "item")))
    if ativo("problema"):
        prob = str(registro.get("problema") or "").strip() or "Nenhum detalhe relatado."
        coleta_linhas.append(f"• {rotulo('problema', 'Problema Relatado')}: {prob}")
    coleta_texto = "\n".join(coleta_linhas)

    status_atual = str(registro.get("status") or "Pendente").strip()
    is_entregue = status_atual.lower() in {"entregue", "finalizado"} or bool(str(registro.get("data_entrega") or "").strip())
    entrega_linhas = [
        f"=== RESUMO DA ENTREGA / SAÍDA (O.S. {os_ent} | Reg. #{reg_id}) ===",
        f"Status: {'ENTREGUE / FINALIZADO' if is_entregue else status_atual.upper()}",
        f"• {rotulo('equipamento', 'Equipamento')}: {equip}",
    ]
    if ativo("tombamento"):
        entrega_linhas.append(f"• {ident_lbl}: {ident_val}")
    if ativo("data_entrega"):
        entrega_linhas.append(
            f"• {rotulo('data_entrega', 'Data de Saída')}: "
            + (formatar_data_br_texto(registro.get("data_entrega")) if registro.get("data_entrega") else "Pendente")
        )
    if ativo("tecnico_entrega") and registro.get("tecnico_entrega"):
        entrega_linhas.append(f"• {rotulo('tecnico_entrega', 'Técnico de Saída')}: {registro.get('tecnico_entrega')}")
    if ativo("os_entrega") and registro.get("os_entrega"):
        entrega_linhas.append(f"• {rotulo('os_entrega', 'O.S. de Saída')}: {registro.get('os_entrega')}")
    if ativo("laudado") and registro.get("laudado"):
        entrega_linhas.append(f"• {rotulo('laudado', 'Laudado')}: {registro.get('laudado')}")
    if ativo("garantia_dias"):
        g_dias = int(registro.get("garantia_dias") or 0)
        if g_dias > 0:
            g_ate = formatar_data_br_texto(registro.get("garantia_ate")) if registro.get("garantia_ate") else ""
            entrega_linhas.append(
                f"• {rotulo('garantia_dias', 'Garantia')}: {g_dias} dias" + (f" (até {g_ate})" if g_ate and g_ate != "---" else "")
            )
        else:
            entrega_linhas.append(f"• {rotulo('garantia_dias', 'Garantia')}: Sem garantia")
    if incluir_custos and ativo("status_custo") and registro.get("status_custo"):
        sc = registro.get("status_custo")
        if sc == "Com Custo":
            val = float(registro.get("valor_custo") or registro.get("valor") or 0.0)
            entrega_linhas.append(f"• {rotulo('status_custo', 'Custo')}: Com Custo (R$ {val:.2f})".replace(".", ","))
        else:
            entrega_linhas.append(f"• {rotulo('status_custo', 'Custo')}: {sc}")
    entrega_linhas.extend(_linhas_campos_customizados(registro, config_norm, ("saida",)))
    if ativo("resolucao"):
        res_txt = str(registro.get("resolucao") or registro.get("servico_realizado") or "").strip() or "Pendente de atendimento."
        entrega_linhas.append(f"• {rotulo('resolucao', 'Serviço / Resolução')}: {res_txt}")
    assin = registro.get("assinatura_entrega")
    if isinstance(assin, dict) and assin.get("nome"):
        doc_txt = f" ({assin.get('documento')})" if assin.get("documento") else ""
        entrega_linhas.append(f"• Assinatura Digital: {assin.get('nome')}{doc_txt}")
    entrega_texto = "\n".join(entrega_linhas)

    checklist = registro.get("checklist") or []
    extras_checklist = ""
    if isinstance(checklist, list) and checklist:
        concl = sum(1 for c in checklist if isinstance(c, dict) and c.get("concluido"))
        extras_checklist = f"\n\n--- CHECKLIST DE BANCADA ({concl}/{len(checklist)} concluídos) ---\n" + "\n".join(
            f"[{'x' if c.get('concluido') else ' '}] {c.get('item')}"
            for c in checklist
            if isinstance(c, dict) and c.get("item")
        )

    completo_texto = f"{coleta_texto}\n\n{entrega_texto}{extras_checklist}"
    return {
        "coleta_texto": coleta_texto,
        "entrega_texto": entrega_texto,
        "completo_texto": completo_texto,
    }


def gerar_resumo_lote_coleta(
    registros: list[dict[str, Any]],
    config_campos: list[dict] | None = None,
    empresa_nome: str = "SysFix",
) -> str:
    """Combina resumos de coleta quando múltiplos equipamentos entram juntos."""
    partes = [
        gerar_resumo_os_texto(
            reg,
            config_campos=config_campos,
            incluir_custos=False,
            empresa_nome=empresa_nome,
        )["coleta_texto"]
        for reg in registros
    ]
    return "\n\n".join(partes)

