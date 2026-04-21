# APEX_GC_Iceberg_V2 — Documentação Técnica
**Módulo:** `ml_iceberg_v2`  
**Versão:** 2.0.0  
**Instrumento:** GC (Gold Futures, XCEC/CME)  
**Última actualização:** 2026-04-11  

---

## 1. Visão Geral

O `ml_iceberg_v2` é o módulo de machine learning para detecção de ordens iceberg em GC. Substitui o `ats_iceberg_v1` com uma arquitectura de 3 stages, features calibradas para o mercado real e pipeline de treino reproduzível via AWS SageMaker.

### Problema que resolve
Detectar ordens iceberg passivas no Level-2 do GC (CME/XCEC) a partir de padrões de refill de liquidez, classificando-as em 4 classes: NOISE, ICEBERG_NATIVE, ICEBERG_SYNTHETIC, ABSORPTION.

### Referências académicas
- Zotikov & Antonov (2019), CME/DXFeed: "Iceberg Order Detection in Limit Order Books" — algoritmo de detecção de refills + Tranche Trees + Kaplan-Meier
- Cont, Kukanov & Rachev (2014): "The Price Impact of Order Book Events" — F21 Order Flow Imbalance
- Christensen (2013): underflow volume detection — F19

---

## 2. Arquitectura do Pipeline

```
L2 Data (S3)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  FEATURE EXTRACTION  (scripts/extract_features.py)      │
│                                                         │
│  1. RefillDetector   ─ RefillEvents + TrancheTree       │
│  2. FeatureExtractor ─ 26 features por janela (5s/500ms)│
│  3. LabelGenerator   ─ 4 classes por janela             │
│  4. DOMConventionGate─ filtro de qualidade DOM          │
│                                                         │
│  Output: features_YYYY-MM-DD.parquet                    │
│           labels_YYYY-MM-DD.parquet    → S3             │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  TREINO 3 STAGES  (sagemaker/train_entry.py)            │
│                                                         │
│  Stage 1: IcebergAutoencoder (unsupervised, NOISE only) │
│  Stage 2: IcebergClassifier  (supervised, 4 classes)    │
│  Stage 3: TemperatureScaler  (Platt calibration)        │
│                                                         │
│  Output: stage1_autoencoder.pt                          │
│           stage2_classifier.pt  → S3                   │
│           stage3_calibrated.pt                          │
│           norm_stats.json                               │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  INFERÊNCIA  (inference/iceberg_inference.py)           │
│                                                         │
│  IcebergInference.run_day() / .run_window()             │
│  → IcebergOutputV2 (Gate 4 do Master Engine)            │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Estrutura de Ficheiros

```
ml_iceberg_v2/
├── __init__.py                    # version="2.0.0"
├── config.py                      # todos os parâmetros calibrados
├── requirements_sagemaker.txt     # deps do container SageMaker
│
├── features/
│   ├── refill_detector.py         # algoritmo CME: RefillEvent, TrancheTree, IcebergChain
│   ├── feature_extractor.py       # F01–F23 (26 colunas), janelas 5s/500ms
│   ├── label_generator.py         # NOISE/NATIVE/SYNTHETIC/ABSORPTION
│   └── dom_convention_gate.py     # filtro de qualidade DOM
│
├── training/
│   ├── models.py                  # IcebergAutoencoder, IcebergClassifier, TemperatureScaler
│   ├── dataset.py                 # IcebergDataset (PyTorch), FEATURE_COLS, build_weighted_sampler
│   ├── trainer_stage1.py          # loop de treino Stage 1 (MSE, noise-only)
│   ├── trainer_stage2.py          # loop de treino Stage 2 (cross-entropy, focal)
│   └── trainer_stage3.py          # calibração Stage 3 (Platt/NLL)
│
├── sagemaker/
│   ├── train_entry.py             # entry point SageMaker (lê SM_CHANNEL_* / SM_HPS)
│   ├── launch_job.py              # submissão de Training Job via boto3
│   └── run_preprocessing.py       # extracção de features em lote (skip inteligente S3)
│
├── scripts/
│   ├── extract_features.py        # CLI: processar um dia ou --all
│   ├── train_stage1.py            # CLI: treinar Stage 1
│   ├── train_stage2.py            # CLI: treinar Stage 2
│   ├── calibrate_stage3.py        # CLI: calibrar Stage 3
│   ├── generate_labels.py         # CLI: gerar labels
│   └── run_inference.py           # CLI: inferência offline
│
├── inference/
│   └── iceberg_inference.py       # IcebergInference (NOT YET IMPLEMENTED)
│
├── output/
│   └── iceberg_output_v2.py       # IcebergOutputV2, IcebergType, AbsorptionState
│
├── prediction/
│   ├── volume_predictor.py        # VolumePredictor (NOT YET IMPLEMENTED)
│   └── absorption_state.py        # AbsorptionStateTracker (NOT YET IMPLEMENTED)
│
└── validation/
    ├── walk_forward.py            # WalkForwardValidator
    └── report_generator.py        # métricas por fold
