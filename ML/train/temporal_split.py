import numpy as np
import os


def temporal_train_test_split(X, y, target_week_starts, all_weeks, train_ratio=0.8):
    """Split examples using one chronological cutoff shared by all models."""
    if os.getenv("TRAIN_MODE", "evaluation") == "production":
        return X, X, y, y, "100% history"

    cutoff_index = int(len(all_weeks) * train_ratio)
    cutoff_week = all_weeks[cutoff_index]["week_start"]
    target_week_starts = np.asarray(target_week_starts)

    train_mask = target_week_starts < cutoff_week
    test_mask = ~train_mask

    return (
        X[train_mask],
        X[test_mask],
        y[train_mask],
        y[test_mask],
        cutoff_week,
    )
