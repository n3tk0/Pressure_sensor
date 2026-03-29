from __future__ import annotations

import time

import dearpygui.dearpygui as dpg

from sensor_core import SensorApp
from ui.dashboard import SensorDashboard
from ui.styles import create_tailwind_theme, setup_fonts


class UiRuntime:
    def __init__(self):
        self.app = SensorApp()
        self.dashboard: SensorDashboard | None = None

    def run(self) -> None:
        dpg.create_context()
        dpg.create_viewport(title="EN 14055 Cistern Analytics", width=1360, height=860, vsync=True)
        dpg.setup_dearpygui()

        self.app.connect()

        fonts = setup_fonts()
        dpg.bind_theme(create_tailwind_theme())

        self.dashboard = SensorDashboard(app=self.app, fonts=fonts)
        self.dashboard.build()

        dpg.set_primary_window("main_window", True)
        dpg.show_viewport()

        self._render_loop(self.dashboard)

        self.app.cleanup()
        dpg.destroy_context()

    def _render_loop(self, dashboard: SensorDashboard) -> None:
        last_plot_update = 0.0
        plot_update_interval = 1.0 / 60.0

        cached_pressure = None
        cached_temp = None
        cached_status = None

        while dpg.is_dearpygui_running():
            now = time.time()
            current_status = self.app.is_connected

            if current_status != cached_status:
                dpg.set_value(
                    dashboard.ids.status_text,
                    "Status: CONNECTED" if current_status else "Status: DISCONNECTED",
                )
                cached_status = current_status

            if current_status:
                if self.app.live_pressure != cached_pressure:
                    dpg.set_value(dashboard.ids.pressure_text, f"Pressure: {self.app.live_pressure:.3f} bar")
                    cached_pressure = self.app.live_pressure

                if self.app.live_temp != cached_temp:
                    dpg.set_value(dashboard.ids.temp_text, f"Temp: {self.app.live_temp:.1f} °C")
                    cached_temp = self.app.live_temp

                if (now - last_plot_update) >= plot_update_interval:
                    with self.app.lock:
                        x_data = list(self.app.time_data)
                        y_data = list(self.app.p_data)

                    if x_data:
                        dpg.set_value(dashboard.ids.pressure_series, [x_data, y_data])
                        dpg.fit_axis_data(dashboard.ids.x_axis)
                    last_plot_update = now

            dpg.render_dearpygui_frame()
