#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║               ECHO-2026 VIZ — Cartographie de la Mousse Quantique            ║
║               Visualisation UMAP + Dashboard HTML interactif                 ║
║                                                                              ║
║  Consomme les JSON exportés par echo_2026_v4.py et produit :                 ║
║    • carte_mousse_quantique.png  (UMAP/t-SNE/PCA des anomalies)              ║
║    • rapport_trame.html          (dashboard interactif Plotly)               ║
║                                                                              ║
║  Usage :                                                                     ║
║    pip install umap-learn scikit-learn matplotlib plotly                     ║
║    python echo_2026_viz.py --input echo_2026_*.json                          ║
║    python echo_2026_viz.py --input dossier_scans/ --offline                  ║
║    python echo_2026_viz.py --input scan.json --pur                           ║
║                                                                              ║
║  Dégradation gracieuse : si UMAP absent → t-SNE → PCA → skip.               ║
║  Si plotly absent → rapport HTML sans graphiques interactifs.                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ─── Dépendances scientifiques (dégradation gracieuse) ───────────────────────
def _try(name):
    try:
        return __import__(name, fromlist=["_"])
    except Exception:
        return None

sklearn_tfidf = _try("sklearn.feature_extraction.text")
sklearn_umap = _try("umap")
sklearn_tsne = _try("sklearn.manifold")
sklearn_pca = _try("sklearn.decomposition")
plt_mod = _try("matplotlib.pyplot")
plotly_go = _try("plotly.graph_objects")
plotly_sub = _try("plotly.subplots")

if plt_mod:
    plt_mod.rcParams.update({
        "figure.facecolor": "#0a0a12", "axes.facecolor": "#0a0a12",
        "savefig.facecolor": "#0a0a12", "text.color": "#d0d0e0",
        "axes.labelcolor": "#d0d0e0", "xtick.color": "#8080a0", "ytick.color": "#8080a0",
        "axes.edgecolor": "#2a2a3a", "axes.titlecolor": "#ff60a0",
        "grid.color": "#1a1a2a", "font.family": "monospace",
    })

