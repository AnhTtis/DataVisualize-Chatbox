import json
import os
import re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
from lightgbm import LGBMRegressor
from scipy import sparse
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBRegressor

TARGET_COL = "Giá bán"
AREA_COL = "Diện tích"
UNIT_PRICE_COL = "Đơn giá (tr/m2)"
UNIT_PRICE_TARGET_COL = "Đơn giá mục tiêu"
UNIT_PRICE_SCALE = 1_000_000.0
UNIT_PRICE_OUTLIER_IQR_MULTIPLIER = 1.5
RANDOM_STATE = 42
TEST_SIZE = 0.15
GOOGLE_GEOCODE_API_KEY = os.getenv("GOOGLE_GEOCODE_API_KEY", "")
PROPERTY_TYPE_COL = "Loại hình"

COMBO_4_FEATURES = [
    "Đường",
    "Huyện/Quận cũ",
    "Tỉnh/Thành phố cũ",
    "Loại hình",
    "Diện tích",
    "Số phòng ngủ",
    "Số phòng vệ sinh",
    "Loại hình nhà ở",
    "Loại hình căn hộ",
    "Loại hình đất",
    "Loại hình văn phòng",
]

MIN_SAMPLES_PER_TYPE = 300

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
NON_ALNUM_FEATURE_PATTERN = re.compile(r"[^0-9a-zA-Z_]+")


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


def normalize_optional_text(value):
    normalized = normalize_text_value(value)
    return None if pd.isna(normalized) else normalized


def sanitize_location_columns(df: pd.DataFrame, columns=None) -> pd.DataFrame:
    cleaned_df = df.copy()
    for column in columns or ADDRESS_TEXT_COLUMNS:
        if column in cleaned_df.columns:
            cleaned_df[column] = cleaned_df[column].apply(normalize_text_value)
    return cleaned_df


def parse_numeric_series(series, keep_decimal=True, area_mode=False, remove_suffix_pattern=None):
    values = series.astype("string").fillna("").str.strip()
    if remove_suffix_pattern:
        values = values.str.replace(remove_suffix_pattern, "", regex=True)

    if area_mode:
        values = (
            values.str.replace(r"(?i)^none\s*m2?$", "", regex=True)
            .str.replace(",", "", regex=False)
            .str.extract(r"^(\d+(?:\.\d+)?)", expand=False)
        )
    else:
        values = values.str.replace(",", "", regex=False)
        pattern = r"[^0-9.]" if keep_decimal else r"[^0-9]"
        values = values.str.replace(pattern, "", regex=True)

    return pd.to_numeric(values, errors="coerce")


