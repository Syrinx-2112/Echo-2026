#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║               PROTOCOLE ECHO-2026 v4.0 — « TRAME RÉELLE »                    ║
║        Observations réseau authentiques · Lecture Trame superposée           ║
║                                                                              ║
║  La frontière est abolie à l'AFFICHAGE : chaque anomalie mesurée sur         ║
║  l'Internet réel reçoit sa lecture Trame, sur la même ligne.                 ║
║  Elle est conservée dans les DONNÉES : chaque entrée du rapport JSON porte   ║
║  une provenance — MESURÉ (observé), CALCULÉ (dérivé), LORE (fiction pure).   ║
║  Seul MESURÉ entre dans le champ Δ.                                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

Couches (toutes réelles) :
  DNS      résolution, adresses non publiques (indice de DNS rebinding)
  HTTP     en-têtes, redirections validées saut par saut, dérive d'horloge,
           Last-Modified futur / antérieur à 1995 / postérieur à Date
  TLS      validité du certificat vs règle CA/B SC-081v3 (≤200 j depuis le
           2026-03-15), expiration, vérification de chaîne, empreinte
  TCP      (opt-in --authorized) ports ouverts parmi une liste courte
  RSS      dates de publication futures, flux périmés ou vides
  Baseline différentielle : nouvelle IP, nouveau port, certificat changé…

Usage :
    python echo_2026_v4.py --target example.com
    python echo_2026_v4.py --target https://example.com --update-baseline
    python echo_2026_v4.py --target mon-serveur.tld --authorized     # + sondage TCP
    python echo_2026_v4.py --target example.com --pur                # sans lore

Dépendance optionnelle : feedparser (couche RSS). Le reste est stdlib.

