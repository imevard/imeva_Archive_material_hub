import sys
import os
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st
from supabase import create_client
import json
from sqlalchemy import text
import plotly.subplots as sp
import plotly.graph_objects as go
from openpyxl import load_workbook
from materialconvert_functions_5 import convert_curve, average_curves



############  PASSWORD #################

def check_password():
    def password_entered():
        if st.session_state["password_input"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password_input"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter password to access Material Hub:", type="password", on_change=password_entered, key="password_input")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Enter password to access Material Hub:", type="password", on_change=password_entered, key="password_input")
        st.error("😕 Incorrect password")
        return False
    else:
        return True

if not check_password():
    st.stop()

# --- Rest of your existing app code ---
##################################################################
# =========================================================================
# ### SECTION 1: PAGE CONFIGURATION & DATABASE SETUP ###
# =========================================================================
st.set_page_config(page_title="IMEVA Materials Hub", layout="wide")

@st.cache_resource
def init_supabase_client():
    url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
    key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase_client = init_supabase_client()

st.cache_data.clear()
try:
    conn = st.connection("supabase", type="sql")
except Exception as e:
    st.error(f"IMEVA MATERIAL HUB ERROR: Failed to connect to Supabase Cloud Database: {e}")
    st.stop()

def upload_certificate_to_cloud(uploaded_file, lotto_identifier):
    """Uploads a certificate file to the Supabase 'certificate_storage' bucket and returns its public URL."""
    try:
        file_ext = uploaded_file.name.split('.')[-1]
        file_path = f"certs/{lotto_identifier}_{int(time.time())}.{file_ext}"
        
        # Push bytes to Supabase Storage
        supabase_client.storage.from_("certificate_storage").upload(
            path=file_path,
            file=uploaded_file.getvalue(),
            file_options={"content-type": uploaded_file.type, "upsert": "true"}
        )
        
        # Retrieve public URL
        public_url = supabase_client.storage.from_("certificate_storage").get_public_url(file_path)
        return public_url
    except Exception as e:
        st.error(f"Cloud Storage Upload Error: {e}")
        return None
    


@st.dialog("Mill Certificate Viewer", width="large")
def show_cert_modal(mat_name, cert_url, info):
    st.markdown(f"### Material: `{mat_name}`")
    st.write(f"**Grade:** {info.get('grade')} | **Lotto:** {info.get('lotto_number')} | **Provider:** {info.get('provider')}")
    st.divider()
    
    if cert_url:
        st.markdown(f"🔗 [Open certificate in a new browser tab]({cert_url})", unsafe_allow_html=True)
        if cert_url.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            st.image(cert_url, caption="Certificate Document", use_container_width=True)
        else:
            st.info("ℹ️ Inline PDF previews are often restricted by security policies. Use the link above if the viewer below appears empty.")
            st.markdown(f'<iframe src="{cert_url}" width="100%" height="600px"></iframe>', unsafe_allow_html=True)
    else:
        st.warning("No certificate URL provided.")



# Auto-restore spectrometer reports from Supabase into session state on app load
if "chemical_reports" not in st.session_state:
    st.session_state.chemical_reports = {}

if "reports_loaded_from_cloud" not in st.session_state:
    try:
        df_cloud_reports = conn.query("SELECT id, spectrometer_data_json FROM materials WHERE spectrometer_data_json IS NOT NULL;", ttl=0)
        
        for _, row in df_cloud_reports.iterrows():
            m_id = row.get("id")
            spec_json = row.get("spectrometer_data_json")
            
            if isinstance(spec_json, str):
                import json
                try:
                    spec_json = json.loads(spec_json)
                except Exception:
                    spec_json = {}
                    
            if spec_json and isinstance(spec_json, dict):
                df_records = spec_json.get("df_records", [])
                comp = spec_json.get("comp", {})
                if df_records:
                    reconstructed_df = pd.DataFrame(df_records)
                    st.session_state.chemical_reports[int(m_id)] = {
                        "df": reconstructed_df,
                        "comp": comp
                    }
        st.session_state.reports_loaded_from_cloud = True
    except Exception as e:
        pass


def fetch_cloud_materials(hub_section=None, family='METALS'):
    """Fetch entries filtered by hub section or return all for Production, aliasing certificate_url as datasheet."""
    if hub_section == "RD":
        query = """
            SELECT 
                id, parent_id, family, hub_section, grade, thickness, 
                yield_mpa, uts_mpa, elongation_pct, coil_weight_kg, coil_length_mm, 
                rd_remaining_weight_kg, rd_remaining_length_mm, rd_notes, 
                lotto_number, lotto_figlio, provider, 
                certificate_url AS datasheet, 
                is_promoted 
            FROM materials 
            WHERE (hub_section = 'RD' OR is_promoted = 1) AND family = :fam 
            ORDER BY id DESC;
        """
        return conn.query(query, params={"fam": family}, ttl=0)
    else:
        query = """
            SELECT 
                id, parent_id, family, hub_section, grade, thickness, 
                yield_mpa, uts_mpa, elongation_pct, coil_weight_kg, coil_length_mm, 
                rd_remaining_weight_kg, rd_remaining_length_mm, rd_notes, 
                lotto_number, lotto_figlio, provider, 
                certificate_url AS datasheet, 
                is_promoted 
            FROM materials 
            WHERE family = :fam 
            ORDER BY id DESC;
        """
        return conn.query(query, params={"fam": family}, ttl=0)

def insert_cloud_material(
    grade, thickness, yield_mpa, uts_mpa, elongation, lotto_padre, lotto_figlio, provider, 
    family, cert_path=None, coil_weight=0.0, coil_length=0.0, coil_length_mm=None
):
    """Insert new production batch into Supabase supporting mutually exclusive Padre/Figlio lottos."""
    actual_length = coil_length_mm if coil_length_mm is not None else coil_length
    
    with conn.session as s:
        s.execute(
            text("""
                INSERT INTO materials (
                    grade, thickness, yield_mpa, uts_mpa, elongation_pct, 
                    lotto_number, lotto_figlio, provider, family, certificate_url, 
                    coil_weight_kg, coil_length_mm,
                    rd_remaining_weight_kg, rd_remaining_length_mm
                )
                VALUES (
                    :grade, :thick, :ys, :uts, :elong, 
                    :l_padre, :l_figlio, :prov, :fam, :cert, 
                    :c_wt, :c_len, :c_wt, :c_len
                )
            """),
            {
                "grade": grade, "thick": thickness, "ys": yield_mpa, 
                "uts": uts_mpa, "elong": elongation, 
                "l_padre": lotto_padre if lotto_padre else None, 
                "l_figlio": lotto_figlio if lotto_figlio else None, 
                "prov": provider, "fam": family, "cert": cert_path, 
                "c_wt": coil_weight, "c_len": actual_length
            }
        )
        s.commit()

