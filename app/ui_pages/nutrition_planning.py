"""Nutrition planning: local catalogue first, planning tools added later."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from backend.nutrition_food_store import delete_food, load_foods, save_food


NUTRIENT_FIELDS = (
    ("calories_per_100g", "Calorías / 100 g"),
    ("carbs_per_100g", "Carbohidratos / 100 g"),
    ("protein_per_100g", "Proteína / 100 g"),
    ("fat_per_100g", "Grasa / 100 g"),
    ("sodium_mg_per_100g", "Sodio / 100 g (mg)"),
    ("package_cost", "Costo del paquete"),
)


def _number(value) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as error:
        raise ValueError("Usa solo números; puedes dejar vacío un dato no disponible.") from error
    if number < 0:
        raise ValueError("Los valores nutricionales y costos no pueden ser negativos.")
    return number


def _form_values(prefix: str, value: dict | None = None) -> dict:
    value = value or {}
    left, right = st.columns(2)
    with left:
        name = st.text_input("Nombre", value=value.get("name", ""), key=f"{prefix}_name")
    with right:
        category = st.text_input("Categoría", value=value.get("category", ""), key=f"{prefix}_category")
    fields = st.columns(3)
    raw = {}
    for index, (field, label) in enumerate(NUTRIENT_FIELDS):
        current = value.get(field)
        with fields[index % 3]:
            raw[field] = st.text_input(
                label,
                value="" if current is None else str(current),
                placeholder="Sin dato",
                key=f"{prefix}_{field}",
            )
    return {"name": name.strip(), "category": category.strip(), **raw}


def _clean_food(raw: dict, existing: dict | None = None) -> dict:
    if not raw["name"]:
        raise ValueError("El nombre es obligatorio.")
    if not raw["category"]:
        raise ValueError("La categoría es obligatoria.")
    return {
        **({"id": existing["id"]} if existing else {}),
        "name": raw["name"],
        "category": raw["category"],
        **{field: _number(raw[field]) for field, _label in NUTRIENT_FIELDS},
    }


def _catalog_table(foods: list[dict]) -> pd.DataFrame:
    rows = []
    for item in foods:
        rows.append({
            "Nombre": item.get("name"),
            "Categoría": item.get("category"),
            "Calorías / 100 g": item.get("calories_per_100g"),
            "Carbohidratos / 100 g": item.get("carbs_per_100g"),
            "Proteína / 100 g": item.get("protein_per_100g"),
            "Grasa / 100 g": item.get("fat_per_100g"),
            "Sodio / 100 g (mg)": item.get("sodium_mg_per_100g"),
            "Costo paquete": item.get("package_cost"),
        })
    return pd.DataFrame(rows)


def render() -> None:
    st.header("Planificación nutricional")
    st.caption("Catálogo local de alimentos para construir las reglas y planes nutricionales posteriores.")
    foods_tab = st.tabs(["Alimentos"])[0]
    with foods_tab:
        foods = load_foods()
        st.subheader("Catálogo de alimentos")
        st.caption("Los valores nutricionales se expresan por 100 g. Los campos vacíos significan “sin dato”, no cero.")
        st.dataframe(
            _catalog_table(foods),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Calorías / 100 g": st.column_config.NumberColumn(format="%.1f"),
                "Carbohidratos / 100 g": st.column_config.NumberColumn(format="%.1f g"),
                "Proteína / 100 g": st.column_config.NumberColumn(format="%.1f g"),
                "Grasa / 100 g": st.column_config.NumberColumn(format="%.1f g"),
                "Sodio / 100 g (mg)": st.column_config.NumberColumn(format="%.0f mg"),
                "Costo paquete": st.column_config.NumberColumn(format="%.2f"),
            },
        )

        create_tab, edit_tab = st.tabs(["Agregar alimento", "Editar o eliminar"])
        with create_tab:
            with st.form("nutrition_create_food", clear_on_submit=True):
                raw = _form_values("nutrition_new")
                submitted = st.form_submit_button("Guardar alimento", type="primary", use_container_width=True)
            if submitted:
                try:
                    save_food(_clean_food(raw))
                    st.success("Alimento agregado.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))

        with edit_tab:
            food_map = {item["id"]: item for item in foods}
            selected_id = st.selectbox(
                "Alimento", tuple(food_map),
                format_func=lambda item_id: f"{food_map[item_id]['name']} · {food_map[item_id]['category']}",
            )
            selected = food_map[selected_id]
            with st.form("nutrition_edit_food"):
                raw = _form_values("nutrition_edit", selected)
                save = st.form_submit_button("Guardar cambios", type="primary", use_container_width=True)
            if save:
                try:
                    save_food(_clean_food(raw, selected))
                    st.success("Alimento actualizado.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
            if st.button("Eliminar alimento", key=f"nutrition_delete_{selected_id}", use_container_width=True):
                delete_food(selected_id)
                st.success("Alimento eliminado.")
                st.rerun()
