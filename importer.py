import csv
import html
import io
import json
import re
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree as ET

from security import SecurityValidator

MAX_IMPORT_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMPORT_ROWS = 5000

_FIELD_ALIASES = {
    "id": "id",
    "codigo": "id",
    "registro": "id",
    "equipamento": "equipamento",
    "aparelho": "equipamento",
    "dispositivo": "equipamento",
    "item": "equipamento",
    "modelo": "equipamento",
    "tombamento": "tombamento",
    "patrimonio": "tombamento",
    "serial": "tombamento",
    "numero_serie": "tombamento",
    "tag": "tombamento",
    "etiqueta": "tombamento",
    "tecnico_coleta": "tecnico_coleta",
    "tecnico": "tecnico_coleta",
    "tec_coleta": "tecnico_coleta",
    "tecnico_entrada": "tecnico_coleta",
    "responsavel_coleta": "tecnico_coleta",
    "data_coleta": "data_coleta",
    "data_entrada": "data_coleta",
    "data": "data_coleta",
    "dt_coleta": "data_coleta",
    "origem": "origem",
    "setor": "origem",
    "departamento": "origem",
    "cliente": "origem",
    "unidade": "origem",
    "local_origem": "origem",
    "os_coleta": "os_coleta",
    "os": "os_coleta",
    "os_entrada": "os_coleta",
    "ordem_servico": "os_coleta",
    "chamado": "os_coleta",
    "localizacao": "localizacao",
    "local": "localizacao",
    "bancada": "localizacao",
    "problema": "problema",
    "defeito": "problema",
    "falha": "problema",
    "descricao": "problema",
    "problema_relatado": "problema",
    "observacao": "problema",
    "status": "status",
    "situacao": "status",
    "estado": "status",
    "status_custo": "status_custo",
    "tipo_custo": "status_custo",
    "cobranca": "status_custo",
    "valor_custo": "valor_custo",
    "valor": "valor_custo",
    "custo": "valor_custo",
    "preco": "valor_custo",
    "valor_r": "valor_custo",
    "resolucao": "resolucao",
    "servico_realizado": "resolucao",
    "solucao": "resolucao",
    "servico": "resolucao",
    "tecnico_entrega": "tecnico_entrega",
    "tec_entrega": "tecnico_entrega",
    "tecnico_saida": "tecnico_entrega",
    "laudado": "laudado",
    "laudo": "laudado",
    "os_entrega": "os_entrega",
    "os_saida": "os_entrega",
    "data_entrega": "data_entrega",
    "data_saida": "data_entrega",
    "dt_entrega": "data_entrega",
    "categoria": "categoria",
    "tipo_equipamento": "categoria",
    "serial_mac": "serial_mac",
    "mac": "serial_mac",
    "mac_address": "serial_mac",
    "gpon": "serial_mac",
    "gpon_sn": "serial_mac",
    "sn": "serial_mac",
    "tipo_fluxo": "tipo_fluxo",
    "fluxo": "tipo_fluxo",
    "prioridade": "prioridade",
    "urgencia": "prioridade",
    "garantia_dias": "garantia_dias",
    "garantia": "garantia_dias",
}

EXPORT_COLUMNS = [
    "id",
    "equipamento",
    "categoria",
    "tombamento",
    "serial_mac",
    "tipo_fluxo",
    "prioridade",
    "tecnico_coleta",
    "data_coleta",
    "origem",
    "os_coleta",
    "localizacao",
    "problema",
    "status",
    "status_custo",
    "valor_custo",
    "resolucao",
    "tecnico_entrega",
    "laudado",
    "os_entrega",
    "data_entrega",
    "garantia_dias",
]


def _normalize_header(header: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(header or ""))
    sem_acentos = "".join(ch for ch in texto if not unicodedata.combining(ch))
    limpo = re.sub(r"[^a-zA-Z0-9]+", "_", sem_acentos.lower()).strip("_")
    return _FIELD_ALIASES.get(limpo, limpo)