def preprocess_raw_data(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()

    for column in ADDRESS_TEXT_COLUMNS:
        if column in df.columns:
            df[column] = df[column].apply(normalize_text_value)

    numeric_columns = ["Số tầng", "Kinh độ", "Vĩ độ"]
    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    if "Ngày đăng" in df.columns:
        df["Ngày đăng"] = pd.to_datetime(df["Ngày đăng"], errors="coerce")

    boolean_columns = ["Đăng bởi Đối tác", "Có hướng ra đường"]
    for column in boolean_columns:
        if column in df.columns and df[column].dtype == "object":
            df[column] = df[column].map({"True": True, "False": False, "1": True, "0": False})

    decimal_numeric_columns = [UNIT_PRICE_COL]
    integer_like_columns = [TARGET_COL]
    area_columns = ["Chiều dài", "Chiều rộng", AREA_COL]

    for column in decimal_numeric_columns:
        if column in df.columns and df[column].dtype == "object":
            df[column] = parse_numeric_series(df[column], keep_decimal=True)

    for column in integer_like_columns:
        if column in df.columns and df[column].dtype == "object":
            df[column] = parse_numeric_series(df[column], keep_decimal=False)

    for column in area_columns:
        if column in df.columns and df[column].dtype == "object":
            df[column] = parse_numeric_series(df[column], area_mode=True)

    return remove_unit_price_outliers(df)


def remove_unit_price_outliers(
    df: pd.DataFrame,
    unit_price_col: str = UNIT_PRICE_COL,
    iqr_multiplier: float = UNIT_PRICE_OUTLIER_IQR_MULTIPLIER,
) -> pd.DataFrame:
    if unit_price_col not in df.columns:
        return df

    unit_price = pd.to_numeric(df[unit_price_col], errors="coerce")
    valid_unit_price = unit_price[unit_price.gt(0)]
    if valid_unit_price.empty:
        return df

    q1 = valid_unit_price.quantile(0.25)
    q3 = valid_unit_price.quantile(0.75)
    iqr = q3 - q1

    if pd.isna(iqr) or iqr <= 0:
        keep_mask = unit_price.isna() | unit_price.gt(0)
        return df.loc[keep_mask].copy()

    lower_bound = max(0.0, q1 - iqr_multiplier * iqr)
    upper_bound = q3 + iqr_multiplier * iqr
    keep_mask = unit_price.isna() | unit_price.between(lower_bound, upper_bound)
    return df.loc[keep_mask].copy()


def fill_area(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    length_col = "Chiều dài"
    width_col = "Chiều rộng"
    district_col = "Huyện/Quận cũ"
    ward_old_col = "Phường/Xã cũ"
    ward_new_col = "Phường mới"

    fallback_area_scope = df.get(ward_new_col)
    if fallback_area_scope is None:
        fallback_area_scope = df.get(ward_old_col)
    elif ward_old_col in df.columns:
        fallback_area_scope = fallback_area_scope.fillna(df[ward_old_col])

    if fallback_area_scope is None:
        fallback_area_scope = df[district_col]

    df["_khu_vuc"] = fallback_area_scope.fillna(df[district_col])

    for column in [AREA_COL, length_col, width_col, UNIT_PRICE_COL, TARGET_COL]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    mask_from_dimensions = df[AREA_COL].isna() & df[length_col].gt(0) & df[width_col].gt(0)
    df.loc[mask_from_dimensions, AREA_COL] = df.loc[mask_from_dimensions, length_col] * df.loc[mask_from_dimensions, width_col]

    mask_from_price = df[AREA_COL].isna() & df[TARGET_COL].gt(0) & df[UNIT_PRICE_COL].gt(0)
    df.loc[mask_from_price, AREA_COL] = (
        df.loc[mask_from_price, TARGET_COL] / (df.loc[mask_from_price, UNIT_PRICE_COL] * UNIT_PRICE_SCALE)
    )

    area_group_mean = df.groupby([PROPERTY_TYPE_COL, district_col, "_khu_vuc"])[AREA_COL].transform("mean")
    mask_from_group_mean = df[AREA_COL].isna() & area_group_mean.notna()
    df.loc[mask_from_group_mean, AREA_COL] = area_group_mean[mask_from_group_mean]

    return df.drop(columns=["_khu_vuc"])


def lookup_google_geocode(address, google_api_key):
    google_geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": google_api_key,
        "language": "vi",
        "region": "vn",
        "components": "country:VN",
    }

    try:
        response = requests.get(google_geocode_url, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None

    if payload.get("status") != "OK":
        return None

    results = payload.get("results", [])
    if not results:
        return None

    location = results[0].get("geometry", {}).get("location", {})
    latitude = location.get("lat")
    longitude = location.get("lng")
    if latitude is None or longitude is None:
        return None

    return latitude, longitude


def fill_missing_coordinates(df: pd.DataFrame, enable_geocode=False, google_api_key=""):
    df = df.copy()

    lat_col = "Vĩ độ"
    lon_col = "Kinh độ"
    city_col = "Tỉnh/Thành phố cũ"
    district_col = "Huyện/Quận cũ"
    ward_old_col = "Phường/Xã cũ"
    ward_new_col = "Phường mới"
    house_col = "Số nhà"
    street_col = "Đường"

    df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
    df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")

    if ward_new_col in df.columns:
        df["_ward_used"] = df[ward_new_col].apply(normalize_optional_text)
    else:
        df["_ward_used"] = None

    if ward_old_col in df.columns:
        missing_ward_mask = df["_ward_used"].isna()
        df.loc[missing_ward_mask, "_ward_used"] = df.loc[missing_ward_mask, ward_old_col].apply(normalize_optional_text)

    def build_address(row):
        parts = [
            normalize_optional_text(row.get(house_col)),
            normalize_optional_text(row.get(street_col)),
            normalize_optional_text(row.get("_ward_used")),
            normalize_optional_text(row.get(district_col)),
            normalize_optional_text(row.get(city_col)),
            "Vietnam",
        ]
        parts = [part for part in parts if part is not None]
        return ", ".join(dict.fromkeys(parts)) if parts else None

    df["_full_address"] = df.apply(build_address, axis=1)
    missing_before = int((df[lat_col].isna() | df[lon_col].isna()).sum())
    missing_after_geocode = missing_before

    df["_geo_lat"] = np.nan
    df["_geo_lon"] = np.nan

    if enable_geocode and google_api_key:
        missing_mask = df[lat_col].isna() | df[lon_col].isna()
        addresses_to_lookup = df.loc[missing_mask, "_full_address"].dropna().drop_duplicates().tolist()
        geo_cache = {}

        for address in addresses_to_lookup:
            location = lookup_google_geocode(address, google_api_key)
            geo_cache[address] = location if location is not None else (np.nan, np.nan)

        df["_geo_lat"] = df["_full_address"].map(
            lambda address: geo_cache.get(address, (np.nan, np.nan))[0] if pd.notna(address) else np.nan
        )
        df["_geo_lon"] = df["_full_address"].map(
            lambda address: geo_cache.get(address, (np.nan, np.nan))[1] if pd.notna(address) else np.nan
        )

        lat_fill_mask = df[lat_col].isna() & df["_geo_lat"].notna()
        lon_fill_mask = df[lon_col].isna() & df["_geo_lon"].notna()
        df.loc[lat_fill_mask, lat_col] = df.loc[lat_fill_mask, "_geo_lat"]
        df.loc[lon_fill_mask, lon_col] = df.loc[lon_fill_mask, "_geo_lon"]
        missing_after_geocode = int((df[lat_col].isna() | df[lon_col].isna()).sum())

    for group_columns in [
        [street_col, "_ward_used", district_col, city_col],
        ["_ward_used", district_col, city_col],
        [district_col, city_col],
        [city_col],
    ]:
        lat_mean = df.groupby(group_columns)[lat_col].transform("mean")
        lon_mean = df.groupby(group_columns)[lon_col].transform("mean")
        lat_mask = df[lat_col].isna() & lat_mean.notna()
        lon_mask = df[lon_col].isna() & lon_mean.notna()
        df.loc[lat_mask, lat_col] = lat_mean[lat_mask]
        df.loc[lon_mask, lon_col] = lon_mean[lon_mask]

    df[lat_col] = df[lat_col].fillna(df[lat_col].mean())
    df[lon_col] = df[lon_col].fillna(df[lon_col].mean())
    missing_after_fill = int((df[lat_col].isna() | df[lon_col].isna()).sum())

    df = df.drop(columns=["_ward_used", "_full_address", "_geo_lat", "_geo_lon"])
    stats = {
        "missing_coordinates_before": missing_before,
        "missing_coordinates_after_geocode": missing_after_geocode,
        "missing_coordinates_after_fill": missing_after_fill,
    }
    return df, stats


def build_preprocessor(X: pd.DataFrame) -> ColumnTransformer:
    numeric_cols = [column for column in X.columns if pd.api.types.is_numeric_dtype(X[column])]
    categorical_cols = [column for column in X.columns if column not in numeric_cols]

    transformers = []
    if numeric_cols:
        transformers.append(
            (
                "num",
                Pipeline(steps=[("imputer", SimpleImputer(strategy="median"))]),
                numeric_cols,
            )
        )

    if categorical_cols:
        transformers.append(
            (
                "cat",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="constant", fill_value="Missing", keep_empty_features=True)),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_cols,
            )
        )

    return ColumnTransformer(transformers=transformers, remainder="drop")


