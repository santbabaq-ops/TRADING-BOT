# Polymarket RBI Bot — Documentation projet

## Vue d'ensemble

Projet de trading automatise complet pour Polymarket, construit de zero avec Python et FastAPI.
4 bots independants, 4 strategies (dont copy-trading), interface web, backtesting, gestion des risques, alertes Telegram/Email, deploiement Docker.

---

## Structure du projet

```
TRADING BOT/
├── api/                    # Serveur FastAPI + orchestrateur de bots
│   ├── server.py           # Routes API REST (port 1818) + lifespan shutdown
│   └── bot_manager.py      # Gestion des 4 bots en threads background
├── bot/                    # Logique d'execution
│   ├── trader.py           # Boucle principale : signal → risk → execute
│   ├── risk_manager.py     # Limites de position, perte journaliere, stop-loss
│   ├── order_manager.py    # Gestion des limit orders + anti-doublons
│   └── position_tracker.py # Suivi des positions et PnL (calcul documente)
├── strategies/             # Les 4 strategies de trading
│   ├── base_strategy.py    # Classe abstraite (BUY/SELL/HOLD)
│   ├── macd_strategy.py    # MACD Histogram (3/15/3) — momentum
│   ├── rsi_mean_reversion.py # RSI(14) + VWAP — mean reversion
│   ├── cvd_strategy.py     # Cumulative Volume Delta — divergence
│   └── copytrade_strategy.py # Copy-trading — suivre les meilleurs wallets
├── backtesting/            # Moteur de test historique
│   ├── engine.py           # Simulateur avec stop-loss intra-bougie (high/low)
│   ├── metrics.py          # Win rate, Sharpe, drawdown, profit factor
│   └── runner.py           # Execution parallele multi-strategies
├── data/                   # Acces aux donnees
│   ├── downloader.py       # Telechargement OHLCV via ccxt (Binance)
│   ├── polymarket_client.py # Client CLOB API Polymarket (limit orders)
│   ├── wallet_scanner.py   # Scanner de wallets Polymarket (API Gamma)
│   └── storage.py          # SQLite thread-safe (singleton + lock) + CSV
├── incubation/             # Monitoring, scaling et alertes
│   ├── monitor.py          # Dashboard console temps reel
│   ├── scaler.py           # Scaling $1 → $100 avec level-up ET level-down
│   ├── alerter.py          # Alertes Telegram + Email (SMTP Gmail)
│   └── logger.py           # Logs structures JSONL + fichiers
├── dashboard/              # Interface web
│   ├── index.html          # Dashboard principal (theme gris clair)
│   ├── audit.html          # Rapport d'audit de code (15 points)
│   ├── docs.html           # Documentation complete du projet
│   ├── guide.html          # Guide utilisateur (simulation + live)
│   └── i18n.json           # Textes et tooltips en francais (externalises)
├── deploy/                 # Scripts de lancement
│   ├── run_backtest.py     # Lancer le backtest des 3 strategies techniques
│   ├── run_bot.py          # Lancer un bot en ligne de commande (live data)
│   └── run_monitor.py      # Lancer le monitoring console
├── config/                 # Configuration
│   ├── settings.py         # Settings dataclass thread-safe + constantes
│   └── accounts.py         # Multi-comptes Polymarket
├── tests/                  # Tests unitaires + integration
│   ├── test_strategies.py  # Tests des 3 strategies techniques
│   ├── test_copytrade.py   # Tests copy-trading (scoring, signaux, cache)
│   ├── test_backtesting.py # Tests du moteur de backtest
│   ├── test_risk_manager.py # Tests du risk manager
│   ├── test_api_integration.py # Tests integration API FastAPI (16 tests)
│   └── test_storage.py     # Tests SQLite concurrent (3 tests)
├── nginx/                  # Configuration reverse proxy
│   └── trading.conf        # Config nginx pour trading.youpiare.fr
├── scripts/                # Scripts de deploiement
│   └── deploy.sh           # Deploy automatise sur VPS OVH
├── Dockerfile              # Image Python 3.12 pour Docker
├── docker-compose.yml      # Container + volumes + healthcheck
├── .dockerignore           # Fichiers exclus de l'image Docker
├── start.bat               # Lanceur Windows (double-clic)
├── requirements.txt        # Dependances Python
├── .env.example            # Template des variables d'environnement
├── .env                    # Cles privees (non versionne)
└── .gitignore              # Fichiers exclus du versionning
```

---

## Ce qui a ete construit

### 1. Quatre strategies de trading

| Strategie | Type | Signal d'entree | Source de donnees |
|-----------|------|-----------------|-------------------|
| **MACD (3/15/3)** | Momentum / Trend | Croisement MACD au-dessus du signal | Binance (ccxt) |
| **RSI + VWAP** | Mean Reversion | RSI < 30 + prix sous VWAP | Binance (ccxt) |
| **CVD Divergence** | Volume Delta | Divergence prix/volume + qualite approx | Binance (ccxt) |
| **Copy Trading** | Social Trading | Copie les positions des meilleurs wallets | API Gamma Polymarket |

