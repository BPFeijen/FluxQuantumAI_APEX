"""
training/ — Dataset loading and training loops for each pipeline stage.

Components
----------
dataset        : PyTorch Dataset — loads feature/label Parquet files, applies
                 normalisation and class-weighted sampling.
trainer_stage1 : Autoencoder training loop (reconstruction loss, MSE).
trainer_stage2 : Classifier training loop (cross-entropy, focal loss option).
trainer_stage3 : Platt scaling calibration loop (NLL minimisation).
"""
