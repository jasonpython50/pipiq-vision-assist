# PiPiQ Vision Assist: QR code locator and AI image describer for NVDA.
# Entry gesture NVDA+Shift+0 opens a one-keystroke command layer; every
# command is also exposed as a separately bindable script.

import os
import threading
import time

import wx

import addonHandler
import api
import config
import controlTypes
import globalPluginHandler
import globalVars
import gui
import languageHandler
import textInfos
import tones
import ui
import winUser
from logHandler import log
from scriptHandler import script

from . import apiClient, formLogic, ocrLogic, qrLogic, screenGrab
from .knownModels import RECOMMENDED_DESCRIPTION_MODEL, RECOMMENDED_QR_MODEL
from .settingsPanel import PipiqSettingsPanel

addonHandler.initTranslation()

config.conf.spec["pipiq"] = {
	"apiKey": "string(default='')",
	"baseURL": "string(default='%s')" % apiClient.DEFAULT_BASE_URL,
	"qrModel": "string(default='%s')" % RECOMMENDED_QR_MODEL,
	"descModel": "string(default='%s')" % RECOMMENDED_DESCRIPTION_MODEL,
	"detailLevel": "string(default='brief')",
	"contentMode": "string(default='auto')",
	"customPrompt": "string(default='')",
	"timeout": "integer(default=90,min=15,max=300)",
	"maxImageDim": "integer(default=1568,min=512,max=3072)",
	"moveMouseToQR": "boolean(default=True)",
	"resultsPresentation": "string(default='auto')",
	"progressBeeps": "boolean(default=True)",
	"respondInUILanguage": "boolean(default=True)",
	"showOnlyVisionModels": "boolean(default=True)",
}

LONG_RESULT_CHARS = 450


def _conf():
	return config.conf["pipiq"]


