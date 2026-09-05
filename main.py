import os
import json
import asyncio
import warnings
from typing import Dict, Any

warnings.filterwarnings("ignore", category=UserWarning, module="pyrogram")

from dotenv import load_dotenv
load_dotenv()

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import RPCError
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from google import genai
from google.genai import types
from pydantic import BaseModel
from rich.panel import Panel
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    DownloadColumn,
    TransferSpeedColumn,
)

console = Console()

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

app = Client(
    "media_evaluator_bot",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN"),
    ipv6=False,
)

# ==========================================
# Layer 1: Session Management
# ==========================================
class SessionState:
    def __init__(self):
        self.language = None
        self.device = None
        self.buffered_messages = []
        self.file_ids = set()

sessions: Dict[int, SessionState] = {}

@app.on_message(filters.command("select"))
async def handle_select(client: Client, message: Message):
    chat_id = message.chat.id
    args = message.command[1:]
    if len(args) < 2:
        await message.reply_text("Usage: /select <language> <device>")
        return
    
    state = SessionState()
    state.language = args[0].lower()
    state.device = " ".join(args[1:])
    sessions[chat_id] = state
    console.print(Panel(
        f"[bold cyan]User Chat ID:[/] {chat_id}\n"
        f"[bold cyan]Target Language:[/] [yellow]{state.language}[/yellow]\n"
        f"[bold cyan]Target Device:[/] [yellow]{state.device}[/yellow]",
        title="[bold green][LAYER 1] Session Initialized[/bold green]",
        expand=False
    ))
    await message.reply_text(f"Session started for {state.language} on {state.device}")

@app.on_message(filters.document | filters.video)
async def handle_media(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id not in sessions:
        return
    
    state = sessions[chat_id]
    media = message.document or message.video
    if not media:
        return

    if media.file_id in state.file_ids:
        console.print(f"[bold yellow][LAYER 1 REJECTED][/bold yellow] Duplicate dropped | Msg ID: {message.id}")
        return

    state.file_ids.add(media.file_id)
    state.buffered_messages.append(message)
    file_name = getattr(media, 'file_name', 'Unknown')
    file_size_mb = (media.file_size or 0) / (1024 * 1024)
    
    console.print(
        f"[bold green][LAYER 1 ACCEPTED][/bold green] Msg ID: [cyan]{message.id}[/cyan] | "
        f"File: [bold white]{file_name[:25]}[/bold white] | Size: [yellow]{file_size_mb:.2f} MB[/yellow] | "
        f"Queue Count: [magenta]{len(state.buffered_messages)}[/magenta]"
    )

# ==========================================
# Layer 2: Byte-Capped Async Fetcher & Metadata Extractor
# ==========================================
async def download_file_header_and_footer(
    client: Client, message: Message, progress: Progress, chunk_task_id
) -> bytes:
    chunks = []
    downloaded_bytes = 0
    target_bytes = 3 * 1024 * 1024  # 3MB Header target

    progress.reset(chunk_task_id, total=target_bytes, visible=True)
    
    try:
        async with asyncio.timeout(30):
            async for chunk in client.stream_media(message):
                chunks.append(chunk)
                chunk_len = len(chunk)
                downloaded_bytes += chunk_len
                progress.update(chunk_task_id, advance=chunk_len)
                
                if downloaded_bytes >= target_bytes:
                    break
    except Exception as e:
        console.print(f"[bold red][LAYER 2 ERROR][/bold red] Streaming error on Msg ID {message.id}: {e}")
    finally:
        progress.update(chunk_task_id, visible=False)
    
    return b"".join(chunks)

async def extract_detailed_metadata(
    client: Client, message: Message, progress: Progress, chunk_task_id
) -> dict:
    payload = await download_file_header_and_footer(client, message, progress, chunk_task_id)
    
    media_obj = message.document or message.video
    file_size_bytes = media_obj.file_size or 0
    
    # Extract duration directly from Telegram media API metadata
    duration_sec = getattr(media_obj, 'duration', 0) or 0
    overall_bitrate = int((file_size_bytes * 8) / (duration_sec * 1000)) if duration_sec > 0 else 0

    extracted = {
        "msg_id": message.id,
        "file_name": getattr(media_obj, 'file_name', f"file_{message.id}"),
        "file_size_mb": round(file_size_bytes / (1024 * 1024), 2),
        "overall_bitrate_kbps": overall_bitrate,
        "video_streams": [],
        "audio_streams": []
    }

    if not payload:
        return extracted

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-i", "pipe:0",
    ]
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=payload)
        data = json.loads(stdout.decode("utf-8"))
        
        for stream in data.get("streams", []):
            st_type = stream.get("codec_type")
            
            if st_type == "video":
                r_fps = stream.get("r_frame_rate", "0/0")
                fps_val = "Unknown"
                if "/" in r_fps:
                    num, den = r_fps.split("/")
                    if den != "0":
                        fps_val = str(round(float(num) / float(den), 2))

                extracted["video_streams"].append({
                    "codec": stream.get("codec_name", "Unknown").upper(),
                    "resolution": f"{stream.get('width', '?')}x{stream.get('height', '?')}",
                    "fps": fps_val,
                    "pix_fmt": stream.get("pix_fmt", "Unknown")
                })
                
            elif st_type == "audio":
                extracted["audio_streams"].append({
                    "codec": stream.get("codec_name", "Unknown").upper(),
                    "channels": stream.get("channels", "Unknown"),
                    "channel_layout": stream.get("channel_layout", "Unknown"),
                    "language": stream.get("tags", {}).get("language", "und").lower()
                })

    except Exception as e:
        console.print(f"[bold yellow][LAYER 2 WARN][/bold yellow] Partial parse on Msg {message.id}: {e}")

    return extracted

