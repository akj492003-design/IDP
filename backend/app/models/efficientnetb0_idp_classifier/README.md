# IDP EfficientNetB0 Classifier — Stage 11

## Model identity

- Model name: IDP EfficientNetB0
- Model version: 1.0.0
- Framework package metadata: TensorFlow 2.10.1 / Keras 2.10.0
- SavedModel package runtime requirement in this project: `tensorflow-cpu==2.15.1` (target-container load must be smoke-tested)
- Classes:
  - `0`: Resume
  - `1`: Invoice
  - `2`: Form

## Package contents

- `saved_model/`: TensorFlow SavedModel used for serving.
- `weights/efficientnetb0_final.weights.h5`: final checkpoint asset.
- `config/class_mapping.json`: authoritative model class mapping.
- `config/preprocessing_config.json`: canonical preprocessing contract.
- `config/model_metadata.json`: model metadata and Stage 10 held-out test metrics.
- `inference.py`: cached/reusable inference wrapper.

## Input contract

The classifier expects a Stage 4 canonical grayscale PNG, not an OCR-enhanced image.

1. Decode as one grayscale channel.
2. Resize to 224 × 224 with bilinear interpolation and antialiasing.
3. Convert grayscale to RGB.
4. Convert to float32 and retain values in `[0, 255]`.
5. Do not apply external rescaling or `MobileNetV2 preprocess_input`.
6. The model returns three logits; the wrapper applies softmax once.

## Runtime usage

```python
from app.models.efficientnetb0_idp_classifier.inference import IDPDocumentClassifier

classifier = IDPDocumentClassifier(
    model_dir="app/models/efficientnetb0_idp_classifier/saved_model"
)

prediction = classifier.predict_canonical_png("path/to/page_0001_canonical.png")
print(prediction)
```

The wrapper returns the predicted ID/class, confidence, per-class probabilities, input-preprocessing duration and TensorFlow forward-pass duration.

## Evaluation

Stage 9 development OOF comparison and Stage 10 independent held-out test results are stored in `backend/app/data/model_metrics.json`. Stage 10 contains 1,127 test records and the confusion matrix in Form, Invoice, Resume order.

The runtime benchmark for all seven Stage 9 candidates is not available. Only this EfficientNetB0 package was supplied; do not infer other model timings from OCR or document processing.
