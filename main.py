"""
main.py - Ponto de entrada e interface gráfica principal do Moreira Spec.

Suporta dois modos de execução:
1. Modo Silencioso / CLI (--coleta-silenciosa): não carrega Tkinter/CustomTkinter/Pillow,
   consumindo o mínimo de CPU e RAM para coletas diárias agendadas em parques de máquinas.
2. Modo Desktop (GUI CustomTkinter): visual Dark Mode moderno, temas personalizáveis,
   coleta assíncrona, botão Copiar WhatsApp, exportação PDF sob demanda e aba de Coleta Diária (TI).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from typing import Any

from config import (
    APP_AUTHOR,
    APP_NAME,
    APP_VERSION,
    GITHUB_LATEST_RELEASE_API,
    GITHUB_RELEASES_URL,
    GITHUB_REPO_URL,
)


def _run_silent_cli(argv: list[str]) -> int:
    """
    Executa coleta silenciosa sem carregar bibliotecas gráficas (baixo uso de RAM/CPU).
    Exemplo:
      MoreiraSpec.exe --coleta-silenciosa --pasta "\\\\SERVIDOR\\Inventario" --formato json,csv --retencao 30
    """
    from inventory import (
        executar_coleta_inventario,
        get_default_inventory_dir,
        load_schedule_config,
    )

    cfg = load_schedule_config()
    default_formats = ",".join(
        fmt for fmt, key in (("json", "formato_json"), ("csv", "formato_csv"), ("pdf", "formato_pdf"))
        if cfg.get(key)
    ) or "json,csv"

    parser = argparse.ArgumentParser(
        prog="MoreiraSpec",
        description="Moreira Spec - Coleta silenciosa de inventário de hardware.",
    )
    parser.add_argument("--coleta-silenciosa", action="store_true", help="Executa sem interface gráfica.")
    parser.add_argument("--pasta", default=cfg.get("pasta") or get_default_inventory_dir(), help="Pasta de destino (local ou UNC).")
    parser.add_argument("--formato", default=default_formats, help="Formatos separados por vírgula: json,csv,pdf.")
    parser.add_argument("--retencao", type=int, default=int(cfg.get("retencao_dias", 30)), help="Dias para manter snapshots antigos.")

    args = parser.parse_args(argv)
    formatos = {f.strip().lower() for f in args.formato.split(",") if f.strip()}
    try:
        resultado = executar_coleta_inventario(
            pasta_destino=args.pasta,
            formatos=formatos,
            retencao_dias=max(0, args.retencao),
        )
        print(
            f"[MoreiraSpec] Coleta concluída ({resultado['hostname']}) em {resultado['pasta']} | "
            f"Arquivos: {len(resultado['arquivos'])} | Alertas: {len(resultado['alertas'])}"
        )
        return 0
    except Exception as exc:
        print(f"[MoreiraSpec] Erro na coleta silenciosa: {exc}", file=sys.stderr)
        return 1


# Intercepta modo CLI silencioso ANTES de importar bibliotecas gráficas (CTk/Tk/PIL/FPDF)
if __name__ == "__main__" and ("--coleta-silenciosa" in sys.argv or "-h" in sys.argv or "--help" in sys.argv):
    raise SystemExit(_run_silent_cli(sys.argv[1:]))


from tkinter import Toplevel, filedialog, messagebox  # noqa: E402
import tkinter as tk  # noqa: E402
import customtkinter as ctk  # noqa: E402
from PIL import Image  # noqa: E402

from hardware import (  # noqa: E402
    HardwareSpecs,
    coletar_especificacoes,
    formatar_resumo_whatsapp,
    get_live_metrics,
)
from inventory import (  # noqa: E402
    TASK_NAME,
    ativar_agendamento_diario,
    consultar_agendamento_diario,
    desativar_agendamento_diario,
    detectar_alertas,
    executar_coleta_inventario,
    load_schedule_config,
    save_schedule_config,
    validate_output_dir,
    validate_schedule_time,
)


def resource_path(relative_path: str) -> str:
    """Resolve caminhos de recursos tanto em desenvolvimento quanto no .exe PyInstaller."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)


def _configure_windows_app_id() -> None:
    """Define AppUserModelID no Windows para exibir o ícone personalizado na barra de tarefas."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MoreiraSpec.Hardware.1.0")
        except Exception:
            pass


def _parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Extrai tupla numérica de uma tag de versão (ex: 'v1.2.3' -> (1, 2, 3))."""
    cleaned = (version_str or "").strip().lstrip("vV")
    nums = [int(part) for part in re.findall(r"\d+", cleaned)]
    return tuple(nums) if nums else (0,)


def _is_newer_version(latest: str, current: str) -> bool:
    """Compara versões semânticas de forma segura."""
    t_latest = list(_parse_version_tuple(latest))
    t_current = list(_parse_version_tuple(current))
    max_len = max(len(t_latest), len(t_current))
    t_latest.extend([0] * (max_len - len(t_latest)))
    t_current.extend([0] * (max_len - len(t_current)))
    return tuple(t_latest) > tuple(t_current)


def verificar_atualizacao_github(current_version: str = APP_VERSION, timeout: int = 6) -> dict[str, Any]:
    """
    Consulta a API pública de releases do GitHub de forma segura (somente HTTPS).
    Nunca lança exceções para a interface; retorna dicionário de status estruturado.
    """
    req = urllib.request.Request(
        GITHUB_LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"MoreiraSpec/{current_version}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return {"status": "offline"}
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"status": "no_release"}
        return {"status": "offline"}
    except Exception:
        return {"status": "offline"}

    if not isinstance(payload, dict):
        return {"status": "offline"}

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return {"status": "no_release"}

    release_url = str(payload.get("html_url") or "").strip()
    if not release_url.startswith(GITHUB_REPO_URL):
        release_url = GITHUB_RELEASES_URL

    if _is_newer_version(tag, current_version):
        return {
            "status": "update_available",
            "latest_version": tag.lstrip("vV"),
            "tag": tag,
            "url": release_url,
        }
    return {
        "status": "up_to_date",
        "latest_version": tag.lstrip("vV"),
        "tag": tag,
        "url": release_url,
    }


# Paletas de cores disponíveis (Moreira Dark é o padrão solicitado)
THEMES: dict[str, dict[str, str]] = {
    "Moreira Dark (Padrão)": {
        "mode": "dark",
        "bg": "#0F172A",
        "surface": "#1E293B",
        "surface_alt": "#162032",
        "border": "#334155",
        "primary": "#38BDF8",
        "primary_soft": "#0C4A6E",
        "action": "#8B5CF6",
        "action_hover": "#7C3AED",
        "text": "#F8FAFC",
        "text_muted": "#94A3B8",
        "text_subtle": "#64748B",
        "success": "#22C55E",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "nav_active": "#25344B",
        "nav_hover": "#283548",
    },
    "Emerald Night": {
        "mode": "dark",
        "bg": "#091311",
        "surface": "#122420",
        "surface_alt": "#0E1D1A",
        "border": "#1F3B34",
        "primary": "#34D399",
        "primary_soft": "#064E3B",
        "action": "#10B981",
        "action_hover": "#059669",
        "text": "#ECFDF5",
        "text_muted": "#9CA3AF",
        "text_subtle": "#6B7280",
        "success": "#34D399",
        "warning": "#FBBF24",
        "danger": "#F87171",
        "nav_active": "#1A352F",
        "nav_hover": "#172E29",
    },
    "Crimson Carbon": {
        "mode": "dark",
        "bg": "#130E14",
        "surface": "#211924",
        "surface_alt": "#1A131C",
        "border": "#38293D",
        "primary": "#FB7185",
        "primary_soft": "#4C0519",
        "action": "#E11D48",
        "action_hover": "#BE123C",
        "text": "#FFF1F2",
        "text_muted": "#B8A5BD",
        "text_subtle": "#7E6B85",
        "success": "#4ADE80",
        "warning": "#FBBF24",
        "danger": "#FB7185",
        "nav_active": "#322238",
        "nav_hover": "#2A1E2F",
    },
    "Arctic Light": {
        "mode": "light",
        "bg": "#F1F5F9",
        "surface": "#FFFFFF",
        "surface_alt": "#F8FAFC",
        "border": "#CBD5E1",
        "primary": "#0284C7",
        "primary_soft": "#E0F2FE",
        "action": "#7C3AED",
        "action_hover": "#6D28D9",
        "text": "#0F172A",
        "text_muted": "#475569",
        "text_subtle": "#64748B",
        "success": "#16A34A",
        "warning": "#D97706",
        "danger": "#DC2626",
        "nav_active": "#E0F2FE",
        "nav_hover": "#E2E8F0",
    },
}

