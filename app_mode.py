import math
import re
import sys
import traceback
import warnings
from pathlib import Path

import gradio as gr
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import _column_transformer as sklearn_column_transformer

try:
    import joblib
except ModuleNotFoundError:  # pragma: no cover - optional import guard
    joblib = None


PROPERTY_TYPE_COL = "Loại hình"
ADDRESS_TEXT_COLUMNS = [
    "Đường",
    "Số nhà",
    "Phường/Xã cũ",
    "Phường mới",
    "Huyện/Quận cũ",
    "Tỉnh/Thành phố cũ",
    "Tỉnh/Thành phố mới",
]
ICON_PATTERN = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+", re.UNICODE)
LEADING_TRAILING_DECORATION_PATTERN = re.compile(r"^[\s,.;:|/\-]+|[\s,.;:|/\-]+$", re.UNICODE)

if not hasattr(sklearn_column_transformer, "_RemainderColsList"):
    class _RemainderColsList(list):
        pass


    sklearn_column_transformer._RemainderColsList = _RemainderColsList


def normalize_text_value(value):
    if pd.isna(value):
        return np.nan

    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null"}:
        return np.nan

    text = ICON_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = LEADING_TRAILING_DECORATION_PATTERN.sub("", text).strip()
    return text if text else np.nan


def sanitize_location_columns(df: pd.DataFrame, columns=None) -> pd.DataFrame:
    cleaned_df = df.copy()
    for column in columns or ADDRESS_TEXT_COLUMNS:
        if column in cleaned_df.columns:
            cleaned_df[column] = cleaned_df[column].apply(normalize_text_value)
    return cleaned_df


class MatrixToFrameTransformer(BaseEstimator, TransformerMixin):
    def fit(self, X, y=None):
        if hasattr(X, "columns"):
            self.feature_names_in_ = list(X.columns)
        elif not hasattr(self, "feature_names_in_"):
            self.feature_names_in_ = None
        return self

    def transform(self, X):
        if hasattr(X, "columns") or self.feature_names_in_ is None:
            return X
        if sparse.issparse(X):
            return pd.DataFrame.sparse.from_spmatrix(X, columns=self.feature_names_in_)
        X_array = np.asarray(X)
        if X_array.ndim == 2 and X_array.shape[1] == len(self.feature_names_in_):
            return pd.DataFrame(X_array, columns=self.feature_names_in_)
        return X


sys.modules.setdefault("train_combo4_xgboost_pipeline", sys.modules[__name__])
sys.modules.setdefault("model_runtime", sys.modules[__name__])
sys.modules.setdefault("__main__", sys.modules[__name__])

MODEL_PATH = Path("artifacts/combo4_best_unit_price_pipeline.pkl")
DATA_PATH = Path("advertisement.csv")

MODEL_CACHE: dict = {"bundle": None, "error": ""}

AREA_FIELD = "Diện tích"
CITY_FIELD = "Tỉnh/Thành phố cũ"
DISTRICT_FIELD = "Huyện/Quận cũ"
OLD_WARD_FIELD = "Phường/Xã cũ"
NEW_CITY_FIELD = "Tỉnh/Thành phố mới"
NEW_WARD_FIELD = "Phường mới"
STREET_FIELD = "Đường"
BEDROOM_FIELD = "Số phòng ngủ"
BATHROOM_FIELD = "Số phòng vệ sinh"

SUBTYPE_FIELDS = [
    "Loại hình nhà ở",
    "Loại hình căn hộ",
    "Loại hình đất",
    "Loại hình văn phòng",
]

PROPERTY_TYPE_CHOICES = [
    "Căn hộ/Chung cư",
    "Đất",
    "Nhà ở",
    "Văn phòng, Mặt bằng kinh doanh",
]
BEDROOM_COUNT_CHOICES = ["0", *[str(value) for value in range(1, 11)], "Nhiều hơn 10"]
BATHROOM_COUNT_CHOICES = [str(value) for value in range(0, 7)] + ["Nhiều hơn 6"]


