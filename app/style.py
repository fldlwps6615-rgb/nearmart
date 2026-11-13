"""Single source of chart styling and alternative colors."""
import plotly.graph_objects as go

ACCENT = "#6C5CE7"
MUTED = "#7C8CA3"
GOOD = "#0F9D8A"
BAD = "#DC2626"
INK = "#14183D"
GRID = "rgba(148,163,184,0.20)"
ALT_COLORS = {"A": "#358CCB", "B": "#149A88", "C": "#D79A2D", "B2": "#E16C9E", "D": "#6C5CE7", "C15": "#D79A2D", "D15": "#6C5CE7"}

PLOTLY_TEMPLATE = go.layout.Template(layout=go.Layout(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Pretendard, Malgun Gothic, Arial, sans-serif", size=13, color=INK),
    title=dict(x=0, xanchor="left", font=dict(size=17, color=INK)),
    margin=dict(l=65, r=35, t=110, b=60),
    legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0,
                bgcolor="rgba(0,0,0,0)", font=dict(size=12)),
    xaxis=dict(showgrid=False, zeroline=False, automargin=True),
    yaxis=dict(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor=GRID,
               tickformat=",.0f", automargin=True),
    hoverlabel=dict(bgcolor="white", font=dict(color=INK, size=13)),
    colorway=list(ALT_COLORS.values()),
))
PLOTLY_CONFIG = {"displaylogo": False, "scrollZoom": False, "responsive": True}
GLASS_CSS = """
<style>
.glass, [class*="st-key-glass_"] {
 background:rgba(255,255,255,0.88); border:1px solid rgba(255,255,255,0.78);
 border-radius:14px; padding:18px 20px 10px; margin-bottom:18px;
 box-shadow:0 8px 28px rgba(71,61,120,0.055);
}
</style>
"""

QUICK_CSS = f"""
<style>
[class*="st-key-quick_"] button {{ border-radius:999px; min-height:38px; padding:6px 8px;
 background:rgba(255,255,255,.76); border:1px solid rgba(124,92,231,.27); }}
[class*="st-key-quick_"] button p {{ font-size:11px; font-weight:650; white-space:nowrap; }}
.nm-change-table {{ width:100%; border-collapse:collapse; font-size:13px; margin:3px 0 12px; }}
.nm-change-table th, .nm-change-table td {{ text-align:right; padding:10px 9px; border-bottom:1px solid {GRID}; }}
.nm-change-table th:first-child {{ text-align:left; font-weight:600; }}
.nm-change-table thead th {{ font-size:11px; color:{MUTED}; font-weight:500; }}
.nm-verdict {{ font-size:13px; margin-top:16px; color:{INK}; }}
.nm-verdict-pill {{ display:inline-block; border-radius:999px; padding:6px 12px; margin-left:8px; font-weight:700; }}
.nm-rule-reason {{ font-size:12px; color:{BAD}; line-height:1.7; margin-top:9px; }}
.st-key-glass_history button {{ text-align:left; justify-content:flex-start; background:rgba(255,255,255,.70);
 border-color:rgba(124,92,231,.16); border-radius:9px; min-height:58px; padding:7px 10px; }}
.st-key-glass_history button p {{ font-size:11px; text-align:left; line-height:1.6; }}
.st-key-glass_changes, .st-key-glass_history {{ padding:14px 17px 13px; }}
</style>
"""

FONT_FAMILY = "Pretendard, 'Malgun Gothic', Arial, sans-serif"
FONT_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.min.css');
html, body, .stApp, .stApp button, .stApp input, .stApp select, .stApp textarea,
[data-testid="stMarkdownContainer"], [data-testid="stMetric"], .nm-change-table {
 font-family:Pretendard,'Malgun Gothic',Arial,sans-serif!important;
 font-variant-numeric:tabular-nums;
}
.stApp { color:#14183D; }
[data-testid="stMainBlockContainer"], [data-testid="stColumn"] { min-width:0; }
[data-testid="stMetricValue"] { font-size:32px!important; font-weight:750; color:#14183D!important; }
[data-testid="stMetricValue"] * { font-size:inherit!important; font-weight:inherit; font-variant-numeric:tabular-nums; }
[class*="st-key-kpi_"] { border-radius:20px!important; border:1px solid rgba(255,255,255,.92)!important;
 background:rgba(255,255,255,.88)!important; min-height:180px; }
.glass, [class*="st-key-glass_"] { border-radius:20px; }
h1, h2, h3, h4 { font-family:Pretendard,'Malgun Gothic',Arial,sans-serif!important; color:#14183D; }
h1 { font-size:32px!important; line-height:1.25!important; overflow-wrap:anywhere; }
.st-key-page_header [data-testid="stColumn"] { min-width:0; }
.nm-change-table { table-layout:fixed; }
.nm-change-table th, .nm-change-table td { overflow-wrap:anywhere; }
.st-key-glass_history button p { overflow-wrap:anywhere; }
@media (max-width:1450px) {
 .st-key-metric_grid [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
 .st-key-metric_grid [data-testid="stColumn"] { flex:1 1 calc(50% - 1rem)!important; min-width:0!important; width:calc(50% - 1rem)!important; }
 [data-testid="stMainBlockContainer"] { padding-left:2rem; padding-right:2rem; }
 .nm-change-table { font-size:12px; }
 .nm-change-table th, .nm-change-table td { padding:9px 4px; }
}
</style>
"""


def mode_css(presenting):
    scale = 1.15 if presenting else 1.0
    maximum = 1820 if presenting else 1480
    return f"""<style>
    html {{font-size:{16 * scale}px!important;}}
    [data-testid="stMainBlockContainer"] {{ max-width:{maximum}px; }}
    [data-testid="stMain"] h2 {{font-size:{28 * scale}px;}}
    [data-testid="stMain"] h3 {{font-size:{24 * scale}px;}}
    [data-testid="stMain"] h4 {{font-size:{20 * scale}px;}}
    .stApp {{ font-size:{14 * scale}px; }}
    [data-testid="stMarkdownContainer"] p, [data-testid="stWidgetLabel"] p {{font-size:{14 * scale}px;}}
    [data-testid="stMetricValue"] {{font-size:{32 * scale}px!important;}}
    h1 {{font-size:{32 * scale}px!important;}}
    .nm-summary {{font-size:{14 * scale}px;}}
    .nm-eyebrow {{font-size:{11 * scale}px;}}
    .nm-badge {{font-size:{12 * scale}px;}}
    [data-testid="stCaptionContainer"] p {{font-size:{12 * scale}px;}}
    </style>"""


def presentation_figure(figure):
    """Scale fonts on a figure still using the one PLOTLY_TEMPLATE."""
    result = go.Figure(figure)
    scale = 1.15
    result.update_layout(height=520, font=dict(family=FONT_FAMILY, size=13 * scale),
                         title_font=dict(size=17 * scale), legend_font=dict(size=12 * scale),
                         margin=dict(l=80, r=35, t=115, b=75))
    result.update_xaxes(tickfont_size=13 * scale, title_font_size=14 * scale)
    result.update_yaxes(tickfont_size=13 * scale, title_font_size=14 * scale)
    for annotation in result.layout.annotations:
        annotation.font.size = 12 * scale
    for trace in result.data:
        if hasattr(trace, "textfont"):
            trace.textfont.size = 12 * scale
    return result


