from datetime import datetime


class SecurityValidator:
    # Whitelist de localizações válidas
    LOCALIZACOES_PERMITIDAS = ["Bancada", "PlayLan", "Sede"]

    @staticmethod
    def sanitizar_texto(texto: str) -> str:
        """Limpa espaços extras nas extremidades de entradas de texto."""
        if not texto:
            return ""
        return str(texto).strip()

    @staticmethod
    def validate_location(location: str) -> str:
        """Valida se a localização pertence à Whitelist."""
        if not location:
            return ""
        clean_loc = str(location).strip()
        if clean_loc not in SecurityValidator.LOCALIZACOES_PERMITIDAS:
            raise ValueError(
                f"Localização inválida. Opções permitidas: {', '.join(SecurityValidator.LOCALIZACOES_PERMITIDAS)}"
            )
        return clean_loc

    @staticmethod
    def validar_data(data_str: str) -> str:
        """
        Valida se a data enviada está correta e converte SEMPRE 
        para o formato ISO padrão do banco de dados (YYYY-MM-DD).
        """
        if not data_str or not str(data_str).strip():
            raise ValueError("A data não pode estar vazia.")

        data_limpa = str(data_str).strip()

        # Tenta converter os formatos comuns para YYYY-MM-DD
        for formato in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(data_limpa, formato)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        raise ValueError(
            f"Data '{data_limpa}' em formato inválido. Use o formato DD/MM/AAAA ou AAAA-MM-DD."
        )

    @staticmethod
    def validate_cost(value_str) -> float:
        """Valida e converte valores monetários sem permitir entradas negativas."""
        if value_str is None or value_str == "":
            return 0.0

        if isinstance(value_str, (int, float)):
            val = float(value_str)
        else:
            clean_value = str(value_str).replace("R$", "").strip()
            # Se contiver vírgula, trata como formato brasileiro (ex: 1.500,50 -> 1500.50)
            if "," in clean_value:
                clean_value = clean_value.replace(".", "").replace(",", ".")
            
            try:
                val = float(clean_value)
            except ValueError:
                raise ValueError("Valor de custo inválido.")

        if val < 0:
            raise ValueError("O valor do custo não pode ser negativo.")
        
        return round(val, 2)

    @staticmethod
    def mask_personal_data(name: str) -> str:
        """
        Função de conformidade com a LGPD:
        Anonimiza o nome de pessoas físicas ao gerar relatórios públicos.
        Exemplo: 'Carlos Eduardo' -> 'C***** E******'
        """
        if not name:
            return ""
        parts = str(name).strip().split()
        masked_parts = [p[0] + "*" * (len(p) - 1) if len(p) > 1 else p for p in parts]
        return " ".join(masked_parts)