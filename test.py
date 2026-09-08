import sys
import traceback
from database import get_connection, init_db


def testar():
    print("Conectando ao banco de testes PostgreSQL (Neon)...")
    conn = None
    try:
        # 1. Inicializa o banco e executa as migrações/criações necessárias
        init_db()
        print("✅ Migração/Schema executado com sucesso!")

        # 2. Conecta para validar a tabela no schema
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE LOWER(table_name) = 'coletas';
            """
        )

        resultados = cursor.fetchall()
        cursor.close()

        # Trata os dados de forma compatível com RealDictCursor (dicionário) ou Tupla
        colunas = []
        for row in resultados:
            if isinstance(row, dict):
                colunas.append(row["column_name"].lower())
            else:
                colunas.append(row[0].lower())

        print(f"\nColunas encontradas na tabela 'coletas' ({len(colunas)}):")
        print(colunas)

        if not colunas:
            print("\n⚠️ AVISO: A tabela 'coletas' não foi encontrada ou está sem colunas no Neon.")
            print("Verifique se o nome da tabela no Neon é exatamente 'coletas'.")
            sys.exit(1)

        # 3. Checa se as colunas críticas existem
        colunas_criticas = ["os_coleta", "os_entrega", "data_entrega"]
        for col in colunas_criticas:
            assert (
                col in colunas
            ), f"Coluna crítica '{col}' NÃO foi encontrada no banco!"

        print("\n🚀 Validação concluída com sucesso! Todas as colunas existem no Neon.")

    except Exception as e:
        print(f"\n❌ Falha no teste de migração: {e}")
        print("\n🔍 Detalhes do erro:")
        traceback.print_exc()
        sys.exit(1)
    finally:
        if conn and not conn.closed:
            conn.close()


if __name__ == "__main__":
    testar()