def split_cloud_material(parent_id, children_list):
    """Splits a parent coil into a dynamic number of child coils and zeros out the parent stock."""
    with conn.session as s:
        parent_res = s.execute(
            text("SELECT * FROM materials WHERE id = :id"), 
            {"id": int(parent_id)}
        ).mappings().first()
        
        if not parent_res:
            raise Exception("Parent material not found.")

        parent_lotto = parent_res['lotto_number'] if parent_res['lotto_number'] else parent_res['lotto_figlio']

        # Insert each child dynamically
        for child in children_list:
            s.execute(
                text("""
                    INSERT INTO materials (
                        parent_id, family, hub_section, grade, thickness, 
                        yield_mpa, uts_mpa, elongation_pct, coil_weight_kg, 
                        coil_length_mm, rd_remaining_weight_kg, rd_remaining_length_mm, 
                        lotto_number, lotto_figlio, provider, certificate_url, is_promoted
                    ) VALUES (
                        :parent_id, :family, :hub_section, :grade, :thickness, 
                        :yield_mpa, :uts_mpa, :elongation_pct, :coil_weight_kg, 
                        :coil_length_mm, :rd_remaining_weight_kg, :rd_remaining_length_mm, 
                        :lotto_number, :lotto_figlio, :provider, :certificate_url, 0
                    )
                """),
                {
                    "parent_id": parent_id,
                    "family": parent_res.get("family", "METALS"),
                    "hub_section": parent_res.get("hub_section", "PROD"),
                    "grade": parent_res["grade"],
                    "thickness": parent_res["thickness"],
                    "yield_mpa": parent_res["yield_mpa"],
                    "uts_mpa": parent_res["uts_mpa"],
                    "elongation_pct": parent_res["elongation_pct"],
                    "coil_weight_kg": child["weight"],
                    "coil_length_mm": child["length"],
                    "rd_remaining_weight_kg": child["weight"],
                    "rd_remaining_length_mm": child["length"],
                    "lotto_number": parent_lotto,
                    "lotto_figlio": child["lotto"],
                    "provider": parent_res["provider"],
                    "certificate_url": parent_res.get("certificate_url")
                }
            )

        # Zero out and deactivate Parent
        s.execute(
            text("""
                UPDATE materials 
                SET rd_remaining_weight_kg = 0.0, 
                    rd_remaining_length_mm = 0.0 
                WHERE id = :id
            """),
            {"id": int(parent_id)}
        )
        s.commit()

def log_material_consumption(material_id, weight_used_kg, length_used_mm, notes=""):
    """Deducts used weight/length from the selected material batch safely."""
    with conn.session as s:
        s.execute(
            text("""
                UPDATE materials 
                SET rd_remaining_weight_kg = GREATEST(0.0, COALESCE(rd_remaining_weight_kg, coil_weight_kg) - :w_used),
                    rd_remaining_length_mm = GREATEST(0.0, COALESCE(rd_remaining_length_mm, coil_length_mm) - :l_used),
                    rd_notes = CASE 
                        WHEN rd_notes IS NULL OR rd_notes = '' THEN :notes
                        ELSE rd_notes || ' | ' || :notes
                    END
                WHERE id = :mat_id
            """),
            {"w_used": weight_used_kg, "l_used": length_used_mm, "notes": notes, "mat_id": int(material_id)}
        )
        s.commit()

def promote_cloud_material(material_id):
    """Marks batch as promoted while keeping it visible in Production Hub."""
    with conn.session as s:
        s.execute(text('UPDATE materials SET is_promoted = 1 WHERE id = :id'), {"id": int(material_id)})
        s.commit()

def delete_cloud_material(material_id):
    """Deletes a material batch, safely handling child foreign key constraints by unlinking."""
    with conn.session as s:
        # Unlink any child records pointing to this parent before deleting
        s.execute(
            text('UPDATE materials SET parent_id = NULL WHERE parent_id = :id'), 
            {"id": int(material_id)}
        )
        # Now safely delete the target record
        s.execute(
            text('DELETE FROM materials WHERE id = :id'), 
            {"id": int(material_id)}
        )
        s.commit()

def demote_cloud_material(material_id):
    """Un-promote batch so it returns to production and leaves R&D deck."""
    with conn.session as s:
        s.execute(text('''
            UPDATE materials
            SET is_promoted = 0,
                experimental_curves_json = NULL,
                calculated_sigy_mpa = NULL,
                calculated_uts_mpa = NULL,
                calculated_e_gpa = NULL,
                calculated_elongation_pct = NULL
            WHERE id = :id
        '''), {"id": int(material_id)})
        s.commit()
    if material_id in st.session_state.get("experimental_curves", {}):
        del st.session_state.experimental_curves[material_id]
    st.cache_data.clear()

# Update your function definition to accept 'lotto_figlio'
def update_cloud_material(
    mat_id, 
    grade, 
    thickness, 
    yield_mpa, 
    uts_mpa, 
    elongation_pct, 
    lotto_number, 
    lotto_figlio, 
    provider, 
    coil_weight_kg, 
    coil_length_mm
):
    with conn.session as s:
        s.execute(
            text("""
                UPDATE materials SET 
                    grade = :grade,
                    thickness = :thickness,
                    yield_mpa = :yield_mpa,
                    uts_mpa = :uts_mpa,
                    elongation_pct = :elongation_pct,
                    lotto_number = :lotto_number,
                    lotto_figlio = :lotto_figlio,
                    provider = :provider,
                    coil_weight_kg = :coil_weight_kg,
                    coil_length_mm = :coil_length_mm
                WHERE id = :id
            """),
            params={
                "id": int(mat_id),
                "grade": grade,
                "thickness": float(thickness),
                "yield_mpa": float(yield_mpa),
                "uts_mpa": float(uts_mpa),
                "elongation_pct": float(elongation_pct),
                "lotto_number": lotto_number,
                "lotto_figlio": lotto_figlio, 
                "provider": provider,
                "coil_weight_kg": float(coil_weight_kg),
                "coil_length_mm": float(coil_length_mm)
            }
        )
        s.commit()



# =========================================================================
# ### SECTION 2: REGULATORY COMPLIANCE HELPER FUNCTIONS ###
# =========================================================================
def get_en10025_limits(grade, thickness):
    """Evaluates standard EN 10025 structural limits based on grade and thickness."""
    clean_grade = str(grade).upper().replace("JR", "").strip()
    if "S355" in clean_grade:
        y = 355 if thickness <= 16.0 else 345
        uts_min, uts_max = 470, 630
    elif "S275" in clean_grade:
        y = 275 if thickness <= 16.0 else 265
        uts_min, uts_max = 410, 560
    elif "S500" in clean_grade:
        y = 500 if thickness <= 16.0 else 490
        uts_min, uts_max = 590, 720
    else:
        y = 235 if thickness <= 16.0 else 225
        uts_min, uts_max = 360, 510
    return {"min_yield": y, "min_uts": uts_min, "max_uts": uts_max}

#### upload chemical composition.. from LAB  

