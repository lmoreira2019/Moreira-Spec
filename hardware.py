"""
hardware.py - Módulo de coleta de especificações de hardware para o Moreira Spec.

Utiliza WMI (Windows), registro do Windows (para VRAM > 4 GB), psutil, platform e socket.
Todas as consultas WMI possuem tratamento de exceções individual para garantir estabilidade
em diferentes máquinas e versões do Windows.
"""

from __future__ import annotations

import platform
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psutil

try:
    import pythoncom
    HAS_PYTHONCOM = True
except Exception:
    pythoncom = None
    HAS_PYTHONCOM = False

try:
    import winreg
    HAS_WINREG = True
except Exception:
    winreg = None
    HAS_WINREG = False


# Mapeamento SMBIOSMemoryType (padrão DMTF SMBIOS 3.x) -> nome legível
SMBIOS_MEMORY_TYPES: dict[int, str] = {
    0: "Desconhecido",
    1: "Outro",
    2: "DRAM",
    3: "DRAM Síncrona",
    4: "DRAM Cache",
    5: "EDO",
    6: "EDRAM",
    7: "VRAM",
    8: "SRAM",
    9: "RAM",
    10: "ROM",
    11: "Flash",
    12: "EEPROM",
    13: "FEPROM",
    14: "EPROM",
    15: "CDRAM",
    16: "3DRAM",
    17: "SDRAM",
    18: "SGRAM",
    19: "RDRAM",
    20: "DDR",
    21: "DDR2",
    22: "DDR2 FB-DIMM",
    24: "DDR3",
    25: "FBD2",
    26: "DDR4",
    27: "LPDDR",
    28: "LPDDR2",
    29: "LPDDR3",
    30: "LPDDR4",
    31: "Memória Não-Volátil",
    32: "HBM",
    33: "HBM2",
    34: "DDR5",
    35: "LPDDR5",
    36: "HBM3",
}

# Mapeamento legado MemoryType (fallback)
LEGACY_MEMORY_TYPES: dict[int, str] = {
    20: "DDR",
    21: "DDR2",
    22: "DDR2 FB-DIMM",
    24: "DDR3",
    26: "DDR4",
    34: "DDR5",
}

CPU_VENDOR_NAMES: dict[str, str] = {
    "GenuineIntel": "Intel (GenuineIntel)",
    "AuthenticAMD": "AMD (AuthenticAMD)",
    "CentaurHauls": "VIA / Centaur",
    "ARM": "ARM",
}

INVALID_SERIAL_MARKERS = {
    "",
    "none",
    "default string",
    "to be filled by o.e.m.",
    "not specified",
    "not available",
    "system serial number",
    "00000000",
    "0",
}


def _clean_str(value: Any, fallback: str = "Não disponível") -> str:
    """Converte um valor qualquer em string limpa, removendo caracteres de controle."""
    if value is None:
        return fallback
    raw = str(value).replace("\x00", "")
    # Remove caracteres de controle ASCII (0x00-0x1F, 0x7F) para segurança e integridade
    sanitized = "".join(ch if ord(ch) >= 32 and ord(ch) != 127 else " " for ch in raw)
    text = " ".join(sanitized.strip().split())
    return text if text else fallback


def _clean_serial(value: Any) -> str:
    """Limpa números de série genéricos de OEM."""
    text = _clean_str(value, "")
    if text.lower() in INVALID_SERIAL_MARKERS:
        return "Não disponível"
    return text


def _format_bytes_gb(num_bytes: int | float | None, decimals: int = 2) -> str:
    """Formata bytes em GB (ou TB quando >= 1024 GB)."""
    if not num_bytes or num_bytes <= 0:
        return "Não disponível"
    gb = num_bytes / (1024 ** 3)
    if gb >= 1024:
        return f"{gb / 1024:.{decimals}f} TB ({gb:.0f} GB)"
    return f"{gb:.{decimals}f} GB"


def _format_disk_size(num_bytes: int | float | None) -> str:
    """Formata tamanho de disco físico em GB/TB decimais e binários de forma clara."""
    if not num_bytes or num_bytes <= 0:
        return "Não disponível"
    gb_bin = num_bytes / (1024 ** 3)
    if gb_bin >= 1024:
        return f"{gb_bin / 1024:.2f} TB ({gb_bin:.0f} GB)"
    return f"{gb_bin:.1f} GB"


def format_uptime(seconds: float) -> str:
    """Converte segundos em formato legível (ex: 1d 04h 22m 15s)."""
    total_seconds = max(0, int(seconds))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    parts.append(f"{hours:02d}h")
    parts.append(f"{minutes:02d}m")
    parts.append(f"{secs:02d}s")
    return " ".join(parts)


