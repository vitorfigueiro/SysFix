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

        data_para_validar = data_coleta if data_coleta else datetime.now().strftime("%d/%m/%Y")
        data_validada = SecurityValidator.validar_data(data_para_validar)

        origem_san = SecurityValidator.sanitizar_texto(origem)
        os_san = SecurityValidator.sanitizar_texto(os_coleta)
        loc_san = SecurityValidator.sanitizar_texto(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

        if not equip_san:
            raise ValueError("O nome do equipamento é obrigatório.")

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
                    tomb_san or "S/N",
                    tec_san or "Não informado",
                    data_validada,
                    origem_san or "Geral",
                    os_san or "",
                    loc_san or "Bancada TI",
                    prob_san or "",
                ),
            )
            
            row = cursor.fetchone()
            if isinstance(row, dict):
                novo_id = row.get("id")
            elif row:
                novo_id = row[0]
            else:
                novo_id = None

            conn.commit()
            return novo_id
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
            raise ValueError("Informe o técnico responsável por realizar a entrega.")

        val_custo = SecurityValidator.validate_cost(valor_custo)
        res_san = SecurityValidator.sanitizar_texto(resolucao)
        laudado_san = SecurityValidator.sanitizar_texto(laudado)
        
        data_para_validar = data_entrega if data_entrega else datetime.now().strftime("%d/%m/%Y")
        entrega_san = SecurityValidator.validar_data(data_para_validar)
        
        os_san = SecurityValidator.sanitizar_texto(os_entrega)
        status_custo_san = SecurityValidator.sanitizar_texto(status_custo) or "Sem Custo"

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
                    os_san or "",
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
        
        data_para_validar = data_coleta if data_coleta else datetime.now().strftime("%d/%m/%Y")
        data_validada = SecurityValidator.validar_data(data_para_validar)
        
        origem_san = SecurityValidator.sanitizar_texto(origem)
        os_san = SecurityValidator.sanitizar_texto(os_coleta)
        loc_san = SecurityValidator.sanitizar_texto(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

        if not equip_san:
            raise ValueError("O nome do equipamento é obrigatório.")

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
                    tomb_san or "",
                    tec_san or "",
                    data_validada,
                    origem_san or "",
                    os_san or "",
                    loc_san or "Bancada TI",
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
    def excluir(registro_id):
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
            cursor.execute("""
                SELECT * FROM coletas
                ORDER BY id DESC;
            """)
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
            cursor.execute(
                """
                SELECT * FROM coletas 
                WHERE data_coleta::text LIKE %s OR data_coleta::text LIKE %s
                ORDER BY id DESC;
            """,
                (f"{mes_iso}%", f"%{mes_br}"),
            )
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
            cursor.execute(
                "SELECT * FROM coletas WHERE id = %s;", (registro_id,)
            )
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()