def parse_metal_power_excel(uploaded_file):
    """Parses Metal Power Spectrometer Excel reports, drops Date/Time, and cleans symbol values."""
    try:
        df_raw = pd.read_excel(uploaded_file, header=None, engine="calamine")
        
        header_row_idx = None
        for idx, row in df_raw.iterrows():
            row_str_vals = [str(val).strip() for val in row.values if val is not None]
            if "Elements" in row_str_vals or any("C (%)" in val for val in row_str_vals):
                header_row_idx = idx
                break
        
        if header_row_idx is not None:
            df_raw.columns = [str(col).strip() for col in df_raw.iloc[header_row_idx].values]
            df = df_raw.iloc[header_row_idx + 1:].reset_index(drop=True)
        else:
            df = df_raw
            df.columns = [str(col).strip() for col in df.iloc[0].values]
            df = df.iloc[1:].reset_index(drop=True)
            
        df.columns = [str(col).strip() for col in df.columns]
        
        # Drop Date/Time column if it exists
        if "Date/Time" in df.columns:
            df = df.drop(columns=["Date/Time"])
            
        mean_row = df[df.iloc[:, 0].astype(str).str.contains("Mean", case=False, na=False)]
        if not mean_row.empty:
            target_data = mean_row.iloc[0]
        else:
            target_data = df.iloc[-1]
            
        composition = {}
        for col in df.columns:
            if "(%)" in col:
                val_str = str(target_data[col]).strip()
                # Remove '<', '>', '-', and extra whitespace
                clean_val = val_str.replace("<", "").replace(">", "").replace("-", "").strip()
                try:
                    composition[col] = float(clean_val)
                except ValueError:
                    composition[col] = 0.0  
                    
        return df, composition
    except Exception as e:
        st.error(f"Error parsing spectrometer report: {e}")
        return None, None
###########################  END OF CHEMICAL COMPOSITION ##################

# =========================================================================
# ### SECTION 3: APPLICATION NAVIGATION INITIALIZATION ###
# =========================================================================
if "current_page" not in st.session_state:
    st.session_state.current_page = "HOME"
if "selected_material" not in st.session_state:
    st.session_state.selected_material = None
if "active_family" not in st.session_state:
    st.session_state.active_family = "METALS"
if "experimental_curves" not in st.session_state:
    st.session_state.experimental_curves = {}
if "chemical_reports" not in st.session_state:
    st.session_state.chemical_reports = {}
################################################################################
if "curves_loaded_from_cloud" not in st.session_state:
    try:
        df_cloud_curves = conn.query("SELECT id, experimental_curves_json FROM materials WHERE experimental_curves_json IS NOT NULL;", ttl=0)
        
        for _, row in df_cloud_curves.iterrows():
            m_id = row.get("id")
            mech_json = row.get("experimental_curves_json")
            
            if isinstance(mech_json, str):
                import json
                try:
                    mech_json = json.loads(mech_json)
                except Exception:
                    mech_json = {}
                    
            if mech_json and isinstance(mech_json, dict):
                st.session_state.experimental_curves[int(m_id)] = {
                    "strain": np.array(mech_json.get("strain", [])),
                    "stress_MPa": np.array(mech_json.get("stress_MPa", [])),
                    "sigy_MPa": mech_json.get("sigy_MPa"),
                    "uts_MPa": mech_json.get("uts_MPa"),
                    "E_GPa": mech_json.get("E_GPa"),
                    "elongation_pct": mech_json.get("elongation_pct"),
                    "avg_strain": np.array(mech_json.get("avg_strain", [])),
                    "avg_stress": np.array(mech_json.get("avg_stress", [])),
                    "eng_strain_clean": np.array(mech_json.get("eng_strain_clean", [])),
                    "eng_stress_clean": np.array(mech_json.get("eng_stress_clean", [])),
                    "deck": mech_json.get("deck")
                }
        st.session_state.curves_loaded_from_cloud = True
    except Exception as e:
        pass

