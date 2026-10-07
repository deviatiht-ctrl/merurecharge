# Meru Auto Recharge

Sistèm rechaj otomatik: ou peye an HTG ak MonCash (via API PLOP PLOP), sistèm nan
voye USDC/USDT sou kont Meru ou otomatikman. Dashboard la montre konbyen chak
dola reyèlman koute w (pèt la).

## Enstalasyon ak lansman lokal

**Windows:** double-klik `run.bat` oswa:
```bat
run.bat
```
**Mac/Linux:**
```bash
chmod +x run.sh && ./run.sh
```

Script la kreye `.venv`, enstale depandans yo, kopye `.env.example` → `.env`,
epi lanse sou http://localhost:8000 (navigatè a louvri pou kont li sou Windows).

Nan premye lansman an, ou kreye modpas la. Apre sa ou konekte.

## Tès

```bash
pytest tests/ -v          # 36 tès (calc, flux konplè, sekirite)
python scripts/selftest.py  # flux konplè san navigatè (mock)
```

### Tès manyèl rekòmande (mock)
1. `run.bat` → louvri http://localhost:8000 → kreye modpas
2. Paj **Float** → "Ranpli float": 13000 HTG → 100 USDC
3. Paj **Nouvo rechaj** → 500 HTG, MonCash USSD, telefòn `50937000000` → "Lanse rechaj"
4. Gade timeline lan; klike "Akselere peman" → rive "Konfime" ak tx_hash
5. Refè yon lòd, klike "Simile echèk" → estati "Peman echwe", pa gen voye
6. **Paramèt** → "Sispann voye otomatik" → lòd rete "Peye" san voye

## Deployman sou Vercel

Frontend la se `public/` (Vercel sèvi l nativ sou CDN), backend la se
`app/main.py` (Vercel detekte FastAPI la otomatikman — pa gen api/index.py).

### 1. Baz done (OBLIGATWA)
SQLite fichye a **pa pèsistan** sou serverless — chak fonksyon gen disk tanporè.
Ou dwe itilize yon baz done ekstèn:

**Supabase (rekòmande — Postgres hosted, gratis):**
1. Kreye yon pwojè sou supabase.com
2. Dashboard → **Connect** → kopye chain **"Transaction pooler"** la (port 6543
   — enpòtan pou serverless):
```
DATABASE_URL=postgresql://postgres.<ref>:<modpas-baz-done>@aws-0-<rejyonal>.pooler.supabase.com:6543/postgres?sslmode=require
```
   `<modpas-baz-done>` = modpas baz done a (sa ou mete lè w kreye pwojè a;
   ou ka reyiniysalize l nan Project Settings → Database).
   **Atansyon**: URL `https://<ref>.supabase.co/rest/v1` ak kle `anon`/`service_role`
   yo se pou API PostgREST la — backend la PA itilize yo. Li bezwen URL pooler la.
   `init_db` aktive RLS sou tout tablo otomatikman, donk kle anon a pa ka li
   done ou yo menm si li pibliye.
3. Tablo yo kreye otomatikman nan premye request la — pa gen migrasyon manyèl.

**Turso (konpatib SQLite):**
```bash
turso db create meru-auto
turso db show meru-auto --url        # libsql://xxx.turso.io
turso db tokens create meru-auto     # token
```
```
DATABASE_URL=sqlite+libsql://xxx.turso.io?authToken=<token>&secure=true
```

### 2. Varyab anviwònman sou Vercel
```
PLOP_CLIENT_ID=...
PLOP_CLIENT_SECRET=...
SESSION_SECRET=<jenere yon chàn long, ex: openssl rand -hex 32>
CRON_SECRET=<yon lòt chàn sekrè>
DATABASE_URL=<Supabase pooler URL — gade pi wo>
MOCK_MODE=true        # kite true pandan ou teste
# An MOD REYÈL sèlman:
WALLET_SECRET=...
MERU_ADDRESS=...
MERU_MEMO=...
```

