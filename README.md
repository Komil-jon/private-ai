# 🚀 AI Chat Web Application

A minimal full-stack web application that enables users to interact with an AI model through a chat interface. The project demonstrates integration between a frontend UI and a backend API using FastAPI and a Large Language Model (LLM).

---

## 📌 Project Overview

This application allows users to:

* Enter messages in a chat interface
* Send them to a backend server
* Receive AI-generated responses in real time

The system is designed with simplicity and clarity in mind, focusing on:

* Clean architecture
* API communication
* Secure handling of API keys
* Basic safety controls

---

## 🧱 Project Structure

```
project-root/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── routes/
│   │   ├── services/
│   │   ├── models/
│   │   └── core/
│   ├── requirements.txt
│   └── .env
│
├── frontend/
│   ├── index.html
│   ├── script.js
│   ├── style.css
│   └── images/
│
├── docs/
│   └── project_documentation.pdf
│
├── README.md
└── .gitignore
```

---

## ⚙️ Requirements

Make sure you have the following installed:

* Python **3.10 or higher** (recommended)
* `pip` (Python package manager)
* A modern web browser (Chrome, Edge, Safari, etc.)

---

## 📦 Installation Guide

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd <project-folder>
```

---

### 2. Set Up Virtual Environment

Navigate to the backend folder:

```bash
cd backend
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it:

* On macOS/Linux:

```bash
source .venv/bin/activate
```

* On Windows:

```bash
.venv\Scripts\activate
```

---

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

### 4. Configure Environment Variables

Create a `.env` file inside the `backend/` folder:

```env
API_KEY=your_api_key_here
```

⚠️ **Important:**

* Never commit your `.env` file to GitHub
* Keep your API key secure

---

## ▶️ Running the Application Locally

### Step 1: Start the Backend Server

From the `backend/` directory:

```bash
uvicorn app.main:app --reload
```

You should see:

```
Uvicorn running on http://127.0.0.1:8000
```

---

### Step 2: Open the Application

Open your browser and go to:

```
http://127.0.0.1:8000
```

👉 The frontend will be served automatically by FastAPI.

---

## 🔄 How It Works

1. User enters a message in the frontend
2. Frontend sends a POST request to `/process`
3. Backend:

   * Receives conversation history
   * Applies safety checks
   * Sends request to LLM API
4. Response is returned and displayed in the chat UI

---

## 🔐 Security Considerations

* API keys are stored using environment variables (`.env`)
* No sensitive data is exposed in the frontend
* Basic filtering is applied to unsafe or inappropriate inputs

---

## ⚠️ Common Issues & Fixes

### ❌ API Key Not Working

* Ensure `.env` file exists in `backend/`
* Restart server after adding the key

---

### ❌ Module Import Errors

* Make sure you are inside `backend/` when running:

```bash
uvicorn app.main:app --reload
```

---

### ❌ Rate Limit / Quota Errors

* Reduce request frequency
* Limit conversation size in backend
* Try a different API key or model

---

## 💡 Notes

* This project uses a simple HTML/JS frontend (no frameworks)
* Backend is built with FastAPI for simplicity and performance
* Designed for learning purposes and demonstration

---

## 🧠 Learning Outcomes

This project demonstrates:

* Frontend ↔ Backend communication
* API integration with LLMs
* Basic system design principles
* Error handling and debugging
* Secure configuration management

---

## 📬 Final Remarks

This implementation prioritizes clarity, structure, and maintainability.
While minimal, it reflects real-world practices such as modular design, environment configuration, and API-based architecture.

---

**Author:** Komiljon
**Purpose:** Educational / Assessment Project
