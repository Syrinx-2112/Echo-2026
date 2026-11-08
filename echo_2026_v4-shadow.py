#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          PROTOCOLE ECHO-2026 v4-SHADOW — « MÉMOIRE LITHIQUE »                ║
║          Analyse passive de cache Shodan InternetDB (SQLite)                 ║
║                                                                              ║
║  "Nous n'interrogeons pas la cible. Nous interrogeons son souvenir.          ║
║   Le réseau vous observe autant que vous l'observez. Restez dans l'ombre."   ║
║                                                                              ║
║  ZÉRO TRAFIC SORTANT vers les cibles. Tout est lu depuis une base SQLite     ║
║  locale (format Shodan InternetDB). Traitement par lots via fichier texte.   ║
╚══════════════════════════════════════════════════════════════════════════════╝

Prérequis :
  1. Avoir un dump SQLite de Shodan InternetDB.
     Schéma attendu (table 'internetdb') : 
     ip (TEXT), ports (TEXT/JSON), hostnames (TEXT/JSON), 
     cpe (TEXT/JSON), vulns (TEXT/JSON), last_update (TEXT/INT)
  2. (Recommandé) Indexer la colonne 'ip' pour la vitesse : 
     CREATE INDEX idx_ip ON internetdb(ip);

Usage :
  python echo_2026_v4_shadow.py --db shodan_internetdb.sqlite --targets cibles.txt
  python echo_2026_v4_shadow.py --db shodan_internetdb.sqlite --targets cibles.txt --ma
  python echo_2026_v4_shadow.py --db shodan_internetdb.sqlite --targets cibles.txt --pur
