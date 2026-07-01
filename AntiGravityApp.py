import os
import sys
import re
import json
import time
import socket
import random
import threading
import ctypes
import ctypes.wintypes
import requests
import base64
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from PIL import ImageGrab

# Constants for Windows API
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

# Configure ctypes function signatures to prevent 64-bit integer truncation/overflow errors
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Force DPI awareness so ImageGrab and SetCursorPos both use logical coordinates.
# Without this, on screens with >100% DPI scaling, screenshot pixels don't match
# the coordinate space that SetCursorPos expects, causing cursor to land in wrong spots.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except:
    try:
        user32.SetProcessDPIAware()  # Fallback for older Windows
    except:
        pass


user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.keybd_event.argtypes = [ctypes.wintypes.BYTE, ctypes.wintypes.BYTE, ctypes.wintypes.DWORD, ctypes.c_void_p]
user32.keybd_event.restype = None

user32.PostThreadMessageW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL

user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.UINT, ctypes.wintypes.UINT]
user32.RegisterHotKey.restype = ctypes.wintypes.BOOL

user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.UnregisterHotKey.restype = ctypes.wintypes.BOOL

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.wintypes.BOOL

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = ctypes.wintypes.BOOL

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long)
    ]

class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.wintypes.DWORD)
    ]

MONITOR_DEFAULTTONEAREST = 0x00000002

user32.MonitorFromPoint.argtypes = [POINT, ctypes.wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p

user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL


# Load pystray for system tray integration
try:
    import pystray
    from PIL import Image, ImageDraw
    PRAY_AVAILABLE = True
except ImportError:
    PRAY_AVAILABLE = False

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_PROMPT = """Analyze the provided screenshot carefully. 
Your task is to detect the type of question and return a structured JSON response.

**IMPORTANT TYPE DETECTION RULES:**
1. If the image shows a **programming/coding problem** (requires writing a function, algorithm, or script):
   - The "code" field must contain the COMPLETE, working, and compilable source code.
   - Include all necessary imports, headers, and boilerplate.
   - Preserve exact indentation and line breaks.
   - STRIP all comments (no "//", "#", or "/* */" inside the code).
   - Do NOT wrap the code in markdown fences (```).

2. If the image shows a **multiple-choice question** (MCQ), Objective Aptitude, or Technical/Programming subjective question:
   - Analyze the question carefully. Identify if it is Quantitative Aptitude, Logical Reasoning, or Technical (e.g., code tracing, core CS concepts).
   - For Aptitude/Math: Perform precise step-by-step calculations. Double-check your logic.
   - For Technical/Code snippets: Dry-run the code line-by-line, watching for edge cases, variable scopes, and language-specific behaviors.
   - Determine the definitive correct answer from the provided options.
   - The "code" field must contain the EXACT answer in this format: "Answer: [LETTER] - [Option Text] | Reasoning: [Concise, step-by-step logical or technical explanation]".
   - The "box_2d" field MUST contain the 2D bounding box coordinates of the correct option's exact clickable area (e.g., the radio button, checkbox, or option letter) in a 0-1000 normalized scale: [ymin, xmin, ymax, xmax].
   - The "question" field must contain the full text of the question.

**OUTPUT SCHEMA (strictly follow this):**
{
  "question": "The full title, prompt, or text of the problem or MCQ",
  "code": "The actual code (for coding) OR the answer/reasoning (for MCQ)",
  "box_2d": "[ymin, xmin, ymax, xmax] - The normalized bounding box of the correct option's clickable area (for MCQ only, otherwise empty)"
}

**CRITICAL CONSTRAINTS:**
- The JSON must be valid and parseable.
- Do not include any text outside the JSON object.
- Ensure the code (if coding) is syntactically correct and ready to compile/run.
"""

# Global Variables
hotkey_thread_id = None
hotkey_thread = None
hotkey_registered = False
is_solving = False
stop_typing = False
tray_icon = None
fix_screenshot_1_path = None

# Thread synchronization variables for pausing
is_paused = False
pause_cond = threading.Condition()

# ----------------- Stealth Mouse Movement (Bezier Curve) -----------------
def stealth_mouse_move(target_x, target_y):
    """
    Moves the mouse to (target_x, target_y) using a randomized Bezier curve
    to mimic human movement and avoid proctoring detection.
    """
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    start_x, start_y = pt.x, pt.y

    # Calculate distance
    distance = ((target_x - start_x)**2 + (target_y - start_y)**2)**0.5
    if distance < 5:
        user32.SetCursorPos(target_x, target_y)
        return

    # Randomize control points for the Bezier curve to create an arc
    # Control point 1
    cp1_x = start_x + (target_x - start_x) * random.uniform(0.1, 0.4) + random.uniform(-distance*0.2, distance*0.2)
    cp1_y = start_y + (target_y - start_y) * random.uniform(0.1, 0.4) + random.uniform(-distance*0.2, distance*0.2)
    
    # Control point 2
    cp2_x = start_x + (target_x - start_x) * random.uniform(0.6, 0.9) + random.uniform(-distance*0.2, distance*0.2)
    cp2_y = start_y + (target_y - start_y) * random.uniform(0.6, 0.9) + random.uniform(-distance*0.2, distance*0.2)

    # Dynamic duration based on distance (closer = faster, further = slower)
    # Humans usually take between 300ms to 800ms to move a mouse
    duration = random.uniform(0.3, 0.8) 
    steps = int(duration * 120)  # Assume 120hz update rate
    
    if steps < 10: steps = 10

    # Execute the curve
    for i in range(steps + 1):
        t = i / steps
        # Cubic Bezier formula
        x = (1 - t)**3 * start_x + 3 * (1 - t)**2 * t * cp1_x + 3 * (1 - t) * t**2 * cp2_x + t**3 * target_x
        y = (1 - t)**3 * start_y + 3 * (1 - t)**2 * t * cp1_y + 3 * (1 - t) * t**2 * cp2_y + t**3 * target_y
        
        user32.SetCursorPos(int(x), int(y))
        time.sleep(duration / steps)
        
    # Final micro-jitter (human overshoot correction)
    if random.random() > 0.5:
        overshoot_x = target_x + random.randint(-3, 3)
        overshoot_y = target_y + random.randint(-3, 3)
        user32.SetCursorPos(overshoot_x, overshoot_y)
        time.sleep(random.uniform(0.05, 0.15))
        
    # Snap to exact target
    user32.SetCursorPos(target_x, target_y)


# ----------------- Keyboard Simulation using Windows API (ctypes) -----------------
def type_character(char):
    user32 = ctypes.windll.user32
    if char == '\n':
        # Send VK_RETURN (Enter key)
        user32.keybd_event(0x0D, 0, 0, 0)
        user32.keybd_event(0x0D, 0, KEYEVENTF_KEYUP, 0)
    else:
        # Send Unicode character
        code = ord(char)
        user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
        user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)

def clear_line_to_start():
    # Helper to select all characters from cursor to start of line and delete them.
    # This cancels any auto-indentation inserted by code editors when pressing Enter.
    user32 = ctypes.windll.user32
    VK_SHIFT = 0x10
    VK_HOME = 0x24
    VK_DELETE = 0x2E
    
    # Press Shift + Home to highlight to start of line
    user32.keybd_event(VK_SHIFT, 0, 0, 0)
    time.sleep(0.005)
    user32.keybd_event(VK_HOME, 0, 0, 0)
    time.sleep(0.005)
    
    # Release Home and Shift
    user32.keybd_event(VK_HOME, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.005)
    user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.005)
    
    # Press Delete to delete the highlighted spaces
    user32.keybd_event(VK_DELETE, 0, 0, 0)
    time.sleep(0.005)
    user32.keybd_event(VK_DELETE, 0, KEYEVENTF_KEYUP, 0)

def check_panic_pressed():
    # Virtual key code for F4 is 0x73
    user32 = ctypes.windll.user32
    return bool(user32.GetAsyncKeyState(0x73) & 0x8000)

def check_pause_and_panic():
    global is_paused, stop_typing
    while is_paused:
        if stop_typing or check_panic_pressed():
            stop_typing = True
            with pause_cond:
                is_paused = False
                pause_cond.notify_all()
            return True
        with pause_cond:
            pause_cond.wait(0.05)
    return stop_typing or check_panic_pressed()

def toggle_pause():
    global is_paused
    if not is_solving:
        log_message("Not typing right now. Ignore pause hotkey.")
        return
    with pause_cond:
        is_paused = not is_paused
        if is_paused:
            log_message("Typing PAUSED. Press pause hotkey again to resume.")
        else:
            log_message("Typing RESUMED.")
        pause_cond.notify_all()

