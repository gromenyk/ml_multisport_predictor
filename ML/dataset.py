from pathlib import Path
from datetime import date, timedelta
import json
import os


class TrainingDataset:

    def __init__(self, data_dir, context_weeks=4, cutoff_date=None, planned_history_dir=None):
        self.data_dir = Path(data_dir)
        self.planned_history_dir = Path(planned_history_dir) if planned_history_dir else None
        self.context_weeks = context_weeks
        self.cutoff_date = cutoff_date or os.getenv("TRAINING_CUTOFF_DATE")

        self.weeks = self._load_weeks()

    def _load_weeks(self):
        by_start = {}
        sources = ((self.data_dir, "human_plan"),)
        if self.planned_history_dir is not None:
            sources += ((self.planned_history_dir, "adaptive_approved"),)
        for directory, default_source in sources:
            if not directory.exists():
                continue
            for file in sorted(directory.glob("*.json")):
                with open(file, "r", encoding="utf-8") as handle:
                    week = json.load(handle)
                if self.cutoff_date and week.get("week_end", "") > self.cutoff_date:
                    continue
                if not week.get("week_start") or not week.get("week_end"):
                    continue
                week = dict(week)
                metadata = dict(week.get("metadata") or {})
                metadata.setdefault("source", default_source)
                week["metadata"] = metadata
                # An explicit caller-supplied overlay can take precedence
                # without modifying the original human plan files. The
                # production next-week predictor intentionally uses no overlay.
                by_start[week["week_start"]] = week
        return sorted(by_start.values(), key=lambda week: week["week_start"])

    def build_examples(self):

        examples = []

        for target_idx in range(self.context_weeks, len(self.weeks)):

            history = self.weeks[
                target_idx - self.context_weeks:target_idx
            ]

            target_week = self.weeks[target_idx]

            # Un ejemplo por día
            for day in range(7):

                examples.append(
                    {
                        "history": history,
                        "target_week": target_week,
                        "target_day": day
                    }
                )

        return examples


def validate_consecutive_context(weeks, required=4):
    context = list(weeks[-required:])
    if len(context) < required:
        raise ValueError(f"Se requieren al menos {required} semanas planificadas.")
    for previous, current in zip(context, context[1:]):
        expected = date.fromisoformat(previous["week_end"]) + timedelta(days=1)
        if date.fromisoformat(current["week_start"]) != expected:
            raise ValueError(
                f"El contexto tiene un salto entre {previous['week_end']} y {current['week_start']}."
            )
    return context


if __name__ == "__main__":

    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    dataset = TrainingDataset(
        data_dir=PROJECT_ROOT / "data" / "processed_data",
        context_weeks=4
    )

    examples = dataset.build_examples()

    print(f"Examples: {len(examples)}")

    print()

    first = examples[0]

    print("History:")
    for week in first["history"]:
        print(
            week["week_start"],
            "->",
            week["week_end"]
        )

    print()

    print("Target week:")
    print(first["target_week"]["week_start"])

    print()

    print("Target day:")
    print(first["target_day"])
