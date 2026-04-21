"""
Anomaly Scorer - Servico de inferencia em tempo real.

Fornece:
- Score de anomalia para dados de microestrutura
- Classificacao binaria (anomalia / normal)
- Contribuicao de cada feature para o score
- Historico de scores para analise de tendencia
"""

import torch
import torch.nn as nn
from typing import Dict, Optional, List, Any, Union
from pathlib import Path
import numpy as np
from collections import deque
from datetime import datetime
import logging
import json

logger = logging.getLogger(__name__)


class AnomalyScorer:
    """
    Servico de scoring de anomalias em tempo real.

    Uso:
        scorer = AnomalyScorer.from_checkpoint('modelo.pt')
        score = scorer.score(microstructure_data)
    """

    def __init__(
        self,
        model: nn.Module,
        feature_names: List[str],
        feature_mean: torch.Tensor,
        feature_std: torch.Tensor,
        history_size: int = 1000,
        device: str = 'auto'
    ):
        """
        Args:
            model: Modelo treinado
            feature_names: Nomes das features na ordem esperada
            feature_mean: Media para normalizacao
            feature_std: Desvio padrao para normalizacao
            history_size: Tamanho do historico de scores
            device: Device para inferencia
        """
        if device == 'auto':
            self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        else:
            self.device = torch.device(device)

        self.model = model.to(self.device)
        self.model.eval()

        self.feature_names = feature_names
        self.feature_mean = feature_mean.to(self.device)
        self.feature_std = feature_std.to(self.device)

        # Historico
        self.score_history = deque(maxlen=history_size)
        self.alert_history = deque(maxlen=100)

        # Thresholds para alertas
        self.alert_thresholds = {
            'warning': 0.7,
            'critical': 0.9
        }

        # Estatisticas de runtime
        self.stats = {
            'total_scored': 0,
            'anomalies_detected': 0,
            'warnings': 0,
            'criticals': 0
        }

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_class: type = None,
        device: str = 'auto'
    ) -> 'AnomalyScorer':
        """
        Carrega scorer de um checkpoint.

        Args:
            checkpoint_path: Path para o arquivo .pt
            model_class: Classe do modelo (auto-detecta se None)
            device: Device para inferencia
        """
        checkpoint = torch.load(checkpoint_path, map_location='cpu')

        # Auto-detecta classe do modelo
        if model_class is None:
            class_name = checkpoint.get('model_class', 'MicrostructureAutoencoder')
            if class_name == 'MicrostructureAutoencoder':
                from ..models.autoencoder import MicrostructureAutoencoder
                model_class = MicrostructureAutoencoder
            elif class_name == 'VariationalAutoencoder':
                from ..models.variational import VariationalAutoencoder
                model_class = VariationalAutoencoder
            elif class_name == 'OrderBookAutoencoder':
                from ..models.autoencoder import OrderBookAutoencoder
                model_class = OrderBookAutoencoder
            else:
                raise ValueError(f"Classe de modelo desconhecida: {class_name}")

        # Infere parametros do modelo do state_dict
        state_dict = checkpoint['model_state_dict']

        # Encontra dimensoes
        encoder_keys = [k for k in state_dict.keys() if 'encoder' in k and 'weight' in k]
        if encoder_keys:
            first_weight = state_dict[encoder_keys[0]]
            input_dim = first_weight.shape[1]
        else:
            input_dim = 55  # default (35 originais + 20 novas features)

        latent_keys = [k for k in state_dict.keys() if 'latent' in k or 'to_latent' in k or 'fc_mu' in k]
        if latent_keys:
            for k in latent_keys:
                if 'weight' in k:
                    latent_dim = state_dict[k].shape[0]
                    break
        else:
            latent_dim = 8

        # Cria modelo
        model = model_class(input_dim=input_dim, latent_dim=latent_dim)
        model.load_state_dict(state_dict)

        # Feature names e stats
        feature_names = checkpoint.get('feature_names', model_class.FEATURE_NAMES[:input_dim]
                                       if hasattr(model_class, 'FEATURE_NAMES') else
                                       [f'feature_{i}' for i in range(input_dim)])

        feature_mean = checkpoint.get('feature_mean', torch.zeros(input_dim))
        feature_std = checkpoint.get('feature_std', torch.ones(input_dim))

        if isinstance(feature_mean, np.ndarray):
            feature_mean = torch.tensor(feature_mean, dtype=torch.float32)
        if isinstance(feature_std, np.ndarray):
            feature_std = torch.tensor(feature_std, dtype=torch.float32)

        return cls(
            model=model,
            feature_names=feature_names,
            feature_mean=feature_mean,
            feature_std=feature_std,
            device=device
        )

    def _prepare_input(self, data: Union[Dict, List, np.ndarray, torch.Tensor]) -> torch.Tensor:
        """Prepara input para o modelo."""
        if isinstance(data, dict):
            # Extrai features na ordem correta
            values = [float(data.get(f, 0.0)) for f in self.feature_names]
            tensor = torch.tensor(values, dtype=torch.float32)
        elif isinstance(data, list):
            if isinstance(data[0], dict):
                # Lista de dicionarios (batch)
                batch = []
                for d in data:
                    values = [float(d.get(f, 0.0)) for f in self.feature_names]
                    batch.append(values)
                tensor = torch.tensor(batch, dtype=torch.float32)
            else:
                tensor = torch.tensor(data, dtype=torch.float32)
        elif isinstance(data, np.ndarray):
            tensor = torch.tensor(data, dtype=torch.float32)
        else:
            tensor = data.float()

        # Garante 2D
        if tensor.dim() == 1:
            tensor = tensor.unsqueeze(0)

        # Normaliza
        tensor = (tensor - self.feature_mean) / (self.feature_std + 1e-8)

        return tensor.to(self.device)

    def score(self, data: Union[Dict, List, np.ndarray, torch.Tensor]) -> Dict[str, Any]:
        """
        Calcula score de anomalia para os dados.

        Args:
            data: Dados de microestrutura (dict, lista, array ou tensor)

        Returns:
            Dicionario com:
                - score: Score de anomalia [0, 1]
                - is_anomaly: Boolean
                - alert_level: 'normal', 'warning', ou 'critical'
                - reconstruction_error: Erro bruto de reconstrucao
                - timestamp: Timestamp da avaliacao
        """
        tensor = self._prepare_input(data)

        with torch.no_grad():
            # Score de anomalia
            if hasattr(self.model, 'anomaly_score'):
                scores = self.model.anomaly_score(tensor)
            else:
                errors = self.model.reconstruction_error(tensor)
                scores = torch.sigmoid((errors - self.model.mean_error) / (self.model.std_error + 1e-8))

            # Erro de reconstrucao
            if hasattr(self.model, 'reconstruction_error'):
                errors = self.model.reconstruction_error(tensor)
            else:
                errors = scores  # fallback

            # Classificacao
            if hasattr(self.model, 'is_anomaly'):
                is_anomaly = self.model.is_anomaly(tensor)
            else:
                is_anomaly = scores > 0.5

        # Processa resultados
        results = []
        for i in range(len(scores)):
            score_val = scores[i].item()
            error_val = errors[i].item()
            anomaly = is_anomaly[i].item() if is_anomaly.dim() > 0 else is_anomaly.item()

            # Alert level
            if score_val >= self.alert_thresholds['critical']:
                alert_level = 'critical'
                self.stats['criticals'] += 1
            elif score_val >= self.alert_thresholds['warning']:
                alert_level = 'warning'
                self.stats['warnings'] += 1
            else:
                alert_level = 'normal'

            result = {
                'score': score_val,
                'is_anomaly': anomaly,
                'alert_level': alert_level,
                'reconstruction_error': error_val,
                'timestamp': datetime.utcnow().isoformat()
            }
            results.append(result)

            # Atualiza estatisticas
            self.stats['total_scored'] += 1
            if anomaly:
                self.stats['anomalies_detected'] += 1

            # Historico
            self.score_history.append({
                'score': score_val,
                'timestamp': result['timestamp']
            })

            if alert_level != 'normal':
                self.alert_history.append(result)

        return results[0] if len(results) == 1 else results

    def score_with_contributions(
        self,
        data: Union[Dict, List, np.ndarray, torch.Tensor]
    ) -> Dict[str, Any]:
        """
        Calcula score com contribuicao de cada feature.
        Util para explicabilidade.
        """
        tensor = self._prepare_input(data)

        with torch.no_grad():
            # Reconstrucao
            if hasattr(self.model, 'forward'):
                reconstruction, latent = self.model(tensor)
            else:
                reconstruction = self.model(tensor)[0]
                latent = None

            # Erro por feature
            feature_errors = (reconstruction - tensor) ** 2

            # Score geral
            score_result = self.score(data)

        # Contribuicoes
        contributions = {}
        feature_errors_np = feature_errors[0].cpu().numpy()

        for i, name in enumerate(self.feature_names):
            contributions[name] = float(feature_errors_np[i])

        # Ordena por contribuicao
        sorted_contributions = dict(sorted(
            contributions.items(),
            key=lambda x: x[1],
            reverse=True
        ))

        if isinstance(score_result, list):
            score_result = score_result[0]

        return {
            **score_result,
            'feature_contributions': sorted_contributions,
            'top_anomalous_features': list(sorted_contributions.keys())[:5],
            'latent_representation': latent[0].cpu().numpy().tolist() if latent is not None else None
        }

    def batch_score(self, data_list: List[Dict]) -> List[Dict]:
        """Score para batch de dados."""
        return self.score(data_list)

    def get_score_trend(self, window: int = 100) -> Dict[str, float]:
        """
        Retorna tendencia dos scores recentes.

        Returns:
            mean: Media dos scores
            std: Desvio padrao
            trend: Tendencia (positivo = aumentando)
            max: Maximo recente
        """
        if len(self.score_history) < 2:
            return {'mean': 0, 'std': 0, 'trend': 0, 'max': 0}

        recent = list(self.score_history)[-window:]
        scores = [s['score'] for s in recent]

        mean = np.mean(scores)
        std = np.std(scores)
        max_score = np.max(scores)

        # Trend (regressao linear simples)
        if len(scores) > 10:
            x = np.arange(len(scores))
            trend = np.polyfit(x, scores, 1)[0]
        else:
            trend = 0

        return {
            'mean': float(mean),
            'std': float(std),
            'trend': float(trend),
            'max': float(max_score)
        }

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatisticas do scorer."""
        return {
            **self.stats,
            'history_size': len(self.score_history),
            'recent_alerts': len(self.alert_history),
            'score_trend': self.get_score_trend()
        }

    def set_alert_thresholds(self, warning: float = 0.7, critical: float = 0.9):
        """Define thresholds de alerta."""
        self.alert_thresholds['warning'] = warning
        self.alert_thresholds['critical'] = critical

    def reset_stats(self):
        """Reseta estatisticas."""
        self.stats = {
            'total_scored': 0,
            'anomalies_detected': 0,
            'warnings': 0,
            'criticals': 0
        }
        self.score_history.clear()
        self.alert_history.clear()


class GrenadierDefenseMode:
    """
    Sprint 3 — Z-Score Defense Mode (Fallback / Hard Shield).

    Guardrail determinístico que activa DEFENSE_MODE quando os features de
    microestrutura desviam significativamente das condições normais de mercado.

    Opera independentemente do autoencoder — sem inferência PyTorch.
    Carrega estatísticas de normalização de grenadier_scaler_4f.json.

    Triggers (Architect-approved 2026-04-11):
        norm_spread    > 3.0   → Spread severely widened
        norm_bid_depth < -3.0  → Bid liquidity collapse
        norm_ask_depth < -3.0  → Ask liquidity collapse
        |norm_imbalance| > 4.0 → Extreme directional pressure

    Usage:
        defense = GrenadierDefenseMode()
        result  = defense.check(spread=0.5, total_bid_depth=110,
                                total_ask_depth=108, book_imbalance=0.01)
        if result["defense_mode"]:
            block_entry(reason=result["trigger_reason"])
    """

    SCALER_PATH = Path(
        r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly\models\grenadier_scaler_4f.json"
    )

    # Thresholds (Architect-approved 2026-04-11)
    THRESH_SPREAD_HI     =  3.0   # z-score upper bound for spread
    THRESH_DEPTH_LO      = -3.0   # z-score lower bound for bid/ask depth
    THRESH_IMBALANCE_ABS =  4.0   # |z-score| bound for book imbalance

    # Tier 2: DEFENSIVE_EXIT thresholds (2026-04-14, shadow mode)
    # TIGHT-B: requires BOTH >=2 triggers AND extreme z-score (AND logic)
    # 9-month replay: 530 events, 5.0% protection, 46.3% false alarm (best balance)
    TIER2_MIN_TRIGGERS   = 2      # minimum simultaneous triggers
    TIER2_EXTREME_MULT   = 2.0    # z-score multiplier for extreme classification
    TIER2_REQUIRE_BOTH   = True   # TIGHT-B: both conditions required (AND)

    def __init__(self, scaler_path: Optional[Path] = None):
        path = Path(scaler_path) if scaler_path else self.SCALER_PATH
        with open(path) as fh:
            data = json.load(fh)
        sc = data["scaler"]
        self._mean: dict = {
            "spread"          : sc["spread"]["mean"],
            "total_bid_depth" : sc["total_bid_depth"]["mean"],
            "total_ask_depth" : sc["total_ask_depth"]["mean"],
            "book_imbalance"  : sc["book_imbalance"]["mean"],
        }
        self._std: dict = {
            "spread"          : sc["spread"]["std"],
            "total_bid_depth" : sc["total_bid_depth"]["std"],
            "total_ask_depth" : sc["total_ask_depth"]["std"],
            "book_imbalance"  : sc["book_imbalance"]["std"],
        }
        logger.info(
            "GrenadierDefenseMode loaded — "
            "spread(μ=%.3f σ=%.3f) bid(μ=%.1f σ=%.1f) "
            "ask(μ=%.1f σ=%.1f) imb(μ=%.4f σ=%.4f)",
            self._mean["spread"],          self._std["spread"],
            self._mean["total_bid_depth"], self._std["total_bid_depth"],
            self._mean["total_ask_depth"], self._std["total_ask_depth"],
            self._mean["book_imbalance"],  self._std["book_imbalance"],
        )

    def _z(self, feature: str, value: float) -> float:
        return (value - self._mean[feature]) / (self._std[feature] + 1e-10)

    def check(
        self,
        spread: float,
        total_bid_depth: float,
        total_ask_depth: float,
        book_imbalance: float,
    ) -> dict:
        """
        Calcula z-scores e avalia triggers de defense mode.

        Args:
            spread          : bid-ask spread em USD/oz
            total_bid_depth : soma dos 10 níveis bid em contratos
            total_ask_depth : soma dos 10 níveis ask em contratos
            book_imbalance  : (bid - ask) / (bid + ask)

        Returns dict com:
            defense_mode   : bool  — True = vetar qualquer nova entrada
            trigger_reason : str   — descrição legível dos triggers
            z_spread       : float
            z_bid_depth    : float
            z_ask_depth    : float
            z_imbalance    : float
        """
        z_spread = self._z("spread",          spread)
        z_bid    = self._z("total_bid_depth",  total_bid_depth)
        z_ask    = self._z("total_ask_depth",  total_ask_depth)
        z_imb    = self._z("book_imbalance",   book_imbalance)

        triggers: list = []
        if z_spread > self.THRESH_SPREAD_HI:
            triggers.append(f"spread_widening(z={z_spread:.2f})")
        if z_bid < self.THRESH_DEPTH_LO:
            triggers.append(f"bid_collapse(z={z_bid:.2f})")
        if z_ask < self.THRESH_DEPTH_LO:
            triggers.append(f"ask_collapse(z={z_ask:.2f})")
        if abs(z_imb) > self.THRESH_IMBALANCE_ABS:
            triggers.append(f"extreme_imbalance(z={z_imb:.2f})")

        defense_mode   = len(triggers) > 0
        trigger_reason = " | ".join(triggers) if triggers else "NORMAL"

        # ── Tier classification (2026-04-14, TIGHT-B) ──
        # NORMAL       : no triggers
        # ENTRY_BLOCK  : 1+ triggers (blocks new entries)
        # DEFENSIVE_EXIT : TIGHT-B = >=2 triggers AND extreme z-score
        n_triggers = len(triggers)
        any_extreme = (
            z_spread > self.THRESH_SPREAD_HI * self.TIER2_EXTREME_MULT
            or z_bid < self.THRESH_DEPTH_LO * self.TIER2_EXTREME_MULT
            or z_ask < self.THRESH_DEPTH_LO * self.TIER2_EXTREME_MULT
            or abs(z_imb) > self.THRESH_IMBALANCE_ABS * self.TIER2_EXTREME_MULT
        )
        multi_trigger = n_triggers >= self.TIER2_MIN_TRIGGERS

        if self.TIER2_REQUIRE_BOTH:
            is_tier2 = multi_trigger and any_extreme
        else:
            is_tier2 = multi_trigger or any_extreme

        if is_tier2:
            defense_tier = "DEFENSIVE_EXIT"
        elif n_triggers >= 1:
            defense_tier = "ENTRY_BLOCK"
        else:
            defense_tier = "NORMAL"

        # ── Directional stress classification (2026-04-14) ──
        # Determines which open positions are at risk based on WHERE the
        # stress is coming from in the order book.
        #
        # bid_collapse (z_bid << 0)  -> buyers disappearing -> price will DROP -> EXIT_LONG
        # ask_collapse (z_ask << 0)  -> sellers disappearing -> price will SPIKE -> EXIT_SHORT
        # imbalance < 0 (bid < ask)  -> selling pressure -> EXIT_LONG
        # imbalance > 0 (bid > ask)  -> buying pressure  -> EXIT_SHORT
        # spread_widening alone      -> liquidity vacuum -> EXIT_ALL
        # mixed signals              -> EXIT_ALL
        #
        # Only classified when defense_tier == DEFENSIVE_EXIT
        stress_direction = "HOLD"
        if defense_tier == "DEFENSIVE_EXIT":
            bearish_signals = 0
            bullish_signals = 0

            if z_bid < self.THRESH_DEPTH_LO:
                bearish_signals += 1  # bid collapse -> price drops
            if z_ask < self.THRESH_DEPTH_LO:
                bullish_signals += 1  # ask collapse -> price spikes
            if z_imb < -self.THRESH_IMBALANCE_ABS:
                bearish_signals += 1  # selling pressure
            if z_imb > self.THRESH_IMBALANCE_ABS:
                bullish_signals += 1  # buying pressure

            if bearish_signals > 0 and bullish_signals == 0:
                stress_direction = "EXIT_LONG"
            elif bullish_signals > 0 and bearish_signals == 0:
                stress_direction = "EXIT_SHORT"
            elif bearish_signals > 0 and bullish_signals > 0:
                stress_direction = "EXIT_ALL"  # conflicting -> full exit
            else:
                # Only spread widening, no directional signal
                stress_direction = "EXIT_ALL"

        return {
            "defense_mode"      : defense_mode,
            "defense_tier"      : defense_tier,
            "stress_direction"  : stress_direction,
            "trigger_reason"    : trigger_reason,
            "n_triggers"        : n_triggers,
            "any_extreme"       : any_extreme,
            "z_spread"          : round(z_spread, 3),
            "z_bid_depth"       : round(z_bid,    3),
            "z_ask_depth"       : round(z_ask,    3),
            "z_imbalance"       : round(z_imb,    3),
        }
