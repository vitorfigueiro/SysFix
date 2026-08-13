import sqlite3
from database import get_connection
from security import SecurityValidator
from datetime import datetime

class ColetaModel:
    @staticmethod
    def registrar_entrada(equipamento, tombamento, tecnico, data_coleta, origem, localizacao, problema):
        equip_san = SecurityValidator.sanitizar_texto(equipamento)
        tomb_san = SecurityValidator.sanitizar_texto(tombamento)
        tec_san = SecurityValidator.sanitizar_texto(tecnico)
        data_validada = SecurityValidator.validar_data(data_coleta)
        origem_san = SecurityValidator.sanitizar_texto(origem)
        loc_san = SecurityValidator.sanitizar_texto(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

        if not equip_san or not tomb_san or not tec_san or not origem_san:
            raise ValueError("Preencha todos os campos obrigatórios (Equipamento, Tombamento, Técnico e Origem).")

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO coletas (equipamento, tombamento, tecnico, data_coleta, origem, localizacao, problema, status, status_custo, valor_custo, laudado)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Pendente', 'Sem Custo', 0.0, 'Não')
            """, (equip_san, tomb_san, tec_san, data_validada, origem_san, loc_san, prob_san))
            conn.commit()
            return cursor.lastrowid

    @staticmethod
    def registrar_saida(registro_id, tecnico_entrega, status_custo, valor_custo, resolucao, laudado):
        if not registro_id:
            raise ValueError("Nenhum registro selecionado para atualizar saída.")

        tec_entrega_san = SecurityValidator.sanitizar_texto(tecnico_entrega)
        if not tec_entrega_san:
            raise ValueError("Informe o técnico responsável por realizar a entrega.")

        val_custo = SecurityValidator.converter_valor_moeda(valor_custo)
        res_san = SecurityValidator.sanitizar_texto(resolucao)
        laudado_san = SecurityValidator.sanitizar_texto(laudado)

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE coletas 
                SET status = 'Entregue', 
                    tecnico_entrega = ?,
                    status_custo = ?,
                    valor_custo = ?, 
                    resolucao = ?,
                    laudado = ?
                WHERE id = ?
            """, (tec_entrega_san, status_custo, val_custo, res_san, laudado_san, registro_id))
            conn.commit()

    @staticmethod
    def atualizar_entrada(registro_id, equipamento, tombamento, tecnico, data_coleta, origem, localizacao, problema):
        equip_san = SecurityValidator.sanitizar_texto(equipamento)
        tomb_san = SecurityValidator.sanitizar_texto(tombamento)
        tec_san = SecurityValidator.sanitizar_texto(tecnico)
        data_validada = SecurityValidator.validar_data(data_coleta)
        origem_san = SecurityValidator.sanitizar_texto(origem)
        loc_san = SecurityValidator.sanitizar_texto(localizacao)
        prob_san = SecurityValidator.sanitizar_texto(problema)

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE coletas 
                SET equipamento = ?, tombamento = ?, tecnico = ?, data_coleta = ?, origem = ?, localizacao = ?, problema = ?
                WHERE id = ?
            """, (equip_san, tomb_san, tec_san, data_validada, origem_san, loc_san, prob_san, registro_id))
            conn.commit()

    @staticmethod
    def excluir(registro_id):
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM coletas WHERE id = ?", (registro_id,))
            conn.commit()

    @staticmethod
    def buscar_nao_finalizados_mes_atual():
        """Busca equipamentos cadastrados no mês atual que ainda NÃO foram entregues."""
        conn = get_connection()
        cursor = conn.cursor()
        mes_atual = datetime.now().strftime("%Y-%m")
        
        cursor.execute("""
            SELECT id, equipamento, tombamento, tecnico_coleta, data_coleta, origem, localizacao, problema, status, status_custo, valor_custo, resolucao, tecnico_entrega, laudado
            FROM coletas
            WHERE strftime('%Y-%m', data_coleta) = ? AND status != 'Entregue'
            ORDER BY id DESC
        """, (mes_atual,))
        
        registros = cursor.fetchall()
        conn.close()
        return registros

    @staticmethod
    def buscar_finalizados_mes_atual():
        """Busca equipamentos entregues no mês atual."""
        conn = get_connection()
        cursor = conn.cursor()
        mes_atual = datetime.now().strftime("%Y-%m")
        
        cursor.execute("""
            SELECT id, equipamento, tombamento, tecnico_coleta, data_coleta, origem, localizacao, problema, status, status_custo, valor_custo, resolucao, tecnico_entrega, laudado
            FROM coletas
            WHERE strftime('%Y-%m', data_coleta) = ? AND status = 'Entregue'
            ORDER BY id DESC
        """, (mes_atual,))
        
        registros = cursor.fetchall()
        conn.close()
        return registros

    @staticmethod
    def buscar_todos():
        """Busca todos os registros do banco de dados."""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, equipamento, tombamento, tecnico_coleta, data_coleta, origem, localizacao, problema, status, status_custo, valor_custo, resolucao, tecnico_entrega, laudado
            FROM coletas
            ORDER BY id DESC
        """)
        
        registros = cursor.fetchall()
        conn.close()
        return registros

    @staticmethod
    def buscar_por_mes_ano(mes: int, ano: int):
        mes_str = f"{ano:04d}-{mes:02d}"
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, equipamento, tombamento, tecnico, data_coleta, origem, localizacao, status, status_custo, valor_custo, resolucao, tecnico_entrega, laudado 
                FROM coletas 
                WHERE strftime('%Y-%m', data_coleta) = ?
                ORDER BY id DESC
            """, (mes_str,))
            return cursor.fetchall()