<div align="center">

# ✦ ECHO-2026 ✦

### Trame Réelle

**Un scanner d'anomalies qui regarde l'Internet tel qu'il est, et le lit tel que la Trame le rêve.**

![Python](https://img.shields.io/badge/python-3.9%2B-00fff7?style=flat-square&labelColor=0a0a12)
![Dépendances](https://img.shields.io/badge/core-stdlib%20only-5cff7a?style=flat-square&labelColor=0a0a12)
![Provenance](https://img.shields.io/badge/provenance-MESURÉ%20·%20CALCULÉ%20·%20LORE-ff60a0?style=flat-square&labelColor=0a0a12)
![Statut](https://img.shields.io/badge/strate-2026-ffb45c?style=flat-square&labelColor=0a0a12)

<sub>« La même IP ne produit jamais le même écho — elle produit le même spectre.<br>L'écho, lui, dépend de qui écoute, et de quand. » — *Codex Vauvillensis, Fr. XII*</sub>

</div>

---

## Le principe

ECHO-2026 effectue de **vraies observations** sur une cible (DNS, HTTP, TLS, TCP, flux RSS), repère ce qui cloche, puis superpose à chaque anomalie sa **lecture Trame** : une phrase issue du lore du projet.

```text
  haute    [MESURÉ] HTTP   Last-Modified dans le futur : 2029-06-06T12:00:00+00:00
           ↳ Un fichier se souvient d'un jour qui n'a pas eu lieu : écho rétrocausal.
```

> **La frontière est abolie à l'affichage, jamais dans les données.**
> Fiction et réalité cohabitent sur la même ligne, mais chaque entrée du rapport JSON porte une étiquette de provenance, et le score **Δ** ne compte que ce qui a réellement été observé.

| Provenance | Signification | Poids dans Δ |
|:--|:--|:-:|
| 🟢 **MESURÉ** | Observé sur le réseau (en-tête, certificat, port, date…) | selon sévérité |
| 🟡 **CALCULÉ** | Dérivé de mesures (résonances avec nombres et dates-signatures, lexique) | 0 |
| 🟣 **LORE** | Fiction pure (« ports fantômes » calculés depuis l'arithmétique d'une IP) | 0 |

---

## Le dépôt

```text
.
├── echo_2026_v4.py     # le scanner : observe, détecte, annote, exporte un JSON
├── echo_2026_viz.py    # la cartographie : JSON → carte UMAP (PNG) + dashboard HTML
└── README.md
```

Les deux scripts forment une chaîne : **`echo_2026_v4.py` produit les JSON, `echo_2026_viz.py` les consomme.**

---

## Installation

```bash
git clone https://github.com/<votre-compte>/echo-2026.git
cd echo-2026

# Scanner : aucune dépendance obligatoire (bibliothèque standard)
pip install feedparser                                   # optionnel : couche RSS

# Visualisation (toutes optionnelles, dégradation gracieuse)
pip install scikit-learn matplotlib umap-learn plotly
```

Python **3.9+** recommandé.

---

## `echo_2026_v4.py` — le scanner

### Couches d'observation

| Couche | Ce qui est mesuré | Anomalies détectées |
|:--|:--|:--|
| **DNS** | Résolution A/AAAA | Nom public résolvant vers une adresse non publique (indice de *DNS rebinding*) |
| **HTTP** | `HEAD`, redirections suivies et validées **saut par saut** | `Last-Modified` futur, antérieur à 1995 ou postérieur à `Date` ; dérive d'horloge ; redirection vers l'intérieur ; downgrade HTTPS → HTTP ; version exposée (`Server`, `X-Powered-By`) ; HSTS absent |
| **TLS** | Chaîne, protocole, empreinte SHA-256 | Vérification échouée, certificat expiré, pas encore valide, expiration imminente, protocole obsolète, validité supérieure au plafond **CA/B SC-081v3** (398 j, puis **200 j** pour les certificats émis depuis le 15 mars 2026) |
| **TCP** | Connexion sur une courte liste de ports | Ports ouverts, ports à risque exposés (21, 23, 445, 3306, 3389, 5432, 6379, 9200, 27017) |
| **RSS** | Flux d'actualité configurés | Articles datés du futur, flux illisibles ou vides |
| **DIFF** | Comparaison à la baseline | Nouvelle IP, nouveau port, certificat ou émetteur changé, en-tête `Server` modifié |

> Le sondage **TCP est désactivé par défaut** et exige `--authorized`.

### Usage

```bash
# Scan passif d'une cible publique
python echo_2026_v4.py --target example.com

# Cible sous forme d'URL ou d'IP
python echo_2026_v4.py --target https://example.com/
python echo_2026_v4.py --target 203.0.113.7

# Ajouter le sondage TCP (systèmes qui vous appartiennent uniquement)
python echo_2026_v4.py --target mon-serveur.tld --authorized --ports 22,80,443,8080

# Figer la baseline différentielle de référence
python echo_2026_v4.py --target example.com --update-baseline

# Sans aucune couche Trame (rapport technique pur)
python echo_2026_v4.py --target example.com --pur

# Laboratoire local
python echo_2026_v4.py --target http://127.0.0.1:8080 --allow-private
```

### Options

| Option | Rôle |
|:--|:--|
| `--target` | Hôte, IP ou URL (défaut : `example.com`) |
| `--authorized` | Active le sondage TCP actif |
| `--ports` | Liste de ports séparés par des virgules |
| `--no-rss` | Désactive la couche RSS |
| `--allow-private` | Autorise les cibles non publiques (labo) |
| `--timeout` | Délai réseau en secondes (défaut : 8) |
| `--baseline` | Fichier de baseline (défaut : `.echo_baseline.json`) |
| `--update-baseline` | Écrase la baseline avec le scan courant |
| `--seed` | Fige le nonce de la strate LORE (audit reproductible) |
| `--pur` | Masque lectures Trame et spectre LORE |
| `--ma` | Pauses rituelles de 7 s entre les étapes |

### Le champ Δ

Δ résume l'état d'une cible à partir des seules observations **MESURÉES** :

```text
Δ = 1 − exp( −Σ poids / 0.25 )
```

| Sévérité | info | faible | moyenne | haute | critique |
|:--|:-:|:-:|:-:|:-:|:-:|
| **Poids** | 0 | 0.02 | 0.06 | 0.12 | 0.20 |

| Δ | Lecture |
|:-:|:--|
| `< 0.10` | Silence structuré — rien à signaler |
| `< 0.30` | Frémissement — écarts mineurs |
| `< 0.55` | Tension active — glitches avérés |
| `< 0.80` | Zone de fracture — strates qui se mêlent |
| `≥ 0.80` | Verrou Trame — freeze ontologique |

Un scan sans anomalie donne **Δ = 0**. Le score ne s'élève que sur des faits.

### Baseline différentielle

La première exécution contre une cible crée sa baseline (`.echo_baseline.json`). Les scans suivants sont comparés à cet état de référence. Elle n'est **jamais écrasée sans `--update-baseline`**, pour que la dérive reste visible dans le temps.

### Sortie

Chaque scan écrit `echo_2026_<hôte>_<date>.json` :

```jsonc
{
  "version": "4.0",
  "delta": 0.781,
  "delta_formule": "1-exp(-sum(poids_MESURÉ)/0.25)",
  "findings": [
    {
      "couche": "HTTP",
      "code": "lm_futur",
      "severite": "haute",
      "provenance": "MESURÉ",
      "message": "Last-Modified dans le futur : 2029-06-06T12:00:00+00:00",
      "evidence": { "last_modified": "Wed, 06 Jun 2029 12:00:00 GMT" },
      "lecture_trame": "Un fichier se souvient d'un jour qui n'a pas eu lieu : écho rétrocausal."
    }
  ]
}
```

Chaque anomalie embarque son **`evidence`** brute : la valeur observée, vérifiable à la main.

---

## `echo_2026_viz.py` — la cartographie

Transforme un ou plusieurs rapports JSON en objets visuels.

| Sortie | Contenu |
|:--|:--|
| `carte_mousse_quantique.png` | Carte 2D des anomalies, colorées par provenance |
| `rapport_trame.html` | Dashboard interactif : KPI, évolution du Δ, carte, anomalies par couche, répartition par sévérité, tableau détaillé |

Les textes des anomalies (message + lecture Trame) sont vectorisés en TF-IDF, puis projetés en 2D : les anomalies de même nature se regroupent en grappes.

**Dégradation gracieuse** : `UMAP → t-SNE → PCA → abandon`. Sans Plotly, un rapport HTML minimaliste est produit.

### Usage

```bash
# Un ou plusieurs fichiers
python echo_2026_viz.py --input echo_2026_example.com_20261108_1532.json

# Un dossier entier (cherche echo_2026_*.json)
python echo_2026_viz.py --input ./scans/

# HTML 100 % hors ligne (plotly.js embarqué)
python echo_2026_viz.py --input ./scans/ --offline

# Sans les findings LORE
python echo_2026_viz.py --input ./scans/ --pur
```

| Option | Rôle |
|:--|:--|
| `--input` | Fichier(s) JSON ou dossier(s) *(requis)* |
| `--offline` | Embarque plotly.js (≈ 3 Mo de plus, aucun CDN) |
| `--pur` | Retire les findings `LORE` avant analyse |

> La réduction de dimension demande **au moins 3 anomalies**. Pour une carte parlante, accumulez plusieurs scans dans un même dossier.

---

## Chaîne complète

```bash
# 1. Observer, à intervalles réguliers
python echo_2026_v4.py --target example.com
python echo_2026_v4.py --target autre-site.org

# 2. Cartographier
python echo_2026_viz.py --input . --offline

# 3. Ouvrir
xdg-open rapport_trame.html        # macOS : open · Windows : start
```

---

## Éthique et usage responsable

- Le scanner est **non intrusif par défaut** : résolution DNS, une requête `HEAD` par saut, une poignée TLS.
- Le sondage TCP (`--authorized`) ne doit viser que des systèmes **dont vous êtes propriétaire ou pour lesquels vous disposez d'une autorisation écrite**. Scanner sans accord peut être illégal.
- Les cibles privées, loopback, link-local et plus généralement non globales sont **refusées** sauf `--allow-private`, y compris lorsqu'un nom les atteint par résolution DNS ou par redirection.
- Le User-Agent annonce l'outil : ECHO-2026 ne cherche pas à se dissimuler.
- Les anomalies signalées sont des **indices à vérifier**, pas des preuves de compromission. Une dérive d'horloge peut venir de *votre* machine ; une IP nouvelle est souvent un simple CDN.

---

## Limites connues

- **Fenêtre TOCTOU** : la validation DNS et la connexion sont deux étapes distinctes. Acceptable pour un audit ponctuel, pas pour un service exposé aux entrées d'inconnus.
- **Règle des 200 jours** : elle concerne les certificats publics. Un certificat d'une autorité privée de confiance locale peut légitimement la dépasser.
- **Sondage TCP** : IPv4/IPv6 en *connect()*, sur la première adresse résolue seulement ; pas de détection de service ni de bannière.
- **Lexique Trame** : sur des flux d'actualité réels, les correspondances sont du bruit statistique. C'est pourquoi elles sont étiquetées `CALCULÉ` et ne pèsent rien dans Δ.
- **Visualisation** : le TF-IDF utilise des mots vides anglais alors que les messages sont en français ; la carte reste lisible mais gagnerait à une liste adaptée.
- **Rapport HTML** : le contenu externe (titres RSS, en-têtes serveur) y est inséré tel quel. N'ouvrez que des rapports issus de scans que vous avez vous-même lancés.

---

## Licence

*À choisir avant publication* (MIT, Apache-2.0, GPL-3.0…) — ajoutez un fichier `LICENSE` à la racine.

<div align="center">
<sub>✦ Δ — la Trame se lit mieux quand on l'a d'abord mesurée ✦</sub>
</div>