def load_artifact():
    if joblib is None:
        raise RuntimeError("Missing Python package: joblib.")

    with MODEL_PATH.open("rb") as artifact_file:
        header = artifact_file.read(64)

    if header.startswith(b"version https://git-lfs.github.com/spec/"):
        raise RuntimeError(
            "Model artifact was not downloaded. The file in artifacts is still a Git LFS pointer."
        )

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*If you are loading a serialized model.*",
            category=UserWarning,
            module=r"xgboost\.core",
        )
        return joblib.load(MODEL_PATH)


def load_raw_address_data():
    df = pd.read_csv(DATA_PATH, low_memory=False, encoding="utf-8-sig")
    return sanitize_location_columns(df)


def load_model_bundle():
    cached = MODEL_CACHE.get("bundle")
    cached_error = MODEL_CACHE.get("error", "")
    if cached is not None and not cached_error:
        return cached, "Model đã được tải.", True

    if not MODEL_PATH.exists():
        error = "Model artifact chưa tồn tại: thiếu file artifacts/combo4_best_unit_price_pipeline.pkl"
        MODEL_CACHE["bundle"] = None
        MODEL_CACHE["error"] = error
        return None, error, False
    if not DATA_PATH.exists():
        error = "Dữ liệu địa chỉ chưa tồn tại: thiếu file advertisement.csv"
        MODEL_CACHE["bundle"] = None
        MODEL_CACHE["error"] = error
        return None, error, False

    try:
        artifact = load_artifact()
        raw_address_df = load_raw_address_data()
        feature_list = artifact["feature_list"]
        target_mode = artifact.get("target_mode", "price_direct")
        unit_price_scale = float(artifact.get("unit_price_scale", 1.0))
        property_type_column = artifact.get("property_type_column", PROPERTY_TYPE_COL)
        summary_df = build_summary_dataframe(artifact)
        bundle = {
            "artifact": artifact,
            "raw_address_df": raw_address_df,
            "feature_list": feature_list,
            "target_mode": target_mode,
            "unit_price_scale": unit_price_scale,
            "property_type_column": property_type_column,
            "summary_df": summary_df,
        }
        MODEL_CACHE["bundle"] = bundle
        MODEL_CACHE["error"] = ""
        return bundle, "Model đã được tải thành công.", True
    except Exception as exc:
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        MODEL_CACHE["bundle"] = None
        MODEL_CACHE["error"] = error
        return None, error, False


def get_cached_bundle():
    bundle = MODEL_CACHE.get("bundle")
    error = MODEL_CACHE.get("error", "")
    if bundle is None or error:
        return None, error or "Model chưa được tải."
    return bundle, ""


def to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_option_series(series):
    values = series.dropna().astype(str).str.strip()
    values = values[(values != "") & (~values.str.lower().isin(["none", "nan", "null"]))]
    return sorted(values.unique().tolist())


def select_options(series, include_blank=False):
    options = clean_option_series(series)
    return ([""] + options) if include_blank else options


def format_vnd(price):
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return "Không có kết quả"
    return f"{price:,.0f} VND"


def format_bn_vnd(price_bn):
    if price_bn is None or (isinstance(price_bn, float) and math.isnan(price_bn)):
        return "Không có kết quả"
    return f"{price_bn:.2f} tỷ VND"


def build_estimate_range(prediction_vnd, mae_bn_vnd):
    mae_vnd = mae_bn_vnd * 1e9
    return max(0.0, prediction_vnd - mae_vnd), prediction_vnd + mae_vnd


def find_relevant_subtype_fields(df, property_type):
    if not property_type:
        return []
    type_df = df[df[PROPERTY_TYPE_COL] == property_type].copy()
    return [field for field in SUBTYPE_FIELDS if field in type_df.columns and clean_option_series(type_df[field])]


def get_model_for_property_type(artifact, property_type):
    models_by_type = artifact.get("models_by_type", {})
    if property_type and property_type in models_by_type:
        model = models_by_type[property_type]
        model_name = artifact.get("property_type_models", {}).get(property_type, {}).get("best_model_name")
        metrics = artifact.get("property_type_models", {}).get(property_type, {}).get("metrics", artifact["metrics"])
        return model, model_name or artifact.get("model_name", "Không rõ"), metrics

    return artifact["pipeline"], artifact.get("model_name", "Không rõ"), artifact["metrics"]