NAV_ITEMS: list[tuple[str, str, str]] = [
    ("resumo", "◈", "Resumo"),
    ("cpu", "▣", "Processador (CPU)"),
    ("placa_mae", "▤", "Placa-Mãe & BIOS"),
    ("ram", "▥", "Memória RAM"),
    ("gpu", "◈", "Placa de Vídeo (GPU)"),
    ("armazenamento", "◫", "Armazenamento"),
    ("sistema", "◎", "Sistema"),
    ("rede", "◌", "Rede"),
    ("coleta_diaria", "⏱", "Coleta Diária (TI)"),
]

DISABLED_SECTIONS: dict[str, str] = {
    "rede": "Em desenvolvimento",
}

SECTION_SUBTITLES: dict[str, str] = {
    "resumo": "Visão geral rápida dos principais componentes da máquina",
    "cpu": "Especificações do processador, núcleos, frequências, carga e temperatura",
    "placa_mae": "Informações da placa-mãe, chipset e firmware da BIOS",
    "ram": "Uso de memória e detalhamento de cada pente físico instalado",
    "gpu": "Adaptadores gráficos detectados, VRAM dedicada e versão de drivers",
    "armazenamento": "Unidades físicas, saúde S.M.A.R.T. e espaço utilizado por partição",
    "sistema": "Sistema operacional, versão/build e tempo de atividade da máquina",
    "rede": "Em desenvolvimento — disponível em versões futuras",
    "coleta_diaria": "Automação leve de inventário diário em JSON/CSV/PDF para gestão de máquinas",
}


class _HoverTooltip:
    """Tooltip leve e estilizado exibido ao passar o mouse sobre widgets (inclusive desativados)."""

    def __init__(self, widget: Any, text: str, bg: str = "#0F172A", fg: str = "#F8FAFC", border: str = "#38BDF8") -> None:
        self.widget = widget
        self.text = text
        self.bg = bg
        self.fg = fg
        self.border = border
        self._tip: Toplevel | None = None
        self._bind_recursive(widget)

    def _bind_recursive(self, w: Any) -> None:
        try:
            w.bind("<Enter>", self._show, add="+")
            w.bind("<Leave>", self._hide, add="+")
            w.bind("<Motion>", self._move, add="+")
            w.bind("<Destroy>", self._hide, add="+")
            for child in w.winfo_children():
                self._bind_recursive(child)
        except Exception:
            pass

    def _show(self, event: Any = None) -> None:
        if self._tip is not None:
            return
        try:
            x = (event.x_root + 14) if event else (self.widget.winfo_rootx() + 20)
            y = (event.y_root + 10) if event else (self.widget.winfo_rooty() + 20)
            tip = Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            try:
                tip.wm_attributes("-topmost", True)
            except Exception:
                pass
            tip.wm_geometry(f"+{x}+{y}")

            frame = tk.Frame(tip, bg=self.border, padx=1, pady=1)
            frame.pack()
            lbl = tk.Label(
                frame,
                text=f"  {self.text}  ",
                bg=self.bg,
                fg=self.fg,
                font=("Segoe UI", 9, "bold"),
                pady=4,
            )
            lbl.pack()
            self._tip = tip
        except Exception:
            self._tip = None

    def _move(self, event: Any = None) -> None:
        if self._tip is not None and event is not None:
            try:
                self._tip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 10}")
            except Exception:
                pass

    def _hide(self, _event: Any = None) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

# Mapeamento de quais métricas em tempo real cada aba realmente precisa (economiza CPU)
LIVE_NEEDS_BY_SECTION: dict[str, tuple[bool, bool, bool]] = {
    "resumo": (True, True, True),
    "cpu": (True, False, False),
    "ram": (False, True, False),
    "sistema": (False, False, True),
}


