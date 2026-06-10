EGIP - Enterprise Governance Intelligence Platform

## Project Overview

## Problem Statement

Organizations today face significant challenges in managing governance, compliance, and procurement intelligence:

- **Fragmented Information** - Governance documents, compliance policies and procurement data are scattered across multiple departments with no centralized access
- **Slow Decision-Making** - Finding relevant policies and approval limits takes hours or days of manual searching
- **Inconsistent Compliance** - Without a centralized system, organizations struggle to maintain consistent regulatory adherence
- **No Intelligent Assistance** - Employees lack a way to get instant answers from internal governance documents
- **Audit Trail Gaps** - Tracking who accessed what information and when is often manual and incomplete

## Target Users

| User Type                      | Role Description |
|-----------                     |------------------|
| **Board Members & Executives** | Need high-level governance overview, compliance status and decision support 
| **Compliance Officers**        | Require real-time risk monitoring, policy management and audit trails 
| **Procurement Teams**          | Need supplier evaluation tools, project requirement tracking, and vendor comparison 
| **Legal Department**           | Require instant access to policies, authority matrices, and compliance frameworks 
| **Internal Auditors**          | Need complete audit logs and document access history 

## System Goal

EGIP transforms enterprise knowledge into intelligent decisions by providing:

- A **centralized repository** for all governance, compliance and procurement documents
- An **AI-powered assistant** that answers questions based on your organization's actual policies
- **Real-time dashboards** with KPIs and risk distribution
- **Complete audit trails** for compliance and accountability
- **Role-based access control** to ensure data security

---

## System Architecture

## Data Flow
'''
┌─────────────────────────────────────────────────────────────────────────┐
│ DATA FLOW DIAGRAM                                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│ INPUT PROCESSING OUTPUT                                                 │
│                                                                         │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                         │
│ │ Text Files  │ ──────→       │   Ingest      │ ──────→ │ SQLite │      │
│ │ (.txt)      │ │ (ingest)    │ │ Database    │                         │
│ └─────────────┘ └─────────────┘ └─────────────┘                         │
│                                                                         │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                         │
│ │ User Query  │ ──────→       │   Search      │ ──────→ │ Relevant │    │
│ │ (Question)  │ │ Documents   │ │ Context     │                         │
│ └─────────────┘ └─────────────┘ └─────────────┘                         │
│                                                                         │
│ ↓                                                                       │
│                                                                         │
│ ┌─────────────────┐                                                     │
│ │ Ollama AI       │                                                     │
│ │ (Local LLM)     │                                                     │
│ └─────────────────┘                                                     │
│ ↓                                                                       │
│                                                                         │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                         │
│ │ Answer      │ ←──────       │ Generate      │ ←────── │ Context │     │
│ │ + Sources   │ │ Response    │ │ + Query     │                         │
│ └─────────────┘ └─────────────┘ └─────────────┘                         │
│                                                                         │
│ ↓                                                                       │
│                                                                         │
│ ┌─────────────────────────────────────────────────────────────────┐     │
│ │ USER INTERFACE                                                  │     │
│ │ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐     │     │
│ │ │ Dashboard│ │ Folder │ │  AI Studio │ │ Settings         │     │     │
│ │ └──────────┘ └──────────┘ └──────────┘ └──────────────────┘     │     │
│ └─────────────────────────────────────────────────────────────────┘     │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
'''

## Module Breakdown

| Module             | File                 | Description |
|--------            |------                |-------------|
| **Frontend**       | `frontend/egip.html` | Single-page application with HTML/CSS/JS, Bootstrap 5 UI |
| **Backend API**    | `frontend/app.py`    | Flask server handling API requests (port 5001) |
| **Database Logic** | `database_logic.py`  | SQLite operations, document ingestion, audit logging |
| **AI Integration** | `database_logic.py`  | Ollama API calls for local LLM inference |
| **Data Storage**   | `data/` folder       | Raw .txt documents organized by category |
| **Authentication** | `database_logic.py`  | Role-based access control (Admin, Compliance, Procurement) |

## Technology Stack

| Component  | Technology |
|----------- |------------|
| Frontend   | HTML5, CSS3, Bootstrap 5, Vanilla JavaScript, Chart.js 
| Backend    | Flask (Python) 
| Database   | SQLite 
| AI Model   | Ollama (Gemma2) 
| Deployment | Local / On-premise 

---

## Setup & Installation

## Prerequisites

