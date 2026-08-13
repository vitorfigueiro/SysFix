import html
from datetime import datetime


class SecurityValidator:
    # Whitelist de localizações válidas (evita injeção de valores arbitrários)
    LOCALIZACOES_PERMITIDAS = ["Almoxarifado", "Lab 01", "Lab 02", "Manutenção", "Recepção"]

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Sanitiza strings para evitar XSS e caracteres maliciosos."""
        if not text:
            return ""
        clean_text = text.strip()
        return html.escape(clean_text)

    @staticmethod
    def validate_date(date_str: str) -> bool:
        """Garante que a data siga o formato seguro YYYY-MM-DD."""
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    @staticmethod
    def validate_cost(value_str: str) -> float:
        """Valida e converte valores monetários sem permitir entradas
        invalidas."""
        try:
            clean_value = str(value_str).replace("R$", "").replace(".", "").replace(",", ".").strip()
            val = float(clean_value)
            if val < 0:
                raise ValueError("O valor do custo não pode ser negativo.")
            return val
        except ValueError:
            raise ValueError("Valor de custo inválido.")

    @staticmethod
    def validate_location(location: str) -> str:
        """Valida se a localização pertence à Whitelist."""
        clean_loc = location.strip()
        if clean_loc not in SecurityValidator.LOCALIZACOES_PERMITIDAS:
            raise ValueError(f"Localização inválida. Permitidas: {', '.join(SecurityValidator.LOCALIZACOES_PERMITIDAS)}")
        return clean_loc

    @staticmethod
    def mask_personal_data(name: str) -> str:
        """
        Função de conformidade com LGPD:
        Anonimiza o nome de pessoas físicas/técnicos ao gerar relatórios públicos.
        Exemplo: 'Carlos Eduardo' -> 'C***** E******'
        """
        if not name:
            return ""
        parts = name.split()
        masked_parts = [p[0] + "*" * (len(p) - 1) if len(p) > 1 else p for p in parts]
        return " ".join(masked_parts)