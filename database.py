import os
import psycopg2
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env
load_dotenv()

# Obtém a URI de conexão com o PostgreSQL na nuvem
# Exemplo no .env: DATABASE_URL="postgresql://usuario:senha@ep-exemplo.us-east-1.aws.neon.tech/nome_do_banco?sslmode=require"
DATABASE_URL = os.getenv("DATABASE_URL","postgresql://neondb_owner:npg_Fn1uJ5SfclgP@ep-square-frog-ac27k813.sa-east-1.aws.neon.tech/neondb?sslmode=require")

# Aumente este número sempre que fizer alterações na estrutura do banco (ex: criar colunas/tabelas)
VERSAO_ATUAL_SCHEMA = 3


def get_connection():
    """Conecta no banco PostgreSQL hospedado na nuvem."""
    if not DATABASE_URL:
        raise ValueError(
            "A variável de ambiente DATABASE_URL não foi configurada no arquivo .env!"
        )

    conn = psycopg2.connect(DATABASE_URL)
    return conn


def aplicar_migracoes(conn, versao_banco):
    """Aplica as alterações no banco de dados de acordo com a versão atual."""
    cursor = conn.cursor()

    # Migração para a Versão 1 (Criação inicial das tabelas)
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

    # Migração para a Versão 2
    if versao_banco < 2:
        try:
            cursor.execute(
                "ALTER TABLE coletas ADD COLUMN IF NOT EXISTS tecnico_coleta VARCHAR(255);"
            )
        except Exception:
            conn.rollback()  # Reseta a transação em caso de erro

    # Atualiza o número da versão registrada no banco
    cursor.execute(
        "UPDATE schema_version SET versao = %s WHERE id = 1;",
        (VERSAO_ATUAL_SCHEMA,),
    )
    conn.commit()
    cursor.close()


def init_db():
    """Inicializa e atualiza o banco de dados automaticamente na nuvem."""
    conn = get_connection()
    cursor = conn.cursor()

    # Cria a tabela de controle de versão do banco se ela não existir
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY,
            versao INTEGER NOT NULL
        );
    """
    )

    cursor.execute("SELECT versao FROM schema_version WHERE id = 1;")
    row = cursor.fetchone()

    if row is None:
        versao_banco = 0
        cursor.execute(
            "INSERT INTO schema_version (id, versao) VALUES (1, 0);"
        )
        conn.commit()
    else:
        versao_banco = row[0]

    # Se a versão do código for maior que a do banco, roda as atualizações
    if versao_banco < VERSAO_ATUAL_SCHEMA:
        aplicar_migracoes(conn, versao_banco)

    cursor.close()
    conn.close()


# Exemplo de consulta segura contra SQL Injection (OWASP) com PostgreSQL (%s)
def buscar_por_tombamento(tombamento: str):
    conn = get_connection()
    cursor = conn.cursor()
    # No PostgreSQL usamos %s em vez de ?
    cursor.execute(
        "SELECT * FROM coletas WHERE tombamento = %s;", (tombamento,)
    )
    resultado = cursor.fetchone()
    cursor.close()
    conn.close()
    return resultado


# Exemplo de remoção em conformidade com o Direito de Eliminação (LGPD)
def deletar_registro_e_auditar(tombamento: str, usuario_atual: str):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM coletas WHERE tombamento = %s;", (tombamento,))
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