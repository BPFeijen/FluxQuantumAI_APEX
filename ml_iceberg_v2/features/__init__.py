"""
features/ — Raw signal extraction from L2 order book data.

Components
----------
dom_convention_gate : Validates/corrects ASK-BID vs BID-ASK convention before processing.
refill_detector     : Core iceberg detection — identifies RefillEvents and chains them
                      into IcebergChains using the CME/DXFeed paper algorithm.
feature_extractor   : Converts RefillEvents into 16-feature windows (5s, step 1s).
label_generator     : Assigns ICEBERG_NATIVE / SYNTHETIC / ABSORPTION / NOISE labels
                      based on CME paper rules applied to IcebergChains.
"""
