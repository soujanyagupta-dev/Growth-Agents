import os
import json
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
import pandas as pd
from pydantic import BaseModel, Field, conint, ValidationError
from slugify import slugify
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading


import google.generativeai as genai

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

API_KEYS = [
    "AIzaSyBtm_YZ5pmgTtiEfqWjwIZvou2qqW1OVcw",
    "AIzaSyBk9NTtyIHqk4gWmFuKMNenjLiw5tDr4z0",  
    "AIzaSyDVzA9WzP2v6wSwQI7ut5UPAMy9rAm7cK4"  
]

if not all(API_KEYS):
    raise SystemExit("Please provide all 3 API keys in the API_KEYS list.")

thread_local = threading.local()

def get_api_key_for_row(row_index: int) -> str:
    """Get API key based on row index (rotating through keys)"""
    return API_KEYS[row_index % len(API_KEYS)]

def configure_api_for_thread(api_key: str):
    """Configure Gemini API for current thread"""
    if not hasattr(thread_local, 'configured_key') or thread_local.configured_key != api_key:
        genai.configure(api_key=api_key)
        thread_local.configured_key = api_key
        print(f"Thread {threading.current_thread().name}: Configured with API key ending in ...{api_key[-4:]}")


SYSTEM_INSTRUCTION = r"""
You are a Human Evaluator Quality Checker for CODING TASK EVALUATIONS.

YOUR ROLE:
You evaluate coding responses yourself and then check if human evaluators align with your expert ratings AND assess the quality of their justifications.

CORE TASK:
1) FIRST: Evaluate the coding response against the SPECIFIC PROMPT REQUIREMENTS and give YOUR OWN ratings (1-5) for all 7 dimensions
2) SECOND: Compare your expert ratings with the human evaluator's ratings
3) THIRD: Check if their ratings align with yours (within ±1 point)
4) FOURTH: Evaluate the QUALITY of their justifications using strict rules
5) FIFTH: Reduce overall rating if justifications don't meet quality standards
6) SIXTH: Provide comprehensive feedback including justification quality issues

EVALUATION FOCUS - PROMPT REQUIREMENTS ONLY:
- Evaluate ONLY based on what the prompt specifically asks for
- Ignore general coding best practices unless explicitly mentioned in prompt
- Do NOT penalize for missing error handling, edge cases, or optimizations unless the prompt requires them
- Focus on: Does the response solve the exact task as stated in the prompt?

JUSTIFICATION QUALITY RULES (STRICT):
Each justification MUST meet these standards or rating gets reduced:

1. **EVIDENCE-BASED**: Must cite specific examples from the response
   - ❌ "The code is good" (no evidence)
   - ✅ "The code correctly implements the sorting algorithm as shown in lines 5-8"

2. **PROMPT-FOCUSED**: Must relate to prompt requirements only
   - ❌ "Good error handling" (if prompt doesn't ask for it)
   - ✅ "Addresses the specific file path requirement mentioned in the prompt"

3. **CLEAR LOGIC**: Must have logical reasoning
   - ❌ "It's complete" (no explanation why)
   - ✅ "Complete because it handles all three cases: empty list, single element, and multiple elements"

4. **SPECIFIC RATING JUSTIFICATION**: Must explain why that specific number (1-5)
   - ❌ "Good quality" (doesn't justify the rating)
   - ✅ "Rating 4 because it solves the main task but misses the edge case mentioned in prompt"

5. **GRAMMAR & CLARITY**: Must be well-written and understandable
   - ❌ "Code work good" (poor grammar)
   - ✅ "The code works well and follows the specified requirements"

RATING REDUCTION RULES:
- **Poor justification**: Reduce rating by 1 point
- **Very poor justification**: Reduce rating by 2 points
- **Missing justification**: Reduce rating by 2 points
- **Multiple quality issues**: Reduce rating by up to 3 points

YOUR PROCESS:
A) Read the prompt carefully to understand exactly what is being asked
B) Evaluate the response against ONLY the prompt requirements
C) Give YOUR expert ratings (1-5) for all 7 dimensions with brief justification
D) Compare your ratings with human evaluator ratings:
   - ALIGNED: |your_rating - human_rating| ≤ 1 point
   - MISALIGNED: |your_rating - human_rating| ≥ 2 points
E) Evaluate justification quality for each dimension using strict rules above
F) Apply rating reductions based on justification quality
G) Determine overall evaluator quality score based on alignment AND justification quality

RATING RUBRIC (1-5 for each dimension):
1. Correctness: Does the code solve the prompt requirements?
2. Completeness: Does it address all prompt requirements?
3. Relevance: Is it focused on the prompt task?
4. Style: Is the code readable and well-formatted?
5. Coherence: Is the solution logical and well-structured?
6. Helpfulness: Does it provide useful guidance for the task?
7. Creativity: Does it show innovative thinking within prompt constraints?

OVERALL EVALUATOR SCORING:
- Score 5: All ratings align with yours (±1 point) AND all justifications meet quality standards
- Score 4: Most ratings align, few minor misalignments, most justifications are good
- Score 3: Some ratings align, some misalignments, some justification quality issues
- Score 2: Many ratings misaligned OR many poor justifications
- Score 1: Most ratings significantly misaligned OR most justifications are poor

FEEDBACK APPROACH:
- If ratings align: "Your evaluation aligns well with expert assessment"
- If ratings misalign: "You rated X as Y but expert assessment shows it should be Z because..."
- For poor justifications: "Your justification for X lacks specific evidence and doesn't justify the rating"
- Focus on major misalignments (≥2 points difference) AND justification quality issues
- Explain why your rating is correct based on prompt requirements

OUTPUT CONTRACT:
Return BOTH:
1) JSON with this structure:
{
  "prompt_digest": str,
  "expert_evaluation": {
    "correctness": {"score": 1-5, "justification": str},
    "completeness": {"score": 1-5, "justification": str},
    "relevance": {"score": 1-5, "justification": str},
    "style": {"score": 1-5, "justification": str},
    "coherence": {"score": 1-5, "justification": str},
    "helpfulness": {"score": 1-5, "justification": str},
    "creativity": {"score": 1-5, "justification": str}
  },
  "alignment_check": {
    "correctness": {"expert": int, "human": int, "aligned": bool, "difference": int},
    "completeness": {"expert": int, "human": int, "aligned": bool, "difference": int},
    "relevance": {"expert": int, "human": int, "aligned": bool, "difference": int},
    "style": {"expert": int, "human": int, "aligned": bool, "difference": int},
    "coherence": {"expert": int, "human": int, "aligned": bool, "difference": int},
    "helpfulness": {"expert": int, "human": int, "aligned": bool, "difference": int},
    "creativity": {"expert": int, "human": int, "aligned": bool, "difference": int}
  },
  "justification_quality": {
    "correctness": {"quality_score": 1-5, "issues": [str], "rating_reduction": int},
    "completeness": {"quality_score": 1-5, "issues": [str], "rating_reduction": int},
    "relevance": {"quality_score": 1-5, "issues": [str], "rating_reduction": int},
    "style": {"quality_score": 1-5, "issues": [str], "rating_reduction": int},
    "coherence": {"quality_score": 1-5, "issues": [str], "rating_reduction": int},
    "helpfulness": {"quality_score": 1-5, "issues": [str], "rating_reduction": int},
    "creativity": {"quality_score": 1-5, "issues": [str], "rating_reduction": int}
  },
  "evaluator_feedback": {
    "score": 1|2|3|4|5,
    "notes": str,
    "justification_quality_summary": str
  }
}

2) Markdown Report: Your Expert Evaluation, Alignment Check, Justification Quality Assessment, and Feedback

CRITICAL REMINDERS:
- FIRST give YOUR expert ratings based on prompt requirements
- THEN compare with human evaluator ratings
- THEN evaluate justification quality using strict rules
- Reduce ratings for poor justifications
- Score the human evaluator based on alignment AND justification quality
- Include justification quality issues in overall feedback
- Focus on prompt requirements, not general coding standards
"""