```

---

## 4. Features (F01–F23) — 26 Colunas

| Col | Nome | Tipo | Descrição |
|-----|------|------|-----------|
| F01 | `refill_count` | int | Refills na janela |
| F02 | `refill_speed_mean_ms` | float | Média do delay trade→refill (ms) |
| F03 | `refill_speed_std_ms` | float | Std do delay trade→refill (ms) |
| F04 | `refill_size_ratio_mean` | float | Média(refill_size / trade_size) |
| F05 | `refill_size_consistency` | float | 1 − CV(refill sizes); 1.0 = tamanhos idênticos |
| F06 | `price_persistence_s` | float | Segundos que o preço se manteve no nível |
| F07 | `aggressor_volume_absorbed` | float | Total de lots agressores no nível |
| F08 | `dom_imbalance_at_level` | float | DOM imbalance via depth_snapshots [−1, 1] |
| F09 | `book_depth_recovery_rate` | float | Velocidade de recuperação após consumo |
| F10 | `trade_intensity` | float | Trades/segundo na janela |
| F11 | `session_label` | int8 | 0=ASIA 1=EUROPE 2=NY |
| F11a | `hour_of_day` | int8 | 0–23 UTC |
| F11b | `minute_of_hour` | int8 | 0–59 UTC |
| F12 | `volatility_bucket` | int8 | 0=LOW 1=MED 2=HIGH (ATR rolling 5min) |
| F12a | `atr_5min_raw` | float | ATR em ticks (raw contínuo) |
| F13 | `refill_velocity` | float | refill_count / duração_janela_s |
| F14 | `refill_velocity_trend` | float | Slope over last 5 windows |
| F15 | `cumulative_absorbed_volume` | float | Soma running de lots absorvidos no nível |
| F16 | `level_lifetime_s` | float | Tempo activo do nível |
| F17 | `time_since_last_trade_at_vanish_ms` | float | ms desde último trade quando depth desapareceu; −1.0 = sem vanish |
| F18 | `price_excursion_beyond_level_ticks` | float | Ticks que o preço excedeu o nível após último refill |
| F19 | `underflow_volume` | float | Σ(trade_size − visible_book) quando trade_size > visible; 0.0 se sem dados |
| F20 | `iceberg_sequence_direction_score` | float | (bid_count − ask_count) / total últimas 20 janelas; [−1.0, 1.0] |
| F21 | `ofi_rate` | float | Order Flow Imbalance / window_s [lots/s] — Cont et al. 2014 |
| F22 | `vot` | float | Velocity of Tape = Σ(size × price) / window_s [USD-lots/s] |
| F23 | `inst_volatility_ticks` | float | std(prices na janela) / tick_size [ticks] |

**Notas:**
- F11 e F12 têm variantes raw contínuas (F11a/F11b, F12a) para não perder informação no embedding
- F17 usa −1.0 como sentinel ("sem vanish"); substituído por 0.0 antes da normalização
- F21 requer MBO events no depth_updates; 0.0 se não disponível
- Total: 26 colunas → `assert len(FEATURE_COLS) == 26` em `dataset.py`

---

## 5. Labels (4 Classes)

| Classe | Int | Critérios |
|--------|-----|-----------|
| `NOISE` | 0 | Sem padrão iceberg consistente |
| `ICEBERG_NATIVE` | 1 | Cadeia ≥ 3 refills, delay < 10ms, size_consistency > 0.70 |
| `ICEBERG_SYNTHETIC` | 2 | Cadeia ≥ 3 refills, delay 10–100ms, size_consistency > 0.50 |
| `ABSORPTION` | 3 | price_persistence > 3.0s AND aggressor_volume > 10 lots AND sem cadeia clara |

**Campos do label parquet:**
- `window_start`, `label`, `label_confidence`, `refill_count_in_chain`
- `peak_size_observed`, `chain_complete`, `chain_weight`
- `cancellation_detected`, `num_chains_in_tree`

**`chain_weight`:** CME Paper §4.2 — `1 / num_unique_length_chains` (Kaplan-Meier)

---

## 6. Arquitectura dos Modelos

### Stage 1 — IcebergAutoencoder
```
Input:  26 features (NOISE windows apenas)
Encoder: 26 → Linear(64) → BN → ReLU → Dropout(0.1) → Linear(32) → BN → ReLU → Linear(8)
Decoder:  8 → Linear(32) → BN → ReLU → Dropout(0.1) → Linear(64) → BN → ReLU → Linear(26)
Loss:   MSE (reconstruction)
Saved:  stage1_autoencoder.pt + norm_stats.json
```
Objectivo: aprender o comportamento "normal" do DOM. Alto erro de reconstrução = padrão anómalo (potencial iceberg).

### Stage 2 — IcebergClassifier
```
Input:  16 raw features + 8 latent (Stage 1 frozen) + 1 recon_error = 25 dims
Net:    Linear(64) → BN → ReLU → Dropout(0.2) → Linear(32) → BN → ReLU → Dropout(0.1) → Linear(4)
Loss:   Cross-entropy (+ opcional focal loss para ABSORPTION raro)
Metric: macro F1 (val set)
Saved:  stage2_classifier.pt
```
Os 16 raw features são o subset `STAGE2_RAW_COLS` de `models.py`.

### Stage 3 — TemperatureScaler
```
Input:  logits do Stage 2
T:      parâmetro escalar aprendido (init=1.0)
Output: logits / T.clamp(min=1e-6)
Loss:   NLL (negative log-likelihood)
Metric: ECE (Expected Calibration Error) before/after
Saved:  stage3_calibrated.pt
```

---

## 7. Dataset e Splitting

`IcebergDataset` em `training/dataset.py`:
- Lê todos os parquets de `features_dir/` e `labels_dir/`
- Suporta dois naming conventions: `YYYY-MM-DD.parquet` (Databento Jul–Nov 2025) e `features_YYYY-MM-DD.parquet` (Dez 2025+)
- Split por data: **80% train / 20% val** (por ordem cronológica)
- Normalização Z-score calculada no train, aplicada ao val
- `noise_only=True` para Stage 1 (filtra label==0)
- F17 sentinels (−1.0) substituídos por 0.0 antes de normalizar
- `build_weighted_sampler()`: combina inverse class frequency com chain_weight (KM)

---

## 8. Dados em S3

| Prefixo S3 | Ficheiros | Cobertura | Naming |
|------------|-----------|-----------|--------|
| `s3://fluxquantumai-data/level2/gc/` | ~101 | Dez 2025 → Abr 2026 | `depth_updates_YYYY-MM-DD.csv.gz` |
| `s3://fluxquantumai-data/features/iceberg_v2/` | 181 | Jul 2025 → 2026-04-10 | `features_YYYY-MM-DD.parquet` |
| `s3://fluxquantumai-data/labels/iceberg_v2/` | 182 | Jul 2025 → 2026-04-10 | `labels_YYYY-MM-DD.parquet` |
| `s3://fluxquantumai-data/sagemaker/models/` | — | por job name | `stage1_autoencoder.pt`, etc. |

