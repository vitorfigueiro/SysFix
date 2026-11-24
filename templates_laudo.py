import base64
import binascii
import html
import io
import json
import re
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
    "Câmera / DVR / NVR",
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