def _format_wmi_date(raw_date: Any) -> str:
    """Formata datas WMI (YYYYMMDD...) para DD/MM/AAAA."""
    text = _clean_str(raw_date, "")
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[6:8]}/{text[4:6]}/{text[0:4]}"
    return "Não disponível"


def _read_reg_str(root: int, path: str, value_name: str) -> str:
    """Lê uma string do Registro do Windows de forma segura."""
    if not HAS_WINREG or platform.system() != "Windows":
        return ""
    try:
        with winreg.OpenKey(root, path) as key:
            val, _ = winreg.QueryValueEx(key, value_name)
            return _clean_str(val, "")
    except OSError:
        return ""


def _read_cpu_temperature(root_wmi_conn: Any) -> tuple[str, float | None]:
    """
    Tenta ler a temperatura da CPU via WMI (MSAcpi_ThermalZoneTemperature em root\\wmi).
    Converte décimos de Kelvin para Celsius: (valor / 10) - 273.15.
    Nunca lança exceção; retorna mensagem amigável caso bloqueado/indisponível.
    """
    fallback_msg = "Indisponível (requer Admin/sensor compatível)"
    if root_wmi_conn is None:
        return fallback_msg, None

    temps_c: list[float] = []
    try:
        for zone in root_wmi_conn.MSAcpi_ThermalZoneTemperature():
            try:
                raw_temp = getattr(zone, "CurrentTemperature", None)
                if raw_temp is not None:
                    celsius = (float(raw_temp) / 10.0) - 273.15
                    if 0.0 < celsius < 130.0:
                        temps_c.append(celsius)
            except Exception:
                continue
    except Exception:
        return fallback_msg, None

    if not temps_c:
        return fallback_msg, None

    temp_val = max(temps_c)
    return f"{temp_val:.1f} °C", temp_val


def _read_smart_predict_status(root_wmi_conn: Any) -> tuple[bool, list[dict[str, Any]]]:
    """
    Lê MSStorageDriver_FailurePredictStatus no namespace root\\wmi (requer Admin).
    Retorna (sucesso_leitura, lista_de_status).
    """
    if root_wmi_conn is None:
        return False, []

    entries: list[dict[str, Any]] = []
    try:
        for item in root_wmi_conn.MSStorageDriver_FailurePredictStatus():
            try:
                instance = _clean_str(getattr(item, "InstanceName", None), "").upper()
                predict_failure = bool(getattr(item, "PredictFailure", False))
                reason = int(getattr(item, "Reason", 0) or 0)
                entries.append({
                    "instance": instance,
                    "predict_failure": predict_failure,
                    "reason": reason,
                })
            except Exception:
                continue
        return len(entries) > 0, entries
    except Exception:
        return False, []


def _evaluate_disk_health(
    wmi_status: str,
    pnp_device_id: str,
    disk_index: int,
    smart_available: bool,
    smart_entries: list[dict[str, Any]],
) -> tuple[str, str, str]:
    """
    Avalia a saúde de um disco combinando Win32_DiskDrive.Status e S.M.A.R.T. (root\\wmi).
    Retorna (texto_badge, nivel_cor ['ok' | 'warn' | 'unknown'], detalhe_smart).
    """
    status_norm = wmi_status.strip().lower()
    warn_keywords = ("pred fail", "degraded", "error", "stressed", "nonrecover", "lost comm")
    is_wmi_warn = any(k in status_norm for k in warn_keywords)
    is_wmi_ok = status_norm == "ok"

    matched_smart: dict[str, Any] | None = None
    if smart_available and smart_entries:
        pnp_norm = pnp_device_id.strip().upper()
        if pnp_norm:
            for entry in smart_entries:
                inst = entry["instance"]
                if pnp_norm in inst or (len(pnp_norm) > 12 and pnp_norm[:20] in inst):
                    matched_smart = entry
                    break
        if matched_smart is None and disk_index < len(smart_entries):
            matched_smart = smart_entries[disk_index]

    if matched_smart is not None:
        if matched_smart["predict_failure"]:
            return "Atenção", "warn", "S.M.A.R.T.: Alerta de falha prevista"
        if is_wmi_warn:
            return "Atenção", "warn", f"WMI: {wmi_status} | S.M.A.R.T.: OK"
        return "Saudável / OK", "ok", "S.M.A.R.T.: Saudável"

    if is_wmi_warn:
        return "Atenção", "warn", f"WMI: {wmi_status} | Execute como Admin para S.M.A.R.T."
    if is_wmi_ok:
        return "Saudável / OK", "ok", "Execute como Admin para S.M.A.R.T."
    return "Execute como Admin para S.M.A.R.T.", "unknown", "Execute como Admin para S.M.A.R.T."