# ==========================================
# Layer 3: ReportLab PDF Generator
# ==========================================
def generate_pdf_report(candidates_data: list, output_path: str):
    doc = SimpleDocTemplate(output_path, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=18, leading=22, textColor=colors.HexColor("#1F4E79"))
    bold_label = ParagraphStyle('BoldLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, leading=14)
    normal_text = ParagraphStyle('NormalText', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14)
    
    story = [
        Paragraph("Candidate Media Releases Audit Report", title_style),
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1F4E79"), spaceAfter=15)
    ]
    
    for idx, item in enumerate(candidates_data, start=1):
        story.append(Paragraph(f"<b>File #{idx}: {item['file_name']}</b> (Message ID: {item['msg_id']})", bold_label))
        story.append(Spacer(1, 4))
        
        story.append(Paragraph(f"• <b>File Size:</b> {item['file_size_mb']} MB", normal_text))
        story.append(Paragraph(f"• <b>Estimated Bitrate:</b> {item['overall_bitrate_kbps']} kbps", normal_text))
        
        if item["video_streams"]:
            for v_idx, v in enumerate(item["video_streams"], start=1):
                story.append(Paragraph(f"• <b>Video Stream #{v_idx}:</b> Codec: {v['codec']} | Resolution: {v['resolution']} | FPS: {v['fps']} | Color: {v['pix_fmt']}", normal_text))
        else:
            story.append(Paragraph("• <b>Video Stream:</b> Not detected in header", normal_text))
            
        if item["audio_streams"]:
            for a_idx, a in enumerate(item["audio_streams"], start=1):
                story.append(Paragraph(f"• <b>Audio Stream #{a_idx}:</b> Codec: {a['codec']} | Channels: {a['channels']} ({a['channel_layout']}) | Language: {a['language']}", normal_text))
        else:
            story.append(Paragraph("• <b>Audio Stream:</b> Not detected in header", normal_text))
            
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey, spaceAfter=10))

    doc.build(story)
    console.print(f"[bold green][LAYER 3 PDF GENERATED][/bold green] Report saved to: [white]{output_path}[/white]")

# ==========================================
# Layer 4: Gemini AI Engine
# ==========================================
class CandidateEvaluation(BaseModel):
    winning_message_id: int
    selected_file_name: str
    merit_summary: str
    discard_reasons: list[str]

def evaluate_candidates(pdf_path: str, target_lang: str, target_device: str) -> CandidateEvaluation:
    console.print(f"[bold cyan][LAYER 4 GEMINI][/bold cyan] Uploading PDF to Gemini 3.6 Flash...")
    try:
        client = genai.Client()
        uploaded_file = client.files.upload(file=pdf_path)
        prompt = f"""
        You are an expert video quality evaluator. Analyze the attached itemized PDF report.
        
        Selection Rules:
        1. Language Filter: Match language '{target_lang}'. Convert target language to ISO-639 codes (e.g., 'malayalam' maps to 'mal', 'may'). Reject files without matching language. Return winning_message_id = -1 if none match.
        2. Playback Match: Pick the optimal file for device '{target_device}' using Video Codec, Resolution, FPS, Bitrate, and Audio Channels.
        
        Return response conforming strictly to the requested JSON schema.
        """
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CandidateEvaluation,
            ),
        )
        res_data = CandidateEvaluation.model_validate_json(response.text)
        
        console.print(Panel(
            f"[bold cyan]Winner Msg ID:[/] [gold1]{res_data.winning_message_id}[/gold1]\n"
            f"[bold cyan]Winner File:[/] {res_data.selected_file_name}\n"
            f"[bold cyan]Merit Summary:[/] {res_data.merit_summary}\n"
            f"[bold cyan]Discards:[/] {', '.join(res_data.discard_reasons)}",
            title="[bold green][LAYER 4 EVALUATION COMPLETE][/bold green]",
            expand=False
        ))
        return res_data
    except Exception as e:
        console.print(f"[bold red][LAYER 4 ERROR][/bold red] Gemini evaluation failed: {e}")
        return CandidateEvaluation(
            winning_message_id=-1,
            selected_file_name="None",
            merit_summary="API evaluation error occurred.",
            discard_reasons=[str(e)],
        )

