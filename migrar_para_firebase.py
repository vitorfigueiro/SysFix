"""
Script CLI para migrar registros de um arquivo JSON local (coletas.json ou exportação Neon)
para uma empresa (tenant) no Cloud Firestore.

Uso:
    python migrar_para_firebase.py --arquivo coletas.json --tenant-id default --empresa "Minha Empresa"
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from config import is_firebase_configured
from firebase_backend import FirestoreColetaStore, FirestoreTenantUserStore
from security import SecurityValidator


def carregar_registros(caminho: Path) -> list[dict]:
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")
    conteudo = json.loads(caminho.read_text(encoding="utf-8"))
    if isinstance(conteudo, dict) and isinstance(conteudo.get("coletas"), list):
        return conteudo["coletas"]
    if isinstance(conteudo, list):
        return conteudo
    raise ValueError(
        "Formato JSON inválido. Esperado lista de coletas ou objeto {'coletas': [...]}."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migra registros locais do SysFix para o Cloud Firestore."
    )
    parser.add_argument(
        "--arquivo",
        required=True,
        help="Caminho do arquivo JSON local (coletas.json ou exportação Neon).",
    )
    parser.add_argument(
        "--tenant-id",
        default="default",
        help="ID da empresa (tenant) de destino no Firestore (padrão: default).",
    )
    parser.add_argument(
        "--empresa",
        default="Empresa Principal",
        help="Nome da empresa caso o documento do tenant precise ser criado.",
    )
    args = parser.parse_args()

    if not is_firebase_configured():
        raise RuntimeError(
            "Configure FIREBASE_SERVICE_ACCOUNT_PATH ou FIREBASE_SERVICE_ACCOUNT_JSON "
            "no arquivo .env antes de executar a migração."
        )

    registros = carregar_registros(Path(args.arquivo))
    tenant_store = FirestoreTenantUserStore()
    if not tenant_store.get_tenant(args.tenant_id):
        tenant_store.create_tenant(
            {
                "id": args.tenant_id,
                "nome": args.empresa.strip() or "Empresa Principal",
                "plano": "pro",
                "ativo": True,
                "sla_dias": 3,
                "localizacoes": list(SecurityValidator.LOCALIZACOES_PERMITIDAS[:4]),
                "criado_em": datetime.now(timezone.utc).isoformat(),
            }
        )

    coleta_store = FirestoreColetaStore()
    total = coleta_store.import_records(registros, tenant_id=args.tenant_id)
    print(
        f"Migração concluída: {total} registro(s) importado(s) para "
        f"tenants/{args.tenant_id}/coletas."
    )


if __name__ == "__main__":
    main()