**Dias ausentes esperados:** Good Friday (2026-04-03), sábados/domingos.

---

## 9. SageMaker Training Pipeline

### Configuração
- **Bucket:** `fluxquantumai-data`
- **Role ARN:** `arn:aws:iam::116101834074:role/SageMakerExecutionRole`
- **Permissões:** `AmazonS3FullAccess` (adicionado 2026-04-11)
- **Região:** `us-east-1`
- **Container CPU:** `pytorch-training:2.1.0-cpu-py310-ubuntu20.04-sagemaker`
- **Container GPU:** `pytorch-training:2.1.0-gpu-py310-cu118-ubuntu20.04-sagemaker`
- **Instância recomendada:** `ml.m5.4xlarge` (CPU, ~1–2h); `ml.p3.2xlarge` (GPU, ~30min) se quota disponível

### Submissão
```bash
cd C:/FluxQuantumAI
python ml_iceberg_v2/sagemaker/launch_job.py --instance ml.m5.4xlarge

# Opções:
# --stage1-epochs 50 --stage2-epochs 50
# --stage1-batch 512 --stage2-batch 256
# --stage1-lr 0.001 --stage2-lr 0.0003
# --dry-run   (mostra config sem submeter)
```

### Hyperparâmetros (valores calibrados 2026-04-10)
| Parâmetro | Valor | Nota |
|-----------|-------|------|
| `stage1_epochs` | 50 | Autoencoder converge em <30 epochs tipicamente |
| `stage2_epochs` | 50 | ABSORPTION raro (~1%) precisa mais epochs |
| `stage1_batch` | 512 | Safe para ~3M NOISE rows em m5.4xlarge |
| `stage2_batch` | 256 | Menor para melhor gradient signal |
| `stage1_lr` | 0.001 | Adam default |
| `stage2_lr` | 0.0003 | Conservador (autoencoder congelado) |
| `STAGE1_LATENT_DIM` | 8 | Bottleneck |
| `WALK_FORWARD_TRAIN_DAYS` | 45 | 12 folds (vs 9 com 60d) |
| `WALK_FORWARD_TEST_DAYS` | 5 | |

