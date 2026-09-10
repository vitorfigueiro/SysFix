import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

VERSAO_ATUAL_SCHEMA = 5


def get_connection():
    """Conecta no banco PostgreSQL hospedado no Neon."""
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("[DEBUG ERROR] DATABASE_URL não foi encontrada em os.environ!")
        raise ValueError(
            "A variável de ambiente DATABASE_URL não foi configurada! Adicione-a no Railway/env."
        )

    url_conexao = database_url.strip()

    # Corrige prefixo antigo postgres:// para postgresql://
    if url_conexao.startswith("postgres://"):
        url_conexao = url_conexao.replace("postgres://", "postgresql://", 1)

    # Garante SSL sem duplicar parâmetros
    if "sslmode=" not in url_conexao:
        conector = "&" if "?" in url_conexao else "?"
        url_conexao += f"{conector}sslmode=require"

    return psycopg2.connect(url_conexao, cursor_factory=RealDictCursor)


def aplicar_migracoes(conn, versao_banco):
    """Aplica as alterações no banco de dados de acordo com a versão do schema."""
    cursor = conn.cursor()

    try:
        if versao_banco < 1:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS coletas (
                    id SERIAL PRIMARY KEY,
                    equipamento VARCHAR(255) NOT NULL,
                    tombamento VARCHAR(255),
                    tecnico_coleta VARCHAR(255),
                    data_coleta VARCHAR(50) NOT NULL,
                    origem VARCHAR(255),
                    localizacao VARCHAR(255),
                    problema TEXT,
                    status VARCHAR(50) DEFAULT 'Pendente',
                    status_custo VARCHAR(50) DEFAULT 'Sem Custo',
                    valor_custo NUMERIC(10, 2) DEFAULT 0.0,
                    resolucao TEXT,
                    tecnico_entrega VARCHAR(255),
                    laudado VARCHAR(10) DEFAULT 'Não'
                );
            """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS logs_auditoria (
                    id SERIAL PRIMARY KEY,
                    usuario VARCHAR(255) NOT NULL,
                    acao VARCHAR(255) NOT NULL,
                    detalhes TEXT,
                    data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """
            )
            conn.commit()

        if versao_banco < 2:
            cursor.execute(
                "ALTER TABLE coletas ADD COLUMN IF NOT EXISTS tecnico_coleta VARCHAR(255);"
            )
            conn.commit()

        if versao_banco < 4:
            colunas_para_adicionar = [
                ("os_coleta", "VARCHAR(255)"),
                ("os_entrega", "VARCHAR(255)"),
                ("data_entrega", "VARCHAR(255)"),
            ]

            for nome_coluna, tipo_coluna in colunas_para_adicionar:
                try:
                    cursor.execute(
                        f"ALTER TABLE coletas ADD COLUMN IF NOT EXISTS {nome_coluna} {tipo_coluna};"
                    )
                    cursor.execute(
                        f"ALTER TABLE coletas ALTER COLUMN {nome_coluna} DROP NOT NULL;"
                    )
                    conn.commit()
                except Exception as err_coluna:
                    conn.rollback()
                    print(f"Aviso ao adicionar coluna {nome_coluna}: {err_coluna}")

        if versao_banco < 5:
            colunas_flexiveis = ["tombamento", "tecnico_coleta", "origem", "localizacao"]
            for col in colunas_flexiveis:
                try:
                    cursor.execute(f"ALTER TABLE coletas ALTER COLUMN {col} DROP NOT NULL;")
                    conn.commit()
                except Exception as err_drop:
                    conn.rollback()
                    print(f"Aviso ao alterar NOT NULL da coluna {col}: {err_drop}")

        cursor.execute(
            "UPDATE schema_version SET versao = %s WHERE id = 1;",
            (VERSAO_ATUAL_SCHEMA,),
        )
        conn.commit()

    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Erro ao aplicar migração do schema: {e}")
    finally:
        cursor.close()


def init_db():
    """Inicializa a tabela de controle de versão e executa migrações necessárias."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY,
                versao INTEGER NOT NULL
            );
        """
        )
        conn.commit()

        cursor.execute("SELECT versao FROM schema_version WHERE id = 1;")
        row = cursor.fetchone()

        if row is None:
            versao_banco = 0
            cursor.execute(
                "INSERT INTO schema_version (id, versao) VALUES (1, 0);"
            )
            conn.commit()
        else:
            if isinstance(row, dict):
                versao_banco = row.get("versao", 0)
            else:
                versao_banco = row[0]

        if versao_banco < VERSAO_ATUAL_SCHEMA:
            aplicar_migracoes(conn, versao_banco)

    finally:
        cursor.close()
        conn.close()


