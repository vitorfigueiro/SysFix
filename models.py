from datetime import date, datetime, timezone

from security import SecurityValidator
from storage import _default_store, get_store

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
    ) -> dict:
        if not data_coleta or not str(data_coleta).strip():
            raise ValueError("O campo 'Data de Coleta' é obrigatório.")
        agora = _now_iso()
        autor = SecurityValidator.sanitizar_texto(actor)
        record = {
            "equipamento": SecurityValidator.sanitizar_texto(equipamento),
            "tombamento": SecurityValidator.sanitizar_texto(tombamento),
            "tecnico_coleta": SecurityValidator.sanitizar_texto(tecnico),
            "data_coleta": SecurityValidator.validar_data(data_coleta),
            "origem": SecurityValidator.sanitizar_texto(origem),
            "os_coleta": SecurityValidator.sanitizar_texto(os_coleta),
            "localizacao": _validar_localizacao_empresa(
                localizacao, allowed_locations=allowed_locations
            ),
            "problema": SecurityValidator.sanitizar_texto(problema),
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
    ) -> list:
        if not lista_coletas or not isinstance(lista_coletas, list):
            raise ValueError("Uma lista válida de registros deve ser fornecida.")

        records = []
        for index, item in enumerate(lista_coletas, start=1):
            try:
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

        atualizacoes = {
            "status": "Entregue",
            "tecnico_entrega": tecnico,
            "data_entrega": SecurityValidator.validar_data(data_entrega),
            "os_entrega": ordem_servico,
            "status_custo": status_custo_norm,
            "valor_custo": valor_custo_norm,
            "resolucao": SecurityValidator.sanitizar_texto(resolucao),
            "laudado": laudado_norm,
            "atualizado_em": _now_iso(),
            "atualizado_por": SecurityValidator.sanitizar_texto(actor),
        }

        return _get_active_store().update_record(
            registro_id, atualizacoes, tenant_id=tenant_id
        )

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
    ):
        registro_id = _validar_registro_id(
            registro_id, "ID de registro inválido para atualização."
        )

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
            "criado_em",
            "criado_por",
        ):
            atualizacoes.pop(campo_fixo, None)

        return _get_active_store().update_record(
            registro_id, atualizacoes, tenant_id=tenant_id
        )

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
                            "data_coleta": (
                                dt_coleta.strftime("%d/%m/%Y") if dt_coleta else ""
                            ),
                            "dias_pendente": dias_pendente,
                        }
                    )

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
            "por_tecnico": por_tecnico,
            "top_origens": top_origens,
            "alertas_atraso": alertas_atraso[:8],
        }
