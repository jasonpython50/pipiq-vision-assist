# Analysis and phrasing for the form check command: given plain records
# describing a web form's fields, work out why a submit button may be
# dimmed and build the spoken and display reports.
# Deliberately NVDA-free (translation falls back to identity) for unit testing.

try:
	import addonHandler
	addonHandler.initTranslation()
except Exception:
	def _(s):
		return s


# A hint pulled from a field's description can be long boilerplate; anything
# longer than this is cut so the report stays listenable.
MAX_HINT_CHARS = 200


def _kindName(kind):
	return {
		# Translators: control type names used in form check reports.
		"edit": _("edit box"),
		"combo": _("combo box"),
		"list": _("list"),
		"checkbox": _("checkbox"),
		"radio": _("radio button"),
		"button": _("button"),
		"slider": _("slider"),
	}.get(kind, _("field"))


def _label(field):
	name = (field.get("name") or "").strip()
	if name:
		return name
	# Translators: stands in for a form field that has no label; {kind} is like "edit box".
	return _("An unlabeled {kind}").format(kind=_kindName(field.get("kind")))


def _withHint(sentence, field):
	hint = (field.get("error") or "").strip() or (field.get("description") or "").strip()
	hint = " ".join(hint.split())
	if not hint or hint == (field.get("name") or "").strip():
		return sentence
	shortened = False
	if len(hint) > MAX_HINT_CHARS:
		# Cut at a word boundary; an ellipsis would be silent in speech, so the
		# shortening is announced in words instead.
		hint = hint[:MAX_HINT_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:")
		shortened = True
	if not hint.endswith((".", "!", "?")):
		hint += "."
	if shortened:
		# Translators: appended to a form problem when the page's long help or error text was cut; {hint} is that text.
		return sentence + " " + _("The page says, shortened: {hint}").format(hint=hint)
	# Translators: appended to a form problem when the page provides help or error text; {hint} is that text.
	return sentence + " " + _("The page says: {hint}").format(hint=hint)


def _analyzeRadioGroup(group, members, issues):
	label = (group or "").strip() or (members[0].get("name") or "").strip()
	if not label:
		# Translators: stands in for a radio button group that has no label.
		label = _("An unlabeled group of radio buttons")
	required = any(m.get("required") for m in members)
	checked = any(m.get("checked") for m in members)
	if required and not checked:
		if len(members) > 1:
			issues.append(_withHint(
				# Translators: form problem; {label} is the group's label, {count} how many radio buttons it has.
				_("{label}: none of its {count} radio buttons is selected, and a choice is required.").format(
					label=label, count=len(members),
				),
				members[0],
			))
		else:
			issues.append(_withHint(
				# Translators: form problem for a single required radio button; {label} is its label.
				_("{label}, radio button, is required but not selected.").format(label=label),
				members[0],
			))
	elif any(m.get("invalid") for m in members):
		invalid = next(m for m in members if m.get("invalid"))
		issues.append(_withHint(
			# Translators: form problem; {label} is the label of a radio button group the page marks invalid.
			_("{label}: the page marks this radio button choice as invalid.").format(label=label),
			invalid,
		))


def _analyzeField(field, issues):
	kind = field.get("kind")
	label = _label(field)
	if kind == "checkbox":
		if field.get("required") and not field.get("checked"):
			issues.append(_withHint(
				# Translators: form problem; {label} is a checkbox's label.
				_("{label}, checkbox, is required but not checked.").format(label=label),
				field,
			))
		elif field.get("invalid"):
			issues.append(_withHint(
				_("{label}, checkbox, is marked as invalid by the page.").format(label=label),
				field,
			))
		return
	empty = not (field.get("value") or "").strip()
	problems = []
	if field.get("required") and empty:
		# Translators: form problem clause for a required field with nothing typed in it.
		problems.append(_("required but still empty"))
	if field.get("invalid"):
		if empty and not field.get("required"):
			# Translators: form problem clause for an empty field the page marks invalid.
			problems.append(_("empty and marked as invalid by the page"))
		elif not empty:
			# Translators: form problem clause for a filled field the page still marks invalid.
			problems.append(_("marked as invalid by the page, so what is typed in it may be in the wrong format"))
		else:
			# Translators: form problem clause for a field the page marks invalid.
			problems.append(_("marked as invalid by the page"))
	if not problems:
		return
	if len(problems) == 1:
		problemsText = problems[0]
	else:
		# Translators: joins two form problem clauses, as in "required but still empty and marked as invalid".
		problemsText = _("{first} and {second}").format(first=problems[0], second=problems[1])
	sentence = _("{label}, {kind}, is {problems}.").format(
		label=label,
		kind=_kindName(kind),
		problems=problemsText,
	)
	issues.append(_withHint(sentence, field))


def analyzeFields(fields):
	"""Split field records into dimmed button names and problem sentences.

	fields: dicts in document order with keys kind, name, value, required,
	invalid, checked, disabled, description, error, group.
	Returns (disabledButtons, issues): lists of plain strings.
	"""
	disabledButtons = []
	issues = []
	i = 0
	n = len(fields)
	while i < n:
		field = fields[i]
		kind = field.get("kind")
		if kind == "radio":
			# Consecutive radio buttons in the same group are one choice.
			group = (field.get("group") or "").strip()
			members = []
			while (
				i < n
				and fields[i].get("kind") == "radio"
				and (fields[i].get("group") or "").strip() == group
			):
				members.append(fields[i])
				i += 1
			_analyzeRadioGroup(group, members, issues)
			continue
		if kind == "button":
			if field.get("disabled"):
				name = (field.get("name") or "").strip()
				# Translators: stands in for a dimmed button that has no label.
				disabledButtons.append(name or _("Unlabeled button"))
		elif not field.get("disabled"):
			# A dimmed field cannot be filled, so it is never reported as a task.
			_analyzeField(field, issues)
		i += 1
	return disabledButtons, issues


def _numbered(issues):
	return ["%d: %s" % (i + 1, s) for i, s in enumerate(issues)]


def buildFormReport(disabledButtons, issues, totalFields, scopeLabel):
	"""Build the (spoken, display) report texts.

	disabledButtons and issues come from analyzeFields; totalFields is how
	many fields were checked; scopeLabel is a localized phrase like
	"the current form" or "this page".
	"""
	if len(disabledButtons) == 1:
		# Translators: start of the form check report; {name} is the dimmed button's label.
		buttonSentence = _("The {name} button is dimmed and cannot be pressed yet.").format(
			name=disabledButtons[0],
		)
	elif disabledButtons:
		# Translators: start of the form check report; {count} buttons named {names} are dimmed.
		buttonSentence = _("{count} buttons are dimmed: {names}.").format(
			count=len(disabledButtons), names=", ".join(disabledButtons),
		)
	else:
		buttonSentence = ""

	if issues:
		if len(issues) == 1:
			# Translators: form check summary when exactly one problem was found.
			countSentence = _("After checking {total} fields in {scope}, 1 likely reason was found.").format(
				total=totalFields, scope=scopeLabel,
			)
		else:
			# Translators: form check summary; {count} problems found among {total} fields in {scope}.
			countSentence = _("After checking {total} fields in {scope}, {count} likely reasons were found.").format(
				total=totalFields, scope=scopeLabel, count=len(issues),
			)
		if buttonSentence:
			spoken = " ".join([buttonSentence, countSentence] + _numbered(issues))
		else:
			spoken = " ".join(
				[
					# Translators: form check lead-in when problems exist but no button is reported dimmed.
					_("No dimmed button was found, but there are things the form still needs."),
					countSentence,
				]
				+ _numbered(issues)
			)
	elif buttonSentence:
		spoken = buttonSentence + " " + _(
			# Translators: form check result when a button is dimmed but every field looks complete.
			"However, all {total} fields in {scope} look complete: no empty required fields and nothing marked invalid. "
			"The page may want something it does not expose to screen readers, such as a CAPTCHA, a verification code you must request first, "
			"or it may only wake up after you leave the last field, so try pressing Tab. "
			"You can also describe the screen with the Vision assist S command to look for visual error messages."
		).format(total=totalFields, scope=scopeLabel)
	else:
		spoken = _(
			# Translators: form check result when nothing is wrong and nothing is dimmed.
			"Nothing looks missing. {total} fields were checked in {scope}: no dimmed buttons, no empty required fields, and nothing marked invalid. "
			"If the page still refuses to continue, describe the screen with the Vision assist S command to look for visual hints."
		).format(total=totalFields, scope=scopeLabel)

	lines = []
	# Translators: first line of the form check window; {total} fields were checked in {scope}.
	lines.append(_("Form check: {total} fields checked in {scope}.").format(total=totalFields, scope=scopeLabel))
	if disabledButtons:
		# Translators: line of the form check window listing the dimmed buttons.
		lines.append(_("Dimmed buttons: {names}").format(names=", ".join(disabledButtons)))
	else:
		# Translators: line of the form check window when no button is dimmed.
		lines.append(_("Dimmed buttons: none found."))
	if issues:
		# Translators: heading line for the numbered problem list in the form check window.
		lines.append(_("What the form still needs:"))
		lines.extend("%d. %s" % (i + 1, s) for i, s in enumerate(issues))
	else:
		# Translators: line of the form check window when no field problems were found.
		lines.append(_("No empty required fields and nothing marked invalid."))
	display = "\n".join(lines)
	return spoken, display
