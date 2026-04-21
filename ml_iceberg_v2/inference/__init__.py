"""
inference/ — Runtime inference pipeline: loads trained models and produces
             IcebergOutputV2 signals from streaming or batch L2 data.

Components
----------
iceberg_inference : Orchestrates DOM convention gate → feature extraction →
                    autoencoder scoring → classifier → calibration → output.
"""