# =========================================================================
# ### SECTION 4: HOME PAGE (CENTRAL PORTAL LANDING) ###
# =========================================================================
if st.session_state.current_page == "HOME":
    logo_left, logo_mid, logo_right = st.columns([3, 1, 3])
    with logo_mid:
        if os.path.exists("imeva_logo.jpg"):
            st.image("imeva_logo.jpg", width=200)
        elif os.path.exists("imeva.jpg"):
            st.image("imeva.jpg", width=200)
            
    st.markdown("<h1 style='text-align: center; color: #28A745;'>IMEVA Materials Hub</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; font-weight: 600;'>Select a material family and choose either the R&D Deck or Production Hub.</p>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #666; font-style: italic; margin-top: -10px;'>Azienda leader nella produzione di barriere stradali — Benvenuti</p>", unsafe_allow_html=True)
    st.write("---")

    # --- BANNER IMAGE (Centered and Compact) ---
    if os.path.exists("road_2.jpg"):
        img_left_pad, img_center, img_right_pad = st.columns([1.5, 2, 1.5])
        with img_center:
            st.image("road_2.jpg", use_container_width=True, caption="IMEVA Road Safety Barrier Installation")

    st.write("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("### Metals Family")
        st.caption("Structural Steel, EN 10025 compliance, tensile strain curves, and batch tracking.")
        box_rd, box_prod = st.columns(2)
        with box_rd:
            st.info("R&D Deck")
            if st.button("Open R&D", key="metals_rd_btn", use_container_width=True):
                st.session_state.active_family = "METALS"
                st.session_state.current_page = "RD_DECK"
                st.rerun()
        with box_prod:
            st.success("Production Hub")
            if st.button("Open Prod", key="metals_prod_btn", use_container_width=True):
                st.session_state.active_family = "METALS"
                st.session_state.current_page = "PROD_HUB"
                st.rerun()

    with col2:
        st.markdown("### Polymers Family")
        st.caption("Viscoelastic/viscoplastic material models & extrusion quality tracking.")
        box_rd, box_prod = st.columns(2)
        with box_rd:
            st.info("R&D Deck")
            st.button("Locked", key="poly_rd_btn", disabled=True, use_container_width=True)
        with box_prod:
            st.success("Production Hub")
            st.button("Locked", key="poly_prod_btn", disabled=True, use_container_width=True)

    with col3:
        st.markdown("### Composites Family")
        st.caption("Orthotropic failure criteria, layups, & coupon production tracking.")
        box_rd, box_prod = st.columns(2)
        with box_rd:
            st.info("R&D Deck")
            st.button("Locked", key="comp_rd_btn", disabled=True, use_container_width=True)
        with box_prod:
            st.success("Production Hub")
            st.button("Locked", key="comp_prod_btn", disabled=True, use_container_width=True)
# =========================================================================
# ### SECTION 5: R&D LABORATORY DECK PAGE ###
# =========================================================================
elif st.session_state.current_page == "RD_DECK":
    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()
        
    family_name = st.session_state.active_family.title()
    st.title(f"{family_name} R&D Laboratory Deck")
    st.write("---")
    
    sq_rd_df = fetch_cloud_materials("RD", family=st.session_state.active_family)
    
    combined_materials = {}
    for _, row in sq_rd_df.iterrows():
        mat_id = int(row['id'])
        
        # --- [BLOCK 2] AUTO-RESTORE MECHANICAL DATA FROM SUPABASE IF NOT IN SESSION ---
        if mat_id not in st.session_state.experimental_curves and pd.notna(row.get('experimental_curves_json')):
            try:
                raw_mech = row['experimental_curves_json']
                mech_data = json.loads(raw_mech) if isinstance(raw_mech, str) else raw_mech
                st.session_state.experimental_curves[mat_id] = {
                    "strain": np.array(mech_data.get("strain", [])),
                    "stress_MPa": np.array(mech_data.get("stress_MPa", [])),
                    "sigy_MPa": mech_data.get("sigy_MPa", 0.0),
                    "uts_MPa": mech_data.get("uts_MPa", 0.0),
                    "E_GPa": mech_data.get("E_GPa", 0.0),
                    "elongation_pct": mech_data.get("elongation_pct", 0.0),
                    "avg_strain": np.array(mech_data.get("avg_strain", [])),
                    "avg_stress": np.array(mech_data.get("avg_stress", [])),
                    "eng_strain_clean": np.array(mech_data.get("eng_strain_clean", [])),
                    "eng_stress_clean": np.array(mech_data.get("eng_stress_clean", [])),
                    "deck": mech_data.get("deck", "")
                }
            except Exception:
                pass
        # --------------------------------------------------------------------

        grade_str = str(row['grade'])
        thick_val = float(row['thickness'])
        lotto_str = str(row['lotto_number']) if pd.notna(row['lotto_number']) else "N/A"
        cert_sy = float(row['yield_mpa'])
        cert_sf = float(row['uts_mpa'])
        
        # --- EXTRACT COIL METRICS & R&D TRACKING FIELDS ---
        c_weight = float(row.get('coil_weight_kg') or 0.0)
        c_length = float(row.get('coil_length_mm') or 0.0)
        rd_rem_weight = float(row.get('rd_remaining_weight_kg') if pd.notna(row.get('rd_remaining_weight_kg')) else c_weight)
        rd_rem_length = float(row.get('rd_remaining_length_mm') if pd.notna(row.get('rd_remaining_length_mm')) else c_length)
        rd_notes_val = str(row.get('rd_notes') or "")
        
        #st.markdown(f"""
        #**Remaining Stock:** **{rd_rem_weight:.1f} kg** / **{rd_rem_length:.1f} mm**  
        #*(Original Baseline: {c_weight:.1f} kg / {c_length:.1f} mm)*  
       # 📝 **Notes / Log History:** {rd_notes_val if rd_notes_val else "No cuts logged yet."}
       # """)
        
        lab_data = st.session_state.experimental_curves.get(mat_id, {})
        lab_sy = f"{lab_data.get('sigy_MPa', 0.0):.0f}" if lab_data else "-"
        lab_sf = f"{lab_data.get('uts_MPa', 0.0):.0f}" if lab_data else "-"

        display_label = (
            f"{grade_str}-SP{thick_val:.1f}-{lotto_str} "
            f"[Cert: {cert_sy:.0f}, {cert_sf:.0f} | Lab: {lab_sy}, {lab_sf}]"
        )
        
        combined_materials[display_label] = {
            "id": mat_id,
            "grade": grade_str,
            "thickness": thick_val,
            "cert_yield_MPa": cert_sy,
            "cert_uts_MPa": cert_sf,
            "lotto": lotto_str,
            "coil_weight_kg": c_weight,
            "coil_length_mm": c_length,
            "rd_remaining_weight_kg": rd_rem_weight,
            "rd_remaining_length_mm": rd_rem_length,
            "rd_notes": rd_notes_val,
            "datasheet": row['datasheet'] if pd.notna(row['datasheet']) else None
        }

    # ==========================================
    # PROFESSIONAL STACKED FILTER HUB
    # ==========================================
    st.sidebar.markdown("Property Filters")
    
    available_grades = sorted(list(set(info["grade"] for info in combined_materials.values()))) if combined_materials else ["All"]
    filter_grade = st.sidebar.selectbox("Steel Class / Grade", ["All"] + available_grades)

    st.sidebar.write("---")

    st.sidebar.markdown("Thickness")
    enable_thick_filter = st.sidebar.checkbox("Filter by Exact Thickness", value=True)
    
    available_thicknesses = sorted(list(set(meta.get("thickness", 3.0) for meta in combined_materials.values()))) if combined_materials else [3.0]
    
    selected_thickness = st.sidebar.number_input(
        "Nominal Thickness [mm]", 
        min_value=1.0, 
        max_value=20.0, 
        value=float(available_thicknesses[0]) if available_thicknesses else 3.0, 
        step=0.1, 
        format="%.1f"
    ) if enable_thick_filter else None

    st.sidebar.write("---")

    with st.sidebar.expander(" Certificate or/& Laboratory", expanded=True):
        cert_status_filter = st.radio( 
            "Select Data mode",
            ["All Batches", "Certificate", "Laboratory"]
        )

    with st.sidebar.expander("Mech. prop", expanded=True):
        min_yield, max_yield = st.slider(
            "Yield Stress Range (σ_yield) [MPa]", 
            min_value=100.0, max_value=1000.0, value=(100.0, 1000.0), step=10.0
        )
        min_uts, max_uts = st.slider(
            "Tensile Stress Range (σ_uts) [MPa]", 
            min_value=100.0, max_value=1200.0, value=(100.0, 1200.0), step=10.0
        )
        min_elong, max_elong = st.slider(
            "Elongation Range [%]",
            min_value=0.0, max_value=50.0, value=(0.0, 50.0), step=0.5
        )

    # ==========================================
    # FILTERING EXECUTION LOGIC
    # ==========================================
    # 2. Main Layout Filter Logic
    filtered_materials = {}
    for name, metadata in combined_materials.items():
        grade_match = (filter_grade == "All" or metadata.get("grade") == filter_grade)
        thick_val = metadata.get("thickness", 0)
        thickness_match = True if not enable_thick_filter else (abs(thick_val - selected_thickness) <= 0.05)
        
        mat_id = metadata.get("id")
        
        if cert_status_filter == "Certificate":
            active_yield = metadata.get("cert_yield_MPa", 0)
            active_uts = metadata.get("cert_uts_MPa", 0)
            active_elong = metadata.get("elongation_pct", 0)
            cert_match = True
        elif cert_status_filter == "Laboratory":
            session_data = st.session_state.experimental_curves.get(mat_id, {})
            active_yield = session_data.get("sigy_MPa", metadata.get("sigy_MPa", 0))
            active_uts = session_data.get("uts_MPa", metadata.get("uts_MPa", 0))
            active_elong = session_data.get("elongation_pct", session_data.get("elongation", metadata.get("elongation_pct", 0)))
            
            has_metadata_lab = metadata.get("sigy_MPa") is not None
            has_session_lab = mat_id in st.session_state.experimental_curves
            cert_match = bool(has_metadata_lab or has_session_lab)
        else: 
            active_yield = metadata.get("sigy_MPa", metadata.get("cert_yield_MPa", 0))
            active_uts = metadata.get("uts_MPa", metadata.get("cert_uts_MPa", 0))
            active_elong = metadata.get("elongation_pct", 0)
            cert_match = True

        yield_match = min_yield <= active_yield <= max_yield
        uts_match = min_uts <= active_uts <= max_uts
        elong_match = min_elong <= active_elong <= max_elong

        if grade_match and thickness_match and yield_match and uts_match and elong_match and cert_match:
            filtered_materials[name] = metadata

    main_col, chart_col = st.columns([1, 1])

    # 3. Apply the loop with clean, deduplicated card columns
    with main_col:
        st.subheader("Available Inventory & Promoted Batches")
        st.caption(" Format: `[Cert: YS, UTS | Lab: YS, UTS]`")
        if not combined_materials:
            st.info(" No materials promoted to R&D yet. Add and promote batches from the Production Hub.")
        elif not filtered_materials:
            st.warning("No promoted materials match the current sidebar filter limits.")
        else:
            for mat_name, info in filtered_materials.items():
                limits = get_en10025_limits(info["grade"], info["thickness"])
                is_compliant = info["cert_yield_MPa"] >= limits["min_yield"]
                
                rem_len = info["rd_remaining_length_mm"]
                rem_weight = info["rd_remaining_weight_kg"]
                orig_weight = info["coil_weight_kg"]
                inv_badge = "🟢 Available" if rem_len > 10.0 else "🔴 Low Stock"
                status_badge = "✅ EN Compliant" if is_compliant else "❌ Non-Compliant"
                st.markdown(f"**Status:** {status_badge} | {inv_badge} | **Stock:** {rem_weight:.1f} kg / {rem_len:.1f} mm")
                
                card_col1, card_col2 = st.columns([3, 1])
                with card_col1:
                    title_col, prev_col = st.columns([4, 1])
                    with title_col:
                        st.markdown(f"#### `{mat_name}`")
                    with prev_col:
                        cert_url = info.get("datasheet")
                        if cert_url:
                            # Modal Trigger Button
                            if st.button("🔍 View Cert", key=f"btn_cert_{info['id']}"):
                                show_cert_modal(mat_name, cert_url, info)
                        else:
                            st.markdown("*(No Cert)*")
                            
                    #st.caption(f"Status: {status_badge} | {inv_badge} | Remaining Stock: `{rem_weight:.1f} kg` / Original: `{orig_weight} kg`")

                with card_col2:
                    # Un-promote Button
                    if st.button("↩️ Un-promote", key=f"demote_rd_{info['id']}", help="Send back to Production Hub"):
                        demote_cloud_material(info['id'])
                        st.toast("Material un-promoted and sent back to Production!")
                        st.rerun()
                #st.markdown(
                #    f"**Remaining Stock:** **{info['rd_remaining_weight_kg']:.1f} kg** / **{info['rd_remaining_length_mm']:.1f} mm**  \n"
                #    f"*(Original Baseline: {info['coil_weight_kg']:.1f} kg / {info['coil_length_mm']:.1f} mm)*  \n"
                #    f"📝 **Notes / Log History:** {info['rd_notes'] if info['rd_notes'] else 'No cuts logged yet.'}"
                #) 

                # Analyze Button
                if st.button(f"Analyze & Process Data", key=f"btn_{info['id']}", use_container_width=True):
                    st.session_state.selected_material = mat_name
                
                st.divider()

    # 4. Maintain the Charts Column 
    with chart_col:
        st.subheader("Inventory Properties Group Overview")
        if filtered_materials:
            batches = list(filtered_materials.keys())
            yields = [info["cert_yield_MPa"] for info in filtered_materials.values()]
            utss = [info["cert_uts_MPa"] for info in filtered_materials.values()]

            x = np.arange(len(batches))
            width = 0.35

            fig_bar, ax_bar = plt.subplots(figsize=(6, 3.8))
            rects1 = ax_bar.bar(x - width/2, yields, width, label='σ_yield (MPa)', color='#1E3A8A')
            rects2 = ax_bar.bar(x + width/2, utss, width, label='σ_uts / failure (MPa)', color='#D97706')

            ax_bar.set_ylabel('Stress [MPa]')
            ax_bar.set_title('Strength Properties Comparison')
            ax_bar.set_xticks(x)
            ax_bar.set_xticklabels(batches, rotation=15, ha='right', fontsize=8)
            ax_bar.legend()
            ax_bar.grid(axis='y', linestyle=':', alpha=0.7)
            
            ax_bar.bar_label(rects1, padding=3, fmt='%.0f')
            ax_bar.bar_label(rects2, padding=3, fmt='%.0f')

            fig_bar.tight_layout()
            st.pyplot(fig_bar)
            
            st.subheader("📈 Multi-Material Comparative Curves")
            fig_comp, ax_comp = plt.subplots(figsize=(6, 3.8))
            curves_plotted = 0

            for mat_name, info in filtered_materials.items():
                mat_id = info["id"]
                if mat_id in st.session_state.experimental_curves:
                    exp = st.session_state.experimental_curves[mat_id]
                    st.write("Debug info keys:", list(info.keys()))
                    st.json(info)
                    lot_name = info.get('lotto_number') or info.get('lotto_figlio') or 'N/A'
                    ax_comp.plot(exp["strain"], exp["stress_MPa"], label=f"Lot: {lot_name}", lw=2)
                    #ax_comp.plot(exp["strain"], exp["stress_MPa"], label=f"{info['grade']} ({info.get('lotto_number', 'N/A')})", lw=2)
                    curves_plotted += 1

            if curves_plotted > 0:
                ax_comp.set_xlabel("Strain [-]")
                ax_comp.set_ylabel("Stress [MPa]")
                ax_comp.set_title("Experimental Stress-Strain Overlay")
                ax_comp.grid(True, linestyle=":")
                ax_comp.legend()
                fig_comp.tight_layout()
                st.pyplot(fig_comp)
            else:
                st.info("Click 'Analyze & Process Data' below to upload CSV dog bone tests for curve plotting.")
        else:
            st.caption("Awaiting inventory entries to generate strength comparison bars.")

    # ---------------------------------------------------------------------
    # ### SUB-SECTION: DETAILED REVIEW & EXPERIMENTAL MULTI-CSV ANALYSIS ###
    # ---------------------------------------------------------------------
    if st.session_state.selected_material and st.session_state.selected_material in combined_materials:
        mat_name = st.session_state.selected_material
        mat_info = combined_materials[mat_name]
        mat_id = mat_info["id"]
        
        st.write("---")
        st.header(f"📊 Detailed Review Deck: {mat_name}")
        
        props_col, action_col = st.columns([1, 1])
        
        with props_col:
            st.subheader("📜 Material Mill Certificate Parameters")
            st.write(f"**Structural Grade:** {mat_info['grade']}")
            st.write(f"**Nominal Thickness:** {mat_info['thickness']} mm")
            st.write(f"**Lotto :** {mat_info['lotto']}")
            st.write(f"**Yield Stress ($R_e$):** {mat_info['cert_yield_MPa']} MPa")
            st.write(f"**Tensile Stress ($R_m$):** {mat_info['cert_uts_MPa']} MPa")

            # --- R&D CONSUMPTION & USAGE TRACKER ---
            st.markdown("---")
            st.subheader("📦 Log Material Usage")
            
            # Fetch initial and remaining safely using millimeters
            init_w = float(mat_info.get("coil_weight_kg") or 0.0)
            init_l = float(mat_info.get("coil_length_mm") or 0.0)
            curr_rem_w = float(mat_info.get("rd_remaining_weight_kg") if mat_info.get("rd_remaining_weight_kg") is not None else init_w)
            curr_rem_l = float(mat_info.get("rd_remaining_length_mm") if mat_info.get("rd_remaining_length_mm") is not None else init_l)

            st.markdown(f"""
            **Remaining Stock:** **{curr_rem_w:.1f} kg** / **{curr_rem_l:.1f} mm**  
            *(Original Baseline: {init_w:.1f} kg / {init_l:.1f} mm)*
            """)

            with st.form(key=f"rd_tracker_form_{mat_id}"):
                col_w1, col_w2 = st.columns(2)
                with col_w1:
                    weight_consumed = st.number_input(
                        "Weight Used [kg]", 
                        value=0.0, 
                        step=0.1,
                        help="Amount of material consumed for this cutting operation."
                    )
                with col_w2:
                    length_consumed = st.number_input(
                        "Length Used [mm]", 
                        value=0.0, 
                        step=100.0,
                        help="Length cut for tensile dog bones."
                    )
                
                usage_notes = st.text_input(
                    "Usage Purpose / Notes", 
                    value="Cut for tensile dog bone batch",
                    placeholder="e.g., Prepared 3 dog bone specimens"
                )
                
                if st.form_submit_button("📉 Deduct & Update Inventory", use_container_width=True):
                    try:
                        log_material_consumption(
                            material_id=mat_id,
                            weight_used_kg=weight_consumed,
                            length_used_mm=length_consumed,
                            notes=usage_notes
                        )
                        st.success("Material usage logged and inventory deducted successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to record consumption: {e}")

            st.markdown("---")
            st.subheader("🔬 Chemical Composition Analysis")

            uploaded_spec = st.file_uploader("Upload Spectrometer Report (Excel/CSV)", type=["xlsx", "csv"], key=f"spec_{mat_id}")
        
        if uploaded_spec is not None:
            df_spec, comp_dict = parse_metal_power_excel(uploaded_spec)
            if comp_dict is not None:
                st.session_state.chemical_reports[mat_id] = {"df": df_spec, "comp": comp_dict}
                
                temp_numeric = {
                    k.replace(" (%)", ""): v for k, v in comp_dict.items() 
                    if isinstance(v, (int, float)) and "Fe" not in k
                }
                
                try:
                    c_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "C"), 0.0)
                    mn_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MN"), 0.0)
                    cr_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CR"), 0.0)
                    mo_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MO"), 0.0)
                    v_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "V"), 0.0)
                    ni_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "NI"), 0.0)
                    cu_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CU"), 0.0)
                    calc_cev = float(c_v) + float(mn_v)/6.0 + (float(cr_v)+float(mo_v)+float(v_v))/5.0 + (float(ni_v)+float(cu_v))/15.0
                except Exception:
                    calc_cev = 0.0

                try:
                    df_json_records = df_spec.to_dict(orient="records") if df_spec is not None else []
                    spec_payload = json.dumps({
                        "df_records": df_json_records,
                        "comp": comp_dict
                    })
                    
                    c_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "C"), 0.0)
                    si_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "SI"), 0.0)
                    mn_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MN"), 0.0)
                    p_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "P"), 0.0)
                    s_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "S"), 0.0)
                    cr_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CR"), 0.0)
                    mo_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MO"), 0.0)
                    ni_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "NI"), 0.0)
                    cu_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CU"), 0.0)
                    al_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "AL"), 0.0)
                    as_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "AS"), 0.0)
                    nb_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "NB"), 0.0)
                    sn_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "SN"), 0.0)
                    ti_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "TI"), 0.0)
                    w_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "W"), 0.0)
                    b_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "B"), 0.0)
                    co_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CO"), 0.0)
                    pb_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "PB"), 0.0)
                    v_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "V"), 0.0)
                    zn_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "ZN"), 0.0)
                    mg_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MG"), 0.0)
                    ce_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CE"), 0.0)
                    la_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "LA"), 0.0)

                    with conn.session as s:
                        s.execute(
                            text("""
                                UPDATE materials SET 
                                    carbon_equivalent_cev = :cev,
                                    c_pct = :c, si_pct = :si, mn_pct = :mn, p_pct = :p, s_pct = :s,
                                    cr_pct = :cr, mo_pct = :mo, ni_pct = :ni, cu_pct = :cu, al_pct = :al,
                                    as_pct = :as, nb_pct = :nb, sn_pct = :sn, ti_pct = :ti, w_pct = :w,
                                    b_pct = :b, co_pct = :co, pb_pct = :pb, v_pct = :v, zn_pct = :zn,
                                    mg_pct = :mg, ce_pct = :ce, la_pct = :la,
                                    spectrometer_data_json = :spec_json
                                WHERE id = :id
                            """),
                            params={
                                "cev": float(calc_cev),
                                "c": float(c_val_db), "si": float(si_val_db), "mn": float(mn_val_db),
                                "p": float(p_val_db), "s": float(s_val_db), "cr": float(cr_val_db),
                                "mo": float(mo_val_db), "ni": float(ni_val_db), "cu": float(cu_val_db),
                                "al": float(al_val_db), "as": float(as_val_db), "nb": float(nb_val_db),
                                "sn": float(sn_val_db), "ti": float(ti_val_db), "w": float(w_val_db),
                                "b": float(b_val_db), "co": float(co_val_db), "pb": float(pb_val_db),
                                "v": float(v_val_db), "zn": float(zn_val_db), "mg": float(mg_val_db),
                                "ce": float(ce_val_db), "la": float(la_val_db),
                                "spec_json": spec_payload,
                                "id": int(mat_id)
                            }
                        )
                        s.commit()
                    st.toast("Spectrometer analysis saved to cloud via SQL!", icon="☁️")
                except Exception as db_err:
                    st.warning(f"Cloud sync error: {db_err}")
        
        if mat_id in st.session_state.chemical_reports:
            saved_spec = st.session_state.chemical_reports[mat_id]
            st.success("Spectrometer Data Loaded Successfully!")
            st.dataframe(saved_spec["df"], use_container_width=True)
            
            numeric_comp = {
                k.replace(" (%)", ""): v for k, v in saved_spec["comp"].items() 
                if isinstance(v, (int, float)) and "Fe" not in k
            }
            
            if numeric_comp:
                st.markdown("#### 📊 Alloy Elements Distribution (Excluding Base Iron)")
                fig_chem, ax_chem = plt.subplots(figsize=(6, 3))
                ax_chem.bar(list(numeric_comp.keys()), list(numeric_comp.values()), color="#28A745")
                ax_chem.set_ylabel("Concentration [%]")
                ax_chem.grid(axis="y", linestyle=":", alpha=0.7)
                plt.xticks(rotation=45, ha="right")
                fig_chem.tight_layout()
                st.pyplot(fig_chem)
            
            try:
                c_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "C"), 0.0)
                mn_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "MN"), 0.0)
                cr_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "CR"), 0.0)
                mo_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "MO"), 0.0)
                v_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "V"), 0.0)
                ni_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "NI"), 0.0)
                cu_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "CU"), 0.0)
                
                p_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "P"), 0.0)
                s_val = next((v for k, v in numeric_comp.items() if k.strip().upper() == "S"), 0.0)
                
                cev = float(c_val) + float(mn_val)/6.0 + (float(cr_val)+float(mo_val)+float(v_val))/5.0 + (float(ni_val)+float(cu_val))/15.0
                total_impurities = float(p_val) + float(s_val)
                
                is_clean = (float(p_val) <= 0.030) and (float(s_val) <= 0.100)
                impurity_status = "✅ Clean (Pass)" if is_clean else "❌ High Impurities (Fail)"
                
                col_m1, col_m2, col_m3 = st.columns(3)
                with col_m1:
                    st.metric(label="Carbon Equivalent (CEV IIW)", value=f"{cev:.3f}%")
                with col_m2:
                    st.metric(label="Total Impurities (P + S)", value=f"{total_impurities:.3f}%")
                with col_m3:
                    st.metric(label="Metallurgical Cleanliness", value=impurity_status)
            except Exception:
                st.metric(label="Calculated Carbon Equivalent (CEV IIW)", value="N/A")


 
