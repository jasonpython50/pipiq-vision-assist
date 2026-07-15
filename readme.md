# PiPiQ Vision Assist for NVDA

Vision assistance for blind users, powered by OpenCode Go vision AI models:

1. **QR code locator**: finds a QR code on your screen (the WhatsApp desktop app, WhatsApp Web, websites, anywhere) and tells you exactly where to point your phone camera, in plain words like "top right of the screen, 78 percent from the left edge". It can also park the mouse pointer on the code so you can feel where it is with mouse tracking.
2. **Image describer**: describes any image: the object you are reviewing with the NVDA navigator, the whole screen, an image you copied to the clipboard (including image files copied in File Explorer), or any image chosen from a list of all images on the current web page.
3. **Screenshot taker**: photographs the navigator object, the current window, or the whole screen at full resolution, saves the picture as a PNG in your Pictures folder, and copies it to the clipboard for pasting into chats and emails. Works entirely offline, no API key needed.
4. **Form check**: when a Next or Submit button on a web form is dimmed and the page gives no hint why, press the entry gesture and then F to hear exactly what the form still needs: which required fields are empty, which checkboxes are unchecked, and what the page marks as invalid. Works entirely offline, no API key needed.

## Installation

The addon requires NVDA 2026.1 or later.

1. Press Enter on the `.nvda-addon` file (get the latest from the [Releases page on GitHub](https://github.com/jasonpython50/pipiq-vision-assist/releases)) and confirm the installation, then restart NVDA.
2. Open NVDA menu, then Preferences, then Settings, then **PiPiQ Vision Assist**.
3. Paste your OpenCode API key into the "OpenCode API key" field and press OK. You can get a key by subscribing to OpenCode Go at opencode.ai.

**Privacy notes:**
- For QR detection and image descriptions, screenshots and clipboard images are sent to the OpenCode API for analysis. Do not run those commands while confidential content is on screen if that concerns you. Screenshots taken with the T commands stay on your computer.
- The API key is stored in NVDA's configuration file in plain text, like most NVDA addon keys.

## Usage

Everything starts with one gesture: **NVDA+Shift+0** (the zero on the number row; reassignable in Input Gestures, category "PiPiQ Vision Assist"). After pressing it, press one letter:

| Key | Action |
| --- | ------ |
| Q | Find a QR code on the whole screen |
| W | Find a QR code in the current window only |
| O | Describe the image at the navigator object |
| S | Describe the whole screen |
| C | Describe the image on the clipboard |
| G | List all images on the current web page and choose one to describe |
| F | Check the form on the current web page: reports dimmed buttons and which required fields are still empty, unchecked, or marked invalid |
| P | Check what a screenshot would capture before taking it: reports the object's size, whether it is fully on screen, and whether another window is covering it |
| T | Take a screenshot of the navigator object: save it to Pictures\PiPiQ Screenshots and copy it to the clipboard |
| Shift+T | Take a screenshot of the current window |
| Control+T | Take a screenshot of the whole screen |
| R | Repeat the last result |
| B | Open the last result in a browseable window (arrow through it line by line, Escape closes) |
| H or F1 | Speak this command list |
| Escape | Cancel a running request, or close the layer |

Every command can also be bound to its own direct gesture in the Input Gestures dialog.

While a request runs you hear a soft beep every second (can be turned off). A high beep means success, a low beep means failure, and the result or error is spoken. Press the entry gesture and then the same command again to cancel and restart if it takes too long.

### Describing an image on a web page

1. With focus on the web page, press NVDA+Shift+0, then G.
2. A list of every image on the page opens, labeled with each image's alternative text ("Unlabeled image" when it has none, and describing exactly those is often the point).
3. Arrow to the image you want and press Enter. The addon scrolls it into view, photographs it, and reads you the AI description.

The picture must be visible on screen to be photographed, so a maximized browser window works best.

### Checking why a form button is dimmed

1. With the browse mode cursor somewhere inside the form (anywhere on the page also works), press NVDA+Shift+0, then F.
2. The addon walks every field of the form and reports, for example: "The Proceed button is dimmed and cannot be pressed yet. After checking 9 fields in the current form, 2 likely reasons were found. 1: Phone number, edit box, is required but still empty. 2: I agree to the terms of service, checkbox, is required but not checked."
3. It covers dimmed buttons, empty required fields, unchecked required checkboxes and radio groups, and fields marked invalid, including any error text the page attaches to the field. If everything looks complete, it says so and suggests describing the screen with S so the AI can look for purely visual causes such as CAPTCHAs.

### Scanning a WhatsApp QR code, step by step

This works the same whether you use the WhatsApp desktop app or WhatsApp Web in a browser; the QR locator reads the code from the screen, so the kind of window does not matter.

1. Open the WhatsApp desktop app, or web.whatsapp.com in your browser, and maximize the window (Windows+Up arrow).
2. Press NVDA+Shift+0, then W.
3. Wait for the high beep. You will hear something like: "QR code found in the middle right of the window, centered 75 percent from the left edge and 50 percent from the top. It spans about 20 percent of the width. The mouse pointer is now on the QR code. Point your phone camera at the middle right of the window."
4. On your phone, open WhatsApp, then Settings, then Linked devices, then Link a device, and point the camera at the reported part of your computer screen. Hold the phone 20 to 40 centimeters away, roughly parallel to the screen. Raising the screen brightness helps.
5. If it does not catch, press NVDA+Shift+0 then W again; the position may shift when the site refreshes the code.

## Settings

- **OpenCode API key**: your key, starting with sk-.
- **API base URL**: default `https://opencode.ai/zen/go/v1`. Any OpenAI-compatible server also works (the same addon can be pointed at OpenCode Zen or another provider).
- **Model for QR code detection**: default `qwen3.7-plus` (tested: accurate boxes, no false positives).
- **Model for image descriptions**: default `kimi-k2.5` (tested: fast, high quality descriptions).
- **Refresh model list from server**: downloads the current model list with your key; vision-capable models are marked.
- **Show only vision-capable models**: hides text-only models that cannot analyze images.
- **What to do with images**: Automatic (default: pictures get described, text-heavy screenshots get their text read out exactly as written), Always describe, or Always extract the text exactly.
- **Image description detail**: brief, detailed, or your own custom prompt.
- **Request timeout**: default 90 seconds. QR detection on busy screens can take 20 to 60 seconds depending on the model.
- **Maximum image size**: screenshots are downscaled to this many pixels on the longest edge before uploading. Smaller is faster, larger preserves fine detail.
- **Move the mouse pointer to a found QR code**: on by default.
- **Presenting results**: speak only, speak and open a window for long results (default), or always open the window.
- **Progress beeps**, **response language**: self explanatory.

## Edge cases the addon handles

- NVDA's **screen curtain** makes screenshots black; the addon warns you and refuses to send a useless black image (detected both from NVDA and from the captured pixels, so it works on every NVDA version).
- Reasoning models that leak their thinking into the reply: thinking traces are stripped and only the final answer is spoken, with a large token budget so the final answer is always reached.
- Multiple QR codes on screen: it reports the count and guides you to the largest.
- Multiple monitors: the report names which monitor the code is on.
- Tiny QR codes: it suggests maximizing or zooming before scanning.
- The AI claiming a QR exists without usable coordinates, replies wrapped in explanations or markdown, coordinates given as percentages or pixels instead of fractions: all normalized or rejected safely.
- Clipboard containing a copied image file (PNG, JPEG, GIF, WebP, BMP) as well as raw copied bitmaps; oversized files are refused with a clear message instead of hanging.
- No API key, wrong key, quota exhausted, server errors, no internet, request timeout: each produces a distinct spoken message saying what to do.
- A second command while one runs cancels the first cleanly; results from cancelled requests are never spoken.
- Everything network-related runs off NVDA's main thread, so NVDA never freezes.

## Troubleshooting

- **"The API rejected your key"**: re-paste the key in settings; make sure your OpenCode Go subscription is active.
- **"No QR code was found"** but you expect one: the code may be scrolled out of view or behind another window. Bring it on screen, then try Q (whole screen) instead of W.
- **Descriptions come in English**: check "Ask for descriptions in your NVDA language" in settings.
- Detailed errors are written to the NVDA log (NVDA+F1).

## Version history

### Version 1.3.1

- The minimum supported NVDA version is now 2026.1.
- A version history section was added to the user guide, and the WhatsApp QR walkthrough now covers the WhatsApp desktop app as well as WhatsApp Web.

### Version 1.3.0

- New form check command, F in the Vision assist layer: when a form's Submit or Next button is dimmed, it reports which required fields are still empty, which checkboxes are unchecked, which radio button groups have nothing selected, and what the page marks as invalid, including any error text the page attaches. Works offline, no API key needed.

### Version 1.2.0

- New screenshot commands: T for the navigator object, Shift+T for the current window, Control+T for the whole screen. Screenshots are saved at full resolution in the PiPiQ Screenshots folder inside Pictures and copied to the clipboard for pasting into chats and emails.
- New web image chooser, G: lists every image on the current web page so you can pick one to describe with AI.
- The user guide was added; it opens from the Help button in the NVDA Add-on Store.

### Version 1.1.0

- New "What to do with images" setting: Automatic, Always describe, or Always extract the text exactly as written.
- New capture check command, P: reports the object's size, how much of it is on screen, and whether another window is covering it, before you photograph or describe it.
- Reasoning models that leak their thinking into replies are handled: only the final answer is spoken.

### Version 1.0.0

- First release: the QR code locator (Q and W), the AI image describer for the navigator object, the whole screen, and the clipboard (O, S, C), the Vision assist command layer on NVDA+Shift+0, and the settings panel.
