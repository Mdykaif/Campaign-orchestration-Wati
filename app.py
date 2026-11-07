import hmac
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import os
import re
import time

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv


# ============================================================
# 1. ENVIRONMENT & CONFIGURATION
# ============================================================

load_dotenv()

WATI_API_URL = os.getenv(
    "WATI_API_URL",
    "https://live-mt-server.wati.io"
).rstrip("/")

TENANT_ID = os.getenv("WATI_TENANT_ID")
TOKEN = os.getenv("WATI_API_TOKEN")

st.set_page_config(
    page_title="WATI Enterprise Broadcast Manager",
    layout="wide"
)

# ============================================================
# LOGIN & AUTHENTICATION
# ============================================================

APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")

if not APP_PASSWORD:
    st.error("Application password is not configured.")
    st.stop()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:

    st.title("WATI Campaign Manager")
    st.caption("Secure application access")

    with st.form("login_form"):
        st.subheader("Welcome Back")

        entered_password = st.text_input(
            "Enter Application Password",
            type="password",
            placeholder="Enter your password"
        )

        login_clicked = st.form_submit_button(
            "Login",
            type="primary",
            use_container_width=True
        )

        if login_clicked:
            if hmac.compare_digest(
                entered_password,
                str(APP_PASSWORD)
            ):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password. Please try again.")

    st.stop()

st.title("WATI Enterprise Campaign & Broadcast Manager")
st.caption(
    "Campaign orchestration, template parameter mapping "
    "and scheduled broadcast administration."
)

if not TENANT_ID or not TOKEN:
    st.error(
        "Authentication credentials missing. "
        "Check WATI_TENANT_ID and WATI_API_TOKEN."
    )
    st.stop()

