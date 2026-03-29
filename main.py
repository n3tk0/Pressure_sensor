import time
import dearpygui.dearpygui as dpg

from sensor_core import SensorApp
from dpg_theme import setup_fonts, create_modern_theme

def main():
    dpg.create_context()
    
    # ОПТИМИЗАЦИЯ 3: VSync=True спира "изгарянето" на процесора
    dpg.create_viewport(title="EN 14055 Cistern Analytics", width=1280, height=800, vsync=True)
    dpg.setup_dearpygui()

    app = SensorApp()
    app.connect() 

    default_font, bold_font = setup_fonts()
    modern_theme = create_modern_theme()
    dpg.bind_theme(modern_theme)

    # ОПТИМИЗАЦИЯ 4: Генерираме предварително ID-та за бърз достъп (по-бързо от стрингове)
    lbl_status_id = dpg.generate_uuid()
    lbl_pressure_id = dpg.generate_uuid()
    lbl_temp_id = dpg.generate_uuid()
    series_p_id = dpg.generate_uuid()
    x_axis_id = dpg.generate_uuid()

    with dpg.window(tag="main_window"):
        with dpg.group(horizontal=True):
            with dpg.child_window(width=300):
                dpg.add_text("System Controls", font=bold_font)
                dpg.add_separator()
                dpg.add_text("Status: Disconnected", tag=lbl_status_id)
                
                def toggle_connection():
                    if app.is_connected:
                        app.disconnect()
                    else:
                        app.connect()

                dpg.add_button(label="Connect / Disconnect", callback=toggle_connection, width=-1)
                
                dpg.add_spacer(height=20)
                dpg.add_text("Live Data", font=bold_font)
                dpg.add_text("Pressure: 0.000 bar", tag=lbl_pressure_id)
                dpg.add_text("Temp: 0.0 °C", tag=lbl_temp_id)

            with dpg.child_window():
                with dpg.plot(label="Sensor Live Data", height=-1, width=-1):
                    dpg.add_plot_legend()
                    dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=x_axis_id)
                    dpg.add_plot_axis(dpg.mvYAxis, label="Pressure (bar)")
                    
                    # Прикрепяме серията към генерираното ID
                    dpg.add_line_series([], [], label="Pressure", parent=dpg.last_item(), tag=series_p_id)

    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()

    # --------------------------------------------------------
    # ОПТИМИЗИРАН ОСНОВЕН ЦИКЪЛ (RENDER LOOP)
    # --------------------------------------------------------
    
    # Променливи за следене на състоянието (ОПТИМИЗАЦИЯ 2)
    last_plot_update = 0.0
    plot_update_interval = 1.0 / 60.0  # <-- ПРОМЕНЕНО НА 60Hz (60 кадъра в секунда)
    
    cached_pressure = None
    cached_temp = None
    cached_status = None

    while dpg.is_dearpygui_running():
        now = time.time()
        
        # 1. Проверка на статуса
        current_status = app.is_connected
        if current_status != cached_status:
            status_text = "Status: CONNECTED" if current_status else "Status: DISCONNECTED"
            dpg.set_value(lbl_status_id, status_text)
            cached_status = current_status

        if current_status:
            # 2. Обновяване на текста САМО при промяна
            if app.live_pressure != cached_pressure:
                dpg.set_value(lbl_pressure_id, f"Pressure: {app.live_pressure:.3f} bar")
                cached_pressure = app.live_pressure
                
            if app.live_temp != cached_temp:
                dpg.set_value(lbl_temp_id, f"Temp: {app.live_temp:.1f} °C")
                cached_temp = app.live_temp

            # 3. Лимитирано обновяване на графиката (Throttling) на 60Hz
            if (now - last_plot_update) >= plot_update_interval:
                # Използваме Lock, за да не четем списъка, докато другата нишка пише в него
                with app.lock:
                    # Превръщането в list() е тежка операция, лимитирана до 60 пъти в секунда
                    x_data = list(app.time_data)
                    y_data = list(app.p_data)
                
                if x_data:
                    dpg.set_value(series_p_id, [x_data, y_data])
                    # Auto-fit по оста X, за да се движи графиката плавно
                    dpg.fit_axis_data(x_axis_id)
                
                last_plot_update = now

        # Рендиране на кадъра (VSync ще го лимитира до честотата на монитора)
        dpg.render_dearpygui_frame()

    # Сигурно затваряне на порта
    app.cleanup()
    dpg.destroy_context()

if __name__ == "__main__":
    main()