### 2. Copy-Trading (strategie #4)

- **Scanner de wallets** : decouvre les traders actifs via l'API Gamma Polymarket
- **Scoring** : win rate, profit factor, score composite (60% win rate + 40% profit factor)
- **Filtrage** : top N wallets avec min 20 trades et win rate >= 55%
- **Cache** : rescoring toutes les heures (configurable)
- **Signaux** : detecte les nouvelles positions des top wallets et les copie
- **Token ID dynamique** : le bot s'adapte au marche que les top wallets tradent
- **Confidence** : nb wallets d'accord / total × score du meilleur wallet

### 3. Moteur de backtesting

- Telecharge les donnees historiques via ccxt (Binance)
- Execute les strategies sur les donnees avec stop-loss et take-profit
- **Stop-loss intra-bougie** : verifie sur high/low (pas seulement close) — approche conservatrice
- Calcule : win rate, profit factor, max drawdown, Sharpe ratio (par trade, non annualise)
- Execution parallele multi-strategies
- Commande : `python deploy/run_backtest.py`

### 4. Systeme de trading live

- **Trader** : boucle signal → verification risque → execution ordre
  - Mode live : `data_fetcher` callable pour donnees temps reel
  - Mode replay : DataFrame statique pour dev/backtest
  - Mode copy-trade : scan wallets → detection signaux → execution
  - Callback `on_trade` pour reporter les events au BotManager
- **Risk Manager** : limite de taille, positions max, perte journaliere max, stop-loss/take-profit
- **Order Manager** : limit orders uniquement (0 frais Polymarket), anti-doublons
- **Position Tracker** : suivi positions ouvertes, PnL realise/non realise
- Mode **DRY_RUN** par defaut (aucun ordre reel)

### 5. Alertes (Telegram + Email)

