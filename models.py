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
        localizacao,
        problema,
    ):
        equip_san = SecurityValidator.sanitizar_texto(equipamento)
        tomb_san = SecurityValidator.sanitizar_texto(tombamento)
        tec_san = SecurityValidator.sanitizar_texto(tecnico)
        data_validada = SecurityValidator.validar_data(data_coleta)
        origem_san = SecurityValidator.sanitizar_texto(origem)
        loc_san = SecurityValidator.sanitizar_texto(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

        if not equip_san or not tomb_san or not tec_san or not origem_san:
            raise ValueError(
                "Preencha todos os campos obrigatórios (Equipamento,"
                " Tombamento, Técnico e Origem)."
            )

        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO coletas (equipamento, tombamento, tecnico_coleta, data_coleta, origem, localizacao, problema)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """,
                (
                    equip_san,
                    tomb_san,
                    tec_san,
                    data_validada,
                    origem_san,
                    loc_san,
                    prob_san,
                ),
            )
            novo_id = cursor.fetchone()[0]
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
            raise ValueError(
                "Nenhum registro selecionado para atualizar saída."
            )

        tec_entrega_san = SecurityValidator.sanitizar_texto(tecnico_entrega)
        if not tec_entrega_san:
            raise ValueError(
                "Informe o técnico responsável por realizar a entrega."
            )

        val_custo = SecurityValidator.validate_cost(valor_custo)
        res_san = SecurityValidator.sanitizar_texto(resolucao)
        laudado_san = SecurityValidator.sanitizar_texto(laudado)
        entrega_san = SecurityValidator.sanitizar_texto(data_entrega)
        os_san = SecurityValidator.sanitizar_texto(os_entrega)

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
                WHERE id = %s
            """,
                (
                    tec_entrega_san,
                    entrega_san,
                    os_san,
                    status_custo,
                    val_custo,
                    res_san,
                    laudado_san,
                    registro_id,
                ),
            )
            conn.commit()
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
        equip_san = SecurityValidator.sanitizar_texto(equipamento)
        tomb_san = SecurityValidator.sanitizar_texto(tombamento)
        tec_san = SecurityValidator.sanitizar_texto(tecnico)
        data_validada = SecurityValidator.validar_data(data_coleta)
        origem_san = SecurityValidator.sanitizar_texto(origem)
        os_san = SecurityValidator.sanitizar_texto(os_coleta)
        loc_san = SecurityValidator.sanitizar_texto(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

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
                WHERE id = %s
            """,
                (
                    equip_san,
                    tomb_san,
                    tec_san,
                    data_validada,
                    origem_san,
                    os_san,
                    loc_san,
                    prob_san,
                    registro_id,
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def excluir(registro_id):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM coletas WHERE id = %s", (registro_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def buscar_nao_finalizados_mes_atual():
        """Busca equipamentos cadastrados no mês atual que ainda NÃO foram entregues."""
        conn = get_connection()
        cursor = conn.cursor()
        mes_atual = datetime.now().strftime("%Y-%m")

        cursor.execute(
            """
            SELECT id, equipamento, tombamento, tecnico_coleta, data_coleta, 
                   origem, os_coleta, localizacao, problema, status, 
                   status_custo, valor_custo, os_entrega, resolucao, 
                   tecnico_entrega, data_entrega, laudado
            FROM coletas
            WHERE data_coleta LIKE %s AND (status != 'Entregue' OR status IS NULL)
            ORDER BY id DESC
        """,
            (f"{mes_atual}%",),
        )

        registros = cursor.fetchall()
        cursor.close()
        conn.close()
        return registros

    @staticmethod
    def buscar_finalizados_mes_atual():
        """Busca equipamentos entregues no mês atual."""
        conn = get_connection()
        cursor = conn.cursor()
        mes_atual = datetime.now().strftime("%Y-%m")

        cursor.execute(
            """
            SELECT id, equipamento, tombamento, tecnico_coleta, data_coleta, 
                   origem, os_coleta, localizacao, problema, status, 
                   status_custo, valor_custo, os_entrega, resolucao, 
                   tecnico_entrega, data_entrega, laudado
            FROM coletas
            WHERE data_coleta LIKE %s AND status = 'Entregue'
            ORDER BY id DESC
        """,
            (f"{mes_atual}%",),
        )

        registros = cursor.fetchall()
        cursor.close()
        conn.close()
        return registros

    @staticmethod
    def buscar_todos():
        """Busca todos os registros do banco de dados."""
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, equipamento, tombamento, tecnico_coleta, data_coleta, 
                   origem, os_coleta, localizacao, problema, status, 
                   status_custo, valor_custo, os_entrega, resolucao, 
                   tecnico_entrega, data_entrega, laudado
            FROM coletas
            ORDER BY id DESC
        """)

        registros = cursor.fetchall()
        cursor.close()
        conn.close()
        return registros

    @staticmethod
    def buscar_por_mes_ano(mes: int, ano: int):
        mes_str = f"{ano:04d}-{mes:02d}"
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, equipamento, tombamento, tecnico_coleta, data_coleta, 
                       origem, os_coleta, localizacao, problema, status, 
                       status_custo, valor_custo, os_entrega, resolucao, 
                       tecnico_entrega, data_entrega, laudado 
                FROM coletas 
                WHERE data_coleta LIKE %s
                ORDER BY id DESC
            """,
                (f"{mes_str}%",),
            )
            registros = cursor.fetchall()
            return registros
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def buscar_por_id(registro_id):
        """Busca o registro pelo ID e retorna os dados mapeados por nome de coluna."""
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute("SELECT * FROM coletas WHERE id = %s;", (registro_id,))
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()