"""
pdf_exporter.py - Módulo de geração de relatório PDF estilizado para o Moreira Spec.

Usa fpdf2 com carregamento automático de fontes TrueType do Windows (Segoe UI / Arial)
para suporte completo a UTF-8 e acentuação em português, com fallback seguro para Helvetica.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import Sequence

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from config import APP_AUTHOR, APP_NAME, APP_VERSION, GITHUB_SHORT_URL
from hardware import HardwareSpecs


# Cores do tema padrão Moreira Spec (RGB)
COLOR_NAVY = (15, 23, 42)        # #0F172A
COLOR_SLATE = (30, 41, 59)       # #1E293B
COLOR_CYAN = (56, 189, 248)      # #38BDF8
COLOR_PURPLE = (139, 92, 246)    # #8B5CF6
COLOR_TEXT_DARK = (15, 23, 42)   # #0F172A
COLOR_TEXT_MUTED = (100, 116, 139)  # #64748B
COLOR_ROW_ALT = (241, 245, 249)  # #F1F5F9
COLOR_BORDER = (203, 213, 225)   # #CBD5E1
COLOR_WHITE = (255, 255, 255)


RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """Remove caracteres inválidos e nomes reservados para nome de arquivo no Windows."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (name or "").strip()).strip(" .")[:64]
    if not cleaned or cleaned.upper() in RESERVED_WINDOWS_NAMES:
        return "PC"
    return cleaned