api_headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# Session initialization
defaults = {
    "step": 1,
    "wati_templates": [],
    "template_fetch_error": None,
    "raw_df": None,
    "phone_col": None,
    "columns": [],
    "registry_cache": None,
    "registry_cache_signature": None,
    "registry_last_sync": None,
    "registry_error": None,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 2. PHONE SANITIZATION & VALIDATION
# ============================================================

def clean_phone(raw_phone):

    if raw_phone is None:
        return None

    if pd.isna(raw_phone):
        return None

    phone = str(raw_phone).strip()

    if phone.lower() in ["", "nan", "none", "null"]:
        return None

    phone = re.sub(r"\D", "", phone)

    if not phone:
        return None

    # Default India country code for 10-digit numbers.
    if len(phone) == 10:
        phone = "91" + phone

    return phone


def validate_phone(phone):

    if not phone:
        return False

    digits = re.sub(r"\D", "", str(phone))

    return 8 <= len(digits) <= 15


# ============================================================
# 3. RECIPIENT COMPILATION
# ============================================================

def get_recipients(df, phone_col, variable_mappings=None):

    recipients = []
    seen = set()

    invalid_count = 0
    duplicate_count = 0
    mapping_errors = []

    if phone_col not in df.columns:
        return [], len(df), 0, [f"Phone column '{phone_col}' not found."]

    variable_mappings = variable_mappings or {}

    # Validate mappings before recipient generation.
    for mapping_key, mapping_info in variable_mappings.items():

        source_type = mapping_info.get("type")
        target_name = str(
            mapping_info.get("target_param_name", "")
        ).strip()

        if not target_name:
            mapping_errors.append(
                f"Empty target parameter name: {mapping_key}"
            )

        if source_type == "CSV Column":

            col_name = mapping_info.get("column")

            if not col_name or col_name not in df.columns:
                mapping_errors.append(
                    f"CSV column not found: {col_name}"
                )

    if mapping_errors:
        return [], 0, 0, mapping_errors

    for _, row in df.iterrows():

        phone = clean_phone(row.get(phone_col))

        if not validate_phone(phone):
            invalid_count += 1
            continue

        if phone in seen:
            duplicate_count += 1
            continue

        seen.add(phone)

        recipient_entry = {
            "phone_number": phone
        }

        custom_params = []

        for mapping_key, mapping_info in variable_mappings.items():

            source_type = mapping_info.get("type")
            resolved_value = ""

            # CSV COLUMN SOURCE
            if source_type == "CSV Column":

                col_name = mapping_info.get("column")
                value = row.get(col_name)

                if value is None or pd.isna(value):
                    resolved_value = ""
                else:
                    resolved_value = str(value).strip()

            # CUSTOM TEXT SOURCE
            elif source_type == "Custom Text":

                resolved_value = str(
                    mapping_info.get("value", "")
                ).strip()

            # FALLBACK HANDLING
            if resolved_value.lower() in [
                "", "nan", "none", "null"
            ]:

                resolved_value = str(
                    mapping_info.get("fallback", "N/A")
                ).strip()

                if not resolved_value:
                    resolved_value = "N/A"

            # EXACTLY ONE PARAMETER PER TEMPLATE VARIABLE
            param_name = str(
                mapping_info.get("target_param_name", "")
            ).strip()

            custom_params.append({
                "name": param_name,
                "value": resolved_value
            })

        if custom_params:
            recipient_entry["custom_params"] = custom_params

        recipients.append(recipient_entry)

    return (
        recipients,
        invalid_count,
        duplicate_count,
        mapping_errors
    )


# ============================================================
# 4. TEMPLATE INTROSPECTION
# ============================================================

def get_template_name(template):

    if not isinstance(template, dict):
        return ""

    return str(
        template.get(
            "elementName",
            template.get(
                "templateName",
                template.get("name", "")
            )
        )
    )


def get_template_language(template):

    if not isinstance(template, dict):
        return ""

    language = template.get("language")

    if isinstance(language, dict):
        return str(
            language.get("code", language.get("name", ""))
        )

    return str(
        language or template.get("lang", "")
    )


def get_template_status(template):

    if not isinstance(template, dict):
        return ""

    return str(
        template.get(
            "status",
            template.get("approvalStatus", "")
        )
    )



def get_template_body(template):

    if not isinstance(template, dict):
        return ""

    # IMPORTANT: Prefer named variables from bodyOriginal
    body_original = template.get("bodyOriginal")

    if isinstance(body_original, str) and body_original.strip():
        return body_original

    # Fallback to components
    components = template.get("components", [])

    if isinstance(components, list):
        for comp in components:
            if (
                isinstance(comp, dict)
                and comp.get("type") == "BODY"
            ):
                text = comp.get("text", "")
                if text:
                    return str(text)

    body = template.get("body", "")

    if isinstance(body, dict):
        return str(
            body.get("text", body.get("body", ""))
        )

    return str(body or template.get("content", ""))



def extract_template_variables(template_obj):

    body_text = get_template_body(template_obj)

    if not body_text:
        return []

    tokens = re.findall(
        r"\{\{\s*(.*?)\s*\}\}",
        body_text
    )

    discovered_variables = []

    for idx, token in enumerate(tokens):

        raw_token = token.strip()

        discovered_variables.append({
            "detected_token": raw_token,
            "numeric_index": str(idx + 1),
            "display_label": raw_token or str(idx + 1)
        })

    return discovered_variables


def extract_template_array(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in [
        "messageTemplates",
        "templates",
        "data",
        "items",
        "results"
    ]:

        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):

            for nested_key in [
                "items",
                "data",
                "messageTemplates",
                "templates",
                "results"
            ]:

                nested_value = value.get(nested_key)

                if isinstance(nested_value, list):
                    return nested_value

    return []


# ============================================================
# 5. FETCH WATI TEMPLATES
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def _cached_wati_templates(api_base, tenant_id, token):
    """Fetch template pages concurrently in small waves; cache for five minutes."""
    page_size = 200
    max_pages = 100
    max_workers = 4
    url = f"{api_base}/{tenant_id}/api/v1/getMessageTemplates"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    def fetch_page(page_number):
        params = {"pageSize": page_size, "pageNumber": page_number}
        try:
            response = requests.get(
                url, headers=headers, params=params, timeout=(5, 30)
            )
        except requests.RequestException as exc:
            return page_number, [], f"Page {page_number}: network exception: {exc}"

        if response.status_code != 200:
            return page_number, [], (
                f"Page {page_number}: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            data = response.json()
        except ValueError:
            return page_number, [], f"Page {page_number}: invalid JSON response."

        return page_number, extract_template_array(data), None

    # Page 1 establishes whether more pages are likely to exist.
    first_page, first_templates, error = fetch_page(1)
    if error:
        return [], error
    all_templates = list(first_templates)
    if len(first_templates) < page_size:
        return all_templates, None

    # Fetch subsequent pages in bounded waves. The short/empty page ends paging.
    next_page = 2
    while next_page <= max_pages:
        page_numbers = list(range(next_page, min(next_page + max_workers, max_pages + 1)))
        page_results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_page, page_number): page_number
                for page_number in page_numbers
            }
            for future in as_completed(futures):
                page_number = futures[future]
                try:
                    result_page, templates, page_error = future.result()
                except Exception as exc:
                    return [], f"Page {page_number}: unexpected fetch error: {exc}"
                if page_error:
                    return [], page_error
                page_results[result_page] = templates

        stop_paging = False
        for page_number in page_numbers:
            templates = page_results.get(page_number, [])
            all_templates.extend(templates)
            if len(templates) < page_size:
                stop_paging = True
                break
        if stop_paging:
            break
        next_page += len(page_numbers)

    return all_templates, None

def fetch_all_wati_templates():
    return _cached_wati_templates(WATI_API_URL, TENANT_ID, TOKEN)


# ============================================================
# 6. IST TO UTC CONVERSION
# ============================================================

def ist_to_utc(date_value, time_value):

    ist_datetime = datetime.datetime.combine(
        date_value,
        time_value
    )

    utc_datetime = (
        ist_datetime - datetime.timedelta(hours=5, minutes=30)
    ).replace(tzinfo=datetime.timezone.utc)

    return utc_datetime


# ============================================================
# 7. CAMPAIGN SCHEDULING
# ============================================================

