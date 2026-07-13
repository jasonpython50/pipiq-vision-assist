# Vision capability registry for OpenCode Go models.
# The live /models endpoint returns only model IDs with no capability metadata,
# so vision support is tracked here (source: models.dev provider data, July 2026)
# and refreshed heuristically for models we have never seen.

KNOWN_VISION_MODELS = (
	"qwen3.7-plus",
	"qwen3.6-plus",
	"qwen3.5-plus",
	"kimi-k2.7-code",
	"kimi-k2.6",
	"kimi-k2.5",
	"mimo-v2.5",
	"mimo-v2.5-pro",
	"mimo-v2-pro",
	"mimo-v2-omni",
)

# Tested defaults: qwen3.7-plus returns clean, accurate QR bounding boxes and a
# correct "not found" on QR-less screens; kimi-k2.5 gives fast, high quality
# image descriptions but floods the QR task with visible reasoning.
RECOMMENDED_QR_MODEL = "qwen3.7-plus"
RECOMMENDED_DESCRIPTION_MODEL = "kimi-k2.5"

# Substrings that suggest vision support in model IDs we have no data for
# (relevant when the user points the addon at a different OpenAI-compatible server).
_VISION_HINTS = ("vl", "vision", "omni", "multimodal", "4o", "gpt-5", "gemini", "claude", "pixtral", "llava")


def isLikelyVisionModel(modelId):
	m = modelId.lower()
	if m in KNOWN_VISION_MODELS:
		return True
	return any(hint in m for hint in _VISION_HINTS)
