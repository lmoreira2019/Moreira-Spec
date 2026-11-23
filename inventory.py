"""
inventory.py - Módulo seguro de coleta diária, inventário consolidado e agendamento (TI).

Projetado para administradores que controlam múltiplas máquinas Windows:
- Exportação atômica em JSON (snapshot completo por máquina/data) e CSV consolidado (Excel PT-BR).
- Proteção contra CSV Formula Injection (DDE/Excel) e Path Traversal.
- Detecção automática de alertas de saúde/capacidade e alterações de hardware entre coletas.
- Retenção automática de arquivos antigos para evitar crescimento infinito em disco/servidor.
- Integração segura com o Agendador de Tarefas do Windows (schtasks.exe sem shell=True).
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

from hardware import HardwareSpecs, coletar_especificacoes

TASK_NAME = "MoreiraSpec_ColetaDiaria"
SCHEMA_VERSION = "1.1"

RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
UNSAFE_PATH_CHARS_RE = re.compile(r'["&|;<>%^\r\n\x00]')
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_identifier(value: str, fallback: str = "PC", max_len: int = 64) -> str:
    """Sanitiza identificadores usados em nomes de arquivos (previne Path Traversal)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    cleaned = cleaned.strip("._-")[:max_len]
    if not cleaned or cleaned.upper() in RESERVED_WINDOWS_NAMES:
        return fallback
    return cleaned


def sanitize_csv_cell(value: Any) -> str:
    """
    Protege contra CSV Formula Injection (Excel / LibreOffice) e remove quebras de linha.
    """
    text = str(value if value is not None else "")
    text = text.replace("\r", " ").replace("\n", " ").replace("\x00", "").strip()
    if text and text[0] in FORMULA_PREFIXES:
        return "'" + text
    return text


def validate_schedule_time(hora_str: str) -> str:
    """Valida estritamente o horário no formato HH:MM (24h)."""
    hora = (hora_str or "").strip()
    if not TIME_RE.match(hora):
        raise ValueError("Horário inválido. Use o formato 24h HH:MM (ex: 09:00 ou 18:30).")
    return hora


def validate_output_dir(pasta: str) -> str:
    """Valida e normaliza a pasta de destino (local ou compartilhamento UNC)."""
    raw = (pasta or "").strip()
    if not raw:
        raise ValueError("Informe uma pasta de destino válida.")
    if UNSAFE_PATH_CHARS_RE.search(raw):
        raise ValueError("O caminho da pasta contém caracteres não permitidos.")
    norm = os.path.normpath(os.path.abspath(raw)) if not raw.startswith("\\\\") else os.path.normpath(raw)
    return norm


def get_default_inventory_dir() -> str:
    """Retorna pasta padrão segura no perfil do usuário (sem exigir privilégio Admin)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MoreiraSpec", "Inventario")


def get_config_path() -> str:
    """Caminho do arquivo local de preferências de coleta diária."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MoreiraSpec", "config_coleta.json")


def load_schedule_config() -> dict[str, Any]:
    """Carrega configuração salva de coleta diária com valores padrão seguros."""
    defaults = {
        "pasta": get_default_inventory_dir(),
        "horario": "09:00",
        "formato_json": True,
        "formato_csv": True,
        "formato_pdf": False,
        "retencao_dias": 30,
    }
    cfg_path = get_config_path()
    if not os.path.isfile(cfg_path):
        return defaults
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            defaults.update({k: data[k] for k in defaults if k in data})
    except Exception:
        pass
    return defaults


def save_schedule_config(config: dict[str, Any]) -> None:
    """Salva configuração de coleta diária de forma atômica."""
    cfg_path = get_config_path()
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    _atomic_write_text(cfg_path, json.dumps(config, ensure_ascii=False, indent=2))