def type_code(code, min_delay, max_delay):
    global stop_typing, is_paused
    stop_typing = False
    is_paused = False
    
    lines = code.splitlines()
    for line_idx, line in enumerate(lines):
        if check_pause_and_panic():
            log_message("Typing aborted by user.")
            return
            
        # Type characters in the current line
        for char in line:
            if check_pause_and_panic():
                log_message("Typing aborted by user.")
                return
                
            type_character(char)
            # Human-like typing delay per character
            delay = min_delay + (max_delay - min_delay) * random.random()
            
            # Wait for delay, checking pause/abort frequently
            start_time = time.time()
            while time.time() - start_time < delay:
                if check_pause_and_panic():
                    log_message("Typing aborted by user.")
                    return
                time.sleep(0.01)
            
        # End of line newline processing
        if line_idx < len(lines) - 1:
            if check_pause_and_panic():
                log_message("Typing aborted by user.")
                return
                
            type_character('\n')
            
            # Wait briefly for the editor to register enter and perform auto-indentation (120ms)
            start_time = time.time()
            while time.time() - start_time < 0.12:
                if check_pause_and_panic():
                    log_message("Typing aborted by user.")
                    return
                time.sleep(0.01)
            
            # Wipe out editor auto-indentation so we can type the exact original indentation
            clear_line_to_start()
            
            # Wait briefly after deleting (100ms)
            start_time = time.time()
            while time.time() - start_time < 0.10:
                if check_pause_and_panic():
                    log_message("Typing aborted by user.")
                    return
                time.sleep(0.01)
            
    log_message("Typing completed successfully!")

# ----------------- Configuration & Storage -----------------
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
                # Upgrade prompt if it contains old imports requirement
                old_prompt_marker = "including all necessary imports/headers"
                if "prompt" in config and old_prompt_marker in config["prompt"]:
                    config["prompt"] = DEFAULT_PROMPT
                    save_config(config)
                return config
        except:
            pass
    return {
        "api_key": "",
        "model": "gemini-2.5-flash",
        "min_delay_ms": 50,
        "max_delay_ms": 150,
        "hotkey": "MEDIASTOP",
        "pause_hotkey": "MEDIAPREV",
        "prompt": DEFAULT_PROMPT
    }

def save_config(config):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print("Failed to save config:", e)

# ----------------- Screen Capture & API Integration -----------------
def capture_screen_to_file():
    img = ImageGrab.grab()
    temp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_screenshot.png")
    img.save(temp_path, format="PNG")
    return temp_path

def call_gemini(api_key, model, prompt, image_path):
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    },
                    {
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": image_base64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "question": {
                        "type": "STRING",
                        "description": "Brief description or title of the coding question"
                    },
                    "code": {
                        "type": "STRING",
                        "description": "Complete source code solution with standard indentation, formatting, and line breaks"
                    }
                },
                "required": ["question", "code"]
            }
        }
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Gemini API error (Status {response.status_code}): {response.text}")
        
    res_json = response.json()
    try:
        text_response = res_json['candidates'][0]['content']['parts'][0]['text']
        parsed = json.loads(text_response)
        return parsed.get('question', 'Coding Question'), parsed.get('code', '')
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        # Fallback parsing in case response JSON is slightly off
        try:
            raw_text = res_json['candidates'][0]['content']['parts'][0]['text']
            return "Extracted Question", raw_text
        except:
            raise Exception(f"Failed to parse response: {e}. Raw response: {response.text}")

def call_groq(api_key, model, prompt, image_path):
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    
    groq_model = model
    if not (model.startswith("meta-llama") or model.startswith("qwen") or model.startswith("mixtral") or model.startswith("llama")):
        groq_model = "meta-llama/llama-4-scout-17b-16e-instruct"
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": groq_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            }
        ],
        "response_format": {
            "type": "json_object"
        },
        "temperature": 0.1
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Groq API error (Status {response.status_code}): {response.text}")
        
    res_json = response.json()
    try:
        text_response = res_json['choices'][0]['message']['content']
        parsed = json.loads(text_response)
        return parsed.get('question', 'Coding Question'), parsed.get('code', '')
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        try:
            raw_text = res_json['choices'][0]['message']['content']
            return "Extracted Question", raw_text
        except:
            raise Exception(f"Failed to parse response: {e}. Raw response: {response.text}")

def call_gemini_vision(api_key, model, prompt, image_path, response_schema=None):
    """Generic Gemini vision call. Optionally enforces a responseSchema."""
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    generation_config = {}
    if response_schema:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = response_schema
        
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    },
                    {
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": image_base64
                        }
                    }
                ]
            }
        ]
    }
    if generation_config:
        payload["generationConfig"] = generation_config
    
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    if response.status_code != 200:
        raise Exception(f"Gemini API error (Status {response.status_code}): {response.text}")
        
    res_json = response.json()
    try:
        text_response = res_json['candidates'][0]['content']['parts'][0]['text']
        # If schema is not enforced, extract JSON from markdown/text if necessary
        text_response = text_response.strip()
        if not response_schema:
            # Look for JSON block in markdown
            match = re.search(r'\{[\s\S]*\}', text_response)
            if match:
                return json.loads(match.group(0))
        return json.loads(text_response)
    except Exception as e:
        raise Exception(f"Failed to parse response: {e}. Raw response: {response.text}")

# Keep backward-compatible wrapper
def call_gemini_mcq(api_key, model, prompt, image_path):
    schema = {
        "type": "OBJECT",
        "properties": {
            "question": {"type": "STRING"},
            "answer_option": {"type": "STRING"},
            "reasoning": {"type": "STRING"},
            "center_x_permille": {"type": "INTEGER"},
            "center_y_permille": {"type": "INTEGER"}
        },
        "required": ["question", "answer_option", "reasoning", "center_x_permille", "center_y_permille"]
    }
    return call_gemini_vision(api_key, model, prompt, image_path, schema)

def call_groq_vision(api_key, model, prompt, image_path):
    """Generic Groq vision call that returns parsed JSON."""
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    
    groq_model = model
    if not (model.startswith("meta-llama") or model.startswith("qwen") or model.startswith("mixtral") or model.startswith("llama")):
        groq_model = "meta-llama/llama-4-scout-17b-16e-instruct"
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": groq_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            }
        ],
        "response_format": {
            "type": "json_object"
        },
        "temperature": 0.1
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    if response.status_code != 200:
        raise Exception(f"Groq API error (Status {response.status_code}): {response.text}")
        
    res_json = response.json()
    try:
        text_response = res_json['choices'][0]['message']['content']
        return json.loads(text_response)
    except Exception as e:
        raise Exception(f"Failed to parse response: {e}. Raw response: {response.text}")

# Backward-compatible wrapper
def call_groq_mcq(api_key, model, prompt, image_path):
    return call_groq_vision(api_key, model, prompt, image_path)

def remove_comments(code):
    if not code:
        return ""
        
    cleaned = code
    
    # 1. Remove multi-line comments: /* ... */
    cleaned = re.sub(r'/\*[\s\S]*?\*/', '', cleaned)
    
    # 2. Remove single-line comments (Java/C++): // ... (using fixed-width lookbehinds to prevent Python re.error)
    cleaned = re.sub(r'(?<!http:)//.*', '', cleaned)
    cleaned = re.sub(r'(?<!https:)//.*', '', cleaned)
    
    # 3. Remove Python single-line comments: # ... (avoiding C++ preprocessor directives)
    cleaned = re.sub(r'(?<!\w)#(?!(?:include|define|pragma|if|else|elif|endif|ifdef|ifndef|line|error|region|endregion|import)\b).*', '', cleaned)
    
    # Clean up empty lines that might be left over from removals
    lines = cleaned.splitlines()
    non_empty_lines = [line for line in lines if line.strip()]
    
    return "\n".join(non_empty_lines).strip()

def clean_code_block(code_str):
    code_str = code_str.strip()
    if code_str.startswith("```"):
        lines = code_str.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code_str = "\n".join(lines).strip()
    return code_str

def clean_and_format_code(code):
    code = clean_code_block(code)
    code = remove_comments(code)
    return code.strip()

# ----------------- Solver Execution Thread -----------------
def solve_process():
    global is_solving, is_paused, stop_typing, fix_screenshot_1_path
    fix_screenshot_1_path = None
    if is_solving:
        log_message("Interrupting current solve to start a new one...")
        stop_typing = True
        with pause_cond:
            is_paused = False
            pause_cond.notify_all()
            
        # Wait for previous thread to exit
        start_wait = time.time()
        while is_solving and (time.time() - start_wait < 2.0):
            time.sleep(0.05)
            
        if is_solving:
            log_message("ERROR: Previous solve thread failed to stop. Cannot start new solve.")
            return
        
    is_solving = True
    is_paused = False
    stop_typing = False
    try:
        config = load_config()
                # Retrieve API keys list for fallback
        api_keys = []
        if isinstance(config.get("api_keys"), list):
            api_keys = [k.strip() for k in config.get("api_keys") if k.strip()]
        else:
            # Fall back to single api_key string (legacy)
            single_key = config.get("api_key", "").strip()
            if single_key:
                api_keys = [single_key]
        if not api_keys:
            log_message("ERROR: No API Keys configured. Open settings to configure them.")
            is_solving = False
            return
        # Retrieve model and prompt
        model = config.get("model", "gemini-2.5-flash")
        prompt = config.get("prompt", DEFAULT_PROMPT)
        
        log_message("Capturing screen...")
        screenshot_path = capture_screen_to_file()
        
        try:
            # Try each API key until a request succeeds
            question = None
            code = None
            for key in api_keys:
                try:
                    if key.startswith("gsk_"):
                        log_message(f"Querying Groq Vision API ({model})...")
                        question, code = call_groq(key, model, prompt, screenshot_path)
                    else:
                        log_message(f"Querying Gemini Vision API ({model})...")
                        question, code = call_gemini(key, model, prompt, screenshot_path)
                    break  # Success
                except Exception as e:
                    log_message(f"API KEY FAILED ({key[:4]}...): {e}")
                    continue
            if question is None:
                log_message("All API keys failed. Aborting solve.")
                is_solving = False
                return
        finally:
            # Clean up temp screenshot file
            try:
                os.remove(screenshot_path)
            except:
                pass

            
        log_message(f"Successfully Solved: {question}")
        
        # Clean and format the code (keeping proper indentation)
        code = clean_and_format_code(code)
        
        # Update preview in Tkinter GUI
        root.after(0, lambda: update_preview_ui(code))
        
        # Load delays
        min_delay = config.get("min_delay_ms", 50) / 1000.0
        max_delay = config.get("max_delay_ms", 150) / 1000.0
        
        log_message("Simulated typing starts in 3 seconds. Focus your editor window!")
        
        start_time = time.time()
        while time.time() - start_time < 3.0:
            if check_pause_and_panic():
                log_message("Typing aborted by user before start.")
                return
            time.sleep(0.1)
            
        log_message("Typing... (Press pause hotkey to pause/resume, F7 to panic abort)")
        type_code(code, min_delay, max_delay)
        
    except Exception as e:
        log_message(f"Unexpected Solver Error: {e}")
    finally:
        is_solving = False
        is_paused = False

