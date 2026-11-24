from datetime import date, datetime, timedelta, timezone
import uuid

from security import SecurityValidator
from storage import _default_store, get_store
from templates_laudo import (
    CONCLUSOES_LAUDO,
    PRIORIDADES,
    TIPOS_FLUXO,
    validar_imagem_base64,
)

_initial_store = _default_store
_store = _default_store


def _get_active_store():
    if _store is not _initial_store:
        return _store
    return get_store()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_finalized(record: dict) -> bool:
    status = str(record.get("status") or "").strip().lower()
    return status in {"entregue", "finalizado"}


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    chunk = raw.replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(chunk, fmt).date()
        except ValueError:
            continue
    return None


def _date_matches(value, month: int, year: int) -> bool:
    parsed = _parse_date(value)
    return bool(parsed and parsed.month == month and parsed.year == year)


def _validar_registro_id(registro_id, msg_erro: str) -> int:
    if isinstance(registro_id, bool) or not isinstance(registro_id, int) or registro_id < 1:
        raise ValueError(msg_erro)
    return registro_id


def _normalizar_status_custo(status_custo) -> str:
    valor = SecurityValidator.sanitizar_texto(status_custo).lower()
    if not valor or valor == "sem custo":
        return "Sem Custo"
    if valor == "com custo":
        return "Com Custo"
    raise ValueError("Status de custo inválido. Use 'Sem Custo' ou 'Com Custo'.")


def _normalizar_laudado(laudado) -> str:
    valor = SecurityValidator.sanitizar_texto(laudado).lower()
    if not valor or valor in {"não", "nao"}:
        return "Não"
    if valor == "sim":
        return "Sim"
    raise ValueError("Campo 'Laudado' inválido. Use 'Sim' ou 'Não'.")


def _validar_localizacao_empresa(
    location: str | None, allowed_locations: list[str] | None = None
) -> str:
    if not allowed_locations:
        return SecurityValidator.validate_location(location)

    clean_loc = SecurityValidator.sanitizar_texto(location)
    if not clean_loc:
        return allowed_locations[0]

    mapa = {loc.lower(): loc for loc in allowed_locations}
    for legado in ("Bancada", "Sede", "Bancada TI"):
        mapa.setdefault(legado.lower(), legado)

    if clean_loc.lower() in mapa:
        return mapa[clean_loc.lower()]

    options_str = ", ".join(allowed_locations)
    raise ValueError(
        f"Localização inválida ('{clean_loc}'). Opções permitidas: {options_str}"
    )


def _extrair_custo(record: dict) -> float:
    raw = record.get("valor_custo")
    if raw is None or raw == "":
        raw = record.get("valor", 0.0)
    try:
        val = float(raw)
        return val if val >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _meses_anteriores(mes: int, ano: int, quantidade: int = 6) -> list[tuple[int, int]]:
    resultado: list[tuple[int, int]] = []
    cur_mes, cur_ano = mes, ano
    for _ in range(quantidade):
        resultado.append((cur_mes, cur_ano))
        cur_mes -= 1
        if cur_mes == 0:
            cur_mes = 12
            cur_ano -= 1
    resultado.reverse()
    return resultado


def _normalizar_prioridade(prioridade) -> str:
    val = SecurityValidator.sanitizar_texto(prioridade)
    if not val:
        return "Normal"
    mapa = {p.lower(): p for p in PRIORIDADES}
    mapa["critica"] = "Crítica"
    if val.lower() in mapa:
        return mapa[val.lower()]
    return val[:40]


def _normalizar_tipo_fluxo(tipo_fluxo) -> str:
    val = SecurityValidator.sanitizar_texto(tipo_fluxo)
    if not val:
        return "Manutenção"
    mapa = {t.lower(): t for t in TIPOS_FLUXO}
    mapa["manutencao"] = "Manutenção"
    if val.lower() in mapa:
        return mapa[val.lower()]
    return val[:60]


def _normalizar_garantia_dias(garantia_dias) -> int:
    if garantia_dias is None or garantia_dias == "":
        return 0
    if isinstance(garantia_dias, bool):
        raise ValueError("Garantia em dias inválida.")
    try:
        dias = int(garantia_dias)
    except (TypeError, ValueError) as exc:
        raise ValueError("Garantia em dias deve ser um número inteiro.") from exc
    if dias < 0 or dias > 3650:
        raise ValueError("A garantia deve estar entre 0 e 3650 dias.")
    return dias


def _normalizar_checklist(checklist, default_items: list[str] | None = None) -> list[dict]:
    if checklist is None:
        if default_items:
            return [{"item": SecurityValidator.sanitizar_texto(t), "concluido": False} for t in default_items if SecurityValidator.sanitizar_texto(t)]
        return []
    if not isinstance(checklist, list):
        raise ValueError("Formato inválido para checklist técnico.")
    limpos: list[dict] = []
    for entry in checklist[:40]:
        if isinstance(entry, str):
            texto = SecurityValidator.sanitizar_texto(entry)
            if texto:
                limpos.append({"item": texto[:160], "concluido": False})
        elif isinstance(entry, dict):
            texto = SecurityValidator.sanitizar_texto(entry.get("item") or entry.get("label") or entry.get("texto"))
            if texto:
                limpos.append({"item": texto[:160], "concluido": bool(entry.get("concluido"))})
    return limpos


def _novo_evento(tipo: str, descricao: str, actor: str = "", publico: bool = True) -> dict:
    return {
        "id": f"ev_{uuid.uuid4().hex[:8]}",
        "data_hora": _now_iso(),
        "tipo": tipo,
        "descricao": SecurityValidator.sanitizar_texto(descricao)[:300],
        "autor": SecurityValidator.sanitizar_texto(actor) or "Sistema",
        "publico": bool(publico),
    }


