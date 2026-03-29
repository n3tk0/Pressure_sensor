import dearpygui.dearpygui as dpg

def setup_fonts():
    """Зарежда шрифтовете на Samsung от папката fonts/"""
    with dpg.font_registry():
        # Размер 18 за основен текст прави интерфейса много по-четим
        default_font = dpg.add_font("fonts/SamsungSans-Regular.ttf", 18)
        bold_font = dpg.add_font("fonts/Samsung Sans Bold.ttf", 20)
        
    dpg.bind_font(default_font)
    return default_font, bold_font

def create_modern_theme():
    """Създава модерна визия без рамки и със заоблени ъгли"""
    with dpg.theme() as modern_theme:
        with dpg.theme_component(dpg.mvAll):
            # 1. ЗАОБЛЯНЕ И ПРЕМАХВАНЕ НА РАМКИ
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 8)

            # 2. ЦВЕТОВА ПАЛИТРА (Modern Dark / Blue Accent)
            # Фон на прозорците (тъмно синьо-сиво)
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (30, 30, 46))
            # Фон на вътрешните панели
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (24, 24, 37))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (30, 30, 46))
            
            # Полета за въвеждане и рамки
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (49, 50, 68))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (69, 71, 90))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (88, 91, 112))
            
            # Бутони (Мек син акцент)
            dpg.add_theme_color(dpg.mvThemeCol_Button, (137, 180, 250)) 
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (180, 190, 254))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (116, 199, 236))
            
            # Текст (Мръсно бяло за по-малко напрежение в очите)
            dpg.add_theme_color(dpg.mvThemeCol_Text, (205, 214, 244))
            # Текст върху бутоните (Тъмен за контраст)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (166, 173, 200))

            # 3. ВИЗИЯ НА ГРАФИКАТА
            dpg.add_theme_color(dpg.mvThemeCol_PlotBg, (24, 24, 37))
            dpg.add_theme_color(dpg.mvThemeCol_PlotBorder, (49, 50, 68))

    return modern_theme