def draw_grid_on_image(img, cols=30, rows=30):
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)
    w, h = img.size
    
    col_w = w / float(cols)
    row_h = h / float(rows)
    
    try:
        font = ImageFont.truetype("arial.ttf", 10)
    except:
        font = ImageFont.load_default()
        
    # Draw vertical grid lines and labels
    for col in range(cols + 1):
        x = int(col * col_w)
        draw.line([(x, 0), (x, h)], fill=(180, 180, 180), width=1)
        label = str(col)
        draw.text((x + 2, 2), label, fill=(255, 0, 0), font=font)
        draw.text((x + 2, h - 15), label, fill=(255, 0, 0), font=font)
        
    # Draw horizontal grid lines and labels
    for row in range(rows + 1):
        y = int(row * row_h)
        draw.line([(0, y), (w, y)], fill=(180, 180, 180), width=1)
        label = str(row)
        draw.text((2, y + 2), label, fill=(255, 0, 0), font=font)
        draw.text((w - 18, y + 2), label, fill=(255, 0, 0), font=font)
        
    return img

def solve_mcq_process():
    global is_solving
    if is_solving:
        log_message("Solver is currently busy.")
        return

    is_solving = True
    try:
        config = load_config()
        api_keys = []
        if isinstance(config.get("api_keys"), list):
            api_keys = [k.strip() for k in config.get("api_keys") if k.strip()]
        else:
            single_key = config.get("api_key", "").strip()
            if single_key:
                api_keys = [single_key]
        if not api_keys:
            log_message("ERROR: No API Keys configured.")
            is_solving = False
            return

        model = config.get("model", "gemini-2.5-flash")

        # ── Step 1: Get current monitor bounds ──────────────────────────────
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hMonitor = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi))

        mon_left   = mi.rcMonitor.left
        mon_top    = mi.rcMonitor.top
        mon_right  = mi.rcMonitor.right
        mon_bottom = mi.rcMonitor.bottom

        log_message(f"MCQ: Capturing monitor ({mon_left},{mon_top})-({mon_right},{mon_bottom})...")

        # ── Step 2: Capture screenshot ──────────────────────────────────────
        img = ImageGrab.grab(bbox=(mon_left, mon_top, mon_right, mon_bottom), all_screens=True)
        img_w, img_h = img.size

        # Draw a small red dot at cursor position for spatial context
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        
        logical_w = max(1, mon_right - mon_left)
        logical_h = max(1, mon_bottom - mon_top)
        scale_x = img_w / logical_w
        scale_y = img_h / logical_h
        
        log_message(f"MCQ Display Info: Physical={img_w}x{img_h}, Logical={logical_w}x{logical_h}, Scale={scale_x:.2f}x{scale_y:.2f}")
        
        rel_cx = max(0, min(img_w - 1, int((pt.x - mon_left) * scale_x)))
        rel_cy = max(0, min(img_h - 1, int((pt.y - mon_top) * scale_y)))
        r = 12
        draw.ellipse((rel_cx - r, rel_cy - r, rel_cx + r, rel_cy + r), fill="red", outline="white", width=2)

        # Draw 60x60 grid overlay for visual coordinate assistance
        img = draw_grid_on_image(img, cols=60, rows=60)

        temp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_mcq_single.png")
        img.save(temp_path, format="PNG")

        # ── Step 3: Single API call for both answer + coordinates ──────────
        mcq_prompt = (
            "You are an expert MCQ solver. The screenshot is overlayed with a 60x60 grid.\n"
            "Column lines are labeled 0 to 60 at the top and bottom.\n"
            "Row lines are labeled 0 to 60 at the left and right.\n"
            "A red dot marks the area the user is focusing on.\n\n"
            "Your task:\n"
            "1. Find the MCQ question nearest to the red dot.\n"
            "2. Read ALL available options carefully.\n"
            "3. Reason step-by-step to determine the SINGLE correct answer. Verify your reasoning: double-check calculations, trace code logic line-by-line, and rule out all wrong options. Do not rush.\n"
            "4. Locate the exact checkbox/radio button (or starting letter) for that correct option on the grid.\n"
            "5. Determine the grid cell of that clickable area. A cell is defined by the column index on its left and the row index on its top (0 to 59).\n"
            "   For example: if the checkbox is between column lines 24 and 25, and row lines 40 and 41, then target_column = 24 and target_row = 40.\n\n"
            "Return JSON:\n"
            "{\n"
            "  \"question_text\": \"full question text\",\n"
            "  \"options_text\": \"List of all available options\",\n"
            "  \"reasoning\": \"Meticulous step-by-step verification. First, write down the code logic or math calculations. Second, prove why the correct answer is correct and why other options are wrong. Third, perform a grid-tracing check: explicitly write down the column lines (left to right) and row lines (top to bottom) that bound the checkbox/radio button of the correct option to determine target_column and target_row.\",\n"
            "  \"correct_option\": \"The correct option letter (e.g. A, B, C, D, E)\",\n"
            "  \"target_column\": <integer 0-59>,\n"
            "  \"target_row\": <integer 0-59>\n"
            "}"
        )
        mcq_schema = {
            "type": "OBJECT",
            "properties": {
                "question_text": {"type": "STRING"},
                "options_text": {"type": "STRING"},
                "reasoning": {"type": "STRING"},
                "correct_option": {"type": "STRING"},
                "target_column": {"type": "INTEGER"},
                "target_row": {"type": "INTEGER"}
            },
            "required": ["question_text", "options_text", "reasoning", "correct_option", "target_column", "target_row"]
        }

        log_message("MCQ: Solving and locating correct option in one pass...")
        result = None
        for key in api_keys:
            try:
                if key.startswith("gsk_"):
                    result = call_groq_vision(key, model, mcq_prompt, temp_path)
                else:
                    # Do not pass schema to allow the model's brain to think before generating JSON
                    result = call_gemini_vision(key, model, mcq_prompt, temp_path)
                break
            except Exception as e:
                log_message(f"MCQ key failed ({key[:4]}...): {e}")
                continue

        # Cleanup temp file
        try: os.remove(temp_path)
        except: pass

        if not result:
            log_message("All API keys failed. Aborting.")
            is_solving = False
            return

        # ── Step 4: Parse result ────────────────────────────────────────────
        correct_letter = result.get("correct_option", "").strip().upper()
        reasoning = result.get("reasoning", "")
        question_text = result.get("question_text", "")
        
        target_col = result.get("target_column", -1)
        target_row = result.get("target_row", -1)
        
        if 0 <= target_col < 60 and 0 <= target_row < 60:
            logical_w = mon_right - mon_left
            logical_h = mon_bottom - mon_top
            
            # Click exactly in the center of the cell
            target_x = int(((target_col + 0.5) / 60.0) * logical_w)
            target_y = int(((target_row + 0.5) / 60.0) * logical_h)
        else:
            target_x, target_y = -1, -1

        log_message(f"MCQ Result: Correct = {correct_letter}")
        log_message(f"  Q: {question_text[:80]}...")
        log_message(f"  Reasoning: {reasoning[:100]}...")
        log_message(f"  AI Grid Target: Col={target_col}, Row={target_row}")

        # ── Step 5: Validate and map coordinates to screen ────────────────
        logical_w = mon_right - mon_left
        logical_h = mon_bottom - mon_top
        if target_x < 0 or target_y < 0 or target_x >= logical_w or target_y >= logical_h:
            log_message(f"ERROR: AI returned invalid coordinates ({target_x}, {target_y}). Aborting.")
            is_solving = False
            return

        # Convert image-local coords to absolute screen coords
        screen_x = mon_left + target_x
        screen_y = mon_top + target_y

        # Clamp to be safe
        screen_x = max(mon_left, min(mon_right - 1, screen_x))
        screen_y = max(mon_top, min(mon_bottom - 1, screen_y))

        log_message(f"Moving cursor to screen: ({screen_x}, {screen_y})")

        # Update UI preview
        root.after(0, lambda: update_preview_ui(
            f"MCQ SOLVED (Single Pass)\n"
            f"Q: {question_text[:100]}\n"
            f"Correct: {correct_letter}\n"
            f"Reasoning: {reasoning[:200]}\n"
            f"Screen Target: ({screen_x}, {screen_y})"
        ))

        # ── Step 6: Move cursor ─────────────────────────────────────────────
        stealth_mouse_move(screen_x, screen_y)

    except Exception as e:
        log_message(f"MCQ Solver Error: {e}")
    finally:
        is_solving = False