def predict_price(model, feature_list, target_mode, unit_price_scale, values):
    row = {feature: None for feature in feature_list}
    row.update(
        {
            CITY_FIELD: values.get(CITY_FIELD) or None,
            DISTRICT_FIELD: values.get(DISTRICT_FIELD) or None,
            PROPERTY_TYPE_COL: values.get(PROPERTY_TYPE_COL) or None,
            AREA_FIELD: to_float(values.get(AREA_FIELD)),
            BEDROOM_FIELD: to_float(values.get(BEDROOM_FIELD)),
            BATHROOM_FIELD: to_float(values.get(BATHROOM_FIELD)),
            STREET_FIELD: values.get(STREET_FIELD) or None,
            "Loại hình nhà ở": values.get("Loại hình nhà ở") or None,
            "Loại hình căn hộ": values.get("Loại hình căn hộ") or None,
            "Loại hình đất": values.get("Loại hình đất") or None,
            "Loại hình văn phòng": values.get("Loại hình văn phòng") or None,
        }
    )

    input_df = pd.DataFrame([row], columns=feature_list)
    pred_value = float(model.predict(input_df)[0])
    area_value = to_float(values.get(AREA_FIELD))

    if target_mode == "unit_price_times_area":
        pred_price = pred_value * area_value * unit_price_scale if area_value is not None else float("nan")
        return {
            "Giá dự đoán (VND)": format_vnd(pred_price),
            "Giá dự đoán (tỷ VND)": round(pred_price / 1e9, 3) if not math.isnan(pred_price) else None,
            "Đơn giá dự đoán (triệu/m²)": round(pred_value, 4),
        }

    return {
        "Giá dự đoán (VND)": format_vnd(pred_value),
        "Giá dự đoán (tỷ VND)": round(pred_value / 1e9, 3) if not math.isnan(pred_value) else None,
    }


def resolve_legacy_address_candidates(df, new_city, new_ward, street):
    if not new_city or not new_ward or not street:
        return pd.DataFrame(), pd.DataFrame()

    filtered = df.copy()
    filtered = filtered[filtered[NEW_CITY_FIELD] == new_city]
    filtered = filtered[filtered[NEW_WARD_FIELD] == new_ward]
    filtered = filtered[filtered[STREET_FIELD] == street]
    if filtered.empty:
        return filtered, pd.DataFrame()

    candidate_summary = (
        filtered.groupby([OLD_WARD_FIELD, DISTRICT_FIELD, CITY_FIELD], dropna=False)
        .size()
        .reset_index(name="rows")
        .sort_values(by=["rows", OLD_WARD_FIELD, DISTRICT_FIELD, CITY_FIELD], ascending=[False, True, True, True])
        .reset_index(drop=True)
    )
    return filtered, candidate_summary


def safe_select(value, options, default=None):
    return value if value in options else default


