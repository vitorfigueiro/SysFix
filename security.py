import html
from datetime import datetime


class SecurityValidator:
    # Whitelist de localizações válidas (evita injeção de valores arbitrários)
    LOCALIZACOES_PERMITIDAS = ["Bancada", "PlayLan", "Sede"]

    @staticmethod
    def sanitizar_texto(texto: str) -> str:
        """Sanitiza e limpa entradas de texto genéricas."""
        if not texto:
            return ""
        return html.escape(str(texto).strip())

    @staticmethod
    def validate_location(location: str) -> str:
        """Valida se a localização pertence à Whitelist."""
        clean_loc = str(location).strip()
        if clean_loc not in SecurityValidator.LOCALIZACOES_PERMITIDAS:
            raise ValueError(
                f"Localização inválida. Opções permitidas: {', '.join(SecurityValidator.LOCALIZACOES_PERMITIDAS)}"
            )
        return clean_loc

    @staticmethod
    def validar_data(data_str: str) -> str:
        """Valida se a data enviada está no formato correto (DD/MM/AAAA ou AAAA-MM-DD)."""
        if not data_str or not str(data_str).strip():
            raise ValueError("A data não pode estar vazia.")

        data_limpa = str(data_str).strip()

        # Tenta validar no formato DD/MM/AAAA
        for formato in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                datetime.strptime(data_limpa, formato)
                return data_limpa
            except ValueError:
                continue

        raise ValueError(
            f"Data '{data_limpa}' em formato inválido. Use o formato DD/MM/AAAA."
        )

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