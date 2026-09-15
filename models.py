from datetime import datetime
import psycopg2
import psycopg2.extras
from database import get_connection
from security import SecurityValidator


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
    ):
        equip_san = SecurityValidator.sanitizar_texto(equipamento)
        tomb_san = SecurityValidator.sanitizar_texto(tombamento)
        tec_san = SecurityValidator.sanitizar_texto(tecnico)
        origem_san = SecurityValidator.sanitizar_texto(origem)
        os_san = SecurityValidator.sanitizar_texto(os_coleta)
        loc_san = SecurityValidator.validate_location(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

        # Validação de campos obrigatórios de Entrada
        if not equip_san:
            raise ValueError("O campo 'Equipamento' é obrigatório.")
        if not tomb_san:
            raise ValueError("O campo 'Tombamento' é obrigatório.")
        if not tec_san:
            raise ValueError("O campo 'Técnico de Coleta' é obrigatório.")
        if not data_coleta or not str(data_coleta).strip():
            raise ValueError("O campo 'Data de Coleta' é obrigatório.")
        if not origem_san:
            raise ValueError("O campo 'Origem' é obrigatório.")
        if not os_san:
            raise ValueError("O campo 'O.S. de Coleta' é obrigatório.")

        data_validada = SecurityValidator.validar_data(data_coleta)

        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute(
                """
                INSERT INTO coletas (
                    equipamento, tombamento, tecnico_coleta, data_coleta, 
                    origem, os_coleta, localizacao, problema, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pendente')
                RETURNING id;
            """,
                (
                    equip_san,
                    tomb_san,
                    tec_san,
                    data_validada,
                    origem_san,
                    os_san,
                    loc_san,
                    prob_san or "",
                ),
            )

            row = cursor.fetchone()
            novo_id = (
                row.get("id")
                if isinstance(row, dict)
                else (row[0] if row else None)
            )

            conn.commit()
            return novo_id
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def registrar_entrada_em_lote(lista_coletas: list) -> list:
        """Processa e cadastra múltiplos equipamentos em lote com validações obrigatórias."""
        if not lista_coletas or not isinstance(lista_coletas, list):
            raise ValueError("Uma lista válida de registros deve ser fornecida.")

        coletas_preparadas = []
        for idx, item in enumerate(lista_coletas, start=1):
            equip_san = SecurityValidator.sanitizar_texto(item.get("equipamento"))
            tomb_san = SecurityValidator.sanitizar_texto(item.get("tombamento"))
            tec_san = SecurityValidator.sanitizar_texto(
                item.get("tecnico") or item.get("tecnico_coleta")
            )
            data_coleta = item.get("data_coleta")
            origem_san = SecurityValidator.sanitizar_texto(item.get("origem"))
            os_san = SecurityValidator.sanitizar_texto(item.get("os_coleta"))
            loc_san = SecurityValidator.validate_location(item.get("localizacao"))
            prob_san = SecurityValidator.sanitizar_texto(item.get("problema"))

            # Validação rigorosa para cada item do lote
            if not equip_san:
                raise ValueError(f"Item {idx}: O campo 'Equipamento' é obrigatório.")
            if not tomb_san:
                raise ValueError(f"Item {idx}: O campo 'Tombamento' é obrigatório.")
            if not tec_san:
                raise ValueError(f"Item {idx}: O campo 'Técnico de Coleta' é obrigatório.")
            if not data_coleta or not str(data_coleta).strip():
                raise ValueError(f"Item {idx}: O campo 'Data de Coleta' é obrigatório.")
            if not origem_san:
                raise ValueError(f"Item {idx}: O campo 'Origem' é obrigatório.")
            if not os_san:
                raise ValueError(f"Item {idx}: O campo 'O.S. de Coleta' é obrigatório.")

            data_validada = SecurityValidator.validar_data(data_coleta)

            coletas_preparadas.append(
                (
                    equip_san,
                    tomb_san,
                    tec_san,
                    data_validada,
                    origem_san,
                    os_san,
                    loc_san,
                    prob_san or "",
                )
            )

        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        ids_criados = []

        try:
            query = """
                INSERT INTO coletas (
                    equipamento, tombamento, tecnico_coleta, data_coleta, 
                    origem, os_coleta, localizacao, problema, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pendente')
                RETURNING id;
            """
            for params in coletas_preparadas:
                cursor.execute(query, params)
                row = cursor.fetchone()
                novo_id = (
                    row.get("id")
                    if isinstance(row, dict)
                    else (row[0] if row else None)
                )

                if novo_id:
                    ids_criados.append(novo_id)

            conn.commit()
            return ids_criados
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

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
    ):
        if not registro_id:
            raise ValueError("Nenhum registro selecionado para atualizar saída.")

        tec_entrega_san = SecurityValidator.sanitizar_texto(tecnico_entrega)
        if not tec_entrega_san:
            raise ValueError("O campo 'Técnico de Entrega/Saída' é obrigatório.")

        os_san = SecurityValidator.sanitizar_texto(os_entrega)
        if not os_san:
            raise ValueError("O campo 'O.S. de Entrega/Saída' é obrigatório.")

        if not data_entrega or not str(data_entrega).strip():
            raise ValueError("O campo 'Data de Entrega/Saída' é obrigatório.")

        entrega_san = SecurityValidator.validar_data(data_entrega)
        val_custo = SecurityValidator.validate_cost(valor_custo)
        res_san = SecurityValidator.sanitizar_texto(resolucao)
        laudado_san = SecurityValidator.sanitizar_texto(laudado)
        status_custo_san = (
            SecurityValidator.sanitizar_texto(status_custo) or "Sem Custo"
        )

        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE coletas 
                SET status = 'Entregue', 
                    tecnico_entrega = %s,
                    data_entrega = %s,
                    os_entrega = %s,
                    status_custo = %s,
                    valor_custo = %s, 
                    resolucao = %s,
                    laudado = %s
                WHERE id = %s;
            """,
                (
                    tec_entrega_san,
                    entrega_san,
                    os_san,
                    status_custo_san,
                    val_custo,
                    res_san or "",
                    laudado_san or "Não",
                    registro_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

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
    ):
        if not registro_id:
            raise ValueError("ID de registro inválido para atualização.")

        equip_san = SecurityValidator.sanitizar_texto(equipamento)
        tomb_san = SecurityValidator.sanitizar_texto(tombamento)
        tec_san = SecurityValidator.sanitizar_texto(tecnico)
        origem_san = SecurityValidator.sanitizar_texto(origem)
        os_san = SecurityValidator.sanitizar_texto(os_coleta)
        loc_san = SecurityValidator.validate_location(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

        # Validação de campos obrigatórios
        if not equip_san:
            raise ValueError("O campo 'Equipamento' é obrigatório.")
        if not tomb_san:
            raise ValueError("O campo 'Tombamento' é obrigatório.")
        if not tec_san:
            raise ValueError("O campo 'Técnico de Coleta' é obrigatório.")
        if not data_coleta or not str(data_coleta).strip():
            raise ValueError("O campo 'Data de Coleta' é obrigatório.")
        if not origem_san:
            raise ValueError("O campo 'Origem' é obrigatório.")
        if not os_san:
            raise ValueError("O campo 'O.S. de Coleta' é obrigatório.")

        data_validada = SecurityValidator.validar_data(data_coleta)

        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE coletas 
                SET equipamento = %s, 
                    tombamento = %s, 
                    tecnico_coleta = %s, 
                    data_coleta = %s, 
                    origem = %s, 
                    os_coleta = %s, 
                    localizacao = %s, 
                    problema = %s
                WHERE id = %s;
            """,
                (
                    equip_san,
                    tomb_san,
                    tec_san,
                    data_validada,
                    origem_san,
                    os_san,
                    loc_san,
                    prob_san or "",
                    registro_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def excluir(registro_id, is_admin: bool = False):
        """Exclui um registro do banco de dados. Operação restrita a administradores."""
        if not is_admin:
            raise PermissionError(
                "Acesso negado: Apenas administradores possuem permissão para excluir registros."
            )

        if not registro_id:
            raise ValueError("ID de registro inválido para exclusão.")

        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM coletas WHERE id = %s;", (registro_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def buscar_nao_finalizados_mes_atual():
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute(
                """
                SELECT * FROM coletas
                WHERE (LOWER(TRIM(status)) NOT IN ('entregue', 'finalizado') OR status IS NULL)
                ORDER BY id DESC;
                """
            )
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def buscar_finalizados_mes_atual():
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute(
                """
                SELECT * FROM coletas
                WHERE LOWER(TRIM(status)) IN ('entregue', 'finalizado')
                ORDER BY id DESC;
                """
            )
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def buscar_todos():
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute("SELECT * FROM coletas ORDER BY id DESC;")
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def buscar_por_mes_ano(mes: int, ano: int):
        mes_iso = f"{ano:04d}-{mes:02d}"
        mes_br = f"/{mes:02d}/{ano:04d}"

        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            query = """
                SELECT * FROM coletas 
                WHERE (data_coleta::text LIKE %s OR data_coleta::text LIKE %s)
                   OR (data_entrega::text LIKE %s OR data_entrega::text LIKE %s)
                ORDER BY id DESC;
            """
            p_iso = f"{mes_iso}%"
            p_br = f"%{mes_br}"

            cursor.execute(query, (p_iso, p_br, p_iso, p_br))
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def buscar_por_id(registro_id):
        if not registro_id:
            return None

        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute("SELECT * FROM coletas WHERE id = %s;", (registro_id,))
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()