def compute_form_state(raw_address_df, property_type_column, selected_new_city="", selected_new_ward="", selected_street="", selected_old_ward="", selected_property_type=""):
    new_city_df = raw_address_df.copy()
    if selected_new_city:
        new_city_df = new_city_df[new_city_df[NEW_CITY_FIELD] == selected_new_city]

    new_ward_options = []
    if selected_new_city:
        new_ward_options = select_options(new_city_df[NEW_WARD_FIELD], include_blank=False)
        selected_new_ward = safe_select(selected_new_ward, new_ward_options)
    else:
        selected_new_ward = None

    street_options = []
    if selected_new_city and selected_new_ward:
        new_ward_df = new_city_df.copy()
        new_ward_df = new_ward_df[new_ward_df[NEW_WARD_FIELD] == selected_new_ward]
        street_options = select_options(new_ward_df[STREET_FIELD], include_blank=False)
        selected_street = safe_select(selected_street, street_options)
    else:
        selected_street = None

    _, legacy_candidates = resolve_legacy_address_candidates(
        raw_address_df,
        selected_new_city,
        selected_new_ward,
        selected_street,
    )

    legacy_old_ward_options = clean_option_series(legacy_candidates[OLD_WARD_FIELD]) if not legacy_candidates.empty else []
    old_ward_dropdown_options = legacy_old_ward_options
    default_old_ward = legacy_old_ward_options[0] if legacy_old_ward_options else None
    selected_old_ward = safe_select(selected_old_ward, legacy_old_ward_options, default_old_ward)

    resolved_candidates = legacy_candidates.copy()
    if selected_old_ward:
        resolved_candidates = resolved_candidates[resolved_candidates[OLD_WARD_FIELD] == selected_old_ward]

    resolved_old_city = ""
    resolved_old_district = ""
    resolved_old_ward = selected_old_ward
    if not resolved_candidates.empty:
        top_match = (
            resolved_candidates.groupby([OLD_WARD_FIELD, DISTRICT_FIELD, CITY_FIELD], dropna=False)
            .size()
            .reset_index(name="rows")
            .sort_values(by=["rows", OLD_WARD_FIELD, DISTRICT_FIELD, CITY_FIELD], ascending=[False, True, True, True])
            .iloc[0]
        )
        resolved_old_ward = top_match[OLD_WARD_FIELD]
        resolved_old_district = top_match[DISTRICT_FIELD]
        resolved_old_city = top_match[CITY_FIELD]

    input_scope_df = raw_address_df.copy()
    if resolved_old_city:
        input_scope_df = input_scope_df[input_scope_df[CITY_FIELD] == resolved_old_city]
    if resolved_old_district:
        input_scope_df = input_scope_df[input_scope_df[DISTRICT_FIELD] == resolved_old_district]
    if selected_street:
        input_scope_df = input_scope_df[input_scope_df[STREET_FIELD] == selected_street]

    property_type_options = PROPERTY_TYPE_CHOICES
    selected_property_type = safe_select(selected_property_type, property_type_options)

    type_df = input_scope_df.copy()
    if selected_property_type:
        type_df = type_df[type_df[property_type_column] == selected_property_type]

    bedroom_options = BEDROOM_COUNT_CHOICES
    bathroom_options = BATHROOM_COUNT_CHOICES
    relevant_subtype_fields = find_relevant_subtype_fields(input_scope_df, selected_property_type)

    subtype_updates = []
    for field in SUBTYPE_FIELDS:
        is_visible = field in relevant_subtype_fields
        choices = select_options(type_df[field], include_blank=False) if is_visible and field in type_df.columns else []
        subtype_updates.append(gr.update(choices=choices, value=None, visible=is_visible))

    return {
        "new_ward_options": new_ward_options,
        "selected_new_ward": selected_new_ward,
        "street_options": street_options,
        "selected_street": selected_street,
        "old_ward_options": old_ward_dropdown_options,
        "selected_old_ward": selected_old_ward,
        "old_ward_visible": len(legacy_old_ward_options) > 1,
        "resolved_old_ward": resolved_old_ward or "",
        "resolved_old_district": resolved_old_district or "",
        "resolved_old_city": resolved_old_city or "",
        "property_type_options": property_type_options,
        "selected_property_type": selected_property_type,
        "bedroom_options": bedroom_options,
        "bathroom_options": bathroom_options,
        "subtype_updates": subtype_updates,
    }


def build_summary_dataframe(artifact):
    if not artifact.get("property_type_models"):
        return pd.DataFrame()
    rows = []
    for property_type, info in artifact["property_type_models"].items():
        rows.append(
            {
                "Loại hình": property_type,
                "Model tốt nhất": info["best_model_name"],
                "MAE (tỷ VND)": round(info["metrics"]["MAE (bn VND)"], 3),
                "RMSLE": round(info["metrics"]["RMSLE"], 3),
                "Số mẫu": info["rows"],
            }
        )
    return pd.DataFrame(rows)