def _resource_path(relative_path: str) -> str:
    """Resolve recursos tanto em desenvolvimento quanto dentro do .exe PyInstaller."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)


def default_pdf_filename(hostname: str) -> str:
    """Sugere o nome padrão MoreiraSpec_[NomeDoPC].pdf."""
    return f"MoreiraSpec_{sanitize_filename(hostname)}.pdf"


class MoreiraSpecPDF(FPDF):
    """Classe FPDF personalizada com cabeçalho estilizado, rodapé e fontes Unicode."""

    def __init__(self, hostname: str, generated_at: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.hostname = hostname
        self.generated_at = generated_at
        self.font_family_name = "Helvetica"
        self.is_unicode_font = False

        self.set_margins(14, 14, 14)
        self.set_auto_page_break(auto=True, margin=18)
        self._load_best_font()

    def _load_best_font(self) -> None:
        """Tenta registrar Segoe UI ou Arial do Windows para suporte total a acentuação."""
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts_dir = os.path.join(windir, "Fonts")

        candidates = [
            ("SegoeUI", "segoeui.ttf", "segoeuib.ttf"),
            ("ArialTTF", "arial.ttf", "arialbd.ttf"),
            ("CalibriTTF", "calibri.ttf", "calibrib.ttf"),
        ]

        for family, regular_file, bold_file in candidates:
            reg_path = os.path.join(fonts_dir, regular_file)
            bold_path = os.path.join(fonts_dir, bold_file)
            if os.path.isfile(reg_path) and os.path.isfile(bold_path):
                try:
                    self.add_font(family, "", reg_path)
                    self.add_font(family, "B", bold_path)
                    self.font_family_name = family
                    self.is_unicode_font = True
                    return
                except Exception:
                    continue

    def safe_text(self, value: object) -> str:
        """Prepara texto para renderização segura, preservando acentos em português."""
        text = str(value) if value is not None else "N/A"
        if self.is_unicode_font:
            return text
        return text.encode("latin-1", "replace").decode("latin-1")

    def header(self) -> None:
        if self.page_no() == 1:
            # Banner principal da primeira página
            self.set_fill_color(*COLOR_NAVY)
            self.rect(0, 0, 210, 38, "F")

            # Faixa de destaque roxa na base do banner
            self.set_fill_color(*COLOR_PURPLE)
            self.rect(0, 38, 210, 1.8, "F")

            # Logo oficial no canto esquerdo do cabeçalho (se disponível)
            text_x = 14
            logo_file = _resource_path("logo.png")
            if os.path.isfile(logo_file):
                try:
                    self.image(logo_file, x=14, y=6, w=26, h=26)
                    text_x = 44
                except Exception:
                    text_x = 14

            # Título MOREIRA SPEC
            self.set_xy(text_x, 7)
            self.set_text_color(*COLOR_CYAN)
            self.set_font(self.font_family_name, "B", 22)
            self.cell(
                0, 10, self.safe_text("MOREIRA SPEC"),
                new_x=XPos.LMARGIN, new_y=YPos.NEXT,
            )

            # Subtítulo
            self.set_x(text_x)
            self.set_text_color(241, 245, 249)
            self.set_font(self.font_family_name, "", 10.5)
            self.cell(
                0, 6, self.safe_text("Relatório de Especificações de Hardware"),
                new_x=XPos.LMARGIN, new_y=YPos.NEXT,
            )

            # Metadados (Nome da máquina + Data/Hora)
            self.set_x(text_x)
            self.set_text_color(148, 163, 184)
            self.set_font(self.font_family_name, "", 9)
            meta = f"Máquina: {self.hostname}   |   Gerado em: {self.generated_at}"
            self.cell(
                0, 6, self.safe_text(meta),
                new_x=XPos.LMARGIN, new_y=YPos.NEXT,
            )

            self.set_y(45)
        else:
            # Cabeçalho compacto para páginas seguintes
            self.set_fill_color(*COLOR_NAVY)
            self.rect(0, 0, 210, 13, "F")
            self.set_xy(14, 3.2)
            self.set_text_color(*COLOR_CYAN)
            self.set_font(self.font_family_name, "B", 10)
            self.cell(90, 6.5, self.safe_text("MOREIRA SPEC"))
            self.set_text_color(203, 213, 225)
            self.set_font(self.font_family_name, "", 8.5)
            self.cell(
                0, 6.5,
                self.safe_text(f"{self.hostname}  |  {self.generated_at}"),
                align="R",
                new_x=XPos.LMARGIN, new_y=YPos.NEXT,
            )
            self.set_y(18)

    def footer(self) -> None:
        self.set_y(-13)
        self.set_draw_color(*COLOR_BORDER)
        self.line(14, self.get_y() - 1, 196, self.get_y() - 1)
        self.set_text_color(*COLOR_TEXT_MUTED)
        self.set_font(self.font_family_name, "", 7.8)
        creditos = f"Gerado por {APP_NAME} v{APP_VERSION} | Criado por {APP_AUTHOR} | {GITHUB_SHORT_URL}"
        self.cell(154, 7, self.safe_text(creditos))
        self.cell(
            0, 7,
            self.safe_text(f"Página {self.page_no()} de {{nb}}"),
            align="R",
        )

    def _ensure_space(self, needed_mm: float) -> None:
        """Evita títulos órfãos no fim da página."""
        if self.get_y() + needed_mm > self.h - self.b_margin:
            self.add_page()

    def add_section_title(self, title: str) -> None:
        """Desenha uma faixa estilizada de título de seção."""
        self._ensure_space(22)
        y = self.get_y()
        usable_w = self.w - self.l_margin - self.r_margin

        # Faixa azul-ardósia
        self.set_fill_color(*COLOR_SLATE)
        self.rect(self.l_margin, y, usable_w, 8.5, "F")

        # Detalhe lateral ciano
        self.set_fill_color(*COLOR_CYAN)
        self.rect(self.l_margin, y, 2.5, 8.5, "F")

        self.set_xy(self.l_margin + 5, y + 1.1)
        self.set_text_color(*COLOR_WHITE)
        self.set_font(self.font_family_name, "B", 11)
        self.cell(0, 6.3, self.safe_text(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_y(y + 10.5)

    def add_kv_table(self, rows: Sequence[tuple[str, str]], key_width: float = 56.0) -> None:
        """Renderiza uma tabela limpa de chave-valor com linhas zebradas e quebra automática."""
        usable_w = self.w - self.l_margin - self.r_margin
        val_width = usable_w - key_width
        line_h = 5.5
        pad_y = 1.6

        for idx, (chave, valor) in enumerate(rows):
            chave_txt = self.safe_text(chave)
            valor_txt = self.safe_text(valor)

            # Calcula altura necessária para valor com múltiplas linhas
            self.set_font(self.font_family_name, "", 9.5)
            lines = self.multi_cell(val_width - 4, line_h, valor_txt, dry_run=True, output="LINES")
            row_h = max(7.5, len(lines) * line_h + pad_y * 2)

            self._ensure_space(row_h + 2)
            y_start = self.get_y()

            fill_color = COLOR_ROW_ALT if idx % 2 == 0 else COLOR_WHITE
            self.set_fill_color(*fill_color)
            self.set_draw_color(*COLOR_BORDER)
            self.rect(self.l_margin, y_start, usable_w, row_h, "DF")

            # Divisória vertical entre chave e valor
            self.line(self.l_margin + key_width, y_start, self.l_margin + key_width, y_start + row_h)

            # Chave
            self.set_xy(self.l_margin + 2.5, y_start + pad_y)
            self.set_text_color(*COLOR_SLATE)
            self.set_font(self.font_family_name, "B", 9.5)
            self.cell(key_width - 4, line_h, chave_txt)

            # Valor
            self.set_xy(self.l_margin + key_width + 2.5, y_start + pad_y)
            self.set_text_color(*COLOR_TEXT_DARK)
            self.set_font(self.font_family_name, "", 9.5)
            self.multi_cell(val_width - 4, line_h, valor_txt, align="L")

            self.set_y(y_start + row_h)

        self.ln(4.5)

    def add_data_table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        col_widths: Sequence[float],
        subtitle: str | None = None,
    ) -> None:
        """Renderiza uma tabela multi-coluna estilizada."""
        if subtitle:
            self._ensure_space(18)
            self.set_text_color(*COLOR_SLATE)
            self.set_font(self.font_family_name, "B", 9.5)
            self.cell(0, 6, self.safe_text(subtitle), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        if not rows:
            self.set_text_color(*COLOR_TEXT_MUTED)
            self.set_font(self.font_family_name, "", 9)
            self.cell(0, 6.5, self.safe_text("Nenhum item detectado."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(3)
            return

        total_w = sum(col_widths)
        line_h = 5.0
        pad_y = 1.5

        def _draw_header() -> None:
            y = self.get_y()
            self.set_fill_color(*COLOR_PURPLE)
            self.set_draw_color(*COLOR_BORDER)
            self.rect(self.l_margin, y, total_w, 7.5, "DF")
            self.set_text_color(*COLOR_WHITE)
            self.set_font(self.font_family_name, "B", 8.8)
            x = self.l_margin
            for header_text, w in zip(headers, col_widths):
                self.set_xy(x + 1.8, y + 1.2)
                self.cell(w - 3.6, 5.0, self.safe_text(header_text))
                x += w
            self.set_y(y + 7.5)

        self._ensure_space(16)
        _draw_header()

        for row_idx, row in enumerate(rows):
            self.set_font(self.font_family_name, "", 8.8)
            max_lines = 1
            safe_cells: list[str] = []
            for cell_val, w in zip(row, col_widths):
                txt = self.safe_text(cell_val)
                safe_cells.append(txt)
                lines = self.multi_cell(w - 3.6, line_h, txt, dry_run=True, output="LINES")
                max_lines = max(max_lines, len(lines))

            row_h = max(7.0, max_lines * line_h + pad_y * 2)
            if self.get_y() + row_h > self.h - self.b_margin:
                self.add_page()
                _draw_header()

            y_start = self.get_y()
            fill_color = COLOR_ROW_ALT if row_idx % 2 == 0 else COLOR_WHITE
            self.set_fill_color(*fill_color)
            self.set_draw_color(*COLOR_BORDER)
            self.rect(self.l_margin, y_start, total_w, row_h, "DF")

            self.set_text_color(*COLOR_TEXT_DARK)
            self.set_font(self.font_family_name, "", 8.8)
            x = self.l_margin
            for col_i, (txt, w) in enumerate(zip(safe_cells, col_widths)):
                if col_i > 0:
                    self.line(x, y_start, x, y_start + row_h)
                self.set_xy(x + 1.8, y_start + pad_y)
                self.multi_cell(w - 3.6, line_h, txt, align="L")
                x += w

            self.set_y(y_start + row_h)

        self.ln(4.5)


def exportar_relatorio_pdf(specs: HardwareSpecs, caminho_arquivo: str) -> str:
    """Gera e salva o relatório PDF completo do Moreira Spec no caminho especificado."""
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    pdf = MoreiraSpecPDF(hostname=specs.hostname or "PC", generated_at=gerado_em)
    pdf.alias_nb_pages()
    pdf.add_page()

    # 1. Resumo Geral
    pdf.add_section_title("1. Resumo do Sistema")
    pdf.add_kv_table(list(specs.resumo.items()))

    # 2. Processador (CPU)
    pdf.add_section_title("2. Processador (CPU)")
    cpu_rows = [
        (k, f"{v:.1f}%" if k == "Uso Atual" and isinstance(v, (int, float)) else str(v))
        for k, v in specs.cpu.items()
        if k != "Temperatura C"
    ]
    pdf.add_kv_table(cpu_rows)

    # 3. Placa-Mãe & BIOS
    pdf.add_section_title("3. Placa-Mãe & BIOS")
    mb_bios_rows = list(specs.placa_mae.items()) + list(specs.bios.items())
    pdf.add_kv_table(mb_bios_rows)

    # 4. Memória RAM
    pdf.add_section_title("4. Memória RAM")
    ram_summary_rows = [
        ("Total Instalado", str(specs.ram.get("Total Instalado", "N/A"))),
        ("Em Uso", str(specs.ram.get("Em Uso", "N/A"))),
        ("Disponível", str(specs.ram.get("Disponível", "N/A"))),
    ]
    pdf.add_kv_table(ram_summary_rows)

    if specs.ram_modulos:
        pdf.add_data_table(
            headers=["Slot", "Capacidade", "Tipo", "Velocidade", "Fabricante", "Part Number"],
            rows=[
                [
                    m.get("Slot", "N/A"),
                    m.get("Capacidade", "N/A"),
                    m.get("Tipo", "N/A"),
                    m.get("Velocidade", "N/A"),
                    m.get("Fabricante", "N/A"),
                    m.get("Part Number", "N/A"),
                ]
                for m in specs.ram_modulos
            ],
            col_widths=[26, 24, 22, 26, 38, 46],
            subtitle="Módulos Físicos Instalados",
        )

    # 5. Placa de Vídeo (GPU)
    pdf.add_section_title("5. Placa de Vídeo (GPU)")
    pdf.add_data_table(
        headers=["Modelo", "VRAM Dedicada", "Driver", "Resolução Atual"],
        rows=[
            [
                g.get("Modelo", "N/A"),
                g.get("VRAM Dedicada", "N/A"),
                g.get("Versão do Driver", "N/A"),
                g.get("Resolução Atual", "N/A"),
            ]
            for g in specs.gpus
        ],
        col_widths=[74, 34, 38, 36],
    )

    # 6. Armazenamento
    pdf.add_section_title("6. Armazenamento")
    if specs.discos_fisicos:
        def _format_disk_health_pdf(d: dict[str, str]) -> str:
            saude = d.get("Saúde", "N/A")
            detalhe = d.get("Detalhe S.M.A.R.T.", "")
            if detalhe and detalhe != saude:
                return f"{saude} ({detalhe})"
            return saude

        pdf.add_data_table(
            headers=["Modelo do Disco Físico", "Tamanho", "Interface", "Tipo de Mídia", "Saúde / S.M.A.R.T."],
            rows=[
                [
                    d.get("Modelo", "N/A"),
                    d.get("Tamanho", "N/A"),
                    d.get("Interface", "N/A"),
                    d.get("Tipo de Mídia", "N/A"),
                    _format_disk_health_pdf(d),
                ]
                for d in specs.discos_fisicos
            ],
            col_widths=[60, 24, 20, 28, 50],
            subtitle="Discos Físicos e Saúde",
        )

    pdf.add_data_table(
        headers=["Unidade", "Sistema", "Total", "Usado", "Livre", "Uso %"],
        rows=[
            [
                str(p.get("Dispositivo", "N/A")),
                str(p.get("Sistema de Arquivos", "N/A")),
                str(p.get("Total", "N/A")),
                str(p.get("Usado", "N/A")),
                str(p.get("Livre", "N/A")),
                f"{p.get('Percentual', 0):.1f}%",
            ]
            for p in specs.particoes
        ],
        col_widths=[28, 24, 34, 34, 34, 28],
        subtitle="Partições e Uso de Espaço",
    )

    # 7. Sistema
    pdf.add_section_title("7. Sistema Operacional")
    sys_rows = [(k, v) for k, v in specs.sistema.items() if k != "Endereço IP Local"]
    pdf.add_kv_table(sys_rows)

    pdf.output(caminho_arquivo)
    return caminho_arquivo