def build_user_payload_from_row(row: pd.Series) -> str:
    def gv(x):  # get value safely, strip spaces
        return "" if pd.isna(x) else str(x).strip()

    prompt = gv(row.get("Prompt"))

    r1 = {
        "Correctness rating": gv(row.get("R1_CorrectnessRating")),
        "Justification correctness": gv(row.get("R1_JustificationCorrectness")),
        "Completeness rating": gv(row.get("R1_CompletenessRating")),
        "Justification completeness": gv(row.get("R1_JustificationCompleteness")),
        "Relevance rating": gv(row.get("R1_RelevanceRating")),
        "Justification relevance": gv(row.get("R1_JustificationRelevance")),
        "Style & Presentation rating": gv(row.get("R1_StyleRating")),
        "Justification style_presentation": gv(row.get("R1_JustificationStyle")),
        "Coherence rating": gv(row.get("R1_CoherenceRating")),
        "Justification coherence": gv(row.get("R1_JustificationCoherence")),
        "Helpfulness rating": gv(row.get("R1_HelpfulnessRating")),
        "Justification helpfulness": gv(row.get("R1_JustificationHelpfulness")),
        "Creativity rating": gv(row.get("R1_CreativityRating")),
        "Justification creativity": gv(row.get("R1_JustificationCreativity")),
        "Overall justification": gv(row.get("R1_OverallJustification")),
    }


    r2_present = any(gv(row.get(col)) for col in [
        "R2_CorrectnessRating","R2_JustificationCorrectness",
        "R2_CompletenessRating","R2_JustificationCompleteness",
        "R2_RelevanceRating","R2_JustificationRelevance",
        "R2_StyleRating","R2_JustificationStyle",
        "R2_CoherenceRating","R2_JustificationCoherence",
        "R2_HelpfulnessRating","R2_JustificationHelpfulness",
        "R2_CreativityRating","R2_JustificationCreativity",
        "R2_OverallJustification"
    ])

    payload_lines = []
    payload_lines.append(f"Prompt: {prompt}\n")
    payload_lines.append("Response 1:\n")
    for k, v in r1.items():
        payload_lines.append(f"{k}: {v}")

    if r2_present:
        r2 = {
            "Correctness rating": gv(row.get("R2_CorrectnessRating")),
            "Justification correctness": gv(row.get("R2_JustificationCorrectness")),
            "Completeness rating": gv(row.get("R2_CompletenessRating")),
            "Justification completeness": gv(row.get("R2_JustificationCompleteness")),
            "Relevance rating": gv(row.get("R2_RelevanceRating")),
            "Justification relevance": gv(row.get("R2_JustificationRelevance")),
            "Style & Presentation rating": gv(row.get("R2_StyleRating")),
            "Justification style_presentation": gv(row.get("R2_JustificationStyle")),
            "Coherence rating": gv(row.get("R2_CoherenceRating")),
            "Justification coherence": gv(row.get("R2_JustificationCoherence")),
            "Helpfulness rating": gv(row.get("R2_HelpfulnessRating")),
            "Justification helpfulness": gv(row.get("R2_JustificationHelpfulness")),
            "Creativity rating": gv(row.get("R2_CreativityRating")),
            "Justification creativity": gv(row.get("R2_JustificationCreativity")),
            "Overall justification": gv(row.get("R2_OverallJustification")),
        }
        payload_lines.append("\nResponse 2:\n")
        for k, v in r2.items():
            payload_lines.append(f"{k}: {v}")

    return "\n".join(payload_lines)



