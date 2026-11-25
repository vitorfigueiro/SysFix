import base64
from datetime import datetime
import html
import io
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from models import ColetaModel
from security import SecurityValidator


class PDFReportGenerator:

    @staticmethod
    def _safe_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
        """Sanitiza caracteres especiais de XML/HTML para evitar quebras no ReportLab."""
        texto = "" if text is None else str(text).strip()
        clean_text = html.escape(texto or "-")
        return Paragraph(clean_text, style)

    @staticmethod
    def _gerar_pdf_buffer(
        titulo: str,
        dados: list,
        anonimizar: bool = False,
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        """Gera o arquivo PDF diretamente em um buffer de memória (BytesIO)."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(letter),
            rightMargin=20,
            leftMargin=20,
            topMargin=20,
            bottomMargin=20,
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "TitleStyle",
            parent=styles["Heading1"],
            fontSize=14,
            leading=18,
            alignment=1,
            textColor=colors.HexColor("#2E1065"),
            spaceAfter=10,
        )

        meta_style = ParagraphStyle(
            "MetaStyle",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#6B5E8E"),
            alignment=2,
        )

        cell_header_style = ParagraphStyle(
            "CellHeader",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            fontName="Helvetica-Bold",
            textColor=colors.whitesmoke,
        )

        cell_style = ParagraphStyle(
            "CellStyle",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#1E293B"),
        )

        elements.append(Paragraph(f"<b>{html.escape(titulo)}</b>", title_style))

        total_registros = len(dados)
        total_entregues = sum(
            1
            for reg in dados
            if str(reg.get("status") or "").strip().lower() in {"entregue", "finalizado"}
        )
        total_pendentes = total_registros - total_entregues
        custo_total = 0.0
        for reg in dados:
            raw_valor_resumo = reg.get("valor_custo")
            if raw_valor_resumo is None or raw_valor_resumo == "":
                raw_valor_resumo = reg.get("valor", 0.0)
            try:
                custo_total += float(raw_valor_resumo)
            except (ValueError, TypeError):
                pass
        custo_total_fmt = (
            f"R$ {custo_total:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        resumo_texto = (
            f"Emitido em: {datetime.now().strftime('%d/%m/%Y às %H:%M')} | "
            f"Total: {total_registros} | Entregues: {total_entregues} | "
            f"Pendentes: {total_pendentes}"
        )
        if not ocultar_custos:
            resumo_texto += f" | Custo Total: {custo_total_fmt}"

        elements.append(Paragraph(resumo_texto, meta_style))
        elements.append(Spacer(1, 10))

        if not dados:
            empty_style = ParagraphStyle(
                "EmptyStyle",
                parent=styles["Normal"],
                fontSize=10,
                leading=14,
                alignment=1,
                textColor=colors.HexColor("#64748B"),
                spaceBefore=24,
            )
            elements.append(
                Paragraph(
                    "Nenhum registro encontrado para o filtro selecionado.",
                    empty_style,
                )
            )
            doc.build(elements)
            buffer.seek(0)
            return buffer

        headers = [
            "ID",
            "Equipamento",
            "Tomb.",
            "Téc. Coleta",
            "Data",
            "Origem",
            "Status",
        ]
        if not ocultar_custos:
            headers.append("Custo")
        headers.extend(["Resolução", "Téc. Entrega"])
        table_data = [
            [
                PDFReportGenerator._safe_paragraph(h, cell_header_style)
                for h in headers
            ]
        ]

        for reg in dados:
            # Formatação de valores financeiros
            raw_valor = reg.get("valor_custo")
            if raw_valor is None or raw_valor == "":
                raw_valor = reg.get("valor", 0.0)
            try:
                val_num = float(raw_valor)
                val_fmt = (
                    f"R$ {val_num:,.2f}"
                    .replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )
            except (ValueError, TypeError):
                val_fmt = "R$ 0,00"

            # Formatação de datas
            raw_data = str(reg.get("data_coleta") or "")
            data_fmt = "-"
            if raw_data:
                try:
                    iso_date = SecurityValidator.validar_data(raw_data)
                    data_fmt = datetime.strptime(
                        iso_date, "%Y-%m-%d"
                    ).strftime("%d/%m/%Y")
                except ValueError:
                    data_fmt = raw_data

            # Trata nomes para exibição/anonimização
            tec_coleta = str(
                reg.get("tecnico_coleta") or reg.get("tecnico") or "-"
            )
            tec_entrega = str(reg.get("tecnico_entrega") or "-")

            if anonimizar:
                tec_coleta = SecurityValidator.mask_personal_data(tec_coleta)
                tec_entrega = SecurityValidator.mask_personal_data(tec_entrega)

            resolucao_txt = (
                reg.get("resolucao") or reg.get("servico_realizado") or "-"
            )

            row_cells = [
                PDFReportGenerator._safe_paragraph(reg.get("id"), cell_style),
                PDFReportGenerator._safe_paragraph(
                    reg.get("equipamento"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(
                    reg.get("tombamento"), cell_style
                ),
                PDFReportGenerator._safe_paragraph(tec_coleta, cell_style),
                PDFReportGenerator._safe_paragraph(data_fmt, cell_style),
                PDFReportGenerator._safe_paragraph(reg.get("origem"), cell_style),
                PDFReportGenerator._safe_paragraph(reg.get("status"), cell_style),
            ]
            if not ocultar_custos:
                row_cells.append(
                    PDFReportGenerator._safe_paragraph(val_fmt, cell_style)
                )
            row_cells.extend([
                PDFReportGenerator._safe_paragraph(resolucao_txt, cell_style),
                PDFReportGenerator._safe_paragraph(tec_entrega, cell_style),
            ])
            table_data.append(row_cells)

        # Larguras totalizam 752pt
        col_widths = (
            [28, 100, 55, 80, 55, 90, 60, 204, 80]
            if ocultar_custos
            else [28, 100, 55, 80, 55, 90, 60, 60, 144, 80]
        )
        t = Table(table_data, colWidths=col_widths, repeatRows=1)

        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4C1D95")),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]

        for i in range(1, len(table_data)):
            if i % 2 == 0:
                t_style.append((
                    "BACKGROUND",
                    (0, i),
                    (-1, i),
                    colors.HexColor("#F5F3FF"),
                ))

        t.setStyle(TableStyle(t_style))
        elements.append(t)

        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def relatorio_por_mes(
        cls,
        mes: int,
        ano: int,
        anonimizar: bool = False,
        tenant_id: str = "default",
        nome_empresa: str = "SysFix",
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        if isinstance(mes, bool) or not isinstance(mes, int) or not (1 <= mes <= 12):
            raise ValueError("Mês inválido. Informe um valor entre 1 e 12.")
        if isinstance(ano, bool) or not isinstance(ano, int) or not (2000 <= ano <= 2100):
            raise ValueError("Ano inválido. Informe um valor entre 2000 e 2100.")

        nome_meses = [
            "",
            "Janeiro",
            "Fevereiro",
            "Março",
            "Abril",
            "Maio",
            "Junho",
            "Julho",
            "Agosto",
            "Setembro",
            "Outubro",
            "Novembro",
            "Dezembro",
        ]
        dados = ColetaModel.buscar_por_mes_ano(mes, ano, tenant_id=tenant_id)
        prefixo = f"{nome_empresa} — " if nome_empresa else ""
        titulo = f"{prefixo}Relatório de Equipamentos - {nome_meses[mes]} / {ano}"
        return cls._gerar_pdf_buffer(
            titulo,
            dados,
            anonimizar=anonimizar,
            ocultar_custos=ocultar_custos,
        )

    @classmethod
    def relatorio_pendentes(
        cls,
        anonimizar: bool = False,
        tenant_id: str = "default",
        nome_empresa: str = "SysFix",
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        dados = ColetaModel.buscar_nao_finalizados_mes_atual(tenant_id=tenant_id)
        prefixo = f"{nome_empresa} — " if nome_empresa else ""
        titulo = f"{prefixo}Relatório de Equipamentos Pendentes / Em Manutenção"
        return cls._gerar_pdf_buffer(
            titulo,
            dados,
            anonimizar=anonimizar,
            ocultar_custos=ocultar_custos,
        )

    @classmethod
    def relatorio_geral(
        cls,
        anonimizar: bool = False,
        tenant_id: str = "default",
        nome_empresa: str = "SysFix",
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        dados = ColetaModel.buscar_todos(tenant_id=tenant_id)
        prefixo = f"{nome_empresa} — " if nome_empresa else ""
        titulo = f"{prefixo}Relatório Geral de Coletas e Manutenções"
        return cls._gerar_pdf_buffer(
            titulo,
            dados,
            anonimizar=anonimizar,
            ocultar_custos=ocultar_custos,
        )

    @staticmethod
    def _format_date_br(raw_val: str | None) -> str:
        texto = str(raw_val or "").strip()
        if not texto:
            return "-"
        try:
            iso = SecurityValidator.validar_data(texto)
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return texto

    @staticmethod
    def _format_moeda(raw_val) -> str:
        try:
            val = float(raw_val or 0.0)
        except (TypeError, ValueError):
            val = 0.0
        return (
            f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )

    @staticmethod
    def _safe_multiline_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
        bruto = "" if text is None else str(text).strip()
        if not bruto:
            return Paragraph("-", style)
        escaped = html.escape(bruto).replace("\r\n", "\n").replace("\n", "<br/>")
        return Paragraph(escaped, style)

    @staticmethod
    def _decode_rl_image(
        data_url: str | None, max_width: float, max_height: float
    ) -> RLImage | None:
        bruto = str(data_url or "").strip()
        if not bruto:
            return None
        try:
            b64_part = bruto.split(",", 1)[1].strip() if "," in bruto else bruto
            raw_bytes = base64.b64decode(b64_part)
            with PILImage.open(io.BytesIO(raw_bytes)) as img:
                w, h = img.size
                if w <= 0 or h <= 0:
                    return None
                png_buf = io.BytesIO()
                img.convert("RGBA").save(png_buf, format="PNG")
                png_buf.seek(0)
            scale = min(max_width / float(w), max_height / float(h), 1.0)
            return RLImage(png_buf, width=w * scale, height=h * scale)
        except Exception:
            return None

    @staticmethod
    def _cor_tema_hex(tenant: dict | None) -> str:
        cor = str((tenant or {}).get("cor_documento") or "#4C1D95").strip()
        if not cor.startswith("#") or len(cor) != 7:
            return "#4C1D95"
        try:
            colors.HexColor(cor)
            return cor
        except Exception:
            return "#4C1D95"

    @classmethod
    def _construir_cabecalho_timbrado(
        cls,
        tenant: dict | None,
        titulo_doc: str,
        subtitulo_doc: str,
        cor_hex: str,
    ) -> list:
        styles = getSampleStyleSheet()
        empresa = tenant or {}
        nome_emp = str(empresa.get("nome") or "SysFix").strip()
        detalhes = []
        if empresa.get("cnpj"):
            detalhes.append(f"CNPJ: {empresa['cnpj']}")
        if empresa.get("telefone"):
            detalhes.append(f"Tel: {empresa['telefone']}")
        if empresa.get("endereco"):
            detalhes.append(str(empresa["endereco"]))

        emp_style = ParagraphStyle(
            "TimbreEmp",
            parent=styles["Normal"],
            fontSize=12,
            leading=15,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(cor_hex),
        )
        info_style = ParagraphStyle(
            "TimbreInfo",
            parent=styles["Normal"],
            fontSize=8,
            leading=10.5,
            textColor=colors.HexColor("#475569"),
        )
        doc_title_style = ParagraphStyle(
            "TimbreDocTitle",
            parent=styles["Normal"],
            fontSize=10.5,
            leading=13,
            alignment=2,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(cor_hex),
        )
        doc_sub_style = ParagraphStyle(
            "TimbreDocSub",
            parent=styles["Normal"],
            fontSize=8,
            leading=10.5,
            alignment=2,
            textColor=colors.HexColor("#64748B"),
        )

        logo_img = cls._decode_rl_image(empresa.get("logo_base64"), 92, 42)
        bloco_emp = [Paragraph(html.escape(nome_emp), emp_style)]
        if detalhes:
            bloco_emp.append(Paragraph(html.escape(" • ".join(detalhes)), info_style))

        bloco_dir = [
            Paragraph(html.escape(titulo_doc), doc_title_style),
            Paragraph(html.escape(subtitulo_doc), doc_sub_style),
        ]

        if logo_img is not None:
            row = [[logo_img, bloco_emp, bloco_dir]]
            widths = [98, 237, 180]
        else:
            row = [[bloco_emp, bloco_dir]]
            widths = [315, 200]

        header_tbl = Table(row, colWidths=widths)
        header_tbl.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor(cor_hex)),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return [header_tbl, Spacer(1, 10)]

    @classmethod
    def comprovante_os(
        cls,
        record: dict,
        tenant: dict | None = None,
        ocultar_custos: bool = False,
    ) -> io.BytesIO:
        """Gera comprovante individual em PDF (A4) da Ordem de Serviço."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=40,
            leftMargin=40,
            topMargin=32,
            bottomMargin=32,
        )
        styles = getSampleStyleSheet()
        cor_hex = cls._cor_tema_hex(tenant)
        cor_clara = colors.HexColor("#F5F3FF")
        cor_borda = colors.HexColor("#DDD6FE")

        sec_style = ParagraphStyle(
            "SecTitle",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
            fontName="Helvetica-Bold",
            textColor=colors.whitesmoke,
        )
        label_style = ParagraphStyle(
            "LabelSt",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#475569"),
        )
        val_style = ParagraphStyle(
            "ValSt",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#0F172A"),
        )
        footer_style = ParagraphStyle(
            "FooterSt",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=10,
            alignment=1,
            textColor=colors.HexColor("#64748B"),
        )

        elements = cls._construir_cabecalho_timbrado(
            tenant,
            titulo_doc=f"COMPROVANTE DE O.S. #{record.get('id', '-')}",
            subtitulo_doc=(
                f"Código: {record.get('codigo_acompanhamento') or '-'} | "
                f"Emitido em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            ),
            cor_hex=cor_hex,
        )

        status_txt = str(record.get("status") or "Pendente")
        garantia_txt = (
            f"{record.get('garantia_dias')} dia(s) (até {cls._format_date_br(record.get('garantia_ate'))})"
            if int(record.get("garantia_dias") or 0) > 0
            else "Sem garantia cadastrada"
        )

        dados_equip = [
            [Paragraph("IDENTIFICAÇÃO DO EQUIPAMENTO & O.S.", sec_style), "", "", ""],
            [
                Paragraph("Equipamento:", label_style),
                cls._safe_paragraph(record.get("equipamento"), val_style),
                Paragraph("Categoria:", label_style),
                cls._safe_paragraph(record.get("categoria") or "Geral", val_style),
            ],
            [
                Paragraph("Tombamento:", label_style),
                cls._safe_paragraph(record.get("tombamento"), val_style),
                Paragraph("Serial / MAC:", label_style),
                cls._safe_paragraph(record.get("serial_mac") or "-", val_style),
            ],
            [
                Paragraph("Tipo de Fluxo:", label_style),
                cls._safe_paragraph(record.get("tipo_fluxo") or "Manutenção", val_style),
                Paragraph("Prioridade:", label_style),
                cls._safe_paragraph(record.get("prioridade") or "Normal", val_style),
            ],
            [
                Paragraph("Cliente / Origem:", label_style),
                cls._safe_paragraph(record.get("origem"), val_style),
                Paragraph("Status Atual:", label_style),
                cls._safe_paragraph(status_txt, val_style),
            ],
            [
                Paragraph("O.S. Coleta:", label_style),
                cls._safe_paragraph(record.get("os_coleta"), val_style),
                Paragraph("Data Coleta:", label_style),
                cls._safe_paragraph(cls._format_date_br(record.get("data_coleta")), val_style),
            ],
            [
                Paragraph("Téc. Coleta:", label_style),
                cls._safe_paragraph(record.get("tecnico_coleta") or record.get("tecnico"), val_style),
                Paragraph("Localização:", label_style),
                cls._safe_paragraph(record.get("localizacao"), val_style),
            ],
        ]
        t_equip = Table(dados_equip, colWidths=[85, 172, 85, 173])
        t_equip.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 0), (-1, 0)),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.extend([t_equip, Spacer(1, 10)])

        dados_atend = [
            [Paragraph("ATENDIMENTO, RESOLUÇÃO E ENTREGA", sec_style), ""],
            [
                Paragraph("Defeito / Problema Relatado:", label_style),
                cls._safe_multiline_paragraph(record.get("problema") or "Não informado.", val_style),
            ],
            [
                Paragraph("Serviço / Resolução:", label_style),
                cls._safe_multiline_paragraph(
                    record.get("resolucao") or record.get("servico_realizado") or "Em atendimento.",
                    val_style,
                ),
            ],
            [
                Paragraph("Entrega (O.S. / Data / Téc.):", label_style),
                cls._safe_paragraph(
                    f"O.S.: {record.get('os_entrega') or '-'} | Data: {cls._format_date_br(record.get('data_entrega'))} | Téc.: {record.get('tecnico_entrega') or '-'}",
                    val_style,
                ),
            ],
            [
                Paragraph("Garantia & Laudos:", label_style),
                cls._safe_paragraph(
                    f"{garantia_txt} | Laudado: {record.get('laudado') or 'Não'}",
                    val_style,
                ),
            ],
        ]
        if not ocultar_custos:
            custo_desc = (
                f"{record.get('status_custo') or 'Sem Custo'} — {cls._format_moeda(record.get('valor_custo'))}"
            )
            orc = record.get("orcamento")
            if isinstance(orc, dict) and orc.get("valor"):
                custo_desc += f" | Orçamento: {orc.get('status')} ({cls._format_moeda(orc.get('valor'))})"
            dados_atend.append(
                [Paragraph("Financeiro:", label_style), cls._safe_paragraph(custo_desc, val_style)]
            )

        t_atend = Table(dados_atend, colWidths=[140, 375])
        t_atend.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 0), (-1, 0)),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.extend([t_atend, Spacer(1, 10)])

        checklist = record.get("checklist") or []
        if checklist:
            chk_rows = [[Paragraph("CHECKLIST TÉCNICO DE BANCADA", sec_style), Paragraph("STATUS", sec_style)]]
            for item in checklist:
                feito = bool(item.get("concluido"))
                chk_rows.append(
                    [
                        cls._safe_paragraph(item.get("item"), val_style),
                        cls._safe_paragraph("OK / Verificado" if feito else "Pendente", val_style),
                    ]
                )
            t_chk = Table(chk_rows, colWidths=[415, 100])
            t_chk.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, cor_clara]),
                        ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            elements.extend([t_chk, Spacer(1, 12)])

        assinatura = record.get("assinatura_entrega")
        img_ass = (
            cls._decode_rl_image(assinatura.get("imagem_base64"), 165, 52)
            if isinstance(assinatura, dict)
            else None
        )
        tec_nome = record.get("tecnico_entrega") or record.get("tecnico_coleta") or "Responsável Técnico"
        rec_nome = (
            f"{assinatura.get('nome')} ({assinatura.get('documento') or 'Assinatura Digital'})"
            if isinstance(assinatura, dict) and assinatura.get("nome")
            else "Assinatura do Cliente / Recebedor"
        )
        ass_rows = [
            [
                Spacer(1, 28),
                img_ass if img_ass is not None else Spacer(1, 28),
            ],
            [
                Paragraph(f"______________________________________<br/>{html.escape(str(tec_nome))}", footer_style),
                Paragraph(f"______________________________________<br/>{html.escape(str(rec_nome))}", footer_style),
            ],
        ]
        t_ass = Table(ass_rows, colWidths=[257, 258])
        t_ass.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ]
            )
        )
        elements.extend([Spacer(1, 8), t_ass])

        rodape = (tenant or {}).get("rodape_documento") or (
            f"Documento gerado eletronicamente pelo SysFix — Acompanhe pelo código {record.get('codigo_acompanhamento') or '-'}"
        )
        elements.extend([Spacer(1, 14), Paragraph(html.escape(rodape), footer_style)])

        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def laudo_tecnico_pdf(
        cls,
        record: dict,
        laudo: dict,
        tenant: dict | None = None,
    ) -> io.BytesIO:
        """Gera Laudo ou Parecer Técnico em PDF (A4) com o design oficial (cabeçalho, logo, cores e rodapé idênticos ao DOCX)."""
        from pathlib import Path

        buffer = io.BytesIO()
        page_w, page_h = A4
        assets_dir = Path(__file__).resolve().parent / "static" / "laudo_assets"
        header_bg_path = assets_dir / "header_bg.png"
        logo_path = assets_dir / "logo_central.png"
        footer_bg_path = assets_dir / "footer_bg.png"

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=68,
            leftMargin=68,
            topMargin=132,
            bottomMargin=72,
        )
        styles = getSampleStyleSheet()
        cor_vermelha = colors.HexColor("#C81018")
        cor_clara = colors.HexColor("#FEF2F2")
        cor_borda = colors.HexColor("#FECACA")

        meses_pt = [
            "",
            "Janeiro",
            "Fevereiro",
            "Março",
            "Abril",
            "Maio",
            "Junho",
            "Julho",
            "Agosto",
            "Setembro",
            "Outubro",
            "Novembro",
            "Dezembro",
        ]

        data_dt = datetime.now()
        raw_dt = str(laudo.get("data_laudo") or laudo.get("emitido_em") or "").strip()
        if raw_dt:
            chunk = raw_dt.replace("T", " ").split(" ")[0]
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    data_dt = datetime.strptime(chunk, fmt)
                    break
                except ValueError:
                    continue

        data_hifen = data_dt.strftime("%d-%m-%Y")
        data_extenso = f"{data_dt.day:02d} de {meses_pt[data_dt.month]} de {data_dt.year}"

        seq_num = "01"
        raw_num = str(laudo.get("numero") or "").strip()
        if raw_num:
            partes_num = raw_num.rsplit("-", 1)
            if partes_num[-1].isdigit():
                seq_num = f"{int(partes_num[-1]):02d}"

        titulo_top_style = ParagraphStyle(
            "CartaTop",
            parent=styles["Normal"],
            fontSize=12,
            leading=16,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#0F172A"),
        )
        data_style = ParagraphStyle(
            "CartaData",
            parent=styles["Normal"],
            fontSize=11,
            leading=15.5,
            fontName="Helvetica",
            textColor=colors.HexColor("#1E293B"),
        )
        campo_style = ParagraphStyle(
            "CartaCampo",
            parent=styles["Normal"],
            fontSize=11,
            leading=16,
            fontName="Helvetica",
            textColor=colors.HexColor("#0F172A"),
        )
        secao_titulo_style = ParagraphStyle(
            "CartaSecTitulo",
            parent=styles["Normal"],
            fontSize=11,
            leading=15.5,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#0F172A"),
        )
        assin_center_style = ParagraphStyle(
            "CartaAssinCenter",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            alignment=1,
            fontName="Helvetica",
            textColor=colors.HexColor("#1E293B"),
        )
        sec_chk_style = ParagraphStyle(
            "LaudoSec",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            fontName="Helvetica-Bold",
            textColor=colors.whitesmoke,
        )
        body_chk_style = ParagraphStyle(
            "LaudoBodyChk",
            parent=styles["Normal"],
            fontSize=8.8,
            leading=12.5,
            textColor=colors.HexColor("#0F172A"),
        )

        def _desenhar_timbre_oficial(canvas_obj, _doc_obj):
            canvas_obj.saveState()
            # 1. Faixa diagonal vermelha + grafite do cabeçalho (idêntica ao DOCX)
            if header_bg_path.exists():
                canvas_obj.drawImage(
                    str(header_bg_path),
                    0,
                    page_h - 244,
                    width=page_w,
                    height=244,
                    mask="auto",
                )
            else:
                canvas_obj.setFillColor(cor_vermelha)
                canvas_obj.rect(0, page_h - 102, page_w, 102, fill=1, stroke=0)

            # 2. Textos brancos do cabeçalho sobre a faixa vermelha
            canvas_obj.setFillColor(colors.white)
            canvas_obj.setFont("Helvetica-Bold", 19.5)
            canvas_obj.drawString(28, page_h - 46, "CENTRAL INFORMÁTICA")
            canvas_obj.setFont("Helvetica", 13.5)
            canvas_obj.drawString(28, page_h - 68, "H & F SOLUÇÕES TECNOLÓGICAS LTDA EPP")

            # 3. Logo oficial Central Informática no canto superior direito
            if logo_path.exists():
                canvas_obj.drawImage(
                    str(logo_path),
                    page_w - 76,
                    page_h - 105,
                    width=45,
                    height=85,
                    preserveAspectRatio=True,
                    mask="auto",
                )

            # 4. Faixa diagonal grafite + vermelha do rodapé (idêntica ao DOCX)
            if footer_bg_path.exists():
                canvas_obj.drawImage(
                    str(footer_bg_path),
                    0,
                    0,
                    width=page_w,
                    height=44,
                    mask="auto",
                )
            else:
                canvas_obj.setFillColor(cor_vermelha)
                canvas_obj.rect(0, 0, page_w, 44, fill=1, stroke=0)

            # 5. Textos brancos do rodapé sobre a faixa vermelha inferior
            canvas_obj.setFillColor(colors.white)
            canvas_obj.setFont("Helvetica", 9.5)
            canvas_obj.drawRightString(
                page_w - 28, 25, "Rua: Paraná, 2509 - Centro – Espigão do Oeste– RO"
            )
            canvas_obj.drawRightString(
                page_w - 28, 11, "Fone: (69) 3481-2185 ou 3481-2828"
            )
            canvas_obj.restoreState()

        elements: list[Any] = []
        elements.append(Paragraph(f"Laudo {data_hifen} {seq_num}", titulo_top_style))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(f"Espigão do Oeste, {data_extenso}.", data_style))
        elements.append(Spacer(1, 18))

        cliente_para = (
            str(laudo.get("cliente_para") or "").strip()
            or str(record.get("origem") or "").strip()
            or "-"
        )
        assunto = str(laudo.get("titulo") or "Parecer Técnico").strip()
        equipamento_txt = (
            str(laudo.get("equipamento_laudo") or "").strip()
            or str(record.get("equipamento") or "").strip()
            or "-"
        )
        tomb_raw = str(laudo.get("tombamento_laudo") or "").strip()
        if not tomb_raw and str(record.get("tombamento_tipo") or "").lower() != "sem_tombamento":
            tomb_cand = str(record.get("tombamento") or "").strip()
            if tomb_cand.lower() not in {"", "-", "sem tombamento", "s/t", "0000"}:
                tomb_raw = tomb_cand
        if tomb_raw and tomb_raw.lower() not in {"sem tombamento", "s/t", "-"}:
            equipamento_linha = f"{equipamento_txt} — Tombamento: {tomb_raw}"
        else:
            equipamento_linha = equipamento_txt

        elements.append(Paragraph(f"<b>Para:</b> {html.escape(cliente_para)}", campo_style))
        elements.append(Spacer(1, 5))
        elements.append(Paragraph(f"<b>Assunto:</b> {html.escape(assunto)}", campo_style))
        elements.append(Spacer(1, 5))
        elements.append(Paragraph(f"<b>Equipamento:</b> {html.escape(equipamento_linha)}", campo_style))
        elements.append(Spacer(1, 16))

        prob_relatado = (
            str(laudo.get("diagnostico") or "").strip()
            or str(record.get("problema") or "").strip()
            or "-"
        )
        prob_identificado = str(laudo.get("parecer") or "").strip() or "-"
        conc_rec = (
            str(laudo.get("recomendacoes") or "").strip()
            or str(laudo.get("conclusao") or "").strip()
            or "-"
        )

        elements.append(Paragraph("Problema Relatado:", secao_titulo_style))
        elements.append(Spacer(1, 4))
        elements.append(cls._safe_multiline_paragraph(prob_relatado, campo_style))
        elements.append(Spacer(1, 14))

        elements.append(Paragraph("Problema Identificado:", secao_titulo_style))
        elements.append(Spacer(1, 4))
        elements.append(cls._safe_multiline_paragraph(prob_identificado, campo_style))
        elements.append(Spacer(1, 14))

        elements.append(Paragraph("Conclusão e Recomendação:", secao_titulo_style))
        elements.append(Spacer(1, 4))
        elements.append(cls._safe_multiline_paragraph(conc_rec, campo_style))
        elements.append(Spacer(1, 24))

        elements.append(Paragraph("Estamos à disposição para sanar qualquer dúvida.", campo_style))
        elements.append(Spacer(1, 14))
        elements.append(Paragraph("Atenciosamente,", campo_style))
        elements.append(Spacer(1, 34))

        tec_nome = str(laudo.get("tecnico_responsavel") or "").strip() or "Vitor Figueiró da Silva"
        tec_cargo = str(laudo.get("registro_profissional") or "").strip() or "T.I"
        tec_email = (
            str(laudo.get("email_tecnico") or "").strip()
            or "vitor.figueiro@centralinformatica.com.br"
        )
        tec_fone = "Fone: 69 3481-2185 | Cel.: 69 9926-2829"

        assin_html = (
            f"<b>{html.escape(tec_nome)}</b><br/>"
            f"{html.escape(tec_cargo)}<br/>"
            f"E-mail: {html.escape(tec_email)}<br/>"
            f"{html.escape(tec_fone)}"
        )
        t_assin = Table(
            [[Paragraph(assin_html, assin_center_style)]],
            colWidths=[284],
            hAlign="CENTER",
        )
        t_assin.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 0.9, colors.HexColor("#1E293B")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(t_assin)

        checklist = record.get("checklist") or []
        if laudo.get("incluir_checklist", False) and checklist:
            elements.append(Spacer(1, 20))
            chk_rows = [[Paragraph("CHECKLIST DE BANCADA", sec_chk_style), Paragraph("RESULTADO", sec_chk_style)]]
            for item in checklist:
                chk_rows.append(
                    [
                        cls._safe_paragraph(item.get("item"), body_chk_style),
                        cls._safe_paragraph("OK" if item.get("concluido") else "Não marcado", body_chk_style),
                    ]
                )
            t_chk = Table(chk_rows, colWidths=[360, 99])
            t_chk.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), cor_vermelha),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, cor_clara]),
                        ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            elements.append(t_chk)

        doc.build(
            elements,
            onFirstPage=_desenhar_timbre_oficial,
            onLaterPages=_desenhar_timbre_oficial,
        )
        buffer.seek(0)
        return buffer

    @classmethod
    def relatorio_auditoria_automacao(
        cls,
        ativos: list[dict],
        tenant: dict | None = None,
    ) -> io.BytesIO:
        """Gera relatório PDF timbrado da auditoria do setor Automação (CFTV, Infra, IoT, Alarmes)."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=32, bottomMargin=32
        )
        styles = getSampleStyleSheet()
        cor_hex = cls._cor_tema_hex(tenant)
        sec_style = ParagraphStyle(
            "AutSec", parent=styles["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.whitesmoke
        )
        cell_st = ParagraphStyle(
            "AutCell", parent=styles["Normal"], fontSize=8, leading=10.5, textColor=colors.HexColor("#1E293B")
        )
        footer_st = ParagraphStyle(
            "AutFooter", parent=styles["Normal"], fontSize=8, leading=10, alignment=1, textColor=colors.HexColor("#64748B")
        )

        total = len(ativos)
        conformes = sum(1 for a in ativos if a.get("status_auditoria") == "Conforme")
        criticos = sum(1 for a in ativos if a.get("status_auditoria") == "Crítico")
        taxa = round((conformes / total) * 100, 1) if total > 0 else 100.0

        elements = cls._construir_cabecalho_timbrado(
            tenant,
            titulo_doc="AUDITORIA DE AUTOMAÇÃO, CFTV & IOT",
            subtitulo_doc=f"Ativos: {total} | Conformidade: {taxa}% | Críticos: {criticos} • {datetime.now().strftime('%d/%m/%Y')}",
            cor_hex=cor_hex,
        )

        if not ativos:
            elements.append(Spacer(1, 16))
            elements.append(Paragraph("Nenhum ativo de automação cadastrado até o momento.", cell_st))
            doc.build(elements)
            buffer.seek(0)
            return buffer

        rows = [[
            Paragraph("Pilar", sec_style),
            Paragraph("Dispositivo / Local", sec_style),
            Paragraph("IP / MAC / VLAN", sec_style),
            Paragraph("Firmware / Modelo", sec_style),
            Paragraph("Status", sec_style),
        ]]
        for a in ativos:
            rows.append([
                cls._safe_paragraph(a.get("pilar"), cell_st),
                cls._safe_paragraph(f"{a.get('nome') or '-'} ({a.get('localizacao') or '-'})", cell_st),
                cls._safe_paragraph(f"{a.get('ip_mac') or '-'} / {a.get('vlan') or '-'}", cell_st),
                cls._safe_paragraph(f"{a.get('fabricante_modelo') or '-'} {a.get('firmware') or ''}", cell_st),
                cls._safe_paragraph(a.get("status_auditoria") or "Alerta", cell_st),
            ])

        tbl = Table(rows, colWidths=[72, 165, 115, 108, 63], repeatRows=1)
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F3FF")]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.extend([tbl, Spacer(1, 14)])
        rodape = (tenant or {}).get("rodape_documento") or "Relatório de Auditoria Técnica de Automação — SysFix"
        elements.append(Paragraph(html.escape(rodape), footer_st))
        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def relatorio_auditoria_cyber(
        cls,
        auditorias: list[dict],
        achados: list[dict],
        checklist: list[dict],
        tenant: dict | None = None,
    ) -> io.BytesIO:
        """Gera relatório executivo PDF de Cibersegurança, achados e controles CIS/NIST/LGPD."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=32, bottomMargin=32
        )
        styles = getSampleStyleSheet()
        cor_hex = cls._cor_tema_hex(tenant)
        sec_style = ParagraphStyle(
            "CybSec", parent=styles["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.whitesmoke
        )
        cell_st = ParagraphStyle(
            "CybCell", parent=styles["Normal"], fontSize=8, leading=10.5, textColor=colors.HexColor("#1E293B")
        )
        footer_st = ParagraphStyle(
            "CybFooter", parent=styles["Normal"], fontSize=8, leading=10, alignment=1, textColor=colors.HexColor("#64748B")
        )

        abertos = [a for a in achados if a.get("status") in {"Aberto", "Em Mitigação"}]
        conformes = sum(1 for c in checklist if c.get("status") == "Conforme")

        elements = cls._construir_cabecalho_timbrado(
            tenant,
            titulo_doc="RELATÓRIO EXECUTIVO DE CIBERSEGURANÇA",
            subtitulo_doc=f"Auditorias: {len(auditorias)} | Achados Abertos: {len(abertos)} | Controles Conformes: {conformes}/{len(checklist)}",
            cor_hex=cor_hex,
        )

        if achados:
            ach_rows = [[
                Paragraph("Sev.", sec_style),
                Paragraph("Achado / Risco", sec_style),
                Paragraph("Alvo", sec_style),
                Paragraph("Recomendação", sec_style),
                Paragraph("Status", sec_style),
            ]]
            for a in achados[:35]:
                ach_rows.append([
                    cls._safe_paragraph(a.get("severidade"), cell_st),
                    cls._safe_paragraph(a.get("titulo"), cell_st),
                    cls._safe_paragraph(a.get("alvo") or "-", cell_st),
                    cls._safe_paragraph(a.get("recomendacao") or "-", cell_st),
                    cls._safe_paragraph(a.get("status"), cell_st),
                ])
            t_ach = Table(ach_rows, colWidths=[52, 155, 85, 173, 58], repeatRows=1)
            t_ach.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F3FF")]),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            elements.extend([t_ach, Spacer(1, 12)])

        if checklist:
            chk_rows = [[
                Paragraph("Framework", sec_style),
                Paragraph("Controle de Segurança", sec_style),
                Paragraph("Status", sec_style),
            ]]
            for c in checklist:
                chk_rows.append([
                    cls._safe_paragraph(c.get("framework"), cell_st),
                    cls._safe_paragraph(c.get("controle"), cell_st),
                    cls._safe_paragraph(c.get("status"), cell_st),
                ])
            t_chk = Table(chk_rows, colWidths=[95, 345, 83], repeatRows=1)
            t_chk.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F3FF")]),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            elements.extend([t_chk, Spacer(1, 12)])

        rodape = (tenant or {}).get("rodape_documento") or "Relatório de Postura e Auditoria Defensiva — SysFix"
        elements.append(Paragraph(html.escape(rodape), footer_st))
        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def recibo_venda_loja(
        cls,
        venda: dict,
        tenant: dict | None = None,
    ) -> io.BytesIO:
        """Gera Recibo de Venda PDV + Certificado de Garantia com Serial/IMEI."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, rightMargin=38, leftMargin=38, topMargin=32, bottomMargin=32
        )
        styles = getSampleStyleSheet()
        cor_hex = cls._cor_tema_hex(tenant)
        cor_clara = colors.HexColor("#F5F3FF")
        cor_borda = colors.HexColor("#DDD6FE")

        sec_style = ParagraphStyle(
            "LojaRecSec", parent=styles["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.whitesmoke
        )
        lbl_style = ParagraphStyle(
            "LojaRecLbl", parent=styles["Normal"], fontSize=8, leading=10, fontName="Helvetica-Bold", textColor=colors.HexColor("#475569")
        )
        val_style = ParagraphStyle(
            "LojaRecVal", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#0F172A")
        )
        footer_style = ParagraphStyle(
            "LojaRecFoot", parent=styles["Normal"], fontSize=7.5, leading=10, alignment=1, textColor=colors.HexColor("#64748B")
        )

        codigo = str(venda.get("codigo") or venda.get("id") or "VND")
        elements = cls._construir_cabecalho_timbrado(
            tenant,
            titulo_doc=f"COMPROVANTE DE VENDA & GARANTIA ({codigo})",
            subtitulo_doc=(
                f"Doc: {venda.get('tipo_documento') or 'Recibo Interno'}"
                + (f" Nº {venda.get('numero_nf')}" if venda.get("numero_nf") else "")
                + f" • Data: {cls._format_date_br(venda.get('data_venda'))}"
            ),
            cor_hex=cor_hex,
        )

        info_rows = [
            [Paragraph("DADOS DO CLIENTE E PAGAMENTO", sec_style), "", "", ""],
            [
                Paragraph("Cliente:", lbl_style),
                cls._safe_paragraph(venda.get("cliente_nome") or "Consumidor Final", val_style),
                Paragraph("CPF / CNPJ:", lbl_style),
                cls._safe_paragraph(venda.get("cliente_doc") or "-", val_style),
            ],
            [
                Paragraph("Vendedor:", lbl_style),
                cls._safe_paragraph(venda.get("vendedor") or "-", val_style),
                Paragraph("Filial / Tabela:", lbl_style),
                cls._safe_paragraph(
                    f"{venda.get('filial') or 'Matriz'} ({str(venda.get('tabela_preco') or 'varejo').upper()})",
                    val_style,
                ),
            ],
            [
                Paragraph("Pagamento:", lbl_style),
                cls._safe_paragraph(
                    f"{venda.get('forma_pagamento') or 'Pix'} ({venda.get('parcelas') or 1}x)",
                    val_style,
                ),
                Paragraph("Status:", lbl_style),
                cls._safe_paragraph(venda.get("status") or "Concluída", val_style),
            ],
        ]
        t_info = Table(info_rows, colWidths=[78, 180, 82, 175])
        t_info.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 0), (-1, 0)),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.extend([t_info, Spacer(1, 10)])

        it_rows = [[
            Paragraph("Produto / Serviço", sec_style),
            Paragraph("Serial / IMEI", sec_style),
            Paragraph("Garantia", sec_style),
            Paragraph("Qtd", sec_style),
            Paragraph("Unit.", sec_style),
            Paragraph("Subtotal", sec_style),
        ]]
        for it in venda.get("itens") or []:
            gar = (
                f"{it.get('garantia_meses')}m (até {cls._format_date_br(it.get('garantia_ate'))})"
                if int(it.get("garantia_meses") or 0) > 0
                else "Sem garantia"
            )
            it_rows.append([
                cls._safe_paragraph(it.get("nome"), val_style),
                cls._safe_paragraph(it.get("serial_imei") or "-", val_style),
                cls._safe_paragraph(gar, val_style),
                cls._safe_paragraph(str(it.get("quantidade") or 1), val_style),
                cls._safe_paragraph(cls._format_moeda(it.get("preco_unitario")), val_style),
                cls._safe_paragraph(cls._format_moeda(it.get("total_item")), val_style),
            ])
        t_itens = Table(it_rows, colWidths=[175, 95, 90, 30, 62, 63], repeatRows=1)
        t_itens.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, cor_clara]),
                    ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.extend([t_itens, Spacer(1, 10)])

        tot_rows = [
            [
                Paragraph("Subtotal:", lbl_style),
                cls._safe_paragraph(cls._format_moeda(venda.get("subtotal")), val_style),
                Paragraph("Desconto:", lbl_style),
                cls._safe_paragraph(cls._format_moeda(venda.get("desconto")), val_style),
                Paragraph("TOTAL PAGO:", lbl_style),
                Paragraph(f"<b>{html.escape(cls._format_moeda(venda.get('total')))}</b>", val_style),
            ]
        ]
        t_tot = Table(tot_rows, colWidths=[58, 100, 58, 95, 72, 132])
        t_tot.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), cor_clara),
                    ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor(cor_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.4, cor_borda),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.extend([t_tot, Spacer(1, 10)])

        if venda.get("observacoes"):
            elements.append(cls._safe_multiline_paragraph(f"Observações: {venda.get('observacoes')}", val_style))
            elements.append(Spacer(1, 8))

        elements.append(
            Paragraph(
                "<b>TERMO DE GARANTIA:</b> A garantia cobre exclusivamente defeitos de fabricação pelo prazo indicado ao lado de cada item, "
                "mediante conferência do Número de Série / IMEI registrado neste documento e preservação dos lacres originais. "
                "Não cobre mau uso, oxidação, descarga elétrica ou violação física.",
                footer_style,
            )
        )
        rodape = (tenant or {}).get("rodape_documento") or f"Comprovante comercial {codigo} — SysFix Loja de TI"
        elements.extend([Spacer(1, 12), Paragraph(html.escape(rodape), footer_style)])
        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def orcamento_loja(
        cls,
        orc: dict,
        tenant: dict | None = None,
    ) -> io.BytesIO:
        """Gera Proposta Comercial / Orçamento de Montagem de PC em PDF."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, rightMargin=38, leftMargin=38, topMargin=32, bottomMargin=32
        )
        styles = getSampleStyleSheet()
        cor_hex = cls._cor_tema_hex(tenant)
        cor_clara = colors.HexColor("#F5F3FF")
        cor_borda = colors.HexColor("#DDD6FE")

        sec_style = ParagraphStyle(
            "LojaOrcSec", parent=styles["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.whitesmoke
        )
        lbl_style = ParagraphStyle(
            "LojaOrcLbl", parent=styles["Normal"], fontSize=8, leading=10, fontName="Helvetica-Bold", textColor=colors.HexColor("#475569")
        )
        val_style = ParagraphStyle(
            "LojaOrcVal", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#0F172A")
        )
        footer_style = ParagraphStyle(
            "LojaOrcFoot", parent=styles["Normal"], fontSize=7.5, leading=10, alignment=1, textColor=colors.HexColor("#64748B")
        )

        codigo = str(orc.get("codigo") or "ORC")
        elements = cls._construir_cabecalho_timbrado(
            tenant,
            titulo_doc=f"PROPOSTA COMERCIAL / ORÇAMENTO ({codigo})",
            subtitulo_doc=f"Validade: {cls._format_date_br(orc.get('valido_ate'))} • Status: {orc.get('status') or 'Enviado'}",
            cor_hex=cor_hex,
        )

        info_rows = [
            [Paragraph("IDENTIFICAÇÃO DA PROPOSTA", sec_style), "", "", ""],
            [
                Paragraph("Título:", lbl_style),
                cls._safe_paragraph(orc.get("titulo"), val_style),
                Paragraph("Tipo:", lbl_style),
                cls._safe_paragraph(orc.get("tipo"), val_style),
            ],
            [
                Paragraph("Cliente:", lbl_style),
                cls._safe_paragraph(orc.get("cliente_nome"), val_style),
                Paragraph("CPF/CNPJ/Tel:", lbl_style),
                cls._safe_paragraph(
                    " • ".join(p for p in (orc.get("cliente_doc"), orc.get("cliente_telefone")) if p) or "-",
                    val_style,
                ),
            ],
            [
                Paragraph("Consultor:", lbl_style),
                cls._safe_paragraph(orc.get("vendedor") or "-", val_style),
                Paragraph("Condições:", lbl_style),
                cls._safe_paragraph(orc.get("condicoes_pagamento") or "-", val_style),
            ],
        ]
        t_info = Table(info_rows, colWidths=[72, 185, 78, 180])
        t_info.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 0), (-1, 0)),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.extend([t_info, Spacer(1, 10)])

        it_rows = [[
            Paragraph("Componente / Serviço", sec_style),
            Paragraph("Garantia", sec_style),
            Paragraph("Qtd", sec_style),
            Paragraph("Unitário", sec_style),
            Paragraph("Total", sec_style),
        ]]
        for it in orc.get("itens") or []:
            it_rows.append([
                cls._safe_paragraph(it.get("nome"), val_style),
                cls._safe_paragraph(f"{it.get('garantia_meses') or 0} meses", val_style),
                cls._safe_paragraph(str(it.get("quantidade") or 1), val_style),
                cls._safe_paragraph(cls._format_moeda(it.get("preco_unitario")), val_style),
                cls._safe_paragraph(cls._format_moeda(it.get("total_item")), val_style),
            ])
        t_itens = Table(it_rows, colWidths=[265, 65, 35, 75, 75], repeatRows=1)
        t_itens.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, cor_clara]),
                    ("GRID", (0, 0), (-1, -1), 0.5, cor_borda),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.extend([t_itens, Spacer(1, 10)])

        tot_rows = [[
            Paragraph("Subtotal:", lbl_style),
            cls._safe_paragraph(cls._format_moeda(orc.get("subtotal")), val_style),
            Paragraph("Desconto:", lbl_style),
            cls._safe_paragraph(cls._format_moeda(orc.get("desconto")), val_style),
            Paragraph("TOTAL PROPOSTA:", lbl_style),
            Paragraph(f"<b>{html.escape(cls._format_moeda(orc.get('total')))}</b>", val_style),
        ]]
        t_tot = Table(tot_rows, colWidths=[55, 95, 55, 90, 95, 125])
        t_tot.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), cor_clara),
                    ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor(cor_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.4, cor_borda),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.extend([t_tot, Spacer(1, 12)])

        if orc.get("observacoes"):
            elements.append(cls._safe_multiline_paragraph(f"Observações: {orc.get('observacoes')}", val_style))
            elements.append(Spacer(1, 10))

        t_ass = Table(
            [
                [Spacer(1, 22), Spacer(1, 22)],
                [
                    Paragraph("______________________________________<br/>Consultor / Loja", footer_style),
                    Paragraph("______________________________________<br/>Aprovação do Cliente", footer_style),
                ],
            ],
            colWidths=[257, 258],
        )
        t_ass.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
        elements.append(t_ass)

        rodape = (tenant or {}).get("rodape_documento") or f"Proposta {codigo} gerada via SysFix Loja de TI."
        elements.extend([Spacer(1, 12), Paragraph(html.escape(rodape), footer_style)])
        doc.build(elements)
        buffer.seek(0)
        return buffer

    @classmethod
    def relatorio_gerencial_loja(
        cls,
        resumo: dict,
        vendas: list[dict],
        rmas: list[dict],
        tenant: dict | None = None,
        incluir_custos: bool = True,
    ) -> io.BytesIO:
        """Gera Relatório Gerencial Consolidado da Loja de TI (Vendas, Curva ABC e RMAs)."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=32, bottomMargin=32
        )
        styles = getSampleStyleSheet()
        cor_hex = cls._cor_tema_hex(tenant)
        sec_style = ParagraphStyle(
            "LojaRelSec", parent=styles["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.whitesmoke
        )
        cell_st = ParagraphStyle(
            "LojaRelCell", parent=styles["Normal"], fontSize=8, leading=10.5, textColor=colors.HexColor("#1E293B")
        )
        footer_st = ParagraphStyle(
            "LojaRelFoot", parent=styles["Normal"], fontSize=8, leading=10, alignment=1, textColor=colors.HexColor("#64748B")
        )

        sub = f"Vendas no mês: {resumo.get('total_vendas_mes', 0)} | Orç. Abertos: {resumo.get('orcamentos_abertos', 0)} | RMAs Abertos: {resumo.get('rmas_abertos', 0)}"
        if incluir_custos:
            sub += f" | Fat. Mês: {cls._format_moeda(resumo.get('faturamento_mes', 0))}"

        elements = cls._construir_cabecalho_timbrado(
            tenant,
            titulo_doc="RELATÓRIO GERENCIAL — LOJA DE TI",
            subtitulo_doc=sub,
            cor_hex=cor_hex,
        )

        curva = resumo.get("curva_abc") or []
        if curva:
            abc_rows = [[
                Paragraph("Classe ABC", sec_style),
                Paragraph("Produto / Item", sec_style),
                Paragraph("Qtd Vendida", sec_style),
                Paragraph("Participação", sec_style),
                Paragraph("Receita" if incluir_custos else "Status", sec_style),
            ]]
            for item in curva:
                abc_rows.append([
                    cls._safe_paragraph(f"Classe {item.get('classe')}", cell_st),
                    cls._safe_paragraph(item.get("nome"), cell_st),
                    cls._safe_paragraph(str(item.get("quantidade") or 0), cell_st),
                    cls._safe_paragraph(f"{item.get('participacao_percent') or 0}%", cell_st),
                    cls._safe_paragraph(
                        cls._format_moeda(item.get("receita")) if incluir_custos else "Ativo",
                        cell_st,
                    ),
                ])
            t_abc = Table(abc_rows, colWidths=[68, 225, 65, 72, 93], repeatRows=1)
            t_abc.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F3FF")]),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            elements.extend([t_abc, Spacer(1, 12)])

        if vendas:
            v_rows = [[
                Paragraph("Código", sec_style),
                Paragraph("Cliente", sec_style),
                Paragraph("Pagamento", sec_style),
                Paragraph("Vendedor", sec_style),
                Paragraph("Total" if incluir_custos else "Status", sec_style),
            ]]
            for v in vendas[:30]:
                v_rows.append([
                    cls._safe_paragraph(v.get("codigo"), cell_st),
                    cls._safe_paragraph(v.get("cliente_nome"), cell_st),
                    cls._safe_paragraph(v.get("forma_pagamento"), cell_st),
                    cls._safe_paragraph(v.get("vendedor"), cell_st),
                    cls._safe_paragraph(
                        cls._format_moeda(v.get("total")) if incluir_custos else v.get("status"),
                        cell_st,
                    ),
                ])
            t_v = Table(v_rows, colWidths=[92, 165, 100, 88, 78], repeatRows=1)
            t_v.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F3FF")]),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            elements.extend([t_v, Spacer(1, 12)])

        if rmas:
            r_rows = [[
                Paragraph("RMA", sec_style),
                Paragraph("Produto / Serial", sec_style),
                Paragraph("Cliente / Fornecedor", sec_style),
                Paragraph("Status", sec_style),
            ]]
            for r in rmas[:20]:
                r_rows.append([
                    cls._safe_paragraph(r.get("codigo"), cell_st),
                    cls._safe_paragraph(f"{r.get('produto')} ({r.get('serial_imei') or '-'})", cell_st),
                    cls._safe_paragraph(r.get("cliente_fornecedor") or "-", cell_st),
                    cls._safe_paragraph(r.get("status"), cell_st),
                ])
            t_r = Table(r_rows, colWidths=[85, 185, 148, 105], repeatRows=1)
            t_r.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(cor_hex)),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F3FF")]),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD6FE")),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            elements.extend([t_r, Spacer(1, 12)])

        rodape = (tenant or {}).get("rodape_documento") or "Relatório Gerencial de Loja de TI — SysFix"
        elements.append(Paragraph(html.escape(rodape), footer_st))
        doc.build(elements)
        buffer.seek(0)
        return buffer


def gerar_recibo_venda_pdf(venda: dict, tenant: dict | None = None) -> io.BytesIO:
    return PDFReportGenerator.recibo_venda_loja(venda, tenant=tenant)


def gerar_orcamento_loja_pdf(orcamento: dict, tenant: dict | None = None) -> io.BytesIO:
    return PDFReportGenerator.orcamento_loja(orcamento, tenant=tenant)


def gerar_relatorio_loja_pdf(
    resumo: dict,
    vendas: list[dict],
    rmas: list[dict],
    tenant: dict | None = None,
    incluir_custos: bool = True,
) -> io.BytesIO:
    return PDFReportGenerator.relatorio_gerencial_loja(
        resumo=resumo,
        vendas=vendas,
        rmas=rmas,
        tenant=tenant,
        incluir_custos=incluir_custos,
    )