# ─── Palette CRT ─────────────────────────────────────────────────────────────
class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"; ITALIC = "\033[3m"
    GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"
    CYAN = "\033[36m"; MAGENTA = "\033[35m"; WHITE = "\033[37m"
    BGREEN = "\033[92m"; BYELLOW = "\033[93m"; BRED = "\033[91m"
    BCYAN = "\033[96m"; BMAGENTA = "\033[95m"

if os.name == "nt":
    os.system("")

PALETTE_PLOTLY = ["#00fff7", "#ff60a0", "#ffb45c", "#7a5cff", "#5cff7a", "#00b4ff", "#ff5c5c"]
SEV_COL_PLOTLY = {"critique": "#ff5c5c", "haute": "#ff8c5c", "moyenne": "#ffb45c", "faible": "#00fff7", "info": "#8080a0"}
PROV_COL_PLOTLY = {"MESURÉ": "#5cff7a", "CALCULÉ": "#ffb45c", "LORE": "#ff60a0"}

# ─── Helpers ─────────────────────────────────────────────────────────────────
def banner(titre, col=C.BCYAN):
    w = 80
    print(f"\n{col}{'═' * w}{C.RESET}")
    pad = (w - len(titre) - 4) // 2
    print(f"{col}✦{C.RESET}{' ' * max(pad, 0)}{C.BOLD}{titre}{C.RESET}{' ' * max(pad, 0)}{col}✦{C.RESET}")
    print(f"{col}{'═' * w}{C.RESET}")

def section(titre, col=C.BMAGENTA):
    print(f"\n{col}◈ {C.BOLD}{titre}{C.RESET}")
    print(f"{col}{'─' * min(len(titre) + 4, 80)}{C.RESET}")

def log(msg, niveau="info"):
    ic = {"info": (C.BCYAN, "›"), "ok": (C.BGREEN, "✓"), "warn": (C.BYELLOW, "⚠"), "err": (C.BRED, "✗")}.get(niveau, (C.WHITE, "·"))
    print(f"  {ic[0]}{ic[1]}{C.RESET} {msg}")

# ─── MODULE 1 : LOADER — Agrège les JSON ─────────────────────────────────────
def charger_json(paths):
    """Charge un ou plusieurs JSON ECHO-2026, retourne une liste de scans."""
    scans = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files = sorted(p.glob("echo_2026_*.json"))
        else:
            files = [p]
        for f in files:
            try:
                data = json.loads(f.read_text())
                if "findings" in data and "delta" in data:
                    data["_source_file"] = f.name
                    scans.append(data)
                    log(f"Chargé : {f.name} (Δ = {data['delta']})", "ok")
                else:
                    log(f"Ignoré (format invalide) : {f.name}", "warn")
            except Exception as e:
                log(f"Erreur lecture {f.name} : {e}", "err")
    return scans

# ─── MODULE 2 : EXTRACTION DES TEXTES ────────────────────────────────────────
def extraire_textes(scans):
    """Extrait les textes des findings pour la vectorisation."""
    textes = []
    metadata = []
    for scan in scans:
        for f in scan.get("findings", []):
            # Combinaison du message + lecture_trame pour enrichir le sens
            texte = f.get("message", "")
            lecture = f.get("lecture_trame") or ""
            texte_complet = f"{texte} {lecture}".strip()
            if len(texte_complet) > 10:
                textes.append(texte_complet)
                metadata.append({
                    "scan": scan.get("_source_file", "?"),
                    "timestamp": scan.get("timestamp", ""),
                    "delta": scan.get("delta", 0),
                    "couche": f.get("couche", "?"),
                    "code": f.get("code", "?"),
                    "severite": f.get("severite", "info"),
                    "provenance": f.get("provenance", "MESURÉ"),
                    "message": f.get("message", ""),
                    "lecture_trame": lecture,
                })
    return textes, metadata

# ─── MODULE 3 : VECTORISATION + RÉDUCTION ────────────────────────────────────
def reduire_dimension(textes):
    """Vectorise TF-IDF puis réduit en 2D (UMAP > t-SNE > PCA)."""
    if len(textes) < 3:
        log(f"Pas assez de textes ({len(textes)}) pour une réduction de dimension.", "warn")
        return None, None

    if not sklearn_tfidf:
        log("scikit-learn absent. Skip vectorisation.", "err")
        return None, None

    log("Vectorisation TF-IDF...", "info")
    vectorizer = sklearn_tfidf.TfidfVectorizer(max_features=200, stop_words='english')
    try:
        X = vectorizer.fit_transform(textes)
    except Exception as e:
        log(f"Échec TF-IDF : {e}", "err")
        return None, None

    log("Réduction de dimension...", "info")
    embedding = None
    methode = None

    # UMAP
    if sklearn_umap:
        try:
            reducer = sklearn_umap.UMAP(n_components=2, random_state=42, metric='cosine', n_neighbors=min(15, len(textes)-1))
            embedding = reducer.fit_transform(X.toarray())
            methode = "UMAP"
            log("✓ UMAP réussi", "ok")
        except Exception as e:
            log(f"UMAP échec : {e}", "warn")

    # Fallback t-SNE
    if embedding is None and sklearn_tsne:
        try:
            perplexity = min(30, len(textes) - 1)
            reducer = sklearn_tsne.TSNE(n_components=2, random_state=42, metric='cosine', perplexity=perplexity)
            embedding = reducer.fit_transform(X.toarray())
            methode = "t-SNE"
            log("✓ Fallback t-SNE réussi", "ok")
        except Exception as e:
            log(f"t-SNE échec : {e}", "warn")

    # Fallback PCA
    if embedding is None and sklearn_pca:
        try:
            reducer = sklearn_pca.PCA(n_components=2, random_state=42)
            embedding = reducer.fit_transform(X.toarray())
            methode = "PCA"
            log("✓ Fallback PCA réussi", "ok")
        except Exception as e:
            log(f"PCA échec : {e}", "err")

    return embedding, methode

# ─── MODULE 4 : VISUALISATION MATPLOTLIB (PNG) ──────────────────────────────
def generer_png(embedding, metadata, methode):
    """Génère un PNG statique de la carte UMAP."""
    if embedding is None or not plt_mod:
        log("Pas d'embedding ou matplotlib absent. Skip PNG.", "warn")
        return None

    section("GÉNÉRATION : carte_mousse_quantique.png", C.BCYAN)
    fig, ax = plt_mod.subplots(figsize=(12, 9))

    # Couleur par provenance
    couleurs = [PROV_COL_PLOTLY.get(m["provenance"], "#8080a0") for m in metadata]
    scatter = ax.scatter(embedding[:, 0], embedding[:, 1], c=couleurs, s=70, alpha=0.85, edgecolors='#00fff7', linewidths=0.5)

    # Annotation des 15 premiers points
    for i in range(min(15, len(metadata))):
        ax.annotate(f"A{i+1}", (embedding[i, 0], embedding[i, 1]),
                    fontsize=8, color="#d0d0e0", xytext=(5, 5), textcoords='offset points')

    ax.set_title(f"TOPOLOGIE DES ANOMALIES RÉTROCAUSALES ({methode})", color="#ff60a0", fontweight='bold', fontsize=14)
    ax.set_xlabel("Composante 1", color="#8080a0")
    ax.set_ylabel("Composante 2", color="#8080a0")
    ax.grid(True, color="#1a1a2a", linestyle='--', alpha=0.3)

    # Légende
    from matplotlib.lines import Line2D
    legend_elements = [Line2D([0], [0], marker='o', color='w', markerfacecolor=PROV_COL_PLOTLY[p], markersize=10, label=p) for p in PROV_COL_PLOTLY]
    ax.legend(handles=legend_elements, loc='upper right', facecolor='#0a0a12', edgecolor='#2a2a3a', labelcolor='#d0d0e0')

    output_path = "carte_mousse_quantique.png"
    plt_mod.savefig(output_path, dpi=150, bbox_inches='tight')
    log(f"✓ PNG sauvegardé : {output_path}", "ok")
    return output_path

# ─── MODULE 5 : DASHBOARD HTML INTERACTIF (PLOTLY) ───────────────────────────
def generer_html(scans, embedding, metadata, methode, offline=False):
    """Génère un dashboard HTML interactif avec Plotly."""
    if not plotly_go or not plotly_sub:
        log("plotly absent. Génération d'un HTML minimaliste sans graphiques.", "warn")
        return generer_html_minimaliste(scans, metadata)

    section("GÉNÉRATION : rapport_trame.html", C.BGREEN)

    # ─── Statistiques globales ───────────────────────────────────────────────
    total_scans = len(scans)
    deltas = [s["delta"] for s in scans]
    delta_moyen = sum(deltas) / len(deltas) if deltas else 0
    delta_max = max(deltas) if deltas else 0
    total_findings = sum(len(s.get("findings", [])) for s in scans)
    compteurs_provenance = Counter(f.get("provenance", "MESURÉ") for s in scans for f in s.get("findings", []))
    compteurs_couche = Counter(f.get("couche", "?") for s in scans for f in s.get("findings", []))
    compteurs_severite = Counter(f.get("severite", "info") for s in scans for f in s.get("findings", []))

    # ─── Figure 1 : Évolution du Δ (timeline) ────────────────────────────────
    fig_delta = plotly_go.Figure()
    timestamps = [s.get("timestamp", "")[:16] for s in scans]
    fig_delta.add_trace(plotly_go.Scatter(
        x=timestamps, y=deltas, mode='lines+markers',
        line=dict(color='#00fff7', width=2),
        marker=dict(size=10, color='#ff60a0'),
        name='Δ local',
        hovertemplate='<b>%{x}</b><br>Δ = %{y:.3f}<extra></extra>'
    ))
    fig_delta.update_layout(
        title="ÉVOLUTION DU Δ LOCAL",
        xaxis_title="Timestamp", yaxis_title="Δ",
        template="plotly_dark",
        paper_bgcolor="#0a0a12", plot_bgcolor="#0a0a12",
        font=dict(family="monospace", color="#d0d0e0"),
        height=400,
    )

    # ─── Figure 2 : UMAP interactif ──────────────────────────────────────────
    fig_umap = plotly_go.Figure()
    if embedding is not None:
        for prov in ["MESURÉ", "CALCULÉ", "LORE"]:
            idx = [i for i, m in enumerate(metadata) if m["provenance"] == prov]
            if idx:
                fig_umap.add_trace(plotly_go.Scattergl(
                    x=embedding[idx, 0], y=embedding[idx, 1],
                    mode='markers',
                    marker=dict(size=8, color=PROV_COL_PLOTLY[prov], opacity=0.8),
                    name=prov,
                    text=[f"<b>{metadata[i]['couche']}</b> [{metadata[i]['severite']}]<br>{metadata[i]['message'][:100]}<br><i>{metadata[i]['lecture_trame'][:80]}</i>" for i in idx],
                    hovertemplate='%{text}<extra></extra>'
                ))
        fig_umap.update_layout(
            title=f"CARTE DE LA MOUSSE QUANTIQUE ({methode})",
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            template="plotly_dark",
            paper_bgcolor="#0a0a12", plot_bgcolor="#0a0a12",
            font=dict(family="monospace", color="#d0d0e0"),
            height=600,
            legend=dict(bgcolor="#0a0a12", bordercolor="#2a2a3a", borderwidth=1),
        )

    # ─── Figure 3 : Anomalies par couche (bar chart) ─────────────────────────
    fig_couche = plotly_go.Figure()
    couches = sorted(compteurs_couche.keys())
    fig_couche.add_trace(plotly_go.Bar(
        x=couches, y=[compteurs_couche[c] for c in couches],
        marker_color=PALETTE_PLOTLY[:len(couches)],
        text=[compteurs_couche[c] for c in couches],
        textposition='auto',
    ))
    fig_couche.update_layout(
        title="ANOMALIES PAR COUCHE",
        xaxis_title="Couche", yaxis_title="Nombre",
        template="plotly_dark",
        paper_bgcolor="#0a0a12", plot_bgcolor="#0a0a12",
        font=dict(family="monospace", color="#d0d0e0"),
        height=350,
    )

    # ─── Figure 4 : Répartition par sévérité (pie) ───────────────────────────
    fig_sev = plotly_go.Figure()
    severites = ["critique", "haute", "moyenne", "faible", "info"]
    fig_sev.add_trace(plotly_go.Pie(
        labels=severites,
        values=[compteurs_severite.get(s, 0) for s in severites],
        marker=dict(colors=[SEV_COL_PLOTLY[s] for s in severites]),
        textinfo='label+value',
        hovertemplate='<b>%{label}</b><br>%{value} (%{percent})<extra></extra>'
    ))
    fig_sev.update_layout(
        title="RÉPARTITION PAR SÉVÉRITÉ",
        template="plotly_dark",
        paper_bgcolor="#0a0a12", plot_bgcolor="#0a0a12",
        font=dict(family="monospace", color="#d0d0e0"),
        height=350,
    )

    # ─── Génération HTML ─────────────────────────────────────────────────────
    import plotly.io as pio
    include_plotlyjs = True if offline else "cdn"

    html_parts = [
        "<!DOCTYPE html>",
        "<html lang='fr'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>ECHO-2026 — Rapport de la Trame</title>",
        "<style>",
        ":root { --bg: #0a0a12; --fg: #d0d0e0; --cyan: #00fff7; --magenta: #ff60a0; --amber: #ffb45c; --green: #5cff7a; --red: #ff5c5c; --border: #2a2a3a; }",
        "* { box-sizing: border-box; margin: 0; padding: 0; }",
        "body { background: var(--bg); color: var(--fg); font-family: 'Share Tech Mono', 'Courier New', monospace; font-size: 14px; line-height: 1.6; padding: 2rem; }",
        "h1 { color: var(--cyan); font-size: 2rem; letter-spacing: 0.1em; text-shadow: 0 0 20px rgba(0, 255, 247, 0.5); margin-bottom: 0.5rem; }",
        "h2 { color: var(--magenta); font-size: 1.3rem; margin: 2rem 0 1rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }",
        ".subtitle { color: var(--fg); opacity: 0.7; font-style: italic; margin-bottom: 2rem; }",
        ".kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin: 2rem 0; }",
        ".kpi { background: #12121f; border: 1px solid var(--border); border-radius: 8px; padding: 1rem; text-align: center; }",
        ".kpi-value { font-size: 2rem; font-weight: bold; color: var(--cyan); }",
        ".kpi-label { font-size: 0.85rem; color: var(--fg); opacity: 0.7; }",
        ".chart-container { background: #12121f; border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin: 1.5rem 0; }",
        "table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.9rem; }",
        "th { background: #12121f; color: var(--cyan); padding: 0.6rem; text-align: left; border-bottom: 2px solid var(--border); }",
        "td { padding: 0.5rem; border-bottom: 1px solid var(--border); }",
        "tr:hover { background: rgba(0, 255, 247, 0.05); }",
        ".sev-critique { color: var(--red); font-weight: bold; }",
        ".sev-haute { color: #ff8c5c; }",
        ".sev-moyenne { color: var(--amber); }",
        ".sev-faible { color: var(--cyan); }",
        ".sev-info { color: #8080a0; }",
        ".prov-MESURÉ { color: var(--green); }",
        ".prov-CALCULÉ { color: var(--amber); }",
        ".prov-LORE { color: var(--magenta); }",
        ".lecture-trame { font-style: italic; color: var(--magenta); opacity: 0.8; font-size: 0.85rem; }",
        ".footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border); text-align: center; color: var(--fg); opacity: 0.5; font-size: 0.85rem; }",
        "/* Effet CRT */",
        "body::before { content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 100; background: repeating-linear-gradient(0deg, rgba(0,0,0,0.1), rgba(0,0,0,0.1) 1px, transparent 1px, transparent 3px); }",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>✦ ECHO-2026 — RAPPORT DE LA TRAME ✦</h1>",
        f"<p class='subtitle'>Cartographie de la Mousse Quantique · Généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>",
        "<div class='kpis'>",
        f"<div class='kpi'><div class='kpi-value'>{total_scans}</div><div class='kpi-label'>Scans analysés</div></div>",
        f"<div class='kpi'><div class='kpi-value'>{delta_moyen:.3f}</div><div class='kpi-label'>Δ moyen</div></div>",
        f"<div class='kpi'><div class='kpi-value'>{delta_max:.3f}</div><div class='kpi-label'>Δ max</div></div>",
        f"<div class='kpi'><div class='kpi-value'>{total_findings}</div><div class='kpi-label'>Anomalies totales</div></div>",
        f"<div class='kpi'><div class='kpi-value prov-MESURÉ'>{compteurs_provenance.get('MESURÉ', 0)}</div><div class='kpi-label'>MESURÉ</div></div>",
        f"<div class='kpi'><div class='kpi-value prov-CALCULÉ'>{compteurs_provenance.get('CALCULÉ', 0)}</div><div class='kpi-label'>CALCULÉ</div></div>",
        f"<div class='kpi'><div class='kpi-value prov-LORE'>{compteurs_provenance.get('LORE', 0)}</div><div class='kpi-label'>LORE</div></div>",
        "</div>",
        "<h2>Évolution du Δ</h2>",
        "<div class='chart-container'>",
        pio.to_html(fig_delta, full_html=False, include_plotlyjs=include_plotlyjs),
        "</div>",
    ]

    if embedding is not None:
        html_parts.extend([
            "<h2>Carte de la Mousse Quantique</h2>",
            "<div class='chart-container'>",
            pio.to_html(fig_umap, full_html=False, include_plotlyjs=False),
            "</div>",
        ])

    html_parts.extend([
        "<h2>Anomalies par couche</h2>",
        "<div class='chart-container'>",
        pio.to_html(fig_couche, full_html=False, include_plotlyjs=False),
        "</div>",
        "<h2>Répartition par sévérité</h2>",
        "<div class='chart-container'>",
        pio.to_html(fig_sev, full_html=False, include_plotlyjs=False),
        "</div>",
        "<h2>Détail des findings</h2>",
        "<table>",
        "<tr><th>Scan</th><th>Couche</th><th>Sévérité</th><th>Provenance</th><th>Message</th><th>Lecture Trame</th></tr>",
    ])

    for scan in scans:
        for f in scan.get("findings", []):
            sev_class = f"sev-{f.get('severite', 'info')}"
            prov_class = f"prov-{f.get('provenance', 'MESURÉ')}"
            html_parts.append(
                f"<tr>"
                f"<td>{scan.get('_source_file', '?')[:20]}</td>"
                f"<td>{f.get('couche', '?')}</td>"
                f"<td class='{sev_class}'>{f.get('severite', 'info')}</td>"
                f"<td class='{prov_class}'>{f.get('provenance', 'MESURÉ')}</td>"
                f"<td>{f.get('message', '')[:80]}</td>"
                f"<td class='lecture-trame'>{f.get('lecture_trame', '')[:60]}</td>"
                f"</tr>"
            )

    html_parts.extend([
        "</table>",
        "<div class='footer'>",
        "<p>✦ ECHO-2026 v4.0 — Trame Réelle ✦</p>",
        "<p>« La frontière est abolie à l'affichage. »</p>",
        "</div>",
        "</body>",
        "</html>",
    ])

    output_path = "rapport_trame.html"
    Path(output_path).write_text("\n".join(html_parts), encoding="utf-8")
    log(f"✓ HTML sauvegardé : {output_path}", "ok")
    return output_path

def generer_html_minimaliste(scans, metadata):
    """Fallback si plotly absent : HTML statique basique."""
    html = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>ECHO-2026</title>",
        "<style>body{background:#0a0a12;color:#d0d0e0;font-family:monospace;padding:2rem}table{width:100%;border-collapse:collapse}th,td{padding:0.5rem;border:1px solid #2a2a3a}</style>",
        "</head><body>",
        "<h1>ECHO-2026 — Rapport minimaliste</h1>",
        "<p>Plotly absent. Install with: pip install plotly</p>",
        "<table><tr><th>Scan</th><th>Δ</th><th>Findings</th></tr>",
    ]
    for s in scans:
        html.append(f"<tr><td>{s.get('_source_file','?')}</td><td>{s.get('delta',0):.3f}</td><td>{len(s.get('findings',[]))}</td></tr>")
    html.append("</table></body></html>")
    Path("rapport_trame.html").write_text("\n".join(html), encoding="utf-8")
    return "rapport_trame.html"

