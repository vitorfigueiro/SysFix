from auth import get_tenant_user_store
from storage import get_store


def init_db() -> None:
    """Inicializa os armazenamentos ativos (local JSON ou Firestore)."""
    get_store().ensure()
    get_tenant_user_store().ensure()
