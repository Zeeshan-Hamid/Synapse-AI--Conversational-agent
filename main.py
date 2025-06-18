import os
import sys
import asyncio
import threading
import io
import sounddevice as sd
import tkinter as tk
from tkinter import messagebox

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent

# Import Groq client
from groq import Groq
# Import boto3 for Polly TTS
import boto3
# Import pygame for audio playback
import pygame

# Constants for transcription
SAMPLE_RATE = 16000
CHANNEL_NUMS = 1
CHUNK_SIZE = 1024 * 4
REGION = "us-east-1"

# Initialize Groq client using your API key (set as environment variable)
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    messagebox.showerror("Configuration Error", "The GROQ_API_KEY environment variable is not set.")
    sys.exit("Exiting: GROQ_API_KEY not set.")
client = Groq(api_key=api_key)

# Boto3 will automatically use credentials from environment variables
# (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION).
# We'll add a check to ensure they are set.
if not os.getenv("AWS_ACCESS_KEY_ID") or not os.getenv("AWS_SECRET_ACCESS_KEY"):
    messagebox.showerror("Configuration Error", "AWS credentials (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY) are not set as environment variables.")
    sys.exit("Exiting: AWS credentials not set.")

# Create Polly client
polly_client = boto3.client('polly')

# Initialize pygame mixer
pygame.mixer.init()

def play_audio_stream(mp3_bytes):
    """Play MP3 bytes directly without saving to disk, stopping any prior playback."""
    try:
        pygame.mixer.stop()
        sound = pygame.mixer.Sound(io.BytesIO(mp3_bytes))
        sound.play()
    except Exception as e:
        print(f"Playback error: {e}")

class MyEventHandler(TranscriptResultStreamHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_memory = []
        self.memory_limit = 20
        self.polly = polly_client

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        for result in results:
            if not result.is_partial:
                for alt in result.alternatives:
                    text = alt.transcript.strip()
                    print(f"Transcript: {text}")

                    # Update memory
                    self.user_memory.append(text)
                    if len(self.user_memory) > self.memory_limit:
                        self.user_memory.pop(0)

                    # Build messages with intelligent end-call handling
                    system_prompt = (
                        "You are a medical assistant. Provide brief and precise medical advice based on the conversation. "
                        "If the user asks to end the call or indicates they want to stop, respond exactly with the token END_SESSION and nothing else."
                    )

                    messages = [{"role": "system", "content": system_prompt}]
                    for past in self.user_memory:
                        messages.append({"role": "user", "content": past})
                    messages.append({"role": "user", "content": text})

                    # LLM call
                    response = client.chat.completions.create(
                        messages=messages,
                        model="llama-3.1-8b-instant"
                    )
                    llm_reply = response.choices[0].message.content.strip()
                    print(f"LLM Raw Response: {llm_reply}\n")

                    # Check for end-session token
                    if llm_reply == "END_SESSION":
                        pygame.mixer.stop()
                        messagebox.showinfo("Session Ended", "The call has been ended. Goodbye!")
                        # Graceful exit: close GUI and terminate process
                        try:
                            root.quit()
                            root.destroy()
                        except Exception:
                            pass
                        os._exit(0)

                    # Otherwise, perform TTS on the reply
                    polly_resp = self.polly.synthesize_speech(
                        Text=llm_reply,
                        OutputFormat='mp3',
                        VoiceId='Joanna'
                    )
                    audio_stream = polly_resp['AudioStream'].read()
                    play_audio_stream(audio_stream)

async def run_transcription():
    client_t = TranscribeStreamingClient(region=REGION)
    stream = await client_t.start_stream_transcription(
        language_code="en-US",
        media_sample_rate_hz=SAMPLE_RATE,
        media_encoding="pcm",
    )
    handler = MyEventHandler(stream.output_stream)
    async def write_chunks():
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()
        def callback(indata, frames, time, status):
            if status: print(status)
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SIZE,
            channels=CHANNEL_NUMS,
            dtype="int16",
            callback=callback
        ):
            while True:
                chunk = await queue.get()
                await stream.input_stream.send_audio_event(audio_chunk=chunk)
        await stream.input_stream.end_stream()

    await asyncio.gather(write_chunks(), handler.handle_events())

# GUI Application
root = tk.Tk()
root.title("Medical Transcriber & TTS")
root.geometry("300x150")

start_button = tk.Button(
    root,
    text="Start Transcribing",
    command=lambda: threading.Thread(
        target=lambda: asyncio.run(run_transcription()),
        daemon=True
    ).start()
)
start_button.pack(pady=10)

root.mainloop()
