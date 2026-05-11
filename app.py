"""
Spendly — семейный бюджет на Streamlit.
Запуск из корня проекта: streamlit run app.py
Переменные: OPENAI_API_KEY; опционально SPENDLY_DB_PATH для общей БД.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from spendly import db
from spendly.images import (
    guess_mime_from_upload,
    pdf_conversion_available,
    pdf_first_page_as_png,
)
from spendly.receipt_ai import parse_receipt_image

load_dotenv(Path(__file__).resolve().parent / ".env")


def _hydrate_env_from_streamlit_secrets() -> None:
    """Streamlit Community Cloud: ключи из Settings → Secrets → TOML."""
    try:
        sec = st.secrets
    except Exception:
        return
    for key in ("OPENAI_API_KEY", "SPENDLY_DB_PATH"):
        try:
            if key in sec:
                os.environ.setdefault(key, str(sec[key]))
        except Exception:
            continue


_hydrate_env_from_streamlit_secrets()


def _resolve_category_id(hint: str | None, cats: list[dict]) -> int | None:
    if not hint or not cats:
        return None
    h = hint.strip().lower()
    for c in cats:
        if c["name"].strip().lower() == h:
            return int(c["id"])
    for c in cats:
        cn = c["name"].strip().lower()
        if h in cn or cn in h:
            return int(c["id"])
    return None


def _category_name_by_id(cats: list[dict], cid: int | None) -> str:
    if cid is None:
        return ""
    for c in cats:
        if int(c["id"]) == int(cid):
            return str(c["name"])
    return ""


st.set_page_config(
    page_title="Spendly — семейный бюджет",
    page_icon="🧾",
    layout="wide",
)

db.init_db()


def sidebar_menu() -> str:
    with st.sidebar:
        st.title("Меню")

        options = ["Скан чека", "Ручной ввод", "Журнал", "Отчёты", "Семья и категории"]
        if "page" not in st.session_state:
            st.session_state.page = options[0]

        for opt in options:
            active = st.session_state.page == opt
            st.button(
                opt,
                key=f"menu_{opt}",
                use_container_width=True,
                type="primary" if active else "secondary",
                on_click=lambda o=opt: st.session_state.__setitem__("page", o),
            )

        return st.session_state.page


page = sidebar_menu()

members = db.list_members()
member_options = {m["name"]: m["id"] for m in members}
categories = db.list_categories()
cat_options = {c["name"]: c["id"] for c in categories}
cat_id_to_name = {c["id"]: c["name"] for c in categories}

# ——— Скан чека ———
if page == "Скан чека":
    st.subheader("Загрузка чека и распознавание")
    up = st.file_uploader(
        "Фото чека или PDF (берётся первая страница)",
        type=["png", "jpg", "jpeg", "webp", "pdf"],
    )
    model = st.selectbox(
        "Модель OpenAI",
        options=["gpt-4o-mini", "gpt-4o"],
        index=0,
        help="gpt-4o-mini дешевле; gpt-4o точнее на сложных чеках.",
    )

    if up is not None:
        raw = up.getvalue()
        mime = guess_mime_from_upload(up.name, "image/jpeg")
        image_bytes = raw
        pdf_ready = True
        if up.name.lower().endswith(".pdf"):
            if not pdf_conversion_available():
                st.error(
                    "Для PDF нужен пакет **PyMuPDF**. В том же окружении, где запускаете Streamlit, выполните:\n\n"
                    "`pip install pymupdf`\n\n"
                    "Пока пакет не установлен, используйте фото чека (PNG или JPEG)."
                )
                pdf_ready = False
            else:
                try:
                    with st.spinner("Конвертация PDF…"):
                        image_bytes = pdf_first_page_as_png(raw)
                    mime = "image/png"
                except Exception as e:
                    pdf_ready = False
                    st.error(str(e))

        recognize = st.button(
            "Распознать чек",
            type="primary",
            disabled=not pdf_ready,
        )
        if pdf_ready and recognize:
            try:
                with st.spinner("Запрос к OpenAI…"):
                    parsed = parse_receipt_image(
                        image_bytes,
                        mime,
                        model=model,
                        category_names=[c["name"] for c in categories],
                    )
                st.session_state["scan_parsed"] = parsed
            except Exception as e:
                st.error(str(e))

    if "scan_parsed" in st.session_state:
        p = st.session_state["scan_parsed"]
        st.json(p)
        col1, col2 = st.columns(2)
        with col1:
            store = st.text_input("Магазин", value=p.get("store_name") or "")
        with col2:
            cur = p.get("currency") or "RUB"
            currency = st.text_input("Валюта", value=cur)

        dt_raw = p.get("purchased_at")
        if dt_raw:
            try:
                dt_parsed = datetime.fromisoformat(str(dt_raw).replace("Z", "+00:00"))
                if dt_parsed.tzinfo:
                    dt_parsed = dt_parsed.replace(tzinfo=None)
            except ValueError:
                dt_parsed = datetime.now()
        else:
            dt_parsed = datetime.now()

        purchased_at = st.datetime_input("Дата и время покупки", value=dt_parsed)
        member_name = st.selectbox(
            "Кто потратил",
            options=list(member_options.keys()),
            index=0,
        )
        total_amount = st.number_input(
            "Итого по чеку",
            min_value=0.0,
            value=float(p.get("total_amount") or 0.0),
            step=0.01,
        )
        notes = st.text_input("Заметки", value="")

        lines_in = p.get("lines") or []
        rows = []
        for ln in lines_in:
            cat_raw = ln.get("category") or ln.get("category_hint")
            cid = _resolve_category_id(cat_raw, categories)
            rows.append(
                {
                    "product_name": ln.get("product_name") or "",
                    "quantity": float(ln.get("quantity") or 1),
                    "unit_price": ln.get("unit_price"),
                    "line_total": float(ln.get("line_total") or 0),
                    "category": _category_name_by_id(categories, cid),
                }
            )
        if not rows:
            rows.append(
                {
                    "product_name": "",
                    "quantity": 1.0,
                    "unit_price": None,
                    "line_total": 0.0,
                    "category": "",
                }
            )

        edited = st.data_editor(
            pd.DataFrame(rows),
            num_rows="dynamic",
            column_config={
                "product_name": st.column_config.TextColumn("Товар"),
                "quantity": st.column_config.NumberColumn("Кол-во", format="%.3f"),
                "unit_price": st.column_config.NumberColumn(
                    "Цена за ед.", format="%.2f", min_value=0.0
                ),
                "line_total": st.column_config.NumberColumn("Сумма строки", format="%.2f"),
                "category": st.column_config.SelectboxColumn(
                    "Категория",
                    options=[""] + list(cat_options.keys()),
                ),
            },
            hide_index=True,
            key="scan_editor",
        )

        st.checkbox(
            "Сохранить, даже если уже есть чек с тем же магазином и той же минутой покупки",
            key="dup_ok_scan",
        )

        if st.button("Сохранить чек в базу", type="primary"):
            out_lines = []
            for _, r in edited.iterrows():
                pname = str(r.get("product_name") or "").strip()
                if not pname:
                    continue
                cat_name = str(r.get("category") or "").strip()
                cat_id = cat_options.get(cat_name) if cat_name else None
                uprice = r.get("unit_price")
                out_lines.append(
                    {
                        "product_name": pname,
                        "quantity": float(r.get("quantity") or 1),
                        "unit_price": float(uprice) if pd.notna(uprice) else None,
                        "line_total": float(r.get("line_total") or 0),
                        "category_id": cat_id,
                    }
                )
            if not out_lines:
                st.warning("Добавьте хотя бы одну позицию с названием.")
            else:
                allow_dup = bool(st.session_state.get("dup_ok_scan"))
                dup = None if allow_dup else db.find_duplicate_receipt(
                    store, purchased_at
                )
                if dup:
                    st.error(
                        "Похожий чек уже есть в базе (тот же магазин и та же минута покупки). "
                        f"Чек №{dup['id']}: {dup['store_name']}, {dup['purchased_at']}, "
                        f"{dup['total_amount']:.2f} {dup['currency']}. "
                        "Если это другой чек, отметьте галочку ниже и сохраните снова."
                    )
                else:
                    mid = member_options[member_name]
                    db.insert_receipt(
                        store_name=store,
                        purchased_at=purchased_at,
                        total_amount=total_amount,
                        currency=currency,
                        member_id=mid,
                        lines=out_lines,
                        notes=notes or None,
                        source="openai_scan",
                    )
                    st.session_state["dup_ok_scan"] = False
                    del st.session_state["scan_parsed"]
                    st.success("Чек сохранён.")
                    st.rerun()

# ——— Ручной ввод ———
if page == "Ручной ввод":
    st.subheader("Новый чек вручную")
    c1, c2, c3 = st.columns(3)
    with c1:
        m_store = st.text_input("Магазин", key="m_store")
    with c2:
        m_currency = st.text_input("Валюта", value="RUB", key="m_cur")
    with c3:
        m_member = st.selectbox(
            "Кто потратил",
            options=list(member_options.keys()),
            key="m_mem",
        )
    m_dt = st.datetime_input("Дата и время", value=datetime.now(), key="m_dt")
    m_total = st.number_input("Итого", min_value=0.0, value=0.0, step=0.01, key="m_tot")
    m_notes = st.text_input("Заметки", key="m_notes")

    if "manual_rows" not in st.session_state:
        st.session_state["manual_rows"] = [
            {
                "product_name": "",
                "quantity": 1.0,
                "unit_price": None,
                "line_total": 0.0,
                "category": "",
            }
        ]

    md = st.data_editor(
        pd.DataFrame(st.session_state["manual_rows"]),
        num_rows="dynamic",
        column_config={
            "product_name": st.column_config.TextColumn("Товар"),
            "quantity": st.column_config.NumberColumn("Кол-во", format="%.3f"),
            "unit_price": st.column_config.NumberColumn(
                "Цена за ед.", format="%.2f", min_value=0.0
            ),
            "line_total": st.column_config.NumberColumn("Сумма строки", format="%.2f"),
            "category": st.column_config.SelectboxColumn(
                "Категория",
                options=[""] + list(cat_options.keys()),
            ),
        },
        hide_index=True,
        key="manual_editor",
    )

    st.checkbox(
        "Сохранить, даже если уже есть чек с тем же магазином и той же минутой покупки",
        key="dup_ok_manual",
    )

    if st.button("Сохранить ручной чек", type="primary"):
        lines_out = []
        for _, r in md.iterrows():
            pname = str(r.get("product_name") or "").strip()
            if not pname:
                continue
            cn = str(r.get("category") or "").strip()
            cid = cat_options.get(cn) if cn else None
            up = r.get("unit_price")
            lines_out.append(
                {
                    "product_name": pname,
                    "quantity": float(r.get("quantity") or 1),
                    "unit_price": float(up) if pd.notna(up) else None,
                    "line_total": float(r.get("line_total") or 0),
                    "category_id": cid,
                }
            )
        if not m_store.strip():
            st.warning("Укажите магазин.")
        elif not lines_out:
            st.warning("Добавьте позиции с названиями.")
        else:
            allow_dup = bool(st.session_state.get("dup_ok_manual"))
            dup = None if allow_dup else db.find_duplicate_receipt(m_store, m_dt)
            if dup:
                st.error(
                    "Похожий чек уже есть в базе (тот же магазин и та же минута покупки). "
                    f"Чек №{dup['id']}: {dup['store_name']}, {dup['purchased_at']}, "
                    f"{dup['total_amount']:.2f} {dup['currency']}. "
                    "Если это другой чек, отметьте галочку выше и сохраните снова."
                )
            else:
                db.insert_receipt(
                    store_name=m_store,
                    purchased_at=m_dt,
                    total_amount=m_total,
                    currency=m_currency,
                    member_id=member_options[m_member],
                    lines=lines_out,
                    notes=m_notes or None,
                    source="manual",
                )
                st.session_state["dup_ok_manual"] = False
                st.session_state["manual_rows"] = [
                    {
                        "product_name": "",
                        "quantity": 1.0,
                        "unit_price": None,
                        "line_total": 0.0,
                        "category": "",
                    }
                ]
                st.success("Сохранено.")
                st.rerun()

# ——— Журнал ———
if page == "Журнал":
    st.subheader("Фильтры")
    j1, j2, j3, j4, j5 = st.columns(5)
    with j1:
        j_from = st.date_input("С даты", value=date.today().replace(day=1), key="jf")
    with j2:
        j_to = st.date_input("По дату", value=date.today(), key="jt")
    with j3:
        j_store = st.text_input("Магазин содержит", key="js")
    with j4:
        j_member = st.selectbox(
            "Член семьи",
            options=["(все)"] + list(member_options.keys()),
            key="jm",
        )
    with j5:
        j_cat = st.selectbox(
            "Категория в позициях",
            options=["(все)"] + list(cat_options.keys()),
            key="jc",
        )
    j_product = st.text_input("Название товара содержит", key="jp")

    mid = None if j_member == "(все)" else member_options[j_member]
    cid = None if j_cat == "(все)" else cat_options[j_cat]

    receipts = db.fetch_receipts_filtered(
        date_from=str(j_from),
        date_to=str(j_to),
        store=j_store.strip() or None,
        category_id=cid,
        member_id=mid,
        product_query=j_product.strip() or None,
    )

    if not receipts:
        st.info("Нет чеков по фильтру.")
    for rec in receipts:
        with st.expander(
            f"{rec['purchased_at']} · {rec['store_name']} · {rec['total_amount']:.2f} {rec['currency']}"
        ):
            st.caption(
                f"ID {rec['id']} · {rec.get('member_name') or '—'} · источник: {rec.get('source')}"
            )
            if rec.get("notes"):
                st.write(rec["notes"])
            lines = db.fetch_lines_for_receipt(int(rec["id"]))
            if lines:
                df = pd.DataFrame(lines)
                st.dataframe(
                    df[["product_name", "quantity", "unit_price", "line_total", "category_name"]],
                    hide_index=True,
                )
            if st.button("Удалить чек", key=f"del_{rec['id']}"):
                db.delete_receipt(int(rec["id"]))
                st.rerun()

# ——— Отчёты ———
if page == "Отчёты":
    st.subheader("Период и участник")
    r1, r2, r3 = st.columns(3)
    with r1:
        r_from = st.date_input("С даты", value=date.today().replace(day=1), key="rf")
    with r2:
        r_to = st.date_input("По дату", value=date.today(), key="rt")
    with r3:
        r_member = st.selectbox(
            "Член семьи",
            options=["(все)"] + list(member_options.keys()),
            key="rm",
        )
    r_mid = None if r_member == "(все)" else member_options[r_member]
    df_s = pd.DataFrame(
        db.report_by_store(str(r_from), str(r_to), r_mid),
    )
    df_c = pd.DataFrame(
        db.report_by_category(str(r_from), str(r_to), r_mid),
    )
    df_p = pd.DataFrame(
        db.report_by_product(str(r_from), str(r_to), r_mid),
    )
    cA, cB = st.columns(2)
    with cA:
        st.markdown("**По магазинам**")
        st.dataframe(df_s, hide_index=True, use_container_width=True)
    with cB:
        st.markdown("**По категориям**")
        st.dataframe(df_c, hide_index=True, use_container_width=True)
    st.markdown("**По товарам (топ-50 по сумме)**")
    st.dataframe(df_p, hide_index=True, use_container_width=True)

# ——— Настройки ———
if page == "Семья и категории":
    st.subheader("Члены семьи")
    new_m = st.text_input("Имя", key="new_m")
    if st.button("Добавить члена семьи"):
        if new_m.strip():
            db.add_member(new_m.strip())
            st.success("Добавлено.")
            st.rerun()
    st.dataframe(pd.DataFrame(db.list_members()), hide_index=True)

    st.subheader("Категории")
    new_c = st.text_input("Название категории", key="new_c")
    if st.button("Добавить категорию"):
        if new_c.strip():
            db.add_category(new_c.strip())
            st.success("Добавлено.")
            st.rerun()
    st.dataframe(pd.DataFrame(db.list_categories()), hide_index=True)