def schedule_campaign_chunk(payload):

    schedule_url = (
        f"{WATI_API_URL}/api/ext/v3/messageTemplates/schedule"
    )

    try:

        response = requests.post(
            schedule_url,
            headers=api_headers,
            json=payload,
            timeout=30
        )

        try:
            response_data = response.json()

        except ValueError:
            response_data = {
                "raw_response": response.text
            }

        api_success_flag = (
            response_data.get("success", True)
            if isinstance(response_data, dict)
            else True
        )

        return {
            "status_code": response.status_code,
            "data": response_data,
            "success": (
                response.status_code in [200, 201, 202]
                and api_success_flag is not False
            )
        }

    except requests.RequestException as e:

        return {
            "status_code": 0,
            "data": {"error": str(e)},
            "success": False
        }


# ============================================================
# 8. SIDEBAR NAVIGATION
# ============================================================

app_window = st.sidebar.radio(
    "Operational Navigation",
    [
        "Campaign Builder & Dispatch",
        "Scheduled Broadcast Registry"
    ]
)
if st.sidebar.button("Logout", use_container_width=True):
    st.session_state.authenticated = False
    st.rerun()


# ============================================================
# WORKSPACE 1: CAMPAIGN BUILDER & DISPATCH
# ============================================================

if app_window == "Campaign Builder & Dispatch":

    # --------------------------------------------------------
    # STEP 1: CSV UPLOAD
    # --------------------------------------------------------

    if st.session_state.step == 1:

        st.subheader("Step 1: Ingest Contact Records")

        uploaded_file = st.file_uploader(
            "Upload target audience CSV",
            type=["csv"]
        )

        if uploaded_file is not None:

            try:

                df_preview = pd.read_csv(
                    uploaded_file,
                    dtype=str,
                    keep_default_na=False,
                    encoding="cp1252"
                )

                st.session_state.raw_df = df_preview

                st.success(
                    f"Dataset loaded: {len(df_preview)} rows, "
                    f"{len(df_preview.columns)} columns."
                )

                st.dataframe(
                    df_preview.head(5),
                    use_container_width=True
                )

                columns = df_preview.columns.tolist()

                phone_col = st.selectbox(
                    "Target Phone Number Column",
                    columns,
                    index=(
                        columns.index("Phone")
                        if "Phone" in columns
                        else 0
                    )
                )

                st.session_state.phone_col = phone_col
                st.session_state.columns = columns

                if st.button(
                    "Proceed to Sequence Architecture →",
                    type="primary"
                ):

                    st.session_state.step = 2
                    st.rerun()

            except Exception as e:

                st.error(f"CSV parsing exception: {e}")


    # --------------------------------------------------------
    # STEP 2: TEMPLATE & SEQUENCE CONFIGURATION
    # --------------------------------------------------------

    elif st.session_state.step == 2:

        st.subheader(
            "Step 2: Sequence Orchestration & Parameter Binding"
        )

        df = st.session_state.raw_df
        phone_col = st.session_state.phone_col
        columns = st.session_state.columns

        if df is None:
            st.error("No dataset found. Please upload a CSV.")
            st.session_state.step = 1
            st.stop()

        # Fetch templates
        if not st.session_state.wati_templates:

            with st.spinner("Synchronizing WATI templates..."):

                templates, error = fetch_all_wati_templates()

                st.session_state.wati_templates = templates
                st.session_state.template_fetch_error = error

        templates = st.session_state.wati_templates

        if st.session_state.template_fetch_error:

            st.error(st.session_state.template_fetch_error)

        if not templates:

            st.warning(
                "No templates found. Check WATI permissions and API access."
            )

            if st.button("Re-evaluate Template Registry"):

                st.session_state.wati_templates = []
                st.session_state.template_fetch_error = None
                _cached_wati_templates.clear()
                st.rerun()

            st.stop()

        # Build template lookup without duplicate-key collisions.
        template_options = []
        template_lookup = {}

        for idx, template in enumerate(templates):

            name = get_template_name(template)
            lang = get_template_language(template)
            status = get_template_status(template)

            if not name:
                continue

            display = f"{name} | {lang} | {status} | #{idx}"

            template_options.append(display)
            template_lookup[display] = template

        if not template_options:
            st.error("No usable templates found.")
            st.stop()

        # Campaign settings
        base_campaign_name = st.text_input(
            "Broadcast Batch Identifier",
            value="Enterprise_Broadcast"
        )

        col_date, col_time = st.columns(2)

        with col_date:

            sched_date = st.date_input(
                "Scheduled Execution Date (IST)",
                value=datetime.date.today()
            )

        with col_time:

            sched_time = st.time_input(
                "Scheduled Execution Time (IST)",
                value=datetime.datetime.now().time().replace(
                    second=0,
                    microsecond=0
                )
            )

        send_now_toggle = st.checkbox(
            "Immediate Execution Pipeline (2-minute forward buffer)",
            value=False
        )

        # Rate limits
        with st.expander("Ingress Rate-Limiting Controls"):

            batch_size = st.number_input(
                "Payload Batch Chunk Size",
                min_value=10,
                max_value=1000,
                value=200,
                step=50
            )

            batch_delay_sec = st.number_input(
                "Ingress Interval Delay (Seconds)",
                min_value=0.0,
                max_value=10.0,
                value=1.0,
                step=0.5
            )

        # Sequence configuration
        num_messages = st.number_input(
            "Sequence Steps",
            min_value=1,
            max_value=10,
            value=1,
            step=1
        )

        sequence_steps = []

        for i in range(int(num_messages)):

            st.markdown(f"#### Sequence Stage {i + 1}")

            col_t, col_delay = st.columns([3, 1])

            with col_t:

                selected_display = st.selectbox(
                    f"Selected Template Stage #{i + 1}",
                    template_options,
                    key=f"tpl_select_{i}"
                )

            with col_delay:

                delay_hrs = st.number_input(
                    f"Staging Delay (Hours) #{i + 1}",
                    min_value=0.0,
                    value=float(i * 24),
                    key=f"delay_{i}"
                )

            selected_template = template_lookup[selected_display]

            body_text = get_template_body(selected_template)

            variable_metadata = extract_template_variables(
                selected_template
            )
            st.subheader("DEBUG 1: Selected Template JSON")
            st.json(selected_template)

            if body_text:

                with st.expander(
                    f"Inspect Template Body #{i + 1}"
                ):

                    st.text(body_text)

            variable_mappings = {}

            if variable_metadata:

                st.markdown(
                    f"**Variable Mapping Configuration - Stage {i + 1}**"
                )

                for var_meta in variable_metadata:

                    raw_token = var_meta["detected_token"]
                    numeric_token = var_meta["numeric_index"]

                    unique_field_key = (
                        f"step_{i}_var_{raw_token}_{numeric_token}"
                    )

                    st.markdown(
                        f"**Detected Token: `{{{{{raw_token}}}}}`**"
                    )

                    (
                        col_target_name,
                        col_src_type,
                        col_val_src,
                        col_fallback
                    ) = st.columns([2, 2, 3, 2])

                    with col_target_name:

                        target_name = st.text_input(
                            "Target Parameter Key",
                            value=raw_token,
                            key=f"target_name_{unique_field_key}",
                            help=(
                                "Must match the parameter name "
                                "expected by the WATI template API."
                            )
                        )

                    with col_src_type:

                        source_type = st.selectbox(
                            "Source Type",
                            ["CSV Column", "Custom Text"],
                            key=f"source_type_{unique_field_key}"
                        )

                    with col_val_src:

                        if source_type == "CSV Column":

                            csv_column_options = ["-- Select CSV column --"] + columns

                            col_choice = st.selectbox(
                                "CSV Data Column",
                                csv_column_options,
                                index=0,
                                key=f"col_choice_{unique_field_key}",
                                help="Select the column containing the actual template value. Do not select Phone unless required."
                            )

                            val_entry = col_choice

                        else:

                            custom_val = st.text_input(
                                "Constant String",
                                value="Value",
                                key=f"val_{unique_field_key}"
                            )

                            val_entry = custom_val

                    with col_fallback:

                        fallback_val = st.text_input(
                            "Null Value Fallback",
                            value="N/A",
                            key=f"fallback_{unique_field_key}"
                        )

                    variable_mappings[unique_field_key] = {

                        "type": source_type,

                        "column": (
                            val_entry
                            if source_type == "CSV Column"
                            else None
                        ),

                        "value": (
                            val_entry
                            if source_type == "Custom Text"
                            else None
                        ),

                        "fallback": fallback_val,

                        "detected_token": raw_token,

                        "target_param_name": target_name.strip()
                    }

            else:

                st.caption(
                    "No dynamic variables detected in the template BODY."
                )

            sequence_steps.append({

                "template_name": get_template_name(
                    selected_template
                ),

                "delay": float(delay_hrs),

                "variable_mappings": variable_mappings

            })

        st.divider()

        # ----------------------------------------------------
        # RECIPIENT PREVIEW
        # ----------------------------------------------------

        recipients_list, invalid_count, duplicate_count, preview_errors = (
            get_recipients(df, phone_col)
        )

        m1, m2, m3 = st.columns(3)

        with m1:
            st.metric(
                "Eligible Unique Recipients",
                len(recipients_list)
            )

        with m2:
            st.metric(
                "Invalid Records Filtered",
                invalid_count
            )

        with m3:
            st.metric(
                "Duplicates Suppressed",
                duplicate_count
            )

        # Canary mode
        test_mode = st.checkbox(
            "Canary Dispatch Mode (Transmit only initial 5 records)",
            value=True
        )

        # IMPORTANT: Keep the original CSV columns.
        active_df = (
            df.head(5).copy()
            if test_mode
            else df.copy()
        )

        active_recipients, active_invalid, active_duplicates, active_errors = (
            get_recipients(active_df, phone_col)
        )

        st.info(
            f"Dispatch audience: {len(active_recipients)} unique recipients."
        )

        # ----------------------------------------------------
        # PREVIEW ORIGINAL DATA
        # ----------------------------------------------------

        with st.expander("Preview Dispatch Audience"):

            st.dataframe(
                active_df.head(10),
                use_container_width=True
            )

        # Navigation
        col_back, col_exec = st.columns(2)

        with col_back:

            if st.button("← Return to Data Upload"):

                st.session_state.step = 1
                st.rerun()

        with col_exec:

            authorize = st.button(
                "Authorize & Schedule Campaign",
                type="primary"
            )

        # ----------------------------------------------------
        # DISPATCH EXECUTION
        # ----------------------------------------------------

        if authorize:

            if not active_recipients:

                st.error(
                    "No valid recipients available for dispatch."
                )
                st.stop()

            if active_errors:

                st.error("Recipient compilation errors:")
                st.write(active_errors)
                st.stop()

            if not base_campaign_name.strip():

                st.error("Campaign name cannot be empty.")
                st.stop()

            # Validate parameter mapping for every stage.
            validation_failed = False

            for stage_number, step_cfg in enumerate(
                sequence_steps,
                start=1
            ):

                mappings = step_cfg["variable_mappings"]

                for mapping_key, mapping_info in mappings.items():

                    if not mapping_info.get("target_param_name"):

                        st.error(
                            f"Stage {stage_number}: "
                            f"Empty target parameter name."
                        )

                        validation_failed = True

                    if (
                        mapping_info.get("type") == "CSV Column"
                        and mapping_info.get("column") not in df.columns
                    ):

                        st.error(
                            f"Stage {stage_number}: "
                            f"CSV column not found: "
                            f"{mapping_info.get('column')}"
                        )

                        validation_failed = True

            if validation_failed:
                st.stop()

            all_success = True
            total_submitted = 0

            progress = st.progress(0)

            total_operations = sum(
                (
                    len(active_df) + int(batch_size) - 1
                ) // int(batch_size)
                for _ in sequence_steps
            )

            completed_operations = 0

            # Base schedule time.
            if send_now_toggle:

                base_dt_utc = (
                    datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(minutes=2)
                )

            else:

                base_dt_utc = ist_to_utc(
                    sched_date,
                    sched_time
                )

            if not send_now_toggle and base_dt_utc <= datetime.datetime.now(
                datetime.timezone.utc
            ):

                st.error(
                    "Scheduled execution time must be in the future."
                )
                st.stop()

            # Execute every sequence stage.
            for idx, step_cfg in enumerate(sequence_steps):

                step_dt_utc = (
                    base_dt_utc
                    + datetime.timedelta(hours=step_cfg["delay"])
                )

                iso_time = step_dt_utc.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )

                total_chunks = (
                    len(active_df) + int(batch_size) - 1
                ) // int(batch_size)

                st.markdown(
                    f"### Stage {idx + 1}: "
                    f"{step_cfg['template_name']}"
                )

                # Build the stage payloads first, then submit them in bounded
                # concurrent waves. Stages themselves remain sequential.
                stage_jobs = []
                stage_build_failed = False

                for chunk_idx in range(total_chunks):
                    start_idx = chunk_idx * int(batch_size)
                    end_idx = min(start_idx + int(batch_size), len(active_df))
                    chunk_slice_df = active_df.iloc[start_idx:end_idx].copy()

                    (
                        chunk_recipients,
                        chunk_invalid,
                        chunk_duplicates,
                        chunk_errors
                    ) = get_recipients(
                        chunk_slice_df,
                        phone_col,
                        variable_mappings=step_cfg["variable_mappings"]
                    )

                    if chunk_errors:
                        all_success = False
                        stage_build_failed = True
                        st.error(
                            f"Stage {idx + 1}, Chunk {chunk_idx + 1}: "
                            "Parameter mapping failed."
                        )
                        st.write(chunk_errors)
                        break

                    if not chunk_recipients:
                        st.warning(
                            f"Chunk {chunk_idx + 1} contains no valid recipients."
                        )
                        completed_operations += 1
                        progress.progress(
                            min(completed_operations / total_operations, 1.0)
                        )
                        continue

                    payload = {
                        "channel": None,
                        "template_name": step_cfg["template_name"],
                        "broadcast_name": (
                            f"{base_campaign_name}_Stage{idx + 1}_Chunk{chunk_idx + 1}"
                        ),
                        "scheduled_at": iso_time,
                        "recipients": chunk_recipients
                    }
                    stage_jobs.append((chunk_idx, payload, len(chunk_recipients)))

                    with st.expander(
                        f"Payload Preview - Stage {idx + 1}, Chunk {chunk_idx + 1}"
                    ):
                        st.json(payload)

                if stage_build_failed:
                    break

                # Keep concurrency deliberately low to reduce WATI 429 risk.
                # No POST retries are performed because a timeout can leave the
                # broadcast creation outcome unknown and a retry could duplicate it.
                campaign_workers = 3
                for wave_start in range(0, len(stage_jobs), campaign_workers):
                    wave = stage_jobs[wave_start:wave_start + campaign_workers]
                    wave_results = {}

                    with ThreadPoolExecutor(max_workers=campaign_workers) as executor:
                        future_to_job = {
                            executor.submit(schedule_campaign_chunk, payload): (chunk_idx, recipient_count)
                            for chunk_idx, payload, recipient_count in wave
                        }
                        for future in as_completed(future_to_job):
                            chunk_idx, recipient_count = future_to_job[future]
                            try:
                                wave_results[chunk_idx] = (future.result(), recipient_count)
                            except Exception as exc:
                                wave_results[chunk_idx] = ({
                                    "status_code": 0,
                                    "data": {"error": str(exc)},
                                    "success": False
                                }, recipient_count)

                    # Render results in original chunk order, not completion order.
                    for chunk_idx, _, _ in wave:
                        result, recipient_count = wave_results[chunk_idx]
                        if result["success"]:
                            total_submitted += recipient_count
                            st.success(
                                f"Stage {idx + 1}, Chunk {chunk_idx + 1}/{total_chunks} "
                                f"accepted for {iso_time} UTC."
                            )
                            st.json(result["data"])
                        else:
                            all_success = False
                            st.error(
                                f"Stage {idx + 1}, Chunk {chunk_idx + 1} rejected. "
                                f"HTTP {result['status_code']}"
                            )
                            st.json(result["data"])

                        completed_operations += 1
                        progress.progress(
                            min(completed_operations / total_operations, 1.0)
                        )

                    # If any request in the wave failed, do not submit later waves.
                    if not all(wave_results[i][0]["success"] for i, _, _ in wave):
                        if any(
                            wave_results[i][0]["status_code"] == 429
                            for i, _, _ in wave
                        ):
                            st.error(
                                "WATI rate limit exceeded. Remaining chunks were not submitted. "
                                "Some requests in the current concurrent wave may already have succeeded."
                            )
                        break

                    if batch_delay_sec > 0 and wave_start + campaign_workers < len(stage_jobs):
                        time.sleep(batch_delay_sec)

                if not all_success:
                    break

            # Final status.
            st.divider()

            if all_success:

                st.balloons()

                st.success(
                    "Broadcast pipeline successfully compiled "
                    "and accepted by WATI."
                )

                st.metric(
                    "Recipients Accepted Across All Stages",
                    total_submitted
                )

            else:

                st.error(
                    "Campaign execution was partially or fully rejected. "
                    "Inspect the individual API responses before retrying."
                )

                st.metric(
                    "Recipients Accepted Before Failure",
                    total_submitted
                )


