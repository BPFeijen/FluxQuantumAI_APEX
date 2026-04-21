# FluxQuantumAI - Documentação Técnica dos Módulos Principais
## Estado Atual: 23/12/2025

**Autor:** FluxFox Research Division
**Sistema:** FluxQuantumAI Trading System
**Versão:** 2.0

---

## Índice

1. [MNQ Signal Generator](#1-mnq-signal-generator)
2. [Master Engine](#2-master-engine)
3. [Iceberg Order Detector](#3-iceberg-order-detector)
4. [Anomaly Detector](#4-anomaly-detector)
5. [Regime Forecast Provider](#5-regime-forecast-provider)
6. [Integração Entre Módulos](#6-integração-entre-módulos)
7. [Configurações e Thresholds](#7-configurações-e-thresholds)

---

## 1. MNQ Signal Generator

### 1.1 Visão Geral

O **MNQ Signal Generator** é o módulo responsável pela geração de sinais de trading para o contrato Micro E-mini NASDAQ-100 (MNQ). Une a lógica de detecção de exaustão (PRIMARY signal) com análise de warnings e lógica de Runner.

**Localização:** `ml_signal/mnq_signal_generator.py`

### 1.2 Arquitetura de Fluxo

```
ROW (dados L2) → BLOCO 1 (PRIMARY) → BLOCO 2 (WARNINGS) → BLOCO 3 (BLOCKED?) → BLOCO 4 (RUNNER?) → OUTPUT
```

### 1.3 BLOCO 1 - Primary Signal (Detecção de Exaustão)

#### 1.3.1 Estratégia: Exhaustion / Mean-Reversion

O gerador detecta padrões de **EXAUSTÃO** e os **FADE** (opera contra).

**Convenção DOM Canônica (Industry Standard):**
```
dom_imbalance = (BID - ASK) / (BID + ASK)
> 0 → buyer-heavy (compradores dominantes) → bullish
< 0 → seller-heavy (vendedores dominantes) → bearish
```

#### 1.3.2 Padrões de Exaustão

| Padrão | Condição | Ação |
|--------|----------|------|
| **BUY_EXHAUSTION** | Livro buyer-heavy (dom > threshold) + delta compradora forte | → SHORT (fade) |
| **SELL_EXHAUSTION** | Livro seller-heavy (dom < threshold) + delta vendedora forte | → LONG (fade) |

#### 1.3.3 Thresholds por Sessão

**DOM Thresholds:**
| Sessão | LONG (min) | SHORT (max) |
|--------|------------|-------------|
| ASIA | 0.115 | -0.079 |
| EUROPE | 0.130 | -0.100 |
| NY | 0.177 | -0.095 |

**Cumulative Delta Thresholds:**
| Sessão | LONG (min) | SHORT (max) |
|--------|------------|-------------|
| ASIA | 1876.0 | -6296.0 |
| EUROPE | 2292.0 | -856.0 |
| NY | 2099.2 | -2478.0 |

**Volume per Second (Filtro de Liquidez):**
| Sessão | Threshold |
|--------|-----------|
| ASIA | 9.4 |
| EUROPE | 3.0 |
| NY | 7.4 |

### 1.4 BLOCO 2 - Warnings

#### 1.4.1 Warnings Estruturais (Bloqueiam)

| Warning | Condição | Tipo |
|---------|----------|------|
| VPIN_CRITICO | vpin >= 0.80 | Crítico |
| VPIN_WARNING | vpin >= 0.70 | Soft |
| CANCEL_RATE_CRITICO | cancel_rate >= 0.90 | Crítico |
| CANCEL_RATE_WARNING | cancel_rate >= 0.70 | Soft |
| TOXICIDADE_CRITICA | toxicity >= 0.80 | Crítico |
| TOXICIDADE_WARNING | toxicity >= 0.60 | Soft |
| SPREAD_ALTO | spread >= 2.0 ticks | Soft |
| LIQUIDEZ_BAIXA | depth < 100 | Soft |
| VOLATILIDADE_EXTREMA | atr > percentil 95 | Soft |
| LOW_ABSORPTION | absorption < min por sessão | Soft |

#### 1.4.2 Warnings Direcionais

| Flag | Direção | Descrição |
|------|---------|-----------|
| BULLISH_CVD | Bullish | CVD divergência bullish |
| OFI_BUY | Bullish | Order Flow Imbalance comprador |
| OBI_BUY_MAGNET | Bullish | Book puxando para cima |
| LARGE_LOTS_BOTTOM | Bullish | Large lots em fundo |
| ABSORPTION_BID | Bullish | Absorção no bid |
| PULLING_STACKING_BULL | Bullish | Ask pulling + Bid stacking |
| BEARISH_CVD | Bearish | CVD divergência bearish |
| OFI_SELL | Bearish | Order Flow Imbalance vendedor |
| OBI_SELL_MAGNET | Bearish | Book puxando para baixo |
| LARGE_LOTS_TOP | Bearish | Large lots em topo |
| ABSORPTION_ASK | Bearish | Absorção no ask |
| PULLING_STACKING_BEAR | Bearish | Bid pulling + Ask stacking |

### 1.5 BLOCO 3 - Decisão de Bloqueio

**Regras de Bloqueio:**
1. Warning estrutural CRÍTICO → bloqueia imediatamente
2. ≥ 2 warnings estruturais SOFT → bloqueia
3. ≥ 2 warnings direcionais OPOSTOS ao sinal → bloqueia

### 1.6 BLOCO 4 - Runner Logic

**Ativação do Runner:**
- PRIMARY bloqueado
- Warnings direcionais têm bias claro (≥ 2 flags na mesma direção, 0 na oposta)
- Sem warnings mistos (mixed_warnings = False)

**Direção do Runner:** Oposta ao PRIMARY (contrarian)

### 1.7 Movement Detector (BLOCO A/C)

#### 1.7.1 Tipos de Movimento

```python
class MovementType(Enum):
    NONE = "NONE"              # Sem classificação
    PULLBACK = "PULLBACK"      # Retração temporária (entrada favorável)
    REVERSAL = "REVERSAL"      # Reversão completa
    CONTINUATION = "CONTINUATION"  # Movimento a favor
    FALSE_BREAKOUT = "FALSE_BREAKOUT"  # Rompimento falso
```

#### 1.7.2 Breakout State

```python
class BreakoutState(Enum):
    NONE = "NONE"              # Dentro do range
    BREAKOUT = "BREAKOUT"      # Rompimento ativo
    FALSE_BREAKOUT = "FALSE_BREAKOUT"  # Rompimento falhou
    RETEST = "RETEST"          # Retestando nível
```

#### 1.7.3 Trend Bias (Config C - H1+M30)

```python
class TrendBias(Enum):
    UP = "UP"                  # Tendência de alta
    DOWN = "DOWN"              # Tendência de baixa
    NEUTRAL = "NEUTRAL"        # Lateralização
    POSSIBLE_REVERSAL_UP = "POSSIBLE_REVERSAL_UP"    # Down mas momentum bullish
    POSSIBLE_REVERSAL_DOWN = "POSSIBLE_REVERSAL_DOWN"  # Up mas momentum bearish
```

**Configuração Config C (Vencedora):**
- Timeframe maior: H1 (peso 0.6)
- Timeframe menor: M30 (peso 0.4)
- H1 lookback: 6 candles (~6 horas)
- M30 lookback: 8 candles (~4 horas)
- Slope threshold: 0.2

**Métricas Validadas Config C:**
- Win Rate: 56.2%
- Profit Factor: 0.65
- Hard Stop Rate: 31.2%
- NEUTRAL: 11.9%

### 1.8 DOM Anti-Noise Filters (PATCH 4)

**Funcionalidades:**
- Smoothing exponencial do DOM
- Deadband para evitar ruído
- Persistência de estado (BULLISH/BEARISH/NEUTRAL)
- Gate de confluence com Delta

### 1.9 Daily Range Gate

**Configuração:**
- MIN_DAY_RANGE_TICKS: 80 (variação mínima do dia)
- LONG_MAX_RANGE_FROM_LOW: 0.20 (máximo 20% do range para LONG)
- SHORT_MAX_RANGE_FROM_HIGH: 0.20 (máximo 20% do range para SHORT)

### 1.10 Output: MNQSignalOutput

```python
@dataclass
class MNQSignalOutput:
    # Sinal principal
    primary_signal: str          # "LONG" / "SHORT" / "NONE"
    primary_blocked: bool
    block_reason: Optional[str]

    # Warnings
    warnings_directional: List[str]
    warnings_structural: List[str]
    warnings_bias: str           # "BULLISH" / "BEARISH" / "MIXED" / "NONE"

    # Runner
    runner_direction: str        # "LONG" / "SHORT" / "NONE"
    runner_allowed: bool
    mixed_warnings: bool

    # Movement Detector
    movement_type: MovementType
    breakout_state: BreakoutState
    trend_bias: TrendBias
    movement_confidence: float   # 0.0 a 1.0

    # DOM Telemetry
    dom_gate_passed_long: bool
    dom_gate_passed_short: bool
    # ... outros campos de telemetria
```

---

## 2. Master Engine

### 2.1 Visão Geral

A **Master Engine** é o orquestrador central que combina sinais de múltiplos providers usando uma arquitetura hierárquica de gates (não média ponderada).

**Localização:** `ml_engine/master_engine.py`

### 2.2 Arquitetura Hierárquica de Gating

```
GATE 1: Anomaly Detector (VETO) → score > 99.9% = BLOCK
GATE 2: News Provider (VETO/FORCE_EXIT)
GATE 3: MNQ Signal Generator (DIRECTION) → ÚNICA fonte de direção
GATE 4: Iceberg Detector (CONFLUENCE) → Ajusta confiança +/-30%
GATE 5: Position Sizing → base x multipliers
GATE 6: Runner de Pullback (BLOCO 2)
GATE 7: Extension Legs (BLOCO 3)
```

### 2.3 GATE 1 - Anomaly Detector

**Comportamento:**
- Score > 99.9% → VETO (bloqueia novas entradas)
- **BLOCO 0:** NUNCA bloqueia fechamento de posição existente

**Multiplicadores:**
```
score < 95% → 1.0
score < 99% → 0.75
score < 99.5% → 0.50
score < 99.9% → 0.25
score >= 99.9% → 0.0 (BLOCK)
```

### 2.4 GATE 2 - News Provider

**Severidades:**
- `LOW`: Multiplier 1.0
- `MEDIUM`: Multiplier 0.75
- `HIGH`: Multiplier 0.5
- `CRITICAL`: Multiplier 0.0 (BLOCK) ou FORCE_EXIT

**force_exit = True:** Fecha posição imediatamente, ignora MNQ

### 2.5 GATE 3 - MNQ Signal Generator

**Única fonte de direção.** Determina:
1. Se PRIMARY não bloqueado → usa primary_signal
2. Se PRIMARY bloqueado + runner_allowed + não mixed → usa runner_direction
3. Caso contrário → NONE

**ANTI_FADE_STRONG_TREND Gate:**
- Bloqueia entradas contra tendência forte
- Exceções: REVERSAL confirmado ou FALSE_BREAKOUT

### 2.6 GATE 4 - Iceberg Detector

**Confluence Multipliers:**
| Situação | Multiplier |
|----------|------------|
| Aligned (iceberg no mesmo lado) | 1.30 |
| Opposed (iceberg contra) | 0.75 |
| Neutro | 1.00 |

### 2.7 GATE 5 - Position Sizing

```
final_multiplier = anomaly_mult * news_mult * iceberg_mult
contracts = base_contracts * final_multiplier
```

**Limites:**
- Min multiplier: 0.25
- Max multiplier: 1.50
- Max contracts: 10

**Zero-Contract Outcome:** Se contracts < 1 e posição FLAT → NO_TRADE

### 2.8 BLOCO 0 - Never Block Close

**Regra de Ouro:** Gates de VETO (Anomaly, News) NUNCA bloqueiam fechamento de posição existente.

```python
has_position = position_state.direction in (LONG, SHORT)
if has_position:
    # Permite fechamento mesmo com VETO ativo
```

### 2.9 BLOCO 1 - Movement Type (Pullback vs Reversal)

| movement_type | Ação |
|---------------|------|
| REVERSAL | Fecha tudo imediatamente |
| FALSE_BREAKOUT | Fecha tudo imediatamente |
| PULLBACK + giveback >= 25% | Fecha |
| PULLBACK + giveback < 25% | HOLD |
| NONE | Fecha (default) |

**Giveback Threshold:** 25% do lucro máximo

### 2.10 BLOCO 2 - Runner de Pullback

**Detecção de Bordas:**
- Pullback Start: previous != PULLBACK, current == PULLBACK
- Pullback End: previous == PULLBACK, current != PULLBACK

**Lógica:**
1. Início do pullback + posição ativa + MNQ mesma direção → Abre Runner oposto
2. Fim do pullback + runner ativo → Fecha Runner

**Runner Contracts:** `base * 0.5 * final_multiplier`

### 2.11 BLOCO 3 - Extension Legs

**Targets:** 1.25%, 1.50%, 1.75%, 2.00%

**Condições para Abrir:**
1. extension_ready == True (TP de 1% atingido)
2. extension_opened == False
3. movement_type != REVERSAL
4. Sem veto crítico

**Fechar:** Se REVERSAL detectado

### 2.12 Decisão Final

| Estado Posição | Direção MNQ | Ação |
|----------------|-------------|------|
| FLAT | LONG | OPEN_LONG |
| FLAT | SHORT | OPEN_SHORT |
| LONG | LONG | HOLD |
| LONG | SHORT | Check movement_type → CLOSE/HOLD |
| SHORT | SHORT | HOLD |
| SHORT | LONG | Check movement_type → CLOSE/HOLD |

### 2.13 Output: EngineDecision

```python
@dataclass
class EngineDecision:
    action: ActionType          # OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT, HOLD, BLOCKED, NO_TRADE
    contracts: int
    source: str                 # "PRIMARY", "RUNNER", "PULLBACK_RUNNER", "EXTENSION", "NEWS"
    reason: str
    anomaly_multiplier: float
    news_multiplier: float
    iceberg_multiplier: float
    warnings: List[str]
    open_extension_legs: bool
    close_extension_legs: bool
```

---

## 3. Iceberg Order Detector

### 3.1 Visão Geral

O **Iceberg Detector** detecta ordens iceberg (ocultas) no order book usando múltiplas estratégias de detecção.

**Localização:** `ml_iceberg/detection/`

**Baseado em:** Paper "CME Iceberg Order Detection and Prediction" (DXFeed/Devexperts)

### 3.2 Arquitetura

```
IcebergDetector (Unificado)
├── HeuristicEngine (regras do paper DXFeed)
├── NativeIcebergDetector (order_id preservado)
└── SyntheticIcebergDetector (novos order_ids)
```

### 3.3 Tipos de Iceberg

```python
class IcebergType(Enum):
    NATIVE = "native"       # Gerenciado pela exchange (mesmo order_id)
    SYNTHETIC = "synthetic" # Gerenciado por ISV (novos order_ids)
    UNKNOWN = "unknown"     # Tipo não determinado
```

### 3.4 Modos de Detecção

```python
class DetectionMode(Enum):
    HEURISTIC_ONLY = "heuristic"   # Apenas regras heurísticas
    ML_ONLY = "ml"                 # Apenas modelo ML
    HYBRID = "hybrid"              # Combinação de ambos
    NATIVE_FOCUS = "native"        # Foco em icebergs nativos
    SYNTHETIC_FOCUS = "synthetic"  # Foco em icebergs sintéticos
```

### 3.5 Regras Heurísticas (DXFeed)

1. **Trade > Visível:** Trade executa volume maior que o visível no book
2. **Refill Rápido:** Nova ordem aparece no mesmo preço em < 100ms após trade
3. **Tamanho Consistente:** Tamanho da nova ordem similar ao trade anterior
4. **Padrão Repetitivo:** Padrão se repete consistentemente

### 3.6 Configuração Heurística

```python
DEFAULT_CONFIG = {
    'max_refill_time_ms': 100,        # Tempo máximo para refill
    'typical_refill_time_ms': 50,     # Tempo típico
    'native_refill_time_ms': 10,      # Refill instantâneo = nativo
    'size_tolerance_percent': 0.2,    # Tolerância 20%
    'min_peak_size': 1,
    'min_refills_for_confirmation': 2,
    'min_probability_threshold': 0.3,
    'max_inactive_time_ms': 5000,
}
```

### 3.7 IcebergTracker

Rastreia cada iceberg ativo:

```python
@dataclass
class IcebergTracker:
    id: str
    price: float
    side: IcebergSide           # BID / ASK
    iceberg_type: IcebergType
    first_detection: datetime
    last_update: datetime
    peak_size: float
    total_executed: float
    refill_count: int
    predicted_total: Optional[float]
```

### 3.8 Cálculo de Probabilidade

```python
probability = base_prob (0.3)
            + refill_bonus (min 0.4, refills * 0.1)
            + type_bonus (0.2 se native)
            + timing_consistency
```

### 3.9 Alert Levels

```python
class AlertLevel(Enum):
    LOW = "low"           # < 50% confiança
    MEDIUM = "medium"     # 50-75% confiança
    HIGH = "high"         # > 75% confiança
    CONFIRMED = "confirmed"  # Múltiplos refills observados
```

### 3.10 Output: IcebergOutput

```python
@dataclass
class IcebergOutput:
    detected: bool
    side: IcebergSide
    price: float
    peak_size: float
    total_executed: float
    refill_count: int
    probability: float
    predicted_total_size: Optional[float]
```

### 3.11 Integração com Master Engine

```python
def get_confluence_multiplier(direction: str) -> float:
    if self.side == BID and direction == "LONG":
        return 1.30  # Aligned
    elif self.side == ASK and direction == "SHORT":
        return 1.30  # Aligned
    elif self.side == BID and direction == "SHORT":
        return 0.75  # Opposed
    elif self.side == ASK and direction == "LONG":
        return 0.75  # Opposed
    return 1.0
```

---

## 4. Anomaly Detector

### 4.1 Visão Geral

O **Anomaly Detector** identifica condições de mercado anormais usando autoencoders para detectar anomalias em dados de microestrutura.

**Localização:** `ml_anomaly/`

### 4.2 Arquitetura

```
ml_anomaly/
├── inference/
│   ├── anomaly_scorer.py    # Scoring em tempo real
│   └── regime_detector.py   # Detecção de regime
├── training/
│   └── trainer.py           # Pipeline de treinamento
├── models/
│   ├── autoencoder.py       # MicrostructureAutoencoder
│   └── variational.py       # VariationalAutoencoder
└── validation/
    └── anomaly_validator.py
```

### 4.3 Modelos Disponíveis

1. **MicrostructureAutoencoder:** Autoencoder básico para features de microestrutura
2. **OrderBookAutoencoder:** Especializado em dados de order book
3. **VariationalAutoencoder:** VAE para detecção mais robusta

### 4.4 AnomalyScorer

```python
class AnomalyScorer:
    def score(data) -> Dict:
        return {
            'score': float,           # 0-1
            'is_anomaly': bool,
            'alert_level': str,       # 'normal', 'warning', 'critical'
            'reconstruction_error': float,
            'timestamp': str
        }

    def score_with_contributions(data) -> Dict:
        # Inclui contribuição de cada feature
        return {
            ...,
            'feature_contributions': Dict[str, float],
            'top_anomalous_features': List[str],
            'latent_representation': List[float]
        }
```

### 4.5 Alert Thresholds

```python
alert_thresholds = {
    'warning': 0.7,   # score >= 0.7 = warning
    'critical': 0.9   # score >= 0.9 = critical
}
```

### 4.6 Score Trend Analysis

```python
def get_score_trend(window: int = 100) -> Dict:
    return {
        'mean': float,
        'std': float,
        'trend': float,  # Regressão linear (positivo = aumentando)
        'max': float
    }
```

### 4.7 AnomalyOutput

```python
@dataclass
class AnomalyOutput:
    score: float              # 0-1
    block_trade: bool         # True se score > 99.9%
    position_multiplier: float
    movement_type: MovementType  # DEPRECATED (migrado para MNQ)
```

### 4.8 Multiplicadores de Posição

| Score | Multiplier |
|-------|------------|
| < 0.95 | 1.0 |
| < 0.99 | 0.75 |
| < 0.995 | 0.50 |
| < 0.999 | 0.25 |
| >= 0.999 | 0.0 (BLOCK) |

---

## 5. Regime Forecast Provider

### 5.1 Visão Geral

O **Regime Forecast Provider** é um provider preditivo que avalia a qualidade de entradas sugeridas pelo MNQ Signal Generator e alerta probabilidade de reversão forte.

**Localização:** `ml_predictive/regime_forecast/provider.py`

### 5.2 Arquitetura

```python
class RegimeForecastProvider:
    def __init__(model_path: Optional[str]):
        # Se model_path não existe, usa fallback heurístico

    def get_predictive_risk(x: RegimeForecastInput) -> PredictiveRiskOutput:
        # ML model ou heurística
```

### 5.3 Input

```python
@dataclass
class RegimeForecastInput:
    timestamp: datetime
    session: str                    # "ASIA" | "EUROPE" | "NY"
    price: float
    direction: Direction            # LONG | SHORT
    dom_imbalance: float
    cumulative_delta: float
    volume_per_second: float
    anomaly_score: float = 0.0
    iceberg_side: str = "NONE"      # "BID" | "ASK" | "NONE"
    iceberg_confidence: float = 0.0
    news_risk_score: float = 0.0
    time_to_session_close_min: float = 999.0
    is_rth: bool = True
    vpin: float = 0.0
    toxicity_score: float = 0.0
    cancel_rate: float = 0.0
```

### 5.4 Output

```python
@dataclass
class PredictiveRiskOutput:
    prob_good: float            # Probabilidade de continuação favorável
    prob_bad: float             # Probabilidade de reversão forte
    risk_level: RiskLevel       # LOW | MEDIUM | HIGH | EXTREME
    recommend: Recommendation   # ALLOW_ENTRY | REDUCE_SIZE | AVOID_ENTRY | FORCE_EXIT
    model_used: str             # "ml_model" | "heuristic"
    confidence: float           # 0-1
```

### 5.5 Classificação de Risco

| prob_bad | RiskLevel | Recommendation |
|----------|-----------|----------------|
| < 0.30 | LOW | ALLOW_ENTRY |
| < 0.50 | MEDIUM | REDUCE_SIZE |
| < 0.75 | HIGH | AVOID_ENTRY |
| >= 0.75 | EXTREME | FORCE_EXIT |

### 5.6 Features do Modelo ML

```python
FEATURE_NAMES = [
    "dom_imbalance",
    "cumulative_delta",
    "volume_per_second",
    "anomaly_score",
    "iceberg_confidence",
    "news_risk_score",
    "time_to_session_close_min",
    "vpin",
    "toxicity_score",
    "cancel_rate",
    "dir_is_long",      # One-hot
    "dir_is_short",     # One-hot
    "session_asia",     # One-hot
    "session_europe",   # One-hot
    "session_ny",       # One-hot
    "iceberg_is_bid",   # One-hot
    "iceberg_is_ask",   # One-hot
    "is_rth_float",
    "dom_against_dir",  # Interação
    "delta_against_dir" # Interação
]
```

### 5.7 Fallback Heurístico

Quando modelo ML não está disponível:

**Regras de Score:**
| Condição | Score Adicionado |
|----------|------------------|
| DOM contra direção (< -0.15 para LONG) | +0.25 |
| Delta fortemente contra | +0.25 |
| Anomaly score > 0.99 | +0.35 |
| Anomaly score > 0.95 | +0.25 |
| Iceberg forte contra | +0.30 |
| News risk > 0.8 | +0.25 |
| Tempo para fechamento < 30min | +0.20 |
| VPIN > 0.8 | +0.15 |
| Toxicity > 0.8 | +0.15 |

---

## 6. Integração Entre Módulos

### 6.1 Fluxo de Dados

```
┌─────────────────┐
│   L2 Data       │
│ (microestrutura)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  MNQ Signal     │     │  Iceberg        │
│  Generator      │     │  Detector       │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│              Master Engine               │
│  (combina todos os sinais via gating)   │
├─────────────────────────────────────────┤
│  + Anomaly Detector (GATE 1)            │
│  + News Provider (GATE 2)               │
│  + Regime Forecast (opcional)           │
└────────────────────┬────────────────────┘
                     │
                     ▼
              EngineDecision
```

### 6.2 Dependências

| Módulo | Depende de |
|--------|------------|
| Master Engine | MNQSignalOutput, AnomalyOutput, IcebergOutput, NewsResult |
| MNQ Signal Generator | L2 Data, Movement Detector |
| Movement Detector | H1/M30 OHLCV (para trend_bias), Microestrutura |
| Iceberg Detector | Trade Events, Order Book State |
| Anomaly Detector | Microstructure Features |
| Regime Forecast | Todos os outputs anteriores |

### 6.3 Responsabilidades Migradas

**BLOCO C (Dezembro 2025):**
- `movement_type` migrado de AnomalyOutput para MNQSignalOutput
- Movement Detector agora é responsabilidade do MNQ Signal Generator

---

## 7. Configurações e Thresholds

### 7.1 Constantes Globais

```python
# ml_engine/config.py
MIN_DAY_RANGE_TICKS = 80
LONG_MAX_RANGE_FROM_LOW = 0.20
SHORT_MAX_RANGE_FROM_HIGH = 0.20
MNQ_TICK_SIZE = 0.25
```

### 7.2 Engine Risk Config

```python
DEFAULT_ENGINE_RISK_CONFIG = {
    'default_base_contracts': 4,
    'anomaly_veto_threshold': 0.999,
    'min_multiplier': 0.25,
    'max_multiplier': 1.50,
    'max_contracts': 10,
    'anomaly_thresholds': {
        'warning': 0.95,
        'high': 0.99,
        'critical': 0.995,
        'veto': 0.999
    }
}
```

### 7.3 Critical Structural Warnings

```python
CRITICAL_STRUCTURAL_WARNINGS = {
    "VPIN_CRITICO",
    "CANCEL_RATE_CRITICO",
    "TOXICIDADE_CRITICA",
}
```

### 7.4 Position Sizing

| Multiplicador Final | Contratos (base=4) |
|---------------------|-------------------|
| 1.30 | 5 |
| 1.00 | 4 |
| 0.75 | 3 |
| 0.50 | 2 |
| 0.25 | 1 |
| < 0.25 | NO_TRADE |

---

## Changelog

### 23/12/2025
- Documento inicial consolidando MNQ Signal Generator, Master Engine, Iceberg Detector, Anomaly Detector e Regime Forecast

### Referências

- `ml_signal/mnq_signal_generator.py`
- `ml_engine/master_engine.py`
- `ml_iceberg/detection/iceberg_detector.py`
- `ml_iceberg/detection/heuristic_engine.py`
- `ml_anomaly/inference/anomaly_scorer.py`
- `ml_anomaly/inference/regime_detector.py`
- `ml_predictive/regime_forecast/provider.py`
- `ml_signal/movement_detector.py`
- `ml_signal/models/movement_types.py`
- `ml_signal/models/signal_output.py`
