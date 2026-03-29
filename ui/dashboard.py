from __future__ import annotations

from dataclasses import dataclass

import dearpygui.dearpygui as dpg

from sensor_core import SensorApp
from ui.styles import UiFonts


@dataclass
class DashboardIds:
    status_text: int
    pressure_text: int
    temp_text: int
    pressure_series: int
    x_axis: int


class SensorDashboard:
    def __init__(self, app: SensorApp, fonts: UiFonts):
        self.app = app
        self.fonts = fonts
        self.ids = DashboardIds(
            status_text=dpg.generate_uuid(),
            pressure_text=dpg.generate_uuid(),
            temp_text=dpg.generate_uuid(),
            pressure_series=dpg.generate_uuid(),
            x_axis=dpg.generate_uuid(),
        )

    def build(self) -> None:
        with dpg.window(tag="main_window"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=340):
                    self._build_control_card()
                    dpg.add_spacer(height=12)
                    self._build_live_card()

                with dpg.child_window():
                    self._build_plot_card()

    def _build_control_card(self) -> None:
        dpg.add_text("System Control", font=self.fonts.heading)
        dpg.add_text("EN 14055 Cistern Analytics")
        dpg.add_separator()
        dpg.add_text("Status: DISCONNECTED", tag=self.ids.status_text)
        dpg.add_button(label="Connect / Disconnect", callback=self._toggle_connection, width=-1, height=42)

    def _build_live_card(self) -> None:
        dpg.add_text("Live Telemetry", font=self.fonts.heading)
        dpg.add_separator()
        dpg.add_text("Pressure: 0.000 bar", tag=self.ids.pressure_text)
        dpg.add_text("Temp: -- °C", tag=self.ids.temp_text)

    def _build_plot_card(self) -> None:
        dpg.add_text("Pressure History", font=self.fonts.heading)
        dpg.add_separator()
        with dpg.plot(label="", height=-1, width=-1):
            dpg.add_plot_legend()
            dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=self.ids.x_axis)
            y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Pressure (bar)")
            dpg.add_line_series([], [], label="Pressure", parent=y_axis, tag=self.ids.pressure_series)

    def _toggle_connection(self) -> None:
        if self.app.is_connected:
            self.app.disconnect()
        else:
            self.app.connect()