### Variáveis de ambiente (container)
| Variável | Descrição |
|----------|-----------|
| `SM_CHANNEL_FEATURES` | Path local das features no container |
| `SM_CHANNEL_LABELS` | Path local dos labels no container |
| `SM_MODEL_DIR` | Destino dos checkpoints |
| `SM_OUTPUT_DATA_DIR` | Destino do `training_results.json` |
| `SM_HPS` | JSON string com hyperparâmetros |
| `SAGEMAKER_SUBMIT_DIRECTORY` | URI S3 do source.tar.gz |
| `SAGEMAKER_PROGRAM` | `train_entry.py` |

### Jobs submetidos

| Job Name | Data | Instância | Status | Nota |
|----------|------|-----------|--------|------|
| `iceberg-v2-20260411-121134` | 2026-04-11 | ml.m5.4xlarge | FAILED | S3 AccessDenied (role sem permissões) |
| `iceberg-v2-20260411-131055` | 2026-04-11 | ml.m5.4xlarge | FAILED | tar.gz com directory entries como ficheiros regulares |
| `iceberg-v2-20260411-131525` | 2026-04-11 | ml.m5.4xlarge | **IN PROGRESS** | Fix aplicado: skip directory entries no zip→tar |

---

## 10. Bugs Corrigidos (2026-04-11)

### BUG-001: `n_features=23` em `trainer_stage1.py`
- **Ficheiro:** `training/trainer_stage1.py` linha 85
- **Antes:** `IcebergAutoencoder(n_features=23, ...)`
- **Depois:** `IcebergAutoencoder(n_features=26, ...)`
- **Impacto:** Shape mismatch em runtime — autoencoder incompatível com dataset (26 features)

### BUG-002: Windows backslash paths no zip (`_zip_source`)
- **Ficheiro:** `sagemaker/launch_job.py` função `_zip_source()`
- **Antes:** `arcname = str(path.relative_to(repo_root))` → `ml_iceberg_v2\training\trainer_stage1.py`
- **Depois:** `arcname = path.relative_to(repo_root).as_posix()` → `ml_iceberg_v2/training/trainer_stage1.py`
- **Impacto:** No Linux, backslashes são literais → Python criava namespace packages (sem `__init__.py`) → `ModuleNotFoundError`

### BUG-003: Directory entries como ficheiros regulares no tar.gz (`_upload_source`)
- **Ficheiro:** `sagemaker/launch_job.py` função `_upload_source()`
- **Antes:** Todos os entries do zip (incluindo directórios como `ml_iceberg_v2/training/`) eram escritos no tar como `REGTYPE` (ficheiro regular)
- **Depois:** `if member.filename.endswith("/"): continue` — skip de directory entries
- **Impacto:** No container SageMaker, `ml_iceberg_v2/training/` era extraído como ficheiro → `__init__.py` e outros ficheiros dentro não podiam ser extraídos → `ModuleNotFoundError`

### BUG-004: `ABSORPTION_PERSISTENCE_S = 5.0` → `3.0`
- **Ficheiro:** `config.py`
- **Antes:** `5.0` — mas a janela tem 5s, logo `price_persistence_s` máximo ≈ 4.97 → condição `> 5.0` nunca era True → 0 labels ABSORPTION
- **Depois:** `3.0` — gera labels ABSORPTION reais

### BUG-005: `ABSORPTION_VOLUME_THRESHOLD = 50` → `10`
- **Ficheiro:** `config.py`
- **Antes:** `50` lots — GC p99 ≈ 22 lots → threshold acima do p99, near-zero samples
- **Depois:** `10` lots — threshold calibrado para o mercado real GC

---

## 11. Preprocessing Pipeline