def _decode_text(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Não foi possível decodificar o arquivo como texto UTF-8 ou Windows-1252.")


def _col_letters_to_index(cell_ref: str) -> int | None:
    match = re.match(r"^([A-Za-z]+)", cell_ref or "")
    if not match:
        return None
    letters = match.group(1).upper()
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _parse_json_bytes(raw_bytes: bytes) -> list[dict]:
    texto = _decode_text(raw_bytes)
    try:
        payload = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Arquivo JSON inválido: {exc}") from exc

    if isinstance(payload, dict) and isinstance(payload.get("coletas"), list):
        rows = payload["coletas"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError(
            "O JSON precisa ser uma lista de registros [...] ou um backup {'coletas': [...]}."
        )

    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Cada item do arquivo JSON deve ser um objeto com campos.")
    return rows


def _parse_csv_bytes(raw_bytes: bytes) -> list[dict]:
    texto = _decode_text(raw_bytes)
    linhas = [ln for ln in texto.splitlines() if ln.strip()]
    if not linhas:
        return []

    amostra = "\n".join(linhas[:5])
    delimiter = ";" if linhas[0].count(";") >= linhas[0].count(",") else ","
    try:
        dialect = csv.Sniffer().sniff(amostra, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        pass

    reader = csv.DictReader(io.StringIO(texto), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("O arquivo CSV não possui linha de cabeçalho.")
    return [dict(row) for row in reader]


def _parse_xlsx_bytes(raw_bytes: bytes) -> list[dict]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Arquivo .xlsx inválido ou corrompido.") from exc

    with zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.iter():
                if si.tag.endswith("}si") or si.tag == "si":
                    textos = [
                        node.text or ""
                        for node in si.iter()
                        if node.tag.endswith("}t") or node.tag == "t"
                    ]
                    shared_strings.append("".join(textos))

        sheet_names = [
            name
            for name in zf.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ]
        if not sheet_names:
            raise ValueError("Nenhuma planilha encontrada dentro do arquivo .xlsx.")
        target_sheet = (
            "xl/worksheets/sheet1.xml"
            if "xl/worksheets/sheet1.xml" in sheet_names
            else sorted(sheet_names)[0]
        )

        sheet_root = ET.fromstring(zf.read(target_sheet))
        raw_rows: list[list[str]] = []

        for row_el in sheet_root.iter():
            if not (row_el.tag.endswith("}row") or row_el.tag == "row"):
                continue
            row_map: dict[int, str] = {}
            next_col = 0

            for cell in row_el:
                if not (cell.tag.endswith("}c") or cell.tag == "c"):
                    continue
                ref = cell.attrib.get("r", "")
                col_idx = _col_letters_to_index(ref)
                if col_idx is None:
                    col_idx = next_col
                next_col = col_idx + 1

                cell_type = cell.attrib.get("t", "")
                val = ""
                if cell_type == "inlineStr":
                    val = "".join(
                        node.text or ""
                        for node in cell.iter()
                        if node.tag.endswith("}t") or node.tag == "t"
                    )
                else:
                    v_node = next(
                        (
                            child
                            for child in cell
                            if child.tag.endswith("}v") or child.tag == "v"
                        ),
                        None,
                    )
                    if v_node is not None and v_node.text is not None:
                        raw_val = v_node.text
                        if cell_type == "s":
                            try:
                                val = shared_strings[int(raw_val)]
                            except (ValueError, IndexError):
                                val = raw_val
                        elif cell_type == "b":
                            val = "Sim" if raw_val == "1" else "Não"
                        else:
                            val = raw_val

                row_map[col_idx] = val

            if row_map:
                max_col = max(row_map.keys())
                row_vals = [row_map.get(idx, "") for idx in range(max_col + 1)]
                if any(str(v).strip() for v in row_vals):
                    raw_rows.append(row_vals)

        if len(raw_rows) < 2:
            return []

        headers = [str(h).strip() for h in raw_rows[0]]
        records: list[dict] = []
        for row_vals in raw_rows[1:]:
            item = {}
            for idx, header in enumerate(headers):
                if header:
                    item[header] = row_vals[idx] if idx < len(row_vals) else ""
            records.append(item)
        return records


def parse_import_file(filename: str, raw_bytes: bytes) -> tuple[str, list[dict]]:
    if not raw_bytes:
        raise ValueError("O arquivo enviado está vazio.")
    if len(raw_bytes) > MAX_IMPORT_BYTES:
        raise ValueError("O arquivo excede o limite máximo permitido de 5 MB.")

    ext = (filename or "").strip().lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""

    if ext == "json":
        rows = _parse_json_bytes(raw_bytes)
        formato = "json"
    elif ext == "csv":
        rows = _parse_csv_bytes(raw_bytes)
        formato = "csv"
    elif ext == "xlsx":
        rows = _parse_xlsx_bytes(raw_bytes)
        formato = "xlsx"
    else:
        snippet = raw_bytes.lstrip()[:1]
        if snippet in (b"[", b"{"):
            rows = _parse_json_bytes(raw_bytes)
            formato = "json"
        else:
            raise ValueError(
                "Formato de arquivo não suportado. Envie um arquivo .json, .csv ou .xlsx."
            )

    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(
            f"O arquivo contém {len(rows)} linhas. O limite por importação é {MAX_IMPORT_ROWS} registros."
        )
    return formato, rows


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return SecurityValidator.sanitizar_texto(html.unescape(str(value)))


def _parse_import_date(value: Any, fallback: str = "") -> str:
    if value is None or str(value).strip() == "":
        return fallback
    raw = str(value).strip()
    # Serial numérico de data do Excel (ex.: 46320)
    try:
        num = float(raw)
        if 20000 <= num <= 80000:
            dt = datetime(1899, 12, 30) + timedelta(days=int(num))
            return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
    return SecurityValidator.validar_data(raw)


def normalizar_registros_importacao(
    raw_rows: list[dict],
    formato: str,
    tenant_id: str,
    actor: str = "",
    default_location: str = "Bancada TI",
) -> dict[str, Any]:
    registros: list[dict] = []
    erros: list[str] = []
    agora = datetime.now(timezone.utc).isoformat()
    autor = _clean_text(actor)
    hoje_iso = datetime.now().strftime("%Y-%m-%d")

    for linha_num, raw_row in enumerate(raw_rows, start=1):
        mapped = {}
        for key, val in raw_row.items():
            norm_key = _normalize_header(key)
            if norm_key and (norm_key not in mapped or mapped[norm_key] in (None, "")):
                mapped[norm_key] = val

        if not any(str(v).strip() for v in mapped.values() if v is not None):
            continue

        equipamento = _clean_text(mapped.get("equipamento"))
        if not equipamento:
            erros.append(f"Linha {linha_num}: campo 'equipamento' ausente ou vazio.")
            continue

        try:
            data_coleta = _parse_import_date(mapped.get("data_coleta"), fallback=hoje_iso)
            data_entrega = _parse_import_date(mapped.get("data_entrega"), fallback="")
        except ValueError as exc:
            erros.append(f"Linha {linha_num}: {exc}")
            continue

        try:
            valor_custo = SecurityValidator.validate_cost(mapped.get("valor_custo"))
        except ValueError as exc:
            erros.append(f"Linha {linha_num}: {exc}")
            continue

        status_custo_raw = _clean_text(mapped.get("status_custo")).lower()
        if status_custo_raw == "sem custo":
            status_custo = "Sem Custo"
            valor_custo = 0.0
        elif status_custo_raw == "com custo" or valor_custo > 0:
            status_custo = "Com Custo"
        else:
            status_custo = "Sem Custo"
            valor_custo = 0.0

        status_raw = _clean_text(mapped.get("status")).lower()
        if status_raw in {"entregue", "finalizado", "concluido", "concluído", "fechado"} or data_entrega:
            status_final = "Entregue"
        else:
            status_final = "Pendente"

        laudado_raw = _clean_text(mapped.get("laudado")).lower()
        laudado = "Sim" if laudado_raw in {"sim", "s", "true", "1", "yes"} else "Não"

        garantia_raw = _clean_text(mapped.get("garantia_dias"))
        try:
            garantia_dias = max(0, int(float(garantia_raw))) if garantia_raw else 0
        except ValueError:
            garantia_dias = 0

        record: dict[str, Any] = {
            "tenant_id": tenant_id,
            "equipamento": equipamento,
            "categoria": _clean_text(mapped.get("categoria")),
            "tombamento": _clean_text(mapped.get("tombamento")) or "S/N",
            "serial_mac": _clean_text(mapped.get("serial_mac")),
            "tipo_fluxo": _clean_text(mapped.get("tipo_fluxo")) or "Manutenção",
            "prioridade": _clean_text(mapped.get("prioridade")) or "Normal",
            "tecnico_coleta": _clean_text(mapped.get("tecnico_coleta")) or "Não informado",
            "data_coleta": data_coleta,
            "origem": _clean_text(mapped.get("origem")) or "Não informado",
            "os_coleta": _clean_text(mapped.get("os_coleta")),
            "localizacao": _clean_text(mapped.get("localizacao")) or default_location,
            "problema": _clean_text(mapped.get("problema")),
            "status": status_final,
            "status_custo": status_custo,
            "valor_custo": valor_custo,
            "resolucao": _clean_text(mapped.get("resolucao")),
            "tecnico_entrega": _clean_text(mapped.get("tecnico_entrega")),
            "laudado": laudado,
            "os_entrega": _clean_text(mapped.get("os_entrega")),
            "data_entrega": data_entrega,
            "garantia_dias": garantia_dias,
            "criado_em": agora,
            "criado_por": autor,
            "atualizado_em": agora,
            "atualizado_por": autor,
        }

        raw_id = mapped.get("id")
        if raw_id is not None and not isinstance(raw_id, bool) and str(raw_id).strip():
            try:
                parsed_id = int(str(raw_id).strip())
                if parsed_id >= 1:
                    record["id"] = parsed_id
            except ValueError:
                pass

        registros.append(record)

    return {
        "formato": formato,
        "total_lidos": len(raw_rows),
        "registros": registros,
        "erros": erros,
    }


def exportar_json(registros: list[dict], nome_empresa: str = "SysFix") -> bytes:
    limpos = []
    for row in sorted(registros, key=lambda item: item.get("id") or 0):
        item = {col: row.get(col, "") for col in EXPORT_COLUMNS}
        limpos.append(item)

    payload = {
        "empresa": nome_empresa,
        "exportado_em": datetime.now(timezone.utc).isoformat(),
        "total": len(limpos),
        "coletas": limpos,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def exportar_csv(registros: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=EXPORT_COLUMNS,
        delimiter=";",
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in sorted(registros, key=lambda item: item.get("id") or 0):
        writer.writerow({col: row.get(col, "") for col in EXPORT_COLUMNS})
    return buffer.getvalue().encode("utf-8-sig")


def gerar_modelo_importacao(formato: str = "csv") -> tuple[bytes, str, str]:
    exemplos = [
        {
            "id": 1,
            "equipamento": "Notebook Dell Vostro",
            "tombamento": "123456",
            "tecnico_coleta": "Carlos Silva",
            "data_coleta": "2026-10-15",
            "origem": "Financeiro",
            "os_coleta": "OS-1001",
            "localizacao": "Bancada TI",
            "problema": "Não liga ao pressionar o botão power",
            "status": "Pendente",
            "status_custo": "Sem Custo",
            "valor_custo": 0.0,
            "resolucao": "",
            "tecnico_entrega": "",
            "laudado": "Não",
            "os_entrega": "",
            "data_entrega": "",
        },
        {
            "id": 2,
            "equipamento": "Impressora Epson L6270",
            "tombamento": "40594",
            "tecnico_coleta": "Ana Souza",
            "data_coleta": "2026-10-10",
            "origem": "Recepção",
            "os_coleta": "OS-1002",
            "localizacao": "PlayLan",
            "problema": "Falha na tinta preta",
            "status": "Entregue",
            "status_custo": "Com Custo",
            "valor_custo": 120.0,
            "resolucao": "Limpeza da cabeça de impressão",
            "tecnico_entrega": "Ana Souza",
            "laudado": "Não",
            "os_entrega": "OS-1015",
            "data_entrega": "2026-10-14",
        },
    ]

    fmt = (formato or "csv").strip().lower()
    if fmt == "json":
        return (
            json.dumps(exemplos, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            "modelo_importacao_sysfix.json",
        )
    if fmt == "csv":
        return (
            exportar_csv(exemplos),
            "text/csv; charset=utf-8",
            "modelo_importacao_sysfix.csv",
        )
    raise ValueError("Formato de modelo inválido. Use 'csv' ou 'json'.")
