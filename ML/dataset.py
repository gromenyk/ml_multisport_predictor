from pathlib import Path
import json


class TrainingDataset:

    def __init__(self, data_dir, context_weeks=4):
        self.data_dir = Path(data_dir)
        self.context_weeks = context_weeks

        self.weeks = self._load_weeks()

    def _load_weeks(self):
        weeks = []

        for file in sorted(self.data_dir.glob("*.json")):

            with open(file, "r", encoding="utf-8") as f:
                week = json.load(f)

            weeks.append(week)

        weeks.sort(key=lambda w: w["week_start"])

        return weeks

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