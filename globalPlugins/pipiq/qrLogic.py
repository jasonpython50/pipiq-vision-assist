# Prompt construction, model-output parsing, and human-friendly position
# phrasing for the QR locator and image describer.
# Deliberately NVDA-free (translation falls back to identity) for unit testing.

import json
import re

try:
	import addonHandler
	addonHandler.initTranslation()
except Exception:
	def _(s):
		return s


QR_PROMPT = (
	"You are helping a blind user aim their phone camera at a QR code shown on their computer screen. "
	"Look at this screenshot. If it contains one or more QR codes, respond ONLY with JSON: "
	'{"found": true, "codes": [{"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}]} '
	"listing every QR code, where values are fractions of image width and height between 0.0 and 1.0. "
	'If there is no QR code respond ONLY with {"found": false}. '
	"Do not include any explanation, reasoning, or markdown. Output the JSON only."
)

BRIEF_DESCRIPTION_PROMPT = (
	"You are describing an image to a blind screen reader user. "
	"Describe the important content in 2 to 4 clear sentences, starting with the most important information. "
	"If the image contains readable text, quote the text exactly. "
	"Plain sentences only: no markdown, no emoji, no special symbols."
)

DETAILED_DESCRIPTION_PROMPT = (
	"You are describing an image to a blind screen reader user who wants full detail. "
	"First give a one-sentence overview, then describe the layout from top to bottom: "
	"people and their expressions, objects, colors, any charts or diagrams, and quote all readable text exactly. "
	"Mention anything a sighted person would consider important. "
	"Plain sentences only: no markdown, no emoji, no special symbols."
)

EXTRACT_TEXT_PROMPT = (
	"You are reading an image aloud to a blind screen reader user. "
	"Transcribe ALL text in the image exactly as written, from top to bottom, preserving the original wording, "
	"numbers, and line order. Do not describe the image, do not summarize, do not translate, do not add commentary. "
	"If a small part of the image is a picture rather than text, mention it in one short bracketed note. "
	"If the image contains no text at all, say: The image contains no text. Then describe it in one sentence."
)

AUTO_MODE_ADDITION = (
	" Important: if the image consists mainly of written text, such as a document, a message, an error dialog, "
	"or a screenshot of text, transcribe all of that text exactly as written instead of describing the image."
)

FINAL_ANSWER_ONLY = " Output only the final answer, with no reasoning, thinking, or preamble."


def buildDescriptionPrompt(detailLevel, customPrompt, languageCode, contentMode="auto"):
	if contentMode == "extract":
		# Exact transcription: never translated, so no language instruction.
		return EXTRACT_TEXT_PROMPT + FINAL_ANSWER_ONLY
	if detailLevel == "custom" and customPrompt.strip():
		prompt = customPrompt.strip()
	elif detailLevel == "detailed":
		prompt = DETAILED_DESCRIPTION_PROMPT
	else:
		prompt = BRIEF_DESCRIPTION_PROMPT
	if contentMode == "auto":
		prompt += AUTO_MODE_ADDITION
	return _appendLanguage(prompt, languageCode) + FINAL_ANSWER_ONLY


def buildQrPrompt():
	# The QR reply is machine-parsed JSON; never localized.
	return QR_PROMPT


def _appendLanguage(prompt, languageCode):
	if languageCode:
		code = languageCode.replace("_", "-").split("-")[0].lower()
		if code and code != "en":
			prompt += " Respond in the language whose ISO 639-1 code is '%s'." % code
	return prompt


def _iterJsonObjects(text):
	"""Yield every brace-balanced {...} substring, tolerating surrounding prose."""
	depth = 0
	start = None
	inString = False
	escape = False
	for i, ch in enumerate(text):
		if inString:
			if escape:
				escape = False
			elif ch == "\\":
				escape = True
			elif ch == '"':
				inString = False
			continue
		if ch == '"':
			inString = True
		elif ch == "{":
			if depth == 0:
				start = i
			depth += 1
		elif ch == "}":
			if depth > 0:
				depth -= 1
				if depth == 0 and start is not None:
					yield text[start:i + 1]


def _normalizeCoord(value, imagePixels):
	"""Accept fractions (0-1), percentages (0-100), or raw pixels."""
	try:
		v = float(value)
	except (TypeError, ValueError):
		return None
	if 0.0 <= v <= 1.0:
		return v
	if 1.0 < v <= 100.0:
		return v / 100.0
	if imagePixels and v <= imagePixels * 1.05:
		return v / imagePixels
	return None


def parseQrResponse(text, imageWidth=None, imageHeight=None):
	"""Parse the model reply into {"found": bool, "codes": [(l, t, r, b), ...]}.

	Returns None when no usable JSON was produced (e.g. a reasoning model that
	ran out of tokens mid-thought).
	"""
	candidates = []
	for blob in _iterJsonObjects(text):
		try:
			obj = json.loads(blob)
		except ValueError:
			continue
		if isinstance(obj, dict) and "found" in obj:
			candidates.append(obj)
	if not candidates:
		return None
	obj = candidates[-1]
	if not obj.get("found"):
		return {"found": False, "codes": []}
	codes = []
	for code in obj.get("codes") or []:
		if not isinstance(code, dict):
			continue
		l = _normalizeCoord(code.get("left"), imageWidth)
		t = _normalizeCoord(code.get("top"), imageHeight)
		r = _normalizeCoord(code.get("right"), imageWidth)
		b = _normalizeCoord(code.get("bottom"), imageHeight)
		if None in (l, t, r, b):
			continue
		l, r = min(l, r), max(l, r)
		t, b = min(t, b), max(t, b)
		l = min(max(l, 0.0), 1.0)
		t = min(max(t, 0.0), 1.0)
		r = min(max(r, 0.0), 1.0)
		b = min(max(b, 0.0), 1.0)
		if r - l > 0.005 and b - t > 0.005:
			codes.append((l, t, r, b))
	if not codes:
		# The model claimed success but gave no usable box; treat as parse failure
		# so the user gets a "try again" message instead of a false positive.
		return None
	# Largest first: with several codes on screen, guide to the biggest target.
	codes.sort(key=lambda c: (c[2] - c[0]) * (c[3] - c[1]), reverse=True)
	return {"found": True, "codes": codes}


