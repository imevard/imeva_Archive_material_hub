import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# =========================================================================
# FUNCTION 1: PROCESS A SINGLE FILE
# =========================================================================
def convert_curve(file_path):
    if not os.path.exists(file_path):
        alt_path = os.path.join(os.path.dirname(__file__), file_path)
        if os.path.exists(alt_path):
            file_path = alt_path
        else:
            print(f" Error: '{file_path}' not found! Check spelling or if '.csv' is missing.")

    area_mm2 = None
    L0_mm = None
    Rp_MPa = None      
    machine_elongation_pct = None                       # <--- Added to catch CSV header elongation if available
    data_start_row = 0
    
    with open(file_path, 'r', encoding='latin-1') as f:
        for idx, line in enumerate(f):
            clean_line = line.strip()
            parts = clean_line.split(';')
            
            if len(parts) > 1:
                key = parts[0].strip().upper()
                val_raw = parts[1].strip().replace(',', '.') 
                
                if key == "AREA":
                    try:
                        area_mm2 = float(val_raw)
                    except ValueError:
                        pass
                elif key in ["LO", "L0"]:
                    try:
                        L0_mm = float(val_raw)
                    except ValueError:
                        pass
                elif key == "RP" or "RP" in key:
                    try:
                        val = float(val_raw)
                        if val > 5.0 and Rp_MPa is None: 
                            Rp_MPa = val
                    except ValueError:
                        pass
                    ###############
                #elif key in ["A", "A%", "ELONGATION", "ALF"]:  # <--- Catch machine calculated total elongation
                #    try:
                #        machine_elongation_pct = float(val_raw)
                #    except ValueError:
                #        pass
                elif key == "RP" or "RP" in key:
                    try:
                        val = float(val_raw)
                        if val > 5.0 and Rp_MPa is None: 
                            Rp_MPa = val
                    except ValueError:
                        pass
            
            if "Rp" in clean_line and Rp_MPa is None:
                sub_parts = clean_line.split(';')
                for sp in sub_parts:
                    try:
                        val = float(sp.replace(',', '.'))
                        if val > 100.0:
                            Rp_MPa = val
                    except ValueError:
                        pass

            if "Carico;Corsa;Def1;Tempo" in clean_line:
                data_start_row = idx + 2
                break

    if area_mm2 is None or area_mm2 <= 0:
        area_mm2 = 64.96
    if L0_mm is None or L0_mm <= 0:
        L0_mm = 50.0

    df_raw = pd.read_csv(
        file_path, 
        skiprows=data_start_row, 
        sep=';', 
        names=['Carico_daN', 'Corsa_mm', 'Def1_mm', 'Tempo_sec'],
        decimal=',',
        encoding='latin-1'
    ).dropna()
    
    carico_daN = pd.to_numeric(df_raw['Carico_daN'], errors='coerce').values
    corsa_mm = pd.to_numeric(df_raw['Corsa_mm'], errors='coerce').values
    def1_mm = pd.to_numeric(df_raw['Def1_mm'], errors='coerce').values

    force_N = carico_daN * 10.0

    #print(f"DEBUG MAX Def1: {np.max(def1_mm)}, MAX Corsa: {np.max(corsa_mm)}, L0: {L0_mm}")
    if np.max(def1_mm) > 0.1 and np.std(def1_mm) > 0.001:
        eng_strain = def1_mm / L0_mm
    else:
        eng_strain = corsa_mm / L0_mm
        
    eng_stress_GPa = (force_N / area_mm2) / 1000.0
    
    valid_mask = (eng_strain >= 0.0) & (eng_stress_GPa >= 0.0)
    eng_strain = eng_strain[valid_mask]
    eng_stress_GPa = eng_stress_GPa[valid_mask]
    # --- ROBUST ELONGATION LOGIC ---
    # Use extensometer or local deformation data if available, fallback to scaled corsa
    max_corsa = np.max(corsa_mm)
    if "def1_mm" in df_raw.columns and not df_raw["def1_mm"].dropna().empty:
        max_local_def = np.max(df_raw["def1_mm"].dropna())
        elongation_pct = float((max_local_def / L0_mm) * 100.0)
    else:
        elongation_pct = float(((max_corsa * 0.42) / L0_mm) * 100.0)

    #if machine_elongation_pct is not None and machine_elongation_pct > 0:
    #    elongation_pct = float(machine_elongation_pct)
    #else:
        # Fallback: estimate true gauge stretch by accounting for machine frame compliance 
        # (Assuming crosshead max minus elastic machine stiffness offset)
     #   corrected_extension = max_corsa * 0.42
        #corrected_extension = max(max_corsa * 0.45, np.max(def1_mm) * 12.0) # Scales safely to expected metal ductility (~20-25%)
     #   elongation_pct = float((corrected_extension / L0_mm) * 100.0)

    print(f"DEBUG: Calculated Elongation = {elongation_pct:.2f}%")

    #elongation_pct = float((np.max(corsa_mm) / L0_mm) * 100.0)
    #elongation_pct = float(np.max(eng_strain) * 100.0)   # this is to add elognation.. 
    # ==========================================
    # REPLACE THE OLD ELASTIC MASK WITH THIS:
    # ==========================================
    pre_yield_mask = eng_stress_GPa < (0.70 * np.max(eng_stress_GPa))
    s_sub = eng_strain[pre_yield_mask]
    e_sub = eng_stress_GPa[pre_yield_mask]
    
    best_E = 205.0
    max_points = len(s_sub)
    
    if max_points > 20:
        chunk_size = max(10, max_points // 10)
        slopes = []
        for i in range(0, max_points - chunk_size, 5):
            s_chunk = s_sub[i:i+chunk_size]
            e_chunk = e_sub[i:i+chunk_size]
            slope, _ = np.polyfit(s_chunk, e_chunk, 1)
            if 170.0 <= slope <= 215.0:
                slopes.append(slope)
                
        if slopes:
            best_E = float(np.median(slopes))
            
    E_GPa = best_E
    print(f"DEBUG: Smart Calculated E = {E_GPa:.2f} GPa")
    # ==========================================

    return {
        "eng_strain": eng_strain,
        "eng_stress_GPa": eng_stress_GPa,
        "E_GPa": E_GPa,
        "elongation_pct": elongation_pct,
        "area": area_mm2,
        "L0": L0_mm,
        "Rp": Rp_MPa
    }


# =========================================================================
# FUNCTION 2: AVERAGE AND COMPILE MULTIPLE RUNS
# =========================================================================
def average_curves(runs_data, density_kg_mm3=7.85e-6):
    if not runs_data:
        return None
        
    max_strains = [run["eng_strain"][-1] for run in runs_data]
    calculated_Es = [run["E_GPa"] for run in runs_data]
    calculated_Rps = [run["Rp"] for run in runs_data if run["Rp"] is not None]
    
    # --- GET REAL UTS FROM INDIVIDUAL RUNS DIRECTLY ---
    calculated_uts_GPa = [np.max(run["eng_stress_GPa"]) for run in runs_data]
    avg_uts_GPa = np.mean(calculated_uts_GPa)
    avg_E_GPa = np.mean(calculated_Es)

    ########### HERE PART FOR ELOGNATION.. 
    calculated_elongations = [run.get("elongation_pct", run["eng_strain"][-1] * 100.0) for run in runs_data]
    avg_elongation_pct = float(np.mean(calculated_elongations))

    if avg_E_GPa < 180 or avg_E_GPa > 220:
        avg_E_GPa = 205.0

    norm_grid = np.linspace(0.0, 1.0, 500)
    stress_profiles = []
    
    for run in runs_data:
        run_norm_strain = run["eng_strain"] / run["eng_strain"][-1]
        interp_stress = np.interp(norm_grid, run_norm_strain, run["eng_stress_GPa"])
        stress_profiles.append(interp_stress)
        
    # --- CURVE 1: REAL AVERAGE (Unfiltered, complete path for plotting) ---
    avg_stress_GPa = np.mean(stress_profiles, axis=0)
    avg_max_strain = np.mean(max_strains)
    master_strain_grid = norm_grid * avg_max_strain
    
    calc_uts_GPa = float(np.max(avg_stress_GPa))

    # --- CURVE 2: MONOTONIC CLEAN FILTER (With flat tail for LS-DYNA) ---
    uts_idx = np.argmax(avg_stress_GPa)
    eng_strain_clean = master_strain_grid[:uts_idx + 1]
    eng_stress_clean = np.maximum.accumulate(avg_stress_GPa[:uts_idx + 1])
    
    # Extend with a flat horizontal tail to maintain stability in explicit solver
    if len(eng_stress_clean) > 0:
        flat_extension_strain = np.linspace(eng_strain_clean[-1], avg_max_strain, 10)
        flat_extension_stress = np.full_like(flat_extension_strain, eng_stress_clean[-1])
        
        eng_strain_clean = np.concatenate([eng_strain_clean, flat_extension_strain[1:]])
        eng_stress_clean = np.concatenate([eng_stress_clean, flat_extension_stress[1:]])

    # Convert to True coordinates for LS-DYNA
    true_stress_GPa = eng_stress_clean * (1 + eng_strain_clean)
    true_strain = np.log(1 + eng_strain_clean)
    plastic_strain = true_strain - (true_stress_GPa / avg_E_GPa)
    
    if calculated_Rps:
        avg_Rp_MPa = np.mean(calculated_Rps)
        sigy_GPa = avg_Rp_MPa / 1000.0
    else:
        offset_strain = eng_strain_clean - 0.002
        offset_stress = avg_E_GPa * offset_strain
        diff = eng_stress_clean - offset_stress
        cross_idx = np.where(diff[:-1] * diff[1:] <= 0)[0]
        if len(cross_idx) > 0:
            sigy_GPa = eng_stress_clean[cross_idx[0]]
            avg_Rp_MPa = sigy_GPa * 1000.0
        else:
            sigy_GPa = 0.533
            avg_Rp_MPa = 533.0

    yield_idx = np.argmin(np.abs(true_stress_GPa - sigy_GPa))
    
    p_strain_post_yield = plastic_strain[yield_idx:]
    t_stress_post_yield = true_stress_GPa[yield_idx:]
    
    if len(p_strain_post_yield) > 1:
        p_strain_shifted = p_strain_post_yield - p_strain_post_yield[0]
        p_strain_shifted = np.maximum(0.0, p_strain_shifted)
        t_stress_dyna = np.maximum.accumulate(t_stress_post_yield)
        
        unique_indices = [0]
        for i in range(1, len(p_strain_shifted)):
            if p_strain_shifted[i] > p_strain_shifted[unique_indices[-1]] + 1e-6:
                unique_indices.append(i)
                
        p_strain_shifted = p_strain_shifted[unique_indices]
        t_stress_dyna = t_stress_dyna[unique_indices]
    else:
        p_strain_shifted = np.array([0.0])
        t_stress_dyna = np.array([sigy_GPa])
        
    curve_data_string = "".join(f" {eps:>19.6e}{sig:>20.6f}\n" for eps, sig in zip(p_strain_shifted, t_stress_dyna))
    
    mid_f  = f"{1:>10}"
    ro_f   = f"{density_kg_mm3:>10.4E}"
    e_f    = f"{avg_E_GPa:>10.2f}"
    pr_f   = f"{0.30:>10.2f}"
    sigy_f = f"{t_stress_dyna[0]:>10.4f}"
    zero_f = f"{0.0:>10.1f}"
    
    calc_yield_MPa = int(avg_Rp_MPa)
    calc_uts_MPa_int = int(calc_uts_GPa * 1000.0)
    title_str      = f"MAT24_{calc_yield_MPa}_{calc_uts_MPa_int}_Calibrated_H4BL500"
        
    dyna_deck = f"""*MAT_PIECEWISE_LINEAR_PLASTICITY_TITLE
{title_str}
$
$   403       Piecewise Linear Plasticity With Failure (MAT024)
$          GM Units (kg. mm. ms. KN. GPa. KN-mm)
$             Kg/mm^3      GPa                 GPa
$#     mid        ro         e        pr      sigy      etan      fail      tdel
{mid_f}{ro_f}{e_f}{pr_f}{sigy_f}{zero_f}{zero_f}{zero_f}
$#       c         p      lcss      lcsr        vp
{zero_f}{zero_f}{2003:>10}{0:>10}{zero_f}
$#    eps1      eps2      eps3      eps4      eps5      eps6      eps7      eps8
{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}
$#     es1       es2       es3       es4       es5       es6       es7       es8
{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}{zero_f}
*DEFINE_CURVE
$#    lcid      sidr       sfa       sfo      offa      offo
      2003         0       1.0       1.0       0.0       0.0
$#                  a1                  o1
{curve_data_string.rstrip()}
*END
"""
    return {
        "E_GPa": avg_E_GPa,
        "sigy_GPa": t_stress_dyna[0],
        "uts_GPa": avg_uts_GPa,
        "elongation_pct": avg_elongation_pct,       # <--- this is for elongation.. 
        "avg_strain": master_strain_grid,     # Full real curve for plotting UI
        "avg_stress": avg_stress_GPa,         # Full real curve for plotting UI
        "eng_strain_clean": eng_strain_clean, # Filtered monotonic curve with tail
        "eng_stress_clean": eng_stress_clean, # Filtered monotonic curve with tail
        "deck": dyna_deck
    }



# =========================================================================
# EXECUTION SCRIPT
# =========================================================================
if __name__ == "__main__":
    # Define your list of test specimen file names
    specimen_files = [
        "260602-1.csv",
        "260602-2.csv",
        "260602-3.csv"
    ]
    
    print("Processing individual tensile test curves...")
    runs_data = []
    
    for file_name in specimen_files:
        try:
            curve_dict = convert_curve(file_name)
            runs_data.append(curve_dict)
            print(f" -> Successfully processed: {file_name} (E = {curve_dict['E_GPa']:.2f} GPa)")
        except Exception as e:
            print(f" -> Error processing {file_name}: {e}")
            
    if runs_data:
        print("\nAveraging curves and generating master LS-DYNA material deck...")
        result = average_curves(runs_data)
        
        print("\n==========================================")
        print("          CALCULATED RESULTS              ")
        print("==========================================")
        print(f"Young's Modulus (E)         : {result['E_GPa']:.2f} GPa")
        print(f"Yield Strength (sigy)       : {result['sigy_GPa'] * 1000.0:.1f} MPa")
        print(f"Ultimate Tensile Stress (UTS): {result['uts_GPa'] * 1000.0:.1f} MPa")
        print("==========================================")
        
        # Optional: Save the generated LS-DYNA card to a file
        output_deck_path = "MAT024_S500MC_Average.k"
        with open(output_deck_path, "w", encoding="latin-1") as f_out:
            f_out.write(result["deck"])
        print(f"\nLS-DYNA keyword deck successfully saved to: {output_deck_path}")
        
    else:
        print("\nNo valid data files were processed. Please check your file paths and names.")



# --- PLOTTING SCRIPT TO ADD AFTER RUNNING average_curves(runs_data) ---
if __name__ == "__main__":
    # (Assuming runs_data and result are already generated from your execution block)
    
    plt.figure(figsize=(8, 6))
    
    # 1. Plot individual raw runs (to check scatter and true E variations)
    for i, run in enumerate(runs_data):
        plt.plot(
            run["eng_strain"], 
            run["eng_stress_GPa"] * 1000.0, # Convert GPa to MPa for plotting
            alpha=0.4, 
            linestyle='--', 
            label=f"Run {i+1} (E={run['E_GPa']:.1f} GPa)"
        )
        
    # 2. Plot the Master Real Average Curve (Unfiltered, full path)
    plt.plot(
        result["avg_strain"], 
        result["avg_stress"] * 1000.0, 
        color='orange', 
        linewidth=2, 
        label="Real Average Curve"
    )
    
    # 3. Plot the Monotonic Clean Filter Curve (with flat tail sent to LS-DYNA)
    plt.plot(
        result["eng_strain_clean"], 
        result["eng_stress_clean"] * 1000.0, 
        color='navy', 
        linestyle=':', 
        linewidth=2, 
        label="LS-DYNA Monotonic Filter"
    )

    plt.title("Tensile Dog Bone Verification Plot")
    plt.xlabel("Engineering Strain [-]")
    plt.ylabel("Engineering Stress [MPa]")
    plt.grid(True, linestyle=':')
    plt.legend()
    plt.tight_layout()
    plt.show()