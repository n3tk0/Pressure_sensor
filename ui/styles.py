from __future__ import annotations

from pathlib import Path

import dearpygui.dearpygui as dpg

ROOT_DIR = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT_DIR / "fonts"


class UiFonts:
    def __init__(self, default_font: int, heading_font: int):
        self.default = default_font
        self.heading = heading_font


def _font_path(filename: str) -> str:
    path = FONTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing font file: {path}")
    return str(path)


def setup_fonts() -> UiFonts:
    with dpg.font_registry():
        default_font = dpg.add_font(_font_path("SamsungSans-Regular.ttf"), 18)
        heading_font = dpg.add_font(_font_path("Samsung Sans Bold.ttf"), 22)
    dpg.bind_font(default_font)
    return UiFonts(default_font=default_font, heading_font=heading_font)


def create_tailwind_theme() -> int:
    """Tailwind-like dark slate + blue accent theme for DearPyGui."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            # Layout rhythm and rounded cards
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 18, 18)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 12, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 12, 10)
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 12)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 12)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 12)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)

            # Tailwind slate palette
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (15, 23, 42), category=dpg.mvThemeCat_Core)      # slate-900
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (30, 41, 59), category=dpg.mvThemeCat_Core)        # slate-800
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (30, 41, 59), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Border, (51, 65, 85), category=dpg.mvThemeCat_Core)         # slate-700

            dpg.add_theme_color(dpg.mvThemeCol_Text, (226, 232, 240), category=dpg.mvThemeCat_Core)        # slate-200
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (148, 163, 184), category=dpg.mvThemeCat_Core)  # slate-400

            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (51, 65, 85), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (71, 85, 105), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (100, 116, 139), category=dpg.mvThemeCat_Core)

            # Tailwind blue buttons
            dpg.add_theme_color(dpg.mvThemeCol_Button, (37, 99, 235), category=dpg.mvThemeCat_Core)        # blue-600
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (59, 130, 246), category=dpg.mvThemeCat_Core)  # blue-500
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (29, 78, 216), category=dpg.mvThemeCat_Core)  # blue-700

            dpg.add_theme_color(dpg.mvThemeCol_Header, (30, 64, 175), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (37, 99, 235), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (29, 78, 216), category=dpg.mvThemeCat_Core)

            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (96, 165, 250), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (96, 165, 250), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (147, 197, 253), category=dpg.mvThemeCat_Core)

            # Plot and accent colors
            dpg.add_theme_color(dpg.mvThemeCol_PlotBg, (15, 23, 42), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvThemeCol_PlotBorder, (51, 65, 85), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_Line, (56, 189, 248), category=dpg.mvThemeCat_Plots)         # sky-400
    return theme
