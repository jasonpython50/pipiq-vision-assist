# HTTP client for the OpenCode Go API (OpenAI-compatible).
# Deliberately NVDA-free so it can be unit-tested outside NVDA; only urllib is
# used because NVDA does not bundle the requests library.

import base64
import json
import socket
import urllib.error
import urllib.request

try:
	import addonHandler
	addonHandler.initTranslation()
except Exception:
	def _(s):
		return s

try:
	from logHandler import log
except Exception:
	import logging
	log = logging.getLogger("pipiq")

# The OpenCode API sits behind Cloudflare, which rejects Python's default
# urllib User-Agent with HTTP 403 error 1010 — a real UA string is required.
USER_AGENT = "NVDA-PiPiQ-VisionAssist/1.0"

DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"


class ApiError(Exception):
	"""Raised with a short, speakable, user-friendly message."""


def _friendlyHttpError(e):
	try:
		detail = e.read().decode("utf-8", "replace")[:300]
	except Exception:
		detail = ""
	log.error("PiPiQ API HTTP %s: %s" % (e.code, detail))
	if e.code in (401, 403):
		# Translators: spoken when the API rejects the configured key.
		return _("The API rejected your key. Check the API key in PiPiQ Vision Assist settings.")
	if e.code in (402, 429):
		# Translators: spoken when the API subscription quota is used up.
		return _("Your OpenCode subscription limit or rate limit was reached. Try again later.")
	if e.code == 404:
		# Translators: spoken when the model or base URL is wrong.
		return _("The server did not recognize the request. Check the base URL and model name in settings.")
	if e.code >= 500:
		# Translators: spoken on a server-side failure.
		return _("The AI service reported a server error. Try again, or pick a different model in settings.")
	# Translators: generic API error; {code} is the HTTP status number.
	return _("The AI service returned error {code}.").format(code=e.code)


def _request(url, apiKey, payload=None, timeout=60):
	headers = {
		"User-Agent": USER_AGENT,
		"Accept": "application/json",
	}
	if apiKey:
		headers["Authorization"] = "Bearer " + apiKey.strip()
	data = None
	if payload is not None:
		headers["Content-Type"] = "application/json"
		data = json.dumps(payload).encode("utf-8")
	req = urllib.request.Request(url, data=data, headers=headers)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			return json.loads(resp.read().decode("utf-8"))
	except urllib.error.HTTPError as e:
		raise ApiError(_friendlyHttpError(e))
	except (socket.timeout, TimeoutError):
		# Translators: spoken when the AI request takes longer than the configured timeout.
		raise ApiError(_("No response from the AI service within the time limit. You can raise the timeout in settings."))
	except urllib.error.URLError as e:
		log.error("PiPiQ API connection error: %s" % e)
		# Translators: spoken when the computer appears to be offline.
		raise ApiError(_("Could not reach the AI service. Check your internet connection."))
	except json.JSONDecodeError:
		raise ApiError(_("The AI service sent an invalid response."))


def listModels(baseURL, apiKey, timeout=30):
	"""Return the list of model ID strings offered by the server."""
	url = baseURL.rstrip("/") + "/models"
	data = _request(url, apiKey, None, timeout)
	items = data.get("data", data) if isinstance(data, dict) else data
	ids = []
	for item in items or []:
		mid = item.get("id") if isinstance(item, dict) else item
		if isinstance(mid, str) and mid:
			ids.append(mid)
	if not ids:
		raise ApiError(_("The server returned no models."))
	return ids


def _extractText(message):
	"""Message content may be a plain string or a list of typed parts."""
	content = message.get("content")
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		parts = []
		for part in content:
			if isinstance(part, dict) and part.get("type") == "text":
				parts.append(part.get("text", ""))
			elif isinstance(part, str):
				parts.append(part)
		return "\n".join(parts)
	return ""


def chatVision(baseURL, apiKey, model, prompt, imageBytes, mime="image/png", maxTokens=4000, timeout=90):
	"""Send one prompt plus one image; return the model's text reply."""
	if not apiKey or not apiKey.strip():
		# Translators: spoken when no API key has been configured yet.
		raise ApiError(_("No API key configured. Open NVDA settings, PiPiQ Vision Assist category, and enter your OpenCode API key."))
	b64 = base64.b64encode(imageBytes).decode("ascii")
	payload = {
		"model": model,
		"max_tokens": maxTokens,
		"messages": [
			{
				"role": "user",
				"content": [
					{"type": "text", "text": prompt},
					{"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}},
				],
			}
		],
	}
	url = baseURL.rstrip("/") + "/chat/completions"
	data = _request(url, apiKey, payload, timeout)
	try:
		choice = data["choices"][0]
		text = _extractText(choice.get("message") or {})
	except (KeyError, IndexError, TypeError):
		log.error("PiPiQ unexpected API response: %r" % str(data)[:500])
		raise ApiError(_("The AI service sent an unexpected response. Try a different model in settings."))
	if not text.strip():
		# Some reasoning models can exhaust the token budget before producing
		# a final answer, leaving empty content.
		raise ApiError(_("The model returned an empty answer. Try again, or pick a different model in settings."))
	return text
