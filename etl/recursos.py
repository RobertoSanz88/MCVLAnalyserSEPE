"""Deteccion de la RAM total del equipo para adaptar automaticamente el ETL: en una
maquina con RAM de sobra (PC de oficina tipico, no la de desarrollo con ~4GB que
motivo todo el diseno por trozos/DuckDB de optimize_types.py y personas_extendida.py)
se puede leer en trozos mas grandes y dar mas hilos a DuckDB sin arriesgar un
MemoryError, lo que acelera el proceso sin tocar la logica (mismo codigo, mismos
resultados, solo cambian los tamanyos de trozo/paralelismo).

Solo Windows (unico SO objetivo del proyecto, ver CLAUDE.md) - usa la API nativa
GlobalMemoryStatusEx en vez de anadir una dependencia nueva (p.ej. psutil) solo para
esto.
"""

import ctypes
import os

UMBRAL_RAM_HOLGADA_GB = 10


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def ram_total_gb():
    """RAM fisica total del equipo, en GB."""
    estado = _MEMORYSTATUSEX()
    estado.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado))
    return estado.ullTotalPhys / (1024**3)


def ram_holgada(ram_gb=None):
    return (ram_total_gb() if ram_gb is None else ram_gb) >= UMBRAL_RAM_HOLGADA_GB


def chunksize_optimo(ram_gb=None):
    """Filas por trozo en leer_csv_por_trozos_a_parquet. 100_000 es el valor
    verificado en la maquina de desarrollo (~4GB); con RAM de sobra, trozos mas
    grandes significan menos vueltas y mas rendimiento (menos overhead por trozo)."""
    return 500_000 if ram_holgada(ram_gb) else 100_000


def threads_optimos(ram_gb=None):
    """Hilos para DuckDB. 2 es el valor verificado en la maquina de desarrollo -
    mas hilos en sorts/agregados grandes usan mas memoria en paralelo, lo que en
    ~4GB provocaba agotar disco (ver notas de fase1_afiliacion_extendida_duckdb)."""
    if not ram_holgada(ram_gb):
        return 2
    return min(os.cpu_count() or 4, 8)