# ==========================================
# Layer 5: Pipeline Execution
# ==========================================
@app.on_message(filters.command("end"))
async def handle_end(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id not in sessions:
        await message.reply_text("No active session found. Start with /select <lang> <device>")
        return

    state = sessions[chat_id]
    total_files = len(state.buffered_messages)

    if total_files == 0:
        await message.reply_text("No media files were received in this session!")
        del sessions[chat_id]
        return

    pdf_path = os.path.join(REPORTS_DIR, f"status_{chat_id}.pdf")

    console.print(
        Panel.fit(
            f"Processing [bold cyan]{total_files}[/bold cyan] candidate media file(s)\n"
            f"Language Target: [bold yellow]{state.language}[/bold yellow]\n"
            f"Device Target: [bold yellow]{state.device}[/bold yellow]",
            title="[bold green]Layer 5: Pipeline Triggered[/bold green]",
        )
    )

    candidates_metadata = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
    ) as progress:
        main_task = progress.add_task("[yellow]Parsing Files...", total=total_files)
        chunk_task = progress.add_task("[cyan]Header Stream...", total=3 * 1024 * 1024, visible=False)

        for msg in state.buffered_messages:
            media_obj = msg.document or msg.video
            filename = getattr(media_obj, 'file_name', f"file_{msg.id}")
            progress.update(main_task, description=f"[yellow]Parsing Msg {msg.id}:[/] {filename[:20]}...")
            
            try:
                meta = await extract_detailed_metadata(client, msg, progress, chunk_task)
                candidates_metadata.append(meta)
            except Exception as err:
                console.print(f"[bold red][LAYER 5 ERROR][/bold red] Failed parsing Msg ID {msg.id}: {err}")
            finally:
                progress.update(main_task, advance=1)

    # Layer 3 Execution
    generate_pdf_report(candidates_metadata, pdf_path)

    # Send report copy to Telegram chat
    try:
        await client.send_document(chat_id, document=pdf_path, caption="Audit Matrix Itemized Report")
        console.print(f"[bold green][LAYER 3 TELEGRAM][/bold green] Sent PDF copy to chat")
    except Exception as pdf_err:
        console.print(f"[bold yellow][LAYER 3 WARN][/bold yellow] Could not send PDF to Telegram: {pdf_err}")

    # Layer 4 Execution
    eval_result = evaluate_candidates(pdf_path, state.language, state.device)

    # Layer 5 Processing - Purge non-winners & Send explicit Winner Message
    try:
        if eval_result.winning_message_id == -1:
            await message.reply_text(f"No candidates contained matching language: {state.language}")
            console.print(f"[bold red][LAYER 5 RESULT][/bold red] No valid candidates found for language: {state.language}")
        else:
            for msg in state.buffered_messages:
                if msg.id == eval_result.winning_message_id:
                    # Send evaluation result as an explicit new reply message
                    summary_msg = (
                        f"🏆 **Winner Media Selected**\n\n"
                        f"**File:** `{eval_result.selected_file_name}`\n\n"
                        f"**Evaluation Summary:**\n{eval_result.merit_summary}"
                    )
                    await client.send_message(
                        chat_id=chat_id,
                        text=summary_msg,
                        reply_to_message_id=msg.id
                    )
                    console.print(f"[bold green][LAYER 5 NOTIFIED][/bold green] Sent separate winner summary message for Msg ID: [cyan]{msg.id}[/cyan]")
                else:
                    try:
                        await client.delete_messages(chat_id, msg.id)
                        console.print(f"[bold red][LAYER 5 PURGED][/bold red] Deleted non-winning Msg ID: {msg.id}")
                    except RPCError as e:
                        console.print(f"[bold yellow][LAYER 5 WARN][/bold yellow] Failed deleting Msg ID {msg.id}: {e}")

    finally:
        if chat_id in sessions:
            del sessions[chat_id]
            console.print(f"[dim text][CLEANUP][/dim text] Active session cleared for Chat ID: {chat_id}")

    console.print(
        Panel.fit(
            f"[bold green]Pipeline Execution Completed Successfully[/bold green]\nPDF report saved at: [white]{pdf_path}[/white]",
            title="[bold green]Success[/bold green]",
        )
    )

if __name__ == "__main__":
    console.print("[bold green]Starting Media Evaluator Bot...[/bold green]")
    app.run()