def rmsle(y_true, y_pred):
    true_values = np.clip(np.asarray(y_true, dtype=float), a_min=0.0, a_max=None)
    pred_values = np.clip(np.asarray(y_pred, dtype=float), a_min=0.0, a_max=None)
    return float(np.sqrt(np.mean((np.log1p(pred_values) - np.log1p(true_values)) ** 2)))


def regression_metrics(y_true_vnd, y_pred_vnd):
    y_true_vnd = np.asarray(y_true_vnd, dtype=float)
    y_pred_vnd = np.clip(np.asarray(y_pred_vnd, dtype=float), a_min=0.0, a_max=None)
    absolute_error = np.abs(y_true_vnd - y_pred_vnd)
    percent_error = absolute_error / np.clip(y_true_vnd, a_min=1.0, a_max=None)
    return {
        "MAE (bn VND)": float(mean_absolute_error(y_true_vnd, y_pred_vnd) / 1e9),
        "RMSLE": rmsle(y_true_vnd, y_pred_vnd),
        "Hit <= 0.5 bn (%)": float(np.mean(absolute_error <= 5e8) * 100.0),
        "Hit <= 10% (%)": float(np.mean(percent_error <= 0.10) * 100.0),
        "Hit <= 20% (%)": float(np.mean(percent_error <= 0.20) * 100.0),
    }