def _gerar_codigo_acompanhamento() -> str:
    return f"SF-{uuid.uuid4().hex[:8].upper()}"


class ColetaModel:
    @staticmethod
    def registrar_entrada(
        equipamento,
        tombamento,
        tecnico,
        data_coleta,
        origem,
        os_coleta,
        localizacao,
        problema,
        tenant_id: str = "default",
        actor: str = "",
        allowed_locations: list[str] | None = None,
        categoria: str = "",
        serial_mac: str = "",
        tipo_fluxo: str = "",
        prioridade: str = "",
        garantia_dias: int = 0,
        checklist: list | None = None,
    ):
        record = ColetaModel._preparar_entrada(
            equipamento,
            tombamento,
            tecnico,
            data_coleta,
            origem,
            os_coleta,
            localizacao,
            problema,
            tenant_id=tenant_id,
            actor=actor,
            allowed_locations=allowed_locations,
            categoria=categoria,
            serial_mac=serial_mac,
            tipo_fluxo=tipo_fluxo,
            prioridade=prioridade,
            garantia_dias=garantia_dias,
            checklist=checklist,
        )
        ids = _get_active_store().create_records([record], tenant_id=tenant_id)
        return ids[0]

    @staticmethod
    def _preparar_entrada(
        equipamento,
        tombamento,
        tecnico,
        data_coleta,
        origem,
        os_coleta,
        localizacao,
        problema,
        tenant_id: str = "default",
        actor: str = "",
        allowed_locations: list[str] | None = None,
        categoria: str = "",
        serial_mac: str = "",
        tipo_fluxo: str = "",
        prioridade: str = "",
        garantia_dias: int = 0,
        checklist: list | None = None,
    ) -> dict:
        if not data_coleta or not str(data_coleta).strip():
            raise ValueError("O campo 'Data de Coleta' é obrigatório.")
        agora = _now_iso()
        autor = SecurityValidator.sanitizar_texto(actor)
        loc_validada = _validar_localizacao_empresa(
            localizacao, allowed_locations=allowed_locations
        )
        fluxo_norm = _normalizar_tipo_fluxo(tipo_fluxo)
        prio_norm = _normalizar_prioridade(prioridade)
        record = {
            "equipamento": SecurityValidator.sanitizar_texto(equipamento),
            "tombamento": SecurityValidator.sanitizar_texto(tombamento),
            "tecnico_coleta": SecurityValidator.sanitizar_texto(tecnico),
            "data_coleta": SecurityValidator.validar_data(data_coleta),
            "origem": SecurityValidator.sanitizar_texto(origem),
            "os_coleta": SecurityValidator.sanitizar_texto(os_coleta),
            "localizacao": loc_validada,
            "problema": SecurityValidator.sanitizar_texto(problema),
            "categoria": SecurityValidator.sanitizar_texto(categoria)[:80],
            "serial_mac": SecurityValidator.sanitizar_texto(serial_mac)[:100],
            "tipo_fluxo": fluxo_norm,
            "prioridade": prio_norm,
            "garantia_dias": _normalizar_garantia_dias(garantia_dias),
            "garantia_ate": "",
        }
        campos = (
            ("equipamento", "Equipamento"),
            ("tombamento", "Tombamento"),
            ("tecnico_coleta", "Técnico de Coleta"),
            ("origem", "Origem"),
            ("os_coleta", "O.S. de Coleta"),
        )
        for field, label in campos:
            if not record[field]:
                raise ValueError(f"O campo '{label}' é obrigatório.")
        record.update(
            {
                "tenant_id": tenant_id,
                "status": "Pendente",
                "status_custo": "Sem Custo",
                "valor_custo": 0.0,
                "resolucao": "",
                "tecnico_entrega": "",
                "laudado": "Não",
                "os_entrega": "",
                "data_entrega": "",
                "codigo_acompanhamento": _gerar_codigo_acompanhamento(),
                "checklist": _normalizar_checklist(checklist),
                "laudos": [],
                "orcamento": None,
                "assinatura_entrega": None,
                "historico_eventos": [
                    _novo_evento(
                        "entrada",
                        f"Entrada registrada ({fluxo_norm}, prioridade {prio_norm}) na localização {loc_validada}.",
                        actor=autor or record["tecnico_coleta"],
                        publico=True,
                    )
                ],
                "criado_em": agora,
                "criado_por": autor,
                "atualizado_em": agora,
                "atualizado_por": autor,
            }
        )
        return record

    @staticmethod
    def registrar_entrada_em_lote(
        lista_coletas: list,
        tenant_id: str = "default",
        actor: str = "",
        allowed_locations: list[str] | None = None,
        default_checklist: list[str] | None = None,
    ) -> list:
        if not lista_coletas or not isinstance(lista_coletas, list):
            raise ValueError("Uma lista válida de registros deve ser fornecida.")

        records = []
        for index, item in enumerate(lista_coletas, start=1):
            try:
                item_checklist = item.get("checklist")
                if item_checklist is None and default_checklist:
                    item_checklist = _normalizar_checklist(None, default_items=default_checklist)
                records.append(
                    ColetaModel._preparar_entrada(
                        equipamento=item.get("equipamento"),
                        tombamento=item.get("tombamento"),
                        tecnico=item.get("tecnico") or item.get("tecnico_coleta"),
                        data_coleta=item.get("data_coleta"),
                        origem=item.get("origem"),
                        os_coleta=item.get("os_coleta"),
                        localizacao=item.get("localizacao"),
                        problema=item.get("problema"),
                        tenant_id=tenant_id,
                        actor=actor,
                        allowed_locations=allowed_locations,
                        categoria=item.get("categoria", ""),
                        serial_mac=item.get("serial_mac", ""),
                        tipo_fluxo=item.get("tipo_fluxo", ""),
                        prioridade=item.get("prioridade", ""),
                        garantia_dias=item.get("garantia_dias", 0) or 0,
                        checklist=item_checklist,
                    )
                )
            except ValueError as exc:
                raise ValueError(f"Item {index}: {exc}") from exc

        return _get_active_store().create_records(records, tenant_id=tenant_id)

    @staticmethod
    def registrar_saida(
        registro_id,
        tecnico_entrega,
        data_entrega,
        os_entrega,
        status_custo,
        valor_custo,
        resolucao,
        laudado,
        tenant_id: str = "default",
        actor: str = "",
        garantia_dias=None,
    ):
        registro_id = _validar_registro_id(
            registro_id, "Nenhum registro selecionado para atualizar saída."
        )

        tecnico = SecurityValidator.sanitizar_texto(tecnico_entrega)
        if not tecnico:
            raise ValueError("O campo 'Técnico de Entrega/Saída' é obrigatório.")
        ordem_servico = SecurityValidator.sanitizar_texto(os_entrega)
        if not ordem_servico:
            raise ValueError("O campo 'O.S. de Entrega/Saída' é obrigatório.")
        if not data_entrega or not str(data_entrega).strip():
            raise ValueError("O campo 'Data de Entrega/Saída' é obrigatório.")

        status_custo_norm = _normalizar_status_custo(status_custo)
        valor_custo_norm = (
            0.0
            if status_custo_norm == "Sem Custo"
            else SecurityValidator.validate_cost(valor_custo)
        )
        laudado_norm = _normalizar_laudado(laudado)
        data_entrega_iso = SecurityValidator.validar_data(data_entrega)

        store = _get_active_store()
        existente = store.get(registro_id, tenant_id=tenant_id)
        if not existente:
            return False

        dias_garantia = (
            _normalizar_garantia_dias(garantia_dias)
            if garantia_dias is not None
            else _normalizar_garantia_dias(existente.get("garantia_dias") or 0)
        )
        garantia_ate = ""
        if dias_garantia > 0:
            dt_ent = _parse_date(data_entrega_iso) or date.today()
            garantia_ate = (dt_ent + timedelta(days=dias_garantia)).strftime("%Y-%m-%d")

        eventos = list(existente.get("historico_eventos") or [])
        eventos.append(
            _novo_evento(
                "entrega",
                f"Equipamento entregue (O.S. {ordem_servico}) por {tecnico}."
                + (f" Garantia de {dias_garantia} dias." if dias_garantia > 0 else ""),
                actor=SecurityValidator.sanitizar_texto(actor) or tecnico,
                publico=True,
            )
        )

        atualizacoes = {
            "status": "Entregue",
            "tecnico_entrega": tecnico,
            "data_entrega": data_entrega_iso,
            "os_entrega": ordem_servico,
            "status_custo": status_custo_norm,
            "valor_custo": valor_custo_norm,
            "resolucao": SecurityValidator.sanitizar_texto(resolucao),
            "laudado": laudado_norm,
            "garantia_dias": dias_garantia,
            "garantia_ate": garantia_ate,
            "historico_eventos": eventos,
            "atualizado_em": _now_iso(),
            "atualizado_por": SecurityValidator.sanitizar_texto(actor),
        }
        if not existente.get("codigo_acompanhamento"):
            atualizacoes["codigo_acompanhamento"] = _gerar_codigo_acompanhamento()

        return store.update_record(registro_id, atualizacoes, tenant_id=tenant_id)

    @staticmethod
    def atualizar_entrada(
        registro_id,
        equipamento,
        tombamento,
        tecnico,
        data_coleta,
        origem,
        os_coleta,
        localizacao,
        problema,
        tenant_id: str = "default",
        actor: str = "",
        allowed_locations: list[str] | None = None,
        categoria: str | None = None,
        serial_mac: str | None = None,
        tipo_fluxo: str | None = None,
        prioridade: str | None = None,
        garantia_dias: int | None = None,
    ):
        registro_id = _validar_registro_id(
            registro_id, "ID de registro inválido para atualização."
        )

        store = _get_active_store()
        existente = store.get(registro_id, tenant_id=tenant_id)
        if not existente:
            return False

        atualizacoes = ColetaModel._preparar_entrada(
            equipamento,
            tombamento,
            tecnico,
            data_coleta,
            origem,
            os_coleta,
            localizacao,
            problema,
            tenant_id=tenant_id,
            actor=actor,
            allowed_locations=allowed_locations,
            categoria=existente.get("categoria", "") if categoria is None else categoria,
            serial_mac=existente.get("serial_mac", "") if serial_mac is None else serial_mac,
            tipo_fluxo=existente.get("tipo_fluxo", "") if tipo_fluxo is None else tipo_fluxo,
            prioridade=existente.get("prioridade", "") if prioridade is None else prioridade,
            garantia_dias=existente.get("garantia_dias", 0) if garantia_dias is None else garantia_dias,
        )
        for campo_fixo in (
            "status",
            "status_custo",
            "valor_custo",
            "resolucao",
            "tecnico_entrega",
            "laudado",
            "os_entrega",
            "data_entrega",
            "garantia_ate",
            "codigo_acompanhamento",
            "checklist",
            "laudos",
            "orcamento",
            "assinatura_entrega",
            "criado_em",
            "criado_por",
        ):
            atualizacoes.pop(campo_fixo, None)

        eventos = list(existente.get("historico_eventos") or [])
        nova_loc = atualizacoes.get("localizacao")
        antiga_loc = existente.get("localizacao")
        if nova_loc and antiga_loc and nova_loc != antiga_loc:
            desc_ev = f"Localização alterada de '{antiga_loc}' para '{nova_loc}'."
        else:
            desc_ev = "Dados técnicos/entrada da O.S. atualizados."
        eventos.append(
            _novo_evento(
                "atualizacao",
                desc_ev,
                actor=SecurityValidator.sanitizar_texto(actor) or tecnico,
                publico=True,
            )
        )
        atualizacoes["historico_eventos"] = eventos

        if not existente.get("codigo_acompanhamento"):
            atualizacoes["codigo_acompanhamento"] = _gerar_codigo_acompanhamento()

        return store.update_record(registro_id, atualizacoes, tenant_id=tenant_id)

    @staticmethod
    def excluir(registro_id, is_admin: bool = False, tenant_id: str = "default"):
        if not is_admin:
            raise PermissionError(
                "Acesso negado: Apenas administradores possuem permissão para excluir registros."
            )
        registro_id = _validar_registro_id(
            registro_id, "ID de registro inválido para exclusão."
        )

        return _get_active_store().delete_record(registro_id, tenant_id=tenant_id)

    @staticmethod
    def buscar_nao_finalizados_mes_atual(tenant_id: str = "default"):
        return sorted(
            (
                row
                for row in _get_active_store().all(tenant_id=tenant_id)
                if not _is_finalized(row)
            ),
            key=lambda row: row.get("id", 0),
            reverse=True,
        )

    @staticmethod
    def buscar_finalizados_mes_atual(tenant_id: str = "default"):
        return sorted(
            (
                row
                for row in _get_active_store().all(tenant_id=tenant_id)
                if _is_finalized(row)
            ),
            key=lambda row: row.get("id", 0),
            reverse=True,
        )

    @staticmethod
    def buscar_todos(tenant_id: str = "default"):
        return sorted(
            _get_active_store().all(tenant_id=tenant_id),
            key=lambda row: row.get("id", 0),
            reverse=True,
        )

    @staticmethod
    def buscar_por_mes_ano(mes: int, ano: int, tenant_id: str = "default"):
        return sorted(
            (
                row
                for row in _get_active_store().all(tenant_id=tenant_id)
                if _date_matches(row.get("data_coleta"), mes, ano)
                or _date_matches(row.get("data_entrega"), mes, ano)
            ),
            key=lambda row: row.get("id", 0),
            reverse=True,
        )

    @staticmethod
    def buscar_por_id(registro_id, tenant_id: str = "default"):
        if isinstance(registro_id, bool) or not isinstance(registro_id, int) or registro_id < 1:
            return None
        return _get_active_store().get(registro_id, tenant_id=tenant_id)

    @staticmethod
    def importar_registros(
        registros: list[dict],
        tenant_id: str = "default",
        modo: str = "anexar",
    ) -> dict:
        if not registros:
            raise ValueError("Nenhum registro válido para importar.")

        modo_norm = str(modo or "anexar").strip().lower()
        if modo_norm == "anexar":
            payload = []
            for row in registros:
                item = dict(row)
                item.pop("id", None)
                item["tenant_id"] = tenant_id
                payload.append(item)
            ids = _get_active_store().create_records(payload, tenant_id=tenant_id)
            return {"importados": len(ids), "modo": "anexar"}

        if modo_norm == "preservar_ids":
            vistos: set[int] = set()
            payload = []
            for idx, row in enumerate(registros, start=1):
                item = dict(row)
                item["tenant_id"] = tenant_id
                rid = item.get("id")
                if isinstance(rid, bool) or not isinstance(rid, int) or rid < 1:
                    raise ValueError(
                        f"Registro {idx}: para preservar IDs originais, todos os itens precisam ter 'id' numérico >= 1."
                    )
                if rid in vistos:
                    raise ValueError(f"ID duplicado no arquivo importado: {rid}.")
                vistos.add(rid)
                payload.append(item)
            total = _get_active_store().import_records(payload, tenant_id=tenant_id)
            return {"importados": total, "modo": "preservar_ids"}

        raise ValueError("Modo de importação inválido. Use 'anexar' ou 'preservar_ids'.")

    @staticmethod
    def garantir_codigo_acompanhamento(registro_id: int, tenant_id: str = "default") -> str | None:
        registro_id = _validar_registro_id(registro_id, "ID de registro inválido.")
        store = _get_active_store()
        reg = store.get(registro_id, tenant_id=tenant_id)
        if not reg:
            return None
        codigo = str(reg.get("codigo_acompanhamento") or "").strip().upper()
        if codigo:
            return codigo
        codigo = _gerar_codigo_acompanhamento()
        store.update_record(
            registro_id,
            {"codigo_acompanhamento": codigo, "atualizado_em": _now_iso()},
            tenant_id=tenant_id,
        )
        return codigo

    @staticmethod
    def buscar_por_codigo_publico(codigo: str) -> tuple[str, dict] | None:
        return _get_active_store().find_by_tracking_code(codigo)

    @staticmethod
    def adicionar_evento_externo(
        registro_id: int,
        tenant_id: str,
        tipo: str,
        descricao: str,
        actor: str = "",
        publico: bool = False,
    ) -> bool:
        store = _get_active_store()
        reg = store.get(registro_id, tenant_id=tenant_id)
        if not reg:
            return False
        eventos = list(reg.get("historico_eventos") or [])
        eventos.append(_novo_evento(tipo, descricao, actor=actor, publico=publico))
        return store.update_record(
            registro_id,
            {"historico_eventos": eventos, "atualizado_em": _now_iso()},
            tenant_id=tenant_id,
        )

    @staticmethod
    def atualizar_checklist(
        registro_id: int,
        checklist: list,
        tenant_id: str = "default",
        actor: str = "",
    ) -> dict | None:
        registro_id = _validar_registro_id(registro_id, "ID de registro inválido.")
        store = _get_active_store()
        reg = store.get(registro_id, tenant_id=tenant_id)
        if not reg:
            return None
        itens_norm = _normalizar_checklist(checklist)
        concluidos = sum(1 for item in itens_norm if item["concluido"])
        eventos = list(reg.get("historico_eventos") or [])
        eventos.append(
            _novo_evento(
                "checklist",
                f"Checklist técnico atualizado ({concluidos}/{len(itens_norm)} item(ns) concluído(s)).",
                actor=actor,
                publico=True,
            )
        )
        store.update_record(
            registro_id,
            {
                "checklist": itens_norm,
                "historico_eventos": eventos,
                "atualizado_em": _now_iso(),
                "atualizado_por": SecurityValidator.sanitizar_texto(actor),
            },
            tenant_id=tenant_id,
        )
        return store.get(registro_id, tenant_id=tenant_id)

    @staticmethod
    def salvar_laudo(
        registro_id: int,
        dados_laudo: dict,
        tenant_id: str = "default",
        actor: str = "",
        laudo_id: str | None = None,
    ) -> tuple[dict, dict] | None:
        registro_id = _validar_registro_id(registro_id, "ID de registro inválido.")
        store = _get_active_store()
        reg = store.get(registro_id, tenant_id=tenant_id)
        if not reg:
            return None

        titulo = SecurityValidator.sanitizar_texto(dados_laudo.get("titulo")) or "LAUDO TÉCNICO"
        conclusao = SecurityValidator.sanitizar_texto(dados_laudo.get("conclusao"))
        if conclusao not in CONCLUSOES_LAUDO:
            conclusao = CONCLUSOES_LAUDO[0]
        diagnostico = str(dados_laudo.get("diagnostico") or "").strip()[:3000]
        parecer = str(dados_laudo.get("parecer") or "").strip()[:10000]
        if not parecer:
            raise ValueError("O texto do parecer / análise técnica do laudo é obrigatório.")

        recomendacoes = str(dados_laudo.get("recomendacoes") or "").strip()[:3000]
        tecnico_resp = (
            SecurityValidator.sanitizar_texto(dados_laudo.get("tecnico_responsavel"))
            or SecurityValidator.sanitizar_texto(actor)
            or "Técnico Responsável"
        )
        registro_prof = SecurityValidator.sanitizar_texto(dados_laudo.get("registro_profissional"))[:60]
        incluir_checklist = bool(dados_laudo.get("incluir_checklist", True))
        visivel_cliente = bool(dados_laudo.get("visivel_cliente", True))
        modelo_origem = SecurityValidator.sanitizar_texto(dados_laudo.get("modelo_nome"))[:100]

        agora = _now_iso()
        laudos = list(reg.get("laudos") or [])
        laudo_salvo = None

        if laudo_id:
            for idx, item in enumerate(laudos):
                if item.get("id") == laudo_id:
                    laudo_salvo = {
                        **item,
                        "titulo": titulo[:140],
                        "conclusao": conclusao,
                        "diagnostico": diagnostico,
                        "parecer": parecer,
                        "recomendacoes": recomendacoes,
                        "tecnico_responsavel": tecnico_resp,
                        "registro_profissional": registro_prof,
                        "incluir_checklist": incluir_checklist,
                        "visivel_cliente": visivel_cliente,
                        "modelo_nome": modelo_origem or item.get("modelo_nome", ""),
                        "atualizado_em": agora,
                        "atualizado_por": SecurityValidator.sanitizar_texto(actor),
                    }
                    laudos[idx] = laudo_salvo
                    break
            if laudo_salvo is None:
                raise LookupError("Laudo não encontrado neste equipamento.")
            desc_ev = f"Laudo técnico {laudo_salvo['numero']} atualizado ({conclusao})."
        else:
            seq = len(laudos) + 1
            numero = f"LT-{registro_id:04d}-{seq}"
            laudo_salvo = {
                "id": f"ld_{uuid.uuid4().hex[:10]}",
                "numero": numero,
                "titulo": titulo[:140],
                "conclusao": conclusao,
                "diagnostico": diagnostico,
                "parecer": parecer,
                "recomendacoes": recomendacoes,
                "tecnico_responsavel": tecnico_resp,
                "registro_profissional": registro_prof,
                "incluir_checklist": incluir_checklist,
                "visivel_cliente": visivel_cliente,
                "modelo_nome": modelo_origem,
                "emitido_em": agora,
                "atualizado_em": agora,
                "criado_por": SecurityValidator.sanitizar_texto(actor),
            }
            laudos.append(laudo_salvo)
            desc_ev = f"Laudo técnico {numero} emitido e anexado ({conclusao})."

        eventos = list(reg.get("historico_eventos") or [])
        eventos.append(_novo_evento("laudo", desc_ev, actor=actor or tecnico_resp, publico=visivel_cliente))

        updates = {
            "laudos": laudos,
            "laudado": "Sim",
            "historico_eventos": eventos,
            "atualizado_em": agora,
            "atualizado_por": SecurityValidator.sanitizar_texto(actor),
        }
        if not reg.get("codigo_acompanhamento"):
            updates["codigo_acompanhamento"] = _gerar_codigo_acompanhamento()

        store.update_record(registro_id, updates, tenant_id=tenant_id)
        return store.get(registro_id, tenant_id=tenant_id), laudo_salvo

    @staticmethod
    def excluir_laudo(
        registro_id: int,
        laudo_id: str,
        tenant_id: str = "default",
        actor: str = "",
    ) -> dict | None:
        registro_id = _validar_registro_id(registro_id, "ID de registro inválido.")
        store = _get_active_store()
        reg = store.get(registro_id, tenant_id=tenant_id)
        if not reg:
            return None

        laudos = list(reg.get("laudos") or [])
        restantes = [item for item in laudos if item.get("id") != laudo_id]
        if len(restantes) == len(laudos):
            raise LookupError("Laudo não encontrado neste equipamento.")

        eventos = list(reg.get("historico_eventos") or [])
        eventos.append(
            _novo_evento("laudo", "Um laudo técnico anexado foi removido.", actor=actor, publico=False)
        )
        store.update_record(
            registro_id,
            {
                "laudos": restantes,
                "laudado": "Sim" if restantes else "Não",
                "historico_eventos": eventos,
                "atualizado_em": _now_iso(),
                "atualizado_por": SecurityValidator.sanitizar_texto(actor),
            },
            tenant_id=tenant_id,
        )
        return store.get(registro_id, tenant_id=tenant_id)

    @staticmethod
    def salvar_orcamento(
        registro_id: int,
        descricao: str,
        valor: float | str,
        tenant_id: str = "default",
        actor: str = "",
    ) -> dict | None:
        registro_id = _validar_registro_id(registro_id, "ID de registro inválido.")
        store = _get_active_store()
        reg = store.get(registro_id, tenant_id=tenant_id)
        if not reg:
            return None

        desc = SecurityValidator.sanitizar_texto(descricao)
        if not desc:
            raise ValueError("Informe a descrição dos serviços/peças do orçamento.")
        valor_norm = SecurityValidator.validate_cost(valor)
        if valor_norm <= 0:
            raise ValueError("O valor do orçamento deve ser maior que zero.")

        agora = _now_iso()
        orcamento = {
            "status": "Aguardando Aprovação",
            "descricao": desc[:1000],
            "valor": valor_norm,
            "enviado_em": agora,
            "enviado_por": SecurityValidator.sanitizar_texto(actor),
            "respondido_em": "",
            "respondido_por": "",
            "observacao_cliente": "",
        }
        eventos = list(reg.get("historico_eventos") or [])
        eventos.append(
            _novo_evento(
                "orcamento",
                f"Orçamento enviado para aprovação do cliente (R$ {valor_norm:,.2f}).",
                actor=actor,
                publico=True,
            )
        )
        updates = {
            "orcamento": orcamento,
            "status_custo": "Com Custo",
            "valor_custo": valor_norm,
            "historico_eventos": eventos,
            "atualizado_em": agora,
            "atualizado_por": SecurityValidator.sanitizar_texto(actor),
        }
        if not reg.get("codigo_acompanhamento"):
            updates["codigo_acompanhamento"] = _gerar_codigo_acompanhamento()
        store.update_record(registro_id, updates, tenant_id=tenant_id)
        return store.get(registro_id, tenant_id=tenant_id)

    @staticmethod
    def responder_orcamento_publico(
        codigo: str,
        decisao: str,
        cliente_nome: str,
        observacao: str = "",
    ) -> tuple[str, dict] | None:
        encontrado = _get_active_store().find_by_tracking_code(codigo)
        if not encontrado:
            return None
        tenant_id, reg = encontrado
        orc = reg.get("orcamento")
        if not isinstance(orc, dict) or not orc.get("valor"):
            raise ValueError("Esta O.S. não possui um orçamento aberto para aprovação.")

        dec_norm = SecurityValidator.sanitizar_texto(decisao).lower()
        if dec_norm in {"aprovar", "aprovado", "sim"}:
            novo_status = "Aprovado"
        elif dec_norm in {"recusar", "recusado", "reprovar", "nao", "não"}:
            novo_status = "Recusado"
        else:
            raise ValueError("Decisão inválida. Informe 'aprovar' ou 'recusar'.")

        nome = SecurityValidator.sanitizar_texto(cliente_nome)
        if not nome:
            raise ValueError("Informe seu nome para registrar a confirmação.")
        obs = SecurityValidator.sanitizar_texto(observacao)[:300]

        agora = _now_iso()
        orc_atualizado = {
            **orc,
            "status": novo_status,
            "respondido_em": agora,
            "respondido_por": nome,
            "observacao_cliente": obs,
        }
        eventos = list(reg.get("historico_eventos") or [])
        eventos.append(
            _novo_evento(
                "orcamento",
                f"Orçamento {novo_status.lower()} pelo cliente ({nome})."
                + (f" Obs: {obs}" if obs else ""),
                actor=nome,
                publico=True,
            )
        )
        _get_active_store().update_record(
            reg["id"],
            {
                "orcamento": orc_atualizado,
                "historico_eventos": eventos,
                "atualizado_em": agora,
                "atualizado_por": nome,
            },
            tenant_id=tenant_id,
        )
        return tenant_id, _get_active_store().get(reg["id"], tenant_id=tenant_id)

    @staticmethod
    def salvar_assinatura(
        registro_id: int,
        nome: str,
        documento: str,
        imagem_base64: str,
        tenant_id: str = "default",
        actor: str = "",
    ) -> dict | None:
        registro_id = _validar_registro_id(registro_id, "ID de registro inválido.")
        store = _get_active_store()
        reg = store.get(registro_id, tenant_id=tenant_id)
        if not reg:
            return None

        nome_limpo = SecurityValidator.sanitizar_texto(nome)
        if not nome_limpo:
            raise ValueError("Informe o nome do recebedor / assinante.")
        doc_limpo = SecurityValidator.sanitizar_texto(documento)[:40]
        img_validada = validar_imagem_base64(imagem_base64)
        if not img_validada:
            raise ValueError("Desenhe a assinatura antes de salvar.")

        agora = _now_iso()
        assinatura = {
            "nome": nome_limpo[:120],
            "documento": doc_limpo,
            "imagem_base64": img_validada,
            "assinado_em": agora,
            "coletado_por": SecurityValidator.sanitizar_texto(actor),
        }
        eventos = list(reg.get("historico_eventos") or [])
        eventos.append(
            _novo_evento(
                "assinatura",
                f"Termo/comprovante assinado digitalmente por {nome_limpo}.",
                actor=actor or nome_limpo,
                publico=True,
            )
        )
        store.update_record(
            registro_id,
            {
                "assinatura_entrega": assinatura,
                "historico_eventos": eventos,
                "atualizado_em": agora,
                "atualizado_por": SecurityValidator.sanitizar_texto(actor),
            },
            tenant_id=tenant_id,
        )
        return store.get(registro_id, tenant_id=tenant_id)

    @staticmethod
    def obter_historico_ativo(registro_id: int, tenant_id: str = "default") -> dict | None:
        registro_id = _validar_registro_id(registro_id, "ID de registro inválido.")
        store = _get_active_store()
        atual = store.get(registro_id, tenant_id=tenant_id)
        if not atual:
            return None

        ignorar = {"", "-", "---", "s/n", "n/a", "nao informado", "não informado"}
        tomb_norm = SecurityValidator.sanitizar_texto(atual.get("tombamento")).lower()
        serial_norm = SecurityValidator.sanitizar_texto(atual.get("serial_mac")).lower()

        todos = store.all(tenant_id=tenant_id)
        relacionados = []
        for row in todos:
            if row.get("id") == registro_id:
                relacionados.append(row)
                continue
            r_tomb = SecurityValidator.sanitizar_texto(row.get("tombamento")).lower()
            r_serial = SecurityValidator.sanitizar_texto(row.get("serial_mac")).lower()
            mesmo_tomb = tomb_norm not in ignorar and r_tomb == tomb_norm
            mesmo_serial = serial_norm not in ignorar and r_serial == serial_norm
            if mesmo_tomb or mesmo_serial:
                relacionados.append(row)

        relacionados.sort(key=lambda r: r.get("id") or 0, reverse=True)
        custo_acumulado = round(sum(_extrair_custo(r) for r in relacionados), 2)
        total_passagens = len(relacionados)

        return {
            "registro_atual": atual,
            "total_passagens": total_passagens,
            "reincidente": total_passagens > 1,
            "custo_acumulado": custo_acumulado,
            "registros": relacionados,
            "timeline_atual": list(atual.get("historico_eventos") or []),
        }

    @staticmethod
    def obter_dashboard(
        tenant_id: str = "default",
        mes: int | None = None,
        ano: int | None = None,
        sla_dias: int = 3,
    ) -> dict:
        hoje = date.today()
        periodo_mes = mes or hoje.month
        periodo_ano = ano or hoje.year
        sla = max(1, int(sla_dias or 3))

        registros = _get_active_store().all(tenant_id=tenant_id)

        total_registros = len(registros)
        total_pendentes = 0
        total_atrasados = 0
        total_entregues = 0
        custo_total_mes = 0.0
        custo_total_geral = 0.0
        qtd_com_custo = 0
        qtd_sem_custo = 0
        duracoes_reparo: list[int] = []

        meses_janela = _meses_anteriores(periodo_mes, periodo_ano, 6)
        nomes_mes_abrev = [
            "",
            "Jan",
            "Fev",
            "Mar",
            "Abr",
            "Mai",
            "Jun",
            "Jul",
            "Ago",
            "Set",
            "Out",
            "Nov",
            "Dez",
        ]
        evolucao_map = {
            (m, a): {
                "mes": m,
                "ano": a,
                "label": f"{nomes_mes_abrev[m]}/{str(a)[-2:]}",
                "entradas": 0,
                "entregas": 0,
                "custo": 0.0,
            }
            for m, a in meses_janela
        }

        loc_counts: dict[str, int] = {}
        cat_counts: dict[str, int] = {}
        prio_counts: dict[str, int] = {}
        total_criticos_pendentes = 0
        tecnico_stats: dict[str, dict] = {}
        origem_stats: dict[str, dict] = {}
        alertas_atraso: list[dict] = []

        def _contabilizar_tecnico(nome_bruto: str, campo: str) -> None:
            nome = SecurityValidator.sanitizar_texto(nome_bruto)
            if not nome or nome in {"-", "---"}:
                return
            chave = nome.lower()
            if chave not in tecnico_stats:
                tecnico_stats[chave] = {
                    "variantes": {},
                    "coletas": 0,
                    "entregas": 0,
                }
            tecnico_stats[chave]["variantes"][nome] = (
                tecnico_stats[chave]["variantes"].get(nome, 0) + 1
            )
            tecnico_stats[chave][campo] += 1

        for row in registros:
            finalizado = _is_finalized(row)
            dt_coleta = _parse_date(row.get("data_coleta"))
            dt_entrega = _parse_date(row.get("data_entrega"))
            custo = _extrair_custo(row)
            status_custo = str(row.get("status_custo") or "").strip().lower()

            custo_total_geral += custo
            if status_custo == "com custo" or custo > 0:
                qtd_com_custo += 1
            else:
                qtd_sem_custo += 1

            if dt_coleta and (dt_coleta.month, dt_coleta.year) in evolucao_map:
                evolucao_map[(dt_coleta.month, dt_coleta.year)]["entradas"] += 1

            if dt_entrega and (dt_entrega.month, dt_entrega.year) in evolucao_map:
                evolucao_map[(dt_entrega.month, dt_entrega.year)]["entregas"] += 1
                evolucao_map[(dt_entrega.month, dt_entrega.year)]["custo"] += custo

            if _date_matches(row.get("data_entrega"), periodo_mes, periodo_ano) or (
                not dt_entrega
                and _date_matches(row.get("data_coleta"), periodo_mes, periodo_ano)
            ):
                custo_total_mes += custo

            if finalizado:
                total_entregues += 1
                if dt_coleta and dt_entrega and dt_entrega >= dt_coleta:
                    duracoes_reparo.append((dt_entrega - dt_coleta).days)
            else:
                total_pendentes += 1
                loc = SecurityValidator.sanitizar_texto(
                    row.get("localizacao") or "Bancada TI"
                ) or "Bancada TI"
                loc_counts[loc] = loc_counts.get(loc, 0) + 1

                prio = _normalizar_prioridade(row.get("prioridade"))
                prio_counts[prio] = prio_counts.get(prio, 0) + 1
                if prio == "Crítica":
                    total_criticos_pendentes += 1

                dias_pendente = (
                    max(0, (hoje - dt_coleta).days) if dt_coleta else 0
                )
                if dias_pendente > sla:
                    total_atrasados += 1
                    alertas_atraso.append(
                        {
                            "id": row.get("id"),
                            "equipamento": row.get("equipamento") or "",
                            "tombamento": row.get("tombamento") or "",
                            "origem": row.get("origem") or "",
                            "localizacao": loc,
                            "prioridade": prio,
                            "data_coleta": (
                                dt_coleta.strftime("%d/%m/%Y") if dt_coleta else ""
                            ),
                            "dias_pendente": dias_pendente,
                        }
                    )

            cat = SecurityValidator.sanitizar_texto(row.get("categoria")) or "Não classificado"
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

            _contabilizar_tecnico(
                row.get("tecnico_coleta") or row.get("tecnico") or "", "coletas"
            )
            _contabilizar_tecnico(row.get("tecnico_entrega") or "", "entregas")

            origem_nome = SecurityValidator.sanitizar_texto(row.get("origem") or "")
            if origem_nome:
                chave_orig = origem_nome.lower()
                if chave_orig not in origem_stats:
                    origem_stats[chave_orig] = {
                        "origem": origem_nome,
                        "quantidade": 0,
                        "custo": 0.0,
                    }
                origem_stats[chave_orig]["quantidade"] += 1
                origem_stats[chave_orig]["custo"] += custo

        taxa_resolucao = (
            round((total_entregues / total_registros) * 100, 1)
            if total_registros > 0
            else 0.0
        )
        tempo_medio = (
            round(sum(duracoes_reparo) / len(duracoes_reparo), 1)
            if duracoes_reparo
            else 0.0
        )

        evolucao_mensal = [
            {
                **evolucao_map[chave],
                "custo": round(evolucao_map[chave]["custo"], 2),
            }
            for chave in meses_janela
        ]

        por_localizacao = [
            {"localizacao": loc, "quantidade": qtd}
            for loc, qtd in sorted(
                loc_counts.items(), key=lambda item: (-item[1], item[0].lower())
            )
        ]

        por_tecnico = []
        for item in tecnico_stats.values():
            nome_mais_comum = max(
                item["variantes"].items(), key=lambda pair: (pair[1], pair[0])
            )[0]
            por_tecnico.append(
                {
                    "tecnico": nome_mais_comum,
                    "coletas": item["coletas"],
                    "entregas": item["entregas"],
                    "total": item["coletas"] + item["entregas"],
                }
            )
        por_tecnico.sort(key=lambda item: (-item["total"], item["tecnico"].lower()))
        por_tecnico = por_tecnico[:8]

        top_origens = sorted(
            (
                {
                    "origem": item["origem"],
                    "quantidade": item["quantidade"],
                    "custo": round(item["custo"], 2),
                }
                for item in origem_stats.values()
            ),
            key=lambda item: (-item["quantidade"], -item["custo"], item["origem"].lower()),
        )[:8]

        alertas_atraso.sort(
            key=lambda item: (-item["dias_pendente"], item.get("id") or 0)
        )

        return {
            "resumo": {
                "total_registros": total_registros,
                "total_pendentes": total_pendentes,
                "total_atrasados": total_atrasados,
                "total_entregues": total_entregues,
                "total_criticos_pendentes": total_criticos_pendentes,
                "taxa_resolucao_percent": taxa_resolucao,
                "tempo_medio_resolucao_dias": tempo_medio,
                "custo_total_mes": round(custo_total_mes, 2),
                "custo_total_geral": round(custo_total_geral, 2),
                "qtd_com_custo": qtd_com_custo,
                "qtd_sem_custo": qtd_sem_custo,
                "sla_dias": sla,
                "periodo_mes": periodo_mes,
                "periodo_ano": periodo_ano,
            },
            "evolucao_mensal": evolucao_mensal,
            "por_localizacao": por_localizacao,
            "por_categoria": [
                {"categoria": cat, "quantidade": qtd}
                for cat, qtd in sorted(
                    cat_counts.items(), key=lambda item: (-item[1], item[0].lower())
                )
            ][:8],
            "por_prioridade": [
                {"prioridade": prio, "quantidade": prio_counts.get(prio, 0)}
                for prio in ("Crítica", "Alta", "Normal", "Baixa")
                if prio_counts.get(prio, 0) > 0
            ],
            "por_tecnico": por_tecnico,
            "top_origens": top_origens,
            "alertas_atraso": alertas_atraso[:8],
        }