def _atomic_write_text(dest_path: str, content: str, encoding: str = "utf-8") -> None:
    """Escreve arquivo via arquivo temporário + os.replace para evitar corrupção."""
    parent = os.path.dirname(dest_path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ms_tmp_", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
        os.replace(tmp_path, dest_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def detectar_alertas(specs: HardwareSpecs, snapshot_anterior: dict[str, Any] | None = None) -> list[str]:
    """
    Analisa a máquina atual em busca de alertas operacionais e mudanças de hardware.
    """
    alertas: list[str] = []

    # 1. Alerta de saúde S.M.A.R.T. / WMI dos discos
    for d in specs.discos_fisicos:
        if d.get("Saúde Nível") == "warn":
            alertas.append(f"Disco com alerta de saúde: {d.get('Modelo', 'Disco')}")

    # 2. Alerta de espaço crítico em partições (>= 90%)
    for p in specs.particoes:
        pct = float(p.get("Percentual", 0.0) or 0.0)
        if pct >= 90.0:
            alertas.append(f"Disco {p.get('Dispositivo', '')} quase cheio ({pct:.1f}%)")

    # 3. Alerta de temperatura elevada da CPU (>= 85 °C)
    temp_c = specs.cpu.get("Temperatura C")
    if isinstance(temp_c, (int, float)) and temp_c >= 85.0:
        alertas.append(f"Temperatura da CPU elevada ({temp_c:.1f} °C)")

    # 4. Alerta de uso muito alto de RAM (>= 92%)
    ram_pct = float(specs.ram.get("Percentual", 0.0) or 0.0)
    if ram_pct >= 92.0:
        alertas.append(f"Uso de RAM crítico ({ram_pct:.1f}%)")

    # 5. Detecção de alterações de hardware comparando com coleta anterior
    if isinstance(snapshot_anterior, dict):
        prev_specs = snapshot_anterior.get("specs", {})
        if isinstance(prev_specs, dict):
            prev_ram_total = (prev_specs.get("ram") or {}).get("Total Instalado")
            cur_ram_total = specs.ram.get("Total Instalado")
            if prev_ram_total and cur_ram_total and prev_ram_total != cur_ram_total:
                alertas.append(f"Alteração de RAM detectada: {prev_ram_total} -> {cur_ram_total}")

            prev_cpu = (prev_specs.get("cpu") or {}).get("Modelo")
            cur_cpu = specs.cpu.get("Modelo")
            if prev_cpu and cur_cpu and prev_cpu != cur_cpu:
                alertas.append("Alteração de CPU detectada")

            prev_disks = sorted(
                d.get("Modelo", "") for d in (prev_specs.get("discos_fisicos") or []) if isinstance(d, dict)
            )
            cur_disks = sorted(d.get("Modelo", "") for d in specs.discos_fisicos)
            if prev_disks and cur_disks and prev_disks != cur_disks:
                alertas.append("Alteração nos discos físicos detectada")

    return alertas


def _cleanup_old_snapshots(historico_dir: str, retencao_dias: int) -> int:
    """Remove snapshots antigos além do prazo de retenção (em dias)."""
    if retencao_dias <= 0 or not os.path.isdir(historico_dir):
        return 0
    cutoff = time.time() - (retencao_dias * 86400)
    removidos = 0
    try:
        for name in os.listdir(historico_dir):
            if not name.lower().endswith((".json", ".pdf")):
                continue
            full = os.path.join(historico_dir, name)
            try:
                if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                    os.remove(full)
                    removidos += 1
            except OSError:
                continue
    except OSError:
        pass
    return removidos


def _build_csv_row(specs: HardwareSpecs, alertas: list[str]) -> dict[str, str]:
    """Constrói uma linha tabular consolidada e sanitizada contra Formula Injection."""
    max_disk_pct = 0.0
    if specs.particoes:
        max_disk_pct = max(float(p.get("Percentual", 0.0) or 0.0) for p in specs.particoes)

    if specs.discos_fisicos:
        disco_principal = f"{specs.discos_fisicos[0].get('Modelo', 'N/A')} ({specs.discos_fisicos[0].get('Tamanho', 'N/A')})"
        saude_resumo = " | ".join(
            f"{d.get('Modelo', 'Disco')}: {d.get('Saúde', 'N/A')}" for d in specs.discos_fisicos
        )
    elif specs.particoes:
        disco_principal = f"{specs.particoes[0].get('Dispositivo', '')} ({specs.particoes[0].get('Total', 'N/A')})"
        saude_resumo = "N/A (sem WMI)"
    else:
        disco_principal = "N/A"
        saude_resumo = "N/A"

    gpu_str = " | ".join(g.get("Modelo", "N/A") for g in specs.gpus)
    raw_row = {
        "Data_Hora": specs.coletado_em,
        "Computador": specs.hostname,
        "IP_Local": specs.sistema.get("Endereço IP Local", "N/A"),
        "Sistema_Operacional": specs.sistema.get("Sistema Operacional", "N/A"),
        "Processador": specs.cpu.get("Modelo", "N/A"),
        "Nucleos_Threads": f"{specs.cpu.get('Núcleos Físicos', 'N/A')}N/{specs.cpu.get('Threads (Lógicos)', 'N/A')}T",
        "Temperatura_CPU": specs.cpu.get("Temperatura", "N/A"),
        "Placa_Mae": f"{specs.placa_mae.get('Fabricante', '')} {specs.placa_mae.get('Modelo', '')}".strip(),
        "RAM_Total": str(specs.ram.get("Total Instalado", "N/A")),
        "RAM_Uso_Pct": f"{float(specs.ram.get('Percentual', 0.0) or 0.0):.1f}%",
        "GPU": gpu_str,
        "Disco_Principal": disco_principal,
        "Saude_Discos": saude_resumo,
        "Maior_Uso_Particao_Pct": f"{max_disk_pct:.1f}%",
        "Uptime": specs.sistema.get("Tempo Ligado (Uptime)", "N/A"),
        "Alertas": " | ".join(alertas) if alertas else "Nenhum",
    }
    return {k: sanitize_csv_cell(v) for k, v in raw_row.items()}


def _update_consolidated_csv(csv_path: str, row: dict[str, str]) -> None:
    """
    Atualiza/insere a linha da máquina atual no inventario_atual.csv (1 linha por máquina)
    de forma atômica, em UTF-8 com BOM e separador ';' (compatível com Excel PT-BR).
    """
    fieldnames = list(row.keys())
    rows_by_host: dict[str, dict[str, str]] = {}

    if os.path.isfile(csv_path):
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for existing in reader:
                    host_key = (existing.get("Computador") or "").strip()
                    if host_key:
                        rows_by_host[host_key] = {k: sanitize_csv_cell(existing.get(k, "")) for k in fieldnames}
        except Exception:
            rows_by_host = {}

    rows_by_host[row["Computador"]] = row

    # Serializa em memória e grava atomicamente
    from io import StringIO
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for host_key in sorted(rows_by_host.keys(), key=str.lower):
        writer.writerow(rows_by_host[host_key])

    _atomic_write_text(csv_path, buf.getvalue(), encoding="utf-8-sig")


def _append_daily_history_csv(history_csv_path: str, row: dict[str, str]) -> None:
    """Registra a coleta no histórico diário (evita duplicar mesma máquina no mesmo dia)."""
    fieldnames = list(row.keys())
    today_prefix = row["Data_Hora"][:10]
    existing_rows: list[dict[str, str]] = []

    if os.path.isfile(history_csv_path):
        try:
            with open(history_csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for item in reader:
                    same_host = (item.get("Computador") or "").strip().lower() == row["Computador"].lower()
                    same_day = (item.get("Data_Hora") or "").startswith(today_prefix)
                    if not (same_host and same_day):
                        existing_rows.append({k: sanitize_csv_cell(item.get(k, "")) for k in fieldnames})
        except Exception:
            existing_rows = []

    existing_rows.append(row)

    from io import StringIO
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    writer.writerows(existing_rows)

    _atomic_write_text(history_csv_path, buf.getvalue(), encoding="utf-8-sig")


def executar_coleta_inventario(
    pasta_destino: str,
    specs: HardwareSpecs | None = None,
    formatos: set[str] | None = None,
    retencao_dias: int = 30,
) -> dict[str, Any]:
    """
    Executa a coleta de inventário e grava os relatórios solicitados na pasta de destino.
    Seguro para rodar em modo silencioso (CLI) ou a partir da interface gráfica.
    """
    pasta = validate_output_dir(pasta_destino)
    os.makedirs(pasta, exist_ok=True)
    historico_dir = os.path.join(pasta, "historico")
    os.makedirs(historico_dir, exist_ok=True)

    fmts = {f.strip().lower() for f in (formatos or {"json", "csv"}) if f.strip()}
    if not fmts:
        fmts = {"json", "csv"}

    if specs is None:
        specs = coletar_especificacoes()

    host_safe = sanitize_identifier(specs.hostname, fallback="PC")
    data_tag = datetime.now().strftime("%Y-%m-%d")
    latest_json_path = os.path.join(pasta, f"{host_safe}_ultimo.json")

    snapshot_anterior = None
    if os.path.isfile(latest_json_path):
        try:
            with open(latest_json_path, "r", encoding="utf-8") as f:
                snapshot_anterior = json.load(f)
        except Exception:
            snapshot_anterior = None

    alertas = detectar_alertas(specs, snapshot_anterior)
    arquivos_gerados: list[str] = []

    payload = {
        "schema_version": SCHEMA_VERSION,
        "coletado_em": specs.coletado_em,
        "hostname": specs.hostname,
        "alertas": alertas,
        "specs": asdict(specs),
    }

    if "json" in fmts:
        json_str = json.dumps(payload, ensure_ascii=False, indent=2)
        daily_json = os.path.join(historico_dir, f"{host_safe}_{data_tag}.json")
        _atomic_write_text(daily_json, json_str)
        _atomic_write_text(latest_json_path, json_str)
        arquivos_gerados.append(daily_json)

    if "csv" in fmts:
        row = _build_csv_row(specs, alertas)
        atual_csv = os.path.join(pasta, "inventario_atual.csv")
        hist_csv = os.path.join(pasta, "historico_diario.csv")
        _update_consolidated_csv(atual_csv, row)
        _append_daily_history_csv(hist_csv, row)
        arquivos_gerados.extend([atual_csv, hist_csv])

    if "pdf" in fmts:
        # Importação tardia do fpdf2 para manter o modo CLI/JSON/CSV ultraleve
        from pdf_exporter import exportar_relatorio_pdf
        pdf_path = os.path.join(historico_dir, f"MoreiraSpec_{host_safe}_{data_tag}.pdf")
        exportar_relatorio_pdf(specs, pdf_path)
        arquivos_gerados.append(pdf_path)

    removidos = _cleanup_old_snapshots(historico_dir, retencao_dias)

    return {
        "pasta": pasta,
        "hostname": specs.hostname,
        "coletado_em": specs.coletado_em,
        "alertas": alertas,
        "arquivos": arquivos_gerados,
        "antigos_removidos": removidos,
    }


def _build_scheduled_command(pasta: str, formatos: set[str], retencao_dias: int) -> str:
    """Monta a linha de comando segura que o Agendador de Tarefas executará diariamente."""
    pasta_norm = validate_output_dir(pasta)
    fmt_arg = ",".join(sorted(f for f in formatos if f in {"json", "csv", "pdf"})) or "json,csv"
    ret_arg = max(0, min(3650, int(retencao_dias)))

    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        return (
            f'"{exe_path}" --coleta-silenciosa '
            f'--pasta "{pasta_norm}" --formato {fmt_arg} --retencao {ret_arg}'
        )

    # Em desenvolvimento, prefere pythonw.exe para não abrir janela de console
    py_exe = os.path.abspath(sys.executable)
    pyw_exe = os.path.join(os.path.dirname(py_exe), "pythonw.exe")
    runner = pyw_exe if os.path.isfile(pyw_exe) else py_exe
    main_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
    return (
        f'"{runner}" "{main_script}" --coleta-silenciosa '
        f'--pasta "{pasta_norm}" --formato {fmt_arg} --retencao {ret_arg}'
    )


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Executa schtasks.exe sem shell=True e sem abrir janela de console."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        ["schtasks.exe", *args],
        capture_output=True,
        text=True,
        encoding="oem" if sys.platform == "win32" else "utf-8",
        errors="replace",
        shell=False,
        timeout=15,
        creationflags=creationflags,
    )


def consultar_agendamento_diario() -> dict[str, Any]:
    """Verifica se a tarefa agendada diária do Moreira Spec está ativa no Windows."""
    if sys.platform != "win32":
        return {"ativo": False, "detalhe": "Disponível apenas no Windows"}

    try:
        res = _run_schtasks(["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
        if res.returncode != 0:
            return {"ativo": False, "detalhe": "Nenhum agendamento ativo"}
        output = res.stdout or ""
        return {"ativo": True, "detalhe": "Agendamento diário ativo no Windows", "raw": output}
    except Exception as exc:
        return {"ativo": False, "detalhe": f"Não foi possível consultar: {exc}"}


def ativar_agendamento_diario(
    pasta: str,
    horario: str,
    formatos: set[str],
    retencao_dias: int = 30,
) -> tuple[bool, str]:
    """Cria ou atualiza a tarefa diária no Agendador de Tarefas do Windows (sem shell=True)."""
    if sys.platform != "win32":
        return False, "O agendamento automático é suportado apenas no Windows."

    hora_ok = validate_schedule_time(horario)
    pasta_ok = validate_output_dir(pasta)
    os.makedirs(pasta_ok, exist_ok=True)
    cmd = _build_scheduled_command(pasta_ok, formatos, retencao_dias)

    try:
        res = _run_schtasks([
            "/Create",
            "/TN", TASK_NAME,
            "/TR", cmd,
            "/SC", "DAILY",
            "/ST", hora_ok,
            "/RL", "LIMITED",
            "/F",
        ])
        if res.returncode == 0:
            save_schedule_config({
                "pasta": pasta_ok,
                "horario": hora_ok,
                "formato_json": "json" in formatos,
                "formato_csv": "csv" in formatos,
                "formato_pdf": "pdf" in formatos,
                "retencao_dias": max(0, min(3650, int(retencao_dias))),
            })
            return True, f"Coleta diária agendada para todos os dias às {hora_ok} em:\n{pasta_ok}"
        err = (res.stderr or res.stdout or "Erro desconhecido").strip()
        return False, f"Falha ao criar agendamento no Windows:\n{err}"
    except Exception as exc:
        return False, f"Erro ao acionar o Agendador de Tarefas:\n{exc}"


def desativar_agendamento_diario() -> tuple[bool, str]:
    """Remove a tarefa agendada diária do Windows."""
    if sys.platform != "win32":
        return False, "Disponível apenas no Windows."
    try:
        res = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
        if res.returncode == 0:
            return True, "Agendamento diário removido com sucesso."
        err = (res.stderr or res.stdout or "").strip()
        return False, f"Não foi possível remover o agendamento:\n{err}"
    except Exception as exc:
        return False, f"Erro ao remover agendamento:\n{exc}"