def _screenCurtainActive():
	try:
		import vision
		for providerInfo in vision.handler.getActiveProviderInfos():
			if getattr(providerInfo, "providerId", "") == "screenCurtain":
				return True
	except Exception:
		pass
	return False


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# Translators: name of the addon's category in the Input gestures dialog.
	scriptCategory = _("PiPiQ Vision Assist")

	def __init__(self):
		super().__init__()
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(PipiqSettingsPanel)
		self._layerActive = False
		self._generation = 0  # bumping this invalidates any in-flight request
		self._inFlight = False
		self._lastResult = None  # (title, text)

	def terminate(self):
		self._generation += 1
		self._inFlight = False
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(PipiqSettingsPanel)
		except ValueError:
			pass
		super().terminate()

	# ------------------------------------------------------------------
	# Command layer

	def getScript(self, gesture):
		if not self._layerActive:
			return super().getScript(gesture)
		boundScript = super().getScript(gesture)
		if boundScript:
			return boundScript
		return self.script_layerUnknown

	def _enterLayer(self):
		self.bindGestures(self.__layerGestures)
		self._layerActive = True
		tones.beep(660, 40)
		# Translators: spoken when the command layer opens. Keep it short; H reads full help.
		ui.message(_("Vision assist. Press H for help."))

	def _exitLayer(self):
		if self._layerActive:
			self._layerActive = False
			self.clearGestureBindings()
			self.bindGestures(self.__gestures)

	@script(
		# Translators: input help for the layer entry command.
		description=_("Opens the Vision assist layer. Then press Q to find a QR code on the screen, W in the current window, O to describe the navigator object, S the screen, C the clipboard image, G to choose an image on the web page to describe, F to check why a form's button is dimmed, P to check what a screenshot would capture, T to take a screenshot, X to read the text with Windows OCR, D to read a PDF or image file, R to repeat the last result, B to open it in a window, Escape to cancel."),
	)
	def script_visionLayer(self, gesture):
		if self._layerActive:
			self._exitLayer()
			# Translators: spoken when the command layer is closed by pressing the entry key again.
			ui.message(_("Vision assist closed."))
			return
		self._enterLayer()

	_MODIFIER_KEY_NAMES = frozenset((
		"shift", "leftShift", "rightShift",
		"control", "leftControl", "rightControl",
		"alt", "leftAlt", "rightAlt",
		"windows", "leftWindows", "rightWindows",
		"NVDA", "capsLock", "insert", "numpadInsert",
	))

	def script_layerUnknown(self, gesture):
		# A lone modifier press is dispatched as a gesture too; it must not
		# knock the user out of the layer.
		if getattr(gesture, "mainKeyName", "") in self._MODIFIER_KEY_NAMES:
			return
		self._exitLayer()
		tones.beep(330, 60)

	@script(description=_("Speaks the Vision assist layer commands."))
	def script_layerHelp(self, gesture):
		self._exitLayer()
		ui.message(
			# Translators: full help for the command layer, spoken on H. Does not
			# name the entry gesture, since the user may have reassigned it.
			_("Vision assist commands: Q, find QR code on the whole screen. W, find QR code in the current window. O, describe the navigator object. S, describe the whole screen. C, describe the image on the clipboard. G, choose an image on the web page to describe. F, check the form on the web page: reports dimmed buttons and which required fields are still empty or invalid. P, check what a screenshot would capture. T, take a screenshot of the navigator object; Shift plus T, of the current window; Control plus T, of the whole screen. X, read the text of the navigator object with Windows OCR, offline; Shift plus X, of the current window; Control plus X, of the whole screen. D, read the text of a PDF or image file selected in File Explorer, offline. R, repeat the last result. B, show the last result in a browseable window. Escape, cancel. Press the Vision assist gesture again first, then one of these letters."),
		)

	@script(description=_("Cancels the running Vision assist request."))
	def script_cancel(self, gesture):
		self._exitLayer()
		if self._inFlight:
			self._cancelInFlight()
		else:
			# Translators: spoken when Escape is pressed with nothing to cancel.
			ui.message(_("Nothing to cancel."))

	# ------------------------------------------------------------------
	# QR code location

	@script(
		# Translators: input help for the whole-screen QR command.
		description=_("Finds a QR code on the whole screen and tells you where to point your phone camera."),
	)
	def script_findQRScreen(self, gesture):
		self._exitLayer()
		rect = screenGrab.getVirtualScreenRect()
		# Translators: used in position reports: "...of the screen".
		self._startQrTask(rect, _("the screen"), reportMonitor=True)

	@script(
		# Translators: input help for the current-window QR command.
		description=_("Finds a QR code in the current foreground window and tells you where to point your phone camera."),
	)
	def script_findQRWindow(self, gesture):
		self._exitLayer()
		rect = self._foregroundRect()
		if not rect:
			# Translators: spoken when the foreground window position cannot be determined.
			ui.message(_("Could not determine the current window's position."))
			return
		# Translators: used in position reports: "...of the window".
		self._startQrTask(rect, _("the window"), reportMonitor=False)

	def _foregroundRect(self):
		try:
			location = api.getForegroundObject().location
			if location and location.width > 0 and location.height > 0:
				return screenGrab.intersectWithVirtualScreen(
					location.left, location.top, location.width, location.height,
				)
		except Exception:
			log.error("PiPiQ: failed to get foreground rect", exc_info=True)
		return None

	def _startQrTask(self, rect, scopeLabel, reportMonitor):
		if not self._preflight():
			return
		try:
			png, outW, outH, isBlack = screenGrab.captureRect(*rect, maxDim=int(_conf()["maxImageDim"]))
		except screenGrab.CaptureError:
			log.error("PiPiQ: screen capture failed", exc_info=True)
			# Translators: spoken when taking the screenshot fails.
			ui.message(_("Could not capture the screen."))
			return
		if isBlack:
			ui.message(self._blackCaptureMessage())
			return
		conf = _conf()
		model = conf["qrModel"]
		prompt = qrLogic.buildQrPrompt()
		timeout = int(conf["timeout"])

		def work():
			text = apiClient.chatVision(
				conf["baseURL"], conf["apiKey"], model, prompt, png,
				maxTokens=8000, timeout=timeout,
			)
			return qrLogic.parseQrResponse(qrLogic.stripReasoning(text), outW, outH)

		def onSuccess(parsed):
			self._handleQrResult(parsed, rect, scopeLabel, reportMonitor)

		# Translators: spoken while the QR detection request is running.
		self._runAsync(work, onSuccess, _("Looking for a QR code..."))

	def _handleQrResult(self, parsed, rect, scopeLabel, reportMonitor):
		if parsed is None:
			tones.beep(300, 90)
			# Translators: spoken when the AI reply could not be understood.
			ui.message(_("The AI response could not be understood. Please try again."))
			return
		if not parsed["found"]:
			# Analysis succeeded with a negative outcome, so the success earcon applies.
			tones.beep(880, 60)
			# Translators: spoken when no QR code is visible; {scope} is "the screen" or "the window".
			ui.message(_("No QR code was found on {scope}. If one should be there, bring it into view and try again.").format(scope=scopeLabel))
			return
		codes = parsed["codes"]
		left, top, width, height = rect
		l, t, r, b = codes[0]
		centerX = int(left + (l + r) / 2.0 * width)
		centerY = int(top + (t + b) / 2.0 * height)
		monitorPhrase = ""
		if reportMonitor:
			index, count = screenGrab.monitorIndexForPoint(centerX, centerY)
			if count > 1:
				# Translators: inserted into the QR report on multi-monitor setups; {number} is the monitor number.
				monitorPhrase = " " + _("on monitor {number}").format(number=index)
		mouseMoved = False
		if _conf()["moveMouseToQR"]:
			try:
				winUser.setCursorPos(centerX, centerY)
				mouseMoved = True
			except Exception:
				log.error("PiPiQ: could not move mouse", exc_info=True)
		message = qrLogic.describeQrResult(codes, scopeLabel, monitorPhrase, mouseMoved)
		# Translators: title of the window showing the last QR result.
		self._deliverResult(_("QR code location"), message, forceSpeakOnly=True)

	# ------------------------------------------------------------------
	# Image description

	@script(
		# Translators: input help for describing the current navigator object.
		description=_("Describes the image at the navigator object using AI."),
	)
	def script_describeNavigator(self, gesture):
		self._exitLayer()
		rect = self._navigatorRect()
		if not rect:
			# Translators: spoken when the navigator object has no usable screen area.
			ui.message(_("The current navigator object has no visible area to capture. Try describing the whole screen with S instead."))
			return
		# Translators: title of the window showing an object description.
		self._startDescribeTask(rect, _("Object description"))

	@script(
		# Translators: input help for describing the whole screen.
		description=_("Describes the whole screen using AI."),
	)
	def script_describeScreen(self, gesture):
		self._exitLayer()
		rect = screenGrab.getVirtualScreenRect()
		# Translators: title of the window showing a screen description.
		self._startDescribeTask(rect, _("Screen description"))

	@script(
		# Translators: input help for describing the clipboard image.
		description=_("Describes the image on the clipboard using AI. Works with copied images and copied image files."),
	)
	def script_describeClipboard(self, gesture):
		self._exitLayer()
		if not self._preflight(needsScreen=False):
			return
		try:
			result = screenGrab.getClipboardImage(maxDim=int(_conf()["maxImageDim"]))
		except screenGrab.CaptureError as e:
			log.error("PiPiQ: clipboard image error: %s" % e)
			# Translators: spoken when the clipboard holds an image the addon cannot convert.
			ui.message(_("The clipboard image is in a format that cannot be processed."))
			return
		if not result:
			# Translators: spoken when the clipboard has no image at all.
			ui.message(_("There is no image on the clipboard. Copy an image or an image file first."))
			return
		imageBytes, mime = result
		# Translators: title of the window showing a clipboard image description.
		self._sendDescribeRequest(imageBytes, mime, _("Clipboard image description"))

	def _navigatorRect(self):
		for getter in (api.getNavigatorObject, api.getFocusObject):
			try:
				location = getter().location
			except Exception:
				location = None
			if location and location.width > 0 and location.height > 0:
				rect = screenGrab.intersectWithVirtualScreen(
					location.left, location.top, location.width, location.height,
				)
				if rect:
					return rect
		return None

	def _startDescribeTask(self, rect, title):
		if not self._preflight():
			return
		try:
			png, _w, _h, isBlack = screenGrab.captureRect(*rect, maxDim=int(_conf()["maxImageDim"]))
		except screenGrab.CaptureError:
			log.error("PiPiQ: screen capture failed", exc_info=True)
			ui.message(_("Could not capture the screen."))
			return
		if isBlack:
			ui.message(self._blackCaptureMessage())
			return
		self._sendDescribeRequest(png, "image/png", title)

	@staticmethod
	def _blackCaptureMessage():
		# Translators: spoken when the screenshot came out entirely black.
		return _("The captured image is completely black. If NVDA's screen curtain or a privacy filter is active, turn it off and try again.")

	def _sendDescribeRequest(self, imageBytes, mime, title):
		conf = _conf()
		language = languageHandler.getLanguage() if conf["respondInUILanguage"] else None
		prompt = qrLogic.buildDescriptionPrompt(
			conf["detailLevel"], conf["customPrompt"], language, conf["contentMode"],
		)
		model = conf["descModel"]
		timeout = int(conf["timeout"])

		def work():
			# A large budget so reasoning models always reach their final
			# answer; the thinking traces are stripped before presentation.
			return apiClient.chatVision(
				conf["baseURL"], conf["apiKey"], model, prompt, imageBytes,
				mime=mime, maxTokens=8000, timeout=timeout,
			)

		extractMode = conf["contentMode"] == "extract"

		def onSuccess(text):
			text = qrLogic.stripReasoning(text)
			if not text:
				tones.beep(300, 90)
				# Translators: spoken when the model reply contained no usable answer.
				ui.message(_("The model returned an empty answer. Try again, or pick a different model in settings."))
				return
			if extractMode:
				# Exact transcription: the markdown sanitizers would corrupt
				# literal characters (underscores in emails, number signs,
				# table pipes) and column spacing, so only trim.
				self._deliverResult(title, text.strip())
			else:
				self._deliverResult(
					title,
					qrLogic.sanitizeForSpeech(text),
					displayText=qrLogic.sanitizeForDisplay(text),
				)

		# Translators: spoken while an image description request is running.
		self._runAsync(work, onSuccess, _("Describing the image..."))

	# ------------------------------------------------------------------
	# Web page images

	@script(
		# Translators: input help for the web image chooser command.
		description=_("Lists all images on the current web page so you can choose one to describe with AI."),
	)
	def script_listWebImages(self, gesture):
		self._exitLayer()
		try:
			ti = api.getFocusObject().treeInterceptor
		except Exception:
			ti = None
		if not ti or not getattr(ti, "isReady", False) or not hasattr(ti, "_iterNodesByType"):
			# Translators: spoken when the image list command is used outside a web page.
			ui.message(_("This command works on web pages and other browse mode documents. Move focus to a web page and try again."))
			return
		try:
			items = list(ti._iterNodesByType("graphic"))
		except NotImplementedError:
			items = []
		except Exception:
			log.error("PiPiQ: iterating page graphics failed", exc_info=True)
			items = []
		if not items:
			# Translators: spoken when the current page contains no images.
			ui.message(_("No images were found on this page."))
			return
		# The dialog must not open from inside the gesture handler.
		wx.CallAfter(self._showImageChooser, items)

	def _showImageChooser(self, items):
		choices = []
		for i, item in enumerate(items):
			try:
				label = (item.label or "").strip()
			except Exception:
				label = ""
			label = " ".join(label.split())
			if len(label) > 100:
				label = label[:100] + "…"
			if not label:
				# Translators: list entry for an image that has no alternative text.
				label = _("Unlabeled image")
			choices.append("%d. %s" % (i + 1, label))
		gui.mainFrame.prePopup()
		try:
			dlg = wx.SingleChoiceDialog(
				gui.mainFrame,
				# Translators: prompt of the dialog listing the images on the page.
				_("Choose the image to describe and press Enter:"),
				# Translators: title of the dialog listing the images on the page; {count} is how many.
				_("Images on this page ({count})").format(count=len(items)),
				choices,
			)
			result = dlg.ShowModal()
			selection = dlg.GetSelection()
			dlg.Destroy()
		finally:
			gui.mainFrame.postPopup()
		if result != wx.ID_OK or selection < 0:
			return
		item = items[selection]
		try:
			item.moveTo()  # put the browse mode cursor on the image
		except Exception:
			log.error("PiPiQ: could not move to the chosen image", exc_info=True)
		try:
			obj = item.textInfo.NVDAObjectAtStart
		except Exception:
			obj = None
		if obj is None:
			ui.message(_("The chosen image could not be located on screen."))
			return
		try:
			obj.scrollIntoView()
		except Exception:
			pass
		# Give the browser a moment to finish scrolling before measuring where
		# the image ended up.
		label = choices[selection].split(". ", 1)[-1]
		wx.CallLater(700, self._describeChosenImage, obj, label)

	def _describeChosenImage(self, obj, label):
		try:
			location = obj.location
		except Exception:
			location = None
		rect = None
		if location and location.width > 0 and location.height > 0:
			rect = screenGrab.intersectWithVirtualScreen(
				location.left, location.top, location.width, location.height,
			)
		if not rect:
			# Translators: spoken when the chosen image could not be scrolled into view.
			ui.message(_("The image could not be brought into view for capture. Scroll until it is visible, place the navigator on it, and press O instead."))
			return
		# Translators: title of the window describing an image chosen from the page list; {name} is its label.
		self._startDescribeTask(rect, _("Image description: {name}").format(name=label))

	# ------------------------------------------------------------------
	# Form check

	@script(
		# Translators: input help for the form check command.
		description=_("Checks the form on the current web page and reports why its submit button may be dimmed: which required fields are still empty, which checkboxes are unchecked, and what the page marks as invalid."),
	)
	def script_checkForm(self, gesture):
		self._exitLayer()
		try:
			ti = api.getFocusObject().treeInterceptor
		except Exception:
			ti = None
		if not ti or not getattr(ti, "isReady", False) or not hasattr(ti, "_iterNodesByType"):
			# Translators: spoken when the form check command is used outside a web page.
			ui.message(_("This command works on web pages and other browse mode documents. Move focus to the page with the form and try again."))
			return
		try:
			items = list(ti._iterNodesByType("formField"))
		except NotImplementedError:
			items = []
		except Exception:
			log.error("PiPiQ: iterating form fields failed", exc_info=True)
			items = []
		if not items:
			# Translators: spoken when the form check finds nothing to check.
			ui.message(_("No form fields were found on this page."))
			return
		scopeInfo, scopeLabel = self._formScope(ti)
		records = []
		for item in items:
			if scopeInfo is not None and not self._itemWithin(item, scopeInfo):
				continue
			obj = self._itemObject(item)
			if obj is not None:
				records.append(self._fieldRecord(obj))
		if not records:
			# The form under the caret yielded nothing usable; check the whole page.
			# Translators: used in the form check report when the whole page was checked.
			scopeLabel = _("this page")
			records = [
				self._fieldRecord(obj)
				for obj in (self._itemObject(item) for item in items)
				if obj is not None
			]
		disabledButtons, issues = formLogic.analyzeFields(records)
		spoken, display = formLogic.buildFormReport(disabledButtons, issues, len(records), scopeLabel)
		# No earcon: this is an instant local check, not a finished AI request.
		# Translators: title of the window showing the last form check report.
		self._deliverResult(_("Form check"), spoken, displayText=display, playEarcon=False)

	def _formScope(self, ti):
		"""Range of the form containing the caret, or (None, label) to check the whole page."""
		try:
			caretInfo = ti.makeTextInfo(textInfos.POSITION_CARET)
			obj = caretInfo.NVDAObjectAtStart
			root = ti.rootNVDAObject
			for _step in range(50):
				if obj is None or obj == root:
					break
				if obj.role == controlTypes.Role.FORM:
					info = ti.makeTextInfo(obj)
					name = (obj.name or "").strip()
					if name:
						# Translators: names the checked form in the form check report; {name} is the form's label.
						return info, _("the {name} form").format(name=name)
					# Translators: used in the form check report for a form without a label.
					return info, _("the current form")
				obj = obj.parent
		except Exception:
			pass
		return None, _("this page")

	@staticmethod
	def _itemObject(item):
		try:
			obj = getattr(item, "obj", None)
			if obj is not None:
				return obj
			return item.textInfo.NVDAObjectAtStart
		except Exception:
			return None

	@staticmethod
	def _itemWithin(item, scopeInfo):
		try:
			info = item.textInfo
			return (
				info.compareEndPoints(scopeInfo, "startToStart") >= 0
				and info.compareEndPoints(scopeInfo, "endToEnd") <= 0
			)
		except Exception:
			# When the range cannot be compared, keep the field rather than lose it.
			return True

	_ROLE_KINDS = None

	@classmethod
	def _roleKind(cls, role):
		if cls._ROLE_KINDS is None:
			Role = controlTypes.Role
			kinds = {
				Role.EDITABLETEXT: "edit",
				Role.PASSWORDEDIT: "edit",
				Role.SPINBUTTON: "edit",
				Role.COMBOBOX: "combo",
				Role.LIST: "list",
				Role.CHECKBOX: "checkbox",
				Role.RADIOBUTTON: "radio",
				Role.BUTTON: "button",
				Role.TOGGLEBUTTON: "button",
				Role.SLIDER: "slider",
			}
			# Roles that may not exist on the oldest supported NVDA.
			for roleName, kind in (
				("SWITCH", "checkbox"),
				("LISTBOX", "list"),
				("MENUBUTTON", "button"),
				("DATEEDITOR", "edit"),
				("TIMEEDITOR", "edit"),
			):
				r = getattr(Role, roleName, None)
				if r is not None:
					kinds[r] = kind
			cls._ROLE_KINDS = kinds
		return cls._ROLE_KINDS.get(role, "field")

	def _fieldRecord(self, obj):
		State = controlTypes.State
		try:
			states = obj.states
		except Exception:
			states = set()
		try:
			kind = self._roleKind(obj.role)
		except Exception:
			kind = "field"
		try:
			name = (obj.name or "").strip()
		except Exception:
			name = ""
		try:
			placeholder = ((getattr(obj, "IA2Attributes", None) or {}).get("placeholder") or "").strip()
		except Exception:
			placeholder = ""
		value = ""
		if kind not in ("checkbox", "radio", "button"):
			try:
				value = (obj.value or "").strip()
			except Exception:
				value = ""
			if not value and kind == "edit":
				# Multi-line and rich edits often expose their text, not a value.
				try:
					value = (obj.makeTextInfo(textInfos.POSITION_ALL).text or "").strip()
				except Exception:
					value = ""
			if value and placeholder and value == placeholder:
				# A visible placeholder is not typed content.
				value = ""
		try:
			description = (obj.description or "").strip()
		except Exception:
			description = ""
		error = ""
		try:
			error = (getattr(obj, "errorMessage", None) or "").strip()
		except Exception:
			pass
		group = self._radioGroupName(obj) if kind == "radio" else ""
		return {
			"kind": kind,
			"name": name or placeholder,
			"value": value,
			"required": State.REQUIRED in states,
			"invalid": State.INVALID_ENTRY in states,
			"checked": State.CHECKED in states or State.HALFCHECKED in states or State.PRESSED in states,
			"disabled": State.UNAVAILABLE in states,
			"description": description,
			"error": error,
			"group": group,
		}

	@staticmethod
	def _radioGroupName(obj):
		try:
			parent = obj.parent
			for _step in range(6):
				if parent is None:
					break
				if parent.role == controlTypes.Role.GROUPING:
					return (parent.name or "").strip()
				parent = parent.parent
		except Exception:
			pass
		return ""

	# ------------------------------------------------------------------
	# Taking screenshots

	@script(
		# Translators: input help for taking a screenshot of the navigator object.
		description=_("Takes a screenshot of the navigator object, saves it as a PNG file in your Pictures folder, and copies it to the clipboard."),
	)
	def script_screenshotObject(self, gesture):
		self._exitLayer()
		rect = self._navigatorRect()
		if not rect:
			ui.message(_("The current navigator object has no visible area to capture. Try describing the whole screen with S instead."))
			return
		# Translators: names what was captured in the screenshot announcement.
		self._startScreenshotTask(rect, _("the navigator object"))

	@script(
		# Translators: input help for taking a screenshot of the current window.
		description=_("Takes a screenshot of the current foreground window, saves it as a PNG file in your Pictures folder, and copies it to the clipboard."),
	)
	def script_screenshotWindow(self, gesture):
		self._exitLayer()
		rect = self._foregroundRect()
		if not rect:
			ui.message(_("Could not determine the current window's position."))
			return
		# Translators: names what was captured in the screenshot announcement.
		self._startScreenshotTask(rect, _("the current window"))

	@script(
		# Translators: input help for taking a screenshot of the whole screen.
		description=_("Takes a screenshot of the whole screen, saves it as a PNG file in your Pictures folder, and copies it to the clipboard."),
	)
	def script_screenshotScreen(self, gesture):
		self._exitLayer()
		rect = screenGrab.getVirtualScreenRect()
		# Translators: names what was captured in the screenshot announcement.
		self._startScreenshotTask(rect, _("the whole screen"))

	def _startScreenshotTask(self, rect, subjectLabel):
		if self._inFlight:
			self._cancelInFlight()
			return
		if _screenCurtainActive():
			ui.message(_("Screen curtain is active, so the screen appears black and cannot be captured. Turn off screen curtain and try again."))
			return
		try:
			# Full resolution: screenshots are for people and other apps, so the
			# AI upload size limit does not apply.
			rgb, width, height, isBlack = screenGrab.captureRgb(*rect)
		except screenGrab.CaptureError:
			log.error("PiPiQ: screenshot capture failed", exc_info=True)
			ui.message(_("Could not capture the screen."))
			return
		if isBlack:
			ui.message(self._blackCaptureMessage())
			return

		def work():
			# PNG encoding of a full-resolution screen takes long enough to
			# stutter speech, so it runs off the main thread.
			return screenGrab.saveScreenshot(rgb, width, height)

		def onSuccess(path):
			copied = True
			try:
				screenGrab.copyImageToClipboard(rgb, width, height)
			except Exception:
				log.error("PiPiQ: could not copy screenshot to clipboard", exc_info=True)
				copied = False
			fileName = os.path.basename(path)
			if copied:
				# Translators: screenshot success announcement. {subject} is what was captured,
				# {file} the file name; the folder is <Pictures>\PiPiQ Screenshots.
				message = _("Screenshot of {subject} copied to the clipboard and saved as {file} in the PiPiQ Screenshots folder inside your Pictures folder. {width} by {height} pixels.").format(
					subject=subjectLabel, file=fileName, width=width, height=height,
				)
			else:
				# Translators: screenshot announcement when the clipboard copy failed but the file was saved.
				message = _("Screenshot of {subject} saved as {file} in the PiPiQ Screenshots folder inside your Pictures folder. {width} by {height} pixels. It could not be copied to the clipboard.").format(
					subject=subjectLabel, file=fileName, width=width, height=height,
				)
			# Translators: title of the window showing the last screenshot report.
			self._deliverResult(_("Screenshot"), message, forceSpeakOnly=True)

		# Translators: spoken while the screenshot is being saved.
		self._runAsync(work, onSuccess, _("Taking a screenshot..."))

	# ------------------------------------------------------------------
	# Windows OCR text recognition

	@script(
		# Translators: input help for reading the navigator object's text with Windows OCR.
		description=_("Reads the text of the navigator object using Windows OCR. Works offline, no API key needed."),
	)
	def script_ocrObject(self, gesture):
		self._exitLayer()
		rect = self._navigatorRect()
		if not rect:
			ui.message(_("The current navigator object has no visible area to capture. Try describing the whole screen with S instead."))
			return
		# Translators: names what was read in OCR results and messages.
		self._startOcrTask(rect, _("the navigator object"))

	@script(
		# Translators: input help for reading the current window's text with Windows OCR.
		description=_("Reads the text of the current foreground window using Windows OCR. Works offline, no API key needed."),
	)
	def script_ocrWindow(self, gesture):
		self._exitLayer()
		rect = self._foregroundRect()
		if not rect:
			ui.message(_("Could not determine the current window's position."))
			return
		# Translators: names what was read in OCR results and messages.
		self._startOcrTask(rect, _("the current window"))

	@script(
		# Translators: input help for reading the whole screen's text with Windows OCR.
		description=_("Reads the text of the whole screen using Windows OCR. Works offline, no API key needed."),
	)
	def script_ocrScreen(self, gesture):
		self._exitLayer()
		rect = screenGrab.getVirtualScreenRect()
		# Translators: names what was read in OCR results and messages.
		self._startOcrTask(rect, _("the whole screen"))

	def _startOcrTask(self, rect, subjectLabel):
		if self._inFlight:
			self._cancelInFlight()
			return
		if _screenCurtainActive():
			ui.message(_("Screen curtain is active, so the screen appears black and cannot be analyzed. Turn off screen curtain and try again."))
			return
		if not self._ocrEngineAvailable():
			return
		try:
			from contentRecog import RecogImageInfo, uwpOcr
			import screenBitmap
		except Exception:
			log.error("PiPiQ: Windows OCR modules unavailable", exc_info=True)
			ui.message(_("Windows OCR is not available on this computer."))
			return
		try:
			# Uses the recognition language from NVDA's Windows OCR settings.
			recognizer = uwpOcr.UwpOcr()
		except Exception:
			log.error("PiPiQ: could not create the Windows OCR recognizer", exc_info=True)
			# Translators: spoken when the Windows OCR engine exists but fails to start.
			ui.message(_("Windows OCR could not be started. Check that an OCR language is installed in Windows settings, under Language."))
			return
		left, top, width, height = rect
		try:
			imgInfo = RecogImageInfo.createFromRecognizer(left, top, width, height, recognizer)
		except ValueError:
			# Translators: spoken when the area is below the OCR engine's minimum size.
			ui.message(_("This area is too small for text recognition. Try the whole window with Shift plus X instead."))
			return
		try:
			# Capture on the main thread, like the other capture commands;
			# GDI capture is fast, recognition is what runs in the background.
			sb = screenBitmap.ScreenBitmap(imgInfo.recogWidth, imgInfo.recogHeight)
			pixels = sb.captureImage(left, top, width, height)
		except Exception:
			log.error("PiPiQ: OCR screen capture failed", exc_info=True)
			ui.message(_("Could not capture the screen."))
			return
		# Translators: title of the window showing recognized text; {subject} is what was read.
		title = _("Recognized text from {subject}").format(subject=subjectLabel)

		def work():
			done = threading.Event()
			holder = {}

			def onResult(result):
				holder["result"] = result
				done.set()

			recognizer.recognize(pixels, imgInfo, onResult)
			if not done.wait(30):
				# Translators: spoken when Windows OCR takes more than 30 seconds.
				raise apiClient.ApiError(_("Text recognition timed out. Please try again."))
			result = holder.get("result")
			if isinstance(result, Exception):
				log.error("PiPiQ: Windows OCR failed: %s" % result)
				# Translators: spoken when Windows OCR reports an error; details go to the NVDA log.
				raise apiClient.ApiError(_("Text recognition failed. See the NVDA log for details."))
			return ocrLogic.linesWordsToText(getattr(result, "data", None))

		def onSuccess(text):
			if not text:
				# Recognition succeeded with a negative outcome, so the success earcon applies.
				tones.beep(880, 60)
				# Translators: spoken when OCR finds no text; {subject} is what was read.
				ui.message(_("No text was recognized in {subject}. For stylized or photographed text, try the AI instead: O describes the navigator object and S the whole screen.").format(subject=subjectLabel))
				return
			# Exact recognized text: only trimmed, never reworded for speech.
			self._deliverResult(title, text.strip())

		# Translators: spoken while Windows OCR is running.
		self._runAsync(work, onSuccess, _("Recognizing text..."))

	def _ocrEngineAvailable(self):
		try:
			import winVersion
			if not winVersion.isUwpOcrAvailable():
				# Translators: spoken when the Windows OCR engine is missing from the system.
				ui.message(_("Windows OCR is not available on this computer."))
				return False
		except ImportError:
			pass  # let the recognition attempt decide
		return True

	# ------------------------------------------------------------------
	# Reading files with Windows OCR

	_FILE_OCR_IMAGE_EXTENSIONS = frozenset(("png", "jpg", "jpeg", "jfif", "bmp", "gif", "tif", "tiff"))
	_FILE_OCR_PAGE_LIMIT = 50
	_OCR_MAX_IMAGE_DIM = 2500  # Windows OCR rejects images beyond about 2600 pixels

	@script(
		# Translators: input help for reading a file with Windows OCR.
		description=_("Reads the text of the file selected in File Explorer using Windows OCR: PDF documents and image files. Opens a file chooser when no file is selected. Works offline, no API key needed."),
	)
	def script_ocrFile(self, gesture):
		self._exitLayer()
		if globalVars.appArgs.secure:
			# Translators: spoken when the file reading command is used on a secure screen.
			ui.message(_("This command is not available on secure screens."))
			return
		if self._inFlight:
			self._cancelInFlight()
			return
		if not self._ocrEngineAvailable():
			return
		path = self._selectedExplorerFile()
		if path:
			self._startFileOcrTask(path)
		else:
			# The dialog must not open from inside the gesture handler.
			wx.CallAfter(self._browseForOcrFile)

	def _selectedExplorerFile(self):
		"""Full path of the file focused in File Explorer or on the desktop, or None."""
		try:
			obj = api.getForegroundObject()
			if not (obj and obj.appModule and obj.appModule.appName == "explorer"):
				return None
		except Exception:
			return None
		try:
			from comtypes.client import CreateObject
			shell = CreateObject("shell.application")
			for window in shell.Windows():
				if window.hwnd == obj.windowHandle:
					path = str(window.Document.FocusedItem.path)
					return path if os.path.isfile(path) else None
		except Exception:
			log.error("PiPiQ: reading the Explorer selection failed", exc_info=True)
			return None
		# No Explorer window matched the foreground window: focus is on the
		# desktop itself, where items are named after their files.
		try:
			name = api.getDesktopObject().objectWithFocus().name
		except Exception:
			return None
		if not name:
			return None
		path = os.path.join(screenGrab.desktopDirectory(), name)
		return path if os.path.isfile(path) else None

	def _browseForOcrFile(self):
		imagePatterns = ";".join("*.%s" % ext for ext in sorted(self._FILE_OCR_IMAGE_EXTENSIONS))
		patterns = "*.pdf;" + imagePatterns
		wildcard = "%s (%s)|%s|%s (*.*)|*.*" % (
			# Translators: file type filter name in the file chooser.
			_("PDF and image files"), patterns, patterns,
			# Translators: file type filter name in the file chooser.
			_("All files"),
		)
		gui.mainFrame.prePopup()
		try:
			dlg = wx.FileDialog(
				gui.mainFrame,
				# Translators: title of the dialog asking which file to read.
				_("Choose the file to read"),
				wildcard=wildcard,
				style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
			)
			result = dlg.ShowModal()
			path = dlg.GetPath()
			dlg.Destroy()
		finally:
			gui.mainFrame.postPopup()
		if result == wx.ID_OK and path:
			self._startFileOcrTask(path)

	def _startFileOcrTask(self, path):
		ext = os.path.splitext(path)[1].lower().lstrip(".")
		if ext != "pdf" and ext not in self._FILE_OCR_IMAGE_EXTENSIONS:
			# Translators: spoken when the selected file is not a recognizable type.
			ui.message(_("This file type cannot be read. Supported types: PDF, PNG, JPEG, BMP, GIF, and TIFF."))
			return
		fileName = os.path.basename(path)
		# Translators: title of the window showing text recognized from a file; {name} is the file name.
		title = _("Recognized text from {name}").format(name=fileName)
		pageLimit = self._FILE_OCR_PAGE_LIMIT

		def work():
			# _runAsync bumped the generation just before starting this thread;
			# a later value means the user cancelled or started something else.
			generation = self._generation
			if ext == "pdf":
				texts, totalPages = self._ocrPdfPages(path, pageLimit, generation)
			else:
				texts, totalPages = self._ocrImageFilePages(path, pageLimit, generation)
			if texts is None:
				return None  # cancelled; _finish discards stale results anyway
			# Translators: header above each page in a multi-page recognition result; {number} is the page number.
			return ocrLogic.pagesToText(texts, _("Page {number}")), len(texts), totalPages

		def onSuccess(result):
			if result is None:
				return
			text, pagesRead, totalPages = result
			if not text.strip():
				# Recognition succeeded with a negative outcome, so the success earcon applies.
				tones.beep(880, 60)
				# Translators: spoken when a file contains no recognizable text; {name} is the file name.
				ui.message(_("No text was recognized in {name}.").format(name=fileName))
				return
			if totalPages > pagesRead:
				# Translators: put before a partly read long document; {read} and {total} are page counts.
				text = _("Note: only the first {read} of {total} pages were read.").format(read=pagesRead, total=totalPages) + "\n" + text
			self._deliverResult(title, text)

		# Translators: spoken while a file is being recognized; {name} is the file name.
		self._runAsync(work, onSuccess, _("Reading {name}...").format(name=fileName))

	def _ocrPdfPages(self, path, pageLimit, generation):
		"""Convert a PDF with the bundled Xpdf tools and OCR each page. Worker thread."""
		import shutil
		import subprocess
		import tempfile
		si = subprocess.STARTUPINFO()
		si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # no console window flash
		toolsDir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
		try:
			proc = subprocess.run(
				[os.path.join(toolsDir, "pdfinfo.exe"), path],
				capture_output=True, text=True, encoding="utf-8", errors="replace",
				startupinfo=si, timeout=60,
			)
			totalPages = ocrLogic.parsePdfPageCount(proc.stdout) if proc.returncode == 0 else None
		except Exception:
			log.error("PiPiQ: pdfinfo failed", exc_info=True)
			totalPages = None
		if not totalPages:
			# Translators: spoken when a PDF cannot be opened for recognition.
			raise apiClient.ApiError(_("The PDF file could not be opened. It may be corrupted or password protected."))
		tempDir = tempfile.mkdtemp(prefix="pipiq-pdf-")
		try:
			proc = subprocess.run(
				[
					os.path.join(toolsDir, "pdftopng.exe"),
					"-f", "1", "-l", str(min(totalPages, pageLimit)),
					path, os.path.join(tempDir, "page"),
				],
				capture_output=True, startupinfo=si, timeout=600,
			)
			pngFiles = sorted(
				os.path.join(tempDir, f) for f in os.listdir(tempDir) if f.lower().endswith(".png")
			)
			if not pngFiles:
				log.error("PiPiQ: pdftopng produced no pages (exit code %s)" % proc.returncode)
				# Translators: spoken when the PDF to image conversion fails.
				raise apiClient.ApiError(_("The PDF file could not be converted for recognition."))
			texts = []
			for pngFile in pngFiles:
				if self._generation != generation:
					return None, totalPages
				noLog = wx.LogNull()  # keep wx image loading errors out of dialogs
				try:
					image = wx.Image(pngFile)
				finally:
					del noLog
				texts.append(self._recognizeWxImage(image))
				self._announcePageProgress(len(texts), len(pngFiles), generation)
			return texts, totalPages
		finally:
			try:
				shutil.rmtree(tempDir)
			except Exception:
				pass

	def _ocrImageFilePages(self, path, pageLimit, generation):
		"""OCR an image file, page by page for multi-page TIFF and GIF. Worker thread."""
		noLog = wx.LogNull()  # keep wx image loading errors out of dialogs
		try:
			try:
				pageCount = wx.Image.GetImageCount(path)
			except Exception:
				pageCount = 0
			if pageCount < 1:
				# Translators: spoken when an image file cannot be decoded.
				raise apiClient.ApiError(_("The image file could not be opened. It may be corrupted or in an unsupported variant."))
			texts = []
			for index in range(min(pageCount, pageLimit)):
				if self._generation != generation:
					return None, pageCount
				image = wx.Image(path, index=index) if pageCount > 1 else wx.Image(path)
				if not image.IsOk():
					raise apiClient.ApiError(_("The image file could not be opened. It may be corrupted or in an unsupported variant."))
				texts.append(self._recognizeWxImage(image))
				self._announcePageProgress(len(texts), min(pageCount, pageLimit), generation)
			return texts, pageCount
		finally:
			del noLog

	def _announcePageProgress(self, done, total, generation):
		if total > 1 and done < total and done % 10 == 0:
			def announce():
				if self._generation == generation:
					# Translators: progress announcement while reading a multi-page file.
					ui.message(_("Page {done} of {total}").format(done=done, total=total))
			wx.CallAfter(announce)

	def _recognizeWxImage(self, image):
		"""OCR one wx.Image and return its text. Runs on the worker thread."""
		from contentRecog import RecogImageInfo, uwpOcr
		import winGDI
		width, height = image.GetWidth(), image.GetHeight()
		if width < 1 or height < 1:
			return ""
		longest = max(width, height)
		if longest > self._OCR_MAX_IMAGE_DIM:
			scale = float(self._OCR_MAX_IMAGE_DIM) / longest
			width = max(1, int(width * scale))
			height = max(1, int(height * scale))
			image = image.Scale(width, height, wx.IMAGE_QUALITY_HIGH)
		try:
			recognizer = uwpOcr.UwpOcr()
		except Exception:
			log.error("PiPiQ: could not create the Windows OCR recognizer", exc_info=True)
			raise apiClient.ApiError(_("Windows OCR could not be started. Check that an OCR language is installed in Windows settings, under Language."))
		try:
			imgInfo = RecogImageInfo.createFromRecognizer(0, 0, width, height, recognizer)
		except ValueError:
			return ""  # page too small to hold readable text
		if (imgInfo.recogWidth, imgInfo.recogHeight) != (width, height):
			# The engine wants small pages upscaled before recognition.
			image = image.Scale(imgInfo.recogWidth, imgInfo.recogHeight, wx.IMAGE_QUALITY_HIGH)
		bitmap = wx.Bitmap(image, 24)
		pixels = (winGDI.RGBQUAD * imgInfo.recogWidth * imgInfo.recogHeight)()
		bitmap.CopyToBuffer(pixels, format=wx.BitmapBufferFormat_ARGB32)
		done = threading.Event()
		holder = {}

		def onResult(result):
			holder["result"] = result
			done.set()

		recognizer.recognize(pixels, imgInfo, onResult)
		if not done.wait(30):
			raise apiClient.ApiError(_("Text recognition timed out. Please try again."))
		result = holder.get("result")
		if isinstance(result, Exception):
			log.error("PiPiQ: Windows OCR failed: %s" % result)
			raise apiClient.ApiError(_("Text recognition failed. See the NVDA log for details."))
		return ocrLogic.linesWordsToText(getattr(result, "data", None))

	# ------------------------------------------------------------------
	# Capture preview

	@script(
		# Translators: input help for the capture check command.
		description=_("Checks what a screenshot or description would capture: reports the navigator object's size and position, whether it is fully on screen, and whether another window is covering it."),
	)
	def script_checkCapture(self, gesture):
		self._exitLayer()
		try:
			obj = api.getNavigatorObject()
			location = obj.location
			if not location or not location.width or not location.height:
				obj = api.getFocusObject()
				location = obj.location
		except Exception:
			obj, location = None, None
		if not obj or not location or not location.width or not location.height:
			# Translators: spoken when the capture check has nothing to measure.
			ui.message(_("The current object has no visible area, so O would capture nothing. S still captures the whole screen."))
			return
		parts = []
		name = obj.name or ""
		try:
			role = obj.role.displayString
		except Exception:
			role = ""
		# Translators: start of the capture check report; {name} and {role} identify the object.
		parts.append(_("Pressing O, T, or X would capture: {name}, {role}.").format(name=name or _("unnamed object"), role=role))
		rect = screenGrab.intersectWithVirtualScreen(location.left, location.top, location.width, location.height)
		if not rect:
			# Translators: capture check result when the object is scrolled off screen.
			parts.append(_("Warning: it is entirely off screen, so the capture would be empty. Scroll it into view first."))
			# Translators: title of the window showing the last capture check report.
			self._deliverResult(_("Capture check"), " ".join(parts), forceSpeakOnly=True, playEarcon=False)
			return
		visibleLeft, visibleTop, visibleWidth, visibleHeight = rect
		# Translators: reports the size of the area that would be captured.
		parts.append(_("Area {width} by {height} pixels.").format(width=location.width, height=location.height))
		fullArea = location.width * location.height
		visiblePct = int(round(100.0 * visibleWidth * visibleHeight / fullArea)) if fullArea else 0
		if visiblePct < 98:
			# Translators: capture check warning; {percent} is how much of the object is on screen.
			parts.append(_("Only {percent} percent of it is on screen; scroll it fully into view for a complete capture.").format(percent=visiblePct))
		blocked, total, covering = screenGrab.occlusionReport(
			visibleLeft, visibleTop, visibleWidth, visibleHeight,
			getattr(obj, "windowHandle", None),
		)
		if total and blocked == 0:
			# Translators: capture check result when nothing overlaps the object.
			parts.append(_("Nothing is covering it."))
		elif blocked:
			if covering:
				# Translators: capture check warning; {count} of {total} checked points are covered by window {title}.
				parts.append(_("Warning: it is covered at {count} of {total} checked points by the window {title}. Bring your window to the front, for example with Alt plus Tab, then capture.").format(count=blocked, total=total, title=covering))
			else:
				parts.append(_("Warning: it is covered at {count} of {total} checked points by another window. Bring your window to the front, then capture.").format(count=blocked, total=total))
		index, count = screenGrab.monitorIndexForPoint(visibleLeft + visibleWidth // 2, visibleTop + visibleHeight // 2)
		if count > 1:
			# Translators: capture check note on multi-monitor setups.
			parts.append(_("It is on monitor {number}.").format(number=index))
		try:
			foregroundName = api.getForegroundObject().name or ""
		except Exception:
			foregroundName = ""
		if foregroundName:
			# Translators: end of the capture check report; {window} is the active window's title.
			parts.append(_("W would capture the whole {window} window, and S the whole screen.").format(window=foregroundName))
		# Stored as the last result so R can re-speak this multi-sentence
		# report and B can open it for line-by-line braille reading.
		self._deliverResult(_("Capture check"), " ".join(parts), forceSpeakOnly=True, playEarcon=False)

	# ------------------------------------------------------------------
	# Results

	@script(
		# Translators: input help for repeating the last result.
		description=_("Repeats the last Vision assist result."),
	)
	def script_repeatLast(self, gesture):
		self._exitLayer()
		if not self._lastResult:
			# Translators: spoken when there is no previous result to repeat.
			ui.message(_("No result yet."))
			return
		ui.message(self._lastResult[1])  # spoken form

	@script(
		# Translators: input help for opening the last result in a window.
		description=_("Shows the last Vision assist result in a browseable window."),
	)
	def script_showLast(self, gesture):
		self._exitLayer()
		if not self._lastResult:
			ui.message(_("No result yet."))
			return
		title, _spoken, displayText = self._lastResult
		ui.browseableMessage(displayText, title=title)

	def _deliverResult(self, title, spokenText, displayText=None, forceSpeakOnly=False, playEarcon=True):
		if displayText is None:
			displayText = spokenText
		if playEarcon:
			tones.beep(880, 60)
		self._lastResult = (title, spokenText, displayText)
		mode = _conf()["resultsPresentation"]
		openWindow = not forceSpeakOnly and (
			mode == "window" or (mode == "auto" and len(spokenText) > LONG_RESULT_CHARS)
		)
		if openWindow:
			# Speaking the full text here would only be cut off by the window
			# announcing itself; the window has the text, R re-speaks it.
			# Translators: short spoken lead-in before the results window opens.
			ui.message(_("Result ready, opening window."))
			# The user explicitly invoked this command moments ago, so opening
			# the window is expected, not focus stealing.
			ui.browseableMessage(displayText, title=title)
		else:
			ui.message(spokenText)

	# ------------------------------------------------------------------
	# Async plumbing

	def _preflight(self, needsScreen=True):
		"""Common checks before starting a task. Returns False if the task must not start."""
		if self._inFlight:
			self._cancelInFlight()
			return False
		if not _conf()["apiKey"].strip():
			# Translators: spoken when a command is used before the API key is configured.
			ui.message(_("No API key configured. Open NVDA menu, Preferences, Settings, PiPiQ Vision Assist, and enter your OpenCode API key."))
			return False
		if needsScreen and _screenCurtainActive():
			# Translators: spoken when NVDA's screen curtain would make every screenshot black.
			ui.message(_("Screen curtain is active, so the screen appears black and cannot be analyzed. Turn off screen curtain and try again."))
			return False
		return True

	def _cancelInFlight(self):
		self._generation += 1
		self._inFlight = False
		# Translators: spoken when a running request is cancelled.
		ui.message(_("Request cancelled."))

	def _runAsync(self, work, onSuccess, progressMessage):
		self._generation += 1
		generation = self._generation
		self._inFlight = True
		ui.message(progressMessage)
		if _conf()["progressBeeps"]:
			threading.Thread(target=self._beeper, args=(generation,), daemon=True).start()
		threading.Thread(target=self._worker, args=(generation, work, onSuccess), daemon=True).start()

	def _beeper(self, generation):
		time.sleep(1.0)
		while self._inFlight and self._generation == generation:
			try:
				tones.beep(750, 40)
			except Exception:
				return
			time.sleep(1.2)

	def _worker(self, generation, work, onSuccess):
		try:
			result = work()
			error = None
		except apiClient.ApiError as e:
			result, error = None, str(e)
		except Exception as e:
			log.error("PiPiQ: unexpected task failure", exc_info=True)
			# Translators: spoken on an unexpected internal error; details go to the NVDA log.
			result, error = None, _("An unexpected error occurred. See the NVDA log for details.")
		wx.CallAfter(self._finish, generation, result, error, onSuccess)

	def _finish(self, generation, result, error, onSuccess):
		if generation != self._generation:
			return  # cancelled or superseded; stay silent
		self._inFlight = False
		if error is not None:
			tones.beep(300, 90)
			ui.message(error)
			return
		# The success earcon is played by the result handlers once they have
		# validated the answer, so an empty or unparseable reply never gets
		# the success tone followed by an error message.
		try:
			onSuccess(result)
		except Exception:
			log.error("PiPiQ: failed to handle result", exc_info=True)
			ui.message(_("An unexpected error occurred. See the NVDA log for details."))

	# The entry gesture lives here (not in the @script decorator) so that
	# _exitLayer's clearGestureBindings + bindGestures(self.__gestures) restores it.
	# NVDA+Shift+V collides with a built-in NVDA command, so the default is
	# NVDA+Shift+0 (number row zero); reassignable in Input Gestures.
	__gestures = {
		"kb:NVDA+shift+0": "visionLayer",
	}

	__layerGestures = {
		"kb:q": "findQRScreen",
		"kb:w": "findQRWindow",
		"kb:o": "describeNavigator",
		"kb:s": "describeScreen",
		"kb:c": "describeClipboard",
		"kb:g": "listWebImages",
		"kb:f": "checkForm",
		"kb:p": "checkCapture",
		"kb:t": "screenshotObject",
		"kb:shift+t": "screenshotWindow",
		"kb:control+t": "screenshotScreen",
		"kb:x": "ocrObject",
		"kb:shift+x": "ocrWindow",
		"kb:control+x": "ocrScreen",
		"kb:d": "ocrFile",
		"kb:r": "repeatLast",
		"kb:b": "showLast",
		"kb:h": "layerHelp",
		"kb:f1": "layerHelp",
		"kb:escape": "cancel",
	}