def _regionName(cx, cy):
	# Translators: horizontal thirds of the screen or window.
	cols = (_("left"), _("center"), _("right"))
	# Translators: vertical thirds of the screen or window.
	rows = (_("top"), _("middle"), _("bottom"))
	col = cols[min(2, int(cx * 3))]
	row = rows[min(2, int(cy * 3))]
	if row == _("middle") and col == _("center"):
		# Translators: QR position when it is in the middle of the screen or window.
		return _("center")
	if row == _("middle"):
		# Translators: e.g. "middle left"; {col} is left/center/right.
		return _("middle {col}").format(col=col)
	if col == _("center"):
		# Translators: e.g. "top center"; {row} is top/bottom.
		return _("{row} center").format(row=row)
	# Translators: e.g. "top right"; {row} is top/bottom, {col} is left/right.
	return _("{row} {col}").format(row=row, col=col)


def describeQrResult(codes, scopeLabel, monitorPhrase="", mouseMoved=False):
	"""Build the spoken guidance for found QR codes.

	codes: normalized (l, t, r, b) tuples, largest first.
	scopeLabel: localized "the screen" / "the window".
	"""
	l, t, r, b = codes[0]
	cx = (l + r) / 2.0
	cy = (t + b) / 2.0
	widthPct = int(round((r - l) * 100))
	parts = []
	if len(codes) > 1:
		# Translators: spoken when several QR codes are visible; {count} is the number found.
		parts.append(_("{count} QR codes found; guiding you to the largest.").format(count=len(codes)))
	# Translators: main QR position report. {region} is like "top right",
	# {scope} is "the screen" or "the window", {x} and {y} are percentages.
	parts.append(
		_("QR code found in the {region} of {scope}{monitor}, centered {x} percent from the left edge and {y} percent from the top.").format(
			region=_regionName(cx, cy),
			scope=scopeLabel,
			monitor=monitorPhrase,
			x=int(round(cx * 100)),
			y=int(round(cy * 100)),
		)
	)
	# Translators: reports how large the QR code is; {size} is a percentage.
	parts.append(_("It spans about {size} percent of the width.").format(size=widthPct))
	if mouseMoved:
		# Translators: confirmation after the mouse pointer was placed on the QR code.
		parts.append(_("The mouse pointer is now on the QR code."))
	if widthPct < 8:
		# Translators: advice when the detected QR code is very small on screen.
		parts.append(_("The code is quite small; consider maximizing the window or zooming in, then scan again."))
	# Translators: final aiming advice; {region} is like "top right", {scope} is "the screen" or "the window".
	parts.append(_("Point your phone camera at the {region} of {scope}.").format(region=_regionName(cx, cy), scope=scopeLabel))
	return " ".join(parts)


# Reasoning models sometimes emit their chain of thought in the visible
# content, wrapped in think tags (<think>...</think>, or Kimi's ◁think▷ form).
_THINK_BLOCK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.IGNORECASE | re.DOTALL)
_KIMI_THINK_BLOCK = re.compile(r"◁think▷.*?◁/think▷", re.DOTALL)
_UNMATCHED_CLOSERS = ("</think>", "</thinking>", "◁/think▷")
_UNMATCHED_OPENERS = ("<think>", "<thinking>", "◁think▷")


def stripReasoning(text):
	"""Remove thinking traces so only the model's final answer is presented."""
	text = _THINK_BLOCK.sub("", text)
	text = _KIMI_THINK_BLOCK.sub("", text)
	for closer in _UNMATCHED_CLOSERS:
		# An opener may have been truncated away; keep only what follows the closer.
		if closer in text:
			text = text.rsplit(closer, 1)[-1]
	for opener in _UNMATCHED_OPENERS:
		# Output truncated mid-thought: everything from the opener on is
		# reasoning, never an answer. Returning "" triggers the caller's
		# "empty answer, try again" path instead of speaking the monologue.
		i = text.lower().find(opener)
		if i != -1:
			text = text[:i]
	return text.strip()


_MARKDOWN_JUNK = re.compile(r"[*_`#|]+")


def sanitizeForSpeech(text):
	"""Strip markdown artifacts and collapse all whitespace so speech and braille flash messages stay clean."""
	text = _MARKDOWN_JUNK.sub("", text)
	text = re.sub(r"\s+", " ", text)
	return text.strip()


def sanitizeForDisplay(text):
	"""Strip markdown artifacts but keep paragraph breaks, so the browseable
	window stays navigable line by line (important for braille reading)."""
	text = _MARKDOWN_JUNK.sub("", text)
	text = re.sub(r"[ \t]+", " ", text)
	text = re.sub(r"\n{3,}", "\n\n", text)
	return text.strip()