`sagemaker/run_preprocessing.py` — extracção de features em lote:

```bash
# Processar dias em falta (modo normal):
python ml_iceberg_v2/sagemaker/run_preprocessing.py --workers 1

# A partir de uma data:
python ml_iceberg_v2/sagemaker/run_preprocessing.py --start-date 2026-03-27 --workers 1

# Forçar reprocessamento:
python ml_iceberg_v2/sagemaker/run_preprocessing.py --start-date 2026-01-01 --overwrite --workers 1
```

**Notas operacionais:**
- `workers=1` recomendado — dias com 90K+ refills causam OOM com workers=2
- Skip inteligente: verifica S3 antes de processar; ficheiros < 50KB = corrompidos → reprocessa
- Upload imediato por data (não batch no final) — evita perda total se o processo crashar

---

## 12. Output Schema (Gate 4)

`IcebergOutputV2` — interface com o Master Engine:

```python
@dataclass
class IcebergOutputV2:
    detected: bool
    iceberg_type: IcebergType        # NONE / NATIVE / SYNTHETIC / ABSORPTION
    side: IcebergSide                 # BID / ASK / NONE
    confidence: float                 # [0.0, 1.0] — calibrado pelo Stage 3
    absorption_state: AbsorptionState # NONE / ACTIVE / WEAKENING / EXHAUSTED
    price_level: float
    estimated_volume: float           # VolumePredictor (ainda não implementado)
    window_start: int                 # nanoseconds UTC
    # ...

    def get_confluence_multiplier(self, direction: str) -> float:
        # Retorna [0.5, 1.5] para uso no Master Engine
```

---

## 13. Módulos Ainda Não Implementados

| Módulo | Status | Sprint |
|--------|--------|--------|
| `inference/iceberg_inference.py` | `NotImplementedError` | Inference Sprint |
| `prediction/volume_predictor.py` | `NotImplementedError` | Prediction Sprint |
| `prediction/absorption_state.py` | `NotImplementedError` | Prediction Sprint |
| `validation/walk_forward.py` | Esqueleto (stub) | Validation Sprint |
| `validation/report_generator.py` | Esqueleto (stub) | Validation Sprint |

---

## 14. Dependências

```
# Já incluídas no container SageMaker:
torch, numpy, scipy, scikit-learn, pandas, boto3, s3fs

# Adicionais (requirements_sagemaker.txt):
pyarrow >= 14.0.0        # pd.read_parquet / to_parquet
fastparquet >= 2023.10.0 # backend alternativo
zstandard >= 0.21.0      # .zst decompression (Databento)
pandas >= 2.0.0, < 3.0.0
numpy >= 1.24.0, < 2.0.0
```

---

## 15. Parâmetros de Configuração (`config.py`)

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| `TICK_SIZE_DEFAULT` | 0.10 | USD/oz fallback |
| `MAX_REFILL_DELAY_MS` | 100 | Máximo delay trade→refill para qualificar |
| `MIN_CHAIN_LENGTH` | 3 | Mínimo refills para formar IcebergChain |
| `NATIVE_MAX_DELAY_MS` | 10 | Threshold NATIVE vs SYNTHETIC |
| `SYNTHETIC_MAX_DELAY_MS` | 100 | Threshold SYNTHETIC vs NOISE |
| `SIZE_CONSISTENCY_NATIVE` | 0.70 | 1−CV mínimo para NATIVE |
| `SIZE_CONSISTENCY_SYNTHETIC` | 0.50 | 1−CV mínimo para SYNTHETIC |
| `ABSORPTION_PERSISTENCE_S` | 3.0 | Price persistence mínima para ABSORPTION |
| `ABSORPTION_VOLUME_THRESHOLD` | 10 | Lots mínimos para ABSORPTION |
| `CHUNK_SIZE` | 500_000 | Rows por chunk no processing |
| `OVERLAP_BUFFER_S` | 30 | Overlap entre chunks para não partir chains |
| `VOLATILITY_LOW_THRESHOLD` | 6.0 | ATR ticks — p22 GC (22% LOW) |
| `VOLATILITY_HIGH_THRESHOLD` | 18.0 | ATR ticks — p75 GC (26% HIGH) |
| `WINDOW_SIZE_S` | 5 | Duração janela normal |
| `STEP_SIZE_S` | 1 | Passo janela normal |
| `SIDE_HISTORY_WINDOWS` | 20 | Lookback F20 direction score |
| `VELOCITY_TREND_LOOKBACK` | 5 | Lookback F14 velocity trend |