# ----------------- Fix Error Process (F9) -----------------
def fix_error_process():
    global is_solving, is_paused, stop_typing, fix_screenshot_1_path

    # ── Interrupt previous solve if running ──────────────────────────────
    if is_solving:
        log_message("Interrupting current solve to start fix...")
        stop_typing = True
        with pause_cond:
            is_paused = False
            pause_cond.notify_all()
        start_wait = time.time()
        while is_solving and (time.time() - start_wait < 2.0):
            time.sleep(0.05)
        if is_solving:
            log_message("ERROR: Previous solve thread still running. Cannot start fix.")
            return

    is_solving = True
    is_paused = False
    stop_typing = False

    # ── Step 1: Capture the error screen ──────────────────────────────────
    if fix_screenshot_1_path is None:
        log_message("🔧 FIX MODE (Step 1): Capturing Error / Wrong-Answer Screen...")
        fix_screenshot_1_path = capture_screen_to_file()
        log_message("✅ Step 1 done! NOW switch to your CODE EDITOR and press F9 again.")
        is_solving = False
        return

    # ── Step 2: Capture the code editor and stitch ──────────────────────
    try:
        log_message("🔧 FIX MODE (Step 2): Capturing Code Editor screen...")
        config = load_config()
        api_keys = []
        if isinstance(config.get("api_keys"), list):
            api_keys = [k.strip() for k in config.get("api_keys") if k.strip()]
        else:
            single_key = config.get("api_key", "").strip()
            if single_key:
                api_keys = [single_key]
        if not api_keys:
            log_message("ERROR: No API Keys configured.")
            is_solving = False
            fix_screenshot_1_path = None
            return

        model = config.get("model", "gemini-2.5-flash")

        screenshot_2_path = capture_screen_to_file()

        # ── Stitch the two images vertically ──────────────────────────────
        from PIL import Image
        img1 = Image.open(fix_screenshot_1_path)
        img2 = Image.open(screenshot_2_path)
        dst = Image.new('RGB', (max(img1.width, img2.width), img1.height + img2.height))
        dst.paste(img1, (0, 0))
        dst.paste(img2, (0, img1.height))

        stitched_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_stitched.png")
        dst.save(stitched_path, format="PNG")

        # Clean up individual shots
        try: os.remove(fix_screenshot_1_path)
        except: pass
        try: os.remove(screenshot_2_path)
        except: pass
        fix_screenshot_1_path = None

    except Exception as e:
        log_message(f"ERROR stitching images: {e}")
        is_solving = False
        fix_screenshot_1_path = None
        return

    # ── Step 3: Improved AI Prompt for precise code extraction ──────────
    fix_prompt = (
        "The screenshot is a vertical stack of TWO images.\n"
        "TOP HALF: The error message, compiler output, or wrong-answer feedback.\n"
        "BOTTOM HALF: The code editor showing the user's CURRENT source code.\n\n"
        "Your task is to:\n"
        "1. Read the TOP HALF to understand what is wrong (compilation error, logic error, or failed test case).\n"
        "2. Read the BOTTOM HALF to extract the EXACT source code that was submitted.\n"
        "3. Modify that source code to fix the exact error identified in the TOP HALF.\n"
        "4. Output the COMPLETE, corrected code (including all imports/headers).\n\n"
        "IMPORTANT RULES:\n"
        "- Preserve the original indentation and formatting of the code.\n"
        "- STRIP all comments (//, #, /* */) from the output.\n"
        "- Do NOT wrap the code in markdown code fences.\n"
        "- The 'code' field must contain ONLY the fixed source code, nothing else.\n\n"
        "Return JSON:\n"
        "{\n"
        "  \"question\": \"Short error description\",\n"
        "  \"code\": \"YOUR_CORRECTED_CODE_HERE\"\n"
        "}\n"
        "Only output the raw JSON, nothing else."
    )

    try:
        question = None
        code = None
        for key in api_keys:
            try:
                if key.startswith("gsk_"):
                    log_message(f"FIX: Querying Groq Vision API ({model})...")
                    question, code = call_groq(key, model, fix_prompt, stitched_path)
                else:
                    log_message(f"FIX: Querying Gemini Vision API ({model})...")
                    question, code = call_gemini(key, model, fix_prompt, stitched_path)
                break
            except Exception as e:
                log_message(f"FIX: API KEY FAILED ({key[:4]}...): {e}")
                continue
        if question is None:
            log_message("FIX: All API keys failed. Aborting.")
            is_solving = False
            return
    finally:
        try: os.remove(stitched_path)
        except: pass

    log_message(f"FIX: Error identified: {question}")

    # ── Clean and format the fixed code ──────────────────────────────────
    code = clean_and_format_code(code)
    if not code.strip():
        log_message("FIX: AI returned empty code. Aborting.")
        is_solving = False
        return

    # Update preview
    root.after(0, lambda: update_preview_ui(code))

    # ── Step 4: Clear old code and type the new one ──────────────────────
    min_delay = config.get("min_delay_ms", 50) / 1000.0
    max_delay = config.get("max_delay_ms", 150) / 1000.0

    log_message("⏳ Clearing old code & typing fixed code in 2 seconds.")
    log_message("👉 IMPORTANT: Make sure your Code Editor window is IN FOCUS (click on it now)!")

    # Wait 2 seconds (with abort checks)
    start_time = time.time()
    while time.time() - start_time < 2.0:
        if check_pause_and_panic():
            log_message("FIX: Aborted by user before start.")
            return
        time.sleep(0.1)

    # ── Select All (Ctrl+A) and Delete to clear ─────────────────────────
    user32_local = ctypes.windll.user32
    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_DELETE_KEY = 0x2E

    user32_local.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    user32_local.keybd_event(VK_A, 0, 0, 0)
    time.sleep(0.02)
    user32_local.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    user32_local.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.15)

    user32_local.keybd_event(VK_DELETE_KEY, 0, 0, 0)
    time.sleep(0.02)
    user32_local.keybd_event(VK_DELETE_KEY, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)

    log_message("FIX: Old code cleared. Typing fixed code...")

    # ── Type the fixed code ──────────────────────────────────────────────
    type_code(code, min_delay, max_delay)

    is_solving = False
    log_message("FIX: Completed! Press F9 again to start a new fix cycle.")

# ----------------- Windows RegisterHotKey API -----------------
SOLVE_HOTKEY_ID = 1
PAUSE_HOTKEY_ID = 2
MCQ_HOTKEY_ID = 3
KILL_HOTKEY_ID = 4
FIX_HOTKEY_ID = 5

def parse_hotkey(hotkey_str):
    parts = [p.strip().upper() for p in hotkey_str.split('+')]
    modifiers = 0
    vk = 0
    
    # Custom mapping for punctuation/special keys to Windows VK codes
    special_vk = {
        "/": 0xBF,       # VK_OEM_2
        "?": 0xBF,
        "-": 0xBD,       # VK_OEM_MINUS
        "_": 0xBD,
        "=": 0xBB,       # VK_OEM_PLUS
        "+": 0xBB,
        ",": 0xBC,       # VK_OEM_COMMA
        "<": 0xBC,
        ".": 0xBE,       # VK_OEM_PERIOD
        ">": 0xBE,
        ";": 0xBA,       # VK_OEM_1
        ":": 0xBA,
        "'": 0xDE,       # VK_OEM_7
        "\"": 0xDE,
        "[": 0xDB,       # VK_OEM_4
        "{": 0xDB,
        "]": 0xDD,       # VK_OEM_6
        "}": 0xDD,
        "\\": 0xDC,      # VK_OEM_5
        "|": 0xDC,
        "`": 0xC0,       # VK_OEM_3
        "~": 0xC0,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "SPACE": 0x20,
        "BACKSPACE": 0x08,
        "ESCAPE": 0x1B,
        "ESC": 0x1B,
        "UP": 0x26,
        "DOWN": 0x28,
        "LEFT": 0x25,
        "RIGHT": 0x27,
        # Media keys
        "MEDIAPLAY": 0xB3,       # VK_MEDIA_PLAY_PAUSE
        "MEDIASTOP": 0xB2,       # VK_MEDIA_STOP
        "MEDIANEXT": 0xB0,       # VK_MEDIA_NEXT_TRACK
        "MEDIAPREV": 0xB1,       # VK_MEDIA_PREV_TRACK
        "PLAY": 0xB3,
        "STOP": 0xB2,
        "NEXT": 0xB0,
        "PREV": 0xB1,
    }
    
    for part in parts:
        if part in ("CTRL", "CONTROL"):
            modifiers |= 0x0002
        elif part == "ALT":
            modifiers |= 0x0001
        elif part == "SHIFT":
            modifiers |= 0x0004
        elif part == "WIN":
            modifiers |= 0x0008
        elif part in special_vk:
            vk = special_vk[part]
        else:
            if part.startswith("F") and len(part) > 1:
                try:
                    num = int(part[1:])
                    if 1 <= num <= 12:
                        vk = 0x6F + num
                except ValueError:
                    pass
            elif len(part) == 1:
                vk = ord(part)
                
    return vk, modifiers