# ==============================================================================
# FUNÇÕES CRUD INTEGRADAS
# ==============================================================================

def salvar_coleta(dados: dict):
    """Insere um novo registro de entrada de equipamento no banco de dados."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            INSERT INTO coletas (
                equipamento, tombamento, tecnico_coleta, data_coleta, 
                origem, os_coleta, localizacao, problema, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pendente')
            RETURNING id;
        """
        cursor.execute(
            query,
            (
                dados.get("equipamento"),
                dados.get("tombamento"),
                dados.get("tecnico") or dados.get("tecnico_coleta"),
                dados.get("data_coleta"),
                dados.get("origem"),
                dados.get("os_coleta"),
                dados.get("localizacao"),
                dados.get("problema"),
            ),
        )
        row = cursor.fetchone()
        novo_id = row["id"] if isinstance(row, dict) else row[0]
        conn.commit()
        return novo_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def listar_coletas(status_filtro: str = "todos"):
    """Lista os equipamentos filtrando por status flexível (pendentes, entregues, finalizados, todos)."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Normaliza o termo de busca enviado pelo frontend
    filtro = str(status_filtro).strip().lower() if status_filtro else "todos"

    try:
        if filtro in ["nao_finalizados", "pendentes", "pendente"]:
            cursor.execute("""
                SELECT * FROM coletas 
                WHERE LOWER(TRIM(status)) NOT IN ('entregue', 'finalizado') 
                   OR status IS NULL 
                ORDER BY id DESC;
            """)
        elif filtro in ["finalizados", "entregues", "entregue", "finalizado"]:
            cursor.execute("""
                SELECT * FROM coletas 
                WHERE LOWER(TRIM(status)) IN ('entregue', 'finalizado') 
                ORDER BY id DESC;
            """)
        else:
            cursor.execute("SELECT * FROM coletas ORDER BY id DESC;")
            
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def obter_coleta_por_id(registro_id: int):
    """Obtém os detalhes de um equipamento pelo ID."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM coletas WHERE id = %s;", (registro_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def atualizar_coleta_entrada(registro_id: int, dados: dict):
    """Atualiza as informações de entrada de um equipamento existente."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            UPDATE coletas SET
                equipamento = %s,
                tombamento = %s,
                tecnico_coleta = %s,
                data_coleta = %s,
                origem = %s,
                os_coleta = %s,
                localizacao = %s,
                problema = %s
            WHERE id = %s;
        """
        cursor.execute(
            query,
            (
                dados.get("equipamento"),
                dados.get("tombamento"),
                dados.get("tecnico") or dados.get("tecnico_coleta"),
                dados.get("data_coleta"),
                dados.get("origem"),
                dados.get("os_coleta"),
                dados.get("localizacao"),
                dados.get("problema"),
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


def salvar_saida_coleta(registro_id: int, dados: dict):
    """Registra a saída/resolução do equipamento e altera o status para Entregue."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            UPDATE coletas SET
                tecnico_entrega = %s,
                os_entrega = %s,
                data_entrega = %s,
                laudado = %s,
                status_custo = %s,
                valor_custo = %s,
                resolucao = %s,
                status = 'Entregue'
            WHERE id = %s;
        """
        cursor.execute(
            query,
            (
                dados.get("tecnico_entrega"),
                dados.get("os_entrega"),
                dados.get("data_entrega"),
                dados.get("laudado"),
                dados.get("status_custo"),
                dados.get("valor_custo", 0.0),
                dados.get("resolucao"),
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


def deletar_registro_e_auditar(registro_id: int, usuario_atual: str = "Sistema"):
    """Exclui o equipamento pelo ID e grava o evento no log de auditoria."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM coletas WHERE id = %s;", (registro_id,))
        cursor.execute(
            "INSERT INTO logs_auditoria (usuario, acao, detalhes) VALUES (%s, %s, %s);",
            (
                usuario_atual,
                "EXCLUSAO_REGISTRO",
                f"Registro ID {registro_id} removido",
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()