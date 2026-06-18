# decoder
# Computational Diagnostic Assay for Vision Model Training Data Failure Patterns

## Overview
This repository contains the source code for a computational diagnostic assay designed to identify, structure, and log known failure patterns of vision models based upon their underlying training datasets. 

The assay surfaces correlational phraseologies paired with their anatomical counterparts, revealing failure clusters regardless of whether vision model (VM) authors disclose their training data (proprietary claims, incomplete documentation, etc.). This diagnostic instrument brings training data transparency to light through user-reported failure modes.

## Purpose & Impact
Identifying VM training data saves users time, resources, and does not perpetuate harm. Models trained on limited landmark sets (e.g., 21 landmarks, MediaPipe) have well-documented failure clusters. This assay operationalizes that knowledge into a structured diagnostic workflow.

Less resource-intensive use in every way benefits everyone. Rather than requiring users to reverse-engineer why a model fails, the assay provides a systematic method to surface and document these failures.

## Methodology & Architecture
The assay operates on a "LLM-in-front, deterministic-backend" architecture to mitigate hallucination and ensure research-grade accuracy:

1.  **Structured Elicitation (Frontend):** An LLM (Qwen2.5-7B-Instruct) engages the user to extract technical and experiential details regarding vision model failures.
2.  **Deterministic Parsing (Backend):** The LLM is prompted to output a strict `[EVIDENCE_GRID]`. A deterministic Python parser extracts this grid, enforcing cross-field constraints and data types.
3.  **Audit & Logging:** Raw LLM outputs and parsed grids are logged to an append-only JSONL audit trail and a structured Google Sheet for subsequent qualitative and quantitative analysis.

## The Evidence Grid Schema
The assay extracts the following diagnostic markers from user reports:
*   **[A] Specific Letters Named:** Identification of specific fingerspelled letters targeted in exclusion (proxy for landmark-based failure patterns).
*   **[B] Timing Complaints:** Analysis of speed-dependent failures (e.g., transitions failing at specific speeds).
*   **[C] Face/Body Ignored:** Instances where non-manual markers or bodily autonomy are dismissed by the vision model.
*   **[D] Signer Exclusion:** Categorical exclusion based on regional dialects, skin tone, handedness, camera angle, age, disability, fluency path, or rejected styles.
*   **[E] Randomness/Inconsistency:** Identification of arbitrary or inconsistent rule application by the vision model.
*   **[F] Emotion-Only Deflection:** Instances where technical critiques are deflected with purely emotional or non-technical responses.

## Equity and Accuracy Safeguards
To ensure the instrument does not perpetuate the very erasure it seeks to measure:
*   **Raw Output Auditing:** Every LLM response is logged verbatim before parsing. This allows researchers to audit the model for dialect bias or misclassification post-hoc.
*   **Deterministic Constraints:** The Python parser enforces logical constraints (e.g., if no specific letters are named, letter details are nullified) to prevent the LLM from generating contradictory diagnostic data.

## Files Included
*   `app.py`: The core assay pipeline, containing the FastAPI server, LLM routing, deterministic parser, and audit logging.
*   `system_prompt.txt`: The exact elicitation prompt used to constrain the LLM's diagnostic behavior.
*   `CITATION.cff`: Metadata for citing this instrument in academic work.
