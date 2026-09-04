import sys
from database import get_connection, init_db


def testar():
    print("Conectando ao banco de testes PostgreSQL...")
    conn = None
    try:
        # Inicializa o banco e roda as migrações necessárias
        init_db()
        print("✅ Migração/Schema executado com sucesso!")

        # Validação: verifica se as colunas existem no schema do PostgreSQL
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE LOWER(table_name) = 'coletas';
        """
        )

        colunas = [row[0].lower() for row in cursor.fetchall()]
        cursor.close()

        print(f"Colunas encontradas na tabela 'coletas' ({len(colunas)}):")
        print(colunas)

        # Checa colunas críticas criadas na migração
        colunas_criticas = ["os_coleta", "os_entrega", "data_entrega"]
        for col in colunas_criticas:
            assert (
                col in colunas
            ), f"Erro: coluna '{col}' não foi encontrada na tabela!"

        print("🚀 Validação concluída! Todas as colunas críticas existem.")

    except Exception as e:
        print(f"❌ Falha no teste de migração: {e}")
        sys.exit(1)
    finally:
        if conn and not conn.closed:
            conn.close()


if __name__ == "__main__":
    testar()