# Media key VK codes that need low-level hook instead of RegisterHotKey
MEDIA_VK_CODES = {0xB0, 0xB1, 0xB2, 0xB3}  # Next, Prev, Stop, Play

def hotkey_loop(solve_vk, solve_mods, pause_vk, pause_mods):
    """Hybrid hotkey listener: uses RegisterHotKey for standard keys and a
    low-level keyboard hook (WH_KEYBOARD_LL) for media keys, since
    RegisterHotKey cannot intercept media keys on most systems."""
    global hotkey_thread_id, hotkey_registered
    
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    
    hotkey_thread_id = kernel32.GetCurrentThreadId()
    
    # ── Collect all VK → action mappings ──────────────────────────────────
    # Fixed (non-configurable) hotkeys
    VK_MCQ   = 0xB3  # Media Play/Pause → MCQ Solver
    VK_BACKTICK = 0xC0  # Backtick (`) → MCQ Solver (Alternative)
    VK_FIX   = 0xB0  # Media Next Track → Fix Error
    VK_KILL  = 0x70  # F1 → Kill App
    
    # Build a dict of VK code → (action_name, debounce_seconds)
    vk_action_map = {}
    vk_action_map[solve_vk] = ("solve", 2.0)
    vk_action_map[pause_vk] = ("pause", 0.3)
    vk_action_map[VK_MCQ]   = ("mcq",   2.0)
    vk_action_map[VK_BACKTICK] = ("mcq",   2.0)
    vk_action_map[VK_FIX]   = ("fix",   2.0)
    vk_action_map[VK_KILL]  = ("kill",  1.0)
    
    # Separate into media vs standard keys
    media_vks = {}     # VK codes that need the LL hook
    standard_vks = {}  # VK codes that work with RegisterHotKey
    
    for vk, action_info in vk_action_map.items():
        if vk in MEDIA_VK_CODES:
            media_vks[vk] = action_info
        else:
            standard_vks[vk] = action_info
    
    # ── Register standard hotkeys via RegisterHotKey ──────────────────────
    hotkey_id_map = {}  # hotkey_id → action_name
    next_id = 1
    registered_ids = []
    
    for vk, (action_name, _) in standard_vks.items():
        hk_id = next_id
        next_id += 1
        mods = 0
        if vk == solve_vk:
            mods = solve_mods
        elif vk == pause_vk:
            mods = pause_mods
        
        # Unregister first in case it was left over
        try: user32.UnregisterHotKey(None, hk_id)
        except: pass
        
        res = user32.RegisterHotKey(None, hk_id, mods, vk)
        if res:
            hotkey_id_map[hk_id] = action_name
            registered_ids.append(hk_id)
            print(f"RegisterHotKey: id={hk_id}, vk=0x{vk:02X} → {action_name}")
        else:
            print(f"FAILED RegisterHotKey: vk=0x{vk:02X} → {action_name}, error={kernel32.GetLastError()}")
    
    # ── Install low-level keyboard hook for media keys ────────────────────
    ll_hook_handle = None
    last_action_times = {name: 0.0 for name in ["solve", "pause", "mcq", "fix", "kill"]}
    
    def dispatch_action(action_name):
        """Dispatch a hotkey action with debounce."""
        current_time = time.time()
        _, debounce = vk_action_map.get(
            next((vk for vk, (a, _) in vk_action_map.items() if a == action_name), 0),
            (action_name, 2.0)
        )
        if current_time - last_action_times.get(action_name, 0) < debounce:
            return
        last_action_times[action_name] = current_time
        
        if action_name == "solve":
            threading.Thread(target=solve_process, daemon=True).start()
        elif action_name == "pause":
            toggle_pause()
        elif action_name == "mcq":
            threading.Thread(target=solve_mcq_process, daemon=True).start()
        elif action_name == "fix":
            threading.Thread(target=fix_error_process, daemon=True).start()
        elif action_name == "kill":
            root.after(0, exit_app)
    
    if media_vks:
        WH_KEYBOARD_LL = 13
        WM_KEYDOWN = 0x0100
        WM_SYSKEYDOWN = 0x0104
        
        # Define KBDLLHOOKSTRUCT
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.wintypes.DWORD),
                ("scanCode", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]
        
        HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(KBDLLHOOKSTRUCT))
        
        def ll_keyboard_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                vk = lParam[0].vkCode
                if vk in media_vks:
                    action_name, _ = media_vks[vk]
                    dispatch_action(action_name)
                    # Return 1 to consume the key (prevent media player from opening)
                    return 1
            return user32.CallNextHookEx(ll_hook_handle, nCode, wParam, lParam)
        
        # Must keep a reference to prevent garbage collection
        global _ll_hook_callback
        _ll_hook_callback = HOOKPROC(ll_keyboard_proc)
        
        ll_hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, _ll_hook_callback, None, 0
        )
        if ll_hook_handle:
            print(f"LL keyboard hook installed for media keys: {[f'0x{vk:02X}→{a}' for vk,(a,_) in media_vks.items()]}")
        else:
            print(f"FAILED to install LL keyboard hook, error={kernel32.GetLastError()}")
    
    hotkey_registered = True
    print(f"Hotkey system ready. Standard: {len(registered_ids)} registered, Media hook: {len(media_vks)} keys")
    
    # ── Message loop (drives both RegisterHotKey and LL hook) ─────────────
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            hk_id = msg.wParam
            if hk_id in hotkey_id_map:
                dispatch_action(hotkey_id_map[hk_id])
                    
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    
    # ── Cleanup ───────────────────────────────────────────────────────────
    if ll_hook_handle:
        user32.UnhookWindowsHookEx(ll_hook_handle)
    for hk_id in registered_ids:
        try: user32.UnregisterHotKey(None, hk_id)
        except: pass
    hotkey_registered = False
    print("Hotkey thread shut down.")

def stop_hotkey_thread():
    global hotkey_thread_id
    user32 = ctypes.windll.user32
    
    # Post WM_QUIT to the hotkey thread's message loop to exit cleanly
    if hotkey_thread_id is not None:
        try:
            user32.PostThreadMessageW(hotkey_thread_id, 0x0012, 0, 0)  # WM_QUIT
        except:
            pass
        hotkey_thread_id = None

def register_hotkey_from_config():
    global hotkey_thread
    
    stop_hotkey_thread()
    time.sleep(0.1)
    
    config = load_config()
    hotkey_str = config.get("hotkey", "MEDIASTOP")
    pause_hotkey_str = config.get("pause_hotkey", "MEDIAPREV")
    
    solve_vk, solve_mods = parse_hotkey(hotkey_str)
    if solve_vk == 0:
        log_message(f"Error: Invalid hotkey format '{hotkey_str}'. Defaulting to Media Stop.")
        solve_vk = 0xB2
        solve_mods = 0
        hotkey_str = "MEDIASTOP"
        
    pause_vk, pause_mods = parse_hotkey(pause_hotkey_str)
    if pause_vk == 0:
        log_message(f"Error: Invalid pause hotkey format '{pause_hotkey_str}'. Defaulting to Media Prev.")
        pause_vk = 0xB1
        pause_mods = 0
        pause_hotkey_str = "MEDIAPREV"
        
    log_message(f"Hotkeys: {hotkey_str} (Solve), {pause_hotkey_str} (Pause), Play/` (MCQ), Next (Fix), F1 (Kill), F4 (Panic)")
    
    hotkey_thread = threading.Thread(
        target=hotkey_loop,
        args=(solve_vk, solve_mods, pause_vk, pause_mods),
        daemon=True
    )
    hotkey_thread.start()

# ----------------- GUI Update Helpers -----------------
def log_message(msg):
    print(msg)
    current_time = time.strftime("[%H:%M:%S]")
    full_msg = f"{current_time} {msg}"
    if 'log_box' in globals() and log_box:
        root.after(0, lambda: append_log_ui(full_msg))

def append_log_ui(msg):
    log_box.configure(state='normal')
    log_box.insert(tk.END, msg + "\n")
    log_box.see(tk.END)
    log_box.configure(state='disabled')

def update_preview_ui(code):
    preview_box.configure(state='normal')
    preview_box.delete("1.0", tk.END)
    preview_box.insert(tk.END, code)
    preview_box.configure(state='disabled')

# ----------------- Window Actions -----------------
def show_window():
    root.deiconify()
    root.lift()
    root.focus_force()

