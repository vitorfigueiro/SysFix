import os
from datetime import datetime
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from models import ColetaModel

class PDFReportGenerator:
    @staticmethod
    def _gerar_pdf(caminho_arquivo, titulo, dados):
        doc = SimpleDocTemplate(
            caminho_arquivo, 
            pagesize=landscape(letter),
            rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20
        )
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontSize=15,
            alignment=1,
            spaceAfter=15
        )

        elements.append(Paragraph(f"<b>{titulo}</b>", title_style))
        elements.append(Paragraph(f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
        elements.append(Spacer(1, 15))

        table_data = [["ID", "Equipamento", "Tomb.", "Téc. Coleta", "Data", "Origem", "Status", "Custo (R$)", "Resolução", "Téc. Entrega"]]
        
        cell_style = ParagraphStyle('CellStyle', parent=styles['Normal'], fontSize=8, leading=10)

        for reg in dados:
            val_fmt = f"R$ {reg[9]:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            data_fmt = datetime.strptime(reg[4], "%Y-%m-%d").strftime("%d/%m/%Y")
            
            table_data.append([
                str(reg[0]),
                Paragraph(str(reg[1]), cell_style),
                Paragraph(str(reg[2]), cell_style),
                Paragraph(str(reg[3]), cell_style),
                data_fmt,
                Paragraph(str(reg[5]), cell_style),
                str(reg[7]),
                val_fmt,
                Paragraph(str(reg[10]) if reg[10] else "-", cell_style),
                Paragraph(str(reg[11]) if reg[11] else "-", cell_style)
            ])

        t = Table(table_data, colWidths=[30, 110, 60, 85, 60, 95, 65, 65, 110, 85])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2C3E50")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#BDC3C7")),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ]))
        
        elements.append(t)
        doc.build(elements)

    @classmethod
    def relatorio_por_mes(cls, pasta_destino, mes: int, ano: int):
        nome_meses = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
        caminho = os.path.join(pasta_destino, f"relatorio_{mes:02d}_{ano}.pdf")
        dados = ColetaModel.buscar_por_mes_ano(mes, ano)
        cls._gerar_pdf(caminho, f"Relatório de Equipamentos - {nome_meses[mes]} / {ano}", dados)
        return caminho