def build_property_model_page():
    gr.Markdown("# Dự đoán giá bất động sản tại Việt Nam")
    gr.Markdown(
        "Nhập địa chỉ mới từ dataset gốc. Ứng dụng sẽ tự map sang địa chỉ cũ tương ứng để đưa vào model dự đoán."
    )
    model_ready = gr.State(False)
    load_status = gr.Markdown("Model chưa được tải. Bấm **Tải model** để bắt đầu.")
    load_button = gr.Button("Tải model")

    with gr.Group(visible=True) as form_group:
        load_city_button = gr.Button("Tải danh sách tỉnh/thành phố", interactive=False)
        with gr.Row():
            new_city = gr.Dropdown(label="Tỉnh/Thành phố mới", choices=[], value=None)
            new_ward = gr.Dropdown(label="Phường/Xã mới", choices=[], value=None)
            street = gr.Dropdown(label="Đường", choices=[], value=None)

        old_ward_choice = gr.Dropdown(
            label="Phường/Xã cũ",
            choices=[],
            value=None,
            visible=False,
            info="Nếu có nhiều khả năng khớp, bạn có thể chọn lại phường/xã cũ.",
        )

        gr.Markdown("## Địa chỉ cũ")
        with gr.Row():
            resolved_old_ward = gr.Textbox(label="Phường/Xã cũ", value="", interactive=False)
            resolved_old_district = gr.Textbox(label="Huyện/Quận cũ", value="", interactive=False)
            resolved_old_city = gr.Textbox(label="Tỉnh/Thành phố cũ", value="", interactive=False)

        property_type = gr.Dropdown(label="Loại hình", choices=PROPERTY_TYPE_CHOICES, value=None)

        with gr.Row():
            area = gr.Number(label="Diện tích", value=None, minimum=0)
            bedrooms = gr.Dropdown(label=BEDROOM_FIELD, choices=BEDROOM_COUNT_CHOICES, value=None)
            bathrooms = gr.Dropdown(label=BATHROOM_FIELD, choices=BATHROOM_COUNT_CHOICES, value=None)

        subtype_house = gr.Dropdown(label="Loại hình nhà ở", choices=[], value=None, visible=False)
        subtype_apartment = gr.Dropdown(label="Loại hình căn hộ", choices=[], value=None, visible=False)
        subtype_land = gr.Dropdown(label="Loại hình đất", choices=[], value=None, visible=False)
        subtype_office = gr.Dropdown(label="Loại hình văn phòng", choices=[], value=None, visible=False)

        predict_button = gr.Button("Dự đoán", variant="primary")
        prediction_output = gr.Markdown()
        summary_title = gr.Markdown("## Model Tốt Nhất Theo Loại Hình", visible=False)
        summary_table = gr.Dataframe(value=pd.DataFrame(), interactive=False, wrap=True, visible=False)

    def handle_load_model():
        yield (
            "Đang tải model...",
            False,
            gr.update(interactive=False),
        )
        bundle, status, ok = load_model_bundle()
        if not ok or bundle is None:
            yield (
                f"## Không thể tải model\n```text\n{status}\n```",
                False,
                gr.update(interactive=False),
            )
            return
        yield (
            status,
            True,
            gr.update(interactive=True),
        )

    def populate_new_city_options(model_ready):
        if not model_ready:
            return gr.update()
        bundle, _ = get_cached_bundle()
        if bundle is None:
            return gr.update()
        return gr.update(choices=select_options(bundle["raw_address_df"][NEW_CITY_FIELD], include_blank=False), value=None)

    def refresh_form(
        model_ready,
        selected_new_city,
        selected_new_ward,
        selected_street,
        selected_old_ward,
        selected_property_type,
    ):
        if not model_ready:
            return [gr.update() for _ in range(13)]
        bundle, _ = get_cached_bundle()
        if bundle is None:
            return [gr.update() for _ in range(13)]
        state = compute_form_state(
            bundle["raw_address_df"],
            bundle["property_type_column"],
            selected_new_city=selected_new_city or "",
            selected_new_ward=selected_new_ward or "",
            selected_street=selected_street or "",
            selected_old_ward=selected_old_ward or "",
            selected_property_type=selected_property_type or "",
        )

        return [
            gr.update(choices=state["new_ward_options"], value=state["selected_new_ward"]),
            gr.update(choices=state["street_options"], value=state["selected_street"]),
            gr.update(choices=state["old_ward_options"], value=state["selected_old_ward"], visible=state["old_ward_visible"]),
            state["resolved_old_ward"],
            state["resolved_old_district"],
            state["resolved_old_city"],
            gr.update(choices=state["property_type_options"], value=state["selected_property_type"]),
            gr.update(choices=state["bedroom_options"], value=None),
            gr.update(choices=state["bathroom_options"], value=None),
            *state["subtype_updates"],
        ]

    def build_prediction_output(
        model_ready,
        selected_new_city,
        selected_new_ward,
        selected_street,
        selected_old_ward,
        selected_property_type,
        area_value,
        selected_bedrooms,
        selected_bathrooms,
        subtype_house,
        subtype_apartment,
        subtype_land,
        subtype_office,
    ):
        if not model_ready:
            return "Hãy bấm Load model trước khi dự đoán."

        bundle, _ = get_cached_bundle()
        if bundle is None:
            return "Hãy bấm Load model trước khi dự đoán."

        state = compute_form_state(
            bundle["raw_address_df"],
            bundle["property_type_column"],
            selected_new_city=selected_new_city or "",
            selected_new_ward=selected_new_ward or "",
            selected_street=selected_street or "",
            selected_old_ward=selected_old_ward or "",
            selected_property_type=selected_property_type or "",
        )

        if area_value is None or area_value <= 0:
            return "Vui lòng nhập diện tích lớn hơn 0."
        if not selected_new_city or not state["selected_new_ward"] or not state["selected_street"] or not state["selected_property_type"]:
            return "Vui lòng chọn đầy đủ Tỉnh/Thành phố mới, Phường/Xã mới, Đường và Loại hình."
        if not state["resolved_old_city"] or not state["resolved_old_district"]:
            return "Không map được địa chỉ cũ từ dữ liệu hiện tại nên chưa thể dự đoán."

        model, model_name, model_metrics = get_model_for_property_type(bundle["artifact"], state["selected_property_type"])
        values = {
            CITY_FIELD: state["resolved_old_city"],
            DISTRICT_FIELD: state["resolved_old_district"],
            STREET_FIELD: state["selected_street"],
            PROPERTY_TYPE_COL: state["selected_property_type"],
            AREA_FIELD: str(area_value),
            BEDROOM_FIELD: selected_bedrooms,
            BATHROOM_FIELD: selected_bathrooms,
            "Loại hình nhà ở": subtype_house,
            "Loại hình căn hộ": subtype_apartment,
            "Loại hình đất": subtype_land,
            "Loại hình văn phòng": subtype_office,
        }
        prediction = predict_price(
            model=model,
            feature_list=bundle["feature_list"],
            target_mode=bundle["target_mode"],
            unit_price_scale=bundle["unit_price_scale"],
            values=values,
        )

        predicted_price_bn = prediction["Giá dự đoán (tỷ VND)"]
        predicted_price_vnd = predicted_price_bn * 1e9 if predicted_price_bn is not None else float("nan")
        lower_bound, upper_bound = build_estimate_range(predicted_price_vnd, model_metrics["MAE (bn VND)"])

        result_lines = [
            "## Kết quả dự đoán",
            f"- Giá dự đoán: **{prediction['Giá dự đoán (VND)']}**",
        ]
        if bundle["target_mode"] == "unit_price_times_area":
            result_lines.append(f"- Đơn giá dự đoán: **{prediction['Đơn giá dự đoán (triệu/m²)']:.2f} triệu/m²**")
        result_lines.extend(
            [
                "",
                "## Thông tin model",
                f"- Model đang dùng: **{model_name}**",
                f"- Sai lệch trung bình tham khảo: **{format_bn_vnd(model_metrics['MAE (bn VND)'])}**",
                f"- Khoảng giá tham khảo: **{format_vnd(lower_bound)}** đến **{format_vnd(upper_bound)}**",
            ]
        )
        return "\n".join(result_lines)

    load_button.click(
        handle_load_model,
        outputs=[
            load_status,
            model_ready,
            load_city_button,
        ],
    )

    load_city_button.click(
        populate_new_city_options,
        inputs=[model_ready],
        outputs=[new_city],
    )

    refresh_inputs = [model_ready, new_city, new_ward, street, old_ward_choice, property_type]
    refresh_outputs = [
        new_ward,
        street,
        old_ward_choice,
        resolved_old_ward,
        resolved_old_district,
        resolved_old_city,
        property_type,
        bedrooms,
        bathrooms,
        subtype_house,
        subtype_apartment,
        subtype_land,
        subtype_office,
    ]

    for component in refresh_inputs[1:]:
        component.change(refresh_form, inputs=refresh_inputs, outputs=refresh_outputs)

    predict_button.click(
        build_prediction_output,
        inputs=[
            model_ready,
            new_city,
            new_ward,
            street,
            old_ward_choice,
            property_type,
            area,
            bedrooms,
            bathrooms,
            subtype_house,
            subtype_apartment,
            subtype_land,
            subtype_office,
        ],
        outputs=prediction_output,
    )