### 3. Deplwaye
```bash
npm i -g vercel
vercel --prod
```
Vercel detekte `app/main.py` (FastAPI preset). Fichye `vercel.json` la mete
`maxDuration: 60` ak yon cron nan `/api/cron/tick`.

### 4. Webhook
Webhooks yo mache dirèkteman — pa bezwen tinèl:
```
URL webhook: https://<app-ou-a>.vercel.app/api/webhook/plop
```
Mete URL sa a nan kont marchan PLOP ou a (webhook_url). Siyati a verifye ak
`X-Webhook-Signature` (HMAC-SHA256 sou kò brit la ak `client_secret`).

**Pou teste webhook la lokalman**, espoze localhost:
```bash
cloudflared tunnel --url http://localhost:8000
# oswa: ngrok http 8000
```
epi mete URL piblik la nan kont marchan an.

### 5. Serverless — sa ki chanje
- Pa gen worker thread: polling la fèt lè frontend la konsilte yon lòd aktif
  (chak 4 s), pa webhook la, ak pa cron (limit: sou plan Hobby, cron mache yon
  fwa pa jou — sou Pro li ka chak minit; ou ka ogmante frekans lan nan
  `vercel.json`)
- SSE `/api/events` ka fèmen apre 50 s — frontend la gen fallback polling
- Chak fonksyon gen `maxDuration: 60 s`

## Mòd similasyon (default)

`MOCK_MODE=true` — default ak RECOMANMANSAN. Peman yo simile (pase "ok" apre
`MOCK_AUTO_OK_S` segonn), wallet la simile, pa gen lajan reyèl. Bannyè jòn an
montre "MOD SIMILASYON".

## Pase an MOD REYÈL

1. Ranpli `.env` (lokal) oswa env vars Vercel: `PLOP_CLIENT_ID`,
   `PLOP_CLIENT_SECRET`, `WALLET_SECRET`, `MERU_ADDRESS`, `MERU_MEMO`
2. **Paramèt → Adrès Meru**: anrejistre adrès la + memo, verifye nan app Meru a
   ki aset/rezo li aksepte (Meru se Stellar/USDC), koche checkbox la
3. **Paramèt → Mòd**: "Pase an MOD REYÈL" — tès pre-vòl la tcheke:
   - `auth/marchand` PLOP reyisi (kredansyèl valid)
   - wallet float konekte ak solde > 0
   - adrès Meru anrejistre + checkbox koche
   - SESSION_SECRET konfigire
   Si yon chèk echwe, mòd reyèl la rete bloke.
4. Opsyonèl: "test-send" voye 1 USDT sou Meru anvan premye rechaj reyèl la.

## Sekirite

- Wallet float la: **dedye, sèlman sa w dakò riske** (WALLET_SECRET nan env
  sèlman, pa janm nan baz done/log/frontend)
- Adrès Meru whiteliste nan Paramèt — pa janm soti nan request lòd;
  chanjman mande modpas + delè 10 minit + alèt
- Idempotans: transisyon `paid→sending` atomik (`UPDATE ... WHERE status='paid'`),
  `tx_hash` inik; rekòmansman pandan "sending" tcheke on-chain anvan reeseye
- Webhook sèl pa janm voye — toujou verifye pa `paiement-verify`
- Sesyon HttpOnly + CSRF (header X-CSRF-Token) + rate limit login (5/15 min)
- Kill switch: bloke tout voye imedyatman
- Jounal odit imuable; log yo pa janm gen sekrè
- Lokalman, backend la mare sou `127.0.0.1` sèlman

## Limit koni

- Sou Vercel Hobby, cron la mache max 1 fwa/jou — webhook la + polling la
  kouvri tout (verify fèt on-demand pandan ou gade yon lòd aktif).
- SSE ka dekoupe sou serverless — fallback polling otomatik.
- `find_outgoing` (repriz apre krach) sipòte sou Stellar sèlman; sou TRC-20
  yon lòd "sending" san hash ap reeseye apre 3 tantativ.
- Tès yo pa t ka verifye API PLOP reyèl la ni tranzaksyon on-chain reyèl —
  sa mande kle reyèl.
