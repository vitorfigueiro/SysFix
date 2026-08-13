import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "coletas.db")

# Aumente este número sempre que fizer alterações na estrutura do banco (ex: criar colunas/tabelas)
VERSAO_ATUAL_SCHEMA = 2


def get_connection():
    """Conecta no banco e ativa diretivas de segurança."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA secure_delete = ON;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def aplicar_migracoes(conn, versao_banco):
    """Aplica as alterações no banco de dados de acordo com a versão atual."""
    cursor = conn.cursor()

    # Migração para a Versão 1 (Criação inicial das tabelas)
    if versao_banco < 1:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS coletas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equipamento TEXT NOT NULL,
                tombamento TEXT NOT NULL,
                tecnico_coleta TEXT NOT NULL,
                data_coleta TEXT NOT NULL,
                origem TEXT NOT NULL,
                localizacao TEXT NOT NULL,
                problema TEXT,
                status TEXT DEFAULT 'Pendente',
                status_custo TEXT DEFAULT 'Sem Custo',
                valor_custo REAL DEFAULT 0.0,
                resolucao TEXT,
                tecnico_entrega TEXT,
                laudado TEXT DEFAULT 'Não'
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS logs_auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                acao TEXT NOT NULL,
                detalhes TEXT,
                data_hora DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

    # Migração para a Versão 2 (Exemplo: Ajustes de colunas ou tabelas futuras)
    if versao_banco < 2:
        try:
            # Exemplo de atualização: garante a presença da coluna 'tecnico_coleta' se ela não existia
            cursor.execute("ALTER TABLE coletas ADD COLUMN tecnico_coleta TEXT;")
        except sqlite3.OperationalError:
            pass  # Coluna já existe

    # Atualiza o número da versão registrada no banco
    cursor.execute(
        "UPDATE schema_version SET versao = ?", (VERSAO_ATUAL_SCHEMA,)
    )
    conn.commit()


def init_db():
    """Inicializa e atualiza o banco de dados automaticamente."""
    conn = get_connection()
    cursor = conn.cursor()

    # Cria a tabela de controle de versão do banco se ela não existir
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY,
            versao INTEGER NOT NULL
        )
    """
    )

    cursor.execute("SELECT versao FROM schema_version WHERE id = 1")
    row = cursor.fetchone()

    if row is None:
        versao_banco = 0
        cursor.execute(
            "INSERT INTO schema_version (id, versao) VALUES (1, 0)"
        )
        conn.commit()
    else:
        versao_banco = row[0]

    # Se a versão do código for maior que a do banco, roda as atualizações
    if versao_banco < VERSAO_ATUAL_SCHEMA:
        aplicar_migracoes(conn, versao_banco)

    conn.close()

# Exemplo de consulta segura contra SQL Injection (OWASP)
def buscar_por_tombamento(tombamento: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        # NUNCA use f-strings no SQL. Use sempre o placeholder (?)
        cursor.execute("SELECT * FROM equipamentos WHERE tombamento = ?", (tombamento,))
        return cursor.fetchone()


# Exemplo de remoção em conformidade com o Direito de Eliminação (LGPD)
def deletar_registro_e_auditar(tombamento: str, usuario_atual: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM equipamentos WHERE tombamento = ?", (tombamento,))
        cursor.execute(
            "INSERT INTO logs_auditoria (usuario, acao, detalhes) VALUES (?, ?, ?)",
            (usuario_atual, "EXCLUSAO_REGISTRO", f"Tombamento removido: {tombamento}")
        )
        conn.commit()