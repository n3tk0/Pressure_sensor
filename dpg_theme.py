"""
dpg_theme.py — Fonts, themes, and asset helpers for the DearPyGui interface.

Provides:
  get_resource_path()   – resolve assets inside a PyInstaller bundle
  setup_fonts()         – load Samsung Sans fonts (regular 18, bold 20)
  create_modern_theme() – borderless, rounded-corner Catppuccin Macchiato theme
  create_status_themes()– colored text themes for status labels (dark + light)
  create_button_themes()– action / danger / success button themes
  create_line_themes()  – chart line + scatter marker themes
"""

import sys
import os
import dearpygui.dearpygui as dpg


# ── Asset resolution ────────────────────────────────────────────────
def get_resource_path(relative_path: str) -> str:
    """Return absolute path to a bundled resource.

    When running from a PyInstaller/auto-py-to-exe one-file bundle,
    assets are extracted to a temporary folder referenced by sys._MEIPASS.
    In normal .py execution, resolve relative to this file's directory.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


# ── Fonts ───────────────────────────────────────────────────────────
def setup_fonts():
    """Load Samsung Sans fonts from the fonts/ folder.

    Returns (default_font, bold_font) — either may be None if the file
    is not found (DearPyGui will fall back to its built-in ProggyClean).
    """
    regular_path = get_resource_path(os.path.join("fonts", "SamsungSans-Regular.ttf"))
    bold_path = get_resource_path(os.path.join("fonts", "Samsung Sans Bold.ttf"))

    default_font = None
    bold_font = None
    font_ui = None
    font_medium = None
    font_large = None

    def _add_glyph_ranges(font_id):
        """Add extended Unicode glyph ranges so symbols like —, ●, △, ✓ render."""
        dpg.add_font_range_hint(dpg.mvFontRangeHint_Default, parent=font_id)
        # Latin Extended-A/B
        dpg.add_font_range(0x0100, 0x024F, parent=font_id)
        # General Punctuation (em-dash U+2014, bullets U+2022, etc.)
        dpg.add_font_range(0x2000, 0x206F, parent=font_id)
        # Superscripts and Subscripts
        dpg.add_font_range(0x2070, 0x209F, parent=font_id)
        # Currency Symbols
        dpg.add_font_range(0x20A0, 0x20CF, parent=font_id)
        # Letterlike Symbols
        dpg.add_font_range(0x2100, 0x214F, parent=font_id)
        # Arrows
        dpg.add_font_range(0x2190, 0x21FF, parent=font_id)
        # Mathematical Operators (≥ ≤ ± etc.)
        dpg.add_font_range(0x2200, 0x22FF, parent=font_id)
        # Miscellaneous Technical
        dpg.add_font_range(0x2300, 0x23FF, parent=font_id)
        # Box Drawing
        dpg.add_font_range(0x2500, 0x257F, parent=font_id)
        # Block Elements
        dpg.add_font_range(0x2580, 0x259F, parent=font_id)
        # Geometric Shapes (▶ ◀ ● ◯ etc.)
        dpg.add_font_range(0x25A0, 0x25FF, parent=font_id)
        # Miscellaneous Symbols (⚠ etc.)
        dpg.add_font_range(0x2600, 0x26FF, parent=font_id)
        # Dingbats (✓ ✗ etc.)
        dpg.add_font_range(0x2700, 0x27BF, parent=font_id)
        # Greek letters (°C uses ° which is in Latin-1 Supplement)
        dpg.add_font_range(0x0080, 0x00FF, parent=font_id)
        # Miscellaneous Symbols and Arrows
        dpg.add_font_range(0x2B00, 0x2BFF, parent=font_id)
        # Delta Δ etc. from Greek
        dpg.add_font_range(0x0370, 0x03FF, parent=font_id)

    with dpg.font_registry():
        if os.path.isfile(regular_path):
            default_font = dpg.add_font(regular_path, 18)
            _add_glyph_ranges(default_font)
            font_ui = dpg.add_font(regular_path, 16)
            _add_glyph_ranges(font_ui)
            font_medium = dpg.add_font(regular_path, 20)
            _add_glyph_ranges(font_medium)
        if os.path.isfile(bold_path):
            bold_font = dpg.add_font(bold_path, 20)
            _add_glyph_ranges(bold_font)
            font_large = dpg.add_font(bold_path, 30)
            _add_glyph_ranges(font_large)
        elif os.path.isfile(regular_path):
            font_large = dpg.add_font(regular_path, 30)
            _add_glyph_ranges(font_large)

    if font_ui:
        dpg.bind_font(font_ui)

    return default_font, bold_font, font_ui, font_medium, font_large


# ── Catppuccin Macchiato / Mocha palette ────────────────────────────
# Dark mode
COL_BG       = (30, 30, 46)
COL_CARD     = (42, 42, 61, 208)
COL_ACCENT   = (137, 180, 250)
COL_GREEN    = (166, 227, 161)
COL_RED      = (243, 139, 168)
COL_GRAY     = (128, 128, 128)
COL_ORANGE   = (250, 179, 90)
COL_BLUE     = (30, 102, 245)
COL_WHITE    = (205, 214, 244)

# Light mode equivalents
LT_ACCENT = (28,  95, 210)
LT_GREEN  = (25, 130,  55)
LT_RED    = (190,  38,  68)
LT_ORANGE = (175,  88,   0)
LT_GRAY   = ( 90,  90, 115)


# ── Modern theme (borderless, rounding=6, Catppuccin dark) ──────────
def create_modern_theme():
    """Create a borderless, rounded-corner theme using a Catppuccin Macchiato palette.

    Returns the theme tag string "theme_dark".
    Also creates "theme_light" as the companion light theme.
    """
    # ── Dark theme ──────────────────────────────────────────────────
    with dpg.theme(tag="theme_dark"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 8)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)

            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,       (30, 30, 46))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,        (42, 42, 61))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,        (40, 40, 58))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,        (55, 55, 77))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (65, 65, 90))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,  (88, 91, 112))
            dpg.add_theme_color(dpg.mvThemeCol_Button,         (55, 55, 85))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (75, 75, 110))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (90, 90, 130))
            dpg.add_theme_color(dpg.mvThemeCol_Text,           (205, 214, 244))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled,   (166, 173, 200))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,        (35, 35, 55))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,  (45, 45, 70))
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg,      (35, 35, 55))
            dpg.add_theme_color(dpg.mvThemeCol_Header,         (55, 55, 85))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,  (70, 70, 100))
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg,  (45, 45, 68))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,    (35, 35, 50))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,  (65, 65, 90))
        with dpg.theme_component(dpg.mvPlot):
            dpg.add_theme_color(dpg.mvPlotCol_PlotBg,     (25, 25, 40))
            dpg.add_theme_color(dpg.mvPlotCol_PlotBorder,  (60, 60, 80))
            dpg.add_theme_color(dpg.mvPlotCol_AxisText,    (180, 190, 210))
            dpg.add_theme_color(dpg.mvPlotCol_AxisGrid,    (60, 60, 80))

    # ── Light theme ─────────────────────────────────────────────────
    with dpg.theme(tag="theme_light"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 8)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)

            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,       (239, 241, 245))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,        (220, 224, 232))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,        (230, 233, 240))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,        (204, 208, 218))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (188, 192, 206))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,  (170, 175, 192))
            dpg.add_theme_color(dpg.mvThemeCol_Button,         (180, 185, 205))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (162, 168, 192))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (144, 151, 178))
            dpg.add_theme_color(dpg.mvThemeCol_Text,           (76, 79, 105))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled,   (140, 143, 160))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,        (210, 215, 228))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,  (188, 195, 215))
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg,      (210, 215, 228))
            dpg.add_theme_color(dpg.mvThemeCol_Header,         (188, 195, 215))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,  (172, 180, 204))
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg,  (172, 178, 200))
            dpg.add_theme_color(dpg.mvThemeCol_Separator,      (180, 185, 200))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,    (215, 218, 228))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,  (172, 176, 196))
        with dpg.theme_component(dpg.mvPlot):
            dpg.add_theme_color(dpg.mvPlotCol_PlotBg,     (248, 249, 252))
            dpg.add_theme_color(dpg.mvPlotCol_PlotBorder,  (180, 185, 200))
            dpg.add_theme_color(dpg.mvPlotCol_AxisText,    (76, 79, 105))
            dpg.add_theme_color(dpg.mvPlotCol_AxisGrid,    (200, 204, 215))

    return "theme_dark"


# ── Status text-color themes ────────────────────────────────────────
def create_status_themes():
    """Create colored text themes for status labels (dark + light variants)."""
    # Dark variants
    for tag, col in [
        ("theme_green",  COL_GREEN),
        ("theme_red",    COL_RED),
        ("theme_gray",   COL_GRAY),
        ("theme_blue",   COL_ACCENT),
        ("theme_orange", COL_ORANGE),
        ("theme_accent_text", COL_ACCENT),
        ("theme_green_text", COL_GREEN),
    ]:
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Text, col)

    # Light variants
    for tag, col in [
        ("theme_green_lt",  LT_GREEN),
        ("theme_red_lt",    LT_RED),
        ("theme_gray_lt",   LT_GRAY),
        ("theme_blue_lt",   LT_ACCENT),
        ("theme_orange_lt", LT_ORANGE),
    ]:
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Text, col)


# ── Button themes ───────────────────────────────────────────────────
def create_button_themes():
    """Create action / danger / success button themes."""
    with dpg.theme(tag="theme_btn_action"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Text,          (240, 240, 240))
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (40,  90,  90))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 115, 115))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (70, 140, 140))

    with dpg.theme(tag="theme_btn_danger"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Text,          (240, 240, 240))
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (100, 40,  50))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (130, 55,  65))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (160, 70,  80))

    with dpg.theme(tag="theme_btn_success"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Text,          (240, 240, 240))
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (35,  90,  55))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 115,  70))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (65, 140,  85))


# ── Chart line + scatter themes ─────────────────────────────────────
def create_line_themes(line_colors: dict, default_colors: dict) -> dict:
    """Create chart line themes from current color settings.

    Args:
        line_colors: current app_settings["line_colors"]
        default_colors: DEFAULT_LINE_COLORS fallback

    Returns:
        dict mapping color key -> dpg theme color item ID (for live recoloring).
    """
    def _get(key):
        return line_colors.get(key, default_colors[key])

    ids = {}

    with dpg.theme(tag="theme_line_main"):
        with dpg.theme_component(dpg.mvLineSeries):
            ids["sensor"] = dpg.add_theme_color(dpg.mvPlotCol_Line, _get("sensor"))
            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0)

    with dpg.theme(tag="theme_line_mwl"):
        with dpg.theme_component(dpg.mvLineSeries):
            ids["mwl"] = dpg.add_theme_color(dpg.mvPlotCol_Line, _get("mwl"))
            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)

    with dpg.theme(tag="theme_line_menis"):
        with dpg.theme_component(dpg.mvLineSeries):
            ids["menis"] = dpg.add_theme_color(dpg.mvPlotCol_Line, _get("menis"))
            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)

    with dpg.theme(tag="theme_line_wd"):
        with dpg.theme_component(dpg.mvLineSeries):
            ids["wd"] = dpg.add_theme_color(dpg.mvPlotCol_Line, _get("wd"))
            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)

    with dpg.theme(tag="theme_line_cwl"):
        with dpg.theme_component(dpg.mvLineSeries):
            ids["cwl"] = dpg.add_theme_color(dpg.mvPlotCol_Line, _get("cwl"))
            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5)

    with dpg.theme(tag="theme_click1"):
        with dpg.theme_component(dpg.mvScatterSeries):
            dpg.add_theme_color(dpg.mvPlotCol_MarkerFill, (243, 139, 168))
            dpg.add_theme_color(dpg.mvPlotCol_MarkerOutline, (220, 80, 120))
            dpg.add_theme_style(dpg.mvPlotStyleVar_Marker, dpg.mvPlotMarker_Circle)
            dpg.add_theme_style(dpg.mvPlotStyleVar_MarkerSize, 8)

    with dpg.theme(tag="theme_click2"):
        with dpg.theme_component(dpg.mvScatterSeries):
            dpg.add_theme_color(dpg.mvPlotCol_MarkerFill, (166, 227, 161))
            dpg.add_theme_color(dpg.mvPlotCol_MarkerOutline, (100, 190, 100))
            dpg.add_theme_style(dpg.mvPlotStyleVar_Marker, dpg.mvPlotMarker_Circle)
            dpg.add_theme_style(dpg.mvPlotStyleVar_MarkerSize, 8)

    return ids
