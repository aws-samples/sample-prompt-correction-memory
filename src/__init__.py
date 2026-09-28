"""sample-prompt-correction-memory.

Automated prompt self-correction for LLM document extraction.
Each human correction improves future extractions — no retraining, no redeployment.
"""

__version__ = "0.1.0"

from src.extraction.extractor import Extractor
from src.extraction.models import CorrectionRecord, ExtractionResult, FieldDefinition
from src.prompt_memory.calibration import ConfidenceCalibrator
from src.prompt_memory.confidence_router import ConfidenceRouter
from src.prompt_memory.correction_store import CorrectionStore
from src.prompt_memory.rule_engine import GraduatedRule, RuleEngine
from src.prompt_memory.semantic_retrieval import SemanticRetriever

__all__ = [
    "Extractor",
    "FieldDefinition",
    "ExtractionResult",
    "CorrectionRecord",
    "ConfidenceRouter",
    "CorrectionStore",
    "RuleEngine",
    "GraduatedRule",
    "ConfidenceCalibrator",
    "SemanticRetriever",
]
