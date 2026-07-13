# NVDA settings panel for PiPiQ Vision Assist.

import threading

import wx

import addonHandler
import config
import gui
import ui
from gui import guiHelper, nvdaControls
from gui.settingsDialogs import SettingsPanel
from logHandler import log

from . import apiClient
from .knownModels import KNOWN_VISION_MODELS, isLikelyVisionModel

addonHandler.initTranslation()


class PipiqSettingsPanel(SettingsPanel):
	# Translators: title of the addon's category in NVDA settings.
	title = _("PiPiQ Vision Assist")

	def makeSettings(self, settingsSizer):
		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		conf = config.conf["pipiq"]

		# Translators: label of the API key field in settings.
		self.apiKeyEdit = helper.addLabeledControl(_("OpenCode API &key:"), wx.TextCtrl, style=wx.TE_PASSWORD)
		self.apiKeyEdit.SetValue(conf["apiKey"])

		# Translators: label of the API base URL field in settings.
		self.baseURLEdit = helper.addLabeledControl(_("API &base URL:"), wx.TextCtrl)
		self.baseURLEdit.SetValue(conf["baseURL"])

		modelIds = self._initialModelIds(conf)
		# Translators: label of the model selector used for QR code detection.
		self.qrModelChoice = helper.addLabeledControl(_("Model for &QR code detection:"), wx.Choice, choices=[])
		# Translators: label of the model selector used for image descriptions.
		self.descModelChoice = helper.addLabeledControl(_("Model for &image descriptions:"), wx.Choice, choices=[])
		self._populateModelChoices(modelIds, conf["qrModel"], conf["descModel"])

		# Translators: label of the button that downloads the current model list.
		self.refreshButton = wx.Button(self, label=_("&Refresh model list from server"))
		helper.addItem(self.refreshButton)
		self.refreshButton.Bind(wx.EVT_BUTTON, self.onRefreshModels)
		self._refreshInFlight = False

		# Translators: label of the checkbox that hides text-only models from the model lists.
		self.visionOnlyCheck = wx.CheckBox(self, label=_("Show only &vision-capable models"))
		self.visionOnlyCheck.SetValue(conf["showOnlyVisionModels"])
		helper.addItem(self.visionOnlyCheck)
		self.visionOnlyCheck.Bind(wx.EVT_CHECKBOX, self.onVisionOnlyToggle)

		self.contentModeRadio = wx.RadioBox(
			self,
			# Translators: label of the radio group choosing between describing images and reading their text.
			label=_("What to do with images"),
			choices=[
				# Translators: content mode option: the AI decides between describing and transcribing.
				_("Automatic: describe pictures, read text screenshots exactly"),
				# Translators: content mode option: always describe.
				_("Always describe the image"),
				# Translators: content mode option: always transcribe text exactly.
				_("Always extract the text exactly, never describe"),
			],
			majorDimension=1,
			style=wx.RA_SPECIFY_COLS,
		)
		self._contentModeValues = ["auto", "describe", "extract"]
		try:
			self.contentModeRadio.SetSelection(self._contentModeValues.index(conf["contentMode"]))
		except ValueError:
			self.contentModeRadio.SetSelection(0)
		helper.addItem(self.contentModeRadio)

		self.detailRadio = wx.RadioBox(
			self,
			# Translators: label of the radio group choosing how detailed image descriptions are.
			label=_("Image description detail"),
			choices=[
				# Translators: brief image descriptions option.
				_("Brief (2 to 4 sentences)"),
				# Translators: detailed image descriptions option.
				_("Detailed"),
				# Translators: option to use the custom prompt written below.
				_("Custom prompt"),
			],
			majorDimension=1,
			style=wx.RA_SPECIFY_COLS,
		)
		self._detailValues = ["brief", "detailed", "custom"]
		try:
			self.detailRadio.SetSelection(self._detailValues.index(conf["detailLevel"]))
		except ValueError:
			self.detailRadio.SetSelection(0)
		helper.addItem(self.detailRadio)

		# Translators: label of the multiline custom prompt field. Tab moves focus out of it.
		self.customPromptEdit = helper.addLabeledControl(
			_("Custom description &prompt:"),
			wx.TextCtrl,
			style=wx.TE_MULTILINE,
			size=(400, 60),
		)
		self.customPromptEdit.SetValue(conf["customPrompt"])

		# Translators: label of the request timeout spin control; the unit is in the label.
		self.timeoutSpin = helper.addLabeledControl(
			_("Request &timeout in seconds:"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=15, max=300, initial=int(conf["timeout"]),
		)

		# Translators: label of the maximum image size spin control.
		self.maxDimSpin = helper.addLabeledControl(
			_("Maximum image si&ze sent to the AI, in pixels (larger is sharper but slower):"),
			nvdaControls.SelectOnFocusSpinCtrl,
			min=512, max=3072, initial=int(conf["maxImageDim"]),
		)

		# Translators: label of the checkbox that moves the mouse pointer onto a found QR code.
		self.moveMouseCheck = wx.CheckBox(self, label=_("&Move the mouse pointer to a found QR code"))
		self.moveMouseCheck.SetValue(conf["moveMouseToQR"])
		helper.addItem(self.moveMouseCheck)

		self.resultsRadio = wx.RadioBox(
			self,
			# Translators: label of the radio group choosing how results are presented.
			label=_("Presenting results"),
			choices=[
				# Translators: results presentation option.
				_("Speak only"),
				# Translators: results presentation option; long results also open a readable window.
				_("Speak, and open a window for long results"),
				# Translators: results presentation option.
				_("Always open results in a window"),
			],
			majorDimension=1,
			style=wx.RA_SPECIFY_COLS,
		)
		self._resultsValues = ["speak", "auto", "window"]
		try:
			self.resultsRadio.SetSelection(self._resultsValues.index(conf["resultsPresentation"]))
		except ValueError:
			self.resultsRadio.SetSelection(1)
		helper.addItem(self.resultsRadio)

		# Translators: label of the checkbox enabling progress beeps during AI requests.
		self.beepsCheck = wx.CheckBox(self, label=_("Play progress bee&ps while waiting for the AI"))
		self.beepsCheck.SetValue(conf["progressBeeps"])
		helper.addItem(self.beepsCheck)

		# Translators: label of the checkbox that asks the AI to answer in the NVDA interface language.
		self.uiLanguageCheck = wx.CheckBox(self, label=_("Ask for descriptions in your NVDA &language"))
		self.uiLanguageCheck.SetValue(conf["respondInUILanguage"])
		helper.addItem(self.uiLanguageCheck)

	def _initialModelIds(self, conf):
		ids = list(KNOWN_VISION_MODELS)
		for saved in (conf["qrModel"], conf["descModel"]):
			if saved and saved not in ids:
				ids.append(saved)
		return ids

	def _populateModelChoices(self, modelIds, qrSelection, descSelection):
		visionOnly = getattr(self, "visionOnlyCheck", None) and self.visionOnlyCheck.GetValue()
		shown = []
		for mid in modelIds:
			if visionOnly and not isLikelyVisionModel(mid) and mid not in (qrSelection, descSelection):
				continue
			shown.append(mid)
		if not shown:
			shown = list(KNOWN_VISION_MODELS)
		self._modelIds = shown
		self._allModelIds = list(modelIds)
		labels = []
		for mid in shown:
			if mid in KNOWN_VISION_MODELS:
				labels.append(mid)
			elif isLikelyVisionModel(mid):
				# Translators: suffix for models that look vision-capable but are untested.
				labels.append(_("{model} (vision untested)").format(model=mid))
			else:
				# Translators: suffix for models not known to support images.
				labels.append(_("{model} (may not support images)").format(model=mid))
		for choice, selection in ((self.qrModelChoice, qrSelection), (self.descModelChoice, descSelection)):
			choice.Set(labels)
			try:
				choice.SetSelection(self._modelIds.index(selection))
			except ValueError:
				choice.SetSelection(0)

	def _selectedModel(self, choice):
		index = choice.GetSelection()
		if 0 <= index < len(self._modelIds):
			return self._modelIds[index]
		return self._modelIds[0] if self._modelIds else ""

	def onVisionOnlyToggle(self, evt):
		qr = self._selectedModel(self.qrModelChoice)
		desc = self._selectedModel(self.descModelChoice)
		self._populateModelChoices(self._allModelIds, qr, desc)

	def onRefreshModels(self, evt):
		# The button stays enabled so focus is never thrown; a second press
		# while a fetch is running is simply ignored.
		if self._refreshInFlight:
			# Translators: spoken if the refresh button is pressed while a refresh is already running.
			ui.message(_("Already refreshing, please wait."))
			return
		apiKey = self.apiKeyEdit.GetValue().strip()
		baseURL = self.baseURLEdit.GetValue().strip() or apiClient.DEFAULT_BASE_URL
		self._refreshInFlight = True
		# Translators: spoken when the model list download starts.
		ui.message(_("Refreshing model list..."))
		threading.Thread(target=self._refreshWorker, args=(baseURL, apiKey), daemon=True).start()

	def _refreshWorker(self, baseURL, apiKey):
		try:
			ids = apiClient.listModels(baseURL, apiKey, timeout=30)
			error = None
		except apiClient.ApiError as e:
			ids, error = None, str(e)
		except Exception as e:
			log.error("PiPiQ model refresh failed", exc_info=True)
			ids, error = None, str(e)
		wx.CallAfter(self._refreshDone, ids, error)

	def _refreshDone(self, ids, error):
		self._refreshInFlight = False
		if not self:  # panel was destroyed while the request ran
			return
		if error or not ids:
			# Translators: spoken when the model list download fails; {error} is the reason.
			ui.message(_("Could not load models: {error}").format(error=error or _("no models returned")))
			return
		qr = self._selectedModel(self.qrModelChoice)
		desc = self._selectedModel(self.descModelChoice)
		for saved in (qr, desc):
			if saved and saved not in ids:
				ids.append(saved)
		self._populateModelChoices(ids, qr, desc)
		visionCount = sum(1 for m in ids if isLikelyVisionModel(m))
		# Translators: spoken when the model list download succeeds.
		ui.message(_("{total} models loaded, {vision} with vision support.").format(total=len(ids), vision=visionCount))

	def isValid(self):
		baseURL = self.baseURLEdit.GetValue().strip()
		if baseURL and not baseURL.lower().startswith(("http://", "https://")):
			gui.messageBox(
				# Translators: error shown for a malformed base URL in settings.
				_("The API base URL must start with http:// or https://"),
				# Translators: title of the settings validation error dialog.
				_("Invalid setting"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			self.baseURLEdit.SetFocus()
			return False
		if (
			self._detailValues[self.detailRadio.GetSelection()] == "custom"
			# In extract mode the detail level and custom prompt are never
			# used, so an empty prompt must not block saving.
			and self._contentModeValues[self.contentModeRadio.GetSelection()] != "extract"
			and not self.customPromptEdit.GetValue().strip()
		):
			gui.messageBox(
				# Translators: error shown when "Custom prompt" is selected but the prompt field is empty.
				_("You selected Custom prompt, but the custom description prompt is empty. Write a prompt, or choose Brief or Detailed."),
				_("Invalid setting"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			self.customPromptEdit.SetFocus()
			return False
		return super().isValid()

	def onSave(self):
		conf = config.conf["pipiq"]
		conf["apiKey"] = self.apiKeyEdit.GetValue().strip()
		conf["baseURL"] = self.baseURLEdit.GetValue().strip() or apiClient.DEFAULT_BASE_URL
		conf["qrModel"] = self._selectedModel(self.qrModelChoice)
		conf["descModel"] = self._selectedModel(self.descModelChoice)
		conf["contentMode"] = self._contentModeValues[self.contentModeRadio.GetSelection()]
		conf["detailLevel"] = self._detailValues[self.detailRadio.GetSelection()]
		conf["customPrompt"] = self.customPromptEdit.GetValue()
		conf["timeout"] = self.timeoutSpin.GetValue()
		conf["maxImageDim"] = self.maxDimSpin.GetValue()
		conf["moveMouseToQR"] = self.moveMouseCheck.GetValue()
		conf["resultsPresentation"] = self._resultsValues[self.resultsRadio.GetSelection()]
		conf["progressBeeps"] = self.beepsCheck.GetValue()
		conf["respondInUILanguage"] = self.uiLanguageCheck.GetValue()
		conf["showOnlyVisionModels"] = self.visionOnlyCheck.GetValue()
