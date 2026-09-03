import json
import os
import tkinter as tk

from datetime import date, datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
import zipfile

from database import init_db
from models import ColetaModel
from reports import PDFReportGenerator
from security import SecurityValidator
from updater import verificar_e_atualizar

CONFIG_FILE = "config.json"


class ConfigManager:
    """Gerencia as configurações persistentes da aplicação (ex: pasta de backup)"""

    @staticmethod
    def load_config():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    @staticmethod
    def save_config(data):
        config = ConfigManager.load_config()
        config.update(data)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)


class BackupManager:
    """Gerencia a verificação/notificação de diretórios no modelo PostgreSQL"""

    @staticmethod
    def criar_backup(destino_dir) -> str:
        if not os.path.exists(destino_dir):
            os.makedirs(destino_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        nome_zip = f"backup_info_{timestamp}.zip"
        caminho_zip = os.path.join(destino_dir, nome_zip)

        # Como o banco PostgreSQL é remoto, geramos um arquivo de log/status de encerramento
        with zipfile.ZipFile(caminho_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
            info_text = (
                f"Sessão encerrada em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Banco PostgreSQL gerenciado remotamente."
            )
            zipf.writestr("status_sessao.txt", info_text)

        return caminho_zip


class Application(tk.Tk):
    ADMIN_PASSWORD = "admin123"

    def __init__(self):
        super().__init__()
        self.title(
            "Sistema de Gestão e Controle de Equipamentos (PostgreSQL)"
        )
        self.geometry("1480x850")
        self.minsize(1200, 700)

        # Configurações do App
        self.config_data = ConfigManager.load_config()
        self.backup_dir = self.config_data.get("backup_dir", "")

        # Paleta de Cores Profissional
        self.colors = {
            "bg_app": "#F0F4F8",
            "bg_card": "#FFFFFF",
            "primary": "#1E40AF",
            "primary_hover": "#1E3A8A",
            "secondary": "#3B82F6",
            "accent_light": "#EFF6FF",
            "success_bg": "#E0F2FE",
            "success_fg": "#0369A1",
            "warning_bg": "#FEF3C7",
            "warning_fg": "#B45309",
            "danger": "#DC2626",
            "danger_hover": "#991B1B",
            "text_main": "#0F172A",
            "text_muted": "#475569",
            "border": "#CBD5E1",
        }

        self.configure(bg=self.colors["bg_app"])
        self.setup_styles()

        self.registro_selecionado_id = None
        self.todos_registros_cache = []
        self.filtro_ativo = "nao_finalizados_mes"

        self.create_widgets()
        self.carregar_dados_tabela()

        # Intercepta o fechamento
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TFrame", background=self.colors["bg_app"])
        style.configure(
            "Card.TFrame", background=self.colors["bg_card"], relief="flat"
        )

        style.configure(
            "TLabelframe",
            background=self.colors["bg_card"],
            relief="solid",
            borderwidth=1,
            bordercolor=self.colors["border"],
        )
        style.configure(
            "TLabelframe.Label",
            font=("Segoe UI", 10, "bold"),
            foreground=self.colors["primary"],
            background=self.colors["bg_card"],
        )

        style.configure(
            "TLabel",
            background=self.colors["bg_card"],
            foreground=self.colors["text_main"],
        )
        style.configure(
            "TEntry",
            fieldbackground="#FFFFFF",
            foreground=self.colors["text_main"],
            bordercolor=self.colors["border"],
        )
        style.map("TEntry", bordercolor=[("focus", self.colors["primary"])])

        style.configure(
            "TButton",
            font=("Segoe UI", 9, "bold"),
            padding=7,
            relief="flat",
            borderwidth=0,
            background="#E2E8F0",
            foreground=self.colors["text_main"],
        )
        style.map("TButton", background=[("active", "#CBD5E1")])

        style.configure(
            "Primary.TButton",
            background=self.colors["primary"],
            foreground="white",
        )
        style.map(
            "Primary.TButton",
            background=[("active", self.colors["primary_hover"])],
        )

        style.configure(
            "Danger.TButton",
            background=self.colors["danger"],
            foreground="white",
        )
        style.map(
            "Danger.TButton",
            background=[("active", self.colors["danger_hover"])],
        )

        style.configure(
            "Treeview",
            font=("Segoe UI", 9),
            rowheight=30,
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground=self.colors["text_main"],
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background="#E0F2FE",
            foreground=self.colors["primary"],
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", self.colors["primary"])],
            foreground=[("selected", "#FFFFFF")],
        )

        style.configure(
            "TNotebook",
            background=self.colors["bg_app"],
            tabmargins=[2, 5, 2, 0],
        )
        style.configure(
            "TNotebook.Tab",
            font=("Segoe UI", 9, "bold"),
            padding=[14, 7],
            background="#94A3B8",
            foreground="#FFFFFF",
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.colors["primary"])],
            foreground=[("selected", "#FFFFFF")],
        )

    def autenticar_admin(self) -> bool:
        senha = simpledialog.askstring(
            "Autenticação de Responsável",
            "🔒 Apenas o responsável tem permissão para esta ação.\nDigite a senha de administrador:",
            show="*",
            parent=self,
        )
        if senha is None:
            return False
        if senha == self.ADMIN_PASSWORD:
            return True
        else:
            messagebox.showerror(
                "Acesso Negado", "Senha incorreta! Operação cancelada."
            )
            return False

    def create_widgets(self):
        main_container = ttk.Frame(self, padding=12)
        main_container.pack(fill="both", expand=True)

        # PAINEL ESQUERDO: FORMULÁRIOS & PAINEL ADM
        left_panel = ttk.Frame(main_container, width=390)
        left_panel.pack(side="left", fill="y", padx=(0, 10))

        self.notebook = ttk.Notebook(left_panel)
        self.notebook.pack(fill="both", expand=True)

        # --- ABA ENTRADA ---
        tab_entrada = ttk.Frame(
            self.notebook, style="Card.TFrame", padding=12
        )
        self.notebook.add(tab_entrada, text="📥 Entrada")

        ttk.Label(
            tab_entrada, text="Equipamento:", font=("Segoe UI", 9, "bold")
        ).grid(row=0, column=0, sticky="w", pady=4)
        self.entry_equip = ttk.Entry(tab_entrada, width=26)
        self.entry_equip.grid(row=0, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_entrada, text="Tombamento:", font=("Segoe UI", 9, "bold")
        ).grid(row=1, column=0, sticky="w", pady=4)
        self.entry_tombamento = ttk.Entry(tab_entrada, width=26)
        self.entry_tombamento.grid(row=1, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_entrada, text="Técnico Coleta:", font=("Segoe UI", 9, "bold")
        ).grid(row=2, column=0, sticky="w", pady=4)
        self.entry_tecnico = ttk.Entry(tab_entrada, width=26)
        self.entry_tecnico.grid(row=2, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_entrada, text="Data Coleta:", font=("Segoe UI", 9, "bold")
        ).grid(row=3, column=0, sticky="w", pady=4)
        self.entry_data = ttk.Entry(tab_entrada, width=26)
        self.entry_data.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.entry_data.grid(row=3, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_entrada, text="Origem:", font=("Segoe UI", 9, "bold")
        ).grid(row=4, column=0, sticky="w", pady=4)
        self.entry_origem = ttk.Entry(tab_entrada, width=26)
        self.entry_origem.grid(row=4, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_entrada, text="O.S de Coleta:", font=("Segoe UI", 9, "bold")
        ).grid(row=5, column=0, sticky="w", pady=4)
        self.entry_os_coleta = ttk.Entry(tab_entrada, width=26)
        self.entry_os_coleta.grid(row=5, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_entrada, text="Localização:", font=("Segoe UI", 9, "bold")
        ).grid(row=6, column=0, sticky="w", pady=4)
        self.combo_localizacao = ttk.Combobox(
            tab_entrada,
            values=SecurityValidator.LOCALIZACOES_PERMITIDAS,
            state="readonly",
            width=24,
        )
        self.combo_localizacao.set(
            SecurityValidator.LOCALIZACOES_PERMITIDAS[0]
        )
        self.combo_localizacao.grid(row=6, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_entrada, text="Problema:", font=("Segoe UI", 9, "bold")
        ).grid(row=7, column=0, sticky="nw", pady=4)
        self.text_problema = tk.Text(
            tab_entrada,
            width=20,
            height=3,
            font=("Segoe UI", 9),
            relief="solid",
            bd=1,
            highlightbackground=self.colors["border"],
        )
        self.text_problema.grid(row=7, column=1, pady=4, sticky="ew")

        ttk.Button(
            tab_entrada,
            text="➕ Registrar Entrada",
            style="Primary.TButton",
            command=self.salvar_entrada,
        ).grid(row=8, column=0, columnspan=2, pady=(12, 4), sticky="ew")
        ttk.Button(
            tab_entrada,
            text="🔒 ✏️ Editar Dados Entrada (Resp.)",
            command=self.atualizar_entrada,
        ).grid(row=9, column=0, columnspan=2, pady=2, sticky="ew")

        # --- ABA SAÍDA ---
        tab_saida = ttk.Frame(self.notebook, style="Card.TFrame", padding=12)
        self.notebook.add(tab_saida, text="📤 Saída")

        self.lbl_item_selecionado = ttk.Label(
            tab_saida,
            text="⚠️ Nenhum item selecionado",
            font=("Segoe UI", 9, "bold"),
            foreground=self.colors["danger"],
        )
        self.lbl_item_selecionado.grid(
            row=0, column=0, columnspan=2, pady=(0, 10), sticky="w"
        )

        ttk.Label(
            tab_saida, text="Técnico Entrega:", font=("Segoe UI", 9, "bold")
        ).grid(row=1, column=0, sticky="w", pady=4)
        self.entry_tecnico_entrega = ttk.Entry(tab_saida, width=26)
        self.entry_tecnico_entrega.grid(row=1, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_saida, text="O.S de Entrega:", font=("Segoe UI", 9, "bold")
        ).grid(row=2, column=0, sticky="w", pady=4)
        self.entry_os_entrega = ttk.Entry(tab_saida, width=26)
        self.entry_os_entrega.grid(row=2, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_saida, text="Data Entrega:", font=("Segoe UI", 9, "bold")
        ).grid(row=3, column=0, sticky="w", pady=4)
        self.entry_data_entrega = ttk.Entry(tab_saida, width=26)
        self.entry_data_entrega.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.entry_data_entrega.grid(row=3, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_saida, text="Laudado?", font=("Segoe UI", 9, "bold")
        ).grid(row=4, column=0, sticky="w", pady=4)
        self.combo_laudado = ttk.Combobox(
            tab_saida, values=["Não", "Sim"], state="readonly", width=24
        )
        self.combo_laudado.set("Não")
        self.combo_laudado.grid(row=4, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_saida, text="Status Custo:", font=("Segoe UI", 9, "bold")
        ).grid(row=5, column=0, sticky="w", pady=4)
        self.combo_status_saida = ttk.Combobox(
            tab_saida,
            values=["Sem Custo", "Com Custo"],
            state="readonly",
            width=24,
        )
        self.combo_status_saida.set("Sem Custo")
        self.combo_status_saida.grid(row=5, column=1, pady=4, sticky="ew")
        self.combo_status_saida.bind(
            "<<ComboboxSelected>>", self.on_status_change
        )

        ttk.Label(
            tab_saida, text="Valor Custo (R$):", font=("Segoe UI", 9, "bold")
        ).grid(row=6, column=0, sticky="w", pady=4)
        self.entry_valor_custo = ttk.Entry(tab_saida, width=26)
        self.entry_valor_custo.insert(0, "0,00")
        self.entry_valor_custo.configure(state="disabled")
        self.entry_valor_custo.grid(row=6, column=1, pady=4, sticky="ew")

        ttk.Label(
            tab_saida, text="Resolução:", font=("Segoe UI", 9, "bold")
        ).grid(row=7, column=0, sticky="nw", pady=4)
        self.text_resolucao = tk.Text(
            tab_saida,
            width=20,
            height=3,
            font=("Segoe UI", 9),
            relief="solid",
            bd=1,
            highlightbackground=self.colors["border"],
        )
        self.text_resolucao.grid(row=7, column=1, pady=4, sticky="ew")

        ttk.Button(
            tab_saida,
            text="✅ Finalizar & Marcar Entregue",
            style="Primary.TButton",
            command=self.salvar_saida,
        ).grid(row=8, column=0, columnspan=2, pady=12, sticky="ew")

        # Botões de Ação Inferiores
        frame_acoes = ttk.Frame(left_panel, padding=(0, 6, 0, 0))
        frame_acoes.pack(fill="x")
        ttk.Button(
            frame_acoes,
            text="🔒 🗑️ Excluir (Resp.)",
            style="Danger.TButton",
            command=self.excluir_registro,
        ).pack(side="left", expand=True, fill="x", padx=(0, 2))
        ttk.Button(
            frame_acoes, text="🧹 Limpar", command=self.limpar_formularios
        ).pack(side="left", expand=True, fill="x", padx=(2, 0))

        # --- SEÇÃO DE RELATÓRIOS E BACKUP ---
        frame_reports = ttk.LabelFrame(
            left_panel, text=" Exportação & Relatórios ", padding=8
        )
        frame_reports.pack(fill="x", pady=(6, 0))

        ttk.Button(
            frame_reports,
            text="📄 Gerar Relatório Mensal (PDF)",
            style="Primary.TButton",
            command=self.abrir_janela_relatorio,
        ).pack(fill="x", pady=(0, 4))
        ttk.Button(
            frame_reports,
            text="💾 Gerar Arquivo de Backup Manual",
            command=self.executar_backup_manual,
        ).pack(fill="x", pady=2)
        ttk.Button(
            frame_reports,
            text="⚙️ Selecionar Pasta de Backup",
            command=self.configurar_pasta_backup,
        ).pack(fill="x", pady=2)

        self.lbl_backup_path = ttk.Label(
            frame_reports,
            text=f"Destino: {self.backup_dir if self.backup_dir else 'Não configurado'}",
            font=("Segoe UI", 8),
            foreground=self.colors["text_muted"],
            wraplength=350,
        )
        self.lbl_backup_path.pack(fill="x", pady=(4, 0))

        # PAINEL CENTRAL: LISTA COMPACTA
        list_panel = ttk.LabelFrame(
            main_container, text=" Painel de Equipamentos ", padding=8
        )
        list_panel.pack(side="left", fill="both", padx=(0, 10))

        frame_filtros = ttk.Frame(list_panel)
        frame_filtros.pack(fill="x", pady=(0, 6))
        ttk.Button(
            frame_filtros,
            text="⏳ Pendentes",
            command=lambda: self.filtrar_tabela("nao_finalizados_mes"),
        ).pack(side="left", expand=True, fill="x", padx=1)
        ttk.Button(
            frame_filtros,
            text="✅ Finalizados",
            command=lambda: self.filtrar_tabela("finalizados_mes"),
        ).pack(side="left", expand=True, fill="x", padx=1)
        ttk.Button(
            frame_filtros,
            text="📋 Todos",
            command=lambda: self.filtrar_tabela("todos"),
        ).pack(side="left", expand=True, fill="x", padx=1)

        columns = ("id", "equipamento", "tombamento", "status")
        self.tree = ttk.Treeview(
            list_panel, columns=columns, show="headings", selectmode="browse"
        )

        self.tree.heading("id", text="ID")
        self.tree.heading("equipamento", text="Equipamento")
        self.tree.heading("tombamento", text="Tombamento")
        self.tree.heading("status", text="Status")

        self.tree.column("id", width=40, anchor="center")
        self.tree.column("equipamento", width=115)
        self.tree.column("tombamento", width=85)
        self.tree.column("status", width=85, anchor="center")

        scroll_y = ttk.Scrollbar(
            list_panel, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<<TreeviewSelect>>", self.on_item_select)

        # PAINEL DIREITO: FICHA TÉCNICA VISUAL
        detail_container = ttk.LabelFrame(
            main_container, text=" Ficha Técnica do Equipamento ", padding=12
        )
        detail_container.pack(side="right", fill="both", expand=True)

        self.canvas_detalhes = tk.Canvas(
            detail_container, bg=self.colors["bg_app"], highlightthickness=0
        )
        scroll_detalhes = ttk.Scrollbar(
            detail_container,
            orient="vertical",
            command=self.canvas_detalhes.yview,
        )

        self.frame_ficha = tk.Frame(
            self.canvas_detalhes, bg=self.colors["bg_app"]
        )
        self.frame_ficha.bind(
            "<Configure>",
            lambda e: self.canvas_detalhes.configure(
                scrollregion=self.canvas_detalhes.bbox("all")
            ),
        )

        self.canvas_detalhes.create_window(
            (0, 0), window=self.frame_ficha, anchor="nw"
        )
        self.canvas_detalhes.configure(yscrollcommand=scroll_detalhes.set)

        scroll_detalhes.pack(side="right", fill="y")
        self.canvas_detalhes.pack(side="left", fill="both", expand=True)

        self.exibir_detalhes_vazio()

    # --- LÓGICA DE BACKUP & GERENCIAMENTO ---
    def configurar_pasta_backup(self):
        if not self.autenticar_admin():
            return

        caminho = filedialog.askdirectory(
            title="Selecione a Pasta para Armazenar os Backups"
        )
        if caminho:
            self.backup_dir = caminho
            ConfigManager.save_config({"backup_dir": caminho})
            self.lbl_backup_path.config(text=f"Destino: {caminho}")
            messagebox.showinfo(
                "Sucesso",
                f"Diretório de backup configurado com sucesso:\n{caminho}",
            )

    def executar_backup_manual(self):
        if not self.backup_dir:
            messagebox.showwarning(
                "Aviso",
                "Por favor, defina primeiro a pasta onde os backups serão salvos no botão abaixo.",
            )
            return

        try:
            zip_criado = BackupManager.criar_backup(self.backup_dir)
            messagebox.showinfo(
                "Backup Concluído", f"Backup gerado com sucesso:\n{zip_criado}"
            )
        except Exception as e:
            messagebox.showerror(
                "Erro no Backup", f"Ocorreu uma falha ao gerar o backup: {str(e)}"
            )

    def on_closing(self):
        if self.backup_dir and os.path.exists(self.backup_dir):
            try:
                BackupManager.criar_backup(self.backup_dir)
            except Exception as e:
                print(f"Erro no fechamento: {e}")
        self.destroy()

    def _add_field(
        self, parent, row, icon_label, value_text, is_full_width=False
    ):
        lbl = tk.Label(
            parent,
            text=icon_label,
            font=("Segoe UI", 9),
            fg=self.colors["text_muted"],
            bg="#FFFFFF",
            anchor="w",
        )
        lbl.grid(row=row, column=0, sticky="w", pady=3, padx=(10, 5))

        val = tk.Label(
            parent,
            text=value_text,
            font=("Segoe UI", 9, "bold"),
            fg=self.colors["text_main"],
            bg="#FFFFFF",
            anchor="w",
            justify="left",
            wraplength=350 if is_full_width else 220,
        )
        val.grid(row=row, column=1, sticky="w", pady=3, padx=(0, 10))

    def renderizar_ficha(self, reg):
        for widget in self.frame_ficha.winfo_children():
            widget.destroy()

        # Conversão de data (aceita objetos date do PostgreSQL ou strings)
        raw_data = reg[4]
        if isinstance(raw_data, (date, datetime)):
            data_br = raw_data.strftime("%d/%m/%Y")
        else:
            try:
                data_br = datetime.strptime(
                    str(raw_data), "%Y-%m-%d"
                ).strftime("%d/%m/%Y")
            except Exception:
                data_br = str(raw_data)

        valor_custo = float(reg[10]) if len(reg) > 10 and reg[10] is not None else 0.0
        val_fmt = (
            f"R$ {valor_custo:,.2f}".replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
        status = reg[8] if len(reg) > 8 else "Pendente"

        card_header = tk.Frame(self.frame_ficha, bg="#FFFFFF", relief="flat")
        card_header.configure(
            highlightbackground=self.colors["border"], highlightthickness=1
        )
        card_header.pack(fill="x", pady=(0, 10), ipady=8, ipadx=10)

        lbl_id = tk.Label(
            card_header,
            text=f"REGISTRO #{reg[0]}",
            font=("Segoe UI", 9, "bold"),
            fg=self.colors["secondary"],
            bg="#FFFFFF",
        )
        lbl_id.pack(anchor="w")

        lbl_titulo = tk.Label(
            card_header,
            text=str(reg[1]).upper(),
            font=("Segoe UI", 13, "bold"),
            fg=self.colors["primary"],
            bg="#FFFFFF",
        )
        lbl_titulo.pack(anchor="w", pady=(2, 6))

        is_entregue = status == "Entregue"
        bg_badge = (
            self.colors["success_bg"]
            if is_entregue
            else self.colors["warning_bg"]
        )
        fg_badge = (
            self.colors["success_fg"]
            if is_entregue
            else self.colors["warning_fg"]
        )
        txt_badge = (
            "  ✔ ENTREGUE  " if is_entregue else "  ⏳ PENDENTE DE ENTREGA  "
        )

        lbl_badge = tk.Label(
            card_header,
            text=txt_badge,
            font=("Segoe UI", 9, "bold"),
            fg=fg_badge,
            bg=bg_badge,
            padx=8,
            pady=3,
        )
        lbl_badge.pack(anchor="w")

        card_tech = tk.Frame(self.frame_ficha, bg="#FFFFFF", relief="flat")
        card_tech.configure(
            highlightbackground=self.colors["border"], highlightthickness=1
        )
        card_tech.pack(fill="x", pady=(0, 10), ipadx=10, ipady=8)

        tk.Label(
            card_tech,
            text="IDENTIFICAÇÃO E LOCALIZAÇÃO",
            font=("Segoe UI", 9, "bold"),
            fg=self.colors["primary"],
            bg="#FFFFFF",
        ).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 8)
        )
        self._add_field(card_tech, 1, "🏷️ Tombamento:", str(reg[2]))
        self._add_field(card_tech, 2, "📍 Localização:", str(reg[7]) if len(reg) > 7 else "N/A")

        card_entrada = tk.Frame(self.frame_ficha, bg="#FFFFFF", relief="flat")
        card_entrada.configure(
            highlightbackground=self.colors["border"], highlightthickness=1
        )
        card_entrada.pack(fill="x", pady=(0, 10), ipadx=10, ipady=8)

        tk.Label(
            card_entrada,
            text="DADOS DE ENTRADA / COLETA",
            font=("Segoe UI", 9, "bold"),
            fg=self.colors["primary"],
            bg="#FFFFFF",
        ).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 8)
        )
        self._add_field(card_entrada, 1, "👤 Técnico Coleta:", str(reg[3]))
        self._add_field(card_entrada, 2, "📅 Data da Coleta:", data_br)
        self._add_field(card_entrada, 3, "🏢 Unidade Origem:", str(reg[5]))
        self._add_field(card_entrada, 4, "📑 O.S de Coleta:", str(reg[6]))
        self._add_field(
            card_entrada,
            5,
            "⚠️ Problema Relatado:",
            reg[8] if len(reg) > 8 and reg[8] else "Nenhum defeito relatado",
            is_full_width=True,
        )

        card_saida = tk.Frame(self.frame_ficha, bg="#FFFFFF", relief="flat")
        card_saida.configure(
            highlightbackground=self.colors["border"], highlightthickness=1
        )
        card_saida.pack(fill="x", pady=(0, 10), ipadx=10, ipady=8)

        tk.Label(
            card_saida,
            text="RESOLUÇÃO E ENTREGA",
            font=("Segoe UI", 9, "bold"),
            fg=self.colors["primary"],
            bg="#FFFFFF",
        ).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 8)
        )
        self._add_field(
            card_saida, 1, "📄 Laudo Técnico:", reg[14] if len(reg) > 14 and reg[14] else "Não"
        )
        self._add_field(
            card_saida,
            2,
            "👤 Téc. Resp. Entrega:",
            reg[11] if len(reg) > 11 and reg[11] else "Pendente",
        )
        self._add_field(
            card_saida, 3, "💰 Status de Custo:", f"{reg[15] if len(reg) > 15 else 'Sem Custo'} ({val_fmt})"
        )
        self._add_field(
            card_saida,
            4,
            "🛠️ Serviço / Resolução:",
            reg[16] if len(reg) > 16 and reg[16] else "Em andamento / Aguardando manutenção",
            is_full_width=True,
        )
        self._add_field(
            card_saida, 5, "📅 Data da Entrega:", str(reg[13]) if len(reg) > 13 and reg[13] else "N/A"
        )

    def exibir_detalhes_vazio(self):
        for widget in self.frame_ficha.winfo_children():
            widget.destroy()

        card_empty = tk.Frame(
            self.frame_ficha, bg="#FFFFFF", padx=30, pady=30
        )
        card_empty.configure(
            highlightbackground=self.colors["border"], highlightthickness=1
        )
        card_empty.pack(fill="both", expand=True, pady=40)

        lbl = tk.Label(
            card_empty,
            text="👈 Selecione um equipamento na lista ao lado\npara visualizar a ficha técnica completa.",
            font=("Segoe UI", 10),
            fg=self.colors["text_muted"],
            bg="#FFFFFF",
            justify="center",
        )
        lbl.pack(expand=True)

    def on_status_change(self, event=None):
        if self.combo_status_saida.get() == "Com Custo":
            self.entry_valor_custo.configure(state="normal")
        else:
            self.entry_valor_custo.delete(0, tk.END)
            self.entry_valor_custo.insert(0, "0,00")
            self.entry_valor_custo.configure(state="disabled")

    def on_item_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return

        item = self.tree.item(selected[0])
        self.registro_selecionado_id = item["values"][0]

        reg = next(
            (
                r
                for r in self.todos_registros_cache
                if r[0] == self.registro_selecionado_id
            ),
            None,
        )
        if not reg:
            return

        self.lbl_item_selecionado.config(
            text=f"Selecionado: #{reg[0]} {reg[1]}",
            foreground=self.colors["success_fg"],
        )

        self.renderizar_ficha(reg)

        self.entry_equip.delete(0, tk.END)
        self.entry_equip.insert(0, str(reg[1]))
        self.entry_tombamento.delete(0, tk.END)
        self.entry_tombamento.insert(0, str(reg[2]))
        self.entry_tecnico.delete(0, tk.END)
        self.entry_tecnico.insert(0, str(reg[3]))

        self.entry_data.delete(0, tk.END)
        self.entry_data.insert(0, str(reg[4]))

        self.entry_origem.delete(0, tk.END)
        self.entry_origem.insert(0, str(reg[5]))

        self.entry_os_coleta.delete(0, tk.END)
        self.entry_os_coleta.insert(0, str(reg[6]))

        if len(reg) > 7 and reg[7] in SecurityValidator.LOCALIZACOES_PERMITIDAS:
            self.combo_localizacao.set(reg[7])

        self.text_problema.delete("1.0", tk.END)
        if len(reg) > 8 and reg[8]:
            self.text_problema.insert("1.0", str(reg[8]))

        if len(reg) > 14 and reg[14] in ["Não", "Sim"]:
            self.combo_laudado.set(reg[14])

        st_custo = (
            reg[15] if len(reg) > 15 and reg[15] in ["Sem Custo", "Com Custo"] else "Sem Custo"
        )
        self.combo_status_saida.set(st_custo)
        self.on_status_change()

        if st_custo == "Com Custo" and len(reg) > 10:
            self.entry_valor_custo.delete(0, tk.END)
            self.entry_valor_custo.insert(0, str(reg[10]))

        self.text_resolucao.delete("1.0", tk.END)
        if len(reg) > 16 and reg[16]:
            self.text_resolucao.insert("1.0", str(reg[16]))

        self.entry_tecnico_entrega.delete(0, tk.END)
        if len(reg) > 11 and reg[11]:
            self.entry_tecnico_entrega.insert(0, str(reg[11]))

    def carregar_dados_tabela(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        if self.filtro_ativo == "nao_finalizados_mes":
            self.todos_registros_cache = (
                ColetaModel.buscar_nao_finalizados_mes_atual()
            )
        elif self.filtro_ativo == "finalizados_mes":
            self.todos_registros_cache = (
                ColetaModel.buscar_finalizados_mes_atual()
            )
        else:
            self.todos_registros_cache = ColetaModel.buscar_todos()

        for reg in self.todos_registros_cache:
            st_exibicao = reg[9] if len(reg) > 9 and reg[9] else "Pendente"
            self.tree.insert(
                "", "end", values=(reg[0], reg[1], reg[2], st_exibicao)
            )

    def filtrar_tabela(self, tipo_filtro):
        self.filtro_ativo = tipo_filtro
        self.limpar_formularios()
        self.carregar_dados_tabela()

    def salvar_entrada(self):
        try:
            registro_id = ColetaModel.registrar_entrada(
                self.entry_equip.get(),
                self.entry_tombamento.get(),
                self.entry_tecnico.get(),
                self.entry_data.get(),
                self.entry_origem.get(),
                self.entry_os_coleta.get(),
                self.combo_localizacao.get(),
                self.text_problema.get("1.0", tk.END).strip(),
            )
            messagebox.showinfo(
                "Sucesso", f"Entrada ID #{registro_id} registrada com sucesso!"
            )
            self.limpar_formularios()
            self.carregar_dados_tabela()
        except Exception as e:
            messagebox.showerror("Erro de Validação", str(e))

    def atualizar_entrada(self):
        if not self.registro_selecionado_id:
            messagebox.showwarning(
                "Aviso", "Selecione um registro na lista para editar."
            )
            return

        if not self.autenticar_admin():
            return

        try:
            ColetaModel.atualizar_entrada(
                self.registro_selecionado_id,
                self.entry_equip.get(),
                self.entry_tombamento.get(),
                self.entry_tecnico.get(),
                self.entry_data.get(),
                self.entry_origem.get(),
                self.entry_os_coleta.get(),
                self.combo_localizacao.get(),
                self.text_problema.get("1.0", tk.END).strip(),
            )
            messagebox.showinfo("Sucesso", "Dados de entrada atualizados!")
            self.carregar_dados_tabela()
        except Exception as e:
            messagebox.showerror("Erro na Atualização", str(e))

    def salvar_saida(self):
        if not self.registro_selecionado_id:
            messagebox.showwarning(
                "Aviso", "Selecione um registro para registrar a saída."
            )
            return

        try:
            ColetaModel.registrar_saida(
                self.registro_selecionado_id,
                self.entry_tecnico_entrega.get(),
                self.entry_os_entrega.get(),
                self.entry_data_entrega.get(),
                self.combo_laudado.get(),
                self.combo_status_saida.get(),
                self.entry_valor_custo.get(),
                self.text_resolucao.get("1.0", tk.END).strip(),
            )
            messagebox.showinfo("Sucesso", "Saída/Entrega registrada com sucesso!")
            self.limpar_formularios()
            self.carregar_dados_tabela()
        except Exception as e:
            messagebox.showerror("Erro ao registrar saída", str(e))

    def excluir_registro(self):
        if not self.registro_selecionado_id:
            messagebox.showwarning("Aviso", "Selecione um registro para excluir.")
            return

        if not self.autenticar_admin():
            return

        if messagebox.askyesno(
            "Confirmar Exclusão",
            f"Tem certeza que deseja excluir o registro #{self.registro_selecionado_id}?",
        ):
            try:
                ColetaModel.excluir(self.registro_selecionado_id)
                messagebox.showinfo("Sucesso", "Registro excluído com sucesso!")
                self.limpar_formularios()
                self.carregar_dados_tabela()
            except Exception as e:
                messagebox.showerror("Erro ao Excluir", str(e))

    def limpar_formularios(self):
        self.registro_selecionado_id = None
        self.lbl_item_selecionado.config(
            text="⚠️ Nenhum item selecionado",
            foreground=self.colors["danger"],
        )

        # Entrada
        self.entry_equip.delete(0, tk.END)
        self.entry_tombamento.delete(0, tk.END)
        self.entry_tecnico.delete(0, tk.END)
        self.entry_data.delete(0, tk.END)
        self.entry_data.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.entry_origem.delete(0, tk.END)
        self.entry_os_coleta.delete(0, tk.END)
        if SecurityValidator.LOCALIZACOES_PERMITIDAS:
            self.combo_localizacao.set(SecurityValidator.LOCALIZACOES_PERMITIDAS[0])
        self.text_problema.delete("1.0", tk.END)

        # Saída
        self.entry_tecnico_entrega.delete(0, tk.END)
        self.entry_os_entrega.delete(0, tk.END)
        self.entry_data_entrega.delete(0, tk.END)
        self.entry_data_entrega.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.combo_laudado.set("Não")
        self.combo_status_saida.set("Sem Custo")
        self.on_status_change()
        self.text_resolucao.delete("1.0", tk.END)

        self.exibir_detalhes_vazio()

    def abrir_janela_relatorio(self):
        ano_atual = datetime.now().year
        mes_atual = datetime.now().month

        mes = simpledialog.askinteger(
            "Relatório Mensal",
            "Mês do relatório (1-12):",
            initialvalue=mes_atual,
            minvalue=1,
            maxvalue=12,
            parent=self,
        )
        if not mes:
            return

        ano = simpledialog.askinteger(
            "Relatório Mensal",
            "Ano do relatório:",
            initialvalue=ano_atual,
            minvalue=2000,
            maxvalue=2100,
            parent=self,
        )
        if not ano:
            return

        caminho_salvar = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=f"Relatorio_{mes:02d}_{ano}.pdf",
            title="Salvar Relatório PDF",
        )

        if caminho_salvar:
            try:
                PDFReportGenerator.gerar_relatorio_mensal(mes, ano, caminho_salvar)
                messagebox.showinfo(
                    "Sucesso", f"Relatório gerado com sucesso em:\n{caminho_salvar}"
                )
            except Exception as e:
                messagebox.showerror("Erro ao Gerar PDF", str(e))


if __name__ == "__main__":
    init_db()
    verificar_e_atualizar()
    app = Application()
    app.mainloop()