# ============================================================
# WORKSPACE 2: SCHEDULED BROADCAST REGISTRY
# ============================================================

elif app_window == "Scheduled Broadcast Registry":

    st.subheader("Scheduled Broadcast Administration Console")
    st.caption("Paginated registry sync, broadcast inspection and cancellation.")

    PAGE_SIZE = 100
    MAX_PAGES = 500
    MAX_WORKERS = 5
    GET_RETRIES = 3

    def api_response_json(response):
        """Return JSON where possible, otherwise a bounded raw response."""
        try:
            return response.json()
        except ValueError:
            return {"raw_response": (response.text or "")[:3000]}

    def safe_get(url, params=None, timeout=(5, 35)):
        """Retry safe GETs only; never automatically retry writes."""
        last_response = None
        for attempt in range(GET_RETRIES):
            try:
                response = requests.get(
                    url, headers=api_headers, params=params, timeout=timeout
                )
            except requests.RequestException:
                if attempt == GET_RETRIES - 1:
                    raise
                time.sleep(min(2 ** attempt, 5))
                continue

            last_response = response
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt == GET_RETRIES - 1:
                    return response
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(max(float(retry_after), 0), 15) if retry_after else min(2 ** attempt, 5)
                except (TypeError, ValueError):
                    delay = min(2 ** attempt, 5)
                time.sleep(delay)
                continue
            return response
        return last_response

    def find_broadcast_records(obj, depth=0):
        """Find a likely list of broadcast records in WATI response wrappers."""
        if depth > 5:
            return [], {}
        if isinstance(obj, list):
            if not obj or isinstance(obj[0], dict):
                return obj, {}
            return [], {}
        if not isinstance(obj, dict):
            return [], {}

        metadata = dict(obj)
        candidate_keys = (
            "content", "items", "campaigns", "broadcasts", "results", "data"
        )
        for key in candidate_keys:
            value = obj.get(key)
            if isinstance(value, list):
                return value, metadata
            if isinstance(value, dict):
                records, nested_meta = find_broadcast_records(value, depth + 1)
                if records or any(k in value for k in candidate_keys):
                    merged = dict(metadata)
                    merged.update(nested_meta)
                    return records, merged
        return [], metadata

    def read_total_pages(meta, page_size):
        for key in ("total_pages", "totalPages", "page_count", "pageCount"):
            value = meta.get(key)
            try:
                if value is not None and int(value) > 0:
                    return int(value)
            except (TypeError, ValueError):
                pass
        for key in ("total_count", "totalCount", "total_records", "totalRecords", "totalElements"):
            value = meta.get(key)
            try:
                if value is not None:
                    count = int(value)
                    return max(1, (count + page_size - 1) // page_size)
            except (TypeError, ValueError):
                pass
        return None

    def fetch_registry_page(page_number, date_from_utc, date_to_utc):
        url = f"{WATI_API_URL}/api/ext/v3/broadcasts"
        params = {
            "date_from": date_from_utc,
            "date_to": date_to_utc,
            "page_number": page_number,
            "page_size": PAGE_SIZE,
        }
        response = safe_get(url, params=params)
        if response is None or response.status_code != 200:
            code = response.status_code if response is not None else 0
            body = response.text[:1000] if response is not None else "No response"
            raise RuntimeError(f"Page {page_number}: HTTP {code} — {body}")
        data = api_response_json(response)
        if isinstance(data, dict) and "raw_response" in data:
            raise RuntimeError(f"Page {page_number}: WATI returned invalid JSON.")
        records, metadata = find_broadcast_records(data)
        return page_number, records, metadata

    def fetch_all_broadcasts(date_from_utc, date_to_utc):
        """Fetch page 1 first, then parallelize remaining pages when count is known."""
        _, first_page, metadata = fetch_registry_page(1, date_from_utc, date_to_utc)
        all_records = list(first_page)
        total_pages = read_total_pages(metadata, PAGE_SIZE)

        if total_pages is not None:
            total_pages = min(total_pages, MAX_PAGES)
            if total_pages > 1:
                page_numbers = range(2, total_pages + 1)
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {
                        executor.submit(
                            fetch_registry_page, page, date_from_utc, date_to_utc
                        ): page for page in page_numbers
                    }
                    page_results = {}
                    for future in as_completed(futures):
                        page_no, records, _ = future.result()
                        page_results[page_no] = records
                for page_no in sorted(page_results):
                    all_records.extend(page_results[page_no])
        else:
            # If WATI omits page metadata, fetch sequentially until a short page.
            # This avoids assuming a page count and avoids unnecessary concurrent calls.
            page_no = 2
            while page_no <= MAX_PAGES and len(first_page) == PAGE_SIZE:
                _, records, _ = fetch_registry_page(page_no, date_from_utc, date_to_utc)
                if not records:
                    break
                all_records.extend(records)
                if len(records) < PAGE_SIZE:
                    break
                page_no += 1

        # De-duplicate by broadcast ID while preserving API order.
        unique = []
        seen_ids = set()
        for record in all_records:
            if not isinstance(record, dict):
                continue
            bid = record.get("id", record.get("broadcastId", record.get("broadcast_id")))
            if bid is not None:
                key = str(bid)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
            unique.append(record)
        return unique

    def broadcast_id(record):
        return str(record.get("id", record.get("broadcastId", record.get("broadcast_id", ""))))

    def broadcast_name(record):
        return str(record.get("name", record.get("broadcastName", record.get("broadcast_name", "Unnamed broadcast"))))

    def format_broadcast_rows(records):
        rows = []
        for item in records:
            rows.append({
                "Broadcast ID": broadcast_id(item),
                "Broadcast Name": broadcast_name(item),
                "Lifecycle Status": item.get("status", item.get("state", "Unknown")),
                "Created Timestamp": item.get("createdTime", item.get("createdAt", item.get("created_at", ""))),
                "Scheduled At": item.get("scheduledAt", item.get("scheduled_at", item.get("scheduledTime", ""))),
                "Template": item.get("templateName", item.get("template_name", "")),
                "Recipients": item.get("totalRecipients", item.get("recipientCount", item.get("recipientsCount", ""))),
            })
        return pd.DataFrame(rows)

    # WATI date filters are date-time values. Convert IST day boundaries to UTC.
    col1, col2 = st.columns(2)
    with col1:
        from_date = st.date_input(
            "Date From",
            value=datetime.date.today() - datetime.timedelta(days=30),
            key="broadcast_from_date"
        )
    with col2:
        to_date = st.date_input(
            "Date To",
            value=datetime.date.today() + datetime.timedelta(days=30),
            key="broadcast_to_date"
        )

    if from_date > to_date:
        st.error("Date From cannot be later than Date To.")
        st.stop()

    date_from_utc = ist_to_utc(from_date, datetime.time.min).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to_utc = ist_to_utc(to_date, datetime.time.max).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache_signature = (date_from_utc, date_to_utc)

    sync_col, clear_col = st.columns([1, 1])
    with sync_col:
        sync_clicked = st.button("Synchronize Gateway Records", type="primary")
    with clear_col:
        clear_clicked = st.button("Clear Cached Registry")

    if clear_clicked:
        st.session_state.registry_cache = None
        st.session_state.registry_cache_signature = None
        st.session_state.registry_error = None
        st.rerun()

    needs_sync = (
        sync_clicked
        or st.session_state.registry_cache is None
        or st.session_state.registry_cache_signature != cache_signature
    )

    if needs_sync:
        try:
            with st.spinner("Fetching WATI broadcast pages..."):
                records = fetch_all_broadcasts(date_from_utc, date_to_utc)
            st.session_state.registry_cache = records
            st.session_state.registry_cache_signature = cache_signature
            st.session_state.registry_last_sync = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state.registry_error = None
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            st.session_state.registry_error = str(exc)

    if st.session_state.registry_error:
        st.error(f"Registry sync failed: {st.session_state.registry_error}")
        if st.session_state.registry_cache:
            st.warning("Showing the last successfully cached results; these may be stale.")

    campaigns_list = st.session_state.registry_cache or []
    if st.session_state.registry_cache_signature == cache_signature:
        st.caption(f"Last successful sync: {st.session_state.registry_last_sync or 'Not available'}")

    if campaigns_list:
        st.success(f"Loaded {len(campaigns_list)} unique broadcast records.")
        st.dataframe(format_broadcast_rows(campaigns_list), use_container_width=True, hide_index=True)

        selectable = [c for c in campaigns_list if broadcast_id(c)]
        labels = {
            f"{broadcast_name(c)} | {broadcast_id(c)} | {c.get('status', 'Unknown')}": broadcast_id(c)
            for c in selectable
        }
        if labels:
            selected_label = st.selectbox("Select a broadcast to inspect or manage", list(labels.keys()))
            selected_id = labels[selected_label]

            detail_tab, cancel_tab, reschedule_tab = st.tabs([
                "Broadcast Details", "Cancel Broadcast", "Experimental Reschedule"
            ])

            with detail_tab:
                if st.button("Fetch Full Broadcast Details", key="fetch_broadcast_detail"):
                    detail_url = f"{WATI_API_URL}/api/ext/v3/broadcasts/{selected_id}"
                    try:
                        with st.spinner("Fetching broadcast details..."):
                            detail_response = safe_get(detail_url)
                        if detail_response.status_code == 200:
                            detail_data = api_response_json(detail_response)
                            st.json(detail_data)
                            st.session_state[f"broadcast_detail_{selected_id}"] = detail_data
                        else:
                            st.error(f"Detail request failed: HTTP {detail_response.status_code}")
                            st.text(detail_response.text[:2000])
                    except requests.RequestException as exc:
                        st.error(f"Detail request failed: {exc}")

                cached_detail = st.session_state.get(f"broadcast_detail_{selected_id}")
                if cached_detail is not None:
                    with st.expander("Previously fetched detail response", expanded=False):
                        st.json(cached_detail)

                with st.expander("List record snapshot"):
                    selected_record = next((x for x in selectable if broadcast_id(x) == selected_id), None)
                    st.json(selected_record or {})

            with cancel_tab:
                st.warning("Cancellation is a write operation. Confirm the exact broadcast ID before proceeding.")
                st.code(selected_id)
                confirm_cancel = st.checkbox(
                    f"I confirm cancellation of broadcast {selected_id}",
                    key=f"confirm_cancel_{selected_id}"
                )
                if st.button("Cancel Scheduled Broadcast", type="primary", disabled=not confirm_cancel, key=f"cancel_{selected_id}"):
                    # Legacy WATI V1 cancellation endpoint from the existing integration.
                    cancel_url = (
                        f"{WATI_API_URL}/{TENANT_ID}/api/v1/broadcast/"
                        f"deleteScheduledBroadcast/{selected_id}"
                    )
                    try:
                        # No automatic retry for DELETE: an unknown outcome must be checked first.
                        cancel_response = requests.delete(
                            cancel_url, headers=api_headers, timeout=(5, 30)
                        )
                        if cancel_response.status_code in (200, 202, 204):
                            st.success(f"Cancellation request accepted for broadcast {selected_id}.")
                            st.session_state.registry_cache = [
                                row for row in campaigns_list if broadcast_id(row) != selected_id
                            ]
                            st.session_state.registry_last_sync = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            st.error(f"Cancellation failed: HTTP {cancel_response.status_code}")
                            st.text(cancel_response.text[:2000])
                    except requests.RequestException as exc:
                        st.error(
                            "Cancellation outcome is unknown because the connection failed. "
                            "Refresh the registry and verify the broadcast status before retrying."
                        )
                        st.caption(str(exc))

            with reschedule_tab:
                st.warning(
                    "Experimental only: the updateScheduledBroadcast endpoint in the original "
                    "integration is not confirmed in the current WATI API documentation. "
                    "Use only if WATI has explicitly enabled this endpoint for your tenant."
                )
                enable_experimental = st.checkbox(
                    "I understand this endpoint is unverified and want to enable it",
                    key=f"enable_reschedule_{selected_id}"
                )
                new_date = st.date_input(
                    "New Date (IST)", value=datetime.date.today() + datetime.timedelta(days=1),
                    key=f"reschedule_date_{selected_id}"
                )
                new_time = st.time_input(
                    "New Time (IST)", value=datetime.time(12, 0),
                    key=f"reschedule_time_{selected_id}"
                )
                if st.button(
                    "Attempt Experimental Reschedule", type="primary",
                    disabled=not enable_experimental,
                    key=f"experimental_reschedule_{selected_id}"
                ):
                    new_dt_utc = ist_to_utc(new_date, new_time)
                    if new_dt_utc <= datetime.datetime.now(datetime.timezone.utc):
                        st.error("Please select a future date and time.")
                    else:
                        new_iso = new_dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                        experimental_url = (
                            f"{WATI_API_URL}/{TENANT_ID}/api/v1/broadcast/"
                            "updateScheduledBroadcast"
                        )
                        try:
                            # No automatic retry for PUT: avoid duplicate/uncertain writes.
                            edit_response = requests.put(
                                experimental_url,
                                headers=api_headers,
                                json={"broadcastId": selected_id, "scheduledTime": new_iso},
                                timeout=(5, 30)
                            )
                            if edit_response.status_code in (200, 202, 204):
                                st.success(f"Experimental reschedule accepted: {new_iso} UTC")
                                st.json(api_response_json(edit_response))
                                st.session_state.registry_cache = None
                            else:
                                st.error(f"Experimental reschedule failed: HTTP {edit_response.status_code}")
                                st.text(edit_response.text[:2000])
                        except requests.RequestException as exc:
                            st.error(
                                "Reschedule outcome is unknown due to a connection failure. "
                                "Verify the broadcast in WATI before trying again."
                            )
                            st.caption(str(exc))
        else:
            st.info("No broadcast IDs were present in the returned records.")
    else:
        st.info("No scheduled broadcast records found for this date range.")