- **Telegram** : via Bot API (gratuit, illimite)
- **Email** : via SMTP Gmail (mot de passe d'application)
- Les 2 canaux fonctionnent en parallele
- Anti-spam avec cooldown par type d'alerte

| Evenement | Seuil | Cooldown |
|-----------|-------|----------|
| Perte sur un trade | > 5$ | 15 min |
| Gain sur un trade | > 10$ | 15 min |
| Perte journaliere | > 20$ | 1h |
| Gain journalier | > 50$ | 1h |
| Level up / down | Toujours | Immediat |
| Erreur bot / Kill all | Toujours | Immediat |

### 6. Incubation et scaling

- Echelle progressive : $1 → $5 → $10 → $50 → $100
- Conditions pour monter de niveau : 20 trades min, win rate > 55%, profit factor > 1.3
- **Level-down automatique** : win rate < 40% ou 5 pertes consecutives → retour au niveau inferieur
- Monitoring continu avec logs structures (JSONL)

### 7. API REST (FastAPI)

| Methode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/bots` | Etat des 4 bots |
| POST | `/api/bots/{key}/start?token_id=...` | Demarrer un bot (token_id optionnel) |
| POST | `/api/bots/{key}/stop` | Arreter un bot |
| POST | `/api/bots/kill-all` | Arret d'urgence |
| GET | `/api/metrics` | Metriques globales |
| GET | `/api/trades?limit=50` | Journal des trades |
| GET | `/api/risk` | Etat du risk manager |
| GET | `/api/settings` | Parametres actuels |
| PUT | `/api/settings` | Modifier les parametres (valide par Pydantic) |
| GET | `/api/alerts/status` | Etat des alertes |
| POST | `/api/alerts/test` | Envoyer un message test |

- **Validation** : `position_size` (0-1000$), `stop_loss_pct` (0-100%), `take_profit_pct` (0-100%)
- **CORS** : restreint a localhost:1818 par defaut (configurable via `CORS_ORIGINS`)
- **Graceful shutdown** : lifespan FastAPI → kill_all() + close_db()
- Documentation Swagger : http://localhost:1818/docs

### 8. Dashboard web

- **4 cartes bot** avec toggle start/stop, sparklines, taux de reussite, PnL
- **4 metriques cles** : PnL total, meilleure strategie, total trades, Sharpe ratio
- **Courbe de capital** (equity curve) par strategie
- **Taux de reussite** comparatif (bar chart)
- **Gestion des risques** : barre de perte journaliere, positions ouvertes, bouton arret d'urgence
- **Journal des trades** triable (50 derniers trades)
- **Parametres** : taille de position, stop-loss, take-profit, mode simulation, compte
- Theme gris clair, responsive
- Tooltips explicatifs sur tous les elements
- Textes externalises dans `i18n.json`
- Liens Guide, Docs, Audit et API

### 9. Documentation

- **docs.html** : documentation complete
- **audit.html** : rapport d'audit de code (15 points)
- **guide.html** : guide utilisateur (simulation + live, 15 etapes)
- Accessible depuis le dashboard

### 10. Deploiement

- **Local** : `start.bat` (double-clic) ou Docker (`docker compose up -d`)
- **Docker** : Dockerfile + docker-compose.yml, port 8818, volumes persistants, healthcheck
- **OVH VPS** : script `deploy.sh`, reverse proxy nginx, SSL Let's Encrypt
- **Sous-domaine** : `trading.youpiare.fr` (a configurer dans DNS OVH)

### 11. Tests

- **47 tests** au total
- Tests unitaires : 3 strategies techniques, backtest, risk manager
- Tests copy-trading : scoring wallets, signaux, cache, deduplication (11 tests)
- Tests integration : API FastAPI (16 tests), SQLite concurrent (3 tests)
- Commande : `python -m pytest tests/ -v`

---

## Architecture technique

### Configuration thread-safe

Les parametres mutables sont encapsules dans une dataclass `Settings` avec `threading.Lock`.
Les constantes (parametres de strategies, endpoints) restent en module-level.
Chaque bot recoit sa propre copie de `dry_run` a l'instanciation — pas de mutation globale.

### SQLite thread-safe

Connexion singleton avec `check_same_thread=False` et `threading.Lock` pour serialiser les ecritures.
`close_db()` appele au shutdown.

### Copy-trading

La strategie copy-trading utilise l'**adapter pattern** : elle implemente `BaseStrategy` mais en interne delegue a un `WalletScanner` qui fetch l'API Gamma Polymarket. Le `token_id` est dynamique — il change selon le marche que les top wallets tradent.

---

## Configuration

### Variables d'environnement (.env)

| Variable | Description | Defaut |
|----------|-------------|--------|
| `POLYMARKET_PRIVATE_KEY` | Cle privee du wallet Polygon | — |
| `POLYMARKET_FUNDER_ADDRESS` | Adresse du wallet | — |
| `POLYMARKET_TOKEN_ID` | Token ID du marche a trader (vide = demo) | — |
| `MAX_POSITION_SIZE` | Taille max par position ($) | 10 |
| `MAX_DAILY_LOSS` | Perte journaliere max ($) | 50 |
| `MAX_OPEN_POSITIONS` | Nombre max de positions ouvertes | 3 |
| `DRY_RUN` | Mode simulation | true |
| `CORS_ORIGINS` | Origines CORS autorisees | localhost:1818 |
| `LOG_LEVEL` | Niveau de log | INFO |
| `TELEGRAM_BOT_TOKEN` | Token du bot Telegram | — |
| `TELEGRAM_CHAT_ID` | Chat ID Telegram | — |
| `SMTP_USER` | Email Gmail pour alertes | — |
| `SMTP_PASSWORD` | Mot de passe d'application Gmail | — |
| `ALERT_EMAIL_TO` | Destinataire des alertes | SMTP_USER |
| `ALERT_LOSS_THRESHOLD` | Seuil perte par trade ($) | 5 |
| `ALERT_GAIN_THRESHOLD` | Seuil gain par trade ($) | 10 |
| `ALERT_DAILY_LOSS_THRESHOLD` | Seuil perte journaliere ($) | 20 |
| `ALERT_DAILY_GAIN_THRESHOLD` | Seuil gain journalier ($) | 50 |
| `COPYTRADE_MIN_TRADES` | Min trades pour scorer un wallet | 20 |
| `COPYTRADE_MIN_WIN_RATE` | Win rate minimum pour top wallets | 0.55 |
| `COPYTRADE_TOP_N` | Nombre de wallets a suivre | 6 |
| `COPYTRADE_SCAN_INTERVAL` | Interval de scan (secondes) | 60 |
| `COPYTRADE_RESCORE_INTERVAL` | Interval de rescoring (secondes) | 3600 |

---

## Dependances

- py-clob-client (API Polymarket)
- pandas, numpy (calculs)
- ta (indicateurs techniques)
- ccxt (donnees de marche Binance)
- requests (API Gamma Polymarket)
- fastapi, uvicorn (serveur web)
- python-dotenv (configuration)
- Chart.js (graphiques dashboard — via CDN)

---

## Lancement

```bash
# Methode 1 — Windows (double-clic)
start.bat
# → http://localhost:1818

# Methode 2 — Docker
docker compose up -d --build
# → http://localhost:8818

# Methode 3 — Manuel
cd "C:\DEV POWERSHELL\__Q17\TRADING BOT"
.venv\Scripts\activate
python api/server.py
# → http://localhost:1818
```

---

## Securite

- Mode DRY_RUN active par defaut
- Cles privees dans .env (jamais versionne)
- CORS restreint a localhost (configurable)
- Validation Pydantic sur tous les parametres modifiables
- Limit orders uniquement (0 frais)
- Risk manager bloque les trades hors limites
- Scaler level-down protege le capital
- Graceful shutdown sauvegarde l'etat
- Alertes Telegram/Email sur pertes et erreurs
- Bouton arret d'urgence sur le dashboard
