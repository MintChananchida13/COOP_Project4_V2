# PROJECT_MEMORY.md

# Intelligent Document Template Management System

> Production-ready Document Intelligence Platform

---

# 1. Project Vision

Build a production-ready Document Intelligence Platform capable of:

- Detecting document templates by layout
- Verifying document identity using OCR
- Extracting only user-selected information
- Supporting any structured document without hardcoding document types
- Allowing continuous template expansion through an admin workflow

The system should evolve by improving independent modules rather than redesigning the whole architecture.

---

# 2. Design Philosophy

## Layout First

Document templates are identified primarily by layout rather than OCR text.

---

## OCR Second

OCR is used only after candidate retrieval for:

- Template verification
- User-selected field extraction
- Custom OCR

The system should never OCR the entire document unless required.

---

## Candidate Retrieval, not Classification

Visual embedding models (such as SigLIP) retrieve candidate templates.

They are not responsible for final document classification.

Final confirmation must combine multiple evidence sources.

---

## Modular AI

Every AI component must be replaceable.

Business logic must never depend on one specific AI model.

---

## Service-Oriented Architecture

AI logic belongs inside services.

Frontend and business logic should never directly depend on AI implementations.

---

# 3. Core Principles

The following principles must never change.

## Relative ROI

Store ROI as ratios.

Never persist pixel coordinates.

Every ROI contains:

- page_number
- x_ratio
- y_ratio
- width_ratio
- height_ratio

---

## Multi-page Native

Every pipeline must preserve page context.

Supported inputs:

- Image
- Multiple Images
- PDF

---

## Ignore Regions

Ignore dynamic regions before generating layout embeddings.

Examples:

- Names
- Numbers
- QR Codes
- Signatures

Ignore Regions preserve layout consistency.

---

## Template Fields

Use only one Template Field model.

Verification Fields are Template Fields where

use_for_verification = true

Do not create separate verification field tables.

---

## Confidence-driven Workflow

Every important stage returns confidence.

The final decision must never rely on one score only.

---

# 4. High-Level Architecture

```
Upload Document
        â”‚
        â–¼
Split into Pages
        â”‚
        â–¼
Image Preprocessing
        â”‚
        â–¼
Generate Layout Embedding
        â”‚
        â–¼
Candidate Retrieval (Top-K)
        â”‚
        â–¼
Multi-stage Verification
        â”‚
        â–¼
Page Matching
        â”‚
        â–¼
Document Alignment
        â”‚
        â–¼
ROI Projection
        â”‚
        â–¼
OCR Extraction
        â”‚
        â–¼
Field Validation
        â”‚
        â–¼
Confidence Engine
        â”‚
        â–¼
Result
```

---

# 5. Detection Pipeline

The detection pipeline consists of:

1. Generate layout embedding.
2. Retrieve Top-K candidate templates.
3. Verify each candidate.
4. Match document pages.
5. Calculate confidence.
6. Confirm template.

Candidate retrieval is never the final decision.

---

# 6. Extraction Pipeline

Extraction begins only after template confirmation.

Workflow:

Template

â†“

Page Matching

â†“

Alignment

â†“

ROI Projection

â†“

OCR

â†“

Validation

â†“

Result

---

# 7. User Workflow

User uploads document.

â†“

System detects template.

â†“

If confirmed:

- Display selectable fields.
- User selects desired fields.
- OCR selected fields only.
- Return structured result.

If no template is confirmed:

â†“

Open Custom OCR Studio.

â†“

User draws ROI.

â†“

OCR selected ROI.

â†“

Optional Template Request.

---

# 8. Admin Workflow

Template Request

â†“

Review Request

â†“

Convert to Template

â†“

Adjust Sample Pages

â†“

Create Template Fields

â†“

Mark Verification Fields

â†“

Create Ignore Regions

â†“

Validate Template

â†“

Generate Embedding

â†“

Activate Template

---

# 9. Template Lifecycle

Draft

â†“

Validated

â†“

Embedding Pending

â†“

Active

â†“

Deprecated

â†“

Archived

Only Active templates participate in candidate retrieval.

---

# 10. Template Knowledge Model

A template represents document knowledge.

Each template may contain:

- Pages
- Fields
- Verification Fields
- Ignore Regions
- Detection Rules
- Validation Rules
- Embeddings
- Version Information

Future metadata may be added without redesigning the architecture.

---

# 11. Service Overview

Core services include:

- PageSplitService
- ImageProcessingService
- ImageEncoderService
- EmbeddingService
- VectorStoreService
- OCRService
- VerificationService
- TemplateDetectionService
- AlignmentService
- ProjectionService
- ExtractionService
- ValidationService
- ConfidenceService
- AdminTemplateService

Each service has a single responsibility.

---

# 12. Confidence Strategy

Confidence should combine multiple stages.

Possible evidence:

- Retrieval Confidence
- Verification Confidence
- Page Matching Confidence
- Alignment Confidence
- OCR Confidence
- Validation Confidence

The confidence formula may evolve.

The architecture should not depend on one fixed formula.

---

# 13. Failure Strategy

Candidate Retrieval Failed

â†“

Open Custom OCR

---

Verification Failed

â†“

Try Next Candidate

---

Alignment Failed

â†“

Retry

â†“

Fallback

---

OCR Confidence Low

â†“

Require Review

---

Validation Failed

â†“

Return Warning

---

System Failure

â†“

Return Clear Error

---

# 14. AI Extensibility

The architecture must support replacing AI modules.

Image Verification

- SigLIP
- CLIP
- Future models

Layout Matching

- PP-DocLayoutV3 Layout Signature
- SQLite/PostgreSQL candidate scoring
- Future indexing layer

OCR

- PaddleOCR
- Tesseract
- EasyOCR
- Cloud OCR

Alignment

- ORB
- SIFT
- LoFTR
- Future methods

Replacing an AI module must not require changing business logic.

---

# 15. Coding Rules

Always:

- Preserve page context.
- Store ROI as ratios.
- Keep AI inside services.
- Prefer refactoring over rewriting.
- Avoid hardcoded document types.
- Design modules to be replaceable.
- Keep template metadata extensible.

Never:

- Store ROI as persistent pixels.
- Couple business logic with AI models.
- Assume page order equals template page order.
- Depend on a single confidence score.

---

# 16. Future Roadmap

Phase 1

Template Management

âœ“ Completed

Phase 2

Embedding Pipeline

âœ“ Completed

Phase 3

Candidate Retrieval

âœ“ Completed

Phase 4

Multi-stage Verification

In Progress

Phase 5

Page Matching

Planned

Phase 6

Document Alignment

Planned

Phase 7

ROI Projection

Planned

Phase 8

Template-based Extraction

Planned

Phase 9

Validation Engine

Planned

Phase 10

Production User Flow

Planned

---

# 17. Never Remove

The following concepts are fundamental.

- Relative ROI
- Multi-page Support
- Ignore Regions
- OCR Verification
- Candidate Retrieval
- Confidence Engine
- Template Lifecycle
- Template Test Mode
- Page Matching
- Document Alignment
- ROI Projection
- Custom OCR
- Template Request
- Admin Approval
- Selectable Extraction Fields
- AI Abstraction
- Service-oriented Architecture

---

# 18. Long-term Vision

This project is not an OCR application.

It is a Document Intelligence Platform.

OCR is only one component.

The platform should continue evolving by improving independent modules while preserving the overall architecture.

Every module should be replaceable, testable, and maintainable without redesigning the entire system.