def _get_registry_gpu_vram() -> dict[str, int]:
    """
    Lê a memória VRAM de 64 bits das GPUs no Registro do Windows.
    Resolve o limite de 32 bits (4 GB) da propriedade Win32_VideoController.AdapterRAM.
    Retorna um dicionário {nome_normalizado: bytes_vram}.
    """
    vram_map: dict[str, int] = {}
    if not HAS_WINREG or platform.system() != "Windows":
        return vram_map

    base_path = r"SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path) as root_key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root_key, index)
                    index += 1
                except OSError:
                    break

                if not subkey_name.isdigit():
                    continue

                try:
                    with winreg.OpenKey(root_key, subkey_name) as gpu_key:
                        try:
                            desc, _ = winreg.QueryValueEx(gpu_key, "DriverDesc")
                        except OSError:
                            continue

                        vram_bytes = 0
                        for reg_val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                val, _ = winreg.QueryValueEx(gpu_key, reg_val)
                                if isinstance(val, int) and val > 0:
                                    vram_bytes = val
                                    break
                                if isinstance(val, bytes) and len(val) >= 4:
                                    vram_bytes = int.from_bytes(val[:8], "little", signed=False)
                                    if vram_bytes > 0:
                                        break
                            except OSError:
                                continue

                        if desc and vram_bytes > 0:
                            vram_map[_clean_str(desc, "").lower()] = vram_bytes
                except OSError:
                    continue
    except OSError:
        pass

    return vram_map


