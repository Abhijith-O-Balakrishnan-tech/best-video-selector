# Best Video Selector Bot
An asynchronous Telegram media analysis bot built with Python, Pyrogram, FFprobe, ReportLab, and Google Gemini.
## System Architecture
- **Layer 1**: Session Management (/select <lang> <device>)
- **Layer 2**: Async Header Streaming (3MB) & FFprobe Metadata Extraction
- **Layer 3**: ReportLab PDF Audit Matrix Generation
- **Layer 4**: Google Gemini API Evaluation (Structured JSON Schema)
- **Layer 5**: Pipeline Cleanup & Message Management
## Setup
1. Copy .env.example to .env and fill in API credentials.
2. Install dependencies: pip install -r requirements.txt
3. Run python main.py
