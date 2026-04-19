from __future__ import annotations

import sys
import threading
import time

from rich.progress import ProgressColumn
from rich.text import Text

try:
    import psutil
except ImportError:
    psutil = None


class HardwareMonitorColumn(ProgressColumn):
    """Progress column that shows live CPU%, RAM%, and CPU temperature."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_temp_str = "  🌡️  [dim white]Temp:[/dim white] [dim]N/A[/dim]"
        self._last_temp_update = 0
        self._wmi = None
        self._wmi_thread_id = None

    def render(self, task) -> Text:
        if psutil is None:
            return Text(" [psutil missing]", style="dim red")

        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent

        cpu_color = "red" if cpu > 85 else "cyan"
        ram_color = "red" if ram > 85 else "cyan"

        current_time = time.time()
        if current_time - self._last_temp_update > 1.0:
            self._refresh_temperature()
            self._last_temp_update = current_time

        return Text.from_markup(
            f"  🖥️  [dim white]CPU:[/dim white] [{cpu_color}]{cpu:04.1f}%[/{cpu_color}]  "
            f"[dim white]RAM:[/dim white] [{ram_color}]{ram:04.1f}%[/{ram_color}]{self._last_temp_str}"
        )

    def _refresh_temperature(self) -> None:
        if sys.platform == "win32":
            self._refresh_temperature_windows()
        else:
            self._refresh_temperature_unix()

    def _refresh_temperature_windows(self) -> None:
        try:
            import pythoncom
            import wmi

            pythoncom.CoInitialize()
            current_thread_id = threading.get_ident()

            if self._wmi_thread_id != current_thread_id:
                self._wmi = wmi.WMI(namespace="root\\wmi")
                self._wmi_thread_id = current_thread_id

            if self._wmi:
                temperature_info = self._wmi.MSAcpi_ThermalZoneTemperature()
                if temperature_info:
                    cpu_temp = (temperature_info[0].CurrentTemperature / 10.0) - 273.15
                    temp_color = "red" if cpu_temp > 80 else "cyan"
                    self._last_temp_str = (
                        f"  🌡️  [dim white]Temp:[/dim white] "
                        f"[{temp_color}]{cpu_temp:.0f}°C[/{temp_color}]"
                    )
        except ImportError:
            self._last_temp_str = (
                "  🌡️  [dim white]Temp:[/dim white] [dim red]lib missing[/dim red]"
            )
        except Exception:
            self._last_temp_str = (
                "  🌡️  [dim white]Temp:[/dim white] [dim red]Err (COM)[/dim red]"
            )

    def _refresh_temperature_unix(self) -> None:
        if psutil is None or not hasattr(psutil, "sensors_temperatures"):
            return
        temps = psutil.sensors_temperatures()
        if not temps:
            return
        try:
            sensor_list = (
                temps.get("coretemp") or temps.get("k10temp")
                or temps.get("zenpower") or temps.get("acpitz")
                or list(temps.values())[0]
            )
            if sensor_list:
                cpu_temp = sensor_list[0].current
                temp_color = "red" if cpu_temp > 80 else "cyan"
                self._last_temp_str = (
                    f"  🌡️  [dim white]Temp:[/dim white] "
                    f"[{temp_color}]{cpu_temp:.0f}°C[/{temp_color}]"
                )
        except Exception:
            pass