def build_model_specs():
    return [
        (
            "XGBoost Regressor",
            XGBRegressor(
                objective="reg:squarederror",
                n_estimators=300,
                learning_rate=0.05,
                max_depth=6,
                min_child_weight=3,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.0,
                reg_lambda=1.0,
                random_state=RANDOM_STATE,
                n_jobs=1,
            ),
        ),
        (
            "LightGBM Regressor",
            LGBMRegressor(
                objective="regression",
                n_estimators=300,
                learning_rate=0.05,
                num_leaves=31,
                max_depth=-1,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.0,
                reg_lambda=1.0,
                random_state=RANDOM_STATE,
                n_jobs=1,
                verbosity=-1,
            ),
        ),
        (
            "Random Forest Regressor",
            RandomForestRegressor(
                n_estimators=250,
                max_depth=None,
                min_samples_leaf=2,
                random_state=RANDOM_STATE,
                n_jobs=1,
            ),
        ),
    ]


def sanitize_feature_names(feature_names):
    sanitized = []
    used_names = set()

    for index, name in enumerate(feature_names):
        normalized = NON_ALNUM_FEATURE_PATTERN.sub("_", str(name)).strip("_").lower()
        if not normalized:
            normalized = f"feature_{index}"

        candidate = normalized
        suffix = 1
        while candidate in used_names:
            suffix += 1
            candidate = f"{normalized}_{suffix}"

        used_names.add(candidate)
        sanitized.append(candidate)

    return sanitized


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


def build_training_pipeline(X: pd.DataFrame, regressor) -> Pipeline:
    preprocessor = build_preprocessor(X)
    steps = [("preprocess", preprocessor)]

    if isinstance(regressor, LGBMRegressor):
        feature_name_aligner = MatrixToFrameTransformer()
        transformed_feature_names = preprocessor.fit(X).get_feature_names_out()
        feature_name_aligner.feature_names_in_ = sanitize_feature_names(transformed_feature_names)
        steps.append(("align_feature_names", feature_name_aligner))

    steps.append(
        (
            "model",
            TransformedTargetRegressor(
                regressor=regressor,
                func=np.log1p,
                inverse_func=np.expm1,
                check_inverse=False,
            ),
        )
    )

    return Pipeline(steps=steps)


def prepare_model_dataframe(cleaned_df: pd.DataFrame) -> pd.DataFrame:
    model_df = cleaned_df[COMBO_4_FEATURES + [TARGET_COL]].copy()
    model_df[TARGET_COL] = pd.to_numeric(model_df[TARGET_COL], errors="coerce")
    model_df[AREA_COL] = pd.to_numeric(model_df[AREA_COL], errors="coerce")
    model_df = model_df.dropna(subset=[TARGET_COL, AREA_COL])
    model_df = model_df[model_df[TARGET_COL] > 0].copy()
    model_df = model_df[model_df[AREA_COL] > 0].copy()
    model_df[UNIT_PRICE_TARGET_COL] = model_df[TARGET_COL] / model_df[AREA_COL] / UNIT_PRICE_SCALE
    return model_df


def split_model_dataframe(model_df: pd.DataFrame):
    train_index, test_index = train_test_split(
        model_df.index,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
    )
    return model_df.loc[train_index].copy(), model_df.loc[test_index].copy()


def evaluate_pipeline_on_dataframe(pipeline: Pipeline, eval_df: pd.DataFrame):
    X_eval = eval_df[COMBO_4_FEATURES].copy()
    area_eval = eval_df[AREA_COL].astype(float).to_numpy()
    y_eval_price = eval_df[TARGET_COL].astype(float).to_numpy()
    pred_unit_price = np.clip(np.asarray(pipeline.predict(X_eval), dtype=float), a_min=0.0, a_max=None)
    pred_price = pred_unit_price * area_eval * UNIT_PRICE_SCALE
    return regression_metrics(y_eval_price, pred_price)