# ─── ORCHESTRATION ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ECHO-2026 VIZ — Cartographie de la Mousse Quantique")
    parser.add_argument("--input", nargs="+", required=True, help="Fichier(s) JSON ou dossier(s)")
    parser.add_argument("--offline", action="store_true", help="Embarque plotly.js (HTML 100% offline)")
    parser.add_argument("--pur", action="store_true", help="Masque les findings LORE")
    args = parser.parse_args()

    banner("ECHO-2026 VIZ — CARTOGRAPHIE DE LA MOUSSE QUANTIQUE", C.BCYAN)

    # 1. Charger les JSON
    section("CHARGEMENT DES SCANS", C.BGREEN)
    scans = charger_json(args.input)
    if not scans:
        log("Aucun scan valide trouvé.", "err")
        sys.exit(1)

    # 2. Filtrer si --pur
    if args.pur:
        for s in scans:
            s["findings"] = [f for f in s.get("findings", []) if f.get("provenance") != "LORE"]
        log("Mode PUR : findings LORE masqués", "info")

    # 3. Extraire les textes
    section("EXTRACTION DES TEXTES", C.BMAGENTA)
    textes, metadata = extraire_textes(scans)
    log(f"{len(textes)} textes extraits pour vectorisation", "ok")

    # 4. Réduction de dimension
    section("RÉDUCTION DE DIMENSION", C.BYELLOW)
    embedding, methode = reduire_dimension(textes)

    # 5. Génération PNG
    png_path = generer_png(embedding, metadata, methode)

    # 6. Génération HTML
    html_path = generer_html(scans, embedding, metadata, methode, offline=args.offline)

    # 7. Synthèse
    section("SYNTHÈSE", C.BGREEN)
    if png_path:
        log(f"✓ PNG : {png_path}", "ok")
    if html_path:
        log(f"✓ HTML : {html_path}", "ok")
    print(f"\n{C.DIM}{'•' * 80}{C.RESET}\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{C.BRED}⚠ Rituel interrompu.{C.RESET}\n")
        sys.exit(1)