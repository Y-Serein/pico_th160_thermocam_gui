"""Shared design tokens for the TN160 desktop workbench."""
from matplotlib.patches import FancyBboxPatch

# Graphite instrument palette. Thermal imagery keeps its own inferno spectrum;
# chrome uses one cool accent so controls never compete with measured data.
APP_BG      = '#0d1013'
SIDEBAR_BG  = '#12161a'
FIG_BG      = '#0f1317'
CARD_BG     = '#171c22'
CARD_EDGE   = '#29313b'
CARD_EDGE_S = '#3a4654'
TITLE_FG    = '#edf1f5'
SUBTITLE_FG = '#8a95a3'
BODY_FG     = '#cbd2da'
DIM_FG      = '#687483'
AXIS_FG     = '#909ba8'
ACCENT_FG   = '#65d1c5'
ACCENT_BG   = '#173330'
WARM_FG     = '#f0ad69'
OK_FG       = '#62d394'
OK_BG       = '#173527'
OK_EDGE     = '#2e7852'
WARN_FG     = '#f0c36b'
FAIL_FG     = '#ff8585'
FAIL_BG     = '#3b2023'
FAIL_EDGE   = '#8d4148'


APP_STYLESHEET = f"""
QMainWindow, QWidget#appShell {{
    background: {APP_BG};
    color: {TITLE_FG};
}}
QWidget {{
    color: {BODY_FG};
    font-size: 13px;
}}
QFrame#sidebar {{
    background: {SIDEBAR_BG};
    border-right: 1px solid {CARD_EDGE};
}}
QLabel#brandMark {{
    color: {ACCENT_FG};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#brandTitle {{
    color: {TITLE_FG};
    font-size: 22px;
    font-weight: 700;
}}
QLabel#brandSub, QLabel#sidebarMeta {{
    color: {DIM_FG};
    font-size: 11px;
}}
QListWidget#primaryNav {{
    background: transparent;
    border: 0;
    outline: 0;
    padding: 0;
}}
QListWidget#primaryNav::item {{
    color: {SUBTITLE_FG};
    min-height: 40px;
    padding: 0 12px;
    margin: 2px 0;
    border-left: 2px solid transparent;
}}
QListWidget#primaryNav::item:hover {{
    color: {TITLE_FG};
    background: #181e24;
}}
QListWidget#primaryNav::item:selected {{
    color: {ACCENT_FG};
    background: {ACCENT_BG};
    border-left: 2px solid {ACCENT_FG};
    font-weight: 600;
}}
QFrame#contentShell {{
    background: {APP_BG};
}}
QFrame#pageHeader {{
    background: #101419;
    border-bottom: 1px solid {CARD_EDGE};
}}
QLabel#pageTitle {{
    color: {TITLE_FG};
    font-size: 18px;
    font-weight: 650;
}}
QLabel#pageSubtitle {{
    color: {SUBTITLE_FG};
    font-size: 12px;
}}
QLabel#profileChip {{
    color: {ACCENT_FG};
    background: {ACCENT_BG};
    border: 1px solid #285650;
    border-radius: 4px;
    padding: 5px 9px;
    font-size: 11px;
}}
QFrame#commandBar, QFrame#telemetryStrip, QFrame#statusPanel {{
    background: {CARD_BG};
    border: 1px solid {CARD_EDGE};
    border-radius: 5px;
}}
QLabel {{
    background: transparent;
}}
QLabel#fieldLabel {{
    color: {SUBTITLE_FG};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#pathValue {{
    color: #aeb8c3;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 11px;
}}
QLabel#metricValue {{
    color: {OK_FG};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#inlineWarning {{
    color: {WARN_FG};
    background: #282218;
    border: 1px solid #544527;
    border-radius: 4px;
    padding: 7px 9px;
}}
QLabel#statusBadge {{
    color: {SUBTITLE_FG};
    background: #14191f;
    border: 1px solid {CARD_EDGE};
    border-radius: 4px;
    padding: 5px 9px;
    font-weight: 600;
}}
QLabel#statusBadge[tone="active"] {{
    color: {ACCENT_FG};
    background: {ACCENT_BG};
    border-color: #2c6c66;
}}
QLabel#statusBadge[tone="success"] {{
    color: {OK_FG};
    background: {OK_BG};
    border-color: {OK_EDGE};
}}
QLabel#statusBadge[tone="warning"] {{
    color: {WARN_FG};
    background: #332b19;
    border-color: #66562a;
}}
QLabel#statusBadge[tone="error"] {{
    color: {FAIL_FG};
    background: {FAIL_BG};
    border-color: {FAIL_EDGE};
}}
QLabel#telemetryText {{
    color: #aab4bf;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 11px;
}}
QPushButton {{
    min-height: 30px;
    padding: 0 12px;
    color: {BODY_FG};
    background: #20262d;
    border: 1px solid #343d48;
    border-radius: 4px;
    font-weight: 600;
}}
QPushButton:hover {{
    color: {TITLE_FG};
    background: #29313a;
    border-color: #46515e;
}}
QPushButton:pressed {{
    background: #171c21;
    border-color: {ACCENT_FG};
}}
QPushButton:focus {{
    border: 1px solid {ACCENT_FG};
}}
QPushButton:disabled {{
    color: #56606c;
    background: #171b20;
    border-color: #252b32;
}}
QPushButton[kind="primary"] {{
    color: #eafffc;
    background: #24534f;
    border-color: #3a837c;
}}
QPushButton[kind="primary"]:hover {{
    background: #2d6660;
    border-color: {ACCENT_FG};
}}
QPushButton[kind="step"] {{
    color: {TITLE_FG};
    background: #26313b;
    border-color: #4b5b69;
}}
QPushButton[kind="step"]:hover {{
    background: #303d48;
    border-color: #6b7d8d;
}}
QPushButton[kind="warning"] {{
    color: #fff1dc;
    background: #4a3421;
    border-color: #805b35;
}}
QPushButton[kind="danger"] {{
    color: #ffdfe1;
    background: #412326;
    border-color: #754047;
}}
QPushButton[kind="quiet"] {{
    color: {SUBTITLE_FG};
    background: transparent;
    border-color: transparent;
}}
QLineEdit, QComboBox {{
    min-height: 30px;
    padding: 0 9px;
    color: {TITLE_FG};
    background: #0f1418;
    border: 1px solid #313a44;
    border-radius: 4px;
    selection-background-color: #28645e;
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {ACCENT_FG};
}}
QComboBox::drop-down {{
    width: 26px;
    border: 0;
}}
QComboBox QAbstractItemView {{
    color: {BODY_FG};
    background: #171c22;
    border: 1px solid #3a4652;
    selection-background-color: {ACCENT_BG};
    selection-color: {ACCENT_FG};
}}
QPlainTextEdit {{
    color: #b9c3cd;
    background: #0c1013;
    border: 1px solid {CARD_EDGE};
    border-radius: 4px;
    padding: 8px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 11px;
    selection-background-color: #285b57;
}}
QCheckBox {{
    color: {SUBTITLE_FG};
    spacing: 7px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid #46515e;
    border-radius: 3px;
    background: #101419;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_FG};
    border-color: {ACCENT_FG};
}}
QStatusBar {{
    color: {DIM_FG};
    background: #0b0e11;
    border-top: 1px solid #222930;
    font-size: 11px;
}}
QToolTip {{
    color: {TITLE_FG};
    background: #20262d;
    border: 1px solid #46515e;
    padding: 5px;
}}
"""