ÉTHIQUE : n'utilisez le sondage TCP (--authorized) que sur des systèmes qui vous
appartiennent ou dont vous avez l'autorisation écrite d'audit.
"""

import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import feedparser  # type: ignore
except Exception:
    feedparser = None

# ═════════════════════════════════════════════════════════════════════════════
# RENDU
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
    print(f"\n{col}{'═' * 80}{C.RESET}\n{col}✦{C.RESET} {C.BOLD}{titre}{C.RESET}\n{col}{'═' * 80}{C.RESET}")

def section(titre, col=C.BMAGENTA):
    print(f"\n{col}◈ {C.BOLD}{titre}{C.RESET}\n{col}{'─' * min(len(titre) + 4, 80)}{C.RESET}")

def log(msg, niveau="info"):
    ic = {"info": (C.BCYAN, "›"), "ok": (C.BGREEN, "✓"), "warn": (C.BYELLOW, "⚠"),
          "err": (C.BRED, "✗")}.get(niveau, (C.WHITE, "·"))
    print(f"  {ic[0]}{ic[1]}{C.RESET} {msg}")

def etape(i, n, label):
    """Progression liée aux étapes RÉELLES (aucun sleep décoratif)."""
    width = 32
    filled = int(width * i / n)
    bar = GLYPHS[-1] * filled + GLYPHS[0] * (width - filled)
    print(f"\n  {C.BCYAN}[{bar}]{C.RESET} {i}/{n} {C.BOLD}{label}{C.RESET}")

def ma_pause(duree=7):
    print(f"\n{C.DIM}  · Ma · {duree}s de silence structuré ·{C.RESET}")
    for i in range(duree, 0, -1):
        p = PULSE[(duree - i) % len(PULSE)]
        print(f"\r  {C.BMAGENTA}{p}{C.RESET} respiration {i}s {p}", end="", flush=True)
        time.sleep(1)
    print(f"\r  {C.BGREEN}✓{C.RESET} silence traversé.            ")

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTES (lore conservé, annotations seulement)
# ═════════════════════════════════════════════════════════════════════════════
UA = {"User-Agent": "Echo-2026/4.0 (audit; non-intrusif)"}

NOMBRES_TRAME = {7, 70, 77, 144, 333, 707, 1944, 2025, 2026, 2027, 2075, 2079, 3333}
DATES_SIGNATURES = {
    "06-06": "Nuit des Trois Caen", "07-07": "Fenêtre du Transept",
    "11-11": "Rappel lithique", "03-14": "Silence du marché", "06-21": "Convergence",
}
# "ma" retiré : mot-outil français → faux positifs massifs. Frontières de mots imposées.
LEXIQUE_TRAME = {
    "trame", "delta", "glitch", "draugr", "yōkai", "gaïalithe", "fracturoscript",
    "prométhée", "correcteur", "transept", "caen-profonde", "hague", "raz blanchard",
    "francfort", "hackervölva", "runesmith", "rétrocausal", "superposition", "strate",
    "convergence", "silence structuré", "écho", "mousse quantique", "effondrement",
}
_LEX_RE = re.compile(
    r"(?<!\w)(" + "|".join(sorted(map(re.escape, LEXIQUE_TRAME), key=len, reverse=True)) + r")(?!\w)"
)

FLUX_RSS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.lemonde.fr/rss/une.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
]

PORTS_DEFAUT = [21, 22, 23, 25, 80, 110, 143, 443, 445, 3306, 3389, 5432, 6379, 8080, 8443, 9200, 27017]
PORTS_RISQUES = {23, 445, 3306, 3389, 5432, 6379, 9200, 27017, 21}

SEV_W = {"info": 0.0, "faible": 0.02, "moyenne": 0.06, "haute": 0.12, "critique": 0.20}
SEV_ORDRE = ["critique", "haute", "moyenne", "faible", "info"]
SEV_COL = {"critique": C.BRED, "haute": C.BRED, "moyenne": C.BYELLOW, "faible": C.BCYAN, "info": C.DIM}

# Lecture Trame : couche d'interprétation, JAMAIS une observation.
LECTURE = {
    "date_skew": "Le temps du serveur ne bat plus au rythme du nôtre — une strate glisse.",
    "lm_futur": "Un fichier se souvient d'un jour qui n'a pas eu lieu : écho rétrocausal.",
    "lm_ancien": "Un galet d'avant le Réseau, resurgi dans la trame.",
    "lm_apres_date": "Le serveur se contredit lui-même : superposition de strates.",
    "tls_futur": "Le sceau n'est pas encore né, et pourtant il parle.",
    "tls_expire": "Le sceau est mort, la porte reste ouverte : un draugr numérique.",
    "tls_verif": "La chaîne de confiance se rompt : glitch dans la lignée.",
    "tls_validite": "Le sceau dure plus que ne le permet la Loi de 2026 : un oubli, ou un défi.",
    "tls_bientot": "Le sceau s'éteindra bientôt : le Correcteur approche.",
    "tls_change": "Le sceau a changé de visage depuis la dernière veille.",
    "port_nouveau": "Une porte s'est ouverte pendant le silence.",
    "port_risque": "Une porte que personne ne devrait avoir ouverte sur le Réseau.",
    "dns_non_public": "Le nom public mène à une adresse intérieure : le Transept est ouvert.",
    "redirect_prive": "La route mène vers l'intérieur : piège de la Trame.",
    "downgrade": "Le chiffrement se défait en chemin : effondrement de la couche.",
    "rss_futur": "Une nouvelle datée de demain : l'écho précède la voix.",
    "ip_changee": "L'adresse a migré : la mousse quantique s'est déplacée.",
    "server_change": "La machine a changé de visage derrière le masque.",
}

# ═════════════════════════════════════════════════════════════════════════════
# RAPPORT : chaque entrée porte sa provenance
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
# CIBLE & GARDE SSRF (résolution DNS validée, pas seulement l'IP littérale)
# ═════════════════════════════════════════════════════════════════════════════
def parse_target(value):
    v = value.strip()
    try:  # IP nue
        ip = ipaddress.ip_address(v)
        v = f"https://[{ip}]" if ip.version == 6 else f"https://{ip}"
    except ValueError:
        if "://" not in v:
            v = "https://" + v
    p = urlparse(v)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"schéma non autorisé : {p.scheme!r}")
    if not p.hostname:
        raise ValueError("URL sans hôte")
    try:
        ipaddress.ip_address(p.hostname)
        is_ip = True
    except ValueError:
        is_ip = False
    port = p.port or (443 if p.scheme == "https" else 80)
    return {"url": v, "scheme": p.scheme, "host": p.hostname, "port": port, "is_ip": is_ip}

def ip_public(ip):
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global

def resolve(host, port):
    out = []
    for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip not in out:
            out.append(ip)
    return out

# ═════════════════════════════════════════════════════════════════════════════
# COUCHE DNS
# ═════════════════════════════════════════════════════════════════════════════
def scan_dns(tgt, rap, allow_private):
    try:
        ips = resolve(tgt["host"], tgt["port"])
    except OSError as e:
        rap.add("DNS", "dns_echec", "moyenne", f"Résolution impossible : {e}")
        return []
    rap.add("DNS", "dns_ok", "info", f"{tgt['host']} → {', '.join(map(str, ips))}",
            {"ips": [str(i) for i in ips]})
    non_pub = [str(i) for i in ips if not ip_public(i)]
    if non_pub and not tgt["is_ip"]:
        rap.add("DNS", "dns_non_public", "haute" if not allow_private else "info",
                f"Le nom public {tgt['host']} résout vers une adresse non publique : {', '.join(non_pub)}",
                {"adresses": non_pub})
    return ips

# ═════════════════════════════════════════════════════════════════════════════
# COUCHE HTTP / TLS (redirections validées à chaque saut)
# ═════════════════════════════════════════════════════════════════════════════
def _ctx(verify):
    c = ssl.create_default_context()
    if not verify:
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
    return c

def _requete(scheme, host, port, path, verify, timeout):
    if scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=_ctx(verify))
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.connect()  # on capture le TLS AVANT la requête (la socket peut être fermée après)
        tls = None
        if scheme == "https":
            s = conn.sock
            tls = {"version": s.version(), "cipher": s.cipher(),
                   "cert": s.getpeercert(), "der": s.getpeercert(binary_form=True)}
        t0 = time.time()
        conn.request("HEAD", path, headers=UA)
        r = conn.getresponse()
        t1 = time.time()
        return {"status": r.status, "headers": {k.lower(): v for k, v in r.getheaders()},
                "t0": t0, "t1": t1, "tls": tls}
    finally:
        conn.close()

def probe_http(tgt, rap, allow_private, timeout):
    url, chain, verify_ok = tgt["url"], [], True
    for hop in range(6):
        p = urlparse(url)
        host = p.hostname
        port = p.port or (443 if p.scheme == "https" else 80)
        path = (p.path or "/") + (("?" + p.query) if p.query else "")
        try:
            ips = resolve(host, port)
        except OSError as e:
            rap.add("HTTP", "http_dns", "moyenne", f"Saut {hop} : résolution impossible pour {host} ({e})")
            return None
        if not allow_private and any(not ip_public(i) for i in ips):
            if hop > 0:
                rap.add("HTTP", "redirect_prive", "critique",
                        f"Redirection vers un hôte non public : {host} → {', '.join(map(str, ips))}",
                        {"chaine": chain})
            return None
        try:
            res = _requete(p.scheme, host, port, path, True, timeout)
        except ssl.SSLCertVerificationError as e:
            verify_ok = False
            rap.add("TLS", "tls_verif", "haute",
                    f"Vérification du certificat échouée pour {host} : {e.verify_message}",
                    {"hote": host, "code": e.verify_code, "message": e.verify_message})
            try:
                res = _requete(p.scheme, host, port, path, False, timeout)
            except (OSError, http.client.HTTPException) as e2:
                rap.add("HTTP", "http_echec", "moyenne", f"Échec après contournement : {e2}")
                return None
        except (OSError, http.client.HTTPException) as e:
            rap.add("HTTP", "http_echec", "moyenne", f"Échec de connexion à {host}:{port} — {e}")
            return None
        chain.append({"url": url, "status": res["status"]})
        loc = res["headers"].get("location")
        if res["status"] in (301, 302, 303, 307, 308) and loc:
            nxt = urljoin(url, loc)
            if p.scheme == "https" and urlparse(nxt).scheme == "http":
                rap.add("HTTP", "downgrade", "haute",
                        f"Redirection HTTPS → HTTP : {url} → {nxt}", {"de": url, "vers": nxt})
            if urlparse(nxt).hostname != host:
                rap.add("HTTP", "redirect_hote", "info", f"Redirection vers un autre hôte : {host} → {urlparse(nxt).hostname}")
            url = nxt
            continue
        res["final_url"], res["chain"], res["verify_ok"] = url, chain, verify_ok
        return res
    rap.add("HTTP", "redirect_boucle", "moyenne", "Plus de 5 redirections successives", {"chaine": chain})
    return None

def _parse_http_date(s):
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def analyser_http(res, rap):
    h = res["headers"]
    mid = datetime.fromtimestamp((res["t0"] + res["t1"]) / 2, tz=timezone.utc)
    date_h = _parse_http_date(h["date"]) if "date" in h else None
    rap.add("HTTP", "http_ok", "info", f"HTTP {res['status']} — {res['final_url']}",
            {"status": res["status"], "chaine": res["chain"]})

    if date_h:
        skew = (date_h - mid).total_seconds()
        a = abs(skew)
        if a > 60:
            sev = "haute" if a > 3600 else "moyenne" if a > 300 else "faible"
            rap.add("HTTP", "date_skew", sev,
                    f"Dérive d'horloge serveur/local : {skew:+.0f}s (horloge locale OU serveur fautive)",
                    {"date_header": h["date"], "local_utc": mid.isoformat(), "skew_s": round(skew, 1)})

    lm = _parse_http_date(h["last-modified"]) if "last-modified" in h else None
    if "last-modified" in h and lm is None:
        rap.add("HTTP", "lm_illisible", "faible", "Last-Modified illisible", {"valeur": h["last-modified"]})
    if lm:
        if lm > mid + timedelta(minutes=5):
            rap.add("HTTP", "lm_futur", "haute", f"Last-Modified dans le futur : {lm.isoformat()}",
                    {"last_modified": h["last-modified"]})
        elif lm.year < 1995:
            rap.add("HTTP", "lm_ancien", "moyenne", f"Last-Modified antérieur à 1995 : {lm.isoformat()}",
                    {"last_modified": h["last-modified"]})
        if date_h and lm > date_h + timedelta(seconds=5):
            rap.add("HTTP", "lm_apres_date", "haute", "Last-Modified postérieur à l'en-tête Date du même serveur",
                    {"last_modified": h["last-modified"], "date": h["date"]})

    for k in ("server", "x-powered-by"):
        if k in h and re.search(r"\d+\.\d+", h[k]):
            rap.add("HTTP", "version_exposee", "faible", f"En-tête {k} expose une version : {h[k]}", {k: h[k]})
    if res["final_url"].startswith("https") and "strict-transport-security" not in h:
        rap.add("HTTP", "hsts_absent", "faible", "HSTS absent sur la réponse HTTPS finale")

def analyser_tls(res, rap, tgt):
    tls = res.get("tls")
    if not tls:
        return None
    rap.add("TLS", "tls_ok", "info", f"{tls['version']} · {tls['cipher'][0] if tls['cipher'] else '?'}")
    if tls["version"] in ("TLSv1", "TLSv1.1", "SSLv3"):
        rap.add("TLS", "tls_ancien", "haute", f"Protocole obsolète négocié : {tls['version']}")
    cert = tls.get("cert") or {}
    snap = {"fp": hashlib.sha256(tls["der"]).hexdigest() if tls.get("der") else None}
    if not cert:  # contexte non vérifié : pas de dict exploitable
        return snap
    now = datetime.now(timezone.utc)
    nb = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notBefore"]), tz=timezone.utc)
    na = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)
    issuer = {k: v for rdn in cert.get("issuer", ()) for (k, v) in rdn}
    snap.update({"issuer": issuer.get("organizationName") or issuer.get("commonName"),
                 "not_after": na.isoformat(), "not_before": nb.isoformat()})
    ev = {"notBefore": nb.isoformat(), "notAfter": na.isoformat(), "emetteur": snap["issuer"]}
    if nb > now:
        rap.add("TLS", "tls_futur", "critique", f"Certificat pas encore valide (début : {nb.date()})", ev)
    if na < now:
        rap.add("TLS", "tls_expire", "critique", f"Certificat expiré le {na.date()}", ev)
    else:
        j = (na - now).total_seconds() / 86400
        if j < 14:
            rap.add("TLS", "tls_bientot", "haute" if j < 3 else "moyenne",
                    f"Certificat expirant dans {j:.1f} jours", ev)
    duree = (na - nb).total_seconds() / 86400
    limite = 200 if nb >= datetime(2026, 3, 15, tzinfo=timezone.utc) else 398
    if duree > limite + 0.01:
        rap.add("TLS", "tls_validite", "moyenne",
                f"Validité de {duree:.0f} j > {limite} j (règle CA/B SC-081v3 ; ignorable si CA privée)",
                {**ev, "duree_jours": round(duree, 1), "limite_jours": limite})
    snap["duree_jours"] = round(duree, 1)
    return snap

# ═════════════════════════════════════════════════════════════════════════════
# COUCHE TCP (opt-in)
# ═════════════════════════════════════════════════════════════════════════════
def _port_ouvert(args):
    ip, port, timeout = args
    s = socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return port if s.connect_ex((str(ip), port)) == 0 else None
    except OSError:
        return None
    finally:
        s.close()

def scan_tcp(ip, ports, rap, timeout=1.5):
    with ThreadPoolExecutor(max_workers=16) as ex:
        ouverts = sorted(p for p in ex.map(_port_ouvert, [(ip, p, timeout) for p in ports]) if p)
    rap.add("TCP", "tcp_ouverts", "info", f"{ip} — ports ouverts : {ouverts or 'aucun parmi ' + str(len(ports))}",
            {"ip": str(ip), "ouverts": ouverts, "sondes": ports})
    for p in ouverts:
        if p in PORTS_RISQUES:
            rap.add("TCP", "port_risque", "haute", f"Port {p} exposé sur {ip}", {"port": p, "ip": str(ip)})
    return ouverts

# ═════════════════════════════════════════════════════════════════════════════
# COUCHE RSS (anomalies réelles de flux)
# ═════════════════════════════════════════════════════════════════════════════
def scan_rss(rap):
    if not feedparser:
        rap.add("RSS", "rss_absent", "info", "feedparser absent — couche RSS sautée")
        return
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(10)
    now = datetime.now(timezone.utc)
    lex = Counter()
    try:
        for url in FLUX_RSS:
            feed = feedparser.parse(url)
            n = len(feed.entries)
            if getattr(feed, "bozo", 0) and not n:
                rap.add("RSS", "rss_illisible", "faible", f"Flux illisible : {url}", {"erreur": str(feed.get("bozo_exception"))})
                continue
            futurs = 0
            for e in feed.entries[:30]:
                pp = e.get("published_parsed") or e.get("updated_parsed")
                if pp:
                    d = datetime(*pp[:6], tzinfo=timezone.utc)
                    if d > now + timedelta(hours=1):
                        futurs += 1
                        rap.add("RSS", "rss_futur", "moyenne",
                                f"Article daté du futur ({d.isoformat()}) : {e.get('title', '')[:60]}",
                                {"flux": url, "date": d.isoformat()})
                txt = f"{e.get('title', '')} {e.get('summary', '')}".lower()
                lex.update(set(_LEX_RE.findall(txt)))
            rap.add("RSS", "rss_ok", "info", f"{url} — {n} articles, {futurs} daté(s) du futur", {"articles": n})
        if lex:
            rap.add("RSS", "rss_lexique", "info",
                    f"Résonance lexicale (mots, pas de pondération) : {dict(lex)}",
                    {"mots": dict(lex)}, prov="CALCULÉ")
    finally:
        socket.setdefaulttimeout(old)

# ═════════════════════════════════════════════════════════════════════════════
# STRATE FICTION (étiquetée LORE, pondération nulle) & RÉSONANCES (CALCULÉ)
# ═════════════════════════════════════════════════════════════════════════════
def spectre_ip(ip, seed=None):
    """Ports fantômes : pure arithmétique sur les octets. AUCUNE observation."""
    if ip.version != 4:
        return []
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
    out = []
    for ok, port, nom, sev in regles:
        if ok:
            i = len(out)
            act = ((nonce[(i * 3) % 32] << 8) | nonce[(i * 3 + 1) % 32]) / 65535.0
            out.append({"port": port, "nom": nom, "severite_lore": sev, "activation": round(act, 3)})
    return out

def resonances(rap):
    """Coïncidences numériques entre valeurs MESURÉES et le lexique de nombres/dates.
    Numérologie assumée : aucune causalité, pondération nulle."""
    nombres, dates = [], []
    for f in rap.mesures():
        ev = f["evidence"]
        for p in ev.get("ouverts", []):
            nombres.append((p, f"port ouvert {p}"))
        if "duree_jours" in ev:
            nombres.append((int(ev["duree_jours"]), "durée du certificat (jours)"))
        for k in ("notBefore", "notAfter", "local_utc"):
            if k in ev:
                dates.append((ev[k][5:10], f"{k}"))
        for k in ("last_modified", "date_header", "date"):
            if k in ev:
                d = _parse_http_date(ev[k])
                if d:
                    dates.append((d.strftime("%m-%d"), k))
    nombres, dates = list(dict.fromkeys(nombres)), list(dict.fromkeys(dates))
    for n, src in nombres:
        if n in NOMBRES_TRAME:
            rap.add("RÉSONANCE", "resonance_nombre", "info", f"{n} ({src}) ∈ nombres-Trame", {"nombre": n}, prov="CALCULÉ")
    for md, src in dates:
        if md in DATES_SIGNATURES:
            rap.add("RÉSONANCE", "resonance_date", "info", f"{md} ({src}) = « {DATES_SIGNATURES[md]} »", {"date": md}, prov="CALCULÉ")

# ═════════════════════════════════════════════════════════════════════════════
# BASELINE DIFFÉRENTIELLE
# ═════════════════════════════════════════════════════════════════════════════
def charger_baseline(path, key):
    try:
        return json.loads(Path(path).read_text()).get(key)
    except Exception:
        return None

def sauver_baseline(path, key, snap):
    try:
        data = json.loads(Path(path).read_text())
    except Exception:
        data = {}
    data[key] = snap
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))

def comparer(base, snap, rap):
    if not base:
        return
    nouvelles = set(snap["ips"]) - set(base.get("ips", []))
    if nouvelles:
        rap.add("DIFF", "ip_changee", "faible", f"Nouvelles adresses : {sorted(nouvelles)} (CDN : souvent normal)")
    for p in sorted(set(snap.get("ports", [])) - set(base.get("ports", []))):
        rap.add("DIFF", "port_nouveau", "haute" if p in PORTS_RISQUES else "moyenne",
                f"Port {p} nouvellement ouvert depuis la baseline ({base.get('ts')})", {"port": p})
    if base.get("tls_fp") and snap.get("tls_fp") and base["tls_fp"] != snap["tls_fp"]:
        sev = "moyenne" if base.get("tls_issuer") != snap.get("tls_issuer") else "faible"
        rap.add("DIFF", "tls_change", sev, "Certificat différent de la baseline"
                + (f" (émetteur : {base.get('tls_issuer')} → {snap.get('tls_issuer')})" if sev == "moyenne" else " (renouvellement probable)"))
    if base.get("server") and snap.get("server") and base["server"] != snap["server"]:
        rap.add("DIFF", "server_change", "faible", f"En-tête Server : {base['server']} → {snap['server']}")

# ═════════════════════════════════════════════════════════════════════════════
# CHAMP Δ — uniquement à partir de MESURÉ, formule explicite
# ═════════════════════════════════════════════════════════════════════════════
def calculer_delta(rap):
    somme = sum(SEV_W[f["severite"]] for f in rap.mesures())
    return round(1 - math.exp(-somme / 0.25), 3)

def interpreter(d):
    return ("Silence structuré — rien à signaler" if d < 0.10 else
            "Frémissement — écarts mineurs" if d < 0.30 else
            "Tension active — glitches avérés" if d < 0.55 else
            "Zone de fracture — strates qui se mêlent" if d < 0.80 else
            "Verrou Trame — freeze ontologique")

# ═════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ═════════════════════════════════════════════════════════════════════════════
def afficher(rap, pur):
    section("ANOMALIES — observées sur l'Internet réel", C.BGREEN)
    ordre = {s: i for i, s in enumerate(SEV_ORDRE)}
    for f in sorted(rap.items, key=lambda f: (f["provenance"] != "MESURÉ", ordre[f["severite"]])):
        if f["severite"] == "info" and f["provenance"] == "MESURÉ" and f["code"] not in ("tcp_ouverts",):
            col, tag = C.DIM, "info"
        else:
            col, tag = SEV_COL[f["severite"]], f["severite"]
        prov = {"MESURÉ": C.BGREEN, "CALCULÉ": C.BYELLOW, "LORE": C.BMAGENTA}[f["provenance"]]
        print(f"  {col}{tag:<8}{C.RESET} {prov}[{f['provenance']}]{C.RESET} {C.DIM}{f['couche']:<9}{C.RESET} {f['message']}")
        if f["lecture_trame"] and not pur:
            print(f"           {C.ITALIC}{C.BMAGENTA}↳ {f['lecture_trame']}{C.RESET}")

def main():
    ap = argparse.ArgumentParser(description="ECHO-2026 v4.0 — Trame Réelle")
    ap.add_argument("--target", default="example.com", help="hôte, IP ou URL (défaut : example.com)")
    ap.add_argument("--authorized", action="store_true", help="active le sondage TCP (systèmes AUTORISÉS uniquement)")
    ap.add_argument("--ports", default=None, help="ports TCP, ex. 22,80,443 (défaut : liste courte)")
    ap.add_argument("--no-rss", action="store_true")
    ap.add_argument("--allow-private", action="store_true", help="autoriser cibles non publiques (labo)")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--baseline", default=".echo_baseline.json")
    ap.add_argument("--update-baseline", action="store_true", help="écraser la baseline avec ce scan")
    ap.add_argument("--seed", default=None, help="fige le nonce de la strate LORE (audit)")
    ap.add_argument("--pur", action="store_true", help="masque la strate Trame (lectures, spectre LORE)")
    ap.add_argument("--ma", action="store_true", help="pauses rituelles de 7s")
    args = ap.parse_args()

    banner("PROTOCOLE ECHO-2026 v4.0 — TRAME RÉELLE")
    try:
        tgt = parse_target(args.target)
    except ValueError as e:
        log(f"Cible refusée : {e}", "err")
        sys.exit(2)
    log(f"Cible : {tgt['host']}:{tgt['port']} ({'IP' if tgt['is_ip'] else 'nom'})", "ok")

    rap = Rapport()
    n = 4 + (0 if args.no_rss else 1)

    etape(1, n, "DNS")
    try:
        ips_check = resolve(tgt["host"], tgt["port"])
    except OSError as e:
        log(f"Résolution impossible : {e}", "err")
        sys.exit(2)
    if not args.allow_private and any(not ip_public(i) for i in ips_check):
        log(f"Cible non publique ({', '.join(map(str, ips_check))}) refusée — voir --allow-private (labo).", "err")
        sys.exit(2)
    ips = scan_dns(tgt, rap, args.allow_private)
    if args.ma:
        ma_pause()

    etape(2, n, "HTTP + TLS")
    res = probe_http(tgt, rap, args.allow_private, args.timeout)
    tls_snap, server = None, None
    if res:
        analyser_http(res, rap)
        tls_snap = analyser_tls(res, rap, tgt)
        server = res["headers"].get("server")

    etape(3, n, "TCP" + ("" if args.authorized else " (sauté — voir --authorized)"))
    ouverts = []
    if args.authorized and ips:
        ports = [int(p) for p in args.ports.split(",")] if args.ports else PORTS_DEFAUT
        log("Sondage actif : assurez-vous d'y être autorisé.", "warn")
        ouverts = scan_tcp(ips[0], ports, rap)
    else:
        rap.add("TCP", "tcp_saute", "info", "Sondage TCP non demandé")

    if not args.no_rss:
        etape(4, n, "RSS")
        scan_rss(rap)

    etape(n, n, "Baseline & résonances")
    key = f"{tgt['host']}:{tgt['port']}"
    snap = {"ts": datetime.now(timezone.utc).isoformat(), "ips": [str(i) for i in ips], "ports": ouverts,
            "tls_fp": (tls_snap or {}).get("fp"), "tls_issuer": (tls_snap or {}).get("issuer"), "server": server}
    base = charger_baseline(args.baseline, key)
    comparer(base, snap, rap)
    if base is None or args.update_baseline:
        sauver_baseline(args.baseline, key, snap)
        log(f"Baseline {'créée' if base is None else 'mise à jour'} pour {key}", "ok")
    resonances(rap)

    if not args.pur:  # strate fiction : étiquetée, hors Δ
        for ip in ips:
            for p in spectre_ip(ip, args.seed):
                rap.add("SPECTRE", "spectre_ip", "info",
                        f"Port fantôme {p['port']} « {p['nom']} » ({p['severite_lore']}) activation {p['activation']:.2f} — "
                        f"dérivé de l'arithmétique de {ip}, aucune observation",
                        p, prov="LORE")

    afficher(rap, args.pur)

    delta = calculer_delta(rap)
    section("SYNTHÈSE", C.BGREEN)
    mes = rap.mesures()
    cnt = Counter(f["severite"] for f in mes)
    print(f"  {C.BOLD}Δ = {delta:.3f}{C.RESET} — {interpreter(delta)}")
    print(f"  {C.DIM}Basé sur {len(mes)} observation(s) MESURÉE(S) : "
          + ", ".join(f"{cnt[s]} {s}" for s in SEV_ORDRE if cnt[s]) + f"{C.RESET}")
    print(f"  {C.DIM}Formule : Δ = 1 − exp(−Σ poids / 0.25) ; poids {SEV_W}. LORE et CALCULÉ pèsent 0.{C.RESET}")
    if not args.pur:
        pro = ("Le silence s'installe. La carte est tracée." if delta < 0.30 else
               "Les strates se superposent. Le Transept est proche.")
        print(f"\n  {C.ITALIC}{C.BYELLOW}PROPHÉTIE : {pro}{C.RESET}")

    safe = re.sub(r"[^\w.-]", "_", tgt["host"])
    out = Path(f"echo_2026_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    out.write_text(json.dumps({
        "version": "4.0", "timestamp": datetime.now(timezone.utc).isoformat(),
        "cible": tgt, "delta": delta, "delta_formule": "1-exp(-sum(poids_MESURÉ)/0.25)",
        "poids": SEV_W, "snapshot": snap, "findings": rap.items,
    }, indent=2, ensure_ascii=False, default=str))
    log(f"Rapport exporté : {out}", "ok")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.BRED}⚠ Rituel interrompu.{C.RESET}")
        sys.exit(1)