def hide_window():
    root.withdraw()
    if PRAY_AVAILABLE and tray_icon:
        try:
            tray_icon.notify("Anti-Gravity is listening in background.", "Active in Tray")
        except:
            pass

def exit_app():
    try:
        stop_hotkey_thread()
    except:
        pass
    if 'server_socket' in globals() and server_socket:
        try:
            server_socket.close()
        except:
            pass
    if PRAY_AVAILABLE and tray_icon:
        try:
            tray_icon.stop()
        except:
            pass
    os._exit(0)

# ----------------- Single Instance TCP Server -----------------
def single_instance_check_and_server(port, on_show_callback):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('127.0.0.1', port))
        s.listen(5)
    except socket.error:
        # Binding failed -> secondary instance. Notify primary to show window and exit.
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(('127.0.0.1', port))
            client.sendall(b"SHOW")
            client.close()
        except Exception as e:
            print("Error connecting to primary instance:", e)
        return False, None

    # Binding succeeded -> primary instance. Listen for wakeup signals.
    def listen_thread():
        while True:
            try:
                conn, addr = s.accept()
                data = conn.recv(1024)
                if data == b"SHOW":
                    on_show_callback()
                conn.close()
            except Exception as e:
                break
                
    t = threading.Thread(target=listen_thread, daemon=True)
    t.start()
    return True, s

# ----------------- System Tray Setup -----------------
def setup_tray():
    global tray_icon
    if not PRAY_AVAILABLE:
        return
        
    try:
        icon_img = Image.new('RGB', (64, 64), color=(15, 23, 42)) # Slate dark theme background
        draw = ImageDraw.Draw(icon_img)
        # Draw bolt shape ⚡
        bolt_points = [(32, 8), (48, 28), (34, 28), (40, 56), (16, 36), (30, 36)]
        draw.polygon(bolt_points, fill=(56, 189, 248)) # Sky blue accent
        
        def on_open(icon, item):
            root.after(0, show_window)
            
        def on_exit_item(icon, item):
            root.after(0, exit_app)
            
        menu = pystray.Menu(
            pystray.MenuItem("Open Settings", on_open),
            pystray.MenuItem("Exit App", on_exit_item)
        )
        
        tray_icon = pystray.Icon("Anti-Gravity", icon_img, "Anti-Gravity AI Solver", menu)
        
        t = threading.Thread(target=tray_icon.run, daemon=True)
        t.start()
    except Exception as e:
        print("Failed to initialize system tray icon:", e)

# ----------------- Settings Save -----------------
def save_settings_ui():
    model = model_var.get().strip()
    hotkey = hotkey_var.get().strip()
    pause_hotkey = pause_hotkey_var.get().strip()
    
    try:
        min_delay = int(min_delay_var.get().strip())
        max_delay = int(max_delay_var.get().strip())
        if min_delay <= 0 or max_delay <= 0 or min_delay > max_delay:
            raise ValueError()
    except ValueError:
        messagebox.showerror("Invalid Delays", "Delays must be positive integers, and Min Delay must be <= Max Delay.")
        return
        
    prompt = prompt_text.get("1.0", tk.END).strip()
    
    api_key_input = api_key_var.get().strip()
    # Split on commas or newlines for multiple keys
    api_keys_list = [k.strip() for k in re.split(r"[\n,]+", api_key_input) if k.strip()]
    if not api_keys_list:
        messagebox.showerror("Invalid API Key", "Please enter at least one valid API key.")
        return
    # Save both legacy single key (first) and the list of keys
    config = {
        "api_key": api_keys_list[0],
        "api_keys": api_keys_list,
        "model": model,
        "min_delay_ms": min_delay,
        "max_delay_ms": max_delay,
        "hotkey": hotkey,
        "pause_hotkey": pause_hotkey,
        "prompt": prompt
    }
    save_config(config)
    log_message("Settings saved successfully.")
    # Update the key count badge on dashboard
    update_key_count_label(len(api_keys_list))
    # Re-register hotkey thread
    register_hotkey_from_config()
    # Hide settings GUI window
    hide_window()

def toggle_api_key_visibility():
    if show_key_var.get():
        api_key_entry.config(show="")
    else:
        api_key_entry.config(show="*")

def update_key_count_label(count):
    """Update the API key count indicator on the dashboard."""
    if 'key_count_lbl' in globals() and key_count_lbl:
        suffix = "key" if count == 1 else "keys"
        key_count_lbl.config(text=f"  {count} API {suffix} configured", fg="#34d399" if count > 0 else "#f87171")