def make_benchmark_row(subset_name: str, rows: int, train_rows: int, test_rows: int, model_name: str, metrics: dict, source: str):
    return {
        "Property Type": subset_name,
        "Rows": int(rows),
        "Train Rows": int(train_rows),
        "Test Rows": int(test_rows),
        "Model": model_name,
        "Source": source,
        "Target": "Đơn giá x Diện tích",
        **metrics,
    }


def train_models_for_subset(train_df: pd.DataFrame, test_df: pd.DataFrame, subset_name: str):
    X_train = train_df[COMBO_4_FEATURES].copy()
    y_train_unit = train_df[UNIT_PRICE_TARGET_COL].astype(float).copy()

    benchmark_rows = []
    trained_models = []

    for model_name, regressor in build_model_specs():
        pipeline = build_training_pipeline(X_train, regressor)
        pipeline.fit(X_train, y_train_unit)

        metrics = evaluate_pipeline_on_dataframe(pipeline, test_df)
        benchmark_rows.append(
            make_benchmark_row(
                subset_name=subset_name,
                rows=len(train_df) + len(test_df),
                train_rows=len(train_df),
                test_rows=len(test_df),
                model_name=model_name,
                metrics=metrics,
                source="type_specific",
            )
        )
        trained_models.append((model_name, pipeline, metrics))

    benchmark_df = pd.DataFrame(benchmark_rows).sort_values(
        by=["MAE (bn VND)", "RMSLE"],
        ascending=[True, True],
    ).reset_index(drop=True)

    best_model_name, best_pipeline, best_metrics = min(
        trained_models,
        key=lambda item: (item[2]["MAE (bn VND)"], item[2]["RMSLE"]),
    )

    return {
        "benchmark_df": benchmark_df,
        "best_model_name": best_model_name,
        "best_pipeline": best_pipeline,
        "best_metrics": best_metrics,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "rows": int(len(train_df) + len(test_df)),
    }