# =========================================================================
# ### SECTION 6: PRODUCTION QUALITY CONTROL HUB PAGE ###
# =========================================================================
elif st.session_state.current_page == "PROD_HUB":
    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()
        
    family_name = st.session_state.active_family.title()
    st.title(f"{family_name} — Production Quality Control Hub")
    st.write("---")
    
    entry_tab1, entry_tab2 = st.tabs(["✍️ Manual Form Entry", "📁 File Upload (.txt / .csv / .xlsx)"])
    
    with entry_tab1:
        with st.form("prod_form_manual", clear_on_submit=True):
            ca, cb = st.columns(2)
            with ca:
                grade = st.text_input("1. Material Grade / Name", value="S500MC")
                thickness = st.number_input("2. Thickness [mm]", value=3.0, step=0.1)
                sig_yield = st.number_input("3. Yield Stress σ_yield [MPa]", value=550.0, step=5.0)
                sig_fail = st.number_input("4. Ultimate Stress σ_uts [MPa]", value=598.0, step=5.0)
                elongation = st.number_input("5. Elongation [%]", value=19.0, step=0.5)
            with cb:
                lotto_padre = st.text_input("6. Lotto Padre", value="LOT-2026-X01")
                lotto_figlio = st.text_input("7. Lotto Figlio", value="")
                provider = st.text_input("8. Material Provider", value="IMEVA")
                coil_weight = st.number_input("9. Coil Weight [kg]", value=1500.0, step=50.0)
                coil_length = st.number_input("10. Coil Length [mm]", value=250.0, step=1000.0)
            
            uploaded_cert = st.file_uploader("Upload Test Certificate (.pdf, .png, .jpg)", type=["pdf", "png", "jpg", "jpeg"])
            
            if st.form_submit_button("💾 Save Batch ", use_container_width=True):
                cert_url = None
                active_lotto_label = lotto_padre if lotto_padre else (lotto_figlio if lotto_figlio else "batch")
                
                if uploaded_cert is not None:
                    # Upload directly to Supabase storage bucket instead of local folder
                    cert_url = upload_certificate_to_cloud(uploaded_cert, active_lotto_label)

                insert_cloud_material(
                    grade, thickness, sig_yield, sig_fail, elongation, 
                    lotto_padre=lotto_padre, lotto_figlio=lotto_figlio, provider=provider, 
                    family=st.session_state.active_family,
                    cert_path=cert_url,
                    coil_weight=coil_weight,
                    coil_length_mm=coil_length
                )
                st.success("Batch registered into Production database!")
                st.rerun()

    with entry_tab2:
        uploaded_file = st.file_uploader("Upload Batch Import File", type=["txt", "csv", "xlsx"])
        if uploaded_file is not None:
            try:
                filename = uploaded_file.name.lower()
                if filename.endswith(".txt") or filename.endswith(".csv"):
                    df_upload = pd.read_csv(
                        uploaded_file, 
                        header=None,
                        names=["grade", "thickness", "yield_mpa", "uts_mpa", "elongation_pct", "lotto_number", "lotto_figlio", "provider", "coil_weight_kg", "coil_length_mm"]
                    )
                elif filename.endswith(".xlsx"):
                    df_upload = pd.read_excel(uploaded_file)
                    if "lotto_figlio" not in df_upload.columns:
                        df_upload["lotto_figlio"] = None

                for col in df_upload.select_dtypes(include=["object"]).columns:
                    df_upload[col] = df_upload[col].astype(str).str.strip()
                st.dataframe(df_upload, use_container_width=True)

                if st.button(" Import Batches to Database", use_container_width=True):
                    for _, r in df_upload.iterrows():
                        insert_cloud_material(
                            r["grade"], r["thickness"], r["yield_mpa"], 
                            r["uts_mpa"], r["elongation_pct"], 
                            lotto_padre=r.get("lotto_number"), lotto_figlio=r.get("lotto_figlio"), 
                            provider=r["provider"],
                            family=st.session_state.active_family,
                            coil_weight=r.get("coil_weight_kg", 0.0),
                            coil_length_mm=r.get("coil_length_mm", 0.0)
                        )
                    st.success("Successfully imported production batches!")
                    st.rerun()
            except Exception as e:
                st.error(f"Error reading file structure: {e}")

    st.write("---")
    
    prod_df = fetch_cloud_materials(family=st.session_state.active_family)
    
    if not prod_df.empty:
        pass

    st.write("---")
    
    p_col1, p_col2 = st.columns([1.2, 0.8])
    
    with p_col1:
            st.subheader(f" Production Batch Queue ({family_name})")
            if prod_df.empty:
                st.info("No production batches in database queue.")
            else:
                prod_df['current_weight_kg'] = prod_df['rd_remaining_weight_kg'].fillna(prod_df['coil_weight_kg'])
                prod_df['current_length_mm'] = prod_df['rd_remaining_length_mm'].fillna(prod_df['coil_length_mm'])

                # 1. MAKE SURE "id" IS INCLUDED HERE
                display_df = prod_df[[
                    "id", "grade", "thickness", "yield_mpa", "uts_mpa", "elongation_pct", 
                    "current_weight_kg", "current_length_mm", "lotto_number", "lotto_figlio", "provider", 
                    "rd_remaining_weight_kg", "is_promoted"
                ]].copy()
                
                def determine_status(row):
                    if row["rd_remaining_weight_kg"] is not None and float(row["rd_remaining_weight_kg"]) <= 0.0:
                        return "🔴 Not Available"
                    elif row["is_promoted"] == 1:
                        return "🚀 R&D Promoted"
                    else:
                        return "🟢 In Production"

                display_df["Status"] = display_df.apply(determine_status, axis=1)
                display_df = display_df.drop(columns=["rd_remaining_weight_kg", "is_promoted"])
                
                display_df = display_df.rename(columns={
                    "current_weight_kg": "Remaining Weight [kg]",
                    "current_length_mm": "Remaining Length [mm]",
                    "lotto_number": "Lotto Padre",
                    "lotto_figlio": "Lotto Figlio"
                })
                
                # 2. SET THE INDEX TO 'id' ON YOUR FORMATTED DATAFRAME AND DISPLAY IT
                st.dataframe(display_df.set_index('id'), use_container_width=True)
            
    with p_col2:
        st.subheader("Actions & Edits")
        if not prod_df.empty:
            selected_prod_id = st.selectbox(
                "Select Production Batch", 
                prod_df["id"].tolist(),
                format_func=lambda x: f"ID {x}: {prod_df[prod_df['id']==x]['grade'].values[0]} ({prod_df[prod_df['id']==x]['lotto_number'].fillna(prod_df[prod_df['id']==x]['lotto_figlio']).values[0]})"
            )
            
            selected_row = prod_df[prod_df['id'] == selected_prod_id].iloc[0]
            is_already_promoted = (selected_row["is_promoted"] == 1)

            b1, b2 = st.columns(2)
            with b1:
                if is_already_promoted:
                    st.info("Already in R&D")
                else:
                    if st.button("🚀 Promote to R&D", use_container_width=True):
                        promote_cloud_material(selected_prod_id)
                        st.success(f"Batch ID {selected_prod_id} promoted to R&D!")
                        st.rerun()
                        
            with b2:
                if "confirm_delete_id" not in st.session_state:
                    st.session_state.confirm_delete_id = None

                if st.session_state.confirm_delete_id == selected_prod_id:
                    st.warning("Are you sure?")
                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        if st.button("Yes, Delete", key=f"yes_del_{selected_prod_id}", use_container_width=True):
                            delete_cloud_material(selected_prod_id)
                            st.session_state.confirm_delete_id = None
                            st.success(f"Batch ID {selected_prod_id} deleted!")
                            st.rerun()
                    with col_no:
                        if st.button("Cancel", key=f"no_del_{selected_prod_id}", use_container_width=True):
                            st.session_state.confirm_delete_id = None
                            st.rerun()
                else:
                    if st.button("🗑️ Delete Batch", use_container_width=True):
                        st.session_state.confirm_delete_id = selected_prod_id
                        st.rerun()

            with st.expander("✏️ Edit Selected Batch Values"):
                with st.form(key=f"edit_form_{selected_prod_id}"):
                    new_grade = st.text_input("Grade", value=str(selected_row["grade"]))
                    new_thickness = st.number_input("Thickness [mm]", value=float(selected_row["thickness"]), step=0.1)
                    new_yield = st.number_input("Yield Stress [MPa]", value=float(selected_row["yield_mpa"]), step=5.0)
                    new_uts = st.number_input("Failure Stress [MPa]", value=float(selected_row["uts_mpa"]), step=5.0)
                    new_elong = st.number_input("Elongation [%]", value=float(selected_row["elongation_pct"]), step=0.5)
                    new_weight = st.number_input("Coil Weight [kg]", value=float(selected_row.get("coil_weight_kg", 0.0)), step=50.0)
                    new_length = st.number_input("Coil Length [mm]", value=float(selected_row.get("coil_length_mm", 0.0)), step=100.0)
                    
                    val_padre = selected_row["lotto_number"] if pd.notna(selected_row["lotto_number"]) else ""
                    val_figlio = selected_row["lotto_figlio"] if pd.notna(selected_row["lotto_figlio"]) else ""
                    
                    new_lotto_padre = st.text_input("Lotto Padre", value=str(val_padre))
                    new_lotto_figlio = st.text_input("Lotto Figlio", value=str(val_figlio))
                    new_provider = st.text_input("Provider", value=str(selected_row["provider"]))
                    
                    if st.form_submit_button("💾 Save Changes", use_container_width=True):
                        update_cloud_material(
                            selected_prod_id, # Pass the ID positionally first, or match the exact parameter name like mat_id=selected_prod_id
                            grade=new_grade, 
                            thickness=new_thickness, 
                            yield_mpa=new_yield, 
                            uts_mpa=new_uts, 
                            elongation_pct=new_elong, 
                            lotto_number=new_lotto_padre, 
                            lotto_figlio=new_lotto_figlio, 
                            provider=new_provider,
                            coil_weight_kg=new_weight, 
                            coil_length_mm=new_length
                        )
                        st.success("Batch updated successfully!")
                        st.rerun()

            with st.expander("✂️ Split Coil (Lotto Padre -> Figli)", expanded=False):
                current_w = float(selected_row.get("rd_remaining_weight_kg", selected_row["coil_weight_kg"]))
                current_l = float(selected_row.get("rd_remaining_length_mm", selected_row["coil_length_mm"]))
                
                base_lotto = selected_row['lotto_number'] if pd.notna(selected_row['lotto_number']) else selected_row['lotto_figlio']
                
                st.write(f"Available to split: **{current_w:.1f} kg** / **{current_l:.1f} mm**")
                
                num_children = st.number_input("Number of Children (Figli)", min_value=2, max_value=10, value=2, step=1)
                
                with st.form(key=f"split_form_{selected_prod_id}"):
                    children_inputs = []
                    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    
                    for i in range(int(num_children)):
                        st.markdown(f"**Child {i+1} (Figlio {letters[i]})**")
                        cc1, cc2, cc3 = st.columns(3)
                        with cc1:
                            c_lotto = st.text_input(f"Lot Name {i+1}", value=f"{base_lotto}-{letters[i]}", key=f"split_lotto_{selected_prod_id}_{i}")
                        with cc2:
                            c_weight = st.number_input(f"Weight {i+1} [kg]", min_value=0.0, max_value=current_w, value=current_w / num_children, key=f"split_w_{selected_prod_id}_{i}")
                        with cc3:
                            c_length = st.number_input(f"Length {i+1} [mm]", min_value=0.0, max_value=current_l, value=current_l / num_children, key=f"split_l_{selected_prod_id}_{i}")
                        
                        children_inputs.append({"lotto": c_lotto, "weight": c_weight, "length": c_length})
                        st.write("---")
                        
                    if st.form_submit_button("Confirm Split & Close Parent", use_container_width=True):
                        total_w = sum(c["weight"] for c in children_inputs)
                        total_l = sum(c["length"] for c in children_inputs)
                        
                        if total_w > current_w or total_l > current_l:
                            st.error("Sum of child weights or lengths exceeds the parent coil's remaining stock!")
                        else:
                            split_cloud_material(selected_prod_id, children_inputs)
                            st.success(f"Coil successfully split into {num_children} children! Parent marked unavailable.")
                            st.rerun()
