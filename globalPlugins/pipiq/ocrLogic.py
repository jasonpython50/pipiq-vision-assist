# Assembles plain text from a Windows OCR lines-and-words result.
# Deliberately NVDA-free so it can be unit tested with system Python;
# the NVDA-dependent capture and recognition live in __init__.py.


def linesWordsToText(lines):
	"""Build plain text from OCR result data.

	``lines`` is the ``data`` attribute of NVDA's contentRecog
	LinesWordsResult: an iterable of lines, each line an iterable of word
	dicts carrying a "text" key. Words are joined with single spaces and
	lines with newlines; leading and trailing blank lines are dropped but
	interior blank lines are kept, so the text keeps its visual structure.
	"""
	out = []
	for line in lines or []:
		words = []
		for word in line:
			try:
				text = (word.get("text") or "").strip()
			except AttributeError:
				text = ""
			if text:
				words.append(text)
		out.append(" ".join(words))
	while out and not out[0]:
		out.pop(0)
	while out and not out[-1]:
		out.pop()
	return "\n".join(out)
