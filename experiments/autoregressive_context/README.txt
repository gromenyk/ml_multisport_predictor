AUTOREGRESSIVE CONTEXT EXPERIMENT
=================================

Purpose
-------
Convert an archived weekly prediction into the normalized weekly structure
expected by the existing model context loader.

This directory is intentionally isolated from the production data pipeline.
The script does not modify predictions and does not copy anything into
data/processed_data automatically.


Directory layout
----------------
input/
    Manually copy one or more original prediction JSON files here.

output/
    The script creates normalized context weeks here. Each output is named
    using its week start date, for example 2026-04-06.json.

adapt_prediction.py
    Performs and validates the conversion.


Usage
-----
From the Project directory:

    python experiments/autoregressive_context/adapt_prediction.py

To convert one specific file without copying it into input first:

    python experiments/autoregressive_context/adapt_prediction.py PATH_TO_FILE

The script never overwrites an existing output. Remove or move the existing
experimental output deliberately before generating that same week again.


Manual rollout workflow
-----------------------
1. Freeze production models trained only with human weeks through the chosen
   historical cutoff.
2. Generate the next prediction.
3. Copy its JSON into input/.
4. Run adapt_prediction.py.
5. Inspect the JSON created in output/.
6. Manually move that JSON into data/processed_data when it should become one
   of the four context weeks.
7. Generate the following week without retraining the frozen models.
8. Repeat.

Do not run the adapted JSON through PDF extraction or normalization. It is
already in normalized context format.


Important experimental assumption
---------------------------------
The prediction does not contain week_type, while several existing downstream
models expect this feature in context. The adapter assigns "Otros", which is
the fallback already used by the prediction pipeline, and records that
assumption in metadata.

This experiment tests autoregression through context only. Retraining models
with generated weeks would additionally test self-training and would confound
the result.
