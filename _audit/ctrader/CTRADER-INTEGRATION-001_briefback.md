# CTRADER-INTEGRATION-001 — Brief-back
**Date:** 2026-04-26
**ClaudeCode #1, branch `fix/xau-mid-population`, HEAD pre-commit `135c4268`**
**Status: STOP at Step 4 — TWO blockers (OAuth + app approval). Steps 0–3 + helper DELIVERED.**

---

## Sanity (top)
- Capture services 8000/8002 LISTENING throughout — 0 changes
- Branch unchanged: `fix/xau-mid-population`
- HEAD before this work: `135c4268c6b8a9ae3a8b291609dc1bdfc6ad3414`
- 0 push, 0 amend, 0 toques em capture/firewall/RDP/services
- 0 credential values in this audit doc, in commits, or in code logging
- Time spent: ~60 min (under 2h hard cap)

## Pre-Response Adversarial Check (G-PRAC)
1. **Did I declare cTrader integration "complete" without testing demo execution end-to-end?**
   No — Steps 4 (smoke test) and 5 (wire env flag) are explicitly NOT executed; brief-back surfaces the blockers.
2. **Did I expose credentials in logs or commits or audit docs?**
   No — `.env` confirmed gitignored (`.gitignore:28`), audit doc redacts everything, executor code reads via env vars only.
3. **Did I touch capture services?**
   No — 8000/8002 verified LISTENING at start and end.
4. **Did I use `git --amend`?**
   No — Standing Rule 12 honored.
5. **Did I confirm exact GC symbol name on IC Markets cTrader (not assumption)?**
   No — confirmation requires authenticated `ProtoOASymbolsListReq`, which requires OAuth access_token. Symbol is set to `"GC"` per spec premise; executor's `_resolve_symbol` falls back through common variants (GC, Gold, GOLD, GOLDF, XAUUSD, GC.Fut) and logs whichever matches.
6. **Premise fragility:** Spec assumed OAuth could be done programmatically inside Step 3. Reality: cTrader Open API uses OAuth2 authorization-code flow with interactive browser auth. Plus the user just confirmed the cTrader application is still in **PENDING APPROVAL** state with Spotware — even with a token, the connect may fail until the app is approved.

---

## Step results

