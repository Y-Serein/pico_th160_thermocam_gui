"""Shared matplotlib styling for the calibration tabs — card look & feel."""
from matplotlib.patches import FancyBboxPatch

# Dark product palette
FIG_BG      = '#12131a'
CARD_BG     = '#1b1d26'
CARD_EDGE   = '#2e3246'
CARD_EDGE_S = '#3e435e'     # stronger (for summary panel)
TITLE_FG    = '#eaecff'
SUBTITLE_FG = '#7a8099'
BODY_FG     = '#c6cbe2'
DIM_FG      = '#5a6079'
AXIS_FG     = '#8c93af'
OK_FG       = '#50c878'
OK_BG       = '#1f4a2c'
OK_EDGE     = '#3aa864'
FAIL_FG     = '#ff7a7a'
FAIL_BG     = '#4a1f1f'
FAIL_EDGE   = '#b84a4a'


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
            color=BODY_FG, fontsize=9, family='DejaVu Sans')


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
                fontsize=9, family='monospace')
        ax.text(x + 0.55, y, v, transform=ax.transAxes,
                va='top', ha='left', color=val_color,
                fontsize=9, family='monospace', fontweight='bold')
