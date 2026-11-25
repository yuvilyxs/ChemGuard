import json
from datetime import datetime

import streamlit as st

from core.pubchem_service import PubChemError, lookup_compounds
from core.report_service import create_safety_report
from core.research_library import add_recent_compound, add_search_entry
from core.safety_service import get_compound_safety_profile


st.set_page_config(
    page_title="ChemGuard | Intelligent Chemical Safety Assistant",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="collapsed",
)


FEATURES = [
    (
        "01",
        "Chemical Library",
        "Find the essentials fast: chemical identities, hazard classifications, and handling guidance.",
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6m-5 0v6.2L5.5 18a2 2 0 0 0 1.7 3h9.6a2 2 0 0 0 1.7-3L14 9.2V3M8 15h8"/></svg>',
    ),
    (
        "02",
        "Hazard Assistant",
        "Get a clearer view of chemical hazards and the precautions that matter during everyday work.",
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3h.01"/></svg>',
    ),
    (
        "03",
        "Safety Reports",
        "Bring key information together in practical reports for safer, more informed decisions.",
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm7 0v5h5M9 13h6m-6 4h6"/></svg>',
    ),
    (
        "04",
        "Research Library",
        "Keep favorite compounds, recent searches, and saved safety reports together.",
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H6.5A2.5 2.5 0 0 1 4 17.5v-12ZM4 17.5A2.5 2.5 0 0 1 6.5 15H20M8 7h8M8 10h8"/></svg>',
    ),
]


def _initialize_research_library():
    st.session_state.setdefault("research_favorites", {})
    st.session_state.setdefault("research_recent_compounds", [])
    st.session_state.setdefault("research_search_history", [])
    st.session_state.setdefault("research_saved_reports", {})
    st.session_state.setdefault("research_last_recent_selection", None)


def _remember_current_compound(compound, query):
    history = st.session_state["research_search_history"]
    search_token = next(
        (
            entry["searched_at"]
            for entry in history
            if entry["query"] == query
        ),
        "",
    )
    marker = (compound["cid"], query, search_token)
    if marker != st.session_state["research_last_recent_selection"]:
        st.session_state["research_recent_compounds"] = add_recent_compound(
            st.session_state["research_recent_compounds"], compound, query
        )
        st.session_state["research_last_recent_selection"] = marker


def _open_research_compound(compound, query):
    st.session_state["selected_compound"] = compound
    st.session_state["selected_compound_query"] = query
    st.session_state["pubchem_compound_query"] = query
    st.session_state["pubchem_compound_results"] = [compound]
    st.session_state["pubchem_compound_error"] = None
    st.session_state.pop("pubchem_selected_compound", None)
    st.session_state["research_last_recent_selection"] = None
    st.session_state["chemguard_page"] = "dashboard"
    st.rerun()


def _format_library_timestamp(value):
    timestamp = datetime.fromisoformat(value)
    return timestamp.astimezone().strftime("%b %d, %Y · %I:%M %p")