- **Python 3.14** installed
- **Ollama** installed ([Download here](https://ollama.com/download))
- **Git** (optional, for version control)

## Installation Steps

1. Clone or Download the Project

powershell
git clone https://github.com/HowardHoJiaHao/GovernanceIntelligencePlatform EGIP
cd EGIP

2. Install Python Dependencies
powershell

pip uv install flask flask-cors

3. Install and Start Ollama
powershell
## Download and install Ollama from https://ollama.com/download

## Pull an AI model 
ollama pull gemma2:2b     # Good balance (2GB)

4. Prepare Your Documents
Place your governance documents in the data/ folder with this structure:
'''
data/
├── documents/
│   ├── corporate_governance_framework.txt
│   └── corporate_strategy_and_esg.txt
├── compliance/
│   ├── risk_and_compliance_framework.txt
│   └── data_privacy_and_security_policy.txt
└── procurement/
│   ├── supplier_management_framework.txt
│   ├── project_a_requirements.txt
│   ├── supplier_alpha_proposal.txt
│   └── supplier_beta_proposal.txt
'''

5. Start Ollama Service
powershell
### Terminal 1 - Keep this running
ollama serve

6. Run the EGIP Backend
powershell
### Terminal 2
uv run main.py

7. Access the Application
Open your browser and go to:
http://127.0.0.1:5001/

FEATURES
1. Executive Dashboard
KPI Cards - Real-time counts for Governance, Compliance, and Procurement documents
Risk Distribution - Visual breakdown of Low/Medium/High/Critical risks
AI Query Trends - Chart showing AI usage over time

2. Document Management (Folder Page)
Upload Documents - Drag-and-drop upload for .txt, .pdf, .docx files
Categorized Repository - Governance, Compliance, and Procurement tabs
Department Views - Legal, Finance, HR, IT, Operations, Marketing document libraries

3. AI Intelligence Workspace
Natural Language Query - Ask questions about policies, approvals, suppliers
Source Retrieval - Shows which documents were used to generate answers
Confidence Scoring - AI provides confidence percentage for each answer
Suggested Questions - Pre-built queries to demonstrate capabilities

4. Audit & Compliance
Complete Audit Trail - Logs all user queries and actions
Risk Matrix - Tracks risks by category and severity
Compliance Score - Real-time compliance percentage
User Action Tracking - Timestamped record of all activities

5. Administration
User Management - Add/delete users with role assignment
Role-Based Access Control (RBAC)
	-Admin - Full access to all modules
	-Compliance Officer - Risk and audit modules
	-Procurement Lead - Supplier and procurement access
System Configuration - View AI model and database status

6. Real-time Metrics
Document counts update dynamically from database
Dashboard numbers reflect actual uploaded files
Upload increases document count immediately

TECHNICAL DECISIONS
1. Architecture Choices
 - Monolithic Flask architecture
	- Chosen to keep backend logic, API, and integration in a single application, simplifying development and deployment.

 - Client–server structure (Frontend + Backend separation)
	- Bootstrap frontend communicates with Flask backend via HTTP requests, ensuring clear separation of UI and logic.

 - Local-first architecture (Ollama + SQLite)
	- All processing and storage are done locally to ensure data privacy, offline capability, and reduced dependency on external services.

 - Synchronous request–response flow
	- System uses direct request handling (no event-driven or async pipeline), making it easier to implement and debug.

 - Modular component integration
	- Components (LLM, database, search, UI) are logically separated within the same project structure to allow future scaling or migration.

2. Trade-offs Made
- Keyword search vs Semantic search
	- Acceptable because current documents are small and domain-specific; semantic/vector search can be added later for improved accuracy.

- SQLite vs production database (PostgreSQL/MySQL)
	- Suitable for local, single-user deployment; lightweight and easy to maintain, with clear upgrade path when scaling is needed.

- Simple file upload vs advanced document processing pipeline
	- Basic upload reduces complexity and speeds up development; advanced parsing (OCR, chunking, structured extraction) can be added later.

- Local LLM (Ollama) vs cloud-based API models
	- Chosen for privacy, cost savings, and offline usage; trade-off is slower inference and dependency on local hardware.

- Keyword-based retrieval vs advanced ranking/semantic retrieval
	- Sufficient for current dataset; more advanced retrieval methods can be integrated when data volume increases.

Security Considerations
-No external API calls - All AI processing happens locally
-On-premise deployment - Data never leaves organization
-Audit logging - Complete traceability of all queries

LIMITATIONS
1. Known Issues
	- Ollama response time slow on first query		    
	- Limited error handling for corrupted or incomplete inputs	        
	- Runs best only on devices with sufficient RAM/CPU (performance drops on low-spec machines)
	- No built-in authentication or role-based access control yet
	- Dependency on local environment setup (Ollama must be properly configured)
	
2. Future Improvements
 i)Semantic Search & Vector Database
	- Integrate ChromaDB for AI-powered semantic search.
	- Improve retrieval accuracy beyond keyword matching.
 ii) Enterprise System Integration
	-Connect with SAP, Oracle, and Microsoft SharePoint.
    -Enable real-time document synchronization.
 iii)Executive Report Generation
	-Generate governance and compliance reports automatically.
	-Provide AI-generated executive summaries for management.

>[!Troubleshooting]

'''powershell
### Check if Ollama is running
curl http://localhost:11434/api/tags

### Restart Ollama
taskkill /F /IM ollama.exe
ollama serve
Flask port already in use
bash

### Change port in app.py from 5001 to 5002
### Or kill process using port 5001
netstat -ano | findstr :5001
taskkill /PID <PID> /F
Documents not showing up
bash

### Re-index documents
curl -X POST http://localhost:5001/api/reindex

### Or restart the backend
uv run main.py
