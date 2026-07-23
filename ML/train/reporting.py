from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score


def print_multiclass_summary(model_name, y_true, y_pred):
    print("\nMODEL_SUMMARY_START")
    print(f"Model       : {model_name}")
    print(f"Test samples: {len(y_true)}")
    print(f"Accuracy    : {accuracy_score(y_true, y_pred):.4f}")
    print(f"Macro F1    : {f1_score(y_true, y_pred, average='macro', zero_division=0):.4f}")
    print(f"Weighted F1 : {f1_score(y_true, y_pred, average='weighted', zero_division=0):.4f}")
    print("Status      : SUCCESS")
    print("MODEL_SUMMARY_END")


def print_binary_summary(model_name, y_true, y_pred):
    print("\nMODEL_SUMMARY_START")
    print(f"Model       : {model_name}")
    print(f"Test samples: {len(y_true)}")
    print(f"Accuracy    : {accuracy_score(y_true, y_pred):.4f}")
    print(f"Precision   : {precision_score(y_true, y_pred, zero_division=0):.4f}")
    print(f"Recall      : {recall_score(y_true, y_pred, zero_division=0):.4f}")
    print(f"Positive F1 : {f1_score(y_true, y_pred, zero_division=0):.4f}")
    print("Status      : SUCCESS")
    print("MODEL_SUMMARY_END")