def inject_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');

        :root {
            --navy: #10253f;
            --navy-deep: #0b1c31;
            --teal: #18a999;
            --teal-bright: #43d5bb;
            --ink: #172a40;
            --muted: #64758a;
            --line: #e4ebf0;
            --surface: #ffffff;
            --page: #f7fafb;
        }
        html, body, [class*="css"] {
            font-family: 'DM Sans', sans-serif;
        }
        .stApp {
            background: var(--page);
            color: var(--ink);
        }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stToolbar"] { right: 1rem; }
        .block-container {
            max-width: 1180px;
            padding: 1.5rem 2rem 0;
        }
        .brandbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 4px 0 25px;
        }
        .brand {
            display: inline-flex;
            align-items: center;
            gap: 11px;
            color: var(--navy);
            font: 800 20px 'Manrope', sans-serif;
            letter-spacing: -0.7px;
        }
        .brand-mark {
            width: 38px;
            height: 38px;
            display: grid;
            place-items: center;
            border-radius: 12px;
            color: white;
            background: linear-gradient(145deg, #1bc3a8, #138a9b);
            box-shadow: 0 7px 18px rgba(24, 169, 153, .22);
        }
        .brand-mark svg { width: 23px; height: 23px; }
        .brand-note {
            color: #748396;
            font-size: 12px;
            font-weight: 600;
            letter-spacing: .3px;
        }
        .hero {
            position: relative;
            overflow: hidden;
            display: grid;
            grid-template-columns: 1.1fr .9fr;
            min-height: 390px;
            padding: 54px 58px;
            border-radius: 24px;
            color: #fff;
            background:
                radial-gradient(ellipse at 83% 18%, rgba(35, 197, 174, .18), transparent 34%),
                linear-gradient(115deg, #10243d 2%, #102c48 64%, #123a50 100%);
            box-shadow: 0 18px 48px rgba(16, 37, 63, .13);
            animation: rise-in .65s ease-out both;
        }
        .hero:after {
            content: '';
            position: absolute;
            width: 390px;
            height: 390px;
            right: -155px;
            top: -225px;
            border: 1px solid rgba(255,255,255,.09);
            border-radius: 50%;
            box-shadow: 0 0 0 35px rgba(255,255,255,.025), 0 0 0 72px rgba(255,255,255,.02);
            pointer-events: none;
        }
        .hero-copy { position: relative; z-index: 1; align-self: center; }
        .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 20px;
            color: #72e0cb;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1.55px;
            text-transform: uppercase;
        }
        .eyebrow:before {
            content: '';
            width: 20px;
            height: 1px;
            background: #43d5bb;
        }
        .hero h1 {
            max-width: 570px;
            margin: 0;
            color: #fff;
            font: 800 clamp(36px, 4.3vw, 55px)/1.12 'Manrope', sans-serif;
            letter-spacing: -2.4px;
        }
        .hero h1 span { color: #54d7c1; }
        .hero p {
            max-width: 475px;
            margin: 18px 0 25px;
            color: #c3cfda;
            font-size: 15px;
            line-height: 1.75;
        }
        .hero-visual {
            position: relative;
            z-index: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 255px;
        }
        .orbit {
            position: relative;
            display: grid;
            place-items: center;
            width: 226px;
            height: 226px;
            border: 1px solid rgba(121, 232, 210, .23);
            border-radius: 50%;
            background: radial-gradient(circle, rgba(38, 189, 168, .17), rgba(38, 189, 168, .025) 65%, transparent 66%);
            box-shadow: 0 0 0 23px rgba(67, 213, 187, .035), 0 0 0 48px rgba(67, 213, 187, .025);
            animation: float 5s ease-in-out infinite;
        }
        .orbit:before, .orbit:after {
            content: '';
            position: absolute;
            width: 11px;
            height: 11px;
            border-radius: 50%;
            background: #59ddc4;
            box-shadow: 0 0 18px rgba(89, 221, 196, .7);
        }
        .orbit:before { top: 26px; right: 41px; }
        .orbit:after { bottom: 34px; left: 17px; width: 7px; height: 7px; }
        .molecule {
            display: grid;
            place-items: center;
            width: 106px;
            height: 106px;
            border: 1px solid rgba(124, 234, 212, .45);
            border-radius: 30px;
            color: #7cead4;
            background: linear-gradient(145deg, rgba(24, 169, 153, .3), rgba(21, 56, 76, .9));
            box-shadow: 0 18px 42px rgba(0,0,0,.2), inset 0 1px rgba(255,255,255,.1);
            transform: rotate(-8deg);
        }
        .molecule svg { width: 56px; height: 56px; transform: rotate(8deg); }
        .visual-tag {
            position: absolute;
            right: 1px;
            bottom: 18px;
            padding: 10px 14px;
            border: 1px solid rgba(255,255,255,.12);
            border-radius: 12px;
            color: #e2f5f1;
            background: rgba(15, 42, 62, .83);
            box-shadow: 0 10px 25px rgba(0,0,0,.14);
            font-size: 11px;
            font-weight: 600;
            letter-spacing: .2px;
        }
        .visual-tag span { margin-right: 7px; color: #59ddc4; }
        div.st-key-enter_dashboard { margin-top: -1px; }
        div.st-key-enter_dashboard button {
            min-height: 46px;
            padding: 0 21px;
            border: 0;
            border-radius: 10px;
            color: #09263b;
            background: #53ddc2;
            font-weight: 700;
            box-shadow: 0 8px 20px rgba(36, 201, 173, .2);
            transition: transform .2s ease, box-shadow .2s ease, background .2s ease;
        }
        div.st-key-enter_dashboard button:hover {
            color: #09263b;
            background: #70e7d0;
            transform: translateY(-2px);
            box-shadow: 0 12px 25px rgba(36, 201, 173, .28);
        }
        .section-heading {
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 20px;
            margin: 56px 0 22px;
        }
        .section-kicker {
            margin-bottom: 7px;
            color: #138f83;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1.5px;
            text-transform: uppercase;
        }
        .section-heading h2 {
            margin: 0;
            color: var(--navy);
            font: 750 27px 'Manrope', sans-serif;
            letter-spacing: -.8px;
        }
        .section-heading p {
            max-width: 390px;
            margin: 0 0 2px;
            color: var(--muted);
            font-size: 13px;
            line-height: 1.65;
        }
        .feature-card {
            min-height: 191px;
            padding: 22px 22px 20px;
            border: 1px solid var(--line);
            border-radius: 15px;
            background: var(--surface);
            box-shadow: 0 5px 16px rgba(16, 37, 63, .025);
            transition: transform .22s ease, box-shadow .22s ease, border-color .22s ease;
            animation: rise-in .6s ease-out both;
        }
        .feature-card:hover {
            transform: translateY(-4px);
            border-color: #b7e5dd;
            box-shadow: 0 14px 28px rgba(16, 67, 78, .09);
        }
        .feature-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
        }
        .feature-icon {
            display: grid;
            place-items: center;
            width: 42px;
            height: 42px;
            border-radius: 12px;
            color: #108e84;
            background: #e9f8f5;
        }
        .feature-icon svg {
            width: 22px;
            height: 22px;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.65;
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        .feature-number { color: #a5b2bf; font: 600 11px 'Manrope', sans-serif; }
        .feature-card h3 {
            margin: 0 0 7px;
            color: var(--navy);
            font: 700 15px 'Manrope', sans-serif;
            letter-spacing: -.2px;
        }
        .feature-card p {
            margin: 0;
            color: #718095;
            font-size: 12px;
            line-height: 1.65;
        }
        .about-panel {
            display: grid;
            grid-template-columns: .8fr 1.2fr;
            gap: 48px;
            align-items: center;
            margin: 54px 0 36px;
            padding: 31px 36px;
            border: 1px solid #e4eeef;
            border-radius: 17px;
            background: linear-gradient(110deg, #eef8f7, #f8fbfc 75%);
        }
        .about-panel h2 {
            margin: 0;
            color: var(--navy);
            font: 750 24px 'Manrope', sans-serif;
            letter-spacing: -.7px;
        }
        .about-panel p {
            margin: 0;
            color: #607387;
            font-size: 13px;
            line-height: 1.8;
        }
        .footer {
            display: flex;
            justify-content: space-between;
            gap: 18px;
            padding: 19px 0 25px;
            border-top: 1px solid #e5ebef;
            color: #8290a0;
            font-size: 11px;
        }
        .footer strong { color: #53677d; font-weight: 600; }
        .dashboard-heading {
            margin: 12px 0 6px;
            color: var(--navy);
            font: 800 34px 'Manrope', sans-serif;
            letter-spacing: -1.2px;
        }
        .dashboard-subtitle { margin: 0 0 28px; color: var(--muted); font-size: 14px; }
        .dashboard-banner {
            padding: 25px 28px;
            border-radius: 16px;
            color: #fff;
            background: linear-gradient(110deg, #10253f, #15445a);
        }
        .dashboard-banner h2 {
            margin: 0 0 6px;
            color: white;
            font: 700 20px 'Manrope', sans-serif;
        }
        .dashboard-banner p { margin: 0; color: #c2d5df; font-size: 13px; }
        .dashboard-note {
            padding: 16px 18px;
            border: 1px solid #dce8ed;
            border-radius: 12px;
            color: #607387;
            background: white;
            font-size: 13px;
            line-height: 1.65;
        }
        .st-key-back_home button {
            border: 1px solid #d7e3e8;
            border-radius: 9px;
            color: var(--navy);
            background: white;
            font-weight: 600;
        }
        .st-key-back_home button:hover { border-color: #18a999; color: #11877e; }
        @keyframes rise-in {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes float {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-8px); }
        }
        @media (max-width: 760px) {
            .block-container { padding: 1rem 1rem 0; }
            .brandbar { padding-bottom: 17px; }
            .brand-note { display: none; }
            .hero { grid-template-columns: 1fr; min-height: auto; padding: 37px 27px 29px; }
            .hero h1 { font-size: clamp(35px, 9vw, 48px); letter-spacing: -1.8px; }
            .hero-visual { min-height: 205px; margin-top: 12px; }
            .orbit { width: 170px; height: 170px; }
            .molecule { width: 82px; height: 82px; border-radius: 24px; }
            .molecule svg { width: 45px; height: 45px; }
            .visual-tag { right: 2%; bottom: 5px; }
            .section-heading { align-items: start; flex-direction: column; margin-top: 42px; }
            .about-panel { grid-template-columns: 1fr; gap: 12px; margin-top: 35px; padding: 24px; }
            .footer { flex-direction: column; gap: 6px; }
        }
        @media (prefers-reduced-motion: reduce) {
            *, *:before, *:after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_brand():
    st.markdown(
        """
        <div class="brandbar">
          <div class="brand">
            <span class="brand-mark">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M9 3h6m-5 0v6.2L5.5 18a2 2 0 0 0 1.7 3h9.6a2 2 0 0 0 1.7-3L14 9.2V3M8 15h8"/>
              </svg>
            </span>
            ChemGuard
          </div>
          <div class="brand-note">INTELLIGENT CHEMICAL SAFETY ASSISTANT</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_landing_page():
    render_brand()
    st.markdown(
        """
        <section class="hero">
          <div class="hero-copy">
            <div class="eyebrow">A smarter standard of safety</div>
            <h1>Making Chemical<br>Safety <span>Smarter.</span></h1>
            <p>
              Confident chemical decisions start with clear information.
              ChemGuard brings essential safety insights into one intuitive workspace.
            </p>
          </div>
          <div class="hero-visual" aria-hidden="true">
            <div class="orbit">
              <div class="molecule">
                <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="32" cy="31" r="7"/>
                  <circle cx="15" cy="19" r="5"/>
                  <circle cx="49" cy="17" r="5"/>
                  <circle cx="19" cy="47" r="5"/>
                  <circle cx="48" cy="46" r="5"/>
                  <path d="m21 23 6 5m10-1 7-7m-17 15-5 8m16-9 7 8"/>
                  <path d="M32 6v7m-3-3.5h6"/>
                </svg>
              </div>
            </div>
            <div class="visual-tag"><span>●</span> Insight-led safety</div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Get Started  →", key="enter_dashboard"):
        st.session_state["chemguard_page"] = "dashboard"
        st.rerun()

    st.markdown(
        """
        <div class="section-heading">
          <div>
            <div class="section-kicker">Everything in one place</div>
            <h2>Safety tools, thoughtfully connected.</h2>
          </div>
          <p>From looking up a chemical to reviewing its hazards, find the support you need to move forward with clarity.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    columns = st.columns(4, gap="medium")
    for column, (number, title, description, icon) in zip(columns, FEATURES):
        with column:
            st.markdown(
                f"""
                <article class="feature-card">
                  <div class="feature-top">
                    <div class="feature-icon">{icon}</div>
                    <span class="feature-number">{number}</span>
                  </div>
                  <h3>{title}</h3>
                  <p>{description}</p>
                </article>
                """,
                unsafe_allow_html=True,
            )

    st.markdown(
        """
        <section class="about-panel">
          <h2>About ChemGuard</h2>
          <p>
            Chemical safety information can be complex. ChemGuard is designed to make it easier
            to find, understand, and use that information in everyday workflows. Explore a
            connected set of tools built to support more thoughtful handling and safety
            planning.
          </p>
        </section>
        <footer class="footer">
          <span><strong>ChemGuard</strong> &nbsp;·&nbsp; Making Chemical Safety Smarter.</span>
          <span>Project interface &nbsp;·&nbsp; Built with Streamlit</span>
        </footer>
        """,
        unsafe_allow_html=True,
    )


def render_compound_explorer():
    st.markdown(
        """
        <div class="section-kicker">PUBCHEM COMPOUND LOOKUP</div>
        <h2 class="dashboard-heading" style="font-size:26px">Explore a compound</h2>
        <p class="dashboard-subtitle">Get verified identifiers and properties directly from PubChem.</p>
        """,
        unsafe_allow_html=True,
    )
    with st.form("pubchem_compound_search"):
        query = st.text_input(
            "Chemical name or identifier",
            placeholder="Try aspirin, ethanol, 64-17-5, C9H8O4, or CID 2244",
            key="pubchem_compound_query",
            help="Search by name, CAS number, molecular formula, or PubChem CID.",
        )
        submitted = st.form_submit_button("Find compound", type="primary")

    if submitted:
        st.session_state["pubchem_compound_results"] = []
        st.session_state["pubchem_compound_error"] = None
        st.session_state.pop("pubchem_selected_compound", None)
        st.session_state.pop("selected_compound", None)
        st.session_state.pop("selected_compound_query", None)
        search_query = query.strip()
        if not search_query:
            st.session_state["pubchem_compound_error"] = "Enter a chemical name or identifier to search."
        else:
            search_error = None
            try:
                with st.spinner("Looking up compound data on PubChem..."):
                    st.session_state["pubchem_compound_results"] = lookup_compounds(search_query)
            except (PubChemError, ValueError) as exc:
                search_error = str(exc)
                st.session_state["pubchem_compound_error"] = search_error
            st.session_state["research_search_history"] = add_search_entry(
                st.session_state["research_search_history"],
                search_query,
                st.session_state["pubchem_compound_results"],
                search_error,
            )

    error = st.session_state.get("pubchem_compound_error")
    if error:
        st.error(error)

    compounds = st.session_state.get("pubchem_compound_results", [])
    if not compounds:
        if st.session_state.get("pubchem_compound_error") is None and st.session_state.get(
            "pubchem_compound_query"
        ):
            st.info(
                "No matches found. Check the spelling, or try a CAS number, "
                "molecular formula, or PubChem CID."
            )
        return

    if len(compounds) > 1:
        st.caption(f"PubChem returned {len(compounds)} matches. Select the compound you mean.")
        selected_index = st.selectbox(
            "Matching compounds",
            range(len(compounds)),
            format_func=lambda index: (
                f"CID {compounds[index]['cid']} | "
                f"{compounds[index].get('iupac_name') or 'Compound'} | "
                f"{compounds[index].get('molecular_formula') or 'Formula unavailable'}"
            ),
            key="pubchem_selected_compound",
        )
    else:
        selected_index = 0

    compound = compounds[selected_index]
    st.session_state["selected_compound"] = compound
    st.session_state["selected_compound_query"] = st.session_state.get(
        "pubchem_compound_query", query
    )
    selected_query = st.session_state["selected_compound_query"].strip()
    _remember_current_compound(compound, selected_query)
    st.caption(f"Result {selected_index + 1} of {len(compounds)}")
    st.subheader(compound.get("iupac_name") or "PubChem compound")
    metric_columns = st.columns(3)
    metric_columns[0].metric("PubChem CID", compound["cid"])
    metric_columns[1].metric("Molecular formula", compound.get("molecular_formula") or "Unavailable")
    metric_columns[2].metric(
        "Molecular weight (g/mol)",
        compound.get("molecular_weight") or "Unavailable",
    )
    cid_key = str(compound["cid"])
    favorite_compounds = st.session_state["research_favorites"]
    is_favorite = cid_key in favorite_compounds
    favorite_label = "Remove from favorites" if is_favorite else "Add to favorites"
    if st.button(favorite_label, key=f"favorite_compound_{cid_key}"):
        if is_favorite:
            favorite_compounds.pop(cid_key)
        else:
            favorite_compounds[cid_key] = compound
        st.rerun()

    overview_tab, physical_tab, identifiers_tab, synonyms_tab, sources_tab = st.tabs(
        ["Overview", "Physical & chemical", "Identifiers", "Synonyms", "Sources"]
    )
    with overview_tab:
        rows = _compound_property_rows(compound)
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.info("PubChem did not provide property values for this compound.")
    with physical_tab:
        rows = _physical_property_rows(compound)
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.info("No additional physical or chemical properties are available.")
    with identifiers_tab:
        for label, key in (
            ("SMILES", "smiles"),
            ("Connectivity SMILES", "connectivity_smiles"),
            ("InChI", "inchi"),
            ("InChIKey", "inchi_key"),
        ):
            value = compound.get(key)
            if value:
                st.markdown(f"**{label}**")
                st.code(value, language=None)
            else:
                st.caption(f"{label}: unavailable")
    with synonyms_tab:
        synonyms = compound.get("synonyms", [])
        if synonyms:
            st.dataframe(
                [{"Synonym": synonym} for synonym in synonyms],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("No synonyms are available for this compound.")
    with sources_tab:
        source_labels = {
            "pubchem_compound": "PubChem compound record",
            "search": "PubChem search",
            "pug_rest_properties": "PUG REST properties",
            "pug_rest_synonyms": "PUG REST synonyms",
            "pug_view_physical_chemical_properties": "PubChem physical and chemical properties",
        }
        for key, url in compound.get("source_urls", {}).items():
            label = source_labels.get(key, key.replace("_", " ").title())
            st.markdown(f"- [{label}]({url})")

    with st.expander("View complete PubChem response", expanded=False):
        st.json(compound)

    action_columns = st.columns(2)
    actions = (
        ("Hazard Analysis", "hazard_assistant"),
        ("Generate Report", "safety_reports"),
    )
    for column, (label, destination) in zip(action_columns, actions):
        with column:
            if st.button(label, key=f"open_{destination}", type="primary"):
                st.session_state["chemguard_page"] = destination
                st.rerun()


_COMPOUND_PROPERTY_LABELS = {
    "MolecularFormula": "Molecular formula",
    "MolecularWeight": "Molecular weight (g/mol)",
    "IUPACName": "IUPAC name",
    "SMILES": "SMILES",
    "ConnectivitySMILES": "Connectivity SMILES",
    "InChI": "InChI",
    "InChIKey": "InChIKey",
    "XLogP": "XLogP",
    "ExactMass": "Exact mass",
    "MonoisotopicMass": "Monoisotopic mass",
    "TPSA": "Topological polar surface area (TPSA)",
    "Complexity": "Complexity",
    "Charge": "Charge",
    "HBondDonorCount": "Hydrogen-bond donor count",
    "HBondAcceptorCount": "Hydrogen-bond acceptor count",
    "RotatableBondCount": "Rotatable bond count",
    "HeavyAtomCount": "Heavy atom count",
    "IsotopeAtomCount": "Isotope atom count",
    "AtomStereoCount": "Atom stereocenter count",
    "DefinedAtomStereoCount": "Defined atom stereocenter count",
    "UndefinedAtomStereoCount": "Undefined atom stereocenter count",
    "BondStereoCount": "Bond stereocenter count",
    "DefinedBondStereoCount": "Defined bond stereocenter count",
    "UndefinedBondStereoCount": "Undefined bond stereocenter count",
    "CovalentUnitCount": "Covalent unit count",
}


def _compound_property_rows(compound):
    properties = compound.get("properties", {})
    return [
        {
            "Property": _COMPOUND_PROPERTY_LABELS.get(name, name),
            "Value": str(value),
        }
        for name, value in properties.items()
        if value is not None
    ]


def _format_pubchem_value(value):
    if not isinstance(value, dict):
        return str(value)

    rendered_values = []
    for key in ("StringWithMarkup", "Number", "Integer", "String", "Boolean"):
        entries = value.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                entry = entry.get("String", entry.get("Number", entry))
            rendered_values.append(str(entry))

    if not rendered_values:
        return json.dumps(value, ensure_ascii=False)

    rendered = ", ".join(rendered_values)
    unit = value.get("Unit")
    return f"{rendered} {unit}" if unit else rendered


def _physical_property_rows(compound):
    physical_properties = compound.get("physical_chemical_properties", {})
    rows = []
    for property_name, entries in physical_properties.items():
        for entry in entries:
            value = entry.get("value")
            row = {
                "Property": property_name,
                "Value": _format_pubchem_value(value) if value is not None else "",
            }
            if entry.get("name"):
                row["Name"] = entry["name"]
            if entry.get("reference"):
                references = entry["reference"]
                row["Reference"] = (
                    "; ".join(references)
                    if isinstance(references, list)
                    else str(references)
                )
            rows.append(row)
    return rows


def _compound_title(compound):
    return compound.get("iupac_name") or compound.get("molecular_formula") or f"PubChem CID {compound.get('cid')}"


def _render_module_navigation():
    if st.button("← Back to Compound Explorer", key="back_to_explorer"):
        st.session_state["chemguard_page"] = "dashboard"
        st.rerun()


def _render_selected_compound(compound):
    st.markdown(f"### {_compound_title(compound)}")
    st.caption(
        f"PubChem CID {compound.get('cid')} · "
        f"{compound.get('molecular_formula') or 'Molecular formula unavailable'}"
    )


def _render_source_links(profile, key="source_references"):
    references = profile.get(key, [])
    rendered = set()
    for reference in references:
        if not isinstance(reference, dict):
            continue
        url = reference.get("URL") or reference.get("url") or reference.get("source_url")
        name = (
            reference.get("SourceName")
            or reference.get("supplier")
            or reference.get("source_name")
            or "Source reference"
        )
        if isinstance(url, str) and url not in rendered:
            rendered.add(url)
            st.markdown(f"- [{name}]({url})")


def render_hazard_assistant():
    render_brand()
    _render_module_navigation()
    st.title("Hazard Assistant")
    compound = st.session_state.get("selected_compound")
    if not isinstance(compound, dict):
        st.info("Search for and select a compound in the Compound Explorer first.")
        return
    _render_selected_compound(compound)

    try:
        profile = get_compound_safety_profile(compound["cid"])
    except PubChemError as exc:
        st.error(f"Could not load PubChem safety annotations: {exc}")
        return
    if not profile.get("available"):
        st.warning(
            profile.get("message")
            or "PubChem has no safety annotations for this compound. Review the exact product SDS."
        )
        _render_source_links(profile)
        return

    st.caption(
        "GHS entries remain grouped by their PubChem-reported source. "
        "They may differ by product, concentration, or jurisdiction."
    )
    if profile.get("ghs_reference_error"):
        st.warning(
            "The PubChem GHS class/category reference could not be loaded: "
            f"{profile['ghs_reference_error']}"
        )
    if profile.get("precautionary_statement_error"):
        st.warning(
            "The official PubChem precautionary-statement reference could not be loaded: "
            f"{profile['precautionary_statement_error']}"
        )

    groups = profile.get("ghs_classifications", [])
    if not groups:
        st.info("No GHS classification groups were present in the retrieved annotation.")
    for index, group in enumerate(groups, start=1):
        source_name = group.get("source_name") or "Source not stated in PubChem"
        with st.expander(f"GHS source: {source_name}", expanded=index == 1):
            if group.get("source_url"):
                st.markdown(f"[Open source annotation]({group['source_url']})")
            if group.get("notes"):
                for note in group["notes"]:
                    st.caption(f"Source note: {note}")
            if group.get("other_classification_data"):
                st.markdown("**Additional source classification data**")
                st.dataframe(
                    [
                        {
                            "Item": item.get("name"),
                            "Value": "; ".join(item.get("values", [])),
                        }
                        for item in group["other_classification_data"]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

            first_column, second_column = st.columns(2)
            with first_column:
                st.markdown("**Signal word**")
                if group.get("signal_words"):
                    st.write(", ".join(group["signal_words"]))
                else:
                    st.write("Not stated in this source.")
            with second_column:
                st.markdown("**GHS pictograms**")
                pictograms = group.get("pictograms", [])
                if pictograms:
                    pictogram_columns = st.columns(min(len(pictograms), 4))
                    for pictogram_column, pictogram in zip(pictogram_columns, pictograms):
                        with pictogram_column:
                            if pictogram.get("url"):
                                st.image(pictogram["url"], width=64)
                            st.caption(pictogram.get("name") or pictogram.get("code") or "GHS pictogram")
                else:
                    st.write("Not listed in this source.")

            statements = group.get("hazard_statements", [])
            if statements:
                st.markdown("**Hazard classifications and H-statements**")
                classification_rows = []
                for statement in statements:
                    classifications = statement.get("classifications", [])
                    category = "; ".join(
                        " — ".join(
                            part
                            for part in (entry.get("hazard_class"), entry.get("category"))
                            if part
                        )
                        for entry in classifications
                        if entry.get("hazard_class") or entry.get("category")
                    )
                    classification_rows.append(
                        {
                            "Area": statement.get("area") or "Uncategorized",
                            "H-code": statement.get("code") or "Not coded",
                            "Classification / category": category or statement.get("category") or "Unavailable in PubChem GHS reference",
                            "H-statement": statement.get("statement"),
                        }
                    )
                st.dataframe(classification_rows, use_container_width=True, hide_index=True)
            else:
                st.info("No H-statements were listed in this source.")

            if group.get("precautionary_codes"):
                statement_lookup = {
                    item["code"]: item
                    for item in profile.get("precautionary_statements", [])
                }
                precautionary_rows = []
                for code in group["precautionary_codes"]:
                    item = statement_lookup.get(code, {})
                    precautionary_rows.append(
                        {
                            "P-code": code,
                            "P-statement": item.get("statement") or "Text unavailable; see the linked PubChem GHS reference.",
                        }
                    )
                st.markdown("**P-statements**")
                st.dataframe(precautionary_rows, use_container_width=True, hide_index=True)
                st.markdown(f"[PubChem GHS Rev. 11 reference]({profile['source_urls']['pubchem_ghs_reference']})")
            elif profile.get("precautionary_statements"):
                st.caption("P-statements are available from the PubChem GHS code reference, but this source group did not list P-codes.")

    for area in ("Physical", "Health", "Environmental", "Uncategorized"):
        statements = profile.get("hazard_groups", {}).get(area, [])
        if statements:
            with st.expander(f"{area} hazards · {len(statements)} statements", expanded=False):
                for statement in statements:
                    source = statement.get("source_name") or "PubChem annotation"
                    st.markdown(
                        f"- **{statement.get('code') or 'Hazard'}** — "
                        f"{statement.get('statement')} _(Source: {source})_"
                    )

    storage_rows = profile.get("storage_information", [])
    st.subheader("Available storage information")
    if storage_rows:
        st.dataframe(
            [
                {
                    "Topic": row.get("heading") or row.get("section_path"),
                    "Information": row.get("value_text"),
                    "Source": row.get("source_name") or "Not stated",
                    "Source URL": row.get("source_url") or "",
                }
                for row in storage_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No storage information was present in the retrieved PubChem annotation.")

    st.subheader("Supplier SDS references")
    sds_references = profile.get("supplier_sds_references", [])
    if sds_references:
        for reference in sds_references:
            st.markdown(
                f"- **{reference.get('supplier') or 'Supplier'}** — "
                f"{reference.get('citation')}"
            )
            if reference.get("url"):
                st.markdown(f"  [Open cited reference]({reference['url']})")
            else:
                st.caption("The PubChem citation does not provide a direct SDS URL.")
    else:
        st.info("No supplier SDS citation was found. Obtain the current SDS for the exact product.")
    st.subheader("Source references")
    _render_source_links(profile)


def render_research_library():
    render_brand()
    if st.button("← Back to Compound Explorer", key="back_from_research_library"):
        st.session_state["chemguard_page"] = "dashboard"
        st.rerun()

    st.markdown(
        """
        <div class="section-kicker" style="margin-top:24px">YOUR PERSONAL WORKSPACE</div>
        <h1 class="dashboard-heading">Research Library</h1>
        <p class="dashboard-subtitle">
          Keep useful compounds, searches, and safety reports close at hand.
        </p>
        """,
        unsafe_allow_html=True,
    )
    st.info("This library is saved for your current app session and is not shared with other users.")

    favorites = st.session_state["research_favorites"]
    recent_compounds = st.session_state["research_recent_compounds"]
    saved_reports = st.session_state["research_saved_reports"]
    search_history = st.session_state["research_search_history"]
    metric_columns = st.columns(4)
    metric_columns[0].metric("Favorite compounds", len(favorites))
    metric_columns[1].metric("Recent compounds", len(recent_compounds))
    metric_columns[2].metric("Saved reports", len(saved_reports))
    metric_columns[3].metric("Searches", len(search_history))

    favorites_tab, recent_tab, reports_tab, history_tab = st.tabs(
        ["Favorites", "Recent compounds", "Saved reports", "Search history"]
    )
    with favorites_tab:
        if not favorites:
            st.info("No favorites yet. Add a compound from the Compound Explorer.")
        for cid_key, compound in list(favorites.items()):
            title = _compound_title(compound)
            with st.expander(f"{title} · CID {compound['cid']}"):
                st.caption(compound.get("molecular_formula") or "Molecular formula unavailable")
                action_columns = st.columns(2)
                if action_columns[0].button(
                    "Open profile", key=f"favorite_open_{cid_key}", type="primary"
                ):
                    _open_research_compound(
                        compound,
                        compound.get("iupac_name") or f"CID {compound['cid']}",
                    )
                if action_columns[1].button(
                    "Remove favorite", key=f"favorite_remove_{cid_key}"
                ):
                    favorites.pop(cid_key, None)
                    st.rerun()

    with recent_tab:
        if not recent_compounds:
            st.info("Compounds you select in the Explorer will appear here.")
        if recent_compounds and st.button(
            "Clear recent compounds", key="clear_recent_compounds"
        ):
            st.session_state["research_recent_compounds"] = []
            st.rerun()
        for index, entry in enumerate(list(recent_compounds)):
            compound = entry["compound"]
            cid_key = str(compound["cid"])
            title = _compound_title(compound)
            with st.expander(f"{title} · CID {compound['cid']}"):
                st.caption(
                    f"{compound.get('molecular_formula') or 'Formula unavailable'} · "
                    f"Opened {_format_library_timestamp(entry['accessed_at'])}"
                )
                if entry.get("query"):
                    st.caption(f"Search: {entry['query']}")
                action_columns = st.columns(2)
                if action_columns[0].button(
                    "Open profile", key=f"recent_open_{index}_{cid_key}", type="primary"
                ):
                    _open_research_compound(
                        compound,
                        entry.get("query") or compound.get("iupac_name") or f"CID {cid_key}",
                    )
                is_favorite = cid_key in favorites
                label = "Remove favorite" if is_favorite else "Add to favorites"
                if action_columns[1].button(
                    label, key=f"recent_favorite_{index}_{cid_key}"
                ):
                    if is_favorite:
                        favorites.pop(cid_key, None)
                    else:
                        favorites[cid_key] = compound
                    st.rerun()

    with reports_tab:
        if not saved_reports:
            st.info("Save a report from Safety Reports to keep it here.")
        for cid_key, saved in list(saved_reports.items()):
            compound = saved["compound"]
            title = saved.get("title") or _compound_title(compound)
            with st.expander(f"{title} · CID {compound['cid']}"):
                st.caption(f"Generated {saved['generated_at']} UTC")
                st.dataframe(saved["rows"], use_container_width=True, hide_index=True)
                download_columns = st.columns(3)
                download_columns[0].download_button(
                    "PDF",
                    data=saved["pdf"],
                    file_name=f"chemguard_cid_{cid_key}.pdf",
                    mime="application/pdf",
                    key=f"saved_report_pdf_{cid_key}",
                )
                download_columns[1].download_button(
                    "CSV",
                    data=saved["csv"],
                    file_name=f"chemguard_cid_{cid_key}.csv",
                    mime="text/csv",
                    key=f"saved_report_csv_{cid_key}",
                )
                download_columns[2].download_button(
                    "Markdown",
                    data=saved["markdown"],
                    file_name=f"chemguard_cid_{cid_key}.md",
                    mime="text/markdown",
                    key=f"saved_report_markdown_{cid_key}",
                )
                action_columns = st.columns(2)
                if action_columns[0].button(
                    "Open profile", key=f"saved_report_open_{cid_key}"
                ):
                    _open_research_compound(
                        compound,
                        compound.get("iupac_name") or f"CID {cid_key}",
                    )
                if action_columns[1].button(
                    "Remove report", key=f"saved_report_remove_{cid_key}"
                ):
                    saved_reports.pop(cid_key, None)
                    st.rerun()

    with history_tab:
        if not search_history:
            st.info("Your submitted compound searches will appear here.")
        if search_history and st.button(
            "Clear search history", key="clear_search_history"
        ):
            st.session_state["research_search_history"] = []
            st.rerun()
        for index, entry in enumerate(list(search_history)):
            title = (
                f"{entry['query']} · {entry['result_count']} "
                f"{'result' if entry['result_count'] == 1 else 'results'}"
            )
            with st.expander(title):
                st.caption(_format_library_timestamp(entry["searched_at"]))
                if entry.get("error"):
                    st.warning(entry["error"])
                if entry.get("results"):
                    st.caption("Reopen the saved PubChem matches without searching again.")
                    if st.button(
                        "Load these results",
                        key=f"history_load_{index}",
                        type="primary",
                    ):
                        st.session_state["pubchem_compound_query"] = entry["query"]
                        st.session_state["pubchem_compound_results"] = entry["results"]
                        st.session_state["pubchem_compound_error"] = None
                        st.session_state.pop("pubchem_selected_compound", None)
                        st.session_state["research_last_recent_selection"] = None
                        st.session_state["chemguard_page"] = "dashboard"
                        st.rerun()


def render_safety_reports():
    render_brand()
    _render_module_navigation()
    st.title("Safety Reports")
    compound = st.session_state.get("selected_compound")
    if not isinstance(compound, dict):
        st.info("Search for and select a compound in the Compound Explorer first.")
        return
    _render_selected_compound(compound)
    try:
        profile = get_compound_safety_profile(compound["cid"])
        report = create_safety_report(compound, profile)
    except PubChemError as exc:
        st.error(f"Could not create the report because PubChem data was unavailable: {exc}")
        return
    except ValueError as exc:
        st.error(f"Could not create the report: {exc}")
        return
    st.markdown(
        "The report includes retrieved molecular properties, source-attributed GHS and "
        "storage annotations, available supplier SDS citations, and a verification note."
    )
    if not profile.get("available"):
        st.warning("PubChem safety annotations are unavailable; the report will label this clearly.")
    summary_columns = st.columns(3)
    summary_columns[0].metric("PubChem CID", compound["cid"])
    summary_columns[1].metric(
        "GHS sources", len(profile.get("ghs_classifications", []))
    )
    summary_columns[2].metric(
        "Supplier SDS citations", len(profile.get("supplier_sds_references", []))
    )
    st.caption(f"Report generated {report['generated_at']} UTC · {len(report['rows'])} sourced rows")
    st.dataframe(report["rows"], use_container_width=True, hide_index=True)
    cid_key = str(compound["cid"])
    if cid_key in st.session_state["research_saved_reports"]:
        st.caption("A report for this compound is already in your Research Library.")
        save_label = "Update saved report"
    else:
        save_label = "Save report to Research Library"
    if st.button(save_label, key=f"save_report_{cid_key}", type="primary"):
        st.session_state["research_saved_reports"][cid_key] = {
            "compound": compound,
            "title": report["title"],
            "generated_at": report["generated_at"],
            "rows": report["rows"],
            "pdf": report["pdf"],
            "csv": report["csv"],
            "markdown": report["markdown"],
        }
        st.success("Report saved to your Research Library for this session.")
    download_columns = st.columns(3)
    with download_columns[0]:
        st.download_button(
            "Download PDF",
            data=report["pdf"],
            file_name=f"chemguard_cid_{compound['cid']}.pdf",
            mime="application/pdf",
            type="primary",
        )
    with download_columns[1]:
        st.download_button(
            "Download CSV",
            data=report["csv"],
            file_name=f"chemguard_cid_{compound['cid']}.csv",
            mime="text/csv",
        )
    with download_columns[2]:
        st.download_button(
            "Download Markdown",
            data=report["markdown"],
            file_name=f"chemguard_cid_{compound['cid']}.md",
            mime="text/markdown",
        )


def render_dashboard():
    render_brand()
    if st.button("← Back to home", key="back_home"):
        st.session_state["chemguard_page"] = "home"
        st.rerun()
    st.markdown(
        """
        <div class="section-kicker" style="margin-top:24px">Your safety workspace</div>
        <h1 class="dashboard-heading">Welcome to ChemGuard</h1>
        <p class="dashboard-subtitle">A clear starting point for your chemical safety workflow.</p>
        <section class="dashboard-banner">
          <h2>Make your next safety decision with confidence.</h2>
          <p>Choose a tool below to get started. Always follow your organization’s procedures and consult current safety documentation.</p>
        </section>
        <div style="height:24px"></div>
        """,
        unsafe_allow_html=True,
    )

    library_columns = st.columns([1, 3])
    with library_columns[0]:
        if st.button("Open My Research Library", type="primary", key="open_research_library"):
            st.session_state["chemguard_page"] = "research_library"
            st.rerun()
    with library_columns[1]:
        st.caption(
            f"{len(st.session_state['research_favorites'])} favorites · "
            f"{len(st.session_state['research_recent_compounds'])} recent compounds · "
            f"{len(st.session_state['research_saved_reports'])} saved reports"
        )

    columns = st.columns(4, gap="medium")
    for column, (number, title, description, icon) in zip(columns, FEATURES):
        with column:
            st.markdown(
                f"""
                <article class="feature-card">
                  <div class="feature-top">
                    <div class="feature-icon">{icon}</div>
                    <span class="feature-number">{number}</span>
                  </div>
                  <h3>{title}</h3>
                  <p>{description}</p>
                </article>
                """,
                unsafe_allow_html=True,
            )

    render_compound_explorer()

    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="dashboard-note">
          <strong>Safety note:</strong> ChemGuard is an informational interface and is not a
          substitute for a Safety Data Sheet (SDS), qualified safety advice, or applicable
          workplace procedures.
        </div>
        <footer class="footer" style="margin-top:38px">
          <span><strong>ChemGuard</strong> &nbsp;·&nbsp; Making Chemical Safety Smarter.</span>
          <span>Project interface &nbsp;·&nbsp; Built with Streamlit</span>
        </footer>
        """,
        unsafe_allow_html=True,
    )


def main():
    inject_styles()
    _initialize_research_library()
    page = st.session_state.get("chemguard_page", "home")
    if page == "dashboard":
        render_dashboard()
    elif page == "hazard_assistant":
        render_hazard_assistant()
    elif page == "research_library":
        render_research_library()
    elif page == "safety_reports":
        render_safety_reports()
    else:
        render_landing_page()


if __name__ == "__main__":
    main()