# ----------------- Main Execution -----------------
if __name__ == "__main__":
    # 1. Single Instance Check (Port 54322 for v2)
    port = 54322
    
    def show_window_callback():
        if 'root' in globals() and root:
            root.after(0, show_window)
            
    is_primary, server_socket = single_instance_check_and_server(port, show_window_callback)
    if not is_primary:
        print("An instance of Anti-Gravity v2 is already running in background.")
        sys.exit(0)
        
    # 2. Initialize Tkinter Root Window
    root = tk.Tk()
    root.title("Anti-Gravity AI Solver v2")
    
    # Dynamically scale the window size based on system DPI
    try:
        scale_factor = ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        scale_factor = 1.0
        
    base_w, base_h = 720, 880
    scaled_w = int(base_w * scale_factor)
    scaled_h = int(base_h * scale_factor)
    
    root.geometry(f"{scaled_w}x{scaled_h}")
    root.minsize(int(640 * scale_factor), int(700 * scale_factor))
    root.configure(bg="#0f172a")
    root.resizable(True, True)
    root.protocol("WM_DELETE_WINDOW", hide_window)
    
    # ── Color Palette ──────────────────────────────────────────────────────
    COL_BG        = "#0f172a"   # Slate-900
    COL_SURFACE   = "#1e293b"   # Slate-800
    COL_SURFACE2  = "#0f1729"   # Slightly darker
    COL_BORDER    = "#334155"   # Slate-700
    COL_TEXT      = "#e2e8f0"   # Slate-200
    COL_MUTED     = "#94a3b8"   # Slate-400
    COL_ACCENT    = "#38bdf8"   # Sky-400
    COL_ACCENT2   = "#818cf8"   # Indigo-400
    COL_GREEN     = "#34d399"   # Emerald-400
    COL_AMBER     = "#fbbf24"   # Amber-400
    COL_RED       = "#f87171"   # Red-400
    COL_ROSE      = "#fb7185"   # Rose-400
    COL_TERMINAL  = "#090d16"   # Near-black terminal
    COL_LOG_TEXT  = "#a7f3d0"   # Emerald-200
    COL_PREVIEW   = "#93c5fd"   # Blue-300
    
    # ── ttk Styles ─────────────────────────────────────────────────────────
    style = ttk.Style()
    style.theme_use('clam')
    
    style.configure('TFrame', background=COL_BG)
    style.configure('Surface.TFrame', background=COL_SURFACE)
    style.configure('TLabel', background=COL_BG, foreground=COL_MUTED, font=('Segoe UI', 10))
    style.configure('Surface.TLabel', background=COL_SURFACE, foreground=COL_MUTED, font=('Segoe UI', 10))
    style.configure('Title.TLabel', background=COL_BG, foreground=COL_ACCENT, font=('Segoe UI', 16, 'bold'))
    style.configure('Subtitle.TLabel', background=COL_BG, foreground=COL_TEXT, font=('Segoe UI', 11, 'bold'))
    style.configure('SectionHeader.TLabel', background=COL_SURFACE, foreground=COL_ACCENT, font=('Segoe UI', 11, 'bold'))
    style.configure('HotkeyDesc.TLabel', background=COL_SURFACE, foreground=COL_TEXT, font=('Segoe UI', 9))
    
    style.configure('TButton', background=COL_SURFACE, foreground=COL_TEXT, borderwidth=0, font=('Segoe UI', 10, 'bold'), padding=(12, 8))
    style.map('TButton', background=[('active', COL_ACCENT)], foreground=[('active', COL_BG)])
    
    style.configure('Primary.TButton', background='#0284c7', foreground='#ffffff', borderwidth=0, font=('Segoe UI', 10, 'bold'), padding=(16, 10))
    style.map('Primary.TButton', background=[('active', COL_ACCENT)], foreground=[('active', COL_BG)])
    
    style.configure('Danger.TButton', background='#991b1b', foreground='#fecaca', borderwidth=0, font=('Segoe UI', 10, 'bold'), padding=(12, 8))
    style.map('Danger.TButton', background=[('active', COL_RED)], foreground=[('active', '#0f172a')])
    
    style.configure('TCombobox', fieldbackground=COL_SURFACE, background=COL_BG, foreground=COL_TEXT, arrowcolor=COL_ACCENT)
    
    # Notebook tab styling
    style.configure('TNotebook', background=COL_BG, borderwidth=0)
    style.configure('TNotebook.Tab', background=COL_SURFACE, foreground=COL_MUTED,
                    font=('Segoe UI', 10, 'bold'), padding=(18, 8), borderwidth=0)
    style.map('TNotebook.Tab',
              background=[('selected', COL_BG), ('active', '#263548')],
              foreground=[('selected', COL_ACCENT), ('active', COL_TEXT)])
    
    style.configure('TSeparator', background=COL_BORDER)

    # ── UI Variables ───────────────────────────────────────────────────────
    api_key_var = tk.StringVar()
    model_var = tk.StringVar(value="gemini-2.5-flash")
    min_delay_var = tk.StringVar(value="50")
    max_delay_var = tk.StringVar(value="150")
    hotkey_var = tk.StringVar(value="MEDIASTOP")
    pause_hotkey_var = tk.StringVar(value="MEDIAPREV")
    show_key_var = tk.BooleanVar(value=False)

    # ══════════════════════════════════════════════════════════════════════
    #  TOP HEADER BAR
    # ══════════════════════════════════════════════════════════════════════
    header_frame = tk.Frame(root, bg=COL_BG, padx=20, pady=12)
    header_frame.pack(fill=tk.X)
    
    # Left: title + status
    header_left = tk.Frame(header_frame, bg=COL_BG)
    header_left.pack(side=tk.LEFT)
    
    title_lbl = tk.Label(header_left, text="⚡ ANTI-GRAVITY", bg=COL_BG, fg=COL_ACCENT,
                         font=('Segoe UI', 18, 'bold'))
    title_lbl.pack(side=tk.LEFT)
    
    version_lbl = tk.Label(header_left, text="  v2", bg=COL_BG, fg=COL_MUTED,
                           font=('Segoe UI', 12))
    version_lbl.pack(side=tk.LEFT, padx=(0, 12))
    
    # Status dot
    status_dot = tk.Label(header_left, text="●", bg=COL_BG, fg=COL_GREEN,
                          font=('Segoe UI', 10))
    status_dot.pack(side=tk.LEFT)
    status_text = tk.Label(header_left, text=" Active", bg=COL_BG, fg=COL_GREEN,
                           font=('Segoe UI', 9))
    status_text.pack(side=tk.LEFT)
    
    # Right: key count
    key_count_lbl = tk.Label(header_frame, text="  0 API keys configured", bg=COL_BG, fg=COL_MUTED,
                             font=('Segoe UI', 9))
    key_count_lbl.pack(side=tk.RIGHT)
    
    # Separator under header
    sep = tk.Frame(root, bg=COL_BORDER, height=1)
    sep.pack(fill=tk.X)

    # ══════════════════════════════════════════════════════════════════════
    #  NOTEBOOK (Tabbed Layout)
    # ══════════════════════════════════════════════════════════════════════
    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

    # ──────────────────────────────────────────────────────────────────────
    #  TAB 1: DASHBOARD
    # ──────────────────────────────────────────────────────────────────────
    dash_tab = tk.Frame(notebook, bg=COL_BG, padx=20, pady=15)
    notebook.add(dash_tab, text="  ⚡ Dashboard  ")
    
    # ── Hotkeys & Usage Reference Card ────────────────────────────────────
    hotkey_card = tk.Frame(dash_tab, bg=COL_SURFACE, highlightbackground=COL_BORDER,
                           highlightthickness=1, padx=16, pady=12)
    hotkey_card.pack(fill=tk.X, pady=(0, 12))
    
    hotkey_title = tk.Label(hotkey_card, text="⌨  Hotkeys & Usage", bg=COL_SURFACE, fg=COL_ACCENT,
                            font=('Segoe UI', 12, 'bold'))
    hotkey_title.pack(anchor=tk.W, pady=(0, 10))
    
    # Hotkey data: (key_label, badge_color, description)
    hotkey_data = [
        ("▶/‖",  "#8b5cf6", "MCQ Solver",       "Play/Pause key — captures screen, solves MCQ, moves cursor"),
        ("■",    "#0ea5e9", "Code Solver",       "Stop key — captures screen, solves coding question, types solution"),
        ("⏮",   "#f59e0b", "Pause / Resume",    "Prev Track key — pauses or resumes simulated typing"),
        ("⏭",   "#10b981", "Fix Error",          "Next Track key — two-step: press on error → editor → press again"),
        ("F4",   "#ef4444", "Panic Abort",        "Hold F4 to immediately stop typing mid-stream"),
        ("F8",   "#dc2626", "Kill App",           "Instantly exits the entire application"),
    ]
    
    for i, (key, color, name, desc) in enumerate(hotkey_data):
        row_frame = tk.Frame(hotkey_card, bg=COL_SURFACE)
        row_frame.pack(fill=tk.X, pady=2)
        
        # Key badge
        badge = tk.Label(row_frame, text=f" {key} ", bg=color, fg="#ffffff",
                         font=('Segoe UI', 10, 'bold'), padx=6, pady=1)
        badge.pack(side=tk.LEFT, padx=(0, 10))
        
        # Action name
        name_lbl = tk.Label(row_frame, text=name, bg=COL_SURFACE, fg=COL_TEXT,
                            font=('Segoe UI', 10, 'bold'), width=14, anchor=tk.W)
        name_lbl.pack(side=tk.LEFT, padx=(0, 6))
        
        # Description
        desc_lbl = tk.Label(row_frame, text=desc, bg=COL_SURFACE, fg=COL_MUTED,
                            font=('Segoe UI', 9), anchor=tk.W)
        desc_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # Note about configurable keys
    note_lbl = tk.Label(hotkey_card, text="💡 Solve (■) and Pause (⏮) can be changed in the Settings tab",
                        bg=COL_SURFACE, fg="#64748b", font=('Segoe UI', 8, 'italic'))
    note_lbl.pack(anchor=tk.W, pady=(8, 0))

    # ── Status Logs ───────────────────────────────────────────────────────
    log_header = tk.Label(dash_tab, text="📋  Status Logs", bg=COL_BG, fg=COL_TEXT,
                          font=('Segoe UI', 11, 'bold'))
    log_header.pack(anchor=tk.W, pady=(8, 4))
    
    log_frame = tk.Frame(dash_tab, bg=COL_TERMINAL, highlightbackground=COL_BORDER,
                         highlightthickness=1)
    log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
    
    log_box = tk.Text(log_frame, bg=COL_TERMINAL, fg=COL_LOG_TEXT, insertbackground="#ffffff",
                      bd=0, font=('Consolas', 9), state='disabled', padx=10, pady=8,
                      wrap=tk.WORD)
    log_scrollbar = tk.Scrollbar(log_frame, command=log_box.yview, bg=COL_SURFACE,
                                  troughcolor=COL_TERMINAL, activebackground=COL_ACCENT)
    log_box.config(yscrollcommand=log_scrollbar.set)
    log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ── Code Preview ──────────────────────────────────────────────────────
    preview_header = tk.Label(dash_tab, text="🖥  Last Solved Code Preview", bg=COL_BG, fg=COL_TEXT,
                              font=('Segoe UI', 11, 'bold'))
    preview_header.pack(anchor=tk.W, pady=(4, 4))
    
    preview_frame = tk.Frame(dash_tab, bg=COL_TERMINAL, highlightbackground=COL_BORDER,
                             highlightthickness=1)
    preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
    
    preview_box = tk.Text(preview_frame, bg=COL_TERMINAL, fg=COL_PREVIEW, insertbackground="#ffffff",
                          bd=0, font=('Consolas', 9), state='disabled', padx=10, pady=8,
                          wrap=tk.NONE)
    preview_scrollbar = tk.Scrollbar(preview_frame, command=preview_box.yview, bg=COL_SURFACE,
                                     troughcolor=COL_TERMINAL, activebackground=COL_ACCENT)
    preview_box.config(yscrollcommand=preview_scrollbar.set)
    preview_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    preview_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ──────────────────────────────────────────────────────────────────────
    #  TAB 2: SETTINGS
    # ──────────────────────────────────────────────────────────────────────
    settings_tab = tk.Frame(notebook, bg=COL_BG, padx=20, pady=15)
    notebook.add(settings_tab, text="  ⚙️ Settings  ")
    
    # ── API Keys Section ──────────────────────────────────────────────────
    api_card = tk.Frame(settings_tab, bg=COL_SURFACE, highlightbackground=COL_BORDER,
                        highlightthickness=1, padx=16, pady=14)
    api_card.pack(fill=tk.X, pady=(0, 14))
    
    tk.Label(api_card, text="🔑  API Keys", bg=COL_SURFACE, fg=COL_ACCENT,
             font=('Segoe UI', 12, 'bold')).pack(anchor=tk.W, pady=(0, 4))
    tk.Label(api_card, text="Enter one or more API keys, separated by commas or newlines.",
             bg=COL_SURFACE, fg="#64748b", font=('Segoe UI', 8, 'italic')).pack(anchor=tk.W, pady=(0, 8))
    
    key_entry_frame = tk.Frame(api_card, bg=COL_SURFACE)
    key_entry_frame.pack(fill=tk.X)
    
    api_key_entry = tk.Entry(key_entry_frame, textvariable=api_key_var, show="*",
                             bg="#0f172a", fg="#ffffff", insertbackground="#ffffff",
                             bd=0, font=('Consolas', 10), relief=tk.FLAT)
    api_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))
    
    show_key_cb = tk.Checkbutton(key_entry_frame, text="Show", variable=show_key_var,
                                  command=toggle_api_key_visibility,
                                  bg=COL_SURFACE, fg=COL_MUTED, activebackground=COL_SURFACE,
                                  activeforeground=COL_TEXT, selectcolor="#0f172a",
                                  font=('Segoe UI', 9), bd=0)
    show_key_cb.pack(side=tk.RIGHT)
    
    # ── Model & Hotkeys Section ───────────────────────────────────────────
    config_card = tk.Frame(settings_tab, bg=COL_SURFACE, highlightbackground=COL_BORDER,
                           highlightthickness=1, padx=16, pady=14)
    config_card.pack(fill=tk.X, pady=(0, 14))
    
    tk.Label(config_card, text="🤖  Model & Hotkeys", bg=COL_SURFACE, fg=COL_ACCENT,
             font=('Segoe UI', 12, 'bold')).pack(anchor=tk.W, pady=(0, 12))
    
    config_grid = tk.Frame(config_card, bg=COL_SURFACE)
    config_grid.pack(fill=tk.X)
    config_grid.columnconfigure(1, weight=1)
    
    # Model
    tk.Label(config_grid, text="AI Model", bg=COL_SURFACE, fg=COL_MUTED,
             font=('Segoe UI', 10)).grid(row=0, column=0, sticky=tk.W, pady=6, padx=(0, 16))
    model_cb = ttk.Combobox(config_grid, textvariable=model_var,
                            values=["gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro",
                                    "gemini-2.5-pro", "meta-llama/llama-4-scout-17b-16e-instruct",
                                    "qwen/qwen3.6-27b"],
                            state="readonly", width=40)
    model_cb.grid(row=0, column=1, sticky=tk.EW, pady=6)
    
    # Solve Hotkey
    tk.Label(config_grid, text="Solve Hotkey", bg=COL_SURFACE, fg=COL_MUTED,
             font=('Segoe UI', 10)).grid(row=1, column=0, sticky=tk.W, pady=6, padx=(0, 16))
    hotkey_entry = tk.Entry(config_grid, textvariable=hotkey_var,
                            bg="#0f172a", fg="#ffffff", insertbackground="#ffffff",
                            bd=0, font=('Consolas', 10), relief=tk.FLAT)
    hotkey_entry.grid(row=1, column=1, sticky=tk.EW, pady=6, ipady=5)
    
    # Pause Hotkey
    tk.Label(config_grid, text="Pause/Resume Hotkey", bg=COL_SURFACE, fg=COL_MUTED,
             font=('Segoe UI', 10)).grid(row=2, column=0, sticky=tk.W, pady=6, padx=(0, 16))
    pause_hotkey_entry = tk.Entry(config_grid, textvariable=pause_hotkey_var,
                                  bg="#0f172a", fg="#ffffff", insertbackground="#ffffff",
                                  bd=0, font=('Consolas', 10), relief=tk.FLAT)
    pause_hotkey_entry.grid(row=2, column=1, sticky=tk.EW, pady=6, ipady=5)
    
    # ── Typing Delays Section ─────────────────────────────────────────────
    delay_card = tk.Frame(settings_tab, bg=COL_SURFACE, highlightbackground=COL_BORDER,
                          highlightthickness=1, padx=16, pady=14)
    delay_card.pack(fill=tk.X, pady=(0, 14))
    
    tk.Label(delay_card, text="⏱  Typing Delays (milliseconds)", bg=COL_SURFACE, fg=COL_ACCENT,
             font=('Segoe UI', 12, 'bold')).pack(anchor=tk.W, pady=(0, 4))
    tk.Label(delay_card, text="Controls the speed of simulated typing. Lower = faster, higher = more human-like.",
             bg=COL_SURFACE, fg="#64748b", font=('Segoe UI', 8, 'italic')).pack(anchor=tk.W, pady=(0, 10))
    
    delay_grid = tk.Frame(delay_card, bg=COL_SURFACE)
    delay_grid.pack(fill=tk.X)
    
    tk.Label(delay_grid, text="Min Delay", bg=COL_SURFACE, fg=COL_MUTED,
             font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=(0, 8))
    min_entry = tk.Entry(delay_grid, textvariable=min_delay_var,
                         bg="#0f172a", fg="#ffffff", insertbackground="#ffffff",
                         bd=0, font=('Consolas', 11), relief=tk.FLAT, width=8, justify=tk.CENTER)
    min_entry.pack(side=tk.LEFT, padx=(0, 20), ipady=5)
    
    tk.Label(delay_grid, text="Max Delay", bg=COL_SURFACE, fg=COL_MUTED,
             font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=(0, 8))
    max_entry = tk.Entry(delay_grid, textvariable=max_delay_var,
                         bg="#0f172a", fg="#ffffff", insertbackground="#ffffff",
                         bd=0, font=('Consolas', 11), relief=tk.FLAT, width=8, justify=tk.CENTER)
    max_entry.pack(side=tk.LEFT, ipady=5)
    
    tk.Label(delay_grid, text="ms", bg=COL_SURFACE, fg="#64748b",
             font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=(4, 0))
    
    # ── Action Buttons ────────────────────────────────────────────────────
    settings_btn_frame = tk.Frame(settings_tab, bg=COL_BG)
    settings_btn_frame.pack(fill=tk.X, pady=(10, 0))
    
    save_btn = ttk.Button(settings_btn_frame, text="💾  Save & Hide to Background",
                          style="Primary.TButton", command=save_settings_ui)
    save_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    
    exit_btn = ttk.Button(settings_btn_frame, text="✕  Exit Application",
                          style="Danger.TButton", command=exit_app)
    exit_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

    # ──────────────────────────────────────────────────────────────────────
    #  TAB 3: PROMPT
    # ──────────────────────────────────────────────────────────────────────
    prompt_tab = tk.Frame(notebook, bg=COL_BG, padx=20, pady=15)
    notebook.add(prompt_tab, text="  📝 Prompt  ")
    
    tk.Label(prompt_tab, text="📝  System Prompt / Instructions", bg=COL_BG, fg=COL_ACCENT,
             font=('Segoe UI', 12, 'bold')).pack(anchor=tk.W, pady=(0, 4))
    tk.Label(prompt_tab, text="This prompt is sent to the AI alongside the screenshot. Customize it to change how the AI responds.",
             bg=COL_BG, fg="#64748b", font=('Segoe UI', 8, 'italic')).pack(anchor=tk.W, pady=(0, 10))
    
    prompt_frame = tk.Frame(prompt_tab, bg=COL_TERMINAL, highlightbackground=COL_BORDER,
                            highlightthickness=1)
    prompt_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
    
    prompt_text = tk.Text(prompt_frame, bg=COL_SURFACE, fg=COL_TEXT, insertbackground="#ffffff",
                          bd=0, font=('Consolas', 10), padx=12, pady=10, wrap=tk.WORD)
    prompt_scrollbar = tk.Scrollbar(prompt_frame, command=prompt_text.yview, bg=COL_SURFACE,
                                    troughcolor=COL_SURFACE, activebackground=COL_ACCENT)
    prompt_text.config(yscrollcommand=prompt_scrollbar.set)
    prompt_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    prompt_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    
    # Reset prompt button
    def reset_prompt():
        prompt_text.delete("1.0", tk.END)
        prompt_text.insert("1.0", DEFAULT_PROMPT)
        log_message("Prompt reset to default.")
    
    prompt_btn_frame = tk.Frame(prompt_tab, bg=COL_BG)
    prompt_btn_frame.pack(fill=tk.X, pady=(0, 0))
    
    reset_btn = ttk.Button(prompt_btn_frame, text="↺  Reset to Default", command=reset_prompt)
    reset_btn.pack(side=tk.RIGHT)

    # ══════════════════════════════════════════════════════════════════════
    #  LOAD CONFIG & INITIALIZE
    # ══════════════════════════════════════════════════════════════════════
    config = load_config()
    
    # Load API keys
    api_keys = config.get("api_keys")
    if isinstance(api_keys, list) and api_keys:
        api_key_var.set(", ".join(api_keys))
        update_key_count_label(len(api_keys))
    else:
        single = config.get("api_key", "")
        api_key_var.set(single)
        update_key_count_label(1 if single.strip() else 0)
    
    model_var.set(config.get("model", "gemini-2.5-flash"))
    min_delay_var.set(str(config.get("min_delay_ms", 50)))
    max_delay_var.set(str(config.get("max_delay_ms", 150)))
    hotkey_var.set(config.get("hotkey", "MEDIASTOP"))
    pause_hotkey_var.set(config.get("pause_hotkey", "MEDIAPREV"))
    prompt_text.insert("1.0", config.get("prompt", DEFAULT_PROMPT))

    # 4. Set up system tray
    setup_tray()

    # 5. Register global hotkeys from settings
    register_hotkey_from_config()

    log_message("Anti-Gravity v2 initialized — all hotkeys active.")
    log_message(f"Solve: {config.get('hotkey', 'MEDIASTOP')}  |  Pause: {config.get('pause_hotkey', 'MEDIAPREV')}  |  MCQ: Play/`  |  Fix: Next  |  Kill: F1")
    
    # 6. Enter GUI Mainloop
    root.mainloop()