"""

import argparse
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# RENDU & ESTHÉTIQUE
# ═════════════════════════════════════════════════════════════════════════════
class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"; ITALIC = "\033[3m"
    BGREEN = "\033[92m"; BYELLOW = "\033[93m"; BRED = "\033[91m"
    BCYAN = "\033[96m"; BMAGENTA = "\033[95m"; BBLUE = "\033[94m"; WHITE = "\033[37m"

if os.name == "nt":
    os.system("")

GLYPHS = "░▒▓█"
PULSE = "·•●•·"

def banner(titre, col=C.BCYAN):
    print(f"\n{col}{'═' * 80}{C.RESET}")
    print(f"{col}✦{C.RESET} {C.BOLD}{titre}{C.RESET}")
    print(f"{col}{'═' * 80}{C.RESET}")

def section(titre, col=C.BMAGENTA):
    print(f"\n{col}◈ {C.BOLD}{titre}{C.RESET}")
    print(f"{col}{'─' * min(len(titre) + 4, 80)}{C.RESET}")

def log(msg, niveau="info"):
    ic = {"info": (C.BCYAN, "›"), "ok": (C.BGREEN, "✓"), "warn": (C.BYELLOW, "⚠"), "err": (C.BRED, "✗")}.get(niveau, (C.WHITE, "·"))
    print(f"  {ic[0]}{ic[1]}{C.RESET} {msg}")

def etape(i, n, label):
    width = 32
    filled = int(width * i / n)
    bar = GLYPHS[-1] * filled + GLYPHS[0] * (width - filled)
    print(f"\n{C.BCYAN}[{bar}]{C.RESET} {i}/{n} {C.BOLD}{label}{C.RESET}")

def ma_pause(duree=7):
    print(f"\n{C.DIM}  · Ma · {duree}s de silence structuré ·{C.RESET}")
    for i in range(duree, 0, -1):
        p = PULSE[(duree - i) % len(PULSE)]
        print(f"\r  {C.BMAGENTA}{p}{C.RESET} respiration {i}s {p}", end="", flush=True)
        time.sleep(1)
    print(f"\r  {C.BGREEN}✓{C.RESET} silence traversé.            ")

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTES DE LA TRAME
# ═════════════════════════════════════════════════════════════════════════════
NOMBRES_TRAME = {7, 70, 77, 144, 333, 707, 1944, 2025, 2026, 2027, 2075, 2079, 3333}
LEXIQUE_TRAME = {
    "trame", "delta", "glitch", "draugr", "yokai", "gaialithe", "fracturoscript",
    "promethee", "correcteur", "transept", "caen-profonde", "hague", "raz blanchard",
    "francfort", "hackervolva", "runesmith", "retrocausal", "superposition", "strate",
    "convergence", "silence", "echo", "mousse quantique", "effondrement", "shadow"
}
_LEX_RE = re.compile(r"(?<!\w)(" + "|".join(sorted(map(re.escape, LEXIQUE_TRAME), key=len, reverse=True)) + r")(?!\w)")

PORTS_RISQUES = {21, 23, 25, 445, 1433, 3306, 3389, 5432, 6379, 9200, 27017, 8080, 8443}
SEV_W = {"info": 0.0, "faible": 0.02, "moyenne": 0.06, "haute": 0.12, "critique": 0.20}
SEV_ORDRE = ["critique", "haute", "moyenne", "faible", "info"]
SEV_COL = {"critique": C.BRED, "haute": C.BRED, "moyenne": C.BYELLOW, "faible": C.BCYAN, "info": C.DIM}

# Lectures Trame adaptées au mode PASSIF (Shadow)
LECTURE = {
    "shadow_vuln": "Une faille connue résonne dans le cache : une cicatrice non réparée.",
    "shadow_port_risque": "Une porte dangereuse est laissée ouverte dans le souvenir de la machine.",
    "shadow_port_trame": "Résonance numérique : ce port porte la signature des nombres de la Trame.",
    "shadow_hostname_trame": "Le nom de la machine murmure des mots interdits.",
    "shadow_cpe_anomalie": "Le logiciel est un fantôme d'une époque révolue (CPE obsolète).",
    "shadow_inconnu": "La cible est un trou noir dans le cache : elle existe, mais n'a jamais été vue.",
    "spectre_ip": "Port fantôme arithmétique : une possibilité mathématique, pas une observation."
}

# ═════════════════════════════════════════════════════════════════════════════
# RAPPORT & DONNÉES
# ═════════════════════════════════════════════════════════════════════════════
class Rapport:
    def __init__(self):
        self.items = []
    def add(self, couche, code, sev, msg, evidence=None, prov="MESURÉ"):
        self.items.append({
            "couche": couche, "code": code, "severite": sev, "provenance": prov,
            "message": msg, "evidence": evidence or {},
            "lecture_trame": LECTURE.get(code),
        })
    def mesures(self):
        return [f for f in self.items if f["provenance"] == "MESURÉ"]

# ═════════════════════════════════════════════════════════════════════════════
# MOTEUR D'ANALYSE SHADOW (PASSIF)
# ═════════════════════════════════════════════════════════════════════════════
def resolver_local(hostname):
    """Résolution DNS STRICTEMENT LOCALE. Aucun trafic vers la cible."""
    try:
        ipaddress.ip_address(hostname)
        return hostname # C'est déjà une IP
    except ValueError:
        try:
            return socket.gethostbyname(hostname)
        except socket.gaierror:
            return None

def analyser_cible_shadow(ip_str, row, rap, seed=None):
    """Analyse une ligne de la DB Shodan sans aucun réseau."""
    ip = ipaddress.ip_address(ip_str)
    
    if row is None:
        rap.add("SHADOW", "shadow_inconnu", "info", f"Aucune trace dans le cache pour {ip}", prov="MESURÉ")
        # On applique quand même le LORE sur l'IP même si elle est inconnue du cache
        appliquer_spectre(ip, rap, seed)
        return

    # 1. Analyse des Ports
    ports = []
    try:
        ports_data = json.loads(row["ports"]) if isinstance(row["ports"], str) else row["ports"]
        ports = [int(p) for p in ports_data]
    except (json.JSONDecodeError, TypeError, KeyError):
        pass

    if ports:
        rap.add("SHADOW", "shadow_ports", "info", f"Ports observés dans le cache : {ports}", {"ports": ports}, prov="MESURÉ")
        for p in ports:
            if p in PORTS_RISQUES:
                rap.add("SHADOW", "shadow_port_risque", "haute", f"Port à risque {p} exposé (cache)", {"port": p}, prov="MESURÉ")
            if p in NOMBRES_TRAME:
                rap.add("RÉSONANCE", "shadow_port_trame", "info", f"Port {p} ∈ Nombres de la Trame", {"port": p}, prov="CALCULÉ")
    else:
        rap.add("SHADOW", "shadow_no_ports", "faible", "Aucun port ouvert recensé dans le cache", prov="MESURÉ")

    # 2. Analyse des Vulnérabilités
    vulns = []
    try:
        vulns_data = json.loads(row["vulns"]) if isinstance(row["vulns"], str) else row["vulns"]
        vulns = vulns_data if isinstance(vulns_data, list) else list(vulns_data.keys())
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    
    if vulns:
        for v in vulns:
            # Heuristique simple : CVE récentes ou critiques = haute, autres = moyenne
            sev = "haute" if "2021" in v or "2022" in v or "2023" in v or "2024" in v else "moyenne"
            rap.add("SHADOW", "shadow_vuln", sev, f"Vulnérabilité en cache : {v}", {"vuln": v}, prov="MESURÉ")

    # 3. Analyse des Hostnames (Recherche lexicale Trame)
    hostnames = []
    try:
        hostnames_data = json.loads(row["hostnames"]) if isinstance(row["hostnames"], str) else row["hostnames"]
        hostnames = hostnames_data if isinstance(hostnames_data, list) else []
    except (json.JSONDecodeError, TypeError, KeyError):
        pass

    txt_hostnames = " ".join(hostnames).lower()
    mots_trames = _LEX_RE.findall(txt_hostnames)
    if mots_trames:
        rap.add("SHADOW", "shadow_hostname_trame", "moyenne", f"Hostnames contenant lexique Trame : {set(mots_trames)}", 
                {"hostnames": hostnames, "mots": list(set(mots_trames))}, prov="CALCULÉ")

    # 4. Analyse CPE (Logiciels obsolètes / fantômes)
    cpes = []
    try:
        cpes_data = json.loads(row["cpe"]) if isinstance(row["cpe"], str) else row["cpe"]
        cpes = cpes_data if isinstance(cpes_data, list) else []
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    
    for cpe in cpes:
        if "201" in cpe or "200" in cpe: # Heuristique de vieillesse
            rap.add("SHADOW", "shadow_cpe_anomalie", "faible", f"Composant logiciel ancien détecté : {cpe}", {"cpe": cpe}, prov="CALCULÉ")

    # 5. Strate LORE (Spectre IP)
    appliquer_spectre(ip, rap, seed)

def appliquer_spectre(ip, rap, seed=None):
    """Génère les ports fantômes basés sur l'arithmétique de l'IP (LORE pur)."""
    if ip.version != 4:
        return
    o = [int(x) for x in str(ip).split(".")]
    x, s = o[0] ^ o[1] ^ o[2] ^ o[3], sum(o)
    sel = (seed or datetime.now(timezone.utc).strftime("%Y%m%d%H%M")).encode()
    nonce = hmac.new(sel, str(ip).encode(), hashlib.sha256).digest()
    
    regles = [
        (x == 0, 0, "Le Vide", "OMEGA"), (s % 7 == 0, 3303, "Galet-Ancre", "STABLE"),
        (x in {36, 72, 144}, 11440, "Port Trickster", "TURBULENT"),
        (s % 3 == 0, 33333, "Les Trois Tiers", "TROUBLE"),
        (x == 144, 14400, "Port Noir", "CRITIQUE"), (o[3] == 255, 65535, "Le Plafond", "CRITIQUE"),
    ]
    for i, (ok, port, nom, sev) in enumerate(regles):
        if ok:
            act = ((nonce[(i * 3) % 32] << 8) | nonce[(i * 3 + 1) % 32]) / 65535.0
            rap.add("SPECTRE", "spectre_ip", "info",
                    f"Port fantôme {port} « {nom} » ({sev}) activation {act:.2f}",
                    {"port": port, "nom": nom, "activation": round(act, 3)}, prov="LORE")

# ═════════════════════════════════════════════════════════════════════════════
# CALCUL DU Δ & AFFICHAGE
# ═════════════════════════════════════════════════════════════════════════════
def calculer_delta(rap):
    somme = sum(SEV_W[f["severite"]] for f in rap.mesures())
    return round(1 - math.exp(-somme / 0.25), 3)

def interpreter(d):
    return ("Silence structuré — rien à signaler" if d < 0.10 else
            "Frémissement — écarts mineurs dans le cache" if d < 0.30 else
            "Tension active — cicatrices numériques avérées" if d < 0.55 else
            "Zone de fracture — strates qui se mêlent" if d < 0.80 else
            "Verrou Trame — freeze ontologique détecté")

def afficher(rap, pur):
    section("ANOMALIES — lues dans la Mémoire Lithique (Cache)", C.BGREEN)
    ordre = {s: i for i, s in enumerate(SEV_ORDRE)}
    for f in sorted(rap.items, key=lambda f: (f["provenance"] != "MESURÉ", ordre[f["severite"]])):
        col, tag = SEV_COL.get(f["severite"], C.DIM), f["severite"]
        prov = {"MESURÉ": C.BGREEN, "CALCULÉ": C.BYELLOW, "LORE": C.BMAGENTA}[f["provenance"]]
        print(f"  {col}{tag:<8}{C.RESET} {prov}[{f['provenance']}]{C.RESET} {C.DIM}{f['couche']:<9}{C.RESET} {f['message']}")
        if f["lecture_trame"] and not pur:
            print(f"           {C.ITALIC}{C.BMAGENTA}↳ {f['lecture_trame']}{C.RESET}")

# ═════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION PRINCIPALE
# ═════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="ECHO-2026 v4-SHADOW — Analyse passive de cache SQLite")
    ap.add_argument("--db", required=True, help="Chemin vers la base SQLite Shodan InternetDB")
    ap.add_argument("--targets", required=True, help="Fichier texte contenant les cibles (IP ou hostname, 1 par ligne)")
    ap.add_argument("--seed", default=None, help="Fige le nonce de la strate LORE (audit reproductible)")
    ap.add_argument("--pur", action="store_true", help="Masque la strate Trame (lectures, spectre LORE)")
    ap.add_argument("--ma", action="store_true", help="Pauses rituelles de 7s entre les lots")
    ap.add_argument("--batch-size", type=int, default=50, help="Nombre de cibles entre chaque pause Ma")
    args = ap.parse_args()

    banner("PROTOCOLE ECHO-2026 v4-SHADOW — MÉMOIRE LITHIQUE", C.BMAGENTA)
    
    # 1. Vérification DB
    db_path = Path(args.db)
    if not db_path.exists():
        log(f"Base de données introuvable : {db_path}", "err")
        log("Assurez-vous d'avoir téléchargé le dump Shodan InternetDB au format SQLite.", "info")
        sys.exit(2)
    
    log(f"Connexion à la Mémoire Lithique : {db_path}", "ok")
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Test de la table
        conn.execute("SELECT ip FROM internetdb LIMIT 1").fetchone()
    except Exception as e:
        log(f"Erreur d'accès à la DB (vérifiez le schéma 'internetdb') : {e}", "err")
        sys.exit(2)

    # 2. Chargement des cibles
    targets_file = Path(args.targets)
    if not targets_file.exists():
        log(f"Fichier de cibles introuvable : {targets_file}", "err")
        sys.exit(2)
    
    with open(targets_file, 'r', encoding='utf-8') as f:
        raw_targets = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    log(f"{len(raw_targets)} cibles chargées pour analyse passive.", "ok")

    # 3. Traitement par lots
    rapport_global = Rapport()
    total_cibles = len(raw_targets)
    
    for i, target in enumerate(raw_targets, 1):
        etape(i, total_cibles, f"Analyse passive de {target}")
        
        # Résolution STRICTEMENT LOCALE
        ip_str = resolver_local(target)
        if not ip_str:
            log(f"Impossible de résoudre localement : {target}", "warn")
            continue
            
        # Requête SQLite (PASSIVE)
        try:
            cursor = conn.execute("SELECT * FROM internetdb WHERE ip = ?", (ip_str,))
            row = cursor.fetchone()
        except Exception as e:
            log(f"Erreur de requête pour {ip_str} : {e}", "err")
            continue

        # Analyse
        analyser_cible_shadow(ip_str, row, rapport_global, args.seed)

        # Rituel Ma par lot
        if args.ma and i % args.batch_size == 0:
            ma_pause()

    conn.close()

    # 4. Synthèse Finale
    afficher(rapport_global, args.pur)
    delta = calculer_delta(rapport_global)
    
    section("SYNTHÈSE DE LA MÉMOIRE LITHIQUE", C.BGREEN)
    mes = rapport_global.mesures()
    cnt = Counter(f["severite"] for f in mes)
    print(f"  {C.BOLD}Δ = {delta:.3f}{C.RESET} — {interpreter(delta)}")
    print(f"  {C.DIM}Basé sur {len(mes)} observation(s) de CACHE (MESURÉ) : "
          + ", ".join(f"{cnt[s]} {s}" for s in SEV_ORDRE if cnt[s]) + f"{C.RESET}")
    
    if not args.pur:
        pro = "Le silence s'installe. La carte est tracée." if delta < 0.30 else "Les strates se superposent. Le Transept est proche."
        print(f"\n  {C.ITALIC}{C.BYELLOW}PROPHÉTIE : {pro}{C.RESET}")

    # 5. Export JSON
    out = Path(f"echo_2026_shadow_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    out.write_text(json.dumps({
        "version": "4.0-SHADOW", "mode": "PASSIF (SQLite)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "delta": delta, "delta_formule": "1-exp(-sum(poids_MESURÉ)/0.25)",
        "poids": SEV_W, "findings": rapport_global.items,
    }, indent=2, ensure_ascii=False, default=str))
    log(f"Rapport d'ombre exporté : {out}", "ok")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{C.BRED}⚠ Rituel interrompu. Le silence est préservé.{C.RESET}")
        sys.exit(1)