class GeminiCallError(Exception):
    pass

@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type(GeminiCallError),
)
def call_gemini(system_instruction: str, user_payload: str, api_key: str) -> str:
    try:
        # Configure API for this thread
        configure_api_for_thread(api_key)
        
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=system_instruction,
        )
        resp = model.generate_content(
            contents=[
                {"role": "user", "parts": [user_payload]}
            ],
            safety_settings=None,
            generation_config={
                "temperature": 0.2,      # stable, less variance for verification
                "top_p": 0.9,
                "max_output_tokens": 2048  # Reduced to save on token usage
            },
        )
        if resp is None or resp.candidates is None:
            raise GeminiCallError("Empty response from Gemini.")
        text = resp.text or ""
        if not text.strip():
            raise GeminiCallError("No text in Gemini response.")
        return text
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "quota" in error_str.lower():
            print(f"Rate limit exceeded for API key ending in ...{api_key[-4:]}. Waiting 30 seconds before retry...")
            time.sleep(30)
        raise GeminiCallError(error_str)



def split_json_and_markdown(output_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Expect the model to return JSON first, then Markdown.
    We try to parse the first {...} as JSON. The remainder is treated as Markdown.
    """
    start = output_text.find("{")
    end = output_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, output_text.strip()

    json_text = output_text[start:end+1]
    md_text = output_text[end+1:].strip()

    try:
        parsed = json.loads(json_text)
        return parsed, md_text
    except Exception:
        return None, output_text.strip()



def process_single_row(row_data: Tuple[int, pd.Series], out_dir: str) -> Dict[str, Any]:
    """Process a single row with its assigned API key"""
    idx, row = row_data
    
    record_id = str(row.get("record_id") or f"row{idx+1}")
    person = str(row.get("person") or "unknown")
    email = str(row.get("email") or "unknown@email.com")
    name = f"{record_id}_{slugify(person)}"
    
    # Get API key for this row
    api_key = get_api_key_for_row(idx)
    
    print(f"Thread {threading.current_thread().name}: Processing {name} with API key ending in ...{api_key[-4:]}")
    
    try:
        user_payload = build_user_payload_from_row(row)
        output_text = call_gemini(SYSTEM_INSTRUCTION, user_payload, api_key)
        
        parsed_json, md_text = split_json_and_markdown(output_text)
        
        # Extract feedback data for CSV output
        if parsed_json and "evaluator_feedback" in parsed_json:
            feedback_score = parsed_json["evaluator_feedback"].get("score", "N/A")
            feedback_notes = parsed_json["evaluator_feedback"].get("notes", "No feedback available")
        else:
            feedback_score = "N/A"
            feedback_notes = "JSON parsing failed - check raw output"
        
        # Save files
        json_path = os.path.join(out_dir, f"{name}_evaluator_feedback.json")
        md_path = os.path.join(out_dir, f"{name}_evaluator_feedback.md")
        raw_path = os.path.join(out_dir, f"{name}_raw_output.txt")

        if parsed_json is not None:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(parsed_json, f, ensure_ascii=False, indent=2)
        else:
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(output_text)

        # Write Markdown if present
        if md_text:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_text)

        print(f"  → {name}: saved {json_path if parsed_json else raw_path}")
        if md_text:
            print(f"  → {name}: saved {md_path}")
        
        return {
            "Name": person,
            "Email": email,
            "Score": feedback_score,
            "Feedback": feedback_notes[:200] + "..." if len(feedback_notes) > 200 else feedback_notes
        }
        
    except Exception as e:
        print(f"Error processing {name}: {e}")
        return {
            "Name": person,
            "Email": email,
            "Score": "Error",
            "Feedback": f"Processing failed: {str(e)}"
        }



def main(input_csv: str, out_dir: str = "reports", max_workers: int = 3):
    df = pd.read_csv(input_csv, keep_default_na=False)
    os.makedirs(out_dir, exist_ok=True)

    required = ["record_id", "person", "email", "Prompt",
                "R1_CorrectnessRating","R1_JustificationCorrectness",
                "R1_CompletenessRating","R1_JustificationCompleteness",
                "R1_RelevanceRating","R1_JustificationRelevance",
                "R1_StyleRating","R1_JustificationStyle",
                "R1_CoherenceRating","R1_JustificationCoherence",
                "R1_HelpfulnessRating","R1_JustificationHelpfulness",
                "R1_CreativityRating","R1_JustificationCreativity",
                "R1_OverallJustification"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"Missing required columns: {missing}")

    print(f"Starting parallel processing with {max_workers} workers (3 API keys)")
    print(f"Processing {len(df)} rows...")
    
    row_data = [(idx, row) for idx, row in df.iterrows()]
    csv_output_data = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_row = {
            executor.submit(process_single_row, row_info, out_dir): row_info[0]
            for row_info in row_data
        }
        
        for future in as_completed(future_to_row):
            row_idx = future_to_row[future]
            try:
                result = future.result()
                csv_output_data.append(result)
                print(f"✓ Completed row {row_idx + 1} using API key {result['api_key_used']}")
            except Exception as exc:
                print(f"✗ Row {row_idx + 1} generated an exception: {exc}")
                csv_output_data.append({
                    "Name": "unknown",
                    "Email": "unknown@email.com",
                    "Score": "Error",
                    "Feedback": f"Thread exception: {str(exc)}"
                })

    csv_output_path = os.path.join(out_dir, "evaluator_summary.csv")
    csv_df = pd.DataFrame(csv_output_data)
    csv_df.to_csv(csv_output_path, index=False)
    
    print(f"\n✓ Concise CSV summary saved: {csv_output_path}")
    print(f"✓ Total records processed: {len(csv_output_data)}")
    
    print(f"\n📊 CSV Columns:")
    print(f"   Name: Evaluator name")
    print(f"   Email: Evaluator email")
    print(f"   Score: Quality score (1-5)")
    print(f"   Feedback: Short feedback summary")
    
    print("\nDone!")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Check quality of human evaluators for coding task responses using parallel processing.")
    p.add_argument("--csv", default="corrected_data.csv", help="Path to input CSV (default: 'corrected_data.csv')")
    p.add_argument("--out", default="reports", help="Output directory (default: reports)")
    p.add_argument("--workers", type=int, default=3, help="Number of parallel workers (default: 3)")
    args = p.parse_args()
    main(args.csv, args.out, args.workers)