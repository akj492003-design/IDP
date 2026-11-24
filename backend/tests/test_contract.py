from pathlib import Path
import json


BACKEND = Path(__file__).parents[1]
DATA = BACKEND / "app" / "data" / "model_metrics.json"
MODEL_PACKAGE = BACKEND / "app" / "models" / "efficientnetb0_idp_classifier"


def test_stage9_development_oof_contains_seven_models():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    evaluation = data["evaluations"]["development_oof"]
    models = evaluation["models"]

    assert data["source_notebook"] == "ml_dl(10).ipynb"
    assert evaluation["samples"] == 6387
    assert evaluation["folds"] == 5
    assert evaluation["metric_type"] == "development_oof"
    assert len(models) == 7
    assert {item["model"] for item in models} == {
        "EfficientNetB0",
        "MobileNetV2",
        "ResNet18",
        "Linear SVM",
        "Logistic Regression",
        "Gaussian NB",
        "KNN",
    }

    for model in models:
        for metric in ("accuracy", "precision", "recall", "f1_score"):
            assert 0 <= model[metric] <= 1


def test_stage10_test_metrics_and_confusion_matrix_are_independent():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    evaluation = data["evaluations"]["final_test"]
    model = evaluation["models"][0]

    assert evaluation["samples"] == 1127
    assert evaluation["correct_predictions"] == 1038
    assert evaluation["incorrect_predictions"] == 89
    assert model["model"] == "EfficientNetB0"
    assert model["test_samples"] == 1127
    assert model["correct_predictions"] == 1038
    assert model["incorrect_predictions"] == 89
    assert model["class_mapping"] == {
        "resume": 0,
        "invoice": 1,
        "form": 2,
    }

    matrix = model["confusion_matrix"]
    assert matrix["labels"] == ["form", "invoice", "resume"]
    assert matrix["rows"] == [
        [326, 29, 20],
        [32, 339, 1],
        [4, 3, 373],
    ]
    assert sum(sum(row) for row in matrix["rows"]) == 1127
    assert sum(matrix["rows"][i][i] for i in range(3)) == 1038


def test_stage11_model_package_and_mapping_exist():
    assert (MODEL_PACKAGE / "inference.py").is_file()
    assert (MODEL_PACKAGE / "saved_model" / "saved_model.pb").is_file()
    assert (MODEL_PACKAGE / "saved_model" / "variables" / "variables.index").is_file()
    assert (MODEL_PACKAGE / "weights" / "efficientnetb0_final.weights.h5").is_file()
    assert (MODEL_PACKAGE / "config" / "preprocessing_config.json").is_file()

    mapping = json.loads(
        (MODEL_PACKAGE / "config" / "class_mapping.json").read_text(encoding="utf-8")
    )
    assert mapping == {
        "0": "resume",
        "1": "invoice",
        "2": "form",
    }


def test_unavailable_seven_model_benchmark_is_not_fabricated():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    benchmark = data["inference_benchmark"]
    assert benchmark["status"] == "unavailable"
    assert benchmark["records"] == []
    assert "Benchmark data unavailable" in benchmark["message"]


def test_legacy_classifier_assets_are_not_used():
    assert not (BACKEND / "app" / "assets" / "models").exists()
