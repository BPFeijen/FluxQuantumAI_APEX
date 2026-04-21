"""
prediction/ — Real-time inference helpers used by IcebergInference.

Components
----------
volume_predictor  : Estimates peak iceberg volume from partial chain observations.
absorption_state  : Tracks AbsorptionState transitions (NONE→ACTIVE→FADING→COMPLETE)
                    based on refill rate and volume-absorbed metrics.
"""