def _get_local_ip() -> str:
    """Obtém o endereço IPv4 local principal da máquina."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass

    try:
        for _, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith(("127.", "169.254.")):
                    return addr.address
    except Exception:
        pass

    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "Não disponível"


def _get_network_interfaces() -> list[dict[str, str]]:
    """Lista adaptadores de rede ativos com endereço IPv4."""
    interfaces: list[dict[str, str]] = []
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        for iface_name, addr_list in addrs.items():
            iface_stat = stats.get(iface_name)
            is_up = bool(iface_stat and iface_stat.isup)
            ipv4 = ""
            mac = ""
            for addr in addr_list:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ipv4 = addr.address
                elif getattr(psutil, "AF_LINK", -1) == addr.family:
                    mac = addr.address
            if ipv4:
                speed = f"{iface_stat.speed} Mbps" if iface_stat and iface_stat.speed > 0 else "N/A"
                interfaces.append({
                    "Adaptador": iface_name,
                    "Status": "Ativo" if is_up else "Inativo",
                    "IPv4": ipv4,
                    "MAC": mac or "N/A",
                    "Velocidade": speed,
                })
    except Exception:
        pass
    return interfaces


def get_live_metrics(
    need_cpu: bool = True,
    need_ram: bool = True,
    need_uptime: bool = True,
) -> dict[str, Any]:
    """
    Coleta apenas as métricas rápidas necessárias para a aba visível (baixo uso de CPU).
    """
    metrics: dict[str, Any] = {}

    if need_cpu:
        cpu_percent = 0.0
        cpu_freq_current = "N/A"
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
        except Exception:
            pass
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current:
                cpu_freq_current = f"{freq.current:.0f} MHz ({freq.current / 1000:.2f} GHz)"
        except Exception:
            pass
        metrics["cpu_percent"] = cpu_percent
        metrics["cpu_freq_current"] = cpu_freq_current

    if need_ram:
        ram_percent = 0.0
        ram_used_gb = "N/A"
        ram_avail_gb = "N/A"
        try:
            vm = psutil.virtual_memory()
            ram_percent = vm.percent
            ram_used_gb = _format_bytes_gb(vm.used)
            ram_avail_gb = _format_bytes_gb(vm.available)
        except Exception:
            pass
        metrics["ram_percent"] = ram_percent
        metrics["ram_used_gb"] = ram_used_gb
        metrics["ram_avail_gb"] = ram_avail_gb

    if need_uptime:
        uptime_str = "N/A"
        try:
            uptime_str = format_uptime(time.time() - psutil.boot_time())
        except Exception:
            pass
        metrics["uptime"] = uptime_str

    return metrics


@dataclass
class HardwareSpecs:
    """Estrutura consolidada de todas as especificações coletadas."""
    coletado_em: str = ""
    hostname: str = ""
    resumo: dict[str, str] = field(default_factory=dict)
    cpu: dict[str, Any] = field(default_factory=dict)
    placa_mae: dict[str, str] = field(default_factory=dict)
    bios: dict[str, str] = field(default_factory=dict)
    ram: dict[str, Any] = field(default_factory=dict)
    ram_modulos: list[dict[str, str]] = field(default_factory=list)
    gpus: list[dict[str, str]] = field(default_factory=list)
    discos_fisicos: list[dict[str, str]] = field(default_factory=list)
    particoes: list[dict[str, Any]] = field(default_factory=list)
    sistema: dict[str, str] = field(default_factory=dict)
    rede_interfaces: list[dict[str, str]] = field(default_factory=list)


def coletar_especificacoes() -> HardwareSpecs:
    """
    Coleta todas as especificações de hardware e sistema.
    Seguro para execução em threads secundárias (inicializa COM automaticamente).
    """
    specs = HardwareSpecs(
        coletado_em=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        hostname=socket.gethostname(),
    )

    com_initialized = False
    wmi_conn = None
    root_wmi_conn = None

    if platform.system() == "Windows":
        if HAS_PYTHONCOM:
            try:
                pythoncom.CoInitialize()
                com_initialized = True
            except Exception:
                pass
        try:
            import wmi  # Importação tardia para não travar a abertura da janela
            wmi_conn = wmi.WMI()
            try:
                root_wmi_conn = wmi.WMI(namespace=r"root\wmi")
            except Exception:
                root_wmi_conn = None
        except Exception:
            wmi_conn = None
            root_wmi_conn = None

    try:
        # 1. Processador (CPU)
        cpu_modelo = _clean_str(platform.processor(), "Não identificado")
        cpu_fabricante = "Não disponível"
        cpu_socket = "Não disponível"
        cpu_cache_l2 = "Não disponível"
        cpu_cache_l3 = "Não disponível"
        wmi_max_mhz = None
        wmi_cur_mhz = None
        wmi_cores = None
        wmi_threads = None

        if wmi_conn is not None:
            try:
                cpu_rows = wmi_conn.query(
                    "SELECT Name, Manufacturer, SocketDesignation, MaxClockSpeed, "
                    "CurrentClockSpeed, NumberOfCores, NumberOfLogicalProcessors, "
                    "L2CacheSize, L3CacheSize FROM Win32_Processor"
                )
                for cpu in cpu_rows:
                    cpu_modelo = _clean_str(getattr(cpu, "Name", None), cpu_modelo)
                    raw_mfg = _clean_str(getattr(cpu, "Manufacturer", None), "Não disponível")
                    cpu_fabricante = CPU_VENDOR_NAMES.get(raw_mfg, raw_mfg)
                    cpu_socket = _clean_str(getattr(cpu, "SocketDesignation", None), "Não disponível")
                    wmi_max_mhz = getattr(cpu, "MaxClockSpeed", None)
                    wmi_cur_mhz = getattr(cpu, "CurrentClockSpeed", None)
                    wmi_cores = getattr(cpu, "NumberOfCores", None)
                    wmi_threads = getattr(cpu, "NumberOfLogicalProcessors", None)
                    l2 = getattr(cpu, "L2CacheSize", None)
                    l3 = getattr(cpu, "L3CacheSize", None)
                    if l2 and int(l2) > 0:
                        cpu_cache_l2 = f"{int(l2) / 1024:.1f} MB" if int(l2) >= 1024 else f"{l2} KB"
                    if l3 and int(l3) > 0:
                        cpu_cache_l3 = f"{int(l3) / 1024:.1f} MB" if int(l3) >= 1024 else f"{l3} KB"
                    break
            except Exception:
                pass

        # Fallback via Registro do Windows caso WMI não esteja acessível
        if platform.system() == "Windows" and HAS_WINREG:
            if wmi_conn is None or cpu_modelo in ("Não identificado", platform.processor()):
                reg_cpu = _read_reg_str(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                    "ProcessorNameString",
                )
                if reg_cpu:
                    cpu_modelo = reg_cpu
            if cpu_fabricante == "Não disponível":
                reg_vendor = _read_reg_str(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                    "VendorIdentifier",
                )
                if reg_vendor:
                    cpu_fabricante = CPU_VENDOR_NAMES.get(reg_vendor, reg_vendor)

        freq = None
        try:
            freq = psutil.cpu_freq()
        except Exception:
            freq = None

        freq_atual_mhz = freq.current if (freq and freq.current) else (float(wmi_cur_mhz) if wmi_cur_mhz else None)
        freq_max_mhz = freq.max if (freq and freq.max) else (float(wmi_max_mhz) if wmi_max_mhz else None)

        nucleos_fisicos = psutil.cpu_count(logical=False) or wmi_cores or "N/A"
        threads_logicos = psutil.cpu_count(logical=True) or wmi_threads or "N/A"

        try:
            cpu_uso = psutil.cpu_percent(interval=0.1)
        except Exception:
            cpu_uso = 0.0

        cpu_temp_str, cpu_temp_c = _read_cpu_temperature(root_wmi_conn)

        specs.cpu = {
            "Modelo": cpu_modelo,
            "Fabricante": cpu_fabricante,
            "Arquitetura": f"{platform.machine()} ({platform.architecture()[0]})",
            "Núcleos Físicos": str(nucleos_fisicos),
            "Threads (Lógicos)": str(threads_logicos),
            "Frequência Atual": f"{freq_atual_mhz:.0f} MHz ({freq_atual_mhz / 1000:.2f} GHz)" if freq_atual_mhz else "N/A",
            "Frequência Máxima": f"{freq_max_mhz:.0f} MHz ({freq_max_mhz / 1000:.2f} GHz)" if freq_max_mhz else "N/A",
            "Socket": cpu_socket,
            "Cache L2 / L3": f"{cpu_cache_l2} / {cpu_cache_l3}",
            "Temperatura": cpu_temp_str,
            "Temperatura C": cpu_temp_c,
            "Uso Atual": cpu_uso,
        }

        # 2. Placa-Mãe & BIOS
        mb_fabricante = "Não disponível"
        mb_modelo = "Não disponível"
        mb_serial = "Não disponível"
        mb_versao = "Não disponível"

        if wmi_conn is not None:
            try:
                for board in wmi_conn.Win32_BaseBoard():
                    mb_fabricante = _clean_str(getattr(board, "Manufacturer", None))
                    mb_modelo = _clean_str(getattr(board, "Product", None))
                    mb_serial = _clean_serial(getattr(board, "SerialNumber", None))
                    mb_versao = _clean_str(getattr(board, "Version", None))
                    break
            except Exception:
                pass

        # Fallback via Registro do Windows para Placa-Mãe
        if platform.system() == "Windows" and HAS_WINREG:
            bios_reg_path = r"HARDWARE\DESCRIPTION\System\BIOS"
            if mb_fabricante == "Não disponível":
                mb_fabricante = _read_reg_str(winreg.HKEY_LOCAL_MACHINE, bios_reg_path, "BaseBoardManufacturer") or mb_fabricante
            if mb_modelo == "Não disponível":
                mb_modelo = _read_reg_str(winreg.HKEY_LOCAL_MACHINE, bios_reg_path, "BaseBoardProduct") or mb_modelo
            if mb_versao == "Não disponível":
                mb_versao = _read_reg_str(winreg.HKEY_LOCAL_MACHINE, bios_reg_path, "BaseBoardVersion") or mb_versao
        elif platform.system() != "Windows":
            mb_modelo = "Disponível apenas no Windows (WMI)"

        specs.placa_mae = {
            "Fabricante": mb_fabricante,
            "Modelo": mb_modelo,
            "Versão": mb_versao,
            "Número de Série": mb_serial,
        }

        bios_fabricante = "Não disponível"
        bios_versao = "Não disponível"
        bios_data = "Não disponível"
        bios_serial = "Não disponível"

        if wmi_conn is not None:
            try:
                for bios in wmi_conn.Win32_BIOS():
                    bios_fabricante = _clean_str(getattr(bios, "Manufacturer", None))
                    smbios_ver = _clean_str(getattr(bios, "SMBIOSBIOSVersion", None), "")
                    raw_ver = _clean_str(getattr(bios, "Version", None), "")
                    bios_versao = smbios_ver or raw_ver or "Não disponível"
                    bios_data = _format_wmi_date(getattr(bios, "ReleaseDate", None))
                    bios_serial = _clean_serial(getattr(bios, "SerialNumber", None))
                    break
            except Exception:
                pass

        # Fallback via Registro do Windows para BIOS
        if platform.system() == "Windows" and HAS_WINREG:
            bios_reg_path = r"HARDWARE\DESCRIPTION\System\BIOS"
            if bios_fabricante == "Não disponível":
                bios_fabricante = _read_reg_str(winreg.HKEY_LOCAL_MACHINE, bios_reg_path, "BIOSVendor") or bios_fabricante
            if bios_versao == "Não disponível":
                bios_versao = _read_reg_str(winreg.HKEY_LOCAL_MACHINE, bios_reg_path, "BIOSVersion") or bios_versao
            if bios_data == "Não disponível":
                bios_data = _read_reg_str(winreg.HKEY_LOCAL_MACHINE, bios_reg_path, "BIOSReleaseDate") or bios_data

        specs.bios = {
            "Fabricante da BIOS": bios_fabricante,
            "Versão da BIOS": bios_versao,
            "Data de Lançamento": bios_data,
            "Série / SMBIOS": bios_serial,
        }

        # 3. Memória RAM (Geral + Pentes físicos)
        try:
            vm = psutil.virtual_memory()
            specs.ram = {
                "Total Instalado": _format_bytes_gb(vm.total),
                "Em Uso": f"{_format_bytes_gb(vm.used)} ({vm.percent:.1f}%)",
                "Disponível": _format_bytes_gb(vm.available),
                "Percentual": vm.percent,
                "Total Bytes": vm.total,
            }
        except Exception:
            specs.ram = {
                "Total Instalado": "N/A",
                "Em Uso": "N/A",
                "Disponível": "N/A",
                "Percentual": 0.0,
                "Total Bytes": 0,
            }

        modulos: list[dict[str, str]] = []
        if wmi_conn is not None:
            try:
                for idx, mem in enumerate(wmi_conn.Win32_PhysicalMemory(), start=1):
                    cap_raw = getattr(mem, "Capacity", None)
                    cap_gb = _format_bytes_gb(int(cap_raw), decimals=0) if cap_raw else "N/A"

                    speed = getattr(mem, "ConfiguredClockSpeed", None) or getattr(mem, "Speed", None)
                    vel_str = f"{speed} MHz" if speed else "N/A"

                    smbios_type = getattr(mem, "SMBIOSMemoryType", None)
                    legacy_type = getattr(mem, "MemoryType", None)
                    tipo_str = "Desconhecido"
                    if smbios_type is not None and int(smbios_type) in SMBIOS_MEMORY_TYPES and int(smbios_type) != 0:
                        tipo_str = SMBIOS_MEMORY_TYPES[int(smbios_type)]
                    elif legacy_type is not None and int(legacy_type) in LEGACY_MEMORY_TYPES:
                        tipo_str = LEGACY_MEMORY_TYPES[int(legacy_type)]

                    slot = _clean_str(
                        getattr(mem, "DeviceLocator", None) or getattr(mem, "BankLabel", None),
                        f"Slot {idx}",
                    )
                    mfg = _clean_str(getattr(mem, "Manufacturer", None))
                    part = _clean_str(getattr(mem, "PartNumber", None))

                    modulos.append({
                        "Slot": slot,
                        "Capacidade": cap_gb,
                        "Tipo": tipo_str,
                        "Velocidade": vel_str,
                        "Fabricante": mfg,
                        "Part Number": part,
                    })
            except Exception:
                pass
        specs.ram_modulos = modulos

        # 4. Placa de Vídeo (GPU)
        gpus: list[dict[str, str]] = []
        reg_vram_map = _get_registry_gpu_vram()

        if wmi_conn is not None:
            try:
                for gpu in wmi_conn.Win32_VideoController():
                    nome = _clean_str(getattr(gpu, "Name", None), "GPU Desconhecida")
                    driver = _clean_str(getattr(gpu, "DriverVersion", None))
                    driver_date = _format_wmi_date(getattr(gpu, "DriverDate", None))

                    # Busca VRAM 64-bit no registro primeiro; fallback para AdapterRAM
                    vram_bytes = reg_vram_map.get(nome.lower(), 0)
                    if not vram_bytes:
                        for reg_name, reg_bytes in reg_vram_map.items():
                            if reg_name in nome.lower() or nome.lower() in reg_name:
                                vram_bytes = reg_bytes
                                break

                    if not vram_bytes:
                        adapter_ram = getattr(gpu, "AdapterRAM", None)
                        if adapter_ram and int(adapter_ram) > 0:
                            vram_bytes = int(adapter_ram)

                    vram_str = _format_bytes_gb(vram_bytes) if vram_bytes > 0 else "Compartilhada / N/A"

                    h_res = getattr(gpu, "CurrentHorizontalResolution", None)
                    v_res = getattr(gpu, "CurrentVerticalResolution", None)
                    refresh = getattr(gpu, "CurrentRefreshRate", None)
                    if h_res and v_res:
                        res_str = f"{h_res} x {v_res}" + (f" @ {refresh} Hz" if refresh else "")
                    else:
                        res_str = "Não ativa / N/A"

                    gpus.append({
                        "Modelo": nome,
                        "VRAM Dedicada": vram_str,
                        "Versão do Driver": driver,
                        "Data do Driver": driver_date,
                        "Resolução Atual": res_str,
                    })
            except Exception:
                pass

        if not gpus:
            gpus.append({
                "Modelo": "Nenhuma GPU detectada via WMI",
                "VRAM Dedicada": "N/A",
                "Versão do Driver": "N/A",
                "Data do Driver": "N/A",
                "Resolução Atual": "N/A",
            })
        specs.gpus = gpus

        # 5. Armazenamento (Discos Físicos + Saúde/S.M.A.R.T. + Partições)
        discos_fisicos: list[dict[str, str]] = []
        smart_available, smart_entries = _read_smart_predict_status(root_wmi_conn)
        if wmi_conn is not None:
            try:
                for idx, disk in enumerate(wmi_conn.Win32_DiskDrive()):
                    modelo = _clean_str(getattr(disk, "Model", None), "Disco Desconhecido")
                    size_raw = getattr(disk, "Size", None)
                    tamanho = _format_disk_size(int(size_raw)) if size_raw else "N/A"
                    interface = _clean_str(getattr(disk, "InterfaceType", None), "N/A")
                    midia = _clean_str(getattr(disk, "MediaType", None), "N/A")
                    wmi_status = _clean_str(getattr(disk, "Status", None), "")
                    pnp_id = _clean_str(getattr(disk, "PNPDeviceID", None), "")

                    saude_badge, saude_nivel, smart_detalhe = _evaluate_disk_health(
                        wmi_status=wmi_status,
                        pnp_device_id=pnp_id,
                        disk_index=idx,
                        smart_available=smart_available,
                        smart_entries=smart_entries,
                    )

                    discos_fisicos.append({
                        "Modelo": modelo,
                        "Tamanho": tamanho,
                        "Interface": interface,
                        "Tipo de Mídia": midia,
                        "Status WMI": wmi_status or "Não disponível",
                        "Saúde": saude_badge,
                        "Saúde Nível": saude_nivel,
                        "Detalhe S.M.A.R.T.": smart_detalhe,
                    })
            except Exception:
                pass
        specs.discos_fisicos = discos_fisicos

        particoes: list[dict[str, Any]] = []
        total_armazenamento_bytes = 0
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    uso = psutil.disk_usage(part.mountpoint)
                    total_armazenamento_bytes += uso.total
                    particoes.append({
                        "Dispositivo": part.device,
                        "Ponto de Montagem": part.mountpoint,
                        "Sistema de Arquivos": part.fstype or "N/A",
                        "Total": _format_disk_size(uso.total),
                        "Usado": _format_disk_size(uso.used),
                        "Livre": _format_disk_size(uso.free),
                        "Percentual": uso.percent,
                    })
                except (PermissionError, OSError):
                    continue
        except Exception:
            pass
        specs.particoes = particoes

        # 6. Sistema & Rede
        os_nome = f"{platform.system()} {platform.release()}"
        os_versao = platform.version()
        os_build = "N/A"
        os_arch = platform.machine()

        if wmi_conn is not None:
            try:
                for os_info in wmi_conn.Win32_OperatingSystem():
                    os_nome = _clean_str(getattr(os_info, "Caption", None), os_nome)
                    os_versao = _clean_str(getattr(os_info, "Version", None), os_versao)
                    os_build = _clean_str(getattr(os_info, "BuildNumber", None), os_build)
                    os_arch = _clean_str(getattr(os_info, "OSArchitecture", None), os_arch)
                    break
            except Exception:
                pass

        try:
            boot_ts = psutil.boot_time()
            uptime_str = format_uptime(time.time() - boot_ts)
            boot_dt = datetime.fromtimestamp(boot_ts).strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            uptime_str = "N/A"
            boot_dt = "N/A"

        local_ip = _get_local_ip()
        specs.rede_interfaces = _get_network_interfaces()

        specs.sistema = {
            "Nome do Computador": specs.hostname,
            "Sistema Operacional": os_nome,
            "Versão / Build": f"{os_versao} (Build {os_build})" if os_build != "N/A" else os_versao,
            "Arquitetura do SO": os_arch,
            "Tempo Ligado (Uptime)": uptime_str,
            "Última Inicialização": boot_dt,
            "Endereço IP Local": local_ip,
        }

        # 7. Resumo (Visão Geral)
        gpu_resumo = " | ".join(g["Modelo"] for g in specs.gpus)
        mb_resumo = (
            f"{mb_fabricante} {mb_modelo}".strip()
            if mb_fabricante != "Não disponível" and mb_fabricante.lower() not in mb_modelo.lower()
            else mb_modelo
        )

        if discos_fisicos:
            arm_resumo = f"{len(discos_fisicos)} disco(s) físico(s) — " + ", ".join(
                f"{d['Modelo']} ({d['Tamanho']})" for d in discos_fisicos[:2]
            )
            if len(discos_fisicos) > 2:
                arm_resumo += f" +{len(discos_fisicos) - 2}"
        elif particoes:
            arm_resumo = f"{len(particoes)} partição(ões) ({_format_disk_size(total_armazenamento_bytes)} total)"
        else:
            arm_resumo = "Não disponível"

        ram_resumo = specs.ram.get("Total Instalado", "N/A")
        if modulos:
            tipos = sorted({m["Tipo"] for m in modulos if m["Tipo"] != "Desconhecido"})
            vels = sorted({m["Velocidade"] for m in modulos if m["Velocidade"] != "N/A"})
            extras = [f"{len(modulos)}x módulo(s)"]
            if tipos:
                extras.append("/".join(tipos))
            if vels:
                extras.append("/".join(vels))
            ram_resumo += f" ({', '.join(extras)})"

        specs.resumo = {
            "Processador (CPU)": f"{cpu_modelo} ({nucleos_fisicos}N / {threads_logicos}T)",
            "Placa de Vídeo (GPU)": gpu_resumo,
            "Memória RAM": ram_resumo,
            "Placa-Mãe": mb_resumo,
            "Sistema Operacional": f"{os_nome} ({os_arch})",
            "Armazenamento": arm_resumo,
        }

    finally:
        if com_initialized and HAS_PYTHONCOM:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    return specs


def formatar_resumo_whatsapp(specs: HardwareSpecs) -> str:
    """Formata um resumo limpo das especificações com negrito do WhatsApp (*texto*)."""
    cpu_modelo = specs.cpu.get("Modelo", "Não identificado")
    nucleos = specs.cpu.get("Núcleos Físicos", "N/A")
    threads = specs.cpu.get("Threads (Lógicos)", "N/A")
    cpu_linha = f"{cpu_modelo} ({nucleos} núcleos / {threads} threads)"

    mb_fab = specs.placa_mae.get("Fabricante", "Não disponível")
    mb_mod = specs.placa_mae.get("Modelo", "Não disponível")
    if mb_fab != "Não disponível" and mb_fab.lower() not in mb_mod.lower():
        placa_mae_linha = f"{mb_fab} {mb_mod}".strip()
    else:
        placa_mae_linha = mb_mod

    ram_total = specs.ram.get("Total Instalado", "N/A")
    if specs.ram_modulos:
        qtd = len(specs.ram_modulos)
        pente_txt = "pente" if qtd == 1 else "pentes"
        tipos = sorted({m["Tipo"] for m in specs.ram_modulos if m.get("Tipo") not in (None, "", "Desconhecido")})
        freqs = sorted({m["Velocidade"] for m in specs.ram_modulos if m.get("Velocidade") not in (None, "", "N/A")})
        detalhes_ram = [f"{qtd} {pente_txt}"]
        if tipos:
            detalhes_ram.append("/".join(tipos))
        if freqs:
            detalhes_ram.append("/".join(freqs))
        ram_linha = f"{ram_total} ({', '.join(detalhes_ram)})"
    else:
        ram_linha = ram_total

    gpu_partes: list[str] = []
    for gpu in specs.gpus:
        modelo = gpu.get("Modelo", "N/A")
        vram = gpu.get("VRAM Dedicada", "N/A")
        if vram not in ("N/A", "Compartilhada / N/A"):
            gpu_partes.append(f"{modelo} ({vram})")
        else:
            gpu_partes.append(modelo)
    gpu_linha = " | ".join(gpu_partes) if gpu_partes else "Não detectada"

    if specs.discos_fisicos:
        arm_linha = " | ".join(
            f"{d.get('Modelo', 'Disco')} ({d.get('Tamanho', 'N/A')})"
            for d in specs.discos_fisicos
        )
    elif specs.particoes:
        arm_linha = " | ".join(
            f"{p.get('Dispositivo', '')} {p.get('Total', 'N/A')} ({p.get('Livre', 'N/A')} livre)"
            for p in specs.particoes
        )
    else:
        arm_linha = "Não disponível"

    so_nome = specs.sistema.get("Sistema Operacional", "Windows")
    so_arch = specs.sistema.get("Arquitetura do SO", "")
    so_linha = f"{so_nome} ({so_arch})" if so_arch and so_arch not in so_nome else so_nome

    linhas = [
        "*MOREIRA SPEC - Resumo da Máquina*",
        "",
        f"*Processador:* {cpu_linha}",
        f"*Placa-Mãe:* {placa_mae_linha}",
        f"*Memória RAM:* {ram_linha}",
        f"*Placa de Vídeo (GPU):* {gpu_linha}",
        f"*Armazenamento:* {arm_linha}",
        f"*Sistema Operacional:* {so_linha}",
    ]
    return "\n".join(linhas)