def set_button_kind(button, kind):
    button.setProperty('kind', kind)


def set_status_tone(label, tone):
    label.setProperty('tone', tone)
    label.style().unpolish(label)
    label.style().polish(label)


def style_figure(fig):
    fig.patch.set_facecolor(FIG_BG)


def style_card(ax, title, subtitle=None):
    """Dark panel with border, left-aligned bold title and dim subtitle."""
    ax.set_facecolor(CARD_BG)
    for sp in ax.spines.values():
        sp.set_edgecolor(CARD_EDGE)
        sp.set_linewidth(1.0)
    ax.tick_params(colors=AXIS_FG, labelsize=7, length=3, pad=2,
                   color=CARD_EDGE)
    ax.set_title(title, color=TITLE_FG, fontsize=10, pad=10,
                 loc='left', fontweight='bold')
    if subtitle:
        ax.text(1.0, 1.015, subtitle, transform=ax.transAxes,
                color=SUBTITLE_FG, fontsize=8, va='bottom', ha='right',
                fontstyle='italic')


def style_summary_card(ax, title="Summary"):
    """Summary panel: thicker border, no ticks, bold title."""
    ax.set_facecolor(CARD_BG)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor(CARD_EDGE_S)
        sp.set_linewidth(1.2)
    ax.set_title(title, color=TITLE_FG, fontsize=10, pad=10,
                 loc='left', fontweight='bold')


def empty_placeholder(ax, msg='no data yet'):
    """Centered dim text, to indicate an unfilled card."""
    ax.set_xticks([]); ax.set_yticks([])
    ax.text(0.5, 0.5, msg,
            ha='center', va='center', transform=ax.transAxes,
            color=DIM_FG, fontsize=10, fontstyle='italic')


def status_badge(ax, ok, label, x=0.03, y=0.88):
    """Draw a colored PASS/FAIL pill with a label next to it (axes coords)."""
    tag = ' PASS ' if ok else ' FAIL '
    fg  = OK_FG   if ok else FAIL_FG
    bg  = OK_BG   if ok else FAIL_BG
    ec  = OK_EDGE if ok else FAIL_EDGE
    ax.text(x, y, tag, transform=ax.transAxes,
            va='center', ha='left',
            color=fg, fontsize=10, fontweight='bold', family='monospace',
            bbox=dict(boxstyle='round,pad=0.35', fc=bg, ec=ec, lw=1.0))
    ax.text(x + 0.18, y, label, transform=ax.transAxes,
            va='center', ha='left',
            color=BODY_FG, fontsize=9)


def kv_block(ax, rows, x=0.03, y_top=0.72, line_h=0.08,
             key_color=None, val_color=None):
    """Render a mono-aligned key/value block inside a card.

    rows = [('img_l mean', '7744.5'), ...]
    """
    key_color = key_color or SUBTITLE_FG
    val_color = val_color or BODY_FG
    for i, (k, v) in enumerate(rows):
        y = y_top - i * line_h
        ax.text(x,       y, k, transform=ax.transAxes,
                va='top', ha='left', color=key_color,
                fontsize=9)
        ax.text(x + 0.55, y, v, transform=ax.transAxes,
                va='top', ha='left', color=val_color,
                fontsize=9, family='DejaVu Sans Mono', fontweight='bold')
