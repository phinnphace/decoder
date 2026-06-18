import modal
import os
import re
import json
import time
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── 1. Modal App + Volume ──────────────────────────────────────────────────────
app = modal.App("asl-decoder-cloud")
model_volume = modal.Volume.from_name("asl-model-volume")

# ── 2. Container image (CPU Optimized) ─────────────────────────────────────────
cuda_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("cmake", "build-essential", "clang", "libomp-dev", "ninja-build")
    .env({"CMAKE_ARGS": "-DLLAMA_CUDA=off -DGGML_CUDA=off"})
    .pip_install("fastapi", "pydantic", "gspread", "google-auth", "ninja", "llama-cpp-python==0.3.6")
)

# ── 3. ASGI app (Global) ───────────────────────────────────────────────────────
web_app = FastAPI(title="Decoder Ring Chat API")
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 4. Pydantic Models ─────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]

class ChatResponse(BaseModel):
    reply: str
    evidence_grid: Optional[Dict[str, Any]]
    conversation_complete: bool
    turn_count: int

# ── 5. Serverless function ─────────────────────────────────────────────────────
@app.function(
    image=cuda_image,
    volumes={"/data": model_volume},
    secrets=[
        modal.Secret.from_name("google-sheets-auth"),
        modal.Secret.from_name("System-prompt"),
    ],
    min_containers=0,
    timeout=120,
)
@modal.asgi_app()
def fastapi_app():
    from llama_cpp import Llama
    import gspread
    from google.oauth2.service_account import Credentials

    # ── Load Secrets ───────────────────────────────────────────────────────────
    SYSTEM_PROMPT = os.environ.get("systemprompt", "")
    GCP_JSON = os.environ.get("google_sheets_auth", "")
    SHEET_ID = os.environ.get("SHEETS_ID", "")

    # ── Load Model ────────────────────────────────────────────────────────────
    MODEL_PATH = "/data/data/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    llm = Llama(model_path=MODEL_PATH, n_gpu_layers=0, n_ctx=8192, verbose=False)

        # ── Setup Google Auth (Bulletproof) ──────────────────────────────────────
    GCP_CREDS = None
    if GCP_JSON:
        try:
            # 1. Strip BOM (invisible character sometimes added by Google/OS)
            clean_json = GCP_JSON.lstrip('\ufeff')
            
            # 2. Remove trailing commas (e.g., "-----END PRIVATE KEY-----\n", })
            clean_json = re.sub(r',\s*([\]}])', r'\1', clean_json)
            
            # 3. Extract only the first valid JSON object (ignores accidental double-pastes)
            brace_count = 0
            end_idx = 0
            for i, char in enumerate(clean_json):
                if char == '{': brace_count += 1
                elif char == '}': 
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            
            creds_dict = json.loads(clean_json[:end_idx])
            scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            GCP_CREDS = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            print("✓ Google Auth Loaded Successfully")
        except Exception as e:
            print(f"Auth Error: {e}")
            print(f"DEBUG: First 100 chars of secret: {repr(GCP_JSON[:100])}")
    # ── THE DETERMINISTIC DIAGNOSTIC ENGINE ────────────────────────────────────
    class DiagnosticEngine:
        def __init__(self):
            self.landmark_keywords = {
                'M': ['m', 'n', 't', 'thumb', 'tucked', 'overlap', 'finger placement'],
                'N': ['m', 'n', 't', 'thumb', 'tucked', 'overlap'],
                'T': ['t', 'a', 'thumb', 'position', 'subtlety'],
                'A': ['a', 's', 'closed fist', 'thumb position'],
                'S': ['a', 's', 'closed fist'],
                'U': ['u', 'v', 'r', 'finger separation', 'crossing'],
                'V': ['u', 'v', 'r', 'finger separation'],
                'R': ['u', 'v', 'r', 'crossed fingers'],
                'J': ['j', 'z', 'dynamic', 'moving', 'motion'],
                'Z': ['j', 'z', 'dynamic', 'moving', 'motion'],
            }
            self.temporal_keywords = ['slow', 'lag', 'cuts off', 'transition', 'blur', 'sequence', 'coarticulation', 'speed', 'timing', 'j', 'z', 'moving letters']
            self.exclusion_keywords = ['works for them', 'others', 'regional', 'skin tone', 'handedness', 'camera', 'age', 'disability', 'fluency', 'style', 'lighting', 'background']

        def extract_evidence(self, user_text: str) -> dict:
            text_lower = user_text.lower()
            evidence = {
                'letters_mentioned': [],
                'timing_issue': False,
                'face_body_issue': False,
                'exclusion_issue': False,
                'randomness': False,
                'verbatim_quote': user_text.strip()
            }
            for letter, keywords in self.landmark_keywords.items():
                if any(kw in text_lower for kw in keywords):
                    if letter not in evidence['letters_mentioned']:
                        evidence['letters_mentioned'].append(letter)
            if any(kw in text_lower for kw in self.temporal_keywords):
                evidence['timing_issue'] = True
            if any(kw in text_lower for kw in ['face', 'body', 'expression', 'eyebrow', 'hands only', 'whole person']):
                evidence['face_body_issue'] = True
            if any(kw in text_lower for kw in self.exclusion_keywords):
                evidence['exclusion_issue'] = True
            if any(kw in text_lower for kw in ['random', 'sometimes', 'unpredictable', 'inconsistent', 'varies']):
                evidence['randomness'] = True
            return evidence

        def determine_category(self, all_evidence: list) -> dict:
            all_letters = []
            has_timing = False
            has_face_body = False
            has_exclusion = False
            has_randomness = False
            latest_quote = ""
            for evidence in all_evidence:
                all_letters.extend(evidence['letters_mentioned'])
                has_timing = has_timing or evidence['timing_issue']
                has_face_body = has_face_body or evidence['face_body_issue']
                has_exclusion = has_exclusion or evidence['exclusion_issue']
                has_randomness = has_randomness or evidence['randomness']
                if evidence['verbatim_quote']:
                    latest_quote = evidence['verbatim_quote']
            unique_letters = list(dict.fromkeys(all_letters))
            category = "unknown"
            confidence = "low"
            if len(unique_letters) >= 2:
                confusion_pairs = [['M', 'N', 'T'], ['A', 'S'], ['U', 'V', 'R'], ['J', 'Z']]
                for pair in confusion_pairs:
                    if any(l.upper() in pair for l in unique_letters):
                        category = "21_landmark_geometric_failure"
                        confidence = "high" if len([l for l in unique_letters if l.upper() in pair]) >= 2 else "medium"
                        break
            if has_timing or has_face_body:
                if category == "unknown":
                    category = "temporal_multimodal_failure"
                    confidence = "high" if (has_timing and has_face_body) else "medium"
                else:
                    category = "hybrid_with_temporal_issues"
                    confidence = "medium"
            if has_exclusion:
                if category == "unknown":
                    category = "training_data_exclusion"
                    confidence = "high" if has_exclusion else "medium"
                else:
                    category = f"{category}_plus_exclusion"
                    confidence = "medium"
            if category == "unknown" and not unique_letters and not has_timing and not has_face_body and not has_exclusion:
                category = "emotion_only_no_technical_detail"
                confidence = "low"
            return {
                "category": category,
                "confidence": confidence,
                "letters": unique_letters,
                "timing": has_timing,
                "face_body": has_face_body,
                "exclusion": has_exclusion,
                "randomness": has_randomness,
                "verbatim": latest_quote
            }

        def get_next_probe(self, current_evidence: dict, all_evidence: list) -> str:
            if not current_evidence.get('letters_mentioned'):
                return "PROBE_LETTERS"
            if not current_evidence.get('timing_issue') and not current_evidence.get('face_body_issue'):
                return "PROBE_TEMPORAL"
            if not current_evidence.get('exclusion_issue'):
                return "PROBE_EXCLUSION"
            return "READY_TO_CLOSE"

    engine = DiagnosticEngine()

    # ── Helper: Parse Evidence Grid (FIXED SPACES) ─────────────────────────────
    def parse_evidence_grid(text: str) -> Optional[Dict[str, Any]]:
        start_marker = "[EVIDENCE_GRID]"
        end_marker = "[/EVIDENCE_GRID]"
        start_idx = text.find(start_marker)
        end_idx = text.find(end_marker)
        if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
            return None
        
        block = text[start_idx + len(start_marker):end_idx].strip()
        fields = {}
        for line in block.split("\n"):  # FIXED: Removed invisible space
            line = line.strip()
            if ": " not in line: continue  # FIXED: Removed invisible space
            key, _, value = line.partition(": ")  # FIXED: Removed invisible space
            fields[key.strip()] = value.strip()
            
        try:
            def b(k): return fields.get(k, "false").lower() == "true"
            def arr(k):
                v = fields.get(k, "null").lower()
                if v == "null": return None
                cleaned = v.strip("[]")
                if not cleaned: return None
                return [i.strip().strip('"').strip("'") for i in cleaned.split(",") if i.strip()]
            def opt(k, allowed):
                v = fields.get(k, "null").lower()
                return v if v in allowed else None
            def s(k):
                v = fields.get(k, "")
                return v[1:-1] if v.startswith('"') and v.endswith('"') else v
                
            grid = {
                "A_specific_letters_named": b("A_specific_letters_named"),
                "A_letter_details": arr("A_letter_details"),
                "B_timing_complaint": b("B_timing_complaint"),
                "B_timing_details": opt("B_timing_details", ["slowing_down_helps", "transitions_fail", "speed_dependent", "JZ_fail"]),
                "C_face_body_ignored": b("C_face_body_ignored"),
                "D_signer_exclusion": b("D_signer_exclusion"),
                "D_exclusion_details": opt("D_exclusion_details", ["regional", "skin_tone", "handedness", "camera", "age", "disability", "fluency_path", "style_rejected"]),
                "E_randomness_inconsistency": b("E_randomness_inconsistency"),
                "F_emotion_only_no_technical_detail": b("F_emotion_only_no_technical_detail"),
                "user_verbatim_quote": s("user_verbatim_quote"),
            }
            
            if not grid["A_specific_letters_named"]: grid["A_letter_details"] = None
            if not grid["B_timing_complaint"]: grid["B_timing_details"] = None
            if not grid["D_signer_exclusion"]: grid["D_exclusion_details"] = None
            
            return grid
        except Exception as e:
            print(f"Grid parse error: {e}")
            return None

    # ── Helper: Chat Runner ────────────────────────────────────────────────────
    def run_chat(messages: List[Dict[str, str]]) -> Dict[str, Any]:
        user_turns = sum(1 for m in messages if m.get("role") == "user")
        
        # 1. Extract evidence from ALL user messages
        all_user_messages = [m for m in messages if m.get("role") == "user"]
        all_evidence = [engine.extract_evidence(m["content"]) for m in all_user_messages]
        current_evidence = all_evidence[-1] if all_evidence else {}
        
        # 2. Determine diagnostic category
        diagnosis = engine.determine_category(all_evidence)
        
        # 3. Determine next probe
        next_probe = engine.get_next_probe(current_evidence, all_evidence) if user_turns < 8 else "READY_TO_CLOSE"
        
        # 4. Build dynamic system prompt
        state_note = f"""
[SYSTEM STATE - DO NOT MENTION TO USER]
Turn count: {user_turns}
Evidence collected so far:
- Letters mentioned: {', '.join(diagnosis['letters']) if diagnosis['letters'] else 'None'}
- Timing issues: {'Yes' if diagnosis['timing'] else 'No'}
- Face/body issues: {'Yes' if diagnosis['face_body'] else 'No'}
- Exclusion issues: {'Yes' if diagnosis['exclusion'] else 'No'}
Next action: {next_probe}
"""
        dynamic_system_prompt = f"{SYSTEM_PROMPT}\n\n{state_note}"
        full_messages = [{"role": "system", "content": dynamic_system_prompt}] + messages
        
        # 5. Generate LLM response
        output = llm.create_chat_completion(
            messages=full_messages,
            max_tokens=512,
            temperature=0.4,
            top_p=0.9,
            repeat_penalty=1.25,
            stop=["[/EVIDENCE_GRID]"],
        )
        reply = output["choices"][0]["message"]["content"].strip()
        
        # 6. Parse grid
        grid = parse_evidence_grid(reply)
        
        # 7. Log to Google Sheets
        if grid and GCP_CREDS:
            try:
                client = gspread.authorize(GCP_CREDS)
                ws = client.open_by_key(SHEET_ID).worksheet("diagnoses")  # FIXED: Removed space in SHEET_ID
                user_texts = [m["content"] for m in messages if m.get("role") == "user"]
                ws.append_row([
                    datetime.utcnow().isoformat(),
                    " | ".join(user_texts)[:1000],
                    json.dumps(grid.get("A_letter_details") or []),
                    grid.get("user_verbatim_quote", ""),
                    grid.get("A_specific_letters_named", False),
                    grid.get("B_timing_complaint", False),
                ])
                print("✓ Sheet logged")
            except Exception as e:
                print(f"Sheet error: {e}")
        else:
            if not grid: print("DEBUG: Grid is None, skipping sheet log.")
            if not GCP_CREDS: print("DEBUG: GCP_CREDS is None, skipping sheet log.")

        # 8. Return response
        conversation_complete = next_probe == "READY_TO_CLOSE" or (user_turns >= 8 and diagnosis['category'] != "unknown")
        
        return {
            "reply": reply,
            "evidence_grid": grid,
            "conversation_complete": conversation_complete,
            "turn_count": user_turns,
            "diagnostic_result": diagnosis,
        }

    # ── Routes ─────────────────────────────────────────────────────────────────
    @web_app.get("/api/health")
    def health():
        return {"status": "ok", "model": "Qwen2.5"}

    @web_app.post("/api/chat")
    def chat(req: ChatRequest):
        messages = [{"role": m.role, "content": m.content} for m in req.messages]
        result = run_chat(messages)
        return ChatResponse(
            reply=result["reply"],
            evidence_grid=result["evidence_grid"],
            conversation_complete=result["conversation_complete"],
            turn_count=result["turn_count"],
        )

    return web_app