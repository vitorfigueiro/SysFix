import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

VERSAO_ATUAL_SCHEMA = 4


def get_connection():
    """Conecta no banco PostgreSQL hospedado na nuvem."""
    if not DATABASE_URL:
        raise ValueError(
            "A variável de ambiente DATABASE_URL não foi configurada no arquivo .env!"
        )

    url_conexao = DATABASE_URL
    if url_conexao.startswith("postgres://"):
        url_conexao = url_conexao.replace("postgres://", "postgresql://", 1)

    return psycopg2.connect(url_conexao, cursor_factory=RealDictCursor)


def aplicar_migracoes(conn, versao_banco):
    """Aplica as alterações no banco de dados de acordo com a versão atual."""
    cursor = conn.cursor()

    try:
        # Migração Versão 1: Criação Inicial
        if versao_banco < 1:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS coletas (
                    id SERIAL PRIMARY KEY,
                    equipamento VARCHAR(255) NOT NULL,
                    tombamento VARCHAR(255) NOT NULL,
                    tecnico_coleta VARCHAR(255) NOT NULL,
                    data_coleta VARCHAR(50) NOT NULL,
                    origem VARCHAR(255) NOT NULL,
                    localizacao VARCHAR(255) NOT NULL,
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

        # Migração Versão 2
        if versao_banco < 2:
            cursor.execute(
                "ALTER TABLE coletas ADD COLUMN IF NOT EXISTS tecnico_coleta VARCHAR(255);"
            )
            conn.commit()

        # Migração Versão 4: Adição individual e segura das colunas de O.S. e entregas
        if versao_banco < 4:
            colunas_para_adicionar = [
                ("os_coleta", "VARCHAR(255)"),
                ("os_entrega", "VARCHAR(255)"),
                ("data_entrega", "VARCHAR(255)")
            ]
            
            for nome_coluna, tipo_coluna in colunas_para_adicionar:
                try:
                    cursor.execute(f"ALTER TABLE coletas ADD COLUMN IF NOT EXISTS {nome_coluna} {tipo_coluna};")
                    cursor.execute(f"ALTER TABLE coletas ALTER COLUMN {nome_coluna} DROP NOT NULL;")
                    conn.commit()  # Garante a criação individual de cada coluna
                except Exception as err_coluna:
                    conn.rollback()
                    print(f"Aviso ao adicionar coluna {nome_coluna}: {err_coluna}")

        # Atualiza a versão registrada no banco
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
    """Inicializa e atualiza o banco de dados automaticamente na nuvem."""
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
            # Compatível com RealDictCursor (dicionário) ou cursor normal (tupla/lista)
            if isinstance(row, dict):
                versao_banco = row.get("versao", 0)
            else:
                versao_banco = row[0]

        if versao_banco < VERSAO_ATUAL_SCHEMA:
            aplicar_migracoes(conn, versao_banco)

    finally:
        cursor.close()
        conn.close()


def buscar_por_tombamento(tombamento: str):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM coletas WHERE tombamento = %s;", (tombamento,)
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def deletar_registro_e_auditar(tombamento: str, usuario_atual: str):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM coletas WHERE tombamento = %s;", (tombamento,)
        )
        cursor.execute(
            "INSERT INTO logs_auditoria (usuario, acao, detalhes) VALUES (%s, %s, %s);",
            (
                usuario_atual,
                "EXCLUSAO_REGISTRO",
                f"Tombamento removido: {tombamento}",
            ),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()