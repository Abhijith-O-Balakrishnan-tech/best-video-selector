# Best Video Selector Bot

An asynchronous Telegram media analysis bot built with Python, Pyrogram, FFprobe, ReportLab, and Google Gemini.

## System Architecture
- **Layer 1**: Session Management (/select <lang> <device>)
- **Layer 2**: Async Header Streaming (3MB) & FFprobe Metadata Extraction
- **Layer 3**: ReportLab PDF Audit Matrix Generation
- **Layer 4**: Google Gemini API Evaluation (Structured JSON Schema)
- **Layer 5**: Pipeline Cleanup & Message Management

## Beginner Setup Guide

Follow these steps if you are running this project on a brand-new system:

### 1. Install System Prerequisites
- **Python 3.10+**: Download and install from python.org. Make sure to check 'Add Python to PATH'.
- **FFmpeg**: Install FFmpeg and add it to your system PATH.

### 2. Clone the Repository & Enter Folder
`ash
git clone [https://github.com/Abhijith-O-Balakrishnan-tech/best-video-selector.git](https://github.com/Abhijith-O-Balakrishnan-tech/best-video-selector.git)
cd best-video-selector
`powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
`powershell
pip install -r requirements.txt
1. Copy .env.example to .env:
   Copy-Item .env.example .env
2. Open .env and fill in API keys (API_ID, API_HASH, BOT_TOKEN, GEMINI_API_KEY).
3. Launch the bot:
   python main.py