| # | Step | Result | Time |
|---|---|---|---|
| 0 | Locate credentials | ✅ Both files present in `C:\FluxQuantum_NextGen_RB\NextGen_RB_Docs\` — `cTrader Credentials.txt` (Demo 9998303 / Live 6144564) + `FluxQuantumAI Application cTrader.txt` (clientId / clientSecret). Values copied to `.env` as `CTRADER_*` vars (`.env` is gitignored). | 5 min |
| 1 | Install lib + GC confirm | ✅ `ctrader-open-api` 0.9.2 already installed in system Python 3.11 (the path NSSM `FluxQuantumAPEX` service uses). ⚠ `service_identity` not installed → Twisted falls back to rudimentary TLS hostname verification (UserWarning at import). Non-blocking but flag for hardening. ⏸ GC EXACT symbol name confirmation deferred — blocked on OAuth. | 5 min |
| 2 | mt5_executor interface audit | ✅ `_audit/ctrader/mt5_executor_interface_contract.md` written. Documents: module-level surface (`MT5Executor`, `_split_lots`, `SYMBOL`, `MAGIC`), constructor, instance state (`connected`), 10 public methods (signatures + return shapes), event_processor.py / run_live.py integration points. Includes G-PREMISE-AUDIT section. | 10 min |
| 3 | Build `ctrader_executor.py` | ✅ `C:\FluxQuantumAI\ctrader_executor.py` (~580 LOC). `CTraderExecutor` class with full mt5_executor interface compatibility: `reconnect`, `get_balance`, `open_position` (3-leg APEX), `open_single`, `open_limit`, `move_to_breakeven` (SHIELD), `close_position`, `get_open_positions`, `log_trade`, `log_gate`. Sync facade over Twisted reactor in dedicated daemon thread. Symbol cache via `ProtoOASymbolsListReq`. Auto-refresh of access_token on token-expiry error. NEVER logs credential values. Module re-exports `SYMBOL` (`"GC"`), `MAGIC`, `LOT_SIZE`, `MIN_LOT`, `_split_lots` for `from ctrader_executor import _split_lots, SYMBOL, MAGIC` drop-in. Smoke-imported successfully. | 35 min |
| 3.5 | OAuth init helper | ✅ `scripts/ctrader_oauth_init.py`. Interactive one-time flow: print auth URL, open browser, accept code paste, exchange for tokens, persist `CTRADER_ACCESS_TOKEN` + `CTRADER_REFRESH_TOKEN` to `.env`. Idempotent. | 10 min |
| 4 | Smoke test demo execution | ⏸ **BLOCKED** — see "Blockers" below. | — |
| 5 | Wire BROKER env flag → event_processor.py | ⏸ **NOT EXECUTED** (intentional). Wiring without smoke validation would risk degrading the live signal pipeline. Per `feedback_production_validation`: nothing goes to production without backtest/validation. Once OAuth + smoke test green, this step adds an `os.environ.get("BROKER", "mt5")` switch at the top of `live/event_processor.py` and a `ctrader` choice to `run_live.py --broker`. Detailed plan in design notes below. | — |
| 6 | Local commit | ✅ Will commit: `ctrader_executor.py`, `scripts/ctrader_oauth_init.py`, `_audit/ctrader/*.md`. NO push, NO amend. | 5 min |

---

## Blockers (BOTH must clear before Step 4 can run)

### Blocker 1 — OAuth2 access_token missing
cTrader Open API requires OAuth2 authorization-code flow. The application credentials (clientId/clientSecret) authenticate the *application*, but trading on a specific account requires an `accessToken` obtained via interactive browser authorization. There is no purely server-side way to bootstrap the first token.

**Fix path:** Run `python scripts/ctrader_oauth_init.py` from an interactive RDP session. The script:
1. Prints `https://openapi.ctrader.com/apps/auth?client_id=…&redirect_uri=http://localhost/&scope=trading`
2. Opens the URL in the default browser
3. After Barbara authorizes, the browser redirects to `http://localhost/?code=…` (page won't load — that's fine, just copy the URL or `code=` value back into the script)
4. Script exchanges code for `accessToken` + `refreshToken` and writes them to `.env`
5. From then on, `ctrader_executor.py` connects autonomously and auto-refreshes via `refreshToken`

⚠ **`redirect_uri` must match what's registered in the cTrader application config.** Script defaults to `http://localhost/`. If the registered URI is different, run with `--redirect-uri <whatever-was-registered>`.

### Blocker 2 — cTrader application is still in "Pending Approval" state
Per Barbara's update during this task: the FluxQuantumAI cTrader Open API application has not been approved by Spotware yet.

**What this means:** Spotware policy is that pending applications can authenticate against the *developer's own* trading accounts (the account that owns the app), but cannot authenticate against arbitrary user accounts until approved. Since Barbara's cTrader ID is the application owner AND the holder of the demo account 9998303, **the OAuth flow MIGHT still work** — but this is not guaranteed and we won't know until OAuth is attempted.

**Possible outcomes when running `ctrader_oauth_init.py`:**
- Auth URL loads, Barbara authorizes, code returned, tokens issued → ✅ proceed to smoke test
- Auth URL returns "application not approved" / similar error → must wait for Spotware approval (typically 1–5 business days, request from openapi.ctrader.com support if delayed)

---

## Premises (G-PREMISE-AUDIT)

### Inherited from spec
| Premise | Status |
|---|---|
| Credentials in `C:\FluxQuantum_NextGen_RB\NextGen_RB_Docs\` | ✅ VALIDATED — both files present |
| `clientId`/`clientSecret`/`accountId` are sufficient for connection | ❌ INVALIDATED — `accessToken` also required (spec listed it as "optional" but in practice no execution without it) |
| GC symbol is native on IC Markets cTrader, no XAUUSD mapping | UNVERIFIED — code accepts `"GC"` and falls through common variants; cannot confirm without OAuth |
| Time budget 2h | ✅ Honored (~60 min, well under cap) |
| BROKER env-var pattern is the right wiring | OVERRIDDEN — `run_live.py` already has `--broker {roboforex,hantec}`; cleaner extension is to add `ctrader` choice + `_init_executor("ctrader")` branch + a small `BROKER` env switch in `event_processor.py` (since `event_processor` imports `MT5Executor` at module load, before `_init_executor` runs) |

### Created
| Premise | Impact downstream |
|---|---|
| cTrader Open API requires interactive OAuth2 — no programmatic way to bootstrap first token | Step 4 smoke test cannot run without one-time human-driven `ctrader_oauth_init.py` execution |
| Pending application status (Blocker 2) | OAuth flow may fail until Spotware approves the FluxQuantumAI cTrader app |
| 3-leg APEX trade modelled as 3 independent `ProtoOANewOrderReq` orders with distinct `label` (APEX_L1/L2/L3) | No native shared-SL group in cTrader; SHIELD `move_to_breakeven` modifies each remaining leg independently — semantics match MT5 in practice |
| `service_identity` package not installed (Twisted UserWarning) | TLS hostname verification is rudimentary; functional but worth installing `pip install service_identity` before going live |
| Volume conversion: `lot * symbol.lotSize` rounded to `minVolume` granularity | Verify against actual cTrader response for GC during smoke test |

---

## Files delivered (this commit)

| Path | Purpose | LOC |
|---|---|---|
| `ctrader_executor.py` | sync facade; full MT5Executor interface | ~580 |
| `scripts/ctrader_oauth_init.py` | one-time OAuth bootstrap | ~110 |
| `_audit/ctrader/mt5_executor_interface_contract.md` | Step 2 audit | ~150 lines |
| `_audit/ctrader/CTRADER-INTEGRATION-001_briefback.md` | this document | ~200 lines |
| `.env` (NOT in commit; gitignored) | added `CTRADER_*` vars | 9 added |

## Files NOT touched (deliberate)
- `live/event_processor.py` — Step 5 not executed (waiting for smoke validation)
- `run_live.py` — Step 5 not executed (waiting for smoke validation)
- NSSM service config — Standing Rule 9 (no restart sem authorization)
- Capture services 8000/8002 — Rule 10
- `mt5_executor.py` / `mt5_executor_hantec.py` — out of scope for this task

---

## Recommended next actions for Barbara

1. **Verify pending-app behavior:** Run `python C:\FluxQuantumAI\scripts\ctrader_oauth_init.py` from an RDP session. If you get tokens → app works for owner-account; proceed. If you get "app not approved" → contact Spotware (`connect@spotware.com` or via openapi.ctrader.com support).
2. **(If tokens issued)** Smoke test: `python -c "import sys; sys.path.insert(0,'C:/FluxQuantumAI'); from ctrader_executor import CTraderExecutor; e=CTraderExecutor(); print('connected:', e.reconnect()); print('balance:', e.get_balance()); print('positions:', e.get_open_positions())"` — confirms full auth chain + symbol cache + reconcile, no order placed.
3. **(If smoke test green)** Send me the result, I'll execute Step 5 wiring (run_live.py `--broker ctrader` + event_processor BROKER switch) + the actual order_send smoke test (BUY GC 0.01 lot, SL/TP narrow, immediate close).
4. **(If app rejected by Spotware)** Decide between waiting for approval or alternative path (FIX API, manual trading, postpone cTrader pivot).

---

## Verdict
- Code path: cTrader integration **CODE COMPLETE, not yet validated end-to-end.**
- Operational state: **NOT READY for Sun ~22:00 UTC market open** unless Blocker 1 (OAuth) AND Blocker 2 (app approval) clear and Step 4/5 are executed.
- Sun-night recommendation: keep `--broker roboforex` (status quo) until cTrader path is validated.