def train_and_export(input_path, output_dir, enable_geocode):
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    raw_df = pd.read_csv(input_path, low_memory=False, encoding="utf-8-sig")
    cleaned_df = preprocess_raw_data(raw_df)
    cleaned_df = fill_area(cleaned_df)
    cleaned_df, coordinate_stats = fill_missing_coordinates(
        cleaned_df,
        enable_geocode=enable_geocode,
        google_api_key=GOOGLE_GEOCODE_API_KEY,
    )
    model_df = prepare_model_dataframe(cleaned_df)

    overall_train_df, overall_test_df = split_model_dataframe(model_df)
    overall_result = train_models_for_subset(overall_train_df, overall_test_df, "ALL")
    benchmark_frames = [overall_result["benchmark_df"]]
    type_summaries = {}
    pipelines_by_type = {}

    property_type_series = model_df[PROPERTY_TYPE_COL].fillna("Unknown").astype(str)
    type_counts = property_type_series.value_counts().sort_index()

    for property_type, row_count in type_counts.items():
        if row_count < MIN_SAMPLES_PER_TYPE:
            continue

        type_train_df = overall_train_df[overall_train_df[PROPERTY_TYPE_COL].fillna("Unknown").astype(str) == property_type].copy()
        type_test_df = overall_test_df[overall_test_df[PROPERTY_TYPE_COL].fillna("Unknown").astype(str) == property_type].copy()
        if type_train_df.empty or type_test_df.empty:
            continue

        type_result = train_models_for_subset(type_train_df, type_test_df, property_type)
        overall_on_type_metrics = evaluate_pipeline_on_dataframe(overall_result["best_pipeline"], type_test_df)

        benchmark_frames.append(type_result["benchmark_df"])
        benchmark_frames.append(
            pd.DataFrame(
                [
                    make_benchmark_row(
                        subset_name=property_type,
                        rows=len(type_train_df) + len(type_test_df),
                        train_rows=len(type_train_df),
                        test_rows=len(type_test_df),
                        model_name=overall_result["best_model_name"],
                        metrics=overall_on_type_metrics,
                        source="all_model",
                    )
                ]
            )
        )

        use_all_model = (
            overall_on_type_metrics["MAE (bn VND)"],
            overall_on_type_metrics["RMSLE"],
        ) < (
            type_result["best_metrics"]["MAE (bn VND)"],
            type_result["best_metrics"]["RMSLE"],
        )

        selected_pipeline = overall_result["best_pipeline"] if use_all_model else type_result["best_pipeline"]
        selected_model_name = overall_result["best_model_name"] if use_all_model else type_result["best_model_name"]
        selected_metrics = overall_on_type_metrics if use_all_model else type_result["best_metrics"]
        selected_source = "all_model" if use_all_model else "type_specific"

        pipelines_by_type[property_type] = selected_pipeline
        type_summaries[property_type] = {
            "best_model_name": selected_model_name,
            "selected_model_name": selected_model_name,
            "selected_source": selected_source,
            "metrics": selected_metrics,
            "rows": type_result["rows"],
            "train_rows": type_result["train_rows"],
            "test_rows": type_result["test_rows"],
            "type_specific_model_name": type_result["best_model_name"],
            "type_specific_metrics": type_result["best_metrics"],
            "all_model_name": overall_result["best_model_name"],
            "all_model_metrics": overall_on_type_metrics,
        }

    benchmark_df = pd.concat(benchmark_frames, ignore_index=True).sort_values(
        by=["Property Type", "MAE (bn VND)", "RMSLE"],
        ascending=[True, True, True],
    ).reset_index(drop=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "combo4_best_unit_price_pipeline.pkl"
    metrics_path = output_dir / "combo4_unit_price_metrics.json"
    benchmark_path = output_dir / "combo4_unit_price_benchmark.csv"

    artifact = {
        "pipeline": overall_result["best_pipeline"],
        "models_by_type": pipelines_by_type,
        "feature_list": COMBO_4_FEATURES,
        "target_column": TARGET_COL,
        "target_transform": "log1p(unit_price_trieu_m2) -> predicted_price = unit_price * area * 1_000_000",
        "model_name": overall_result["best_model_name"],
        "combo_name": "Combo_4",
        "metrics": overall_result["best_metrics"],
        "train_rows": overall_result["train_rows"],
        "test_rows": overall_result["test_rows"],
        "rows": overall_result["rows"],
        "property_type_column": PROPERTY_TYPE_COL,
        "property_type_models": type_summaries,
        "min_samples_per_type": MIN_SAMPLES_PER_TYPE,
        "coordinate_fill_stats": coordinate_stats,
        "geocode_enabled": bool(enable_geocode and GOOGLE_GEOCODE_API_KEY),
        "google_geocode_configured": bool(GOOGLE_GEOCODE_API_KEY),
        "random_state": RANDOM_STATE,
        "test_size": TEST_SIZE,
        "target_mode": "unit_price_times_area",
        "unit_price_scale": UNIT_PRICE_SCALE,
    }

    joblib.dump(artifact, model_path)
    benchmark_df.to_csv(benchmark_path, index=False, encoding="utf-8-sig")

    serializable_summary = {
        "combo_name": artifact["combo_name"],
        "model_name": artifact["model_name"],
        "target_transform": artifact["target_transform"],
        "feature_list": artifact["feature_list"],
        "metrics": artifact["metrics"],
        "rows": artifact["rows"],
        "train_rows": artifact["train_rows"],
        "test_rows": artifact["test_rows"],
        "property_type_column": artifact["property_type_column"],
        "property_type_models": artifact["property_type_models"],
        "min_samples_per_type": artifact["min_samples_per_type"],
        "coordinate_fill_stats": artifact["coordinate_fill_stats"],
        "geocode_enabled": artifact["geocode_enabled"],
        "google_geocode_configured": artifact["google_geocode_configured"],
        "random_state": artifact["random_state"],
        "test_size": artifact["test_size"],
        "target_mode": artifact["target_mode"],
        "unit_price_scale": artifact["unit_price_scale"],
        "benchmark_path": str(benchmark_path),
        "top_models": benchmark_df.head(5).to_dict(orient="records"),
    }

    metrics_path.write_text(json.dumps(serializable_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "model_path": str(model_path),
        "metrics_path": str(metrics_path),
        "benchmark_path": str(benchmark_path),
        **serializable_summary,
    }


INPUT_FILE = "advertisement.csv"
OUTPUT_DIR = "artifacts"
ENABLE_GEOCODE = False


if __name__ == "__main__":
    summary = train_and_export(
        input_path=INPUT_FILE,
        output_dir=OUTPUT_DIR,
        enable_geocode=ENABLE_GEOCODE,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
