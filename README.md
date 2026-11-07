# WATI Enterprise Campaign & Broadcast Manager

A Streamlit-based campaign orchestration and WhatsApp broadcast management application powered by the WATI API.

The application enables users to upload contact datasets, configure WhatsApp message templates, dynamically map template variables, create multi-stage messaging sequences, and schedule broadcasts in bulk.

## 🚀 Features

### 1. Campaign Builder & Dispatch
- Upload CSV files containing recipient information.
- Automatically identify and select phone number columns.
- Preview uploaded datasets before processing.
- Configure multiple WhatsApp message sequence stages.
- Select approved WATI templates.
- Schedule campaigns for immediate or future execution.

### 2. Dynamic Template Variable Mapping
- Automatically extract dynamic variables from WhatsApp template bodies.
- Map template variables to CSV columns.
- Support custom text values.
- Configure fallback values for missing or empty data.
- Configure parameter names expected by the WATI API.

### 3. Recipient Management
- Automatically clean phone numbers.
- Add India's country code (`91`) to 10-digit phone numbers.
- Validate phone number formats.
- Remove duplicate recipients.
- Filter invalid phone numbers.
- Preview eligible recipients before dispatch.

### 4. Campaign Scheduling
- Schedule broadcasts using Indian Standard Time (IST).
- Automatically convert IST to UTC.
- Support immediate execution with a two-minute buffer.
- Configure delays between sequence stages.
- Split large recipient datasets into configurable chunks.

### 5. Performance & API Optimization
- Concurrent WATI template fetching using `ThreadPoolExecutor`.
- Five-minute template caching.
- Concurrent broadcast scheduling with controlled workers.
- Configurable chunk sizes and submission intervals.
- Request timeouts and API error handling.
- Stops subsequent submission waves when a scheduling request fails.

### 6. Canary Dispatch Mode
- Test campaigns using the first five records.
- Preview outgoing payloads before scheduling.
- Verify template mappings and recipient configurations before full dispatch.

### 7. Scheduled Broadcast Registry
A navigation entry is provided for the scheduled broadcast registry workspace.

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application backend |
| Streamlit | User interface |
| Pandas | CSV processing and data manipulation |
| Requests | WATI API integration |
| ThreadPoolExecutor | Concurrent API operations |
| Python-dotenv | Environment variable management |
| WATI API | WhatsApp template and broadcast management |

## 📁 Project Structure

```text
wati-enterprise-broadcast-manager/
│
├── app.py
├── .env
├── .env.example
├── requirements.txt
├── .gitignore
└── README.md
```

## ⚙️ Installation & Setup

### Prerequisites

- Python 3.9 or later
- WATI account
- WATI API access token
- WATI Tenant ID

### 1. Clone the Repository

```bash
git clone YOUR_GITHUB_REPOSITORY_URL
```

```bash
cd wati-enterprise-broadcast-manager
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate the environment.

**Windows:**

```bash
venv\Scripts\activate
```

**Linux / macOS:**

```bash
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root.

```env
WATI_API_URL=https://live-mt-server.wati.io
WATI_TENANT_ID=YOUR_TENANT_ID
WATI_API_TOKEN=YOUR_API_TOKEN
```

**Important:** Never commit your `.env` file or expose your WATI API token publicly.

### 5. Run the Application

```bash
streamlit run app.py
```

The application will be available at:

```text
http://localhost:8501
```

## 📦 Requirements

Create a `requirements.txt` file:

```text
streamlit
pandas
requests
python-dotenv
```

## 🔄 Application Workflow

```text
Upload CSV Dataset
        |
        ▼
Select Phone Number Column
        |
        ▼
Fetch WATI Templates
        |
        ▼
Configure Campaign
        |
        ▼
Select Message Templates
        |
        ▼
Map Dynamic Variables
        |
        ▼
Configure Sequence & Delays
        |
        ▼
Preview Recipients & Payloads
        |
        ▼
Enable Canary Testing
        |
        ▼
Authorize Campaign
        |
        ▼
Split Recipients into Chunks
        |
        ▼
Schedule Broadcasts via WATI API
        |
        ▼
Review API Responses
```

## ⚡ Configuration Options

| Parameter | Default | Description |
|---|---:|---|
| Template page size | 200 | Templates fetched per API page |
| Template workers | 4 | Concurrent template-fetch workers |
| Template cache | 300 seconds | Template cache duration |
| Campaign batch size | 200 | Recipients per broadcast payload |
| Campaign workers | 3 | Concurrent scheduling requests |
| Batch delay | 1 second | Delay between submission waves |
| Immediate execution buffer | 2 minutes | Forward scheduling buffer |
| Canary mode | Enabled | Limits dispatch to first five CSV records |

## 🔐 Security Considerations

- Store API credentials in environment variables.
- Never hardcode API tokens in application code.
- Keep `.env` excluded from Git.
- Use a private GitHub repository when appropriate.
- Avoid sharing API responses containing sensitive customer information.
- Rotate exposed API tokens immediately.

## 📌 Important Notes

1. The application uses WATI's template retrieval endpoint to fetch available message templates.
2. Template retrieval results are cached for five minutes.
3. Campaign scheduling uses the WATI external scheduling endpoint.
4. Scheduling timestamps are submitted in UTC.
5. A failed scheduling request stops subsequent submission waves.
6. Automatic POST retries are intentionally avoided to reduce the risk of duplicate broadcasts when request outcomes are uncertain.
7. Canary mode should be disabled only after validating the campaign configuration.

## 🧑‍💻 Future Improvements

Potential enhancements:

- Scheduled broadcast registry with campaign status tracking.
- Campaign execution history and audit logs.
- Retry management for safely recoverable failures.
- Recipient-level delivery status tracking.
- Campaign analytics dashboard.
- Persistent campaign configuration storage.
- Role-based access control.
- Campaign duplication and reusable templates.
- Background task processing for large campaigns.

## 📄 License

All rights reserved. Add an appropriate open-source or proprietary license before distributing this project.

---

**WATI Enterprise Campaign & Broadcast Manager**

*Simplifying WhatsApp campaign orchestration through automation, dynamic parameter mapping, and controlled bulk scheduling.*