class MoreiraSpecApp(ctk.CTk):
    """Janela principal do Moreira Spec."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Moreira Spec — Diagnóstico de Hardware")
        self.geometry("1160x710")
        self.minsize(980, 620)

        self.theme_name = "Moreira Dark (Padrão)"
        self.theme = THEMES[self.theme_name]
        ctk.set_appearance_mode(self.theme["mode"])

        self.specs: HardwareSpecs | None = None
        self.current_section = "resumo"
        self.is_loading = False
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.live_widgets: dict[str, Any] = {}
        self._live_job_id: str | None = None
        self._whatsapp_reset_job_id: str | None = None
        self._sidebar_logo_img: ctk.CTkImage | None = self._load_cached_logo()
        self._schedule_cfg: dict[str, Any] = load_schedule_config()
        self._schedule_status: dict[str, Any] = {"ativo": False, "detalhe": "Verificando..."}
        self._inventory_busy = False
        self._checking_updates = False
        self._tooltips: list[_HoverTooltip] = []

        self._apply_window_icon()
        # CustomTkinter reaplica ícone padrão ~200ms após inicialização no Windows
        self.after(250, self._apply_window_icon)

        self._build_layout()
        self._start_hardware_scan()
        self._refresh_schedule_status_async()
        self._schedule_live_update()

    def _load_cached_logo(self) -> ctk.CTkImage | None:
        """Carrega logo.png uma única vez fechando o arquivo imediatamente (economiza RAM)."""
        logo_file = resource_path("logo.png")
        if not os.path.isfile(logo_file):
            return None
        try:
            with Image.open(logo_file) as img:
                loaded = img.copy()
            return ctk.CTkImage(light_image=loaded, dark_image=loaded, size=(46, 46))
        except Exception:
            return None

    def _apply_window_icon(self) -> None:
        """Aplica o ícone oficial (icon.ico) na barra de título e barra de tarefas."""
        icon_file = resource_path("icon.ico")
        if os.path.isfile(icon_file):
            try:
                self.iconbitmap(icon_file)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Construção do Layout Principal
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        t = self.theme
        self.configure(fg_color=t["bg"])

        for child in self.winfo_children():
            child.destroy()
        self.nav_buttons.clear()
        self.live_widgets.clear()
        self._tooltips.clear()

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- SIDEBAR ----
        self.sidebar = ctk.CTkFrame(
            self,
            width=250,
            corner_radius=0,
            fg_color=t["surface"],
            border_width=0,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        sidebar_divider = ctk.CTkFrame(self.sidebar, width=1, fg_color=t["border"], corner_radius=0)
        sidebar_divider.pack(side="right", fill="y")

        sidebar_inner = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        sidebar_inner.pack(side="left", fill="both", expand=True)

        # Logo / Marca
        brand_frame = ctk.CTkFrame(sidebar_inner, fg_color="transparent")
        brand_frame.pack(fill="x", padx=16, pady=(14, 10))

        if self._sidebar_logo_img is not None:
            ctk.CTkLabel(
                brand_frame,
                text="",
                image=self._sidebar_logo_img,
            ).pack(side="left", padx=(0, 10))

        brand_text_box = ctk.CTkFrame(brand_frame, fg_color="transparent")
        brand_text_box.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            brand_text_box,
            text="MOREIRA SPEC",
            font=("Segoe UI", 18, "bold"),
            text_color=t["primary"],
            anchor="w",
        ).pack(fill="x")

        ctk.CTkLabel(
            brand_text_box,
            text="Diagnóstico & Inventário",
            font=("Segoe UI", 11),
            text_color=t["text_muted"],
            anchor="w",
        ).pack(fill="x", pady=(1, 0))

        ctk.CTkFrame(sidebar_inner, height=1, fg_color=t["border"]).pack(fill="x", padx=16, pady=(0, 8))

        # Botões de navegação
        nav_container = ctk.CTkFrame(sidebar_inner, fg_color="transparent")
        nav_container.pack(fill="x", padx=12)

        for key, badge, label in NAV_ITEMS:
            is_disabled = key in DISABLED_SECTIONS
            btn = ctk.CTkButton(
                nav_container,
                text=f"  {badge}   {label}",
                anchor="w",
                height=32,
                corner_radius=9,
                font=("Segoe UI", 13, "bold"),
                fg_color="transparent",
                hover_color=t["surface"] if is_disabled else t["nav_hover"],
                text_color=t["text_subtle"] if is_disabled else t["text_muted"],
                text_color_disabled=t["text_subtle"],
                state="disabled" if is_disabled else "normal",
                command=(lambda: None) if is_disabled else (lambda k=key: self._select_section(k)),
            )
            btn.pack(fill="x", pady=1)
            self.nav_buttons[key] = btn

            if is_disabled:
                tip = _HoverTooltip(
                    btn,
                    DISABLED_SECTIONS[key],
                    bg=t["bg"],
                    fg=t["primary"],
                    border=t["border"],
                )
                self._tooltips.append(tip)

        # Rodapé da sidebar: tema, GitHub, atualizações e créditos do autor
        sidebar_footer = ctk.CTkFrame(sidebar_inner, fg_color="transparent")
        sidebar_footer.pack(side="bottom", fill="x", padx=14, pady=(8, 12))

        ctk.CTkFrame(sidebar_footer, height=1, fg_color=t["border"]).pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            sidebar_footer,
            text="TEMA DE COR",
            font=("Segoe UI", 10, "bold"),
            text_color=t["text_subtle"],
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self.theme_menu = ctk.CTkOptionMenu(
            sidebar_footer,
            values=list(THEMES.keys()),
            command=self._on_theme_change,
            fg_color=t["surface_alt"],
            button_color=t["primary"],
            button_hover_color=t["action"],
            dropdown_fg_color=t["surface"],
            dropdown_hover_color=t["nav_hover"],
            text_color=t["text"],
            font=("Segoe UI", 11),
            corner_radius=8,
            height=30,
        )
        self.theme_menu.set(self.theme_name)
        self.theme_menu.pack(fill="x", pady=(0, 8))

        self.btn_github = ctk.CTkButton(
            sidebar_footer,
            text="↗  GitHub / Atualizações",
            font=("Segoe UI", 11, "bold"),
            fg_color=t["surface_alt"],
            hover_color=t["nav_hover"],
            border_width=1,
            border_color=t["border"],
            text_color=t["primary"],
            corner_radius=8,
            height=28,
            command=self._open_github_repo,
        )
        self.btn_github.pack(fill="x", pady=(0, 5))

        self.btn_check_updates = ctk.CTkButton(
            sidebar_footer,
            text="↻  Verificar Atualizações",
            font=("Segoe UI", 11, "bold"),
            fg_color="transparent",
            hover_color=t["nav_hover"],
            border_width=1,
            border_color=t["border"],
            text_color=t["text_muted"],
            corner_radius=8,
            height=28,
            command=self._check_for_updates,
        )
        self.btn_check_updates.pack(fill="x", pady=(0, 8))

        self.footer_info_lbl = ctk.CTkLabel(
            sidebar_footer,
            text=f"v{APP_VERSION}  •  Open Source",
            font=("Segoe UI", 10, "bold"),
            text_color=t["text_muted"],
            anchor="w",
        )
        self.footer_info_lbl.pack(fill="x")

        ctk.CTkLabel(
            sidebar_footer,
            text=f"Criado por {APP_AUTHOR}",
            font=("Segoe UI", 10),
            text_color=t["text_subtle"],
            anchor="w",
        ).pack(fill="x", pady=(1, 0))

        # ---- ÁREA PRINCIPAL (DIREITA) ----
        main_area = ctk.CTkFrame(self, fg_color=t["bg"], corner_radius=0)
        main_area.grid(row=0, column=1, sticky="nsew")
        main_area.grid_rowconfigure(0, weight=0)
        main_area.grid_rowconfigure(1, weight=1)
        main_area.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(
            main_area,
            fg_color=t["surface"],
            corner_radius=14,
            border_width=1,
            border_color=t["border"],
        )
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(20, 10))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", fill="y", padx=20, pady=14)

        self.section_title_lbl = ctk.CTkLabel(
            title_box,
            text="Resumo",
            font=("Segoe UI", 20, "bold"),
            text_color=t["text"],
            anchor="w",
        )
        self.section_title_lbl.pack(anchor="w")

        self.section_subtitle_lbl = ctk.CTkLabel(
            title_box,
            text=SECTION_SUBTITLES["resumo"],
            font=("Segoe UI", 12),
            text_color=t["text_muted"],
            anchor="w",
        )
        self.section_subtitle_lbl.pack(anchor="w", pady=(2, 0))

        actions_box = ctk.CTkFrame(header, fg_color="transparent")
        actions_box.pack(side="right", padx=18, pady=14)

        self.btn_refresh = ctk.CTkButton(
            actions_box,
            text="↻  Atualizar Dados",
            font=("Segoe UI", 12, "bold"),
            fg_color=t["surface_alt"],
            hover_color=t["nav_hover"],
            border_width=1,
            border_color=t["border"],
            text_color=t["text"],
            corner_radius=10,
            height=38,
            width=142,
            command=self._start_hardware_scan,
        )
        self.btn_refresh.pack(side="left", padx=(0, 8))

        self.btn_whatsapp = ctk.CTkButton(
            actions_box,
            text="Copiar WhatsApp",
            font=("Segoe UI", 12, "bold"),
            fg_color="#10B981",
            hover_color="#059669",
            text_color="#FFFFFF",
            corner_radius=10,
            height=38,
            width=142,
            command=self._copy_whatsapp,
        )
        self.btn_whatsapp.pack(side="left", padx=(0, 8))

        self.btn_pdf = ctk.CTkButton(
            actions_box,
            text="⬇  Exportar PDF",
            font=("Segoe UI", 12, "bold"),
            fg_color=t["action"],
            hover_color=t["action_hover"],
            text_color="#FFFFFF",
            corner_radius=10,
            height=38,
            width=138,
            command=self._export_pdf,
        )
        self.btn_pdf.pack(side="left")

        self.content_scroll = ctk.CTkScrollableFrame(
            main_area,
            fg_color="transparent",
            scrollbar_button_color=t["border"],
            scrollbar_button_hover_color=t["primary"],
        )
        self.content_scroll.grid(row=1, column=0, sticky="nsew", padx=22, pady=(4, 18))

        self._update_nav_highlight()

    # ------------------------------------------------------------------
    # Tema & Navegação
    # ------------------------------------------------------------------
    def _on_theme_change(self, selected_theme: str) -> None:
        if selected_theme == self.theme_name or selected_theme not in THEMES:
            return
        self.theme_name = selected_theme
        self.theme = THEMES[selected_theme]
        ctk.set_appearance_mode(self.theme["mode"])
        self._build_layout()
        if self.is_loading:
            self.btn_refresh.configure(state="disabled", text="Analisando...")
            self.btn_whatsapp.configure(state="disabled")
            self.btn_pdf.configure(state="disabled")
            self._render_loading_state()
        elif self.specs is not None:
            self._render_current_section()

    def _select_section(self, section_key: str) -> None:
        if section_key in DISABLED_SECTIONS:
            return
        self.current_section = section_key
        self._update_nav_highlight()
        if not self.is_loading and self.specs is not None:
            self._render_current_section()

    def _update_nav_highlight(self) -> None:
        t = self.theme
        label_map = {k: lbl for k, _, lbl in NAV_ITEMS}
        self.section_title_lbl.configure(text=label_map.get(self.current_section, "Resumo"))
        self.section_subtitle_lbl.configure(text=SECTION_SUBTITLES.get(self.current_section, ""))

        for key, btn in self.nav_buttons.items():
            if key in DISABLED_SECTIONS:
                btn.configure(fg_color="transparent", text_color=t["text_subtle"])
            elif key == self.current_section:
                btn.configure(fg_color=t["nav_active"], text_color=t["primary"])
            else:
                btn.configure(fg_color="transparent", text_color=t["text_muted"])

    # ------------------------------------------------------------------
    # Coleta Assíncrona de Hardware
    # ------------------------------------------------------------------
    def _start_hardware_scan(self) -> None:
        if self.is_loading:
            return
        self.is_loading = True
        self.btn_refresh.configure(state="disabled", text="Analisando...")
        self.btn_whatsapp.configure(state="disabled")
        self.btn_pdf.configure(state="disabled")
        self._render_loading_state()

        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            specs = coletar_especificacoes()
            self.after(0, lambda: self._on_scan_complete(specs, None))
        except Exception as exc:
            self.after(0, lambda err=exc: self._on_scan_complete(None, err))

    def _on_scan_complete(self, specs: HardwareSpecs | None, error: Exception | None) -> None:
        self.is_loading = False
        self.btn_refresh.configure(state="normal", text="↻  Atualizar Dados")

        if error is not None or specs is None:
            self.btn_whatsapp.configure(state="disabled")
            self.btn_pdf.configure(state="disabled")
            self._clear_content()
            card = self._create_card("Erro ao coletar dados")
            ctk.CTkLabel(
                card,
                text=f"Não foi possível concluir a análise de hardware:\n{error}",
                font=("Segoe UI", 13),
                text_color=self.theme["danger"],
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=18, pady=(4, 16))
            return

        self.specs = specs
        self.btn_whatsapp.configure(state="normal")
        self.btn_pdf.configure(state="normal")
        self.footer_info_lbl.configure(text=f"v{APP_VERSION}  •  {specs.hostname}")
        self._render_current_section()

    def _render_loading_state(self) -> None:
        t = self.theme
        self._clear_content()

        box = ctk.CTkFrame(
            self.content_scroll,
            fg_color=t["surface"],
            corner_radius=14,
            border_width=1,
            border_color=t["border"],
        )
        box.pack(fill="x", pady=60, padx=60)

        ctk.CTkLabel(
            box,
            text="Analisando hardware...",
            font=("Segoe UI", 18, "bold"),
            text_color=t["primary"],
        ).pack(pady=(32, 8))

        ctk.CTkLabel(
            box,
            text="Coletando informações de CPU, Placa-Mãe, BIOS, RAM, GPU, Armazenamento e Rede via WMI.",
            font=("Segoe UI", 12),
            text_color=t["text_muted"],
            wraplength=460,
        ).pack(pady=(0, 20))

        bar = ctk.CTkProgressBar(
            box,
            mode="indeterminate",
            width=360,
            height=10,
            corner_radius=5,
            fg_color=t["border"],
            progress_color=t["primary"],
        )
        bar.pack(pady=(0, 34))
        bar.start()

    # ------------------------------------------------------------------
    # Atualização Inteligente em Tempo Real (pausa quando minimizado/ocioso)
    # ------------------------------------------------------------------
    def _schedule_live_update(self) -> None:
        if self._live_job_id is not None:
            try:
                self.after_cancel(self._live_job_id)
            except Exception:
                pass
        self._live_job_id = self.after(2000, self._tick_live_metrics)

    def destroy(self) -> None:
        for job_attr in ("_live_job_id", "_whatsapp_reset_job_id"):
            job_id = getattr(self, job_attr, None)
            if job_id is not None:
                try:
                    self.after_cancel(job_id)
                except Exception:
                    pass
                setattr(self, job_attr, None)
        super().destroy()

    def _tick_live_metrics(self) -> None:
        try:
            is_minimized = self.state() == "iconic"
        except Exception:
            is_minimized = False

        needs = LIVE_NEEDS_BY_SECTION.get(self.current_section)
        if not is_minimized and not self.is_loading and self.specs is not None and needs is not None:
            need_cpu, need_ram, need_uptime = needs
            metrics = get_live_metrics(need_cpu=need_cpu, need_ram=need_ram, need_uptime=need_uptime)
            if need_cpu:
                self.specs.cpu["Uso Atual"] = metrics["cpu_percent"]
                if metrics["cpu_freq_current"] != "N/A":
                    self.specs.cpu["Frequência Atual"] = metrics["cpu_freq_current"]
            if need_ram:
                self.specs.ram["Percentual"] = metrics["ram_percent"]
                if metrics["ram_used_gb"] != "N/A":
                    self.specs.ram["Em Uso"] = f"{metrics['ram_used_gb']} ({metrics['ram_percent']:.1f}%)"
                    self.specs.ram["Disponível"] = metrics["ram_avail_gb"]
            if need_uptime and metrics.get("uptime", "N/A") != "N/A":
                self.specs.sistema["Tempo Ligado (Uptime)"] = metrics["uptime"]

            self._apply_live_widgets(metrics)

        self._live_job_id = self.after(2000, self._tick_live_metrics)

    def _apply_live_widgets(self, metrics: dict[str, Any]) -> None:
        if "cpu_percent" in metrics:
            cpu_pct = metrics["cpu_percent"]
            self._safe_widget_update(
                "cpu_bar",
                lambda w: (w.set(max(0.0, min(1.0, cpu_pct / 100.0))), w.configure(progress_color=self._usage_color(cpu_pct))),
            )
            self._safe_widget_update("cpu_pct_lbl", lambda w: w.configure(text=f"{cpu_pct:.1f}%"))
            if metrics.get("cpu_freq_current", "N/A") != "N/A":
                self._safe_widget_update("cpu_freq_lbl", lambda w: w.configure(text=metrics["cpu_freq_current"]))

        if "ram_percent" in metrics:
            ram_pct = metrics["ram_percent"]
            self._safe_widget_update(
                "ram_bar",
                lambda w: (w.set(max(0.0, min(1.0, ram_pct / 100.0))), w.configure(progress_color=self._usage_color(ram_pct))),
            )
            self._safe_widget_update("ram_pct_lbl", lambda w: w.configure(text=f"{ram_pct:.1f}%"))
            if metrics.get("ram_used_gb", "N/A") != "N/A":
                self._safe_widget_update("ram_used_lbl", lambda w: w.configure(text=f"{metrics['ram_used_gb']} ({ram_pct:.1f}%)"))
                self._safe_widget_update("ram_avail_lbl", lambda w: w.configure(text=metrics["ram_avail_gb"]))

        if metrics.get("uptime", "N/A") != "N/A":
            self._safe_widget_update("uptime_lbl", lambda w: w.configure(text=metrics["uptime"]))

    def _safe_widget_update(self, key: str, updater: Any) -> None:
        widget = self.live_widgets.get(key)
        if widget is not None:
            try:
                if widget.winfo_exists():
                    updater(widget)
            except Exception:
                pass

    def _usage_color(self, percent: float) -> str:
        t = self.theme
        if percent >= 85:
            return t["danger"]
        if percent >= 65:
            return t["warning"]
        return t["primary"]

    # ------------------------------------------------------------------
    # Componentes Visuais Reutilizáveis
    # ------------------------------------------------------------------
    def _clear_content(self) -> None:
        self.live_widgets.clear()
        for widget in self.content_scroll.winfo_children():
            widget.destroy()

    def _create_card(self, title: str, badge_text: str | None = None) -> ctk.CTkFrame:
        t = self.theme
        card = ctk.CTkFrame(
            self.content_scroll,
            fg_color=t["surface"],
            corner_radius=12,
            border_width=1,
            border_color=t["border"],
        )
        card.pack(fill="x", pady=(0, 12))

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(14, 8))

        ctk.CTkLabel(
            top,
            text=title,
            font=("Segoe UI", 15, "bold"),
            text_color=t["primary"],
            anchor="w",
        ).pack(side="left")

        if badge_text:
            ctk.CTkLabel(
                top,
                text=f"  {badge_text}  ",
                font=("Segoe UI", 11, "bold"),
                text_color=t["primary"],
                fg_color=t["primary_soft"],
                corner_radius=6,
                height=24,
            ).pack(side="right")

        ctk.CTkFrame(card, height=1, fg_color=t["border"]).pack(fill="x", padx=18, pady=(0, 8))
        return card

    def _add_kv_row(
        self,
        parent: ctk.CTkFrame,
        label: str,
        value: str,
        live_key: str | None = None,
    ) -> ctk.CTkLabel:
        t = self.theme
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)

        ctk.CTkLabel(
            row,
            text=f"{label}:",
            font=("Segoe UI", 12, "bold"),
            text_color=t["text_muted"],
            width=185,
            anchor="w",
        ).pack(side="left")

        val_lbl = ctk.CTkLabel(
            row,
            text=str(value),
            font=("Segoe UI", 12),
            text_color=t["text"],
            anchor="w",
            justify="left",
            wraplength=540,
        )
        val_lbl.pack(side="left", fill="x", expand=True)

        if live_key:
            self.live_widgets[live_key] = val_lbl
        return val_lbl

    def _add_card_bottom_spacer(self, card: ctk.CTkFrame) -> None:
        ctk.CTkFrame(card, height=10, fg_color="transparent").pack()

    def _add_progress_block(
        self,
        parent: ctk.CTkFrame,
        label: str,
        percent: float,
        bar_key: str | None = None,
        pct_key: str | None = None,
        detail_text: str | None = None,
    ) -> None:
        t = self.theme
        wrap = ctk.CTkFrame(parent, fg_color=t["surface_alt"], corner_radius=10, border_width=1, border_color=t["border"])
        wrap.pack(fill="x", padx=18, pady=(6, 10))

        header = ctk.CTkFrame(wrap, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 6))

        ctk.CTkLabel(
            header,
            text=label,
            font=("Segoe UI", 12, "bold"),
            text_color=t["text"],
        ).pack(side="left")

        pct_lbl = ctk.CTkLabel(
            header,
            text=f"{percent:.1f}%",
            font=("Segoe UI", 13, "bold"),
            text_color=t["primary"],
        )
        pct_lbl.pack(side="right")

        bar = ctk.CTkProgressBar(
            wrap,
            height=12,
            corner_radius=6,
            fg_color=t["border"],
            progress_color=self._usage_color(percent),
        )
        bar.set(max(0.0, min(1.0, percent / 100.0)))
        bar.pack(fill="x", padx=14, pady=(0, 8 if detail_text else 12))

        if detail_text:
            ctk.CTkLabel(
                wrap,
                text=detail_text,
                font=("Segoe UI", 11),
                text_color=t["text_muted"],
                anchor="w",
            ).pack(fill="x", padx=14, pady=(0, 10))

        if bar_key:
            self.live_widgets[bar_key] = bar
        if pct_key:
            self.live_widgets[pct_key] = pct_lbl

    # ------------------------------------------------------------------
    # Renderização das Seções
    # ------------------------------------------------------------------
    def _render_current_section(self) -> None:
        if self.specs is None:
            return
        self._clear_content()

        renderers = {
            "resumo": self._render_resumo,
            "cpu": self._render_cpu,
            "placa_mae": self._render_placa_mae,
            "ram": self._render_ram,
            "gpu": self._render_gpu,
            "armazenamento": self._render_armazenamento,
            "sistema": self._render_sistema,
            "coleta_diaria": self._render_coleta_diaria,
        }
        render_fn = renderers.get(self.current_section, self._render_resumo)
        render_fn(self.specs)

    def _render_resumo(self, specs: HardwareSpecs) -> None:
        t = self.theme

        metrics_row = ctk.CTkFrame(self.content_scroll, fg_color="transparent")
        metrics_row.pack(fill="x", pady=(0, 12))
        metrics_row.grid_columnconfigure(0, weight=1)
        metrics_row.grid_columnconfigure(1, weight=1)

        cpu_mini = ctk.CTkFrame(
            metrics_row, fg_color=t["surface"], corner_radius=12, border_width=1, border_color=t["border"]
        )
        cpu_mini.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._add_progress_block(
            cpu_mini,
            "Uso da CPU em Tempo Real",
            float(specs.cpu.get("Uso Atual", 0.0)),
            bar_key="cpu_bar",
            pct_key="cpu_pct_lbl",
            detail_text=f"{specs.cpu.get('Núcleos Físicos', 'N/A')} Núcleos / {specs.cpu.get('Threads (Lógicos)', 'N/A')} Threads",
        )

        ram_mini = ctk.CTkFrame(
            metrics_row, fg_color=t["surface"], corner_radius=12, border_width=1, border_color=t["border"]
        )
        ram_mini.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._add_progress_block(
            ram_mini,
            "Uso de Memória RAM",
            float(specs.ram.get("Percentual", 0.0)),
            bar_key="ram_bar",
            pct_key="ram_pct_lbl",
            detail_text=f"Total: {specs.ram.get('Total Instalado', 'N/A')}  •  Disponível: {specs.ram.get('Disponível', 'N/A')}",
        )

        card = self._create_card("Principais Componentes", badge_text=specs.hostname)
        for chave, valor in specs.resumo.items():
            self._add_kv_row(card, chave, valor)
        self._add_card_bottom_spacer(card)

        status_card = self._create_card("Status Rápido do Sistema")
        self._add_kv_row(status_card, "Tempo Ligado (Uptime)", specs.sistema.get("Tempo Ligado (Uptime)", "N/A"), live_key="uptime_lbl")
        self._add_kv_row(status_card, "Última Inicialização", specs.sistema.get("Última Inicialização", "N/A"))
        self._add_kv_row(status_card, "Coletado em", specs.coletado_em)
        self._add_card_bottom_spacer(status_card)

    def _render_cpu(self, specs: HardwareSpecs) -> None:
        t = self.theme
        cpu = specs.cpu
        card_uso = self._create_card("Carga e Temperatura", badge_text=cpu.get("Arquitetura", ""))
        self._add_progress_block(
            card_uso,
            "Utilização do Processador",
            float(cpu.get("Uso Atual", 0.0)),
            bar_key="cpu_bar",
            pct_key="cpu_pct_lbl",
            detail_text=cpu.get("Modelo", "Não identificado"),
        )

        temp_str = str(cpu.get("Temperatura", "Indisponível (requer Admin/sensor compatível)"))
        temp_c = cpu.get("Temperatura C")
        if isinstance(temp_c, (int, float)):
            if temp_c >= 82.0:
                temp_fg, temp_bg = "#FFFFFF", t["danger"]
            elif temp_c >= 65.0:
                temp_fg, temp_bg = "#0F172A", t["warning"]
            else:
                temp_fg, temp_bg = "#FFFFFF", "#10B981"
        else:
            temp_fg, temp_bg = t["text_muted"], t["border"]

        temp_box = ctk.CTkFrame(
            card_uso,
            fg_color=t["surface_alt"],
            corner_radius=10,
            border_width=1,
            border_color=t["border"],
        )
        temp_box.pack(fill="x", padx=18, pady=(0, 8))

        ctk.CTkLabel(
            temp_box,
            text="Temperatura da CPU (ACPI / WMI)",
            font=("Segoe UI", 12, "bold"),
            text_color=t["text"],
        ).pack(side="left", padx=14, pady=10)

        ctk.CTkLabel(
            temp_box,
            text=f"  {temp_str}  ",
            font=("Segoe UI", 11, "bold"),
            text_color=temp_fg,
            fg_color=temp_bg,
            corner_radius=6,
            height=24,
        ).pack(side="right", padx=14, pady=8)

        self._add_card_bottom_spacer(card_uso)

        card_det = self._create_card("Especificações do Processador")
        for key in (
            "Modelo",
            "Fabricante",
            "Arquitetura",
            "Núcleos Físicos",
            "Threads (Lógicos)",
            "Frequência Atual",
            "Frequência Máxima",
            "Socket",
            "Cache L2 / L3",
            "Temperatura",
        ):
            live_k = "cpu_freq_lbl" if key == "Frequência Atual" else None
            self._add_kv_row(card_det, key, str(cpu.get(key, "N/A")), live_key=live_k)
        self._add_card_bottom_spacer(card_det)

    def _render_placa_mae(self, specs: HardwareSpecs) -> None:
        mb_card = self._create_card("Placa-Mãe (BaseBoard)")
        for chave, valor in specs.placa_mae.items():
            self._add_kv_row(mb_card, chave, valor)
        self._add_card_bottom_spacer(mb_card)

        bios_card = self._create_card("BIOS / UEFI")
        for chave, valor in specs.bios.items():
            self._add_kv_row(bios_card, chave, valor)
        self._add_card_bottom_spacer(bios_card)

    def _render_ram(self, specs: HardwareSpecs) -> None:
        t = self.theme
        ram = specs.ram

        card_geral = self._create_card(
            "Visão Geral da Memória RAM",
            badge_text=f"{len(specs.ram_modulos)} módulo(s)" if specs.ram_modulos else "RAM",
        )
        self._add_progress_block(
            card_geral,
            "Consumo Atual de Memória",
            float(ram.get("Percentual", 0.0)),
            bar_key="ram_bar",
            pct_key="ram_pct_lbl",
        )
        self._add_kv_row(card_geral, "Total Instalado", str(ram.get("Total Instalado", "N/A")))
        self._add_kv_row(card_geral, "Em Uso", str(ram.get("Em Uso", "N/A")), live_key="ram_used_lbl")
        self._add_kv_row(card_geral, "Disponível", str(ram.get("Disponível", "N/A")), live_key="ram_avail_lbl")
        self._add_card_bottom_spacer(card_geral)

        card_slots = self._create_card("Pentes Físicos Instalados (Win32_PhysicalMemory)")
        if not specs.ram_modulos:
            ctk.CTkLabel(
                card_slots,
                text="Nenhum módulo físico retornado via WMI (pode exigir permissões ou rodar no Windows).",
                font=("Segoe UI", 12),
                text_color=t["text_muted"],
                anchor="w",
            ).pack(fill="x", padx=18, pady=(4, 16))
        else:
            for idx, mod in enumerate(specs.ram_modulos, start=1):
                mod_box = ctk.CTkFrame(
                    card_slots,
                    fg_color=t["surface_alt"],
                    corner_radius=10,
                    border_width=1,
                    border_color=t["border"],
                )
                mod_box.pack(fill="x", padx=18, pady=6)

                header = ctk.CTkFrame(mod_box, fg_color="transparent")
                header.pack(fill="x", padx=14, pady=(10, 4))

                ctk.CTkLabel(
                    header,
                    text=f"Módulo #{idx}  —  {mod['Slot']}",
                    font=("Segoe UI", 13, "bold"),
                    text_color=t["primary"],
                ).pack(side="left")

                ctk.CTkLabel(
                    header,
                    text=f"{mod['Capacidade']}  •  {mod['Tipo']}  •  {mod['Velocidade']}",
                    font=("Segoe UI", 12, "bold"),
                    text_color=t["text"],
                ).pack(side="right")

                ctk.CTkLabel(
                    mod_box,
                    text=f"Fabricante: {mod['Fabricante']}    |    Part Number: {mod['Part Number']}",
                    font=("Segoe UI", 11),
                    text_color=t["text_muted"],
                    anchor="w",
                ).pack(fill="x", padx=14, pady=(0, 10))

            self._add_card_bottom_spacer(card_slots)

    def _render_gpu(self, specs: HardwareSpecs) -> None:
        for idx, gpu in enumerate(specs.gpus, start=1):
            card = self._create_card(
                f"Placa de Vídeo #{idx}",
                badge_text=gpu.get("VRAM Dedicada", "N/A"),
            )
            for chave, valor in gpu.items():
                self._add_kv_row(card, chave, valor)
            self._add_card_bottom_spacer(card)

    def _render_armazenamento(self, specs: HardwareSpecs) -> None:
        t = self.theme

        card_disks = self._create_card(
            "Discos Físicos",
            badge_text=f"{len(specs.discos_fisicos)} unidade(s)",
        )
        if not specs.discos_fisicos:
            ctk.CTkLabel(
                card_disks,
                text="Nenhum disco físico listado via WMI.",
                font=("Segoe UI", 12),
                text_color=t["text_muted"],
                anchor="w",
            ).pack(fill="x", padx=18, pady=(4, 16))
        else:
            for idx, disk in enumerate(specs.discos_fisicos, start=1):
                box = ctk.CTkFrame(
                    card_disks,
                    fg_color=t["surface_alt"],
                    corner_radius=10,
                    border_width=1,
                    border_color=t["border"],
                )
                box.pack(fill="x", padx=18, pady=6)

                top = ctk.CTkFrame(box, fg_color="transparent")
                top.pack(fill="x", padx=14, pady=(10, 4))

                ctk.CTkLabel(
                    top,
                    text=f"Disco #{idx} — {disk['Modelo']}",
                    font=("Segoe UI", 13, "bold"),
                    text_color=t["text"],
                ).pack(side="left")

                ctk.CTkLabel(
                    top,
                    text=disk["Tamanho"],
                    font=("Segoe UI", 12, "bold"),
                    text_color=t["primary"],
                ).pack(side="right")

                saude_txt = disk.get("Saúde", "Execute como Admin para S.M.A.R.T.")
                saude_nivel = disk.get("Saúde Nível", "unknown")
                smart_det = disk.get("Detalhe S.M.A.R.T.", "")

                if saude_nivel == "ok":
                    badge_bg, badge_fg = "#10B981", "#FFFFFF"
                elif saude_nivel == "warn":
                    badge_bg, badge_fg = "#F59E0B", "#0F172A"
                else:
                    badge_bg, badge_fg = "#475569", "#F8FAFC"

                health_row = ctk.CTkFrame(box, fg_color="transparent")
                health_row.pack(fill="x", padx=14, pady=(2, 6))

                ctk.CTkLabel(
                    health_row,
                    text=f"  {saude_txt}  ",
                    font=("Segoe UI", 11, "bold"),
                    text_color=badge_fg,
                    fg_color=badge_bg,
                    corner_radius=6,
                    height=22,
                ).pack(side="left")

                if smart_det and smart_det != saude_txt:
                    is_admin_hint = "Admin" in smart_det
                    sub_bg = "#475569" if is_admin_hint else t["primary_soft"]
                    sub_fg = "#F8FAFC" if is_admin_hint else t["primary"]
                    ctk.CTkLabel(
                        health_row,
                        text=f"  {smart_det}  ",
                        font=("Segoe UI", 10, "bold"),
                        text_color=sub_fg,
                        fg_color=sub_bg,
                        corner_radius=6,
                        height=22,
                    ).pack(side="left", padx=(8, 0))

                ctk.CTkLabel(
                    box,
                    text=f"Interface: {disk['Interface']}    |    Tipo de Mídia: {disk['Tipo de Mídia']}",
                    font=("Segoe UI", 11),
                    text_color=t["text_muted"],
                    anchor="w",
                ).pack(fill="x", padx=14, pady=(0, 10))
            self._add_card_bottom_spacer(card_disks)

        card_parts = self._create_card(
            "Partições e Espaço em Disco",
            badge_text=f"{len(specs.particoes)} partição(ões)",
        )
        if not specs.particoes:
            ctk.CTkLabel(
                card_parts,
                text="Nenhuma partição acessível encontrada.",
                font=("Segoe UI", 12),
                text_color=t["text_muted"],
                anchor="w",
            ).pack(fill="x", padx=18, pady=(4, 16))
        else:
            for part in specs.particoes:
                pct = float(part.get("Percentual", 0.0))
                titulo = f"Unidade {part['Dispositivo']}  ({part['Sistema de Arquivos']})"
                detalhes = f"Total: {part['Total']}   •   Usado: {part['Usado']}   •   Livre: {part['Livre']}"
                self._add_progress_block(card_parts, titulo, pct, detail_text=detalhes)
            self._add_card_bottom_spacer(card_parts)

    def _render_sistema(self, specs: HardwareSpecs) -> None:
        card_sys = self._create_card("Sistema Operacional & Máquina", badge_text=specs.hostname)
        for chave, valor in specs.sistema.items():
            if chave == "Endereço IP Local":
                continue
            live_k = "uptime_lbl" if chave == "Tempo Ligado (Uptime)" else None
            self._add_kv_row(card_sys, chave, valor, live_key=live_k)
        self._add_card_bottom_spacer(card_sys)

    # ------------------------------------------------------------------
    # Nova Seção: Coleta Diária (TI / Inventário de Máquinas)
    # ------------------------------------------------------------------
    def _refresh_schedule_status_async(self) -> None:
        def _worker() -> None:
            status = consultar_agendamento_diario()
            self.after(0, lambda: self._on_schedule_status_loaded(status))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_schedule_status_loaded(self, status: dict[str, Any]) -> None:
        self._schedule_status = status
        if self.current_section == "coleta_diaria" and not self.is_loading and self.specs is not None:
            self._render_current_section()

    def _render_coleta_diaria(self, specs: HardwareSpecs) -> None:
        t = self.theme
        cfg = self._schedule_cfg
        ativo = bool(self._schedule_status.get("ativo"))

        # 1. Card de Alertas da Máquina Atual
        alertas = detectar_alertas(specs)
        alert_card = self._create_card(
            "Monitoramento de Alertas da Máquina",
            badge_text=f"{len(alertas)} alerta(s)" if alertas else "Tudo OK",
        )
        if not alertas:
            ctk.CTkLabel(
                alert_card,
                text="●  Nenhum alerta crítico de disco, temperatura ou memória detectado nesta máquina.",
                font=("Segoe UI", 12, "bold"),
                text_color=t["success"],
                anchor="w",
            ).pack(fill="x", padx=18, pady=(4, 14))
        else:
            for item in alertas:
                ctk.CTkLabel(
                    alert_card,
                    text=f"▲  {item}",
                    font=("Segoe UI", 12, "bold"),
                    text_color=t["warning"],
                    anchor="w",
                ).pack(fill="x", padx=18, pady=3)
            self._add_card_bottom_spacer(alert_card)

        # 2. Card de Configuração e Agendamento Diário
        sched_card = self._create_card(
            "Coleta Diária Automática (Agendador do Windows)",
            badge_text="ATIVO" if ativo else "INATIVO",
        )

        status_row = ctk.CTkFrame(sched_card, fg_color=t["surface_alt"], corner_radius=10, border_width=1, border_color=t["border"])
        status_row.pack(fill="x", padx=18, pady=(4, 12))

        st_color = "#10B981" if ativo else "#64748B"
        st_text = f"Tarefa '{TASK_NAME}': {self._schedule_status.get('detalhe', '')}"
        ctk.CTkLabel(
            status_row,
            text=f"●  {st_text}",
            font=("Segoe UI", 12, "bold"),
            text_color=st_color,
            anchor="w",
        ).pack(side="left", padx=14, pady=10)

        # Pasta de destino (local ou rede \\SERVIDOR\Compartilhamento)
        ctk.CTkLabel(
            sched_card,
            text="Pasta de Destino (aceita pasta local ou rede \\\\SERVIDOR\\Inventario):",
            font=("Segoe UI", 12, "bold"),
            text_color=t["text_muted"],
            anchor="w",
        ).pack(fill="x", padx=18, pady=(2, 4))

        dir_row = ctk.CTkFrame(sched_card, fg_color="transparent")
        dir_row.pack(fill="x", padx=18, pady=(0, 10))

        self.inv_dir_entry = ctk.CTkEntry(
            dir_row,
            fg_color=t["surface_alt"],
            border_color=t["border"],
            text_color=t["text"],
            font=("Segoe UI", 12),
            height=36,
        )
        self.inv_dir_entry.insert(0, str(cfg.get("pasta", "")))
        self.inv_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            dir_row,
            text="Escolher Pasta",
            font=("Segoe UI", 12, "bold"),
            fg_color=t["surface_alt"],
            hover_color=t["nav_hover"],
            border_width=1,
            border_color=t["border"],
            text_color=t["text"],
            height=36,
            width=125,
            command=self._choose_inventory_dir,
        ).pack(side="left")

        # Horário + Retenção
        opts_row = ctk.CTkFrame(sched_card, fg_color="transparent")
        opts_row.pack(fill="x", padx=18, pady=(0, 10))

        ctk.CTkLabel(
            opts_row, text="Horário diário (HH:MM):", font=("Segoe UI", 12, "bold"), text_color=t["text_muted"]
        ).pack(side="left", padx=(0, 8))

        self.inv_time_entry = ctk.CTkEntry(
            opts_row,
            width=84,
            height=34,
            fg_color=t["surface_alt"],
            border_color=t["border"],
            text_color=t["text"],
            font=("Segoe UI", 12),
        )
        self.inv_time_entry.insert(0, str(cfg.get("horario", "09:00")))
        self.inv_time_entry.pack(side="left", padx=(0, 22))

        ctk.CTkLabel(
            opts_row, text="Manter histórico (dias):", font=("Segoe UI", 12, "bold"), text_color=t["text_muted"]
        ).pack(side="left", padx=(0, 8))

        self.inv_retention_entry = ctk.CTkEntry(
            opts_row,
            width=72,
            height=34,
            fg_color=t["surface_alt"],
            border_color=t["border"],
            text_color=t["text"],
            font=("Segoe UI", 12),
        )
        self.inv_retention_entry.insert(0, str(cfg.get("retencao_dias", 30)))
        self.inv_retention_entry.pack(side="left")

        # Checkboxes de formato
        fmt_row = ctk.CTkFrame(sched_card, fg_color="transparent")
        fmt_row.pack(fill="x", padx=18, pady=(2, 14))

        self.inv_var_json = ctk.BooleanVar(value=bool(cfg.get("formato_json", True)))
        self.inv_var_csv = ctk.BooleanVar(value=bool(cfg.get("formato_csv", True)))
        self.inv_var_pdf = ctk.BooleanVar(value=bool(cfg.get("formato_pdf", False)))

        ctk.CTkCheckBox(
            fmt_row, text="JSON (Snapshot + Diff de Hardware)", variable=self.inv_var_json,
            font=("Segoe UI", 12), text_color=t["text"], fg_color=t["primary"], border_color=t["border"],
        ).pack(side="left", padx=(0, 18))

        ctk.CTkCheckBox(
            fmt_row, text="CSV Consolidado (Excel PT-BR)", variable=self.inv_var_csv,
            font=("Segoe UI", 12), text_color=t["text"], fg_color=t["primary"], border_color=t["border"],
        ).pack(side="left", padx=(0, 18))

        ctk.CTkCheckBox(
            fmt_row, text="PDF Diário", variable=self.inv_var_pdf,
            font=("Segoe UI", 12), text_color=t["text"], fg_color=t["primary"], border_color=t["border"],
        ).pack(side="left")

        # Botões de ação
        btn_row = ctk.CTkFrame(sched_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=18, pady=(0, 14))

        self.btn_run_inv_now = ctk.CTkButton(
            btn_row,
            text="▶  Executar Coleta Agora",
            font=("Segoe UI", 12, "bold"),
            fg_color="#10B981",
            hover_color="#059669",
            text_color="#FFFFFF",
            height=38,
            command=self._run_inventory_now,
        )
        self.btn_run_inv_now.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="Ativar Coleta Diária",
            font=("Segoe UI", 12, "bold"),
            fg_color=t["action"],
            hover_color=t["action_hover"],
            text_color="#FFFFFF",
            height=38,
            command=self._enable_daily_schedule,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="Desativar Agendamento",
            font=("Segoe UI", 12, "bold"),
            fg_color=t["surface_alt"],
            hover_color=t["nav_hover"],
            border_width=1,
            border_color=t["border"],
            text_color=t["text"],
            height=38,
            command=self._disable_daily_schedule,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="Abrir Pasta",
            font=("Segoe UI", 12, "bold"),
            fg_color=t["surface_alt"],
            hover_color=t["nav_hover"],
            border_width=1,
            border_color=t["border"],
            text_color=t["primary"],
            height=38,
            width=110,
            command=self._open_inventory_folder,
        ).pack(side="right")

        # 3. Card informativo de implantação em massa (GPO / Rede / Modo Silencioso)
        cli_card = self._create_card("Implantação em Massa (Modo Ultraleve sem Interface)")
        ctk.CTkLabel(
            cli_card,
            text=(
                "Para controlar várias máquinas em rede (via GPO, Intune ou script de logon), "
                "execute o MoreiraSpec.exe em modo silencioso — ele não carrega a interface gráfica, "
                "usa pouquíssima RAM e consolida todas as máquinas no mesmo inventario_atual.csv:"
            ),
            font=("Segoe UI", 12),
            text_color=t["text_muted"],
            anchor="w",
            justify="left",
            wraplength=760,
        ).pack(fill="x", padx=18, pady=(2, 8))

        cmd_sample = (
            'MoreiraSpec.exe --coleta-silenciosa --pasta "\\\\SERVIDOR\\InventarioTI" '
            '--formato json,csv --retencao 30'
        )
        cmd_box = ctk.CTkEntry(
            cli_card,
            fg_color=t["surface_alt"],
            border_color=t["border"],
            text_color=t["primary"],
            font=("Consolas", 11),
            height=34,
        )
        cmd_box.insert(0, cmd_sample)
        cmd_box.configure(state="readonly")
        cmd_box.pack(fill="x", padx=18, pady=(0, 14))

    def _collect_inventory_form(self) -> tuple[str, str, set[str], int]:
        pasta = validate_output_dir(self.inv_dir_entry.get())
        horario = validate_schedule_time(self.inv_time_entry.get())
        try:
            retencao = int(self.inv_retention_entry.get().strip())
            if retencao < 0 or retencao > 3650:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Retenção deve ser um número inteiro entre 0 e 3650 dias.") from exc

        formatos: set[str] = set()
        if self.inv_var_json.get():
            formatos.add("json")
        if self.inv_var_csv.get():
            formatos.add("csv")
        if self.inv_var_pdf.get():
            formatos.add("pdf")
        if not formatos:
            raise ValueError("Selecione pelo menos um formato de saída (JSON, CSV ou PDF).")

        self._schedule_cfg = {
            "pasta": pasta,
            "horario": horario,
            "formato_json": "json" in formatos,
            "formato_csv": "csv" in formatos,
            "formato_pdf": "pdf" in formatos,
            "retencao_dias": retencao,
        }
        save_schedule_config(self._schedule_cfg)
        return pasta, horario, formatos, retencao

    def _choose_inventory_dir(self) -> None:
        chosen = filedialog.askdirectory(title="Selecione a Pasta de Inventário")
        if chosen:
            self.inv_dir_entry.delete(0, "end")
            self.inv_dir_entry.insert(0, os.path.normpath(chosen))

    def _run_inventory_now(self) -> None:
        if self._inventory_busy:
            return
        try:
            pasta, _, formatos, retencao = self._collect_inventory_form()
        except ValueError as exc:
            messagebox.showwarning("Configuração Inválida", str(exc))
            return

        self._inventory_busy = True
        self.btn_run_inv_now.configure(state="disabled", text="Coletando...")

        def _worker() -> None:
            try:
                res = executar_coleta_inventario(
                    pasta_destino=pasta,
                    specs=self.specs,
                    formatos=formatos,
                    retencao_dias=retencao,
                )
                self.after(0, lambda: self._on_inventory_now_done(res, None))
            except Exception as exc:
                self.after(0, lambda err=exc: self._on_inventory_now_done(None, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_inventory_now_done(self, res: dict[str, Any] | None, err: Exception | None) -> None:
        self._inventory_busy = False
        try:
            if self.btn_run_inv_now.winfo_exists():
                self.btn_run_inv_now.configure(state="normal", text="▶  Executar Coleta Agora")
        except Exception:
            pass

        if err is not None or res is None:
            messagebox.showerror("Erro na Coleta", f"Não foi possível salvar o inventário:\n{err}")
            return

        alertas_txt = (
            "\n\nAlertas detectados:\n- " + "\n- ".join(res["alertas"])
            if res["alertas"]
            else "\n\nNenhum alerta crítico detectado."
        )
        messagebox.showinfo(
            "Inventário Salvo",
            f"Coleta gravada com sucesso em:\n{res['pasta']}\n"
            f"Arquivos gerados: {len(res['arquivos'])}{alertas_txt}",
        )

    def _enable_daily_schedule(self) -> None:
        try:
            pasta, horario, formatos, retencao = self._collect_inventory_form()
        except ValueError as exc:
            messagebox.showwarning("Configuração Inválida", str(exc))
            return

        ok, msg = ativar_agendamento_diario(pasta, horario, formatos, retencao)
        self._refresh_schedule_status_async()
        if ok:
            messagebox.showinfo("Agendamento Ativado", msg)
        else:
            messagebox.showerror("Erro no Agendamento", msg)

    def _disable_daily_schedule(self) -> None:
        ok, msg = desativar_agendamento_diario()
        self._refresh_schedule_status_async()
        if ok:
            messagebox.showinfo("Agendamento Removido", msg)
        else:
            messagebox.showwarning("Aviso", msg)

    def _open_inventory_folder(self) -> None:
        try:
            pasta = validate_output_dir(self.inv_dir_entry.get())
            os.makedirs(pasta, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(pasta)
        except Exception as exc:
            messagebox.showerror("Erro ao Abrir Pasta", f"Não foi possível abrir a pasta:\n{exc}")

    # ------------------------------------------------------------------
    # GitHub & Verificação de Atualizações
    # ------------------------------------------------------------------
    def _open_github_repo(self) -> None:
        try:
            webbrowser.open(GITHUB_REPO_URL)
        except Exception:
            pass

    def _check_for_updates(self) -> None:
        if self._checking_updates:
            return
        self._checking_updates = True
        try:
            if self.btn_check_updates.winfo_exists():
                self.btn_check_updates.configure(state="disabled", text="Verificando...")
        except Exception:
            pass

        def _worker() -> None:
            result = verificar_atualizacao_github(APP_VERSION)
            self.after(0, lambda: self._on_update_check_done(result))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_check_done(self, result: dict[str, Any]) -> None:
        self._checking_updates = False
        try:
            if self.btn_check_updates.winfo_exists():
                self.btn_check_updates.configure(state="normal", text="↻  Verificar Atualizações")
        except Exception:
            pass

        status = result.get("status")
        if status == "update_available":
            latest = result.get("latest_version", "")
            url = result.get("url", GITHUB_RELEASES_URL)
            abrir = messagebox.askyesno(
                "Atualização Disponível",
                f"Uma nova versão do {APP_NAME} está disponível!\n\n"
                f"Versão instalada: v{APP_VERSION}\n"
                f"Nova versão: v{latest}\n\n"
                f"Deseja abrir a página de Releases no GitHub para baixar o novo executável?",
            )
            if abrir:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
        elif status == "up_to_date":
            messagebox.showinfo(
                "Atualizações",
                f"Você está usando a versão mais recente! (v{APP_VERSION})",
            )
        elif status == "no_release":
            messagebox.showinfo(
                "Atualizações",
                f"Você está usando a versão mais recente! (v{APP_VERSION})\n"
                f"(Nenhuma release oficial publicada ainda no GitHub.)",
            )
        else:
            messagebox.showinfo(
                "Verificar Atualizações",
                "Não foi possível consultar atualizações no momento.\n"
                "Verifique sua conexão com a internet ou tente novamente mais tarde.",
            )

    # ------------------------------------------------------------------
    # Copiar Resumo para WhatsApp
    # ------------------------------------------------------------------
    def _copy_whatsapp(self) -> None:
        if self.specs is None:
            messagebox.showwarning("Aguarde", "Os dados de hardware ainda estão sendo carregados.")
            return

        resumo_texto = formatar_resumo_whatsapp(self.specs)
        try:
            self.clipboard_clear()
            self.clipboard_append(resumo_texto)
            self.update()
        except Exception as exc:
            messagebox.showerror(
                "Erro ao Copiar",
                f"Não foi possível copiar para a área de transferência:\n{exc}",
            )
            return

        if self._whatsapp_reset_job_id is not None:
            try:
                self.after_cancel(self._whatsapp_reset_job_id)
            except Exception:
                pass

        self.btn_whatsapp.configure(text="✓  Copiado!")
        self._whatsapp_reset_job_id = self.after(2000, self._reset_whatsapp_button)

    def _reset_whatsapp_button(self) -> None:
        self._whatsapp_reset_job_id = None
        try:
            if self.btn_whatsapp.winfo_exists():
                self.btn_whatsapp.configure(text="Copiar WhatsApp")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Exportação PDF (importação tardia para manter inicialização leve)
    # ------------------------------------------------------------------
    def _export_pdf(self) -> None:
        if self.specs is None:
            messagebox.showwarning("Aguarde", "Os dados de hardware ainda estão sendo carregados.")
            return

        from pdf_exporter import default_pdf_filename, exportar_relatorio_pdf

        caminho = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=default_pdf_filename(self.specs.hostname),
            filetypes=[("Arquivo PDF", "*.pdf")],
            title="Salvar Relatório Moreira Spec",
        )
        if not caminho:
            return

        try:
            exportar_relatorio_pdf(self.specs, caminho)
            messagebox.showinfo(
                "Relatório Exportado",
                f"Relatório PDF salvo com sucesso em:\n{caminho}",
            )
        except Exception as exc:
            messagebox.showerror(
                "Erro ao Exportar PDF",
                f"Não foi possível gerar o relatório PDF:\n{exc}",
            )


if __name__ == "__main__":
    _configure_windows_app_id()
    app = MoreiraSpecApp()
    app.mainloop()
