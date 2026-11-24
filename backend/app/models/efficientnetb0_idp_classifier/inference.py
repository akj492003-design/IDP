import json
import time
from pathlib import Path

import numpy as np
import tensorflow as tf


PACKAGE_DIR = Path(__file__).resolve().parent

SAVED_MODEL_DIR = PACKAGE_DIR / "saved_model"

CONFIG_DIR = PACKAGE_DIR / "config"


class IDPDocumentClassifier:

    def __init__(self, model_dir=None):

        self.model_dir = (
            Path(model_dir)
            if model_dir is not None
            else SAVED_MODEL_DIR
        )

        if not self.model_dir.is_dir():
            raise FileNotFoundError(
                f"Model directory not found: {self.model_dir}"
            )

        self.model = tf.keras.models.load_model(
            str(self.model_dir),
            compile=False
        )

        with open(
            CONFIG_DIR / "class_mapping.json",
            "r",
            encoding="utf-8"
        ) as file:

            mapping = json.load(file)

        self.class_names = {
            int(key): value
            for key, value in mapping.items()
        }

        self.image_size = (224, 224)

    def preprocess_grayscale_array(self, image):

        image = np.asarray(image)

        if image.ndim == 3:

            if image.shape[-1] == 1:
                image = np.squeeze(
                    image,
                    axis=-1
                )

            else:
                raise ValueError(
                    "Expected a canonical grayscale image. "
                    "Convert the document to grayscale before inference."
                )

        if image.ndim != 2:
            raise ValueError(
                f"Expected a 2D grayscale image, got {image.shape}"
            )

        if not np.isfinite(image).all():
            raise ValueError(
                "Image contains invalid pixel values."
            )

        if image.min() < 0 or image.max() > 255:
            raise ValueError(
                "Expected input pixel range [0, 255]."
            )

        image = tf.convert_to_tensor(
            image[..., np.newaxis],
            dtype=tf.float32
        )

        image = tf.image.resize(
            image,
            self.image_size,
            method="bilinear",
            antialias=True
        )

        image = tf.image.grayscale_to_rgb(
            image
        )

        image = tf.expand_dims(
            image,
            axis=0
        )

        return image

    def predict_grayscale_array(self, image):

        preprocessing_started = time.perf_counter()
        batch = self.preprocess_grayscale_array(
            image
        )
        preprocessing_time_ms = (
            time.perf_counter() - preprocessing_started
        ) * 1000

        inference_started = time.perf_counter()
        logits = self.model(
            batch,
            training=False
        )
        logits = logits.numpy()[0]
        inference_time_ms = (
            time.perf_counter() - inference_started
        ) * 1000

        if not np.isfinite(logits).all():
            raise RuntimeError(
                "Model returned invalid logits."
            )

        probabilities = tf.nn.softmax(
            logits,
            axis=-1
        ).numpy()

        predicted_label = int(
            np.argmax(probabilities)
        )

        return {
            "predicted_label": predicted_label,
            "predicted_class": self.class_names[predicted_label],
            "confidence": float(
                probabilities[predicted_label]
            ),
            "probabilities": {
                self.class_names[index]: float(
                    probabilities[index]
                )
                for index in range(len(probabilities))
            },
            "preprocessing_time_ms": round(
                preprocessing_time_ms, 3
            ),
            "inference_time_ms": round(
                inference_time_ms, 3
            ),
        }

    def predict_canonical_pngs(self, image_paths):
        """Classify a batch of canonical grayscale PNG pages in one model call."""
        paths = [Path(path) for path in image_paths]
        if not paths:
            return []

        preprocessing_started = time.perf_counter()
        prepared = []
        for image_path in paths:
            if not image_path.is_file():
                raise FileNotFoundError(image_path)

            image_bytes = tf.io.read_file(str(image_path))
            image = tf.io.decode_png(image_bytes, channels=1)
            image = tf.squeeze(image, axis=-1).numpy()
            prepared.append(self.preprocess_grayscale_array(image))

        batch = tf.concat(prepared, axis=0)
        preprocessing_time_ms = (
            time.perf_counter() - preprocessing_started
        ) * 1000

        inference_started = time.perf_counter()
        logits = self.model(batch, training=False)
        logits = logits.numpy()
        inference_time_ms = (
            time.perf_counter() - inference_started
        ) * 1000

        if logits.ndim != 2 or logits.shape[0] != len(paths):
            raise RuntimeError("Model returned an invalid batched logits shape.")
        if not np.isfinite(logits).all():
            raise RuntimeError("Model returned invalid logits.")

        probabilities = tf.nn.softmax(logits, axis=-1).numpy()
        per_page_inference_ms = inference_time_ms / len(paths)
        per_page_preprocessing_ms = preprocessing_time_ms / len(paths)

        results = []
        for row in probabilities:
            predicted_label = int(np.argmax(row))
            results.append({
                "predicted_label": predicted_label,
                "predicted_class": self.class_names[predicted_label],
                "confidence": float(row[predicted_label]),
                "probabilities": {
                    self.class_names[index]: float(row[index])
                    for index in range(len(row))
                },
                # The batch forward-pass duration is evenly apportioned for
                # per-page telemetry; summing these values gives batch time.
                "preprocessing_time_ms": round(per_page_preprocessing_ms, 3),
                "inference_time_ms": round(per_page_inference_ms, 3),
            })

        return results

    def predict_canonical_png(self, image_path):
        """Backward-compatible single-page inference wrapper."""
        return self.predict_canonical_pngs([image_path])[0]
