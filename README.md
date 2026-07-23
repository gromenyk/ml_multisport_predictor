# Training Planner

A personal prototype designed to learn a multisport coach's historical
planning style and generate a training plan for the following week.

The application can import weekly plans from PDF files, normalize their
contents, train and evaluate several predictive models, and build a new week
using the trained models.

## Workflow

The user interface organizes the process into six steps:

1. Upload a weekly training plan in PDF format.
2. Extract and normalize its contents into JSON.
3. Train evaluation models using a chronological 80/20 split.
4. Evaluate the pipeline's style on unseen historical weeks.
5. Train production models using 100% of the available history.
6. Predict the following week using the production models and the latest four
   weeks as context.

The models predict the different components of each training session:

- Sport.
- Session type.
- Structured or unstructured session.
- Objective family.
- Terrain.
- Zone.
- Distance.

`week_type` is retained as a historical feature, but it is neither requested
manually nor predicted as a separate output.

## Project structure

```text
Project/
├── app/
│   ├── app.py                    # Streamlit user interface
│   ├── config.py                 # Paths and directory initialization
│   └── backend/                  # Extraction and normalization
├── data/
│   ├── raw_data/                 # Original PDF files
│   └── processed_data/           # Normalized weekly JSON files
├── ML/
│   ├── train/                    # Individual model training
│   ├── models/                   # Generated model files
│   ├── results/                  # Evaluations and predictions
│   ├── evaluate_pipeline.py      # Historical pipeline evaluation
│   ├── style_score.py            # Historical style similarity
│   └── predict_next_week.py      # Next-week prediction
└── prompts/                      # Extraction prompt and schema
```

Generated data, models, and results are excluded from Git through
`.gitignore`.

## Requirements

- Python 3.10 or a compatible version.
- An OpenAI API key for extracting information from PDF files.
- PDF files containing selectable and extractable text.

Main dependencies:

```text
streamlit
openai
python-dotenv
PyMuPDF
numpy
pandas
scikit-learn
xgboost
joblib
```

The current extractor does not include OCR. A PDF made entirely from images
will produce zero extracted characters and must first be converted into
searchable text.

## Local installation

From the `Project` directory, activate the desired Python environment and
install the dependencies:

```powershell
pip install streamlit openai python-dotenv PyMuPDF numpy pandas scikit-learn xgboost joblib
```

Create a `.env` file in the project root:

```dotenv
OPENAI_API_KEY=your_api_key
```

The `.env` file is excluded from the repository.

## Running the application

From the project root:

```powershell
streamlit run app/app.py
```

Then follow the steps in the user interface in order. Evaluation and production
training are separate processes:

- `evaluation` models use the first 80% of the history for training and the
  latest 20% for evaluation.
- `production` models use all available history to generate the next-week
  prediction.

## Style Score

The Style Score does not determine whether a training week is optimal. It
measures how closely the predictions resemble the statistical style of the
historical plans.

It compares:

- Class distributions.
- Transitions between sessions.
- Weekday profiles.
- Repetition.
- Sport and session-type consistency.
- The pipeline's joint distribution.

It should be interpreted alongside the individual model metrics, particularly
when some classes are infrequent.

## Local files

The following paths contain generated or potentially private information and
are not tracked:

```text
.env
data/raw_data/
data/processed_data/
data/temp/
ML/models/
ML/results/
ML/train/*_predictions.txt
```

The data directories are retained in Git only through `.gitkeep` files.

## Status

This is an experimental project for personal use. Its current purpose is to
test whether the pipeline can reproduce the observed planning style in a useful way. It is not prepared as a public product or as